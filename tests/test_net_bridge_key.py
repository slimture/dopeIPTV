"""The net bridge is the app's own player's and recorder's, nobody else's.

It listens on 127.0.0.1, which is every program's on the computer and every
account's: without a key it was an open proxy for all of them - GET
http://anything and CONNECT anywhere, out as this person and through their
route. Each bridge has a key now, handed to mpv and ffmpeg as the proxy's
login; a request without it gets 407 and nothing is looked up or connected.
"""

from __future__ import annotations

import base64
import logging
import shutil
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from dopeiptv.core import net_bridge
from dopeiptv.core.log import log
from dopeiptv.core.net_route import Route

TS = (Path(__file__).parent / "data" / "tiny.ts").read_bytes()


class Inside:
    """A service on this computer nobody else was to reach through the app."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        me = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_GET(self):
                me.requests.append({"path": self.path,
                                    "headers": {k.lower(): v for k, v in self.headers.items()}})
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Content-Length", str(len(TS)))
                self.end_headers()
                self.wfile.write(TS)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def case(monkeypatch):
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(k, raising=False)
    inside = Inside()
    bridge = net_bridge.Bridge(Route(family="ipv4"))
    try:
        yield bridge, inside
    finally:
        bridge.close()
        inside.close()


def _ask(bridge, request: str) -> bytes:
    with socket.create_connection(("127.0.0.1", bridge.port), timeout=10) as s:
        s.sendall(request.encode())
        s.settimeout(10)
        got = b""
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


def _login(bridge, key: str | None = None) -> str:
    word = base64.b64encode(f"{net_bridge.USER}:{key or bridge.secret}".encode()).decode()
    return f"Proxy-Authorization: Basic {word}\r\n"


def test_another_program_gets_nothing_through_the_bridge(case):
    """The attack as it was made: any local process, GET and CONNECT."""
    bridge, inside = case
    target = f"127.0.0.1:{inside.port}"
    for request in (f"GET http://{target}/secret HTTP/1.1\r\nHost: {target}\r\nConnection: close\r\n\r\n",
                    f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n",
                    "GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"):
        got = _ask(bridge, request)
        head = got.split(b"\r\n\r\n", 1)[0].decode()
        assert head.startswith("HTTP/1.1 407 "), head
        assert 'proxy-authenticate: basic realm="dopeiptv"' in head.lower()
        assert TS not in got
    assert inside.requests == [] and bridge.served == 0 and bridge.unkeyed == 3


@pytest.mark.parametrize("login", [
    "Proxy-Authorization: Basic ZG9wZWlwdHY6d3Jvbmc=\r\n",            # dopeiptv:wrong
    "Proxy-Authorization: Basic \r\n", "Proxy-Authorization: Bearer x\r\n",
    "Authorization: Basic ZG9wZWlwdHY6d3Jvbmc=\r\n", "Proxy-Authorization: Basic !!!\r\n"])
def test_a_wrong_key_is_no_key(case, login):
    bridge, inside = case
    target = f"127.0.0.1:{inside.port}"
    got = _ask(bridge, f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n{login}\r\n")
    assert got.startswith(b"HTTP/1.1 407 ") and inside.requests == []
    # ...and another bridge's key is not this one's.
    other = net_bridge.Bridge(Route(family="ipv4"))
    try:
        assert other.secret != bridge.secret and len(bridge.secret) >= 32
        got = _ask(bridge, f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n{_login(other)}\r\n")
        assert got.startswith(b"HTTP/1.1 407 ")
    finally:
        other.close()


def test_with_its_key_the_bridge_works_and_the_key_goes_no_further(case):
    bridge, inside = case
    target = f"127.0.0.1:{inside.port}"
    got = _ask(bridge, f"GET http://{target}/live/1.ts HTTP/1.1\r\nHost: {target}\r\n"
                       f"{_login(bridge)}User-Agent: X/1\r\n\r\n")
    assert got.startswith(b"HTTP/1.0 200") or got.startswith(b"HTTP/1.1 200")
    assert got.endswith(TS)
    sent = inside.requests[0]
    assert sent["path"] == "/live/1.ts" and sent["headers"]["user-agent"] == "X/1"
    # The proxy's login is the proxy's: the server the stream is on never sees it.
    assert "proxy-authorization" not in sent["headers"]
    assert bridge.secret not in repr(sent)
    assert _ask(bridge, f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n{_login(bridge)}\r\n"
                ).startswith(b"HTTP/1.1 200 Connection established")
    # The bridge counts a tunnel on its own thread once it has answered: on a
    # busy machine that can come a moment after the answer is read here.
    import time
    end = time.monotonic() + 10
    while bridge.served < 2 and time.monotonic() < end:
        time.sleep(0.01)
    assert bridge.served == 2 and bridge.unkeyed == 0


def test_the_address_handed_out_carries_the_key_and_the_log_does_not(case, monkeypatch):
    from urllib.parse import urlsplit

    bridge, _inside = case
    u = urlsplit(bridge.address)
    assert (u.scheme, u.hostname, u.port) == ("http", "127.0.0.1", bridge.port)
    assert u.username == net_bridge.USER and u.password == bridge.secret
    assert bridge.secret not in bridge.where
    assert net_bridge.proxy_login(_login(bridge).encode()) \
        == base64.b64encode(f"{net_bridge.USER}:{bridge.secret}".encode())
    assert net_bridge.proxy_login(b"Host: x\r\nproxy-AUTHORIZATION:   basic   abc  ") == b"abc"
    assert net_bridge.proxy_login(b"Host: x") == b""

    lines: list[str] = []

    class Catch(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())
    catch = Catch(logging.DEBUG)
    level = log.level
    log.addHandler(catch)
    log.setLevel(logging.DEBUG)
    monkeypatch.setattr(net_bridge, "_bridges", {})
    try:
        made = net_bridge.bridge_for(Route(family="ipv6"))
        _ask(made, "GET http://127.0.0.1:9/ HTTP/1.1\r\nHost: x\r\n\r\n")
        made.close()
    finally:
        log.removeHandler(catch)
        log.setLevel(level)
    assert lines and all(made.secret not in line for line in lines)
    assert any(made.where in line for line in lines)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_the_real_ffmpeg_goes_through_with_the_key_and_not_without(case):
    """ffmpeg itself: it sends a proxy's login once the proxy has asked,
    so its first try is turned away and its second goes through - and
    with the bridge's bare address (no key) it reads nothing."""
    bridge, inside = case
    url = f"http://127.0.0.1:{inside.port}/live/2.ts"
    env = {"PATH": __import__("os").environ.get("PATH", "")}

    def record(proxy: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error",
             *net_bridge.recorder_options("ffmpeg", proxy, False),
             "-i", url, "-c", "copy", "-f", "null", "-"],
            capture_output=True, timeout=60, env=env, check=False)

    bare = record(f"http://{bridge.where}")
    assert bare.returncode != 0 and inside.requests == []
    keyed = record(bridge.address)
    assert keyed.returncode == 0, keyed.stderr[-400:]
    assert [r["path"] for r in inside.requests] == ["/live/2.ts"]
    assert "proxy-authorization" not in inside.requests[0]["headers"]


def test_the_bridge_serves_a_bounded_number_of_connections_and_drops_a_trickle(case, monkeypatch):
    """Another account on this computer cannot pile up threads: past
    MAX_SERVING a connection is closed before any work, and one that sends
    its request a byte at a time is let go once HEAD_TIMEOUT is over."""
    import time
    bridge, _inside = case
    monkeypatch.setattr(net_bridge, "HEAD_TIMEOUT", 0.6)
    held = []
    try:
        for _ in range(net_bridge.MAX_SERVING):
            held.append(socket.create_connection(("127.0.0.1", bridge.port), timeout=5))
        deadline = time.monotonic() + 5
        extra = None
        while time.monotonic() < deadline:
            extra = socket.create_connection(("127.0.0.1", bridge.port), timeout=5)
            extra.settimeout(2)
            try:
                if extra.recv(1) == b"":
                    break               # closed at once: its share was taken
            except (TimeoutError, OSError):
                pass
            extra.close()
            time.sleep(0.05)
        assert bridge.turned_away >= 1
    finally:
        for s in held:
            s.close()
    trickle = socket.create_connection(("127.0.0.1", bridge.port), timeout=5)
    trickle.settimeout(5)
    start = time.monotonic()
    try:
        while time.monotonic() - start < 4:
            trickle.sendall(b"G")
            time.sleep(0.2)
    except OSError:
        pass                            # closed on it - as it should be
    assert time.monotonic() - start < 3.5, "the trickle was let go"
    trickle.close()
