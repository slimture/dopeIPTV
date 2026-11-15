"""Nerd's backup addresses (core/mirrors.py): the list as typed - each bad
line refused with its reason - when a client moves on to the next address
(a server that cannot be reached or answers 5xx, never a refused login),
the one that answered kept per source, and every address the client hands
out built from it: Xtream's API, streams and guide, an M3U playlist, a
Stalker portal's handshake. Against fake servers on the loopback."""
from __future__ import annotations

import json
import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from dopeiptv.core import mirrors, nerd
from dopeiptv.core._lazy_requests import requests
from dopeiptv.core.nerd_check import FAIL, INFO, OK, WARN
from dopeiptv.providers.client import M3UClient, XtreamClient, make_client


class _S(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value


@pytest.fixture
def nerd_on():
    s = _S(nerd_enabled="true")
    nerd.install(s)
    yield s
    nerd.install(None)


@pytest.fixture(autouse=True)
def _local_is_direct(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost,::1")


def dead_address() -> str:
    """An address on the loopback where nothing listens: refused at once."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


class Server:
    """A fake Xtream server: player_api.php, xmltv.php, /live/... - or, with
    *status*, that status for everything."""

    def __init__(self, status: int = 200, playlist: bytes = b"") -> None:
        self.hits: list[str] = []
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                parts = urlsplit(self.path)
                srv.hits.append(parts.path)
                if status != 200:
                    return self._send(status, b"down")
                q = {k: v[0] for k, v in parse_qs(parts.query).items()}
                if parts.path == "/player_api.php":
                    if q.get("action") == "get_live_streams":
                        return self._send(200, json.dumps([{"stream_id": 1, "name": "One"}]).encode())
                    return self._send(200, json.dumps({"user_info": {
                        "auth": 1, "status": "Active", "max_connections": "2",
                        "active_cons": "1"}}).encode())
                if parts.path == "/xmltv.php":
                    return self._send(200, b"<tv></tv>")
                if parts.path.startswith("/live/"):
                    return self._send(200, b"G" * 188 * 10)
                if parts.path == "/list.m3u":
                    return self._send(200, playlist)
                self._send(404, b"no")

            def _send(self, code, body):
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def servers():
    made: list[Server] = []

    def make(**kw) -> Server:
        s = Server(**kw)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


# -- the list ------------------------------------------------------------------

def test_good_lines_are_kept_in_order_once_each():
    text = ("# the provider's mail of 3 May\n"
            "backup1.example.com:8080\n\n"
            "https://Backup2.example.com/\n"
            "http://BACKUP1.example.com:8080\n"
            "http://[2001:db8::1]:8000/path")
    assert mirrors.parse(text) == ["http://backup1.example.com:8080",
                                   "https://Backup2.example.com",
                                   "http://[2001:db8::1]:8000/path"]
    assert mirrors.problems(text) == []


@pytest.mark.parametrize("line, why", [
    ("http://a b.example.com", "space"),
    ("rtmp://a.example.com", "scheme"),
    ("ftp://a.example.com", "scheme"),
    ("http://", "host"),
    ("http://:8080", "host"),
    ("http://bad_host$.com", "host"),
    ("http://a.example.com:99999", "port"),
    ("http://a.example.com:port", "port"),
    ("http://user:pass@a.example.com", "login"),
    ("http://a.example.com/get.php?username=u", "query"),
    ("http://a.example.com/#x", "query"),
])
def test_a_bad_line_is_refused_with_its_reason(line, why):
    assert mirrors.problem(line) == why
    p = mirrors.problems(f"ok.example.com\n{line}")
    assert [(x.line, x.reason) for x in p] == [(2, why)]
    assert mirrors.parse(line) == []


def test_a_playlist_may_carry_a_query():
    url = "https://b.example.com/get.php?username=u&password=p&type=m3u_plus"
    assert mirrors.problem(url, "m3u") == ""
    assert mirrors.parse(url, "m3u") == [url]


def test_too_many_addresses():
    text = "\n".join(f"h{i}.example.com" for i in range(mirrors.MAX_ADDRESSES + 2))
    bad = mirrors.problems(text)
    assert [b.reason for b in bad] == ["many", "many"]
    assert len(mirrors.parse(text)) == mirrors.MAX_ADDRESSES


def test_addresses_only_while_nerd_is_on():
    opts = {mirrors.KEY: "b.example.com\nmain.example.com"}
    nerd.install(_S())
    assert mirrors.addresses("main.example.com", opts) == ["http://main.example.com"]
    nerd.install(_S(nerd_enabled="true"))
    try:
        assert mirrors.addresses("main.example.com", opts) == [
            "http://main.example.com", "http://b.example.com"]
        assert mirrors.addresses("main.example.com", opts, "enigma2") == ["http://main.example.com"]
    finally:
        nerd.install(None)


def test_what_moves_on_and_what_does_not():
    assert mirrors.fails_over_status(500) and mirrors.fails_over_status(502)
    assert mirrors.fails_over_status(521) and mirrors.fails_over_status(526)
    for code in (200, 301, 401, 403, 404, 407, 458):
        assert not mirrors.fails_over_status(code)
    assert not mirrors.fails_over_status(None)
    ex = requests.exceptions
    for e in (ex.ConnectionError("dns"), ex.ConnectTimeout("slow"), ex.SSLError("tls"),
              ConnectionRefusedError()):
        assert mirrors.fails_over_error(e), e
    for e in (ex.ReadTimeout("read"), ex.ProxyError("proxy"), ValueError("x"),
              ex.ChunkedEncodingError("cut")):
        assert not mirrors.fails_over_error(e), e


def test_shown_never_has_a_path_or_query():
    assert mirrors.shown("https://h.example.com:8443/get.php?username=u&password=p") == \
        "https://h.example.com:8443"
    assert mirrors.shown("h.example.com") == "http://h.example.com"


def test_the_picker_starts_from_the_remembered_one_and_tells_a_change():
    told: list[str] = []
    p = mirrors.Picker(["http://a.example.com", "http://b.example.com", "http://c.example.com"],
                       current="http://B.example.com/", on_switch=told.append)
    assert p.current == "http://b.example.com"
    assert p.order() == ["http://b.example.com", "http://a.example.com", "http://c.example.com"]
    assert not p.worked("http://b.example.com")
    assert p.worked("http://c.example.com") and told == ["http://c.example.com"]
    assert not p.worked("http://elsewhere.example.com")
    assert p.primary == "http://a.example.com"


def test_remembered_per_source():
    s = _S()
    mirrors.remember(s, "pl1", "http://b.example.com")
    assert s[mirrors.pick_key("pl1")] == "http://b.example.com"
    assert mirrors.remembered(s, "pl1") == "http://b.example.com"
    assert mirrors.remembered(s, "pl2") == "" and mirrors.remembered(None, "pl1") == ""


def test_the_tail_of_an_address():
    assert mirrors.tail("http://a.example.com/live/u/p/1.ts", "http://a.example.com") == "/live/u/p/1.ts"
    assert mirrors.tail("http://a.example.comx/live", "http://a.example.com") is None
    assert mirrors.tail("http://other.example.com/live", "http://a.example.com") is None


# -- Xtream ---------------------------------------------------------------------------

def xtream(nerd_on, primary: str, *backups: str, pid: str = "pl1", **kw) -> XtreamClient:
    return make_client({"id": pid, "kind": "xtream", "server": primary, "username": "alice",
                        "password": "s3cretpw", "options": {mirrors.KEY: "\n".join(backups)}, **kw})


def test_a_dead_server_hands_over_to_the_next(nerd_on, servers):
    good = servers()
    c = xtream(nerd_on, dead_address(), good.url)
    assert c.authenticate()["user_info"]["auth"] == 1
    assert c.server == good.url
    assert nerd_on[mirrors.pick_key("pl1")] == good.url
    # Every address it hands out is the one that answered.
    assert c.live_url(5).startswith(good.url + "/live/alice/s3cretpw/5.")
    assert c.vod_url(6).startswith(good.url + "/movie/")
    assert all(u.startswith(good.url) for u in c.timeshift_urls(
        5, __import__("datetime").datetime(2026, 1, 1, 20, 0), 30))
    assert c.xmltv() == b"<tv></tv>"


def test_the_one_that_answered_is_written_on_the_window_s_thread(nerd_on, servers):
    """The lists, the guide and the player's probe meet a dead server on
    whichever worker asked, and the address that answered was written into
    the settings right there: one QSettings object written from two
    threads. Now the write is queued to the window's thread."""
    import time
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    wrote: list[bool] = []
    set_value = nerd_on.setValue

    def recording(key, value):
        wrote.append(threading.current_thread() is threading.main_thread())
        set_value(key, value)
    nerd_on.setValue = recording
    good = servers()
    c = xtream(nerd_on, dead_address(), good.url)
    t = threading.Thread(target=c.authenticate)
    t.start()
    t.join(20)
    assert c.server == good.url, "the worker went on with the one that answered"
    assert wrote == [] and mirrors.pick_key("pl1") not in nerd_on, wrote
    end = time.monotonic() + 5
    while not wrote and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    assert wrote == [True] and nerd_on[mirrors.pick_key("pl1")] == good.url, wrote


def test_the_remembered_one_is_asked_first(nerd_on, servers):
    first, second = servers(), servers()
    nerd_on[mirrors.pick_key("pl1")] = second.url
    c = xtream(nerd_on, first.url, second.url)
    assert c.server == second.url
    c.authenticate()
    assert first.hits == [] and second.hits == ["/player_api.php"]


def test_5xx_and_cloudflare_move_on(nerd_on, servers):
    for code in (503, 522):
        bad, good = servers(status=code), servers()
        c = xtream(nerd_on, bad.url, good.url, pid=f"p{code}")
        c.authenticate()
        assert c.server == good.url, code


@pytest.mark.parametrize("code", [401, 403, 404])
def test_a_refused_login_or_missing_path_never_moves_on(nerd_on, servers, code):
    bad, good = servers(status=code), servers()
    c = xtream(nerd_on, bad.url, good.url, pid=f"q{code}")
    with pytest.raises(requests.HTTPError):
        c.authenticate()
    assert c.server == bad.url and good.hits == []
    assert mirrors.pick_key(f"q{code}") not in nerd_on


def test_all_down_fails_as_before(nerd_on):
    c = xtream(nerd_on, dead_address(), dead_address())
    with pytest.raises(requests.ConnectionError):
        c.authenticate()
    # ... and the fail-fast cooldown holds: no new waits on either.
    with pytest.raises(requests.ConnectionError, match="(?i)cooldown|again|seconds|\\d"):
        c.live_categories()


def test_nothing_changes_without_backup_addresses_or_nerd(servers):
    good = servers()
    nerd.install(_S())
    try:
        c = make_client({"id": "x", "kind": "xtream", "server": dead_address(), "username": "u",
                         "password": "p", "options": {mirrors.KEY: good.url}})
    finally:
        nerd.install(None)
    assert c.picker is None
    with pytest.raises(requests.ConnectionError):
        c.authenticate()
    assert good.hits == []


def test_the_log_says_where_it_went_but_no_login(nerd_on, servers, caplog):
    good = servers()
    dead = dead_address()
    with caplog.at_level(logging.INFO, logger="dopeiptv"):
        c = xtream(nerd_on, dead, good.url)
        c.authenticate()
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "trying the next address" in text and good.url in text
    assert "s3cretpw" not in text and "alice" not in text


def test_a_stream_on_a_dead_server_is_found_on_the_next(nerd_on, servers):
    good = servers()
    dead = dead_address()
    c = xtream(nerd_on, dead, good.url)
    url = c.live_url(7)
    assert url.startswith(dead)
    assert c.stream_failover(url, None) == good.url
    assert c.server == good.url and c.live_url(7).startswith(good.url)
    # A stream that answered (a refused one too) says the server is up.
    c2 = xtream(nerd_on, dead, good.url, pid="pl9")
    assert c2.stream_failover(c2.live_url(7), 403) is None


def test_the_guide_download_moves_on_too(nerd_on, servers):
    from dopeiptv.providers.epg import XmltvGuide
    good = servers()
    c = xtream(nerd_on, dead_address(), good.url)
    g = XmltvGuide(c)
    r = g._download()
    assert r == b"<tv></tv>" and c.server == good.url


# -- M3U and Stalker -------------------------------------------------------------------

def test_a_playlist_from_its_backup_address(nerd_on, servers):
    body = b"#EXTM3U\n#EXTINF:-1 group-title=\"News\",One\nhttp://cdn.example/1.ts\n"
    good = servers(playlist=body)
    c = make_client({"id": "m1", "kind": "m3u", "server": dead_address() + "/list.m3u",
                     "options": {mirrors.KEY: good.url + "/list.m3u"}})
    assert isinstance(c, M3UClient)
    c.authenticate()
    assert [r["name"] for r in c.live_streams()] == ["One"]
    assert c.server == good.url + "/list.m3u"


def test_a_portal_hands_over_at_the_handshake(nerd_on):
    from tests.test_stalker import Portal
    p = Portal()
    threading.Thread(target=p.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        c = make_client({"id": "s1", "kind": "stalker", "server": dead_address() + "/c/",
                         "mac": "00:1A:79:12:34:56",
                         "options": {mirrors.KEY: p.url + "/stalker_portal/c/"}})
        assert c.authenticate()["user_info"]["auth"] == 1
        assert c.server == p.url + "/stalker_portal/c"
        assert nerd_on[mirrors.pick_key("s1")] == p.url + "/stalker_portal/c"
    finally:
        p.shutdown()
        p.server_close()


def test_a_refusing_portal_does_not_hand_over(nerd_on):
    from tests.test_stalker import Portal
    from dopeiptv.i18n import LocalizedError
    bad, good = Portal(blocked=True), Portal()
    for p in (bad, good):
        threading.Thread(target=p.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        c = make_client({"id": "s2", "kind": "stalker", "server": bad.url + "/stalker_portal/c/",
                         "mac": "00:1A:79:12:34:56",
                         "options": {mirrors.KEY: good.url + "/stalker_portal/c/"}})
        with pytest.raises(LocalizedError):
            c.authenticate()
        assert good.handshakes == 0
    finally:
        for p in (bad, good):
            p.shutdown()
            p.server_close()


# -- the line under the list -------------------------------------------------------------

def test_the_line_says_which_is_in_use():
    c = mirrors.check_list("b.example.com\nc.example.com", "xtream", "a.example.com",
                           in_use="http://b.example.com")
    assert c.state == OK and c.key == "nerd_ck_mr_list"
    assert c.params == {"host": "http://b.example.com", "n": 2}
    assert "→ http://b.example.com" in c.detail
    assert mirrors.check_list("", "xtream", "a.example.com").state == INFO
    bad = mirrors.check_list("ok.example.com\nrtmp://x", "xtream", "a.example.com")
    assert bad.state == FAIL and bad.key == "nerd_ck_mr_scheme" and bad.params["n"] == 2
    assert mirrors.check_list("x", "enigma2", "a").key == "nerd_ck_mr_kind"


def test_each_address_tested(servers):
    good, broken = servers(), servers(status=502)
    dead = dead_address()
    src = {"username": "alice", "password": "s3cretpw"}
    c = mirrors.check_each([good.url, broken.url, dead], "xtream", src, in_use=good.url,
                           secrets=["alice", "s3cretpw"])
    assert c.state == WARN and c.params["up"] == 1 and c.params["n"] == 3
    assert "(in use)" in c.detail and "HTTP 502" in c.detail
    assert "s3cretpw" not in c.detail and "alice" not in c.detail
    assert mirrors.check_each([good.url, good.url + "/"], "xtream", src).state == OK
    assert mirrors.check_each([dead, dead_address()], "m3u").state == FAIL
