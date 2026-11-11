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

    def __init__(self, chunks: int, status: int = 200, mode: str = "same",
                 repeat: int = 0) -> None:
        # mode "same": every packet alike; "count": each packet numbered,
        # a connection going on from where the last one stopped - or, with
        # *repeat*, starting that many packets back, as providers do.
        self.chunks, self.status = chunks, status
        self.mode, self.repeat = mode, repeat
        self.sent = 0
        self.connections = 0
        self.open_now = 0
        self.agents: list[str] = []
        super().__init__(("127.0.0.1", 0), Handler)


def packet(n: int) -> bytes:
    return b"\x47" + n.to_bytes(4, "big") + b"x" * 183


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
            if srv.mode == "count" and srv.connections > 1:
                srv.sent = max(0, srv.sent - srv.repeat)
            while srv.chunks < 0 or n < srv.chunks:
                if srv.mode == "count":
                    self.wfile.write(packet(srv.sent))
                    srv.sent += 1
                else:
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

    def make(chunks, status=200, mode="same", repeat=0):
        p = Provider(chunks, status, mode, repeat)
        threading.Thread(target=p.serve_forever, daemon=True).start()
        servers.append(p)
        return p

    yield make
    for p in servers:
        p.shutdown()
        p.server_close()


class Fast(Ingest):
    BACKOFF = 0.01
    SHORT_RUN = 0.05
    BLOCK_SECONDS = 0.01
    MERGE_SECONDS = 0.5


def stored(store: SegmentStore) -> bytes:
    return b"".join(seg.path.read_bytes() for seg in store._segs)


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


def test_a_dropped_connection_not_picked_up_again_is_a_new_run(
        provider, tmp_path):
    # The provider hangs up each time and goes on past what it sent - a gap,
    # nothing of the dropped connection's end comes again.
    p = provider(20, mode="count")
    s = SegmentStore(tmp_path / "s", 60, 10**9, min_free=0)
    ing = Fast(f"http://127.0.0.1:{p.server_address[1]}/live.ts", s)
    ing.start()
    assert wait_until(lambda: len(s.runs()) >= 2, 10)
    ing.stop(wait=5)
    assert p.connections >= 2
    assert ing.merged == 0


def test_a_reconnect_that_repeats_the_end_goes_on_in_the_same_run(
        provider, tmp_path):
    # Providers start a connection with the last seconds they hold: the
    # dropped one's end comes again. Skipped, the run goes on - no gap for
    # playback to stop at, and nothing played twice.
    p = provider(30, mode="count", repeat=15)
    s = SegmentStore(tmp_path / "s", 60, 10**9, min_free=0)
    ing = Fast(f"http://127.0.0.1:{p.server_address[1]}/live.ts", s)
    ing.start()
    assert wait_until(lambda: ing.merged >= 2, 10)
    ing.stop(wait=5)
    assert len(s.runs()) == 1
    data = stored(s)
    numbers = [int.from_bytes(data[i + 1:i + 5], "big")
               for i in range(0, len(data) - 187, 188)]
    assert numbers == list(range(len(numbers))), "a packet twice or missing"


def test_small_pieces_go_into_the_store_in_blocks(tmp_path):
    # A provider sending a few packets at a time: one append each took the
    # store's lock, wrote, flushed and woke the readers hundreds of times a
    # second, and the ingest fell behind until the provider hung up.
    s = SegmentStore(tmp_path / "s", 60, 10**9, min_free=0)
    calls = []
    real = s.append
    s.append = lambda data: (calls.append(len(data)), real(data))

    class Resp:
        def raise_for_status(self):
            pass

        def iter_content(self, _n):
            for i in range(2000):
                yield packet(i) * 7          # 1316 bytes, as UDP relays send

        def close(self):
            pass

    class Once(Ingest):
        BLOCK_SECONDS = 60.0

    ing = Once("http://x/live.ts", s, opener=lambda **kw: Resp())
    ing.start()
    assert wait_until(lambda: sum(calls) >= 2000 * 1316, 10)
    ing.stop(wait=5)
    assert len(calls) < 30, f"{len(calls)} appends for 2000 pieces"


def test_gives_up_when_nothing_comes(provider, tmp_path):
    p = provider(0, status=404)
    s = SegmentStore(tmp_path / "s", 60, 10**9, min_free=0)
    ing = Fast(f"http://127.0.0.1:{p.server_address[1]}/live.ts", s)
    ing.start()
    assert wait_until(lambda: ing.state == "failed", 10)
    assert p.connections == Fast.MAX_FAILURES


def test_a_connection_taken_away_again_and_again_is_contested(
        provider, tmp_path):
    # Another device or app on the account: the provider takes the one
    # connection from one to give it to the other, over and over. Seen, the
    # app can say so instead of stuttering on in silence.
    p = provider(20, mode="count")
    s = SegmentStore(tmp_path / "s", 60, 10**9, min_free=0)
    ing = Fast(f"http://127.0.0.1:{p.server_address[1]}/live.ts", s)
    assert not ing.contested
    ing.start()
    assert wait_until(lambda: ing.contested, 10)
    ing.stop(wait=5)


def test_one_drop_is_not_a_tug_of_war(provider, tmp_path):
    p = provider(-1)
    s = SegmentStore(tmp_path / "s", 60, 10**9, min_free=0)
    ing = Fast(f"http://127.0.0.1:{p.server_address[1]}/live.ts", s)
    ing.start()
    assert wait_until(lambda: s.newest() is not None, 5)
    assert not ing.contested
    ing.stop(wait=5)
