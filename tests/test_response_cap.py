"""requests reads an answer whole into memory unless the caller streams it,
and a server decides how long its answer is: a playlist, a list or a device
description that never ended was read until the memory was gone. Every
session and call made through the app's stand-in for requests counts what
it reads whole and ends the read past a ceiling far above any real answer
(dopeiptv/core/_lazy_requests.py); what the caller streams is never
counted. A document nested deeper than Python reads is "not JSON", not a
RecursionError."""
from __future__ import annotations

import gzip
import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dopeiptv.core import _lazy_requests
from dopeiptv.core._lazy_requests import requests

CHUNK = b"#EXTINF:-1,A channel\nhttp://tv.example/1.ts\n" * 1500      # 66 KB


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass

    def _endless(self) -> None:
        self.server.endless += 1
        try:
            for _ in range(20000):                # 1.3 GB if anyone read it all
                self.wfile.write(CHUNK)
                self.server.sent += len(CHUNK)
        except OSError:
            pass

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path in ("/endless", "/player_api.php", "/list.m3u"):
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self._endless()
        elif path == "/small":
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/deep":
            body = b"[" * 200_000 + b"]" * 200_000
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/bomb":
            body = gzip.compress(b"\x00" * (64 << 20))
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/small")
            self.end_headers()
            self._endless()
        elif path == "/digest":
            if "Digest" in (self.headers.get("Authorization") or ""):
                self.send_response(200)
                self.end_headers()
                self._endless()
                return
            nonce = hashlib.md5(b"n").hexdigest()
            body = b"login"
            self.send_response(401)
            self.send_header("WWW-Authenticate", f'Digest realm="box", nonce="{nonce}", qop="auth"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.daemon_threads = True
    srv.endless = srv.sent = 0
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def small_ceiling(monkeypatch):
    """1 MiB for anything, 2 MiB for a source's lists: the real ones are
    256 MiB and 1 GiB, which a test would take minutes to reach."""
    monkeypatch.setattr(_lazy_requests, "RESPONSE_MAX", 1 << 20)
    monkeypatch.setattr(_lazy_requests, "LIST_MAX", 2 << 20)


def _too_large():
    return _lazy_requests._classes()["ResponseTooLarge"]


def test_the_ceilings_are_far_above_any_real_answer():
    assert _lazy_requests.RESPONSE_MAX == 256 << 20 and _lazy_requests.LIST_MAX == 1 << 30
    assert issubclass(_too_large(), requests.RequestException), "handled as any failed request is"


def test_an_answer_read_whole_ends_at_the_ceiling(server, small_ceiling):
    with pytest.raises(_too_large()):
        requests.get(server.url + "/endless", timeout=10)
    assert server.sent < 8 << 20, "the server went on sending and the app went on reading"
    s = requests.Session()
    with pytest.raises(_too_large()):
        s.get(server.url + "/endless", timeout=10)
    with pytest.raises(_too_large()):
        s.post(server.url + "/endless", data={"a": "b"}, timeout=10) if False else s.request(
            "GET", server.url + "/endless", timeout=10)
    # A session's own ceiling, for a source's lists.
    s.max_response = 3 << 20
    before = server.sent
    with pytest.raises(_too_large()):
        s.get(server.url + "/endless", timeout=10)
    assert server.sent - before >= 3 << 20
    # What fits is read as ever.
    assert requests.get(server.url + "/small", timeout=10).json() == {"ok": True}
    assert s.get(server.url + "/small", timeout=10).content == b'{"ok": true}'


def test_what_the_caller_streams_is_not_counted(server, small_ceiling):
    """A live channel, a download to disk, a guide read with its own
    ceiling: theirs to read as far as they will."""
    r = requests.get(server.url + "/endless", stream=True, timeout=10)
    read = 0
    for chunk in r.iter_content(64 * 1024):
        read += len(chunk)
        if read > 4 << 20:
            break
    r.close()
    assert read > 4 << 20


def test_a_redirects_own_body_and_a_packed_answer_are_counted(server, small_ceiling):
    with pytest.raises(_too_large()):
        requests.get(server.url + "/redirect", timeout=10)
    with pytest.raises(_too_large()):
        requests.get(server.url + "/redirect", stream=True, timeout=10)
    # 64 MiB that came as 64 KB: counted as what it unpacks to.
    with pytest.raises(_too_large()):
        requests.get(server.url + "/bomb", timeout=10)


def test_a_login_asked_for_twice_is_counted_too(server, small_ceiling):
    """A box that asks for a digest login: the answer after it comes from
    the adapter itself, past the session."""
    s = requests.Session()
    with pytest.raises(_too_large()):
        s.get(server.url + "/digest", auth=requests.auth.HTTPDigestAuth("u", "p"), timeout=10)


def test_a_document_nested_too_deep_is_not_json(server):
    r = requests.get(server.url + "/deep", timeout=10)
    with pytest.raises(ValueError):
        r.json()
    with pytest.raises(requests.RequestException):
        r.json()


def test_a_playlist_and_a_list_that_never_end_fail_as_a_request_does(server, small_ceiling):
    from dopeiptv.providers.client import M3UClient, XtreamClient, make_client
    m3u = make_client({"kind": "m3u", "server": server.url + "/list.m3u"})
    assert m3u.session.max_response == 2 << 20
    with pytest.raises(requests.RequestException):
        m3u._fetch()
    assert M3UClient(server.url + "/list.m3u").session is not None
    x = make_client({"kind": "xtream", "server": server.url, "username": "anna", "password": "hunter22"})
    assert isinstance(x, XtreamClient) and x.session.max_response == 2 << 20
    with pytest.raises(requests.RequestException):
        x.live_categories()
    assert server.sent < 40 << 20


def test_a_list_nested_too_deep_is_a_failed_request_and_the_copy_kept_is_served(server, tmp_path):
    from dopeiptv.providers.client import XtreamClient
    c = XtreamClient(server.url, "anna", "hunter22", cache_path=str(tmp_path / "lists_a.json"))
    c._list_cache[("get_live_categories",)] = (1.0, [{"category_id": "1", "category_name": "News"}])
    c._api = lambda **p: requests.get(server.url + "/deep", timeout=10).json()
    assert c.live_categories() == [{"category_id": "1", "category_name": "News"}]


def test_a_playlist_file_is_read_as_far_as_one_from_the_network(tmp_path, small_ceiling):
    from dopeiptv.core import picture_files
    from dopeiptv.providers.client import M3UClient
    big = tmp_path / "big.m3u"
    big.write_bytes(b"#EXTM3U\n" + b"#" * (3 << 20))
    with pytest.raises(ValueError):
        M3UClient(str(big))._fetch()
    import os
    if os.path.exists("/dev/zero"):
        with pytest.raises(ValueError):
            M3UClient("/dev/zero")._fetch()
    ok = tmp_path / "lists" / "tv.m3u"
    ok.parent.mkdir()
    ok.write_text("#EXTM3U\n#EXTINF:-1,One\nhttp://tv.example/1.ts\n", encoding="utf-8")
    assert "One" in M3UClient(str(ok))._fetch()
    # Logos beside a playlist of the person's own are theirs to list.
    assert picture_files.trusted(str(ok.parent / "logos" / "one.png"))
    assert not picture_files.trusted(str(tmp_path / "elsewhere" / "one.png"))


def test_a_route_of_nerds_has_the_ceiling_too(server, small_ceiling):
    from dopeiptv.core import net_adapter
    from dopeiptv.core._lazy_requests import capped_adapter

    class Route:
        active = True

        def changes(self, *a):
            return False

        def pin(self, *a):
            return None
    s = requests.Session()
    s.max_response = 2 << 20
    assert net_adapter.mount(s, Route())
    a = s.get_adapter(server.url)
    assert isinstance(a, capped_adapter()) and type(a).__name__ == "NetAdapter" and a.max_response == 2 << 20
    with pytest.raises(_too_large()):
        s.get(server.url + "/endless", timeout=10)


def test_a_replaced_call_of_requests_own_is_the_one_called(monkeypatch):
    """A test (or anything else) that stands in for requests.get is asked,
    as it was before the stand-in had calls of its own."""
    import requests as real
    monkeypatch.setattr(real, "get", lambda url, **kw: ("theirs", url))
    assert requests.get("http://x.example/") == ("theirs", "http://x.example/")
    monkeypatch.undo()
    assert requests.get is _lazy_requests._classes()["get"]
    assert requests.Session is _lazy_requests._classes()["Session"]
    assert requests.RequestException is real.RequestException and requests.auth is real.auth
