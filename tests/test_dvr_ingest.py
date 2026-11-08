"""The ingest: one provider connection at a time, reconnects as runs of
their own, gives up when nothing comes, and lets go of the connection at
once when stopped."""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dopeiptv.core.dvr_ingest import Ingest, wait_until
from dopeiptv.core.dvr_store import SegmentStore


class Provider(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, chunks: int, status: int = 200) -> None:
        self.chunks, self.status = chunks, status
        self.connections = 0
        self.open_now = 0
        self.agents: list[str] = []
        super().__init__(("127.0.0.1", 0), Handler)


class Handler(BaseHTTPRequestHandler):
    server: Provider

    def log_message(self, *a) -> None:
        pass

    def do_GET(self) -> None:
        srv = self.server
        srv.connections += 1
        srv.open_now += 1
        srv.agents.append(self.headers.get("User-Agent", ""))
        try:
            if srv.status != 200:
                self.send_error(srv.status)
                return
            self.send_response(200)
            self.end_headers()
            n = 0
            while srv.chunks < 0 or n < srv.chunks:
                self.wfile.write(b"\x47" + b"x" * 187)
                self.wfile.flush()
                n += 1
                time.sleep(0.01)
        except OSError:
            pass
        finally:
            srv.open_now -= 1


@pytest.fixture
def provider():
    servers = []

    def make(chunks, status=200):
        p = Provider(chunks, status)
        threading.Thread(target=p.serve_forever, daemon=True).start()
        servers.append(p)
        return p

    yield make
    for p in servers:
        p.shutdown()
        p.server_close()


class Fast(Ingest):
    BACKOFF = 0.01


def test_reads_into_the_store_with_the_players_user_agent(provider, tmp_path):
    p = provider(-1)
    s = SegmentStore(tmp_path / "s", 60, 10**9, min_free=0)
    ing = Fast(f"http://127.0.0.1:{p.server_address[1]}/live.ts", s)
    ing.start()
    assert wait_until(lambda: s.newest() is not None
                      and s.newest().size > 188 * 5, 5)
    assert ing.state == "streaming"
    assert p.agents == ["dopeIPTV/1.0"]
    ing.stop(wait=5)
    assert wait_until(lambda: p.open_now == 0, 3), "connection left open"
    assert p.connections == 1


def test_a_dropped_connection_is_a_new_run(provider, tmp_path):
    p = provider(20)                       # the provider hangs up each time
    s = SegmentStore(tmp_path / "s", 60, 10**9, min_free=0)
    ing = Fast(f"http://127.0.0.1:{p.server_address[1]}/live.ts", s)
    ing.start()
    assert wait_until(lambda: len(s.runs()) >= 2, 10)
    ing.stop(wait=5)
    assert p.connections >= 2


def test_gives_up_when_nothing_comes(provider, tmp_path):
    p = provider(0, status=404)
    s = SegmentStore(tmp_path / "s", 60, 10**9, min_free=0)
    ing = Fast(f"http://127.0.0.1:{p.server_address[1]}/live.ts", s)
    ing.start()
    assert wait_until(lambda: ing.state == "failed", 10)
    assert p.connections == Fast.MAX_FAILURES
