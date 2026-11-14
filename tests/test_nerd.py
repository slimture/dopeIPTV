"""Nerd settings: parsing what is typed, what reaches the network and the
player - and, where libmpv is installed, a real mpv core playing from fake
servers to show that the user agent, headers, proxy, logins in the address
(basic and digest) and Enigma2's colon-laden stream addresses really work."""
from __future__ import annotations

import base64
import hashlib
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from dopeiptv.core import nerd
from dopeiptv.core.nerd import Nerd, parse_headers, parse_mpv_options

TS = (Path(__file__).parent / "data" / "tiny.ts").read_bytes()


class S(dict):
    def value(self, key, default=None):
        return self.get(key, default)


def on(**kw) -> Nerd:
    return Nerd(S(nerd_enabled="true", **kw))


# -- parsing ---------------------------------------------------------------------

def test_mpv_options_as_mpv_conf_writes_them():
    text = """
# a comment
--volume-max=150
hwdec = auto-safe   # trailing comment
deband
sub-font="DejaVu Sans"
vo=gpu
wid=123
config-dir=/tmp/x
bad key=1
[hq]
scale=ewa_lanczossharp
[default]
cache=yes
"""
    assert parse_mpv_options(text) == [
        ("volume-max", "150"), ("hwdec", "auto-safe"), ("deband", "yes"),
        ("sub-font", "DejaVu Sans"), ("cache", "yes")]


def test_headers_typed_one_a_line():
    assert parse_headers("Referer: http://a/\n# no\nX-Forwarded-For=1.2.3.4\nbroken\nEmpty:\n") == {
        "Referer": "http://a/", "X-Forwarded-For": "1.2.3.4"}


# -- off means off ---------------------------------------------------------------

def test_nothing_happens_while_nerd_is_off():
    n = Nerd(S(nerd_enabled="false", nerd_user_agent="vlc", nerd_proxy="http://p:3128",
               nerd_mpv_options="volume-max=150", nerd_vpn_guard="true"))
    assert n.core_options() == [] and n.scripts() == []
    assert n.stream_options() == {"user-agent": "dopeIPTV/1.0", "http-header-fields": [],
                                  "referrer": "", "http-proxy": ""}
    assert n.vpn_blocks() is False
    import requests
    s = requests.Session()
    before = dict(s.headers)
    n.apply_session(s)
    assert dict(s.headers) == before and not s.proxies
    assert Nerd(None).on is False


# -- the network -----------------------------------------------------------------

def test_user_agent_presets_custom_and_per_source():
    assert on(nerd_user_agent="vlc").user_agent().startswith("VLC/3.0")
    assert on(nerd_user_agent="MyBox/1.0").user_agent() == "MyBox/1.0"
    assert on().user_agent() == "dopeIPTV/1.0"
    assert on(nerd_user_agent="vlc").user_agent({"user_agent": "tivimate"}).startswith("TiviMate/")


def test_headers_referer_and_per_source():
    n = on(nerd_headers="X-A: 1\nX-B: 2", nerd_referer="http://global/")
    assert n.headers() == {"X-A": "1", "X-B": "2", "Referer": "http://global/"}
    assert n.headers({"headers": "X-B: 3", "referer": "http://src/"}) == {
        "X-A": "1", "X-B": "3", "Referer": "http://src/"}
    so = n.stream_options({"headers": "X-C: 4"})
    assert so["referrer"] == "http://global/" and "X-C: 4" in so["http-header-fields"]


def test_proxy_global_per_source_and_direct():
    n = on(nerd_proxy="http://10.0.0.1:3128")
    assert n.proxy() == "http://10.0.0.1:3128"
    assert n.proxy({"proxy": "direct"}) == ""
    assert n.proxy({"proxy": "http://other:8080"}) == "http://other:8080"
    assert on(nerd_proxy="http://p:1", nerd_proxy_player="false").stream_options()["http-proxy"] == ""


def test_session_gets_agent_headers_and_proxy():
    import requests
    s = requests.Session()
    on(nerd_user_agent="kodi", nerd_headers="X-Token: abc", nerd_proxy="http://p:3128").apply_session(s)
    assert s.headers["User-Agent"].startswith("Kodi/") and s.headers["X-Token"] == "abc"
    assert s.proxies == {"http": "http://p:3128", "https": "http://p:3128"}


def test_ip_family_for_the_apps_own_connections():
    from urllib3.util import connection
    original = connection.allowed_gai_family
    try:
        nerd.install(S(nerd_enabled="true", nerd_ip_family="ipv4"))
        assert connection.allowed_gai_family() == socket.AF_INET
        nerd.install(S(nerd_enabled="false", nerd_ip_family="ipv4"))
        assert connection.allowed_gai_family is original
    finally:
        nerd.install(None)
    assert connection.allowed_gai_family is original


# -- the player's options ----------------------------------------------------------

def test_core_options_from_every_part(tmp_path):
    (tmp_path / "mpv.conf").write_text("deband=yes\nvo=gpu\n[x]\nscale=y\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "a.lua").write_text("--", encoding="utf-8")
    (tmp_path / "scripts" / "notes.txt").write_text("", encoding="utf-8")
    n = on(nerd_mpv_config_dir=str(tmp_path), nerd_mpv_options="volume-max=130",
           nerd_net_timeout="12", nerd_reconnect_max="9", nerd_spdif="ac3,dts,bogus",
           nerd_shaders="~/s/FSRCNNX.glsl\n/x/y.glsl", nerd_video_sync="display-resample",
           nerd_interpolation="true", nerd_demux_max_mb="300", nerd_probe="thorough")
    opts = dict(n.core_options())
    assert opts["deband"] == "yes" and "vo" not in opts and "scale" not in opts
    assert opts["volume-max"] == "130" and opts["network-timeout"] == "12"
    assert "reconnect_delay_max=9" in opts["demuxer-lavf-o"]
    assert opts["audio-spdif"] == "ac3,dts"
    assert opts["glsl-shaders"].endswith("/x/y.glsl") and "~" not in opts["glsl-shaders"]
    assert opts["video-sync"] == "display-resample" and opts["interpolation"] == "yes"
    assert opts["demuxer-max-bytes"] == "300MiB" and opts["demuxer-lavf-analyzeduration"] == "5"
    assert n.scripts() == [str(tmp_path / "scripts" / "a.lua")]


# -- the VPN guard -----------------------------------------------------------------

def test_vpn_interfaces_by_name(monkeypatch):
    monkeypatch.setattr(nerd, "_is_up", lambda n: n != "tun1")
    assert nerd.vpn_interfaces(["lo", "eth0", "wg0", "tun1", "tailscale0", "wlan0"]) == ["wg0", "tailscale0"]


def test_vpn_guard(monkeypatch):
    names = [(1, "lo"), (2, "eth0")]
    monkeypatch.setattr(socket, "if_nameindex", lambda: names)
    monkeypatch.setattr(nerd, "_is_up", lambda n: True)
    g = on(nerd_vpn_guard="true")
    assert g.vpn_blocks() is True
    assert g.vpn_blocks(local_source=True) is False
    assert on(nerd_vpn_guard="true", nerd_vpn_lan="true").vpn_blocks(local_source=True) is True
    names.append((3, "wg0"))
    assert g.vpn_blocks() is False
    named = on(nerd_vpn_guard="true", nerd_vpn_iface="proton0")
    assert named.vpn_blocks() is True
    names.append((4, "proton0"))
    assert named.vpn_blocks() is False
    assert on(nerd_vpn_guard="false").vpn_blocks() is False


# -- a real mpv against fake servers -------------------------------------------------

def _libmpv():
    try:
        import mpv
        m = mpv.MPV(vo="null", ao="null", terminal=False)
    except Exception as e:     # no libmpv here: CI installs it, a dev box may not
        pytest.skip(f"libmpv not available: {e}")
    return m


class Recorder:
    """Serves the test clip; records what each request said. auth: None,
    ("basic"|"digest", user, pw). proxy=True answers absolute-URI requests
    as a proxy does."""

    def __init__(self, auth=None):
        self.requests: list[dict] = []
        self.auth = auth
        rec = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                hdrs = {k.lower(): v for k, v in self.headers.items()}
                rec.requests.append({"path": self.path, "headers": hdrs})
                if rec.auth and not rec.authorized(hdrs.get("authorization", "")):
                    self.send_response(401)
                    kind = rec.auth[0]
                    self.send_header("WWW-Authenticate", 'Digest realm="tvheadend", nonce="abc", qop="auth"'
                                     if kind == "digest" else 'Basic realm="enigma2"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Content-Length", str(len(TS)))
                self.end_headers()
                try:
                    self.wfile.write(TS)
                except OSError:
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def authorized(self, header: str) -> bool:
        kind, user, pw = self.auth
        if kind == "basic":
            return header == "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
        if not header.lower().startswith("digest "):
            return False
        f = dict(re.findall(r'(\w+)="?([^",]*)"?', header[7:]))
        ha1 = hashlib.md5(f"{user}:tvheadend:{pw}".encode()).hexdigest()
        ha2 = hashlib.md5(f"GET:{f.get('uri')}".encode()).hexdigest()
        if f.get("qop"):
            want = hashlib.md5(f"{ha1}:abc:{f.get('nc')}:{f.get('cnonce')}:{f.get('qop')}:{ha2}".encode()).hexdigest()
        else:
            want = hashlib.md5(f"{ha1}:abc:{ha2}".encode()).hexdigest()
        return f.get("username") == user and f.get("response") == want

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _plays(m, url: str, timeout: float = 15.0) -> bool:
    m.command("loadfile", url)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            # Properties, not options: m["x"] reads options/x.
            if m.file_format == "mpegts" and m.time_pos is not None:
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


@pytest.fixture
def player():
    m = _libmpv()
    yield m
    m.terminate()
    nerd.install(None)


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


def test_real_mpv_sends_the_nerd_agent_headers_and_referer(player, server):
    srv = server()
    nerd.install(S(nerd_enabled="true", nerd_user_agent="vlc", nerd_headers="X-Token: s3cret",
                   nerd_referer="http://portal.example/"))
    assert nerd.apply_stream(player, {"headers": "X-Box: vu"}, False) is True
    assert _plays(player, f"http://127.0.0.1:{srv.port}/live.ts")
    h = srv.requests[0]["headers"]
    assert h["user-agent"].startswith("VLC/3.0") and h["x-token"] == "s3cret" and h["x-box"] == "vu"
    assert h["referer"] == "http://portal.example/"


def test_real_mpv_goes_back_to_defaults_when_nerd_is_turned_off(player, server):
    srv = server()
    nerd.install(S(nerd_enabled="true", nerd_user_agent="kodi", nerd_headers="X-Token: 1"))
    nerd.apply_stream(player, None, False)
    nerd.install(S(nerd_enabled="false"))
    assert nerd.apply_stream(player, None, True) is False
    assert _plays(player, f"http://127.0.0.1:{srv.port}/a.ts")
    h = srv.requests[0]["headers"]
    assert h["user-agent"] == "dopeIPTV/1.0" and "x-token" not in h


def test_real_mpv_through_the_http_proxy(player, server):
    proxy = server()
    nerd.install(S(nerd_enabled="true", nerd_proxy=f"http://127.0.0.1:{proxy.port}"))
    nerd.apply_stream(player, None, False)
    # The address is not even reachable: only the proxy can answer it.
    assert _plays(player, "http://provider.invalid:8080/live/1.ts")
    assert proxy.requests[0]["path"] == "http://provider.invalid:8080/live/1.ts"


def test_real_mpv_core_options_take_and_refused_ones_do_not(player):
    nerd.install(S(nerd_enabled="true", nerd_mpv_options="volume-max=150\nvo=gpu\nno-such-option=1",
                   nerd_demux_max_mb="64", nerd_spdif="ac3,eac3", nerd_net_timeout="11"))
    took = nerd.apply_core(player)
    assert "volume-max" in took and "no-such-option" not in took and "vo" not in took
    assert player["volume-max"] == 150 and player["network-timeout"] == 11
    assert player["audio-spdif"] in ("ac3,eac3", ["ac3", "eac3"])
    assert player["demuxer-max-bytes"] == 64 * 1024 * 1024


def test_real_mpv_plays_an_enigma2_address_with_basic_login(player, server):
    """What Enigma2Client hands the player: the service reference as the
    path, colons and all, with the box's login in the address."""
    from dopeiptv.providers.boxes import with_login
    box = server(auth=("basic", "root", "pa:ss w0rd"))
    url = with_login(f"http://127.0.0.1:{box.port}/1:0:19:283D:3FB:1:C00000:0:0:0:", "root", "pa:ss w0rd")
    assert _plays(player, url)
    assert box.requests[-1]["path"] == "/1:0:19:283D:3FB:1:C00000:0:0:0:"


def test_real_mpv_answers_tvheadends_digest_login_from_the_address(player, server):
    from dopeiptv.providers.boxes import with_login
    tvh = server(auth=("digest", "kodi", "s3cret"))
    url = with_login(f"http://127.0.0.1:{tvh.port}/stream/channel/c1?profile=pass", "kodi", "s3cret")
    assert _plays(player, url)
    assert tvh.requests[0]["headers"].get("authorization") is None       # challenged first
    assert tvh.requests[-1]["headers"]["authorization"].lower().startswith("digest ")
