"""The app's own servers, asked by someone who means harm.

An address on the network that opens connections by the thousand, a Host
header of its own choosing in a playlist's addresses, a source's address
with a line break in it as a redirect, an answer nobody reads, the tuner's
endpoints - which take no token - asked without pause, and what a device
at home can do and learn there without the token.
"""

from __future__ import annotations

import hmac
import http.client
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from dopeiptv.core import dvr_server, hdhr, home_net, remote_api
from dopeiptv.core.dvr_pool import DvrPool
from dopeiptv.core.dvr_store import SegmentStore
from dopeiptv.core.exclusive_bind import ConnectionLimitMixin
from dopeiptv.core.phone_remote import RateLimiter
from dopeiptv.providers import cast_bridge, dlna
from dopeiptv.services import home_server as hs
from tests.test_server_mode import TOKEN, FakeClient, Host

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
PROVIDER = "http://provider.invalid:8080/live/user1/secretpw"


def _until(check, secs: float = 10.0) -> bool:
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.02)
    return bool(check())


def _ask(port: int, path: str, host: str | None = None, method: str = "GET",
         body: bytes | None = None, headers: dict[str, str] | None = None):
    """One request with exactly the Host given (none: HTTP/1.0's)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.putrequest(method, path, skip_host=True)
    if host is not None:
        conn.putheader("Host", host)
    for k, v in (headers or {}).items():
        conn.putheader(k, v)
    if body is not None:
        conn.putheader("Content-Length", str(len(body)))
    conn.endheaders(body)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp, data


# -- the names a home has ---------------------------------------------------------------

HOME = ("192.168.1.20:3673", "10.0.0.5", "127.0.0.1:5004", "[fd00::1]:3673", "[::1]",
        "localhost:3673", "mediapc", "mediapc:5004", "mediapc.local", "nas.lan:5004",
        "tv.home.arpa", "pc.internal", "box.fritz.box:1", "pc.tail1234.ts.net:5004",
        "PC.Local", "100.101.102.103:5004")
NOT_HOME = ("evil.example.com", "evil.example.com:5004", "attacker.net", "a.local.evil.com",
            "192.168.1.20.evil.com", "", " ", "[fd00::1", "x y.local", "a\r\nb.local",
            "user@mediapc.local", "mediapc.local/x", "mediapc.local?x", "é.local",
            "a" * 250 + ".local")


def test_a_home_s_names_and_addresses_are_kept_and_no_other_host():
    for host in HOME:
        assert home_net.home_host(host), host
        assert home_net.host_for_addresses(host) == host, host
    for host in NOT_HOME:
        assert home_net.host_for_addresses(host) == "", host
    # The tuner's rule is the same one, and still lets an HTTP/1.0 client by.
    assert hs.tuner_host_ok(None) and hs.tuner_host_ok("192.168.1.20:5004")
    assert not hs.tuner_host_ok("evil.example.com")
    assert hs.HOME_SUFFIXES is home_net.HOME_SUFFIXES


# -- how many connections ----------------------------------------------------------------

class _Waits(BaseHTTPRequestHandler):
    def log_message(self, *a) -> None:
        pass

    def do_GET(self) -> None:
        self.server.gate.wait(20)             # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


class _Limited(ConnectionLimitMixin, ThreadingHTTPServer):
    daemon_threads = True


def _held(port: int) -> socket.socket:
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    s.sendall(b"GET / HTTP/1.0\r\n\r\n")
    return s


def _closed_unanswered(s: socket.socket) -> bool:
    try:
        return s.recv(64) == b""
    except (ConnectionResetError, BrokenPipeError):
        return True


@pytest.mark.parametrize("in_all, per_client", [(2, 9), (9, 2)])
def test_one_more_connection_than_a_server_serves_is_closed_unanswered(in_all, per_client):
    srv = _Limited(("127.0.0.1", 0), _Waits)
    srv.max_connections, srv.max_per_client = in_all, per_client
    srv.gate = threading.Event()              # type: ignore[attr-defined]
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05},
                     daemon=True).start()
    port = srv.server_address[1]
    held = [_held(port), _held(port)]
    try:
        assert _until(lambda: srv.connections() == 2)
        assert srv.connections("127.0.0.1") == 2 and srv.connections("192.0.2.1") == 0
        extra = _held(port)
        assert _closed_unanswered(extra), "one more than its share is not served"
        extra.close()
        assert srv.turned_away == 1 and srv.connections() == 2
        srv.gate.set()                        # type: ignore[attr-defined]
        for s in held:
            assert s.recv(256).startswith(b"HTTP/1.0 200")
        # Every connection served is counted out again: none is lost.
        assert _until(lambda: srv.connections() == 0)
        again = _held(port)
        assert again.recv(256).startswith(b"HTTP/1.0 200")
        again.close()
        assert _until(lambda: srv.connections() == 0) and srv.turned_away == 1
    finally:
        srv.gate.set()                        # type: ignore[attr-defined]
        for s in held:
            s.close()
        srv.shutdown()
        srv.server_close()


def test_the_app_s_lan_servers_all_count_their_connections():
    for cls in (remote_api._Server, hs._Server, cast_bridge._Server, dlna._FileServer):
        assert issubclass(cls, ConnectionLimitMixin), cls
    from dopeiptv.providers import airplay_bridge
    assert issubclass(airplay_bridge._Server, ConnectionLimitMixin)


# -- the remote API and its shared playlist ---------------------------------------------

class _Share:
    def m3u(self, base: str, token: str) -> str:
        return f"#EXTM3U\n#EXTINF:-1,One\n{base}/stream/s/1?token={token}\n"

    def stream_url(self, _source: str, _stream: str) -> str:
        return "http://h.invalid/live/1.ts\r\nX-Injected: yes\r\n\r\n<html>"

    def guide(self, _gz: bool):
        return iter([b"<tv/>"])


@pytest.fixture
def remote():
    srv = remote_api.RemoteServer("127.0.0.1", 0, "t" * 32, call=lambda _a, _p: {"ok": True},
                                  share=_Share(), remote=True)
    srv.start()
    yield srv
    srv.stop()


def test_the_shared_playlist_is_never_written_under_a_host_of_the_asker_s_choosing(remote):
    path = f"/playlist.m3u?token={remote.token}"
    here = f"http://127.0.0.1:{remote.port}/stream/s/1?token={remote.token}"
    for host in ("evil.example.com", "evil.example.com:80", "192.168.1.20.evil.com", None):
        resp, body = _ask(remote.port, path, host)
        assert resp.status == 200
        assert here in body.decode(), host
        assert "evil" not in body.decode()
    # Every name and address a device at home asks by is kept as it was.
    for host in ("mediapc.local:3673", "192.168.1.20:3673", "[fd00::1]:3673",
                 "pc.tail1234.ts.net:3673", "mediapc:3673", f"localhost:{remote.port}"):
        _resp, body = _ask(remote.port, path, host)
        assert f"http://{host}/stream/s/1?token={remote.token}" in body.decode(), host


def test_a_source_s_address_with_a_line_break_adds_no_header_to_the_redirect(remote):
    resp, body = _ask(remote.port, f"/stream/s/1?token={remote.token}", "127.0.0.1")
    assert resp.status == 302 and body == b""
    assert resp.getheader("X-Injected") is None
    where = resp.getheader("Location") or ""
    assert where.startswith("http://h.invalid/live/1.ts%0D%0A")
    assert "\r" not in where and "\n" not in where and " " not in where
    # An address as it should be goes out untouched.
    plain = "https://h.invalid:8080/live/u/p/1.ts?a=1&b=%20x#t"
    assert remote_api.header_safe(plain) == plain


def test_no_answer_is_sniffed_or_hands_its_address_on(remote):
    for path in (f"/?token={remote.token}", f"/guide.xml?token={remote.token}", "/api/status"):
        resp, _body = _ask(remote.port, path, "127.0.0.1")
        assert resp.getheader("X-Content-Type-Options") == "nosniff", path
        assert resp.getheader("Referrer-Policy") == "no-referrer", path


# -- server mode ----------------------------------------------------------------------------

@pytest.fixture
def home(tmp_path):
    pool = DvrPool(tmp_path, 60.0, 10**9, min_free=0)
    host = Host(pool)
    cfg = hs.Config(port=0, tuners=0, token=TOKEN, device_id=hdhr.new_device_id())
    srv = hs.HomeServer(host, cfg, bind="127.0.0.1", discovery_port=0,
                        make_client=lambda _pl: FakeClient(PROVIDER),
                        guide_path=lambda pid: str(tmp_path / f"epg_{pid}.xml"))
    srv.start()
    yield srv, host
    srv.stop()
    pool.close_all(wait=2.0)


def test_server_mode_s_addresses_are_never_under_a_host_of_the_asker_s_choosing(home):
    srv, _host = home
    for path in (f"/playlist.m3u?token={TOKEN}", "/lineup.json", f"/api/status?token={TOKEN}"):
        # The tuner's own endpoints refuse the name outright; the rest
        # answer, at this computer's address.
        resp, body = _ask(srv.port, path, "evil.example.com")
        assert "evil" not in body.decode(), path
        if path != "/lineup.json":
            assert resp.status == 200 and f"http://127.0.0.1:{srv.port}/" in body.decode(), path
    for host in ("mediapc.local:5004", "192.168.1.20:5004", "pc.tail1234.ts.net:5004"):
        _resp, body = _ask(srv.port, f"/playlist.m3u?token={TOKEN}", host)
        assert f"http://{host}/stream/p1/1?token={TOKEN}" in body.decode(), host
        _resp, body = _ask(srv.port, "/lineup.json", host)
        assert json.loads(body)[0]["URL"].startswith(f"http://{host}/auto/v"), host
    assert srv.base("evil.example.com", "127.0.0.1") == f"http://127.0.0.1:{srv.port}"


def test_the_tuner_s_endpoints_are_held_to_a_pace_and_the_rest_is_not(home):
    srv, _host = home
    srv.tuner_rate = RateLimiter(rate=0.001, burst=3.0)
    for _ in range(3):
        resp, _body = _ask(srv.port, "/discover.json", "127.0.0.1")
        assert resp.status == 200
    for path in ("/discover.json", "/lineup.json", "/auto/v5", "/lineup.post"):
        resp, _body = _ask(srv.port, path, "127.0.0.1")
        assert resp.status == 429 and resp.getheader("Retry-After"), path
    # What takes the token is not held up by it, and another address not either.
    resp, _body = _ask(srv.port, f"/api/status?token={TOKEN}", "127.0.0.1")
    assert resp.status == 200
    assert srv.tuner_rate.allow("192.168.1.9")
    # As it is set: a media server's few questions a minute are far inside it.
    assert hs.TUNER_BURST >= 60 and hs.TUNER_RATE >= 10


def test_without_the_token_a_device_at_home_changes_nothing_and_learns_no_secret(home):
    srv, host = home
    change = json.dumps({"adult": True, "port": 1, "tuners": 9}).encode()
    for path in ("/api/settings", "/api/settings?token=wrong"):
        resp, _body = _ask(srv.port, path, "127.0.0.1", "POST", change,
                           {"Content-Type": "application/json"})
        assert resp.status == 401, path
    for path in ("/", "/api/status", "/api/settings", "/api/categories?source=p1",
                 "/playlist.m3u", "/xmltv.xml", "/stream/p1/1"):
        resp, body = _ask(srv.port, path, "127.0.0.1")
        assert resp.status == 401 and TOKEN not in body.decode(), path
    # What the HDHomeRun protocol needs, and no more: the lineup's numbers
    # and names at the tuner's own addresses - no token, nothing of the
    # provider's address or login.
    seen = b""
    for path in ("/discover.json", "/lineup.json", "/lineup_status.json", "/device.xml"):
        resp, body = _ask(srv.port, path, "127.0.0.1")
        assert resp.status == 200, path
        seen += body + repr(resp.getheaders()).encode()
    resp, body = _ask(srv.port, "/lineup.post?scan=start&source=Cable", "127.0.0.1", "POST", b"x")
    assert resp.status == 200
    seen += body
    for secret in (TOKEN, "secretpw", "user1", "provider.invalid"):
        assert secret.encode() not in seen, secret
    assert host.saved == {} and srv.config.token == TOKEN and not srv.config.adult
    at = f"127.0.0.1:{srv.port}"
    urls = [c["URL"] for c in json.loads(_ask(srv.port, "/lineup.json", at)[1])]
    assert urls and all(u.startswith(f"http://{at}/auto/v") for u in urls)


def test_an_answer_nobody_reads_is_let_go_with_its_thread(home, monkeypatch):
    srv, _host = home
    monkeypatch.setattr(hs, "ANSWER_TIMEOUT", 0.4)

    def endless():
        while True:
            yield b"<programme/>" * 4096

    srv.guide = endless                        # type: ignore[method-assign]
    s = socket.create_connection(("127.0.0.1", srv.port), timeout=10)
    try:
        s.sendall(f"GET /xmltv.xml?token={TOKEN} HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n".encode())
        assert srv._httpd is not None
        assert _until(lambda: srv._httpd.connections() == 1, 5.0)
        # Nothing is read here: the server gives up on it by itself.
        assert _until(lambda: srv._httpd.connections() == 0, 15.0)
    finally:
        s.close()


def test_a_source_without_a_name_is_told_without_its_login(home):
    srv, host = home
    host.sources = lambda: [{"id": "p9", "kind": "m3u", "server":       # type: ignore[method-assign]
                             "http://h.invalid/get.php?username=u1&password=secretpw&type=m3u"}]
    name = srv.settings_view()["sources"][0]["name"]
    assert "h.invalid" in name and "secretpw" not in name and "u1" not in name


# -- the rewind buffer's and the casts' servers ---------------------------------------------

def test_the_rewind_buffer_s_server_lets_a_silent_connection_go(tmp_path, monkeypatch):
    monkeypatch.setattr(dvr_server._Handler, "idle_timeout", 0.3)
    store = SegmentStore(tmp_path / "s", 60.0, 10**9, min_free=0)
    store.append(b"A" * 188 * 4)
    srv = dvr_server.LocalServer(store)
    try:
        s = socket.create_connection(("127.0.0.1", srv.port), timeout=10)
        assert s.recv(16) == b"", "a connection that asks nothing is closed"
        s.close()
        # And one that asks is answered as before.
        with OPENER.open(srv.url(0), timeout=10) as r:
            assert r.status == 200 and r.read(188) == b"A" * 188
    finally:
        store.close()
        srv.close()


def test_a_cast_s_address_is_compared_whole_in_the_same_time(monkeypatch):
    seen: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a, b):
        seen.append((a, b))
        return real(a, b)

    monkeypatch.setattr(cast_bridge.hmac, "compare_digest", spy)
    assert cast_bridge._same("/tok/stream.mp4", "/tok/stream.mp4")
    assert not cast_bridge._same("/tok/stream.mp4x", "/tok/stream.mp4")
    assert not cast_bridge._same("/tök/stream.mp4", "/tok/stream.mp4")
    assert not cast_bridge._same("/tok/stream.mp4", None)
    assert cast_bridge._under("/tok/seg1.ts", "/tok/")
    assert not cast_bridge._under("/tox/seg1.ts", "/tok/")
    assert not cast_bridge._under("/tok/seg1.ts", None)
    assert len(seen) >= 5
    b = cast_bridge.CastBridge()
    assert b.path is None and not cast_bridge._same("/", b.path)


def test_a_cast_file_s_address_has_a_long_token_and_no_other_opens_it(tmp_path, monkeypatch):
    film = tmp_path / "film.mp4"
    film.write_bytes(b"x" * 100)
    monkeypatch.setattr(dlna, "lan_address", lambda: "127.0.0.1")
    used: list[int] = []
    real = hmac.compare_digest

    def spy(a, b):
        used.append(1)
        return real(a, b)

    monkeypatch.setattr(dlna.hmac, "compare_digest", spy)
    share = dlna.FileShare()
    url = share.start(str(film))
    try:
        parts = urlsplit(url)
        token = parts.path.split("/")[1]
        assert len(token) >= 22, "128 bits and more"
        with OPENER.open(f"http://127.0.0.1:{parts.port}{parts.path}", timeout=10) as r:
            assert r.read() == b"x" * 100
        for wrong in (f"/{'A' * len(token)}/film.mp4", "/film.mp4", f"/{token}/other.mp4",
                      f"/{token}/film.mp4/..", "/t%C3%B6k/film.mp4"):
            with pytest.raises(urllib.error.HTTPError) as e:
                OPENER.open(f"http://127.0.0.1:{parts.port}{wrong}", timeout=10)
            assert e.value.code == 404, wrong
        assert used
    finally:
        share.stop()
