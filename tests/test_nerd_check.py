"""The checks behind the Nerd page's ✓ and ✗: each tried for real against
fake servers, a fake proxy, real folders and a real mpv core - and never a
password in what they show."""
from __future__ import annotations

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dopeiptv.core import nerd_check as ck


@pytest.fixture(autouse=True)
def _local_is_direct(monkeypatch):
    # This machine may have a proxy of its own in the environment; the
    # fake servers are on the loopback.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost,::1")


class Server:
    """Answers with *status* (and *body*); records each request. proxy=True
    forwards absolute-URI requests as an HTTP proxy does (or answers 407)."""

    def __init__(self, status=200, body=b"{}", delay=0.0, proxy=False, proxy_status=None,
                 family=socket.AF_INET, host="127.0.0.1"):
        self.requests: list[dict] = []
        srv = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                srv.requests.append({"path": self.path,
                                     "headers": {k.lower(): v for k, v in self.headers.items()}})
                if delay:
                    time.sleep(delay)
                if proxy and proxy_status:
                    self._answer(proxy_status, b"")
                    return
                if proxy and self.path.startswith("http://"):
                    import urllib.request
                    req = urllib.request.Request(self.path, headers={
                        k: v for k, v in self.headers.items() if k.lower() not in ("proxy-connection", "host")})
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    try:
                        with opener.open(req, timeout=5) as r:
                            self._answer(r.status, r.read())
                    except urllib.error.HTTPError as e:
                        self._answer(e.code, b"")
                    return
                self._answer(status, body)

            def _answer(self, code, data):
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Server", "fake/1.0")
                self.end_headers()
                try:
                    self.wfile.write(data)
                except OSError:
                    pass

        cls = ThreadingHTTPServer
        if family == socket.AF_INET6:
            class V6(ThreadingHTTPServer):
                address_family = socket.AF_INET6
            cls = V6
        self.httpd = cls((host, 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# -- secrets -----------------------------------------------------------------

def test_no_login_in_what_a_check_shows():
    url = "http://john:pw1@h:8080/live/john/s3cret/1.ts?username=john&password=s3cret&x=1"
    shown = ck.redact_url(url)
    assert "john" not in shown and "s3cret" not in shown and "pw1" not in shown
    assert shown.startswith("http://•••@h:8080/live/•••/•••/1.ts") and "x=1" in shown
    assert ck.scrub("refused for s3cret at h", ["s3cret", ""]) == "refused for ••• at h"


def test_the_address_asked_of_each_kind_of_source():
    assert ck.probe_url({"kind": "xtream", "server": "h.tv:8080", "username": "a",
                         "password": "b"}) == "http://h.tv:8080/player_api.php?username=a&password=b"
    assert ck.probe_url({"kind": "m3u", "server": "https://h/x.m3u"}) == "https://h/x.m3u"
    assert ck.probe_url({"kind": "enigma2", "server": "http://10.0.0.2/"}) == "http://10.0.0.2/web/about"
    assert ck.probe_url({"kind": "tvheadend", "server": "http://t:9981"}) == "http://t:9981/api/serverinfo"
    assert ck.probe_url({"kind": "hdhomerun", "server": "10.0.0.9"}) == "http://10.0.0.9/discover.json"
    assert ck.probe_url({"kind": "local"}) is None and ck.probe_url(None) is None


# -- requests: user agent, headers, the source answering -----------------------

def test_the_source_sees_the_user_agent_and_headers():
    s = Server()
    try:
        r = ck.check_request(s.url + "/player_api.php?username=u1&password=topsecret", "VLC/3.0.20",
                             {"X-Forwarded-For": "203.0.113.7", "Authorization": "Bearer tok123"},
                             secrets=["u1", "topsecret"])
        assert r.state == ck.OK and r.key == "nerd_ck_http_ok" and r.params["code"] == 200
        seen = s.requests[-1]["headers"]
        assert seen["user-agent"] == "VLC/3.0.20" and seen["x-forwarded-for"] == "203.0.113.7"
        # What it shows: the request and the answer, not the login or the token.
        assert "User-Agent: VLC/3.0.20" in r.detail and "HTTP 200" in r.detail
        assert "topsecret" not in r.detail and "tok123" not in r.detail and "u1" not in r.detail
    finally:
        s.close()


@pytest.mark.parametrize("status, state, key", [
    (403, ck.FAIL, "nerd_ck_denied"), (401, ck.FAIL, "nerd_ck_denied"),
    (404, ck.WARN, "nerd_ck_http_odd"), (503, ck.FAIL, "nerd_ck_server_error"),
])
def test_what_the_source_answers_is_said_with_its_code(status, state, key):
    s = Server(status=status)
    try:
        r = ck.check_request(s.url + "/x")
        assert (r.state, r.key, r.params["code"]) == (state, key, status)
        assert f"HTTP {status}" in r.detail
    finally:
        s.close()


def test_nobody_there_and_too_slow():
    r = ck.check_request(f"http://127.0.0.1:{_closed_port()}/x")
    assert (r.state, r.key) == (ck.FAIL, "nerd_ck_unreachable")
    assert "ConnectionError" in r.detail
    slow = Server(delay=2.0)
    try:
        r = ck.check_request(slow.url + "/x", timeout=0.5)
        assert (r.state, r.key) == (ck.FAIL, "nerd_ck_timeout")
    finally:
        slow.close()


# -- the proxy -----------------------------------------------------------------

def test_through_the_proxy_to_the_source():
    source, proxy = Server(), Server(proxy=True)
    try:
        r = ck.check_request(source.url + "/web/about", "Kodi/21", proxy=proxy.url, about_proxy=True)
        assert (r.state, r.key) == (ck.OK, "nerd_ck_proxy_ok")
        assert proxy.requests and proxy.requests[-1]["path"].startswith(source.url)   # it went through
        assert source.requests and source.requests[-1]["headers"]["user-agent"] == "Kodi/21"
        assert f"Proxy: {proxy.url}" in r.detail
    finally:
        source.close()
        proxy.close()


def test_a_proxy_that_is_down_or_wants_a_login():
    r = ck.check_request("http://192.0.2.1/x", proxy=f"http://127.0.0.1:{_closed_port()}")
    assert (r.state, r.key) == (ck.FAIL, "nerd_ck_proxy_down")
    assert "ProxyError" in r.detail
    p = Server(proxy=True, proxy_status=407)
    try:
        r = ck.check_request("http://192.0.2.1/x", proxy=p.url.replace("http://", "http://me:pw9@"),
                             secrets=["pw9"])
        assert (r.state, r.key, r.params["code"]) == (ck.FAIL, "nerd_ck_proxy_auth", 407)
        assert "pw9" not in r.detail
    finally:
        p.close()


def test_a_proxy_with_no_source_to_ask():
    p = Server(proxy=True)
    try:
        assert ck.check_proxy_listens(p.url).key == "nerd_ck_proxy_listens"
    finally:
        p.close()
    r = ck.check_proxy_listens(f"127.0.0.1:{_closed_port()}")
    assert (r.state, r.key) == (ck.FAIL, "nerd_ck_proxy_down")


# -- IP version ----------------------------------------------------------------

def test_ip_version_reaches_the_host_or_says_why_not():
    s = Server()
    try:
        r = ck.check_ip_family(s.url, "ipv4")
        assert (r.state, r.key, r.params["ip"]) == (ck.OK, "nerd_ck_ip_ok", "IPv4")
        r = ck.check_ip_family(s.url, "ipv6")              # 127.0.0.1 has no IPv6 address
        assert (r.state, r.key) == (ck.FAIL, "nerd_ck_ip_none")
        r = ck.check_ip_family(s.url, "auto")
        assert r.key == "nerd_ck_ip_auto" and r.params == {"v4": "✓", "v6": "✗"}
    finally:
        s.close()


def test_ipv6_for_real_where_the_machine_has_it():
    try:
        s = Server(family=socket.AF_INET6, host="::1")
    except OSError:
        pytest.skip("no IPv6 loopback here")
    try:
        r = ck.check_ip_family(f"http://[::1]:{s.port}/", "ipv6")
        assert (r.state, r.key, r.params["addr"]) == (ck.OK, "nerd_ck_ip_ok", "::1")
    finally:
        s.close()


# -- udpxy -----------------------------------------------------------------------

def test_udpxy_status_page():
    good = Server(body=b"<html><title>udpxy status</title></html>")
    other = Server(body=b"<html>router</html>")
    try:
        assert ck.check_udpxy(good.url).key == "nerd_ck_udpxy_ok"
        assert good.requests[-1]["path"] == "/status"
        assert (ck.check_udpxy(other.url).state, ck.check_udpxy(other.url).key) == (ck.WARN, "nerd_ck_udpxy_not")
    finally:
        good.close()
        other.close()
    r = ck.check_udpxy(f"127.0.0.1:{_closed_port()}")
    assert (r.state, r.key) == (ck.FAIL, "nerd_ck_udpxy_down")


# -- folders and files -----------------------------------------------------------

def test_picons_count_and_the_channels_they_match(tmp_path):
    (tmp_path / "svt1hd.png").write_bytes(b"x")
    (tmp_path / "1_0_19_283D_3FB_1_C00000_0_0_0.png").write_bytes(b"x")
    (tmp_path / "readme.txt").write_text("not a picon")
    rows = [{"stream_id": 1, "name": "SVT1 HD"},
            {"stream_id": 2, "name": "Das Erste", "_key": "1:0:19:283D:3FB:1:C00000:0:0:0:"},
            {"stream_id": 3, "name": "Kanal 5"}]
    r = ck.check_picons(str(tmp_path), rows)
    assert (r.state, r.key) == (ck.OK, "nerd_ck_picons")
    assert r.params == {"n": 2, "matched": 2, "total": 3}
    assert "Kanal 5" in r.detail
    assert ck.check_picons(str(tmp_path)).params == {"n": 2}
    assert ck.check_picons(str(tmp_path / "nope")).key == "nerd_ck_folder_missing"
    empty = tmp_path / "empty"
    empty.mkdir()
    assert ck.check_picons(str(empty)).key == "nerd_ck_picons_none"


def test_the_mpv_folder(tmp_path):
    assert ck.check_mpv_dir(str(tmp_path / "nope")).key == "nerd_ck_folder_missing"
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "stats.lua").write_text("--")
    r = ck.check_mpv_dir(str(tmp_path))
    assert (r.key, r.params) == ("nerd_ck_mpvconf_none", {"scripts": 1})
    (tmp_path / "mpv.conf").write_text("deband=yes\nvo=gpu\n[profile]\nscale=x\nvolume-max=150\n")
    r = ck.check_mpv_dir(str(tmp_path))
    assert (r.state, r.key, r.params) == (ck.WARN, "nerd_ck_mpvconf", {"n": 1, "scripts": 1})
    assert "vo" in r.detail and "stats.lua" in r.detail


def test_shader_files(tmp_path):
    a = tmp_path / "a.glsl"
    a.write_text("//")
    assert ck.check_shaders(str(a)).params == {"n": 1}
    r = ck.check_shaders(f"{a}\n{tmp_path / 'gone.glsl'}")
    assert (r.state, r.key, r.params) == (ck.FAIL, "nerd_ck_shaders_missing", {"missing": 1, "total": 2})
    assert "gone.glsl" in r.detail


# -- mpv options on a real core ----------------------------------------------------

def test_mpv_options_are_tried_on_a_real_mpv():
    pytest.importorskip("mpv")
    from dopeiptv.media.players import _libmpv, throwaway_mpv
    if _libmpv is None:
        pytest.skip("libmpv not available")
    r = ck.check_mpv_options("deband=yes\nvolume-max=150\nfoo-bar=1\ninterpolation=maybe\nvo=x11",
                             extra=[("video-sync", "display-resample")], make_mpv=throwaway_mpv)
    assert (r.state, r.key, r.params) == (ck.FAIL, "nerd_ck_mpv_refused", {"bad": 3, "n": 6})
    assert "unknown option" in r.detail and "foo-bar=1" in r.detail
    assert "value not accepted" in r.detail and "interpolation=maybe" in r.detail
    assert "vo" in r.detail and "picture out of the app" in r.detail
    good = ck.check_mpv_options("deband=yes\nscale=ewa_lanczossharp", make_mpv=throwaway_mpv)
    assert (good.state, good.key, good.params) == (ck.OK, "nerd_ck_mpv_ok", {"n": 2})
    assert ck.check_mpv_options("").key == "nerd_ck_mpv_empty"


def test_no_libmpv_says_so_rather_than_guess():
    r = ck.check_mpv_options("deband=yes", make_mpv=lambda: None)
    assert (r.state, r.key) == (ck.INFO, "nerd_ck_mpv_unavailable")
