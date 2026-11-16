"""The rewind buffer keeps only a stream. A web page opened as a channel
(File > Open address… with a page that was no stream) was asked again
twice a second for ever: each answer of a few kilobytes counted as a run
that streamed, so the failures never added up. Now such an answer is given
up at once, a few kilobytes and over is a failed try, the pool plays such
a channel without the buffer for a while, the local server says so at once
- and a real stream that drops is picked up again as before."""
from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from dopeiptv.core.dvr_ingest import Ingest, NotAStream, not_a_stream, ts_synced, wait_until
from dopeiptv.core.dvr_pool import DvrPool
from dopeiptv.core.dvr_server import LocalServer
from dopeiptv.core.dvr_store import SegmentStore

PKT = b"\x47" + b"x" * 187
PAGE = (b"<!DOCTYPE html>\n<html><head><title>Jellyfin</title></head>"
        b"<body><div id='app'></div>" + b"<script src='main.js'></script>" * 40
        + b"</body></html>\n")


class Answer(ThreadingHTTPServer):
    """Each connection: *body* (bytes, or a callable writing to the
    socket) with *ctype*, then the connection ends."""
    daemon_threads = True

    def __init__(self, body, ctype: str = "application/octet-stream") -> None:
        self.body, self.ctype = body, ctype
        self.connections = 0
        self.at: list[float] = []
        super().__init__(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/live/1.ts"

    def close(self) -> None:
        self.shutdown()
        self.server_close()


class _Handler(BaseHTTPRequestHandler):
    server: Answer

    def log_message(self, *a) -> None:
        pass

    def do_GET(self) -> None:
        srv = self.server
        srv.connections += 1
        srv.at.append(time.monotonic())
        self.send_response(200)
        self.send_header("Content-Type", srv.ctype)
        self.end_headers()
        try:
            if callable(srv.body):
                srv.body(self.wfile)
            else:
                self.wfile.write(srv.body)
            self.wfile.flush()
        except OSError:
            pass


@pytest.fixture
def answer():
    made: list[Answer] = []

    def make(body, ctype="application/octet-stream") -> Answer:
        a = Answer(body, ctype)
        made.append(a)
        return a
    yield make
    for a in made:
        a.close()


class Quick(Ingest):
    """The real rules - what counts as streamed - with short waits."""
    BACKOFF = 0.01
    BLOCK_SECONDS = 0.01


def _store(tmp_path: Path) -> SegmentStore:
    return SegmentStore(tmp_path / "s", 60, 10**9, min_free=0)


# -- what is no stream ---------------------------------------------------------------


def test_what_is_plainly_no_stream():
    assert not_a_stream(PAGE, "text/html; charset=utf-8") == "a web page"
    assert not_a_stream(b"\xef\xbb\xbf  <?xml version='1.0'?><error/>") == "a web page"
    assert not_a_stream(b'{"error": "not found"}', "application/json") == "JSON"
    assert not_a_stream(b"[1, 2]") == "JSON"
    assert not_a_stream(b"#EXTM3U\n#EXTINF:-1,x\nhttp://h/1.ts\n") == "a playlist"
    assert not_a_stream(b"Forbidden", "text/plain") == "text"
    assert not_a_stream(b"Max connections reached\r\n") == "text"


def test_a_stream_never_is():
    ts = PKT * 20
    assert ts_synced(ts) and not_a_stream(ts) is None
    # A relay need not start on a packet; nor does a stream say what it is.
    assert not_a_stream(b"\x00\x13junk" + ts, "text/html") is None
    assert ts_synced(b"zz" + ts)
    # Sound and pictures are no text: an MP3 radio, an FLV channel.
    mp3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + bytes(range(256)) * 4
    assert not_a_stream(mp3, "audio/mpeg") is None
    assert not_a_stream(b"FLV\x01\x05\x00\x00\x00\x09" + bytes(range(256)) * 3) is None
    assert not_a_stream(b"") is None
    assert not ts_synced(PKT) and not ts_synced(b"")


# -- the ingest ------------------------------------------------------------------------


def test_a_web_page_is_given_up_at_once(answer, tmp_path):
    srv = answer(PAGE, "text/html; charset=utf-8")
    s = _store(tmp_path)
    ing = Quick(srv.url, s)
    t0 = time.monotonic()
    ing.start()
    assert wait_until(lambda: ing.state == "failed", 5)
    assert time.monotonic() - t0 < 3
    assert ing.refused == "a web page" and srv.connections == 1
    assert s.newest() is None, "a page went into the buffer"
    time.sleep(0.3)
    assert srv.connections == 1 and not ing.alive, "asked again"


def test_a_page_is_no_stream_whatever_its_type_says(answer, tmp_path):
    srv = answer(PAGE, "application/octet-stream")
    ing = Quick(srv.url, _store(tmp_path))
    ing.start()
    assert wait_until(lambda: ing.state == "failed", 5)
    assert ing.refused == "a web page" and srv.connections == 1


def test_a_stream_said_to_be_a_page_still_streams(answer, tmp_path):
    def stream(w) -> None:
        for _ in range(4000):
            w.write(PKT * 7)
            time.sleep(0.002)
    srv = answer(stream, "text/html")
    s = _store(tmp_path)
    ing = Quick(srv.url, s)
    ing.start()
    assert wait_until(lambda: s.newest() is not None and s.newest().size > 188 * 50, 5)
    assert ing.state == "streaming" and ing.refused is None
    ing.stop(wait=5)


def test_a_few_kilobytes_and_over_each_time_gives_up(answer, tmp_path):
    # What the tester saw: a few KB, the connection over, asked again in
    # half a second - for ever, since each counted as streamed.
    srv = answer(PKT * 20)
    s = _store(tmp_path)
    ing = Quick(srv.url, s)
    t0 = time.monotonic()
    ing.start()
    assert wait_until(lambda: ing.state == "failed", 10), srv.connections
    assert time.monotonic() - t0 < 8
    assert srv.connections == Ingest.MAX_FAILURES
    assert ing.merged == 0, "the same few KB taken for the run going on"
    assert ing.refused is None
    time.sleep(0.3)
    assert srv.connections == Ingest.MAX_FAILURES and not ing.alive


def test_a_stream_that_drops_with_a_megabyte_each_time_goes_on(answer, tmp_path):
    # No regression: a real stream the provider ends after under SHORT_RUN
    # seconds, with a megabyte each time, is picked up again and again.
    def run(w) -> None:
        for _ in range(40):
            w.write(PKT * 140)          # about 26 KB, 40 times: about 1 MB
            time.sleep(0.005)
    srv = answer(run, "video/mp2t")
    s = _store(tmp_path)
    ing = Quick(srv.url, s)
    ing.start()
    want = Ingest.MAX_FAILURES + 2
    assert wait_until(lambda: srv.connections >= want, 20), srv.connections
    assert ing.state != "failed" and ing.alive
    assert all(b - a < Ingest.SHORT_RUN for a, b in zip(srv.at, srv.at[1:], strict=False))
    ing.stop(wait=5)
    assert s.total_bytes() > 1_000_000


# -- the local server ------------------------------------------------------------------


def _get(url: str, timeout: float = 10.0) -> int:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_a_live_request_is_answered_at_once_when_the_connection_gave_up(tmp_path):
    s = _store(tmp_path)
    over = [False]
    srv = LocalServer(s, lambda: over[0])
    try:
        threading.Timer(0.3, lambda: over.__setitem__(0, True)).start()
        t0 = time.monotonic()
        assert _get(srv.live_url()) == 404
        assert time.monotonic() - t0 < 3, "waited for bytes that cannot come"
    finally:
        srv.close()
        s.close()


# -- the pool --------------------------------------------------------------------------


class FakeIngest:
    """A provider connection that found a web page - or, for any address
    but a page's, a stream."""

    def __init__(self, url: str, store) -> None:
        self.url, self.store = url, store
        page = url.endswith("/page")
        self.state = "failed" if page else "streaming"
        self.refused = "a web page" if page else None
        self.connections = 1

    def start(self) -> None:
        pass

    def stop(self, wait: float = 0.0) -> None:
        pass

    @property
    def alive(self) -> bool:
        return False


def test_the_pool_plays_such_a_channel_without_the_buffer_for_a_while(tmp_path):
    made: list[str] = []

    def factory(url, store):
        made.append(url)
        return FakeIngest(url, store)
    pool = DvrPool(tmp_path, 600.0, 10**9, ingest_factory=factory, min_free=0)
    try:
        s = pool.open("http://p.invalid/page", "live:page")
        assert s.refused == "a web page" and s._gave_up()
        pool.release(s)        # the player starts again: its session is let go
        with pytest.raises(NotAStream):
            pool.open("http://p.invalid/page", "live:page")
        assert made == ["http://p.invalid/page"], "the provider asked again"
        # Another channel is not touched.
        other = pool.open("http://p.invalid/other", "live:other")
        assert other.refused is None and not other._gave_up()
        pool.release(other)
        # After a while the buffer is tried again: it may have been an outage.
        key_at = pool._refused["live:page"]
        pool._refused["live:page"] = (key_at[0], key_at[1] - pool.REFUSED_FOR - 1)
        again = pool.open("http://p.invalid/page", "live:page")
        assert again is not None and made.count("http://p.invalid/page") == 2
    finally:
        pool.close_all()
