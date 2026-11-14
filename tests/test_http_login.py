"""A login in a stream's address, answered as the server asks - basic or
digest - wherever the app fetches the stream itself: the rewind buffer's
ingest used to send only basic (requests' own way), so a Tvheadend channel
behind its default digest login failed there while mpv played it."""
from __future__ import annotations

import time

import pytest

from dopeiptv.core.dvr_ingest import Ingest
from dopeiptv.core.http_login import get_with_login, split_login
from dopeiptv.providers.boxes import with_login
from tests.test_nerd import TS, Recorder, _libmpv, _plays


@pytest.fixture
def server():
    made = []

    def make(auth=None):
        r = Recorder(auth)
        made.append(r)
        return r
    yield make
    for r in made:
        r.close()


def test_split_login():
    assert split_login("http://a%20b:c%40d@h:9981/x?y=1") == ("http://h:9981/x?y=1", "a b", "c@d")
    assert split_login("http://u@[fd00::1]:80/p") == ("http://[fd00::1]:80/p", "u", "")
    assert split_login("http://h/x") is None


@pytest.mark.parametrize("kind", ["basic", "digest"])
def test_each_kind_of_login(server, kind):
    srv = server(auth=(kind, "kodi", "s3 cret"))
    r = get_with_login(with_login(f"http://127.0.0.1:{srv.port}/stream/channel/c1", "kodi", "s3 cret"),
                       timeout=5)
    assert r.status_code == 200 and r.content == TS
    assert get_with_login(with_login(f"http://127.0.0.1:{srv.port}/x", "kodi", "nope"),
                          timeout=5).status_code == 401


def test_no_login_is_a_plain_get(server):
    srv = server()
    assert get_with_login(f"http://127.0.0.1:{srv.port}/a.ts", timeout=5).content == TS
    assert len(srv.requests) == 1


def test_the_rewind_buffer_ingest_opens_a_digest_channel(server):
    srv = server(auth=("digest", "kodi", "s3cret"))
    url = with_login(f"http://127.0.0.1:{srv.port}/stream/channel/c1?profile=pass", "kodi", "s3cret")
    r = Ingest._default_open(url, stream=True, timeout=5, headers={"User-Agent": "dopeIPTV/1.0"})
    assert r.status_code == 200 and r.raw.read() == TS
    assert srv.requests[-1]["headers"]["authorization"].lower().startswith("digest ")


def test_a_digest_channel_plays_through_the_rewind_buffer(server, tmp_path):
    """The whole way: the app's own buffer reads a Tvheadend channel behind
    digest, and a real mpv plays the buffer's local server."""
    from dopeiptv.core.dvr_session import DvrSession
    m = _libmpv()
    srv = server(auth=("digest", "kodi", "s3cret"))
    url = with_login(f"http://127.0.0.1:{srv.port}/stream/channel/c1", "kodi", "s3cret")
    s = DvrSession(url, tmp_path, keep_seconds=120, max_bytes=64 * 1024 * 1024)
    try:
        end = time.monotonic() + 10
        while time.monotonic() < end and not any(
                r["headers"].get("authorization", "").lower().startswith("digest")
                for r in srv.requests):
            time.sleep(0.05)
        assert _plays(m, s.live_url())
    finally:
        m.terminate()
        s.close()


class _Refuses:
    """A server that answers a request without a login with 403 - never a
    401 that would ask for one - and plays with basic."""

    def __init__(self) -> None:
        import base64
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        self.seen: list[str] = []
        want = "Basic " + base64.b64encode(b"kodi:s3cret").decode()
        rec = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                given = self.headers.get("Authorization") or ""
                rec.seen.append(given)
                body = TS if given == want else b""
                self.send_response(200 if given == want else 403)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def test_basic_goes_with_the_first_request_as_it_always_did():
    """1.4.5's ingest (requests.get(url)) sent the address's login as basic
    at once; a server that says 403 to a request without one never asks."""
    srv = _Refuses()
    try:
        url = with_login(f"http://127.0.0.1:{srv.port}/live/1.ts", "kodi", "s3cret")
        r = get_with_login(url, timeout=5)
        assert r.status_code == 200 and r.content == TS
        assert len(srv.seen) == 1 and srv.seen[0].startswith("Basic ")
        r = Ingest._default_open(url, stream=True, timeout=5, headers={"User-Agent": "dopeIPTV/1.0"})
        assert r.status_code == 200 and r.raw.read() == TS
        assert len(srv.seen) == 2
    finally:
        srv.close()


def test_the_ingest_takes_the_proxy_only_while_streams_go_through_it(monkeypatch):
    """"Streams through the proxy too" off: the player goes direct, and so
    does the rewind buffer's own connection."""
    from dopeiptv.core import http_login, nerd
    from tests.test_nerd import on
    got: list[dict] = []
    monkeypatch.setattr(http_login, "get_with_login", lambda url, **kw: got.append(kw))
    for player, want in (("true", {"http": "http://p:3128", "https": "http://p:3128"}),
                         ("false", None)):
        monkeypatch.setattr(nerd, "_current", on(nerd_proxy="http://p:3128", nerd_proxy_player=player))
        Ingest._default_open("http://h/live/1.ts", stream=True, timeout=5)
        assert got[-1].get("proxies") == want, (player, got[-1])
