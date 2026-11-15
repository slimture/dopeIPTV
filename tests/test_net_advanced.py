"""Nerd's advanced network options: own DNS (DNS over HTTPS), low latency
for live, an IP version per source and a pinned certificate - each off by
default and changing nothing while off. Fake servers on this computer
stand in for the DNS server, the provider (http) and a home server (https,
a certificate made for the test by the openssl command); where ffmpeg and
libmpv are installed, the real player and recorder go through the bridge."""
from __future__ import annotations

import json
import shutil
import socket
import ssl
import struct
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from dopeiptv.core import cert_pin, doh, live_latency, nerd, net_adapter, net_bridge, net_route
from dopeiptv.core.net_route import Route

TS = (Path(__file__).parent / "data" / "tiny.ts").read_bytes()


class S(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):
        self[key] = value


def on(**kw) -> nerd.Nerd:
    return nerd.Nerd(S(nerd_enabled="true", **kw))


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    # The test machine's own proxy would take the requests made here.
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(k, raising=False)
    doh.clear_cache()
    yield
    nerd.install(None)
    doh.clear_cache()


# -- fake servers ---------------------------------------------------------------

def _answer(query: bytes, names: dict[str, list[str]], ttl: int = 300) -> bytes:
    qid = struct.unpack("!H", query[:2])[0]
    off, labels = 12, []
    while query[off]:
        n = query[off]
        labels.append(query[off + 1:off + 1 + n].decode())
        off += 1 + n
    qtype = struct.unpack("!H", query[off + 1:off + 3])[0]
    question = query[12:off + 5]
    name = ".".join(labels).lower()
    if name not in names:
        return struct.pack("!HHHHHH", qid, 0x8183, 1, 0, 0, 0) + question
    fam = socket.AF_INET if qtype == doh.A else socket.AF_INET6
    ips = [ip for ip in names[name] if (":" in ip) == (fam == socket.AF_INET6)]
    # A CNAME first, then the addresses - as a recursive server answers.
    body = struct.pack("!HHHIH", 0xC00C, doh.CNAME, 1, ttl, 2) + b"\xc0\x0c"
    for ip in ips:
        raw = socket.inet_pton(fam, ip)
        body += struct.pack("!HHHIH", 0xC00C, qtype, 1, ttl, len(raw)) + raw
    return struct.pack("!HHHHHH", qid, 0x8180, 1, 1 + len(ips), 0, 0) + question + body


class FakeDoh:
    """A DNS-over-HTTPS server (plain http here) that knows *names*."""

    def __init__(self, names: dict[str, list[str]], down: bool = False, ttl: int = 300):
        self.names, self.asked, self.down, self.ttl = names, [], down, ttl
        me = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                from urllib.parse import parse_qs, urlsplit
                u = urlsplit(self.path)
                q = parse_qs(u.query)
                if me.down:
                    self.send_response(503)
                    self.end_headers()
                    return
                if u.path.endswith("/resolve"):
                    name, kind = q["name"][0], q["type"][0]
                    me.asked.append((name, kind))
                    qtype = doh.A if kind == "A" else doh.AAAA
                    ips = [ip for ip in me.names.get(name, []) if (":" in ip) == (qtype == doh.AAAA)]
                    body = json.dumps({"Status": 0 if name in me.names else 3,
                                       "Answer": [{"name": name, "type": qtype, "TTL": me.ttl, "data": ip}
                                                  for ip in ips]}).encode()
                    kind_hdr = "application/dns-json"
                else:
                    import base64
                    raw = base64.urlsafe_b64decode(q["dns"][0] + "==")
                    body = _answer(raw, me.names, me.ttl)
                    me.asked.append(("wire", raw))
                    kind_hdr = "application/dns-message"
                self.send_response(200)
                self.send_header("Content-Type", kind_hdr)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/dns-query"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Origin:
    """Serves the test clip over http, or https with *cert*; records each
    request's Host header and the TLS server name it came with."""

    def __init__(self, cert: tuple[str, str] | None = None):
        self.requests: list[dict] = []
        self.sni: list[str | None] = []
        me = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                me.requests.append({"path": self.path, "host": self.headers.get("Host")})
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Content-Length", str(len(TS)))
                self.end_headers()
                try:
                    self.wfile.write(TS)
                except OSError:
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        if cert is not None:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(*cert)

            def sni(_sock, name, _ctx):
                me.sni.append(name)
            ctx.sni_callback = sni
            self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def servers():
    made = []

    def make(kind, *a, **kw):
        s = kind(*a, **kw)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


def _make_cert(tmp: Path, name: str, tag: str) -> tuple[str, str]:
    if shutil.which("openssl") is None:
        pytest.skip("no openssl command to make a test certificate")
    cert, key = tmp / f"{tag}.pem", tmp / f"{tag}.key"
    r = subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                        "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "3",
                        "-subj", f"/CN={name}/O=Home server {tag}",
                        "-addext", f"subjectAltName=DNS:{name},IP:127.0.0.1"],
                       capture_output=True, timeout=30)
    if r.returncode:
        pytest.skip(f"openssl could not make a certificate: {r.stderr[-200:]!r}")
    return str(cert), str(key)


def _fp(cert: tuple[str, str]) -> str:
    return cert_pin.fingerprint(ssl.PEM_cert_to_DER_cert(Path(cert[0]).read_text(encoding="utf-8")))


# -- DNS over HTTPS -------------------------------------------------------------------

def test_wire_query_and_answer_with_a_cname_and_compression():
    q = doh.build_query("Provider.Example", doh.A)
    assert q[:4] == b"\x00\x00\x01\x00" and b"\x08Provider\x07Example\x00" in q
    ans = _answer(doh.build_query("provider.example", doh.A), {"provider.example": ["198.51.100.7", "2001:db8::7"]})
    assert doh.parse_response(ans, doh.A) == (["198.51.100.7"], 300, 0)
    ans6 = _answer(doh.build_query("provider.example", doh.AAAA), {"provider.example": ["2001:db8::7"]})
    assert doh.parse_response(ans6, doh.AAAA)[0] == ["2001:db8::7"]
    nx = _answer(doh.build_query("gone.example", doh.A), {})
    assert doh.parse_response(nx, doh.A) == ([], doh.MAX_TTL, doh.NXDOMAIN)
    with pytest.raises(ValueError):
        doh.parse_response(ans[:20], doh.A)
    with pytest.raises(ValueError):
        doh.build_query("a..b", doh.A)


def test_json_answers():
    obj = {"Status": 0, "Answer": [{"type": 5, "data": "cdn.example."},
                                   {"type": 1, "TTL": 42, "data": "203.0.113.9"},
                                   {"type": 1, "TTL": 99, "data": "not an ip"}]}
    assert doh.parse_json(obj, doh.A) == (["203.0.113.9"], 42, 0)
    assert doh.parse_json({"Status": 3}, doh.A) == ([], doh.MAX_TTL, 3)


def test_presets_are_addressed_by_ip_and_a_custom_one_must_be_a_web_address():
    for servers in doh.PRESETS.values():
        assert all(net_route.is_ip(s.split("/")[2]) and s.startswith("https://") for s in servers)
    assert doh.servers_for("custom", "https://dns.example/dns-query") == ("https://dns.example/dns-query",)
    assert doh.servers_for("custom", "dns.example") == ()
    assert doh.servers_for("off") == () and doh.servers_for("") == ()
    assert doh.label("quad9") == "Quad9" and doh.label("custom", "https://dns.example/q") == "dns.example"


def test_answers_are_kept_for_their_ttl_and_the_next_server_is_asked(servers, monkeypatch):
    dead = servers(FakeDoh, {}, down=True)
    good = servers(FakeDoh, {"provider.example": ["192.0.2.10"]}, ttl=120)
    r = doh.Resolver((dead.url, good.url))
    assert r.query("provider.example", doh.A) == ["192.0.2.10"]
    assert r.query("provider.example", doh.A) == ["192.0.2.10"]
    assert len(good.asked) == 1                         # the second came from the cache
    clock = [time.monotonic() + 121]
    monkeypatch.setattr(doh.time, "monotonic", lambda: clock[0])
    r.query("provider.example", doh.A)
    assert len(good.asked) == 2                         # its TTL ran out
    assert r.query("nothing.example", doh.A) == []      # no such name: none, not an error
    dead2 = servers(FakeDoh, {}, down=True)
    with pytest.raises(doh.DohError) as e:
        doh.Resolver((dead2.url,)).query("provider.example", doh.A)
    assert "HTTP 503" in str(e.value)


def test_the_json_api_is_asked_by_name(servers):
    srv = servers(FakeDoh, {"provider.example": ["192.0.2.11"]})
    url = srv.url.replace("/dns-query", "/resolve")
    assert doh.Resolver((url,)).query("provider.example", doh.A) == ["192.0.2.11"]
    assert srv.asked == [("provider.example", "A")]


# -- the route ----------------------------------------------------------------------

def test_nothing_changes_while_nerd_is_off_or_nothing_is_set():
    off = nerd.Nerd(S(nerd_enabled="false", nerd_dns="cloudflare"))
    assert net_route.route_for({"dns": "google", "ip_family": "ipv6"}, off) is net_route.INACTIVE
    assert not net_route.route_for({}, on()).active
    assert net_adapter.getter(net_route.route_for({}, on())) is not None
    import requests
    s = requests.Session()
    before = dict(s.adapters)
    assert net_adapter.mount(s, net_route.route_for({}, on())) is False
    assert s.adapters == before


def test_a_sources_own_choice_wins_over_the_nerd_page():
    n = on(nerd_dns="cloudflare", nerd_dns_fallback="true", nerd_ip_family="ipv4")
    r = net_route.route_for({}, n)
    assert r.servers == doh.PRESETS["cloudflare"] and r.dns_label == "Cloudflare" and r.fallback
    assert net_route.route_for({"dns": "off"}, n).servers == ()
    assert net_route.route_for({"dns": "quad9"}, n).servers == doh.PRESETS["quad9"]
    own = net_route.route_for({"dns": "custom", "dns_url": "https://dns.example/dns-query"}, n)
    assert own.servers == ("https://dns.example/dns-query",) and own.dns_label == "dns.example"
    assert net_route.route_for({"ip_family": "prefer6"}, n).family == "prefer6"
    assert net_route.route_for({"ip_family": "bogus"}, n).family == ""
    pin = cert_pin.pin_options("NAS.example", 8443, "AB" * 32)
    assert net_route.route_for(pin, n).pin("nas.example", 8443) == "ab" * 32
    assert net_route.route_for(pin, n).pin("nas.example", 443) is None


def test_ip_versions_in_order():
    assert Route(family="ipv4").families() == [socket.AF_INET]
    if socket.has_ipv6:
        assert Route(family="ipv6").families() == [socket.AF_INET6]
        assert Route(family="prefer6").families() == [socket.AF_INET6, socket.AF_INET]
        assert Route(servers=("x",)).families() == [socket.AF_INET, socket.AF_INET6]
        assert Route(servers=("x",), default_family="ipv6").families() == [socket.AF_INET6]


def test_which_hosts_a_route_changes():
    dns = Route(servers=("https://1.1.1.1/dns-query",))
    assert dns.changes("provider.example", 80)
    assert not dns.changes("nas.local", 80) and not dns.changes("vuduo2", 80)    # the home network's names
    assert not dns.changes("93.184.215.14", 80) and not dns.changes("localhost", 80)
    assert dns.bridges("http", "93.184.215.14", 80)       # it may send the player on to names
    assert not dns.bridges("http", "127.0.0.1", 80) and not dns.bridges("http", "192.168.1.5", 80)
    fam = Route(family="ipv4")
    assert fam.changes("nas.local", 80) and fam.bridges("http", "192.168.1.5", 80)
    pinned = Route(pins=(("nas.example", 8443, "ab" * 32),))
    assert pinned.changes("nas.example", 8443, "https") and not pinned.changes("nas.example", 8443, "http")
    assert pinned.bridges("https", "nas.example", 8443) and not pinned.bridges("https", "other.example", 443)


def test_own_dns_fails_clearly_and_the_system_only_takes_over_when_allowed(servers, monkeypatch):
    dead = servers(FakeDoh, {}, down=True)
    asked = []
    monkeypatch.setattr(net_route.socket, "getaddrinfo",
                        lambda h, p, f, t: asked.append(h) or [(f, t, 6, "", ("127.0.0.1", p))])
    strict = Route(servers=(dead.url,), dns_label="Mine")
    with pytest.raises(doh.DohError) as e:
        net_route.open_connection(strict, "provider.example", 9, 2)
    assert "Mine did not answer" in str(e.value) and "system's DNS is not used" in str(e.value)
    assert "provider.example" not in asked              # nothing leaked to the system
    srv = servers(Origin)
    lenient = Route(servers=(dead.url,), fallback=True)
    sock = net_route.open_connection(lenient, "provider.example", srv.port, 2)
    sock.close()
    assert "provider.example" in asked


def test_a_name_the_server_does_not_know(servers):
    srv = servers(FakeDoh, {})
    with pytest.raises(doh.DohError) as e:
        net_route.open_connection(Route(servers=(srv.url,), dns_label="Mine"), "gone.example", 80, 2)
    assert e.value.missing and "Mine knows no address" in str(e.value)


# -- the app's own requests -----------------------------------------------------------

def test_requests_connect_to_the_address_own_dns_gives_and_keep_the_name(servers):
    import requests
    dns = servers(FakeDoh, {"provider.example": ["127.0.0.1"]})
    origin = servers(Origin)
    s = requests.Session()
    assert net_adapter.mount(s, Route(servers=(dns.url,)))
    r = s.get(f"http://provider.example:{origin.port}/player_api.php", timeout=5)
    assert r.status_code == 200 and r.content == TS
    assert origin.requests[0]["host"] == f"provider.example:{origin.port}"
    assert any(a[0] == "wire" for a in dns.asked)


def test_a_pinned_certificate_and_only_that_one(servers, tmp_path):
    import requests
    cert = _make_cert(tmp_path, "nas.example", "a")
    other = _make_cert(tmp_path, "nas.example", "b")
    dns = servers(FakeDoh, {"nas.example": ["127.0.0.1"]})
    home = servers(Origin, cert)
    url = f"https://nas.example:{home.port}/api/serverinfo"
    plain = requests.Session()
    net_adapter.mount(plain, Route(servers=(dns.url,)))
    with pytest.raises(requests.exceptions.SSLError):   # not trusted, and not switched off
        plain.get(url, timeout=5)
    good = requests.Session()
    net_adapter.mount(good, Route(servers=(dns.url,), pins=(("nas.example", home.port, _fp(cert)),)))
    assert good.get(url, timeout=5).content == TS
    assert home.sni[-1] == "nas.example"                # the name went in the handshake
    wrong = requests.Session()
    net_adapter.mount(wrong, Route(servers=(dns.url,), pins=(("nas.example", home.port, _fp(other)),)))
    cert_pin.clear_mismatch("nas.example", home.port)
    with pytest.raises(requests.exceptions.SSLError) as e:
        wrong.get(url, timeout=5)
    found = cert_pin.mismatch_in(e.value)
    assert found is not None and found.got == _fp(cert) and found.pinned == _fp(other)
    assert cert_pin.mismatch("nas.example", home.port) == _fp(cert)
    assert "changed" in str(found)


def test_a_pin_holds_through_a_proxy_too(servers, tmp_path):
    import requests
    cert = _make_cert(tmp_path, "127.0.0.1", "p")
    home = servers(Origin, cert)
    proxy = net_bridge.Bridge(Route())                 # a plain CONNECT proxy
    try:
        s = requests.Session()
        net_adapter.mount(s, Route(pins=(("127.0.0.1", home.port, _fp(cert)),)))
        r = s.get(f"https://127.0.0.1:{home.port}/x", timeout=5,
                  proxies={"https": proxy.address, "http": proxy.address})
        assert r.content == TS and proxy.served == 1
    finally:
        proxy.close()


def test_an_ip_version_of_the_sources_own(servers, monkeypatch):
    import requests
    origin = servers(Origin)
    families = []
    real = net_route._system

    def system(host, port, fam):
        families.append(fam)
        return real("127.0.0.1", port, fam) if fam == socket.AF_INET else []
    monkeypatch.setattr(net_route, "_system", system)
    s = requests.Session()
    net_adapter.mount(s, Route(family="prefer6"))
    assert s.get(f"http://provider.example:{origin.port}/", timeout=5).status_code == 200
    expected = [socket.AF_INET6, socket.AF_INET] if socket.has_ipv6 else [socket.AF_INET]
    assert families == expected                         # IPv6 asked first, IPv4 then


def test_make_client_mounts_the_route_and_hands_it_to_the_guide():
    from dopeiptv.providers.client import make_client
    nerd.install(S(nerd_enabled="true", nerd_dns="quad9"))
    c = make_client({"kind": "m3u", "server": "http://provider.example/list.m3u"})
    assert c.net_route.servers == doh.PRESETS["quad9"]
    assert type(c.session.get_adapter("https://provider.example/")).__name__ == "NetAdapter"
    nerd.install(S(nerd_enabled="false", nerd_dns="quad9"))
    c = make_client({"kind": "m3u", "server": "http://provider.example/list.m3u"})
    assert not c.net_route.active
    assert type(c.session.get_adapter("https://provider.example/")).__name__ == "HTTPAdapter"


def test_a_settings_change_reaches_the_clients_session():
    import requests
    s = requests.Session()
    net_adapter.remount(s, Route())                     # nothing there: nothing done
    assert type(s.get_adapter("https://x/")).__name__ == "HTTPAdapter"
    net_adapter.remount(s, Route(family="ipv4"))
    assert type(s.get_adapter("https://x/")).__name__ == "NetAdapter"
    net_adapter.remount(s, Route())                     # off again: requests' own back
    assert type(s.get_adapter("http://x/")).__name__ == "HTTPAdapter"


def test_a_restore_shows_a_trusted_certificate_and_an_own_dns_server():
    from dopeiptv.core import backup
    b = backup.Backup(kind="full", sources=[{"name": "NAS", "options": {"dns_url": "https://dns.example/q",
                                                          **cert_pin.pin_options("nas.example", 443, "ab" * 32)}}])
    assert backup.risky_sources(b) == [("NAS", ["dns_url", "cert_pin"])]


def test_the_rewind_buffers_connection_takes_the_route(monkeypatch):
    from dopeiptv.core import dvr_ingest, http_login
    seen = {}

    def fake(url, getter=None, **kw):
        seen["getter"] = getter
        return None
    monkeypatch.setattr(http_login, "get_with_login", fake)
    nerd.install(S(nerd_enabled="true"))
    nerd.set_source({"kind": "xtream", "options": {"ip_family": "ipv4"}})
    dvr_ingest.Ingest._default_open("http://provider.example/live/1.ts")
    assert seen["getter"] is not None and seen["getter"].__self__ is net_adapter.session_for(
        net_route.route_for(nerd.source()))
    nerd.set_source({"kind": "xtream", "options": {}})
    dvr_ingest.Ingest._default_open("http://provider.example/live/1.ts")
    assert seen["getter"] is None                       # as before: requests.get
    nerd.set_source(None)


# -- the certificate --------------------------------------------------------------------

def test_reading_a_certificate(tmp_path):
    cert, _key = _make_cert(tmp_path, "nas.example", "r")
    info = cert_pin.parse(ssl.PEM_cert_to_DER_cert(Path(cert).read_text(encoding="utf-8")))
    assert info.subject == {"CN": "nas.example", "O": "Home server r"} and info.self_signed
    assert info.names == ["nas.example", "127.0.0.1"]
    assert info.not_before is not None and info.not_after is not None
    assert 2 * 86400 < (info.not_after - info.not_before).total_seconds() <= 3 * 86400 + 60
    assert not info.expired()
    assert cert_pin.read(b"\x30\x03\x02\x01\x00").sha256 == cert_pin.fingerprint(b"\x30\x03\x02\x01\x00")


def test_fingerprints_written_any_way_and_the_pin_in_a_sources_options():
    fp = "ab" * 32
    assert cert_pin.normal(cert_pin.pretty(fp)) == fp == cert_pin.normal(fp.upper())
    assert cert_pin.normal("AB:CD") == "" and cert_pin.normal("zz" * 32) == ""
    opts = cert_pin.pin_options("nas.example", 8443, cert_pin.pretty(fp))
    assert opts == {"cert_pin": fp, "cert_pin_for": "nas.example:8443"}
    assert cert_pin.pin_of(opts) == ("nas.example", 8443, fp)
    assert cert_pin.pin_of(cert_pin.pin_options("::1", 443, fp)) == ("::1", 443, fp)
    assert cert_pin.pin_of({"cert_pin": fp}) is None
    assert cert_pin.host_port("https://NAS.example/x") == ("nas.example", 443)
    assert cert_pin.host_port("http://nas.example/") is None


def test_the_check_says_matches_changed_or_not_trusted(servers, tmp_path):
    cert = _make_cert(tmp_path, "127.0.0.1", "c")
    home = servers(Origin, cert)
    url = f"https://127.0.0.1:{home.port}/"
    ok = cert_pin.check(url, ("127.0.0.1", home.port, _fp(cert)))
    assert ok.state == "ok" and ok.key == "nerd_ck_pin_ok" and "SHA-256" in ok.detail
    changed = cert_pin.check(url, ("127.0.0.1", home.port, "00" * 32))
    assert changed.state == "fail" and changed.key == "nerd_ck_pin_changed"
    untrusted = cert_pin.check(url, None)
    assert untrusted.state == "warn" and untrusted.key == "nerd_ck_pin_untrusted"
    assert cert_pin.check("http://127.0.0.1/", None).key == "nerd_ck_pin_plain"


# -- the bridge: the player and recordings -----------------------------------------------

def test_a_proxys_request_goes_on_as_the_server_takes_it():
    host, port, head = net_bridge.origin_head(
        "GET", "http://provider.example:8080/live/1.ts?x=1", "HTTP/1.1",
        b"User-Agent: Lavf\r\nHost: provider.example:8080\r\nProxy-Connection: keep-alive\r\nConnection: keep-alive")
    assert (host, port) == ("provider.example", 8080)
    assert head == (b"GET /live/1.ts?x=1 HTTP/1.1\r\nUser-Agent: Lavf\r\nHost: provider.example:8080\r\n"
                    b"Connection: close\r\n\r\n")
    with pytest.raises(net_bridge.Refused):
        net_bridge.origin_head("GET", "/", "HTTP/1.1", b"Host: 127.0.0.1")
    assert net_bridge.split_target("[::1]:8443", 443) == ("::1", 8443)


def test_the_player_is_pointed_at_the_bridge_only_where_the_route_changes_something():
    n = on(nerd_dns="cloudflare")
    nerd.install(n.s)
    px = n.stream_options({}, "http://provider.example/live/1.ts")["http-proxy"]
    assert px.startswith("http://127.0.0.1:")
    assert n.stream_options({}, "http://127.0.0.1:5000/dvr/abc")["http-proxy"] == ""
    assert n.stream_options({}, "http://provider.example/1.ts", opened="http://127.0.0.1:9/x")["http-proxy"] == ""
    assert n.stream_options({}, "http://192.168.1.20:8001/1:0:1")["http-proxy"] == ""
    assert n.stream_options({"dns": "off"}, "http://provider.example/1.ts")["http-proxy"] == ""
    assert n.stream_options({}, "rtmp://provider.example/live")["http-proxy"] == ""
    proxied = on(nerd_dns="cloudflare", nerd_proxy="http://10.0.0.1:3128")
    assert proxied.stream_options({}, "http://provider.example/1.ts")["http-proxy"] == "http://10.0.0.1:3128"
    assert nerd.Nerd(S(nerd_dns="cloudflare")).stream_options({}, "http://provider.example/1.ts")["http-proxy"] == ""


def test_the_rewind_buffer_on_this_computer_is_never_asked_through_the_proxy():
    """The app-kept rewind buffer: its ingest takes the provider through
    the proxy, the player reads the buffer's own server here. mpv sends any
    address to its http-proxy (loopback too), so the proxy given for that
    stream got the loopback address and its token - and a proxy elsewhere
    cannot reach it: the channel never played."""
    proxied = on(nerd_proxy="http://10.0.0.1:3128")
    nerd.install(proxied.s)
    opts = proxied.stream_options({}, "http://provider.example/live/1.ts",
                                  opened="http://127.0.0.1:41234/tok/live.ts")
    assert opts["http-proxy"] == ""
    # The provider's own address still goes through it.
    assert proxied.stream_options({}, "http://provider.example/live/1.ts")["http-proxy"] \
        == "http://10.0.0.1:3128"


def _bridge_case(servers, tmp_path, pin_right=True):
    """The bridge, the provider (http), the home server (https, its own
    certificate) and the source whose options give that route: own DNS and
    the home server's certificate pinned."""
    cert = _make_cert(tmp_path, "nas.example", "s")
    dns = servers(FakeDoh, {"provider.example": ["127.0.0.1"], "nas.example": ["127.0.0.1"]})
    plain = servers(Origin)
    home = servers(Origin, cert)
    fp = _fp(cert) if pin_right else "00" * 32
    source = {"dns": "custom", "dns_url": dns.url, **cert_pin.pin_options("nas.example", home.port, fp)}
    route = net_route.route_for(source, on())
    assert route.servers == (dns.url,) and route.pins == (("nas.example", home.port, fp),)
    return net_bridge.Bridge(route), plain, home, source


def _recorder(bridge, source, url):
    """ffmpeg as the recorder runs it through the bridge."""
    pinned = net_bridge.checks_pin(source, url, on())
    return ["ffmpeg", "-loglevel", "error", *net_bridge.recorder_options("ffmpeg", bridge.address, pinned),
            "-i", url, "-c", "copy", "-f", "null", "-"]


def test_what_the_recorder_is_told_for_the_bridge():
    pin = cert_pin.pin_options("nas.example", 8443, "ab" * 32)
    assert net_bridge.checks_pin(pin, "https://nas.example:8443/s/2.ts", on())
    assert not net_bridge.checks_pin(pin, "https://nas.example/s/2.ts", on())       # another port
    assert not net_bridge.checks_pin(pin, "http://nas.example:8443/s/2.ts", on())
    assert not net_bridge.checks_pin({}, "https://nas.example:8443/s/2.ts", on())
    assert not net_bridge.checks_pin(pin, "https://nas.example:8443/s/2.ts",
                                     nerd.Nerd(S(nerd_enabled="false")))
    px = "http://127.0.0.1:5"
    assert net_bridge.recorder_options("ffmpeg", px, True) == ["-http_proxy", px, "-tls_verify", "0"]
    assert net_bridge.recorder_options("ffmpeg", px, False) == ["-http_proxy", px]
    assert net_bridge.recorder_options("mpv", px, True) == [f"--http-proxy={px}", "--tls-verify=no"]
    assert net_bridge.recorder_options("mpv", "", True) == []


def test_ffmpeg_through_the_bridge_http_and_https_with_the_name_kept(servers, tmp_path):
    """The home server's certificate is its own: the bridge's check of the
    pin is the trust (a newer ffmpeg checks the chain by default)."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("no ffmpeg here")
    bridge, plain, home, source = _bridge_case(servers, tmp_path)
    try:
        for url in (f"http://provider.example:{plain.port}/live/1.ts", f"https://nas.example:{home.port}/s/2.ts"):
            r = subprocess.run(_recorder(bridge, source, url), capture_output=True, timeout=60)
            assert r.returncode == 0, r.stderr[-400:]
        assert plain.requests[0] == {"path": "/live/1.ts", "host": f"provider.example:{plain.port}"}
        assert home.requests[0]["host"] == f"nas.example:{home.port}" and "nas.example" in home.sni
    finally:
        bridge.close()


def test_a_changed_certificate_stops_the_bridge(servers, tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("no ffmpeg here")
    bridge, _plain, home, source = _bridge_case(servers, tmp_path, pin_right=False)
    try:
        r = subprocess.run(_recorder(bridge, source, f"https://nas.example:{home.port}/s/2.ts"),
                           capture_output=True, timeout=60)
        assert r.returncode != 0 and home.requests == []
        assert any("changed" in f for f in bridge.failures)
        assert cert_pin.mismatch("nas.example", home.port) is not None
    finally:
        bridge.close()


def test_real_mpv_plays_through_the_bridge(servers, tmp_path):
    try:
        import mpv
        m = mpv.MPV(vo="null", ao="null", terminal=False, keep_open="yes")
    except Exception as e:
        pytest.skip(f"libmpv not available: {e}")
    own = m["tls-verify"]                       # no on 0.37, yes on a newer mpv
    bridge, _plain, home, source = _bridge_case(servers, tmp_path)
    bridge.close()                              # the player's own comes from the app's path
    nerd.install(S(nerd_enabled="true"))
    try:
        url = f"https://nas.example:{home.port}/live/3.ts"
        assert nerd.apply_stream(m, source, False, url=url)
        assert m["http-proxy"].startswith("http://127.0.0.1:") and m["tls-verify"] is False
        m.command("loadfile", url)
        end = time.monotonic() + 20
        while time.monotonic() < end and not home.requests:
            time.sleep(0.1)
        assert home.requests and "nas.example" in home.sni
        # The next stream is not to the pinned host: the core's own check.
        nerd.apply_stream(m, source, True, url="https://provider.example/live/4.ts")
        assert m["tls-verify"] == own
        nerd.apply_stream(m, source, True, url=url)
        nerd.install(S(nerd_enabled="false"))
        nerd.apply_stream(m, source, True, url=url)
        assert m["tls-verify"] == own and m["http-proxy"] == ""
    finally:
        m.terminate()
        for b in list(net_bridge._bridges.values()):
            b.close()
        net_bridge._bridges.clear()


def test_a_recording_goes_through_the_bridge(monkeypatch, tmp_path):
    from PyQt6.QtCore import QSettings

    from dopeiptv.core import recording
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    qs = QSettings(str(tmp_path / "rec.ini"), QSettings.Format.IniFormat)
    qs.setValue("recordings_dir", str(tmp_path))
    nerd.install(S(nerd_enabled="true", nerd_dns="google"))
    nerd.set_source({"kind": "xtream", "options": {}})
    mgr = recording.RecordingManager(qs)
    calls = []

    class P:
        def __init__(self, cmd, **kw):
            calls.append(cmd)

        def poll(self):
            return None
    monkeypatch.setattr(recording.RecordingManager, "recorder", staticmethod(lambda: ("ffmpeg", "/usr/bin/ffmpeg")))
    monkeypatch.setattr(recording.subprocess, "Popen", P)
    job = {"id": "1", "url": "http://provider.example/live/u/p/1.ts", "title": "News",
           "stop": time.time() + 60, "status": "scheduled"}
    mgr._spawn(job)
    cmd = calls[-1]
    i = cmd.index("-http_proxy")
    assert cmd[i + 1].startswith("http://127.0.0.1:") and cmd.index("-i") > i
    assert "-tls_verify" not in cmd                     # the stream's own check
    # A pinned home server: the bridge checks the pin, the recorder not the chain.
    nerd.set_source({"kind": "xtream", "options": cert_pin.pin_options("nas.example", 8443, "ab" * 32)})
    pinned = dict(job, id="p", url="https://nas.example:8443/live/u/p/1.ts")
    mgr._spawn(pinned)
    cmd = calls[-1]
    i = cmd.index("-http_proxy")
    assert cmd[i + 2:i + 4] == ["-tls_verify", "0"] and cmd.index("-i") == i + 4
    monkeypatch.setattr(recording.RecordingManager, "recorder", staticmethod(lambda: ("mpv", "/usr/bin/mpv")))
    mgr._spawn(dict(pinned, id="m"))
    assert "--tls-verify=no" in calls[-1] and any(a.startswith("--http-proxy=") for a in calls[-1])
    mgr._spawn(dict(job, id="n"))
    assert "--tls-verify=no" not in calls[-1]
    monkeypatch.setattr(recording.RecordingManager, "recorder", staticmethod(lambda: ("ffmpeg", "/usr/bin/ffmpeg")))
    nerd.set_source({"kind": "xtream", "options": {}})
    nerd.install(S(nerd_enabled="false", nerd_dns="google"))
    mgr._spawn(dict(job, id="2"))
    assert "-http_proxy" not in calls[-1]
    nerd.set_source(None)


# -- low latency ---------------------------------------------------------------------------

class FakeMpv(dict):
    """An mpv core as these options see it: refuses what *refuse* names."""

    def __init__(self, refuse=(), **kw):
        super().__init__({"cache-secs": 10.0, "demuxer-readahead-secs": 1.0, "cache-pause-wait": 1.0,
                          "demuxer-lavf-analyzeduration": 0.0, "demuxer-lavf-probesize": 0,
                          "video-latency-hacks": False, "demuxer-lavf-o": {}, **kw})
        self.refuse = set(refuse)

    def __setitem__(self, key, value):
        if key in self.refuse or (key == "demuxer-lavf-probesize" and value == 0):
            raise ValueError(f"refused {key}")
        super().__setitem__(key, value)


def test_low_latency_for_a_live_channel_and_back_for_the_next_film():
    m = FakeMpv()
    nerd.install(S(nerd_enabled="true", nerd_low_latency="true"))
    took = live_latency.apply(m, live=True)
    assert set(took) == {k for k, _v in live_latency.options()}
    assert m["cache-secs"] == "2" and m["cache-pause-wait"] == "0.5" and m["video-latency-hacks"] == "yes"
    assert m["demuxer-lavf-o"] == "fflags=+nobuffer,live_start_index=-1"
    m["cache-secs"] = 10.0                              # the player's own, for the next stream
    assert live_latency.apply(m, live=False) == []
    assert m["cache-secs"] == 10.0 and m["cache-pause-wait"] == 1.0 and m["demuxer-readahead-secs"] == 1.0
    assert m["video-latency-hacks"] is False and m["demuxer-lavf-o"] == {}
    assert m["demuxer-lavf-probesize"] == "5000000"     # mpv's 0 ("not set") is not taken back


def test_low_latency_keeps_nerds_own_options_and_goes_when_the_switch_does():
    m = FakeMpv()
    nerd.install(S(nerd_enabled="true", nerd_low_latency="true", nerd_probe="thorough", nerd_reconnect_max="5"))
    nerd.apply_core(m)
    live_latency.apply(m, live=True)
    assert m["demuxer-lavf-o"].startswith("reconnect=1,") and m["demuxer-lavf-o"].endswith(
        "fflags=+nobuffer,live_start_index=-1")
    assert m["demuxer-lavf-analyzeduration"] == "0.5"
    nerd.install(S(nerd_enabled="true", nerd_low_latency="false", nerd_probe="thorough", nerd_reconnect_max="5"))
    live_latency.apply(m, live=True)                    # switched off: a live channel as before
    assert m["demuxer-lavf-analyzeduration"] == "5"      # the Channel start choice, not mpv's default
    assert "nobuffer" not in m["demuxer-lavf-o"] and m["demuxer-lavf-o"].startswith("reconnect=1")
    nerd.install(S(nerd_enabled="false"))
    assert live_latency.apply(m, live=True) == []


def test_low_latency_never_touches_anything_while_off():
    m = FakeMpv()
    snapshot = dict(m)
    nerd.install(S(nerd_enabled="true"))
    assert live_latency.apply(m, live=True) == [] and dict(m) == snapshot
    nerd.install(S(nerd_enabled="false", nerd_low_latency="true"))
    assert live_latency.apply(m, live=True) == [] and dict(m) == snapshot


def test_an_option_this_mpv_refuses_is_skipped():
    m = FakeMpv(refuse={"video-latency-hacks"})
    nerd.install(S(nerd_enabled="true", nerd_low_latency="true"))
    took = live_latency.apply(m, live=True)
    assert "video-latency-hacks" not in took and "cache-pause-wait" in took
    live_latency.apply(m, live=False)
    assert m["cache-pause-wait"] == 1.0


def test_low_latency_on_a_real_mpv():
    try:
        import mpv
        m = mpv.MPV(vo="null", ao="null", terminal=False)
    except Exception as e:
        pytest.skip(f"libmpv not available: {e}")
    try:
        nerd.install(S(nerd_enabled="true", nerd_low_latency="true"))
        before = {k: m[k] for k in ("cache-pause-wait", "demuxer-readahead-secs", "video-latency-hacks",
                                    "demuxer-lavf-o")}
        took = live_latency.apply(m, live=True)
        assert len(took) == len(live_latency.options())
        assert m["demuxer-lavf-o"] == {"fflags": "+nobuffer", "live_start_index": "-1"}
        assert m["cache-pause-wait"] == 0.5 and m["video-latency-hacks"] is True
        live_latency.apply(m, live=False)
        assert {k: m[k] for k in before} == before
        check = live_latency.check(lambda: mpv.MPV(vo="null", ao="null", terminal=False))
        assert check.state == "ok" and check.key == "nerd_ck_latency_ok"
    finally:
        m.terminate()
