"""The one provider connection of an application-kept rewind buffer.

It reads the live channel into the store whether anything plays it or not -
which is the point: a pause no longer stops the download, so LIVE never has
to reopen the provider stream. One connection, as mpv's own was; a dropped
one is opened again a few times (each a run of its own in the store), and
then given up, for the app's own reconnect to take over.

Bytes are moved in large blocks - socket reads and file writes, which leave
the interpreter lock free while they wait - so the window does not feel it.
"""
from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .dvr_store import SegmentStore
from .log import log

if TYPE_CHECKING:
    import requests

USER_AGENT = "dopeIPTV/1.0"     # what mpv sends; providers may check it


def _hard_close(resp: "requests.Response") -> None:
    """Close the connection at once: a plain close from another thread can
    leave a read blocked on the socket - and the account's only connection
    held - until data or the timeout arrives. shutdown() sends the FIN and
    wakes it."""
    sock = None
    try:
        conn = getattr(resp.raw, "_connection", None)
        sock = getattr(conn, "sock", None)
        if sock is None:
            fp = getattr(resp.raw, "_fp", None)
            sock = getattr(getattr(getattr(fp, "fp", None), "raw", None),
                           "_sock", None)
    except Exception:
        sock = None
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    try:
        resp.close()
    except Exception:
        pass


class Ingest:
    CHUNK = 64 * 1024
    MAX_FAILURES = 5            # in a row, without a byte in between
    TIMEOUT = (10.0, 20.0)      # connect, and silence while reading
    BACKOFF = 0.5               # doubled per failure, at most 8 s

    def __init__(self, url: str, store: SegmentStore,
                 opener: Callable[..., Any] | None = None
                 ) -> None:
        self.url = url
        self.store = store
        self._open = opener or self._default_open
        self._stop = threading.Event()
        self._resp: Any = None
        self._lock = threading.Lock()
        self.state = "connecting"   # streaming / reconnecting / failed / stopped
        self.connections = 0
        self._thread = threading.Thread(target=self._run, name="dvr-ingest",
                                        daemon=True)

    @staticmethod
    def _default_open(url: str, **kw: Any) -> Any:
        import requests     # not at startup: the window opens without it
        return requests.get(url, **kw)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            got = 0
            try:
                self.connections += 1
                resp = self._open(url=self.url, stream=True,
                                  timeout=self.TIMEOUT,
                                  headers={"User-Agent": USER_AGENT})
                with self._lock:
                    if self._stop.is_set():
                        resp.close()
                        break
                    self._resp = resp
                resp.raise_for_status()
                self.state = "streaming"
                for chunk in resp.iter_content(self.CHUNK):
                    if self._stop.is_set():
                        break
                    if chunk:
                        got += len(chunk)
                        failures = 0
                        self.store.append(chunk)
            except Exception as e:
                if not self._stop.is_set():
                    log.info("DVR ingest: %s", type(e).__name__)
            finally:
                with self._lock:
                    if self._resp is not None:
                        try:
                            self._resp.close()
                        except Exception:
                            pass
                        self._resp = None
            if self._stop.is_set():
                break
            failures += 0 if got else 1
            if failures >= self.MAX_FAILURES:
                self.state = "failed"
                log.info("DVR ingest: gave up after %d tries", failures)
                return
            self.state = "reconnecting"
            self.store.new_run()
            self._stop.wait(min(8.0, self.BACKOFF * (2 ** failures)))
        self.state = "stopped"

    def stop(self, wait: float = 0.0) -> None:
        """Close the provider connection now (another channel may need the
        account's only one), and let the thread end."""
        self._stop.set()
        with self._lock:
            resp = self._resp
        if resp is not None:
            _hard_close(resp)
        if wait and self._thread.is_alive():
            self._thread.join(wait)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()


def wait_until(predicate: Callable[[], bool], timeout: float,
               step: float = 0.05) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(step)
    return predicate()
