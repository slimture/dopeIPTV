"""The local server mpv plays an application-kept rewind buffer from.

Loopback only, on a port the system picks, and every path starts with a
token made for this session: nothing else on the machine - or the network -
can ask it for anything, and no provider address or account is in a URL it
serves. mpv sees a plain HTTP live TS stream, as from a provider: no length
and no ranges, so it never tries to seek in it itself.
"""
from __future__ import annotations

import re
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .dvr_store import SegmentStore

_PATH = re.compile(r"^/([A-Za-z0-9_-]+)/(?:s/(\d+)|(live))\.ts$")
# Join live this far behind the newest byte, so mpv has something to start from.
LIVE_LEAD = 4.0
# How long a live request may wait for the provider's first bytes.
FIRST_DATA_WAIT = 25.0


class _Handler(BaseHTTPRequestHandler):
    server: "_Server"
    protocol_version = "HTTP/1.0"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return      # not onto the app's stderr

    def do_GET(self) -> None:  # noqa: N802 - the http.server API
        m = _PATH.match(self.path.split("?", 1)[0])
        owner = self.server.owner
        if m is None or not secrets.compare_digest(m.group(1), owner.token):
            self.send_error(404)
            return
        store = owner.store
        if m.group(3):
            store.wait_for_data(FIRST_DATA_WAIT)
            seg = store.live_segment(LIVE_LEAD)
            if seg is None:
                self.send_error(404)
                return
            seq = seg.seq
            owner.opened(seq, seg.t0)
        else:
            seq = int(m.group(2))
            owner.opened(seq, None)
        reader = store.read_from(seq)
        try:
            first = next(reader, None)
            if first is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(first)
            for chunk in reader:
                if owner.stopping:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError,
                socket.timeout, OSError):
            pass        # mpv went away (a seek, a channel change)
        finally:
            reader.close()


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, owner: "LocalServer") -> None:
        self.owner = owner
        super().__init__(("127.0.0.1", 0), _Handler)


class LocalServer:
    def __init__(self, store: SegmentStore) -> None:
        self.store = store
        self.token = secrets.token_urlsafe(18)
        self.stopping = False
        self.last_opened: int | None = None
        self.last_opened_t0: float | None = None
        self._srv = _Server(self)
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(
            target=self._srv.serve_forever, kwargs={"poll_interval": 0.25},
            name="dvr-server", daemon=True)
        self._thread.start()

    def url(self, seq: int) -> str:
        return f"http://127.0.0.1:{self.port}/{self.token}/s/{seq}.ts"

    def live_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/{self.token}/live.ts"

    def opened(self, seq: int, t0: float | None) -> None:
        self.last_opened = seq
        self.last_opened_t0 = t0

    def close(self) -> None:
        self.stopping = True
        try:
            self._srv.shutdown()
        except Exception:
            pass
        try:
            self._srv.server_close()
        except Exception:
            pass
