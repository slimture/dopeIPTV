"""The programs the app starts on a provider's stream go out one guarded way
(core/net_bridge.py), and are never handed its address.

- Where they land: an HLS playlist the provider wrote cannot send FFmpeg or
  mpv to this computer's services, a link-local host or a device at home -
  judged by the host as written, then on the connected socket (DNS
  rebinding), every hop. The stream's own server is let in.
- What they are handed: an alias of the address, which names nothing of it
  and works through the bridge alone, the bridge's key in their environment;
  an address that is no http(s) one and holds a login in a private file.

The last tests record a real AES-128 HLS stream with the real ffmpeg and mpv
through RecordingManager, and read the recorder's own /proc/<pid>/cmdline.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import socket
import stat
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from dopeiptv.core import cert_pin, dest_guard, net_bridge, net_route
from dopeiptv.core.net_bridge import HOME_POLICY, Policy, make_alias, real_target
from dopeiptv.core.net_route import Route

TS = (Path(__file__).parent / "data" / "tiny.ts").read_bytes()
THIS, LINK, HOME = dest_guard.THIS_COMPUTER, dest_guard.LINK_LOCAL, dest_guard.HOME


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    # The test machine's own proxy would take the requests made here.
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY",
              "no_proxy", "NO_PROXY"):
        monkeypatch.delenv(k, raising=False)
    dest_guard.forget_names()
    yield
    dest_guard.forget_names()


# -- an alias's address ------------------------------------------------------------

def _alias(url: str) -> net_bridge.Alias:
    return make_alias(url, "t" * 48)


def test_an_alias_names_nothing_of_the_address():
    a = _alias("http://alice:s3cret@provider.example:8080/live/alice/s3cret/1.m3u8?token=t0k")
    assert a.address == "http://" + "t" * 48 + ".stream.invalid/~/~/~/~.m3u8"
    for word in ("alice", "s3cret", "provider", "8080", "live", "t0k", "1.m3u8"):
        assert word not in a.address
    assert (a.host, a.port, a.netloc, a.user, a.password) == (
        "provider.example", 8080, "provider.example:8080", "alice", "s3cret")
    # The last part's extension stays, for the format; nothing more of it.
    assert _alias("http://h.example/play/abcdef0123456789").path == "/~/~"
    assert _alias("http://h.example/a/b/token.ABCDEFGHIJ").path == "/~/~/~"
    assert _alias("http://h.example/a/1.TS").path == "/~/~.ts"
    assert _alias("http://h.example").path == "/~" and _alias("http://h.example/x/").path == "/~/~"


@pytest.mark.parametrize("ref, real", [
    ("", "/live/u/p/1.m3u8?token=t"),                   # the alias itself: the real query
    ("seg_1.ts", "/live/u/p/seg_1.ts"),
    ("sub/seg_1.ts?n=2", "/live/u/p/sub/seg_1.ts?n=2"),
    ("../key.bin", "/live/u/key.bin"),
    ("../../../k", "/k"),
    ("../../../../../k", "/k"),                         # no higher than the root
    ("./x/../y.ts", "/live/u/p/y.ts"),
    ("/hls/x.ts", "/hls/x.ts"),                         # an absolute path: as written
    ("?other=1", "/live/u/p/1.m3u8?other=1"),           # its own query, not the real one
    ("1.m3u8", "/live/u/p/1.m3u8"),                     # the real name: no query added
    ("%7E.ts", "/live/u/p/%7E.ts"),
    ("a%20b.ts", "/live/u/p/a%20b.ts"),
])
def test_a_relative_address_resolves_as_against_the_real_one(ref, real):
    from urllib.parse import urljoin
    url = "http://provider.example/live/u/p/1.m3u8?token=t"
    a = _alias(url)
    asked = urlsplit(urljoin(a.address, ref) if ref else a.address)
    assert real_target(a, asked.path, asked.query) == real
    # ... which is where the same reference leads from the real address.
    want = urlsplit(urljoin(url, ref) if ref else url)
    assert want.path + (f"?{want.query}" if want.query else "") == real


def test_folders_with_odd_names_and_placeholders_percent_written():
    a = _alias("http://h.example/a%2Fb/~/c%20d/x.ts")
    assert a.dirs == ("a%2Fb", "~", "c%20d")
    assert real_target(a, a.path) == "/a%2Fb/~/c%20d/x.ts"
    assert real_target(a, "/%7E/%7e/~/y.ts") == "/a%2Fb/~/c%20d/y.ts"
    # The one address read otherwise than against the real one: an absolute
    # path whose folder is a "~" itself is the real folder at that place.
    assert real_target(_alias("http://h.example/live/u/p/1.m3u8"), "/~/x.ts") == "/live/x.ts"


@pytest.mark.parametrize("url, host, port, netloc", [
    ("http://[::1]:8001/a/b.ts", "::1", 8001, "[::1]:8001"),
    ("https://[2001:db8::5]/a/b.ts", "2001:db8::5", 443, "[2001:db8::5]"),
    ("http://PROVIDER.Example./a", "provider.example", 80, "provider.example"),
    ("https://bücher.example/a", "bücher.example", 443, "xn--bcher-kva.example"),
])
def test_hosts_and_ports(url, host, port, netloc):
    a = _alias(url)
    assert (a.host, a.port, a.netloc) == (host, port, netloc)


def test_a_login_in_the_address_goes_as_the_servers_login():
    a = _alias("http://us%40er:p%3Ass@h.example/x/1.m3u8")
    assert (a.user, a.password) == ("us@er", "p:ss")
    head = net_bridge.alias_head("GET", "/x/1.m3u8", "HTTP/1.1",
                                 b"Host: tok.stream.invalid\r\nUser-Agent: Lavf\r\n"
                                 b"Proxy-Authorization: Basic abc\r\nConnection: keep-alive", a)
    lines = head.decode().split("\r\n")
    assert lines[0] == "GET /x/1.m3u8 HTTP/1.1"
    assert "Host: h.example" in lines and "User-Agent: Lavf" in lines
    assert "Authorization: Basic " + base64.b64encode(b"us@er:p:ss").decode() in lines
    assert not any(line.lower().startswith(("proxy-", "host: tok")) for line in lines)
    assert lines[-3:] == ["Connection: close", "", ""]
    with pytest.raises(ValueError):
        _alias("rtsp://h.example/x")


# -- where a connection may land ---------------------------------------------------

INTERNET = Policy(internet=True, entry=("provider.example", 80))


@pytest.mark.parametrize("host, port, why", [
    ("127.0.0.1", 6379, THIS), ("localhost", 80, THIS), ("2130706433", 80, THIS),
    ("[::ffff:127.0.0.1]", 80, THIS), ("0.0.0.0", 80, THIS),
    ("169.254.169.254", 80, LINK), ("metadata.google.internal", 80, LINK), ("fe80::1", 80, LINK),
    ("192.168.1.1", 80, HOME), ("nas.local", 80, HOME), ("router", 80, HOME), ("10.0.0.5", 80, HOME),
    ("cdn.example", 443, ""), ("provider.example", 80, ""), ("93.184.215.14", 80, ""),
])
def test_a_source_on_the_internet_never_reaches_in_by_the_host_as_written(host, port, why):
    assert INTERNET.written(host, port) == why
    assert HOME_POLICY.written(host, port) == ""            # a home source's own business


def test_the_streams_own_server_is_let_in():
    ace = Policy(internet=True, entry=("127.0.0.1", 6878))
    assert ace.written("127.0.0.1", 6878) == "" and ace.landed("127.0.0.1", 6878, "127.0.0.1") == ""
    assert ace.written("127.0.0.1", 6879) == THIS           # another port of this computer
    relay = Policy(internet=True, entry=("192.168.1.1", 4022))
    assert relay.written("192.168.1.1", 4022) == "" and relay.written("192.168.1.2", 4022) == HOME
    # A public name of the stream's own that the home's DNS answers at home.
    assert INTERNET.landed("provider.example", 80, "192.168.1.9") == ""
    assert INTERNET.landed("cdn.example", 80, "192.168.1.9") == HOME
    # ... but never this computer or a link-local host by that public name.
    assert INTERNET.landed("provider.example", 80, "127.0.0.1") == THIS
    assert INTERNET.landed("provider.example", 80, "169.254.169.254") == LINK


def test_home_addresses_and_the_persons_own_names():
    let_in = Policy(internet=True, home_ok=True, entry=("provider.example", 80))
    assert let_in.written("192.168.1.5", 80) == "" and let_in.landed("cdn.example", 80, "10.1.1.1") == ""
    assert let_in.written("127.0.0.1", 80) == THIS and let_in.written("169.254.1.1", 80) == LINK
    dest_guard.note_own("http://myserver.example:8000/list.m3u")
    assert INTERNET.landed("myserver.example", 8000, "192.168.1.9") == ""
    assert INTERNET.landed("192-168-1-5.abc.plex.direct", 32400, "192.168.1.5") == ""


@pytest.mark.parametrize("host", ["example.com", "nas.local", "127.0.0.1", "localhost", "router",
                                  "169.254.169.254", "203.0.113.5", "pictures.example"])
@pytest.mark.parametrize("peer", ["127.0.0.1", "::1", "169.254.169.254", "169.254.1.7",
                                  "192.168.1.4", "fd00::4", "203.0.113.7", "100.100.100.200"])
def test_a_home_sources_rule_is_dest_guards_own(host, peer):
    """The rule the app's own requests follow (dest_guard.peer_refusal) is
    the one a home source's stream gets - drifting apart would be a hole."""
    dest_guard.note_outside("https://pictures.example/logo.png")
    assert HOME_POLICY.landed(host, 80, peer) == dest_guard.peer_refusal(host, peer)


def test_where_the_source_is_sets_the_policy():
    pol = net_bridge.policy_for({"_kind": "xtream", "_server": "http://provider.example:8080"},
                                "http://127.0.0.1:6878/ace/getstream?id=x")
    assert pol == Policy(internet=True, home_ok=False, entry=("127.0.0.1", 6878))
    home = net_bridge.policy_for({"_kind": "enigma2", "_server": "http://192.168.1.5"},
                                 "http://192.168.1.5:8001/1:0:1")
    assert not home.internet and home.entry == ("192.168.1.5", 8001)
    # A backup address on the internet makes the source the internet's.
    mirrored = {"_kind": "xtream", "_server": "http://192.168.1.5", "backup_servers": "http://p.example"}
    assert net_bridge.policy_for(mirrored, "http://192.168.1.5/live/1.ts").internet
    assert not net_bridge.policy_for({"_kind": "m3u", "_server": "/home/me/list.m3u"},
                                     "http://p.example/1.ts").internet      # a file: the person's
    assert net_bridge.policy_for({"_kind": "m3u", "_server": "http://p.example/x.m3u",
                                  "home_addresses": "true"}, "http://p.example/1.ts").home_ok
    # Not said where its server is (nerd.source()'s form): where the stream is.
    assert net_bridge.policy_for({"_kind": "xtream"}, "http://p.example/1.ts").internet
    assert not net_bridge.policy_for(None, "http://192.168.1.5/1.ts").internet


# -- the bridge itself -------------------------------------------------------------

class Server:
    """An http server that answers *routes* (path -> status, headers, body)
    and notes every request: its path, Host, Authorization. *digest*: a
    Digest login it asks for (user, password)."""

    def __init__(self, routes: dict | None = None, digest: tuple[str, str] | None = None,
                 tls: tuple[str, str] | None = None):
        self.routes = routes or {}
        self.digest = digest
        self.seen: list[dict] = []
        me = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):
                pass

            def do_GET(self):  # noqa: N802
                auth = self.headers.get("Authorization") or ""
                me.seen.append({"path": self.path, "host": self.headers.get("Host"), "auth": auth})
                if me.digest and not me._digest_ok(auth, "GET", self.path):
                    self.send_response(401)
                    self.send_header("WWW-Authenticate",
                                     'Digest realm="box", nonce="n0nce", qop="auth", algorithm=MD5')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                code, headers, body = me.routes.get(self.path.split("?", 1)[0],
                                                    (404, {}, b"no"))
                self.send_response(code)
                for k, v in {"Content-Length": str(len(body)), **headers}.items():
                    self.send_header(k, v)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        if tls:
            import ssl
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(*tls)
            self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def _digest_ok(self, auth: str, method: str, uri: str) -> bool:
        if not auth.startswith("Digest "):
            return False
        f = dict(re.findall(r'(\w+)="?([^",]*)"?', auth[7:]))
        user, pw = self.digest or ("", "")
        ha1 = hashlib.md5(f"{user}:box:{pw}".encode()).hexdigest()
        ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
        want = hashlib.md5(f"{ha1}:n0nce:{f.get('nc')}:{f.get('cnonce')}:auth:{ha2}".encode()).hexdigest()
        return f.get("response") == want and f.get("username") == user and f.get("uri") == uri

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def servers():
    made: list = []

    def make(*a, **kw):
        s = Server(*a, **kw)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


@pytest.fixture
def bridges():
    made: list = []

    def make(route=Route(), policy=None, **kw):
        b = net_bridge.Bridge(route, policy=policy, **kw)
        made.append(b)
        return b
    yield make
    for b in made:
        b.close()


def _login(bridge) -> str:
    word = base64.b64encode(f"{net_bridge.USER}:{bridge.secret}".encode()).decode()
    return f"Proxy-Authorization: Basic {word}\r\n"


def _ask(bridge, request: str, keyed: bool = True) -> bytes:
    head, _sep, rest = request.partition("\r\n")
    text = head + "\r\n" + (_login(bridge) if keyed else "") + rest
    with socket.create_connection(("127.0.0.1", bridge.port), timeout=15) as s:
        s.sendall(text.encode())
        got = b""
        s.settimeout(15)
        try:
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                got += chunk
                if got.startswith(b"HTTP/1.1 200 Connection established"):
                    break
        except socket.timeout:
            pass
    return got


def _get(bridge, url: str, keyed: bool = True) -> bytes:
    host = urlsplit(url).netloc
    return _ask(bridge, f"GET {url} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: Lavf\r\n\r\n", keyed)


def test_another_programs_on_this_computer_hold_no_alias(servers, bridges):
    srv = servers({"/live/u/p/1.ts": (200, {}, TS)})
    b = bridges()
    alias = b.alias(f"http://127.0.0.1:{srv.port}/live/u/p/1.ts")
    assert _get(b, alias, keyed=False).startswith(b"HTTP/1.1 407 ")         # no key, nothing
    assert srv.seen == []
    other = alias.replace(urlsplit(alias).hostname.split(".")[0], "0" * 48)
    assert _get(b, other).startswith(b"HTTP/1.1 404 ") and srv.seen == []     # no such alias
    got = _get(b, alias)
    assert got.split(b"\r\n", 1)[0].endswith(b"200 OK") and got.endswith(TS)
    assert srv.seen == [{"path": "/live/u/p/1.ts", "host": f"127.0.0.1:{srv.port}", "auth": ""}]
    # Asked for over CONNECT, as for https: no.
    host = urlsplit(alias).hostname
    assert _ask(b, f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}\r\n\r\n").startswith(b"HTTP/1.1 502 ")
    b.forget(alias)
    assert b.aliases == 0 and _get(b, alias).startswith(b"HTTP/1.1 404 ")
    assert len(srv.seen) == 1


def test_the_alias_table_is_bounded(bridges, monkeypatch):
    monkeypatch.setattr(net_bridge, "MAX_ALIASES", 4)
    b = bridges()
    made = [b.alias(f"http://h.example/{i}.ts") for i in range(6)]
    assert b.aliases == 4 and len(set(made)) == 6
    tokens = [urlsplit(a).hostname[:-len(net_bridge.ALIAS_HOST)] for a in made]
    assert all(len(t) == 48 and int(t, 16) >= 0 for t in tokens)   # 192 bits, hex
    assert b._alias_of(tokens[0]) is None and b._alias_of(tokens[1]) is None
    assert b._alias_of(tokens[5]).last == "5.ts"


def test_a_login_basic_and_digest(servers, bridges):
    srv = servers({"/x/1.ts": (200, {}, TS)}, digest=("tv", "pa:ss"))
    b = bridges()
    got = _get(b, b.alias(f"http://tv:pa%3Ass@127.0.0.1:{srv.port}/x/1.ts"))
    assert got.endswith(TS) and b"200 OK" in got.split(b"\r\n", 1)[0]
    first, second = srv.seen
    assert first["auth"] == "Basic " + base64.b64encode(b"tv:pa:ss").decode()
    assert second["auth"].startswith('Digest username="tv"')
    wrong = _get(b, b.alias(f"http://tv:nope@127.0.0.1:{srv.port}/x/1.ts"))
    assert b" 401 " in wrong.split(b"\r\n", 1)[0]                  # the server's own word back


def test_a_source_on_the_internet_reaches_no_service_on_this_computer(servers, bridges):
    inside = servers({"/": (200, {}, b"secret")})
    srv = servers({"/live/1.m3u8": (200, {}, b"#EXTM3U\n")})
    b = bridges(policy=Policy(internet=True, entry=("127.0.0.1", srv.port)))
    for request in (f"GET http://127.0.0.1:{inside.port}/ HTTP/1.1\r\nHost: x\r\n\r\n",
                    f"CONNECT 127.0.0.1:{inside.port} HTTP/1.1\r\nHost: x\r\n\r\n",
                    f"GET http://localhost:{inside.port}/ HTTP/1.1\r\nHost: x\r\n\r\n",
                    "GET http://169.254.169.254/latest/meta-data/ HTTP/1.1\r\nHost: x\r\n\r\n",
                    "GET http://192.168.1.1/ HTTP/1.1\r\nHost: x\r\n\r\n"):
        got = _ask(b, request)
        assert got.startswith(b"HTTP/1.1 502 "), got[:80]
        assert b"secret" not in got
    assert inside.seen == [] and b.guarded == 5
    # The stream's own server, by itself and through an alias.
    assert _get(b, f"http://127.0.0.1:{srv.port}/live/1.m3u8").endswith(b"#EXTM3U\n")
    assert _get(b, b.alias(f"http://127.0.0.1:{srv.port}/live/1.m3u8")).endswith(b"#EXTM3U\n")


def test_a_refusal_is_said_once_per_host_and_reason(bridges, caplog):
    import logging
    b = bridges(policy=Policy(internet=True))
    with caplog.at_level(logging.INFO, logger="dopeiptv"):
        for _ in range(3):
            _ask(b, "GET http://127.0.0.1:9/a/u/p/1.ts?token=x HTTP/1.1\r\nHost: x\r\n\r\n")
    said = [r.getMessage() for r in caplog.records if "net bridge" in r.getMessage()]
    assert len(said) == 1 and "this computer (127.0.0.1:9)" in said[0]
    assert "token" not in said[0] and "/u/p/" not in said[0]      # never the address
    assert b.guarded == 3


def test_dns_rebinding_is_caught_on_the_socket(servers, bridges, monkeypatch):
    """A public name whose answer is this computer: refused once connected,
    before a byte is sent - the service there gets nothing, whatever the
    policy, and a home source's too."""
    inside = servers({"/": (200, {}, b"secret")})
    monkeypatch.setattr(net_route, "_system",
                        lambda host, port, fam: ["127.0.0.1"] if host == "evil.example" else [])
    for policy in (Policy(internet=True), HOME_POLICY):
        b = bridges(Route(family="ipv4"), policy=policy)
        for request in (f"GET http://evil.example:{inside.port}/ HTTP/1.1\r\nHost: evil.example\r\n\r\n",
                        f"CONNECT evil.example:{inside.port} HTTP/1.1\r\nHost: x\r\n\r\n"):
            got = _ask(b, request)
            assert got.startswith(b"HTTP/1.1 502 ") and b"this computer" in got
        alias = b.alias(f"http://u:p@evil.example:{inside.port}/live/1.m3u8")
        assert _get(b, alias).startswith(b"HTTP/1.1 502 ")
    assert inside.seen == []


def test_the_seam_stands_a_test_server_in_for_the_internet(servers, bridges, monkeypatch):
    srv = servers({"/x": (200, {}, b"ok")})
    monkeypatch.setattr(net_route, "_system",
                        lambda host, port, fam: ["127.0.0.1"] if host == "cdn.example" else [])
    b = bridges(Route(family="ipv4"), policy=Policy(internet=True))
    assert _get(b, f"http://cdn.example:{srv.port}/x").startswith(b"HTTP/1.1 502 ")
    monkeypatch.setattr(net_bridge, "place_of", lambda _ip: "")
    assert _get(b, f"http://cdn.example:{srv.port}/x").endswith(b"ok")


# -- https behind an alias: the stream's own trust ---------------------------------

def _certs(tmp_path):
    from tests.test_net_advanced import _fp, _make_ca, _make_cert, _make_signed
    ca = _make_ca(tmp_path)
    return ca, _make_signed(tmp_path, ca, "cdn.example", "c"), _make_cert(tmp_path, "nas.example", "n"), _fp


def test_an_https_alias_is_checked_as_the_player_checks_it(servers, bridges, tmp_path, monkeypatch):
    ca, signed, own, fp = _certs(tmp_path)
    cdn = servers({"/live/1.m3u8": (200, {}, b"#EXTM3U cdn\n")}, tls=signed)
    nas = servers({"/live/1.m3u8": (200, {}, b"#EXTM3U nas\n")}, tls=own)
    names = {"cdn.example": "127.0.0.1", "nas.example": "127.0.0.1", "fake.example": "127.0.0.1"}
    monkeypatch.setattr(net_route, "_system", lambda host, port, fam: [names[host]] if host in names else [])
    monkeypatch.setattr(net_bridge, "place_of", lambda _ip: "")         # the servers: the internet
    route = Route(family="ipv4", pins=(("nas.example", nas.port, fp(own)),))
    b = bridges(route, policy=Policy(internet=True), cafile=ca[0])
    checked = b.alias(f"https://cdn.example:{cdn.port}/live/1.m3u8")
    assert b._alias_of(urlsplit(checked).hostname[:48]).trust == net_bridge.CHECKED
    assert _get(b, checked).endswith(b"#EXTM3U cdn\n")
    # A certificate no authority signed, on the internet: nothing.
    nobody = b.alias(f"https://fake.example:{nas.port}/live/1.m3u8")
    assert _get(b, nobody).startswith(b"HTTP/1.1 502 ")
    # The pinned host by its pin, and a changed one refused and noted.
    pinned = b.alias(f"https://nas.example:{nas.port}/live/1.m3u8")
    assert b._alias_of(urlsplit(pinned).hostname[:48]).trust == net_bridge.PINNED
    assert _get(b, pinned).endswith(b"#EXTM3U nas\n")
    wrong = bridges(Route(family="ipv4", pins=(("nas.example", nas.port, "00" * 32),)),
                    policy=Policy(internet=True))
    cert_pin.clear_mismatch("nas.example", nas.port)
    assert _get(wrong, wrong.alias(f"https://nas.example:{nas.port}/live/1.m3u8")).startswith(
        b"HTTP/1.1 502 ")
    assert cert_pin.mismatch("nas.example", nas.port) == fp(own)
    # A source the person let play unchecked: as the recorder did, unchecked.
    loose = b.alias(f"https://fake.example:{nas.port}/live/1.m3u8", {"tls_unchecked": "true"})
    assert b._alias_of(urlsplit(loose).hostname[:48]).trust == net_bridge.UNCHECKED
    assert _get(b, loose).endswith(b"#EXTM3U nas\n")
    assert [s["path"] for s in nas.seen] == ["/live/1.m3u8", "/live/1.m3u8"]


# -- the API for what the app starts -----------------------------------------------

def test_guard_for_child_env_and_the_bridges_kept(monkeypatch):
    monkeypatch.setattr(net_bridge, "_bridges", type(net_bridge._bridges)())
    monkeypatch.setattr(net_bridge, "MAX_BRIDGES", 3)
    try:
        src = {"_kind": "m3u", "_server": "http://provider.example/list.m3u"}
        b = net_bridge.guard_for(src, "http://provider.example/live/1.m3u8")
        assert b is not None and b.policy.internet and not b.route.active
        assert net_bridge.guard_for(src, "http://provider.example/live/2.m3u8") is b
        assert net_bridge.guard_for(src, "rtsp://provider.example/x") is None
        env = net_bridge.child_env(b)
        assert env == {"http_proxy": b.address, "no_proxy": ""} and net_bridge.child_env(None) == {}
        held = b.alias("http://provider.example/live/1.m3u8")
        others = [net_bridge.guard_for(src, f"http://cdn{i}.example/x.ts") for i in range(4)]
        # The one used longest ago that holds no alias goes first; one
        # whose alias a recorder reads stays.
        assert not b.closed and others[0].closed and len(net_bridge._bridges) == 3
        assert _get(b, held).startswith(b"HTTP/1.1 ")
    finally:
        for kept in list(net_bridge._bridges.values()):
            kept.close()


# -- what a recorder is handed ---------------------------------------------------

def _manager(tmp_path, name, playlists=()):
    import json

    from PyQt6.QtCore import QSettings

    from dopeiptv.core.recording import RecordingManager
    s = QSettings("dopeIPTV-test", name)
    s.clear()
    s.setValue("recordings_dir", str(tmp_path / "rec"))
    s.setValue("playlists", json.dumps(list(playlists)))
    return RecordingManager(s)


def test_an_rtsp_address_with_a_login_goes_in_a_private_list(tmp_path, monkeypatch):
    from dopeiptv.core import recording

    rm = _manager(tmp_path, "recrtsp")
    seen = []

    class FakeProc:
        def __init__(self, cmd, **kw):
            seen.append(cmd)
            self.ended = False

        def poll(self):
            return 0 if self.ended else None

        def terminate(self):
            self.ended = True

        def wait(self, _t=None):
            return 0

    monkeypatch.setattr(recording.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(recording, "ffmpeg_major", lambda _exe: 6)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    os.chmod(tmp_path, 0o700)
    for kind in ("ffmpeg", "mpv"):
        monkeypatch.setattr(recording.RecordingManager, "recorder",
                            staticmethod(lambda k=kind: (k, "/usr/bin/" + k)))
        for url in ("rtsp://cam:s3cret@cam.example/live", "srt://h.example:9000?passphrase=s3cret",
                    "rtmp://h.example/app/x?token=s3cret"):
            seen.clear()
            job = {"url": url, "title": "T", "stop": None, "status": "scheduled"}
            rm._spawn(job)
            cmd = seen[0]
            assert not any("s3cret" in a for a in cmd), cmd
            listed = job["_listed"]
            assert stat.S_IMODE(os.stat(listed).st_mode) == 0o600
            text = Path(listed).read_text(encoding="utf-8")
            assert url in text
            if kind == "ffmpeg":
                assert cmd[cmd.index("-f") + 1] == "concat" and cmd[cmd.index("-i") + 1] == listed
                assert text.startswith("ffconcat version 1.0\n") and "option user_agent" in text
            else:
                assert cmd[-1] == "--playlist=" + listed
            rm._stop_proc(job)
            assert not os.path.exists(listed) and "_listed" not in job    # gone with the recording
    # Without a login, and udp's empty one: as it always was.
    monkeypatch.setattr(recording.RecordingManager, "recorder",
                        staticmethod(lambda: ("ffmpeg", "/usr/bin/ffmpeg")))
    for url in ("rtsp://192.168.1.5/?src=1&freq=11494", "udp://@239.0.0.1:1234"):
        seen.clear()
        rm._spawn({"url": url, "title": "T", "stop": None, "status": "scheduled"})
        assert seen[0][seen[0].index("-i") + 1] == url
    # No private file to be had: not started, and never the address instead.
    monkeypatch.setattr(recording, "private_file", lambda *_a, **_k: "")
    seen.clear()
    job = {"url": "rtsp://cam:s3cret@cam.example/live", "title": "T", "stop": None,
           "status": "scheduled"}
    rm._spawn(job)
    assert seen == [] and job["status"] == "failed" and job["error"] == recording.NO_PRIVATE_FILE


def test_an_old_ffmpeg_reads_the_list_without_its_options(tmp_path, monkeypatch):
    from dopeiptv.core import recording
    rm = _manager(tmp_path, "recold")
    monkeypatch.setattr(recording, "ffmpeg_major", lambda _exe: 4)
    listed = rm._private_list("ffmpeg", "/usr/bin/ffmpeg", "rtsp://u:p@h.example/x", {})
    assert Path(listed).read_text(encoding="utf-8") == (
        "ffconcat version 1.0\nfile 'rtsp://u:p@h.example/x'\n")
    os.remove(listed)
    assert recording.concat_list("rtsp://h/it's", ["-rtsp_transport", "tcp"]) == (
        "ffconcat version 1.0\nfile 'rtsp://h/it'\\''s'\noption rtsp_transport 'tcp'\n")
    assert recording.ffmpeg_major(shutil.which("ffmpeg") or "/nonexistent/ffmpeg") in (
        None, *range(3, 20))


def test_no_bridge_no_recording(tmp_path, monkeypatch):
    from dopeiptv.core import recording
    rm = _manager(tmp_path, "recnobridge")
    started = []
    monkeypatch.setattr(recording.subprocess, "Popen", lambda cmd, **_k: started.append(cmd))
    monkeypatch.setattr(net_bridge, "guard_for", lambda *_a, **_k: None)
    monkeypatch.setattr(recording.RecordingManager, "recorder",
                        staticmethod(lambda: ("ffmpeg", "/usr/bin/ffmpeg")))
    job = {"url": "http://h.example/live/u/p/1.m3u8", "title": "T", "stop": None,
           "status": "scheduled"}
    rm._spawn(job)
    assert started == [] and job["status"] == "failed" and job["error"] == recording.NO_BRIDGE


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_the_real_ffmpeg_reads_a_private_concat_list(tmp_path):
    """The list a recorder reads an address from, with the real ffmpeg: its
    options reach the input (the user agent), the address is in no argument."""
    srv = Server({"/cam/1.ts": (200, {"Content-Type": "video/mp2t"}, TS)})
    try:
        from dopeiptv.core import recording
        from dopeiptv.core.player_exec import FFMPEG_PROTOCOLS
        url = f"http://127.0.0.1:{srv.port}/cam/1.ts"
        listed = tmp_path / "x.ffconcat"
        listed.write_text(recording.concat_list(url, ["-user_agent", "dopeIPTV/1.0"]), encoding="utf-8")
        out = tmp_path / "out.ts"
        cmd = ["ffmpeg", "-y", "-nostdin", "-loglevel", "error", "-f", "concat", "-safe", "0",
               "-protocol_whitelist", "file," + FFMPEG_PROTOCOLS, "-i", str(listed), "-c", "copy",
               str(out)]
        done = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
        assert done.returncode == 0, done.stderr[-400:]
        assert out.stat().st_size > 0 and srv.seen[0]["path"] == "/cam/1.ts"
    finally:
        srv.close()


# -- the real thing: an AES-128 HLS recording from a source on the internet --------

@pytest.fixture
def hls(tmp_path):
    """A provider serving an encrypted HLS list at /live/alice/s3cr3tpass/,
    its key at /live/keys/k.bin (a relative ../../keys/k.bin), one segment by
    an absolute path (/hls/...), one on another server of this computer
    that the provider must not reach; and that other server."""
    ff = shutil.which("ffmpeg")
    if ff is None:
        pytest.skip("needs ffmpeg")
    work = tmp_path / "make"
    work.mkdir()
    made = subprocess.run(
        [ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-i",
         "testsrc=duration=4:size=160x120:rate=25", "-c:v", "mpeg2video", "-f", "mpegts",
         str(work / "sample.ts")], capture_output=True, timeout=120, check=False)
    if made.returncode:
        pytest.skip("this ffmpeg cannot make the sample")
    key = os.urandom(16)
    (work / "k.bin").write_bytes(key)
    (work / "keyinfo").write_text(f"../../keys/k.bin\n{work / 'k.bin'}\n", encoding="utf-8")
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(work / "sample.ts"),
                    "-c", "copy", "-f", "hls", "-hls_time", "1", "-hls_list_size", "0",
                    "-hls_key_info_file", str(work / "keyinfo"),
                    "-hls_segment_filename", str(work / "seg%d.ts"), str(work / "index.m3u8")],
                   capture_output=True, timeout=120, check=True)
    inside = Server({"/steal.ts": (200, {}, (work / "seg0.ts").read_bytes())})
    lines = (work / "index.m3u8").read_text(encoding="utf-8").splitlines()
    segs = [line for line in lines if line.endswith(".ts")]
    assert len(segs) >= 3 and "#EXT-X-KEY:METHOD=AES-128" in "\n".join(lines)
    routes = {"/live/keys/k.bin": (200, {}, key)}
    for line in segs:
        routes[f"/live/alice/s3cr3tpass/{line}"] = (200, {"Content-Type": "video/mp2t"},
                                                    (work / line).read_bytes())
    routes["/hls/" + segs[1]] = routes.pop(f"/live/alice/s3cr3tpass/{segs[1]}")
    text = "\n".join(lines).replace(f"\n{segs[1]}\n", f"\n/hls/{segs[1]}\n")
    # Before the last segment, one the provider names on this computer.
    text = text.replace(f"\n{segs[-1]}\n", f"\nhttp://127.0.0.1:{inside.port}/steal.ts\n"
                                            f"#EXTINF:1.000000,\n{segs[-1]}\n")
    routes["/live/alice/s3cr3tpass/index.m3u8"] = (
        200, {"Content-Type": "application/vnd.apple.mpegurl"}, text.encode() + b"\n")
    provider = Server(routes)
    try:
        yield provider, inside
    finally:
        provider.close()
        inside.close()


def _record_for_real(tmp_path, monkeypatch, provider, kind, name):
    from dopeiptv.core import recording
    exe = shutil.which(kind)
    if exe is None:
        pytest.skip(f"needs {kind}")
    monkeypatch.setattr(net_bridge, "_bridges", type(net_bridge._bridges)())
    # A source on the internet: its own server is a public name.
    rm = _manager(tmp_path, name, [{"id": "p1", "kind": "m3u", "name": "P",
                                    "server": "http://provider.example/list.m3u", "options": {}}])
    monkeypatch.setattr(recording.RecordingManager, "recorder", staticmethod(lambda: (kind, exe)))
    url = f"http://127.0.0.1:{provider.port}/live/alice/s3cr3tpass/index.m3u8?token=t0k3n"
    job = {"id": "r", "url": url, "title": "Real", "stop": None, "status": "scheduled",
           "start": time.time(), "source": "p1"}
    rm.jobs = [job]
    rm._spawn(job)
    assert job["status"] == "recording", job.get("error")
    proc = job["proc"]
    bridge = job["_alias"][0]
    try:
        with open(f"/proc/{proc.pid}/cmdline", "rb") as fh:
            argv = fh.read()
        with open(f"/proc/{proc.pid}/environ", "rb") as fh:
            environ = fh.read()
    except FileNotFoundError:
        argv, environ = b"\0".join(a.encode() for a in proc.args), bridge.secret.encode()
    for word in (b"alice", b"s3cr3tpass", b"t0k3n", str(provider.port).encode(),
                 bridge.secret.encode()):
        assert word not in argv, word
    assert b".stream.invalid/~/~/~/~.m3u8" in argv
    assert bridge.secret.encode() in environ              # the key: its environment alone
    try:
        assert proc.wait(120) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
    rm.tick()
    assert job["status"] == "done" and "_alias" not in job and bridge.aliases == 0
    return job, bridge


@pytest.mark.parametrize("kind", ["ffmpeg", "mpv"])
def test_a_real_encrypted_hls_recording_through_the_alias(hls, tmp_path, monkeypatch, kind):
    provider, inside = hls
    job, bridge = _record_for_real(tmp_path, monkeypatch, provider, kind, f"recreal{kind}")
    paths = [s["path"] for s in provider.seen]
    # Every relative address resolved as against the real one: the list with
    # its own query alone, the key two folders up, a segment by its absolute
    # path, the rest beside the list.
    assert paths[0] == "/live/alice/s3cr3tpass/index.m3u8?token=t0k3n"
    assert "/live/keys/k.bin" in paths and any(p.startswith("/hls/") for p in paths)
    assert all("t0k3n" not in p or p == paths[0] for p in paths)
    assert all(s["host"] == f"127.0.0.1:{provider.port}" for s in provider.seen)
    # The provider named a service on this computer: never asked.
    assert inside.seen == [] and bridge.guarded >= 1
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name",
                            "-of", "csv=p=0", job["path"]], capture_output=True, text=True, timeout=60)
    assert "mpeg2video" in probe.stdout
    assert os.path.getsize(job["path"]) > 20_000
