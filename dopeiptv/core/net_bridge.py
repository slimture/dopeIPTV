"""The player's and recordings' way onto a source's route (Nerd,
core/net_route.py): a small HTTP proxy on this computer that mpv and ffmpeg
are pointed at (http-proxy, -http_proxy) when a source's own DNS, its own
IP version or a pinned certificate asks for it.

Through a proxy, ffmpeg asks for an https stream with CONNECT host:port and
makes the TLS connection itself, through the tunnel: the server name it
sends (SNI), its Host header and its certificate check stay the stream's
own, and only the lookup and the TCP connection are made here, as the
route says. That is why this is a proxy and not the address with the IP
put in: ffmpeg's TLS takes the server name from the address, so with an IP
there it sends none, and a server that picks its certificate (or its site)
by name - most behind a CDN - refuses. A plain http stream comes as an
absolute address (GET http://...); it goes on in the form a server expects,
with Connection: close, so that each request is looked up for its own host
(an HLS playlist's segments are often on another, a panel redirects to its
load balancer).

A pinned certificate cannot be read inside the tunnel (TLS 1.3 sends it
encrypted). Before a tunnel to a pinned host opens, the bridge makes a TLS
connection of its own to it and checks the fingerprint - again after
PIN_RECHECK seconds - and refuses the tunnel when it differs. That check is
the trust for a pinned host: a home server's own certificate is signed by
no authority, and a newer ffmpeg or mpv checks the chain by default (FFmpeg
9 and mpv 0.42 here; FFmpeg 6 and mpv 0.37 did not), so for a stream to a
pinned host - and only there - they are told not to (checks_pin).

That leaves the rest of such a stream unchecked by them: a redirect, or an
HLS segment on another host, comes through the bridge too. So on a route
with a pin every other https host is checked here before its tunnel opens,
as the player would check it - a TLS connection of its own, the system's
authorities, for that name; again after TRUST_RECHECK seconds - and the
tunnel is refused when that fails. Only the pinned host goes by its pin.

Only this computer connects (127.0.0.1), each route has a bridge of its own
on a port of its own, and what is not a proxy's request (a web page's
GET /...) is refused.

And only the app's own player and recorder use it. A port on 127.0.0.1 is
every program's on this computer, and every account's: without a key the
bridge was an open proxy for all of them - out onto the network as this
person, through their route. Each bridge makes a key as it starts (kept in
memory, gone with the app) and is handed out as the proxy's login, in its
address (``Bridge.address``: http://dopeiptv:<key>@127.0.0.1:<port>), which
is how mpv and ffmpeg take a proxy's login. A request without it gets 407
and nothing else: no lookup, no connection. ffmpeg (and so mpv) sends a
proxy's login only once the proxy has asked for it, so each of its
connections is asked once - a round trip on this computer - before it goes
through.
"""

from __future__ import annotations

import base64
import hmac
import secrets
import selectors
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

from . import cert_pin, doh, net_route
from .log import log
from .net_route import Route

PIN_RECHECK = 600.0
TRUST_RECHECK = 600.0
CONNECT_TIMEOUT = 10.0
HEAD_MAX = 64 * 1024
#: Connections a bridge serves at once - the player's and the recorder's
#: need a handful. One more is closed before a thread is made for it: a
#: program of another account on this computer that opens thousands gets
#: nothing but closed sockets.
MAX_SERVING = 64
#: A connection that has not sent its request with the key by then is let go.
HEAD_TIMEOUT = 10.0
_HOP = (b"proxy-connection", b"proxy-authorization", b"connection", b"keep-alive")
#: The name in the bridge's login; its key is the password (Bridge.secret).
USER = "dopeiptv"
_CHALLENGE = (b"HTTP/1.1 407 Proxy Authentication Required\r\n"
              b'Proxy-Authenticate: Basic realm="dopeIPTV"\r\n'
              b"Content-Length: 0\r\nConnection: close\r\n\r\n")


class Refused(Exception):
    pass


def _read_head(sock: socket.socket, within: float | None = None) -> tuple[bytes, bytes] | None:
    """A request's head, read in *within* seconds at most in all (a sender
    that trickles a byte at a time is let go too)."""
    buf = b""
    deadline = None if within is None else time.monotonic() + within
    while b"\r\n\r\n" not in buf:
        if len(buf) > HEAD_MAX:
            return None
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            sock.settimeout(left)
        data = sock.recv(8192)
        if not data:
            return None
        buf += data
    head, _sep, rest = buf.partition(b"\r\n\r\n")
    return head, rest


def proxy_login(fields: bytes) -> bytes:
    """What a request's headers give as the proxy's login: the word after
    ``Proxy-Authorization: Basic`` (b"" for none, or another scheme)."""
    for raw in fields.split(b"\r\n"):
        name, sep, value = raw.partition(b":")
        if sep and name.strip().lower() == b"proxy-authorization":
            scheme, _sp, word = value.strip().partition(b" ")
            return word.strip() if scheme.lower() == b"basic" else b""
    return b""


def _reply(sock: socket.socket, code: int, text: str) -> None:
    body = text.encode("utf-8", "replace")
    reason = {400: "Bad Request", 502: "Bad Gateway", 504: "Gateway Timeout"}.get(code, "Error")
    try:
        sock.sendall(f"HTTP/1.1 {code} {reason}\r\nContent-Type: text/plain; charset=utf-8\r\n"
                     f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("ascii") + body)
    except OSError:
        pass


def split_target(target: str, default_port: int) -> tuple[str, int]:
    """host and port of a CONNECT target ("[::1]:443" too)."""
    u = urlsplit("//" + target)
    return (u.hostname or "").lower(), u.port or default_port


def origin_head(method: str, target: str, version: str, fields: bytes) -> tuple[str, int, bytes]:
    """(host, port, the request as the server takes it) of a proxy's GET
    http://host/path: the path alone on the first line, the proxy's own
    headers left out, Connection: close."""
    u = urlsplit(target)
    if (u.scheme or "").lower() != "http" or not u.hostname:
        raise Refused("only http:// addresses and CONNECT")
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    out = [f"{method} {path} {version}".encode("latin-1")]
    have_host = False
    for raw in fields.split(b"\r\n"):
        name = raw.split(b":", 1)[0].strip().lower()
        if not raw or name in _HOP:
            continue
        have_host = have_host or name == b"host"
        out.append(raw)
    if not have_host:
        out.append(b"Host: " + (u.netloc.rsplit("@", 1)[-1]).encode("latin-1"))
    out.append(b"Connection: close")
    return u.hostname.lower(), u.port or 80, b"\r\n".join(out) + b"\r\n\r\n"


def pipe(a: socket.socket, b: socket.socket) -> None:
    """Bytes both ways until both have ended (one side ending its sending
    is passed on to the other, as TCP does)."""
    sel = selectors.DefaultSelector()
    sel.register(a, selectors.EVENT_READ, b)
    sel.register(b, selectors.EVENT_READ, a)
    open_ = 2
    try:
        while open_:
            for key, _ev in sel.select():
                src: socket.socket = key.fileobj    # type: ignore[assignment]
                dst: socket.socket = key.data
                try:
                    data = src.recv(65536)
                except OSError:
                    return
                if data:
                    try:
                        dst.sendall(data)
                    except OSError:
                        return
                    continue
                sel.unregister(src)
                open_ -= 1
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
    finally:
        sel.close()


class Bridge:
    """One route's proxy on 127.0.0.1. *cafile*: the authorities another
    host than the pinned one is checked against, in place of the system's."""

    def __init__(self, route: Route, cafile: str | None = None) -> None:
        self.route = route
        self.cafile = cafile
        self._trust_ctx: ssl.SSLContext | None = None
        self._trust_ok: dict[tuple[str, int], float] = {}
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(64)
        self.port = int(self._sock.getsockname()[1])
        #: Where it listens, as a log line may say it.
        self.where = f"127.0.0.1:{self.port}"
        #: This bridge's key, made now and never kept anywhere: whoever asks
        #: without it is turned away (the module's docstring).
        self.secret = secrets.token_urlsafe(24)
        self._login = base64.b64encode(f"{USER}:{self.secret}".encode("ascii"))
        #: What the player and the recorder are handed: the key is the
        #: proxy's login, which mpv and ffmpeg take from its address.
        self.address = f"http://{USER}:{self.secret}@{self.where}"
        self.served = 0
        #: Requests turned away for want of the key (a player's first try
        #: at each connection among them).
        self.unkeyed = 0
        self.failures: list[str] = []        # the last few, for the log and the tests
        self._pins_ok: dict[tuple[str, int], float] = {}
        self._lock = threading.Lock()
        self._closed = False
        self._slots = threading.BoundedSemaphore(MAX_SERVING)
        #: Connections closed because MAX_SERVING were being served.
        self.turned_away = 0
        threading.Thread(target=self._accept, name="net-bridge", daemon=True).start()

    def close(self) -> None:
        self._closed = True
        try:
            self._sock.close()
        except OSError:
            pass

    def _accept(self) -> None:
        while not self._closed:
            try:
                conn, peer = self._sock.accept()
            except OSError:
                if self._closed:
                    return
                time.sleep(0.05)
                continue
            if peer[0] != "127.0.0.1" or not self._slots.acquire(blocking=False):
                if peer[0] == "127.0.0.1":
                    with self._lock:
                        self.turned_away += 1
                conn.close()
                continue
            threading.Thread(target=self._serve_slot, args=(conn,), name="net-bridge-conn",
                             daemon=True).start()

    def _serve_slot(self, client: socket.socket) -> None:
        try:
            self._serve(client)
        finally:
            self._slots.release()

    def _failed(self, what: str) -> None:
        log.info("net bridge: %s", what)
        with self._lock:
            self.failures = [*self.failures[-9:], what]

    def _check_pin(self, host: str, port: int) -> None:
        pinned = self.route.pin(host, port)
        if pinned is None:
            if self.route.pins:
                self._check_trust(host, port)
            return
        now = time.monotonic()
        with self._lock:
            if self._pins_ok.get((host, port), 0.0) > now:
                return
        got = cert_pin.fetch(host, port, CONNECT_TIMEOUT, self.route).sha256
        if got != pinned:
            cert_pin.record_mismatch(host, port, got)
            raise Refused(str(cert_pin.PinMismatch(host, port, pinned, got)))
        cert_pin.clear_mismatch(host, port)
        with self._lock:
            self._pins_ok[(host, port)] = now + PIN_RECHECK

    def _check_trust(self, host: str, port: int) -> None:
        """Another host than the pinned one, on a route with a pin: the
        player checks no certificate in a stream to the pinned host, so this
        host's is checked here (the module's docstring)."""
        now = time.monotonic()
        with self._lock:
            if self._trust_ok.get((host, port), 0.0) > now:
                return
            if self._trust_ctx is None:
                self._trust_ctx = cert_pin.trust_context(self.cafile)
            ctx = self._trust_ctx
        trusted, why = cert_pin.system_trust(host, port, CONNECT_TIMEOUT, self.route, context=ctx)
        if not trusted:
            raise Refused(f"its certificate is not trusted ({why})")
        with self._lock:
            self._trust_ok[(host, port)] = now + TRUST_RECHECK

    def _serve(self, client: socket.socket) -> None:
        up: socket.socket | None = None
        where = "?"
        try:
            got = _read_head(client, HEAD_TIMEOUT)
            if got is None:
                return
            client.settimeout(20.0)
            head, rest = got
            line, _sep, fields = head.partition(b"\r\n")
            parts = line.decode("latin-1").split(" ")
            if len(parts) != 3:
                raise Refused("not a request")
            method, target, version = parts
            if not hmac.compare_digest(proxy_login(fields), self._login):
                # Not the app's own player or recorder - or their first try,
                # before the proxy has asked: asked for the key, and nothing
                # looked up or connected for it.
                with self._lock:
                    self.unkeyed += 1
                try:
                    client.sendall(_CHALLENGE)
                except OSError:
                    pass
                return
            if method.upper() == "CONNECT":
                host, port = split_target(target, 443)
                where = f"{host}:{port}"
                self._check_pin(host, port)
                up = net_route.open_connection(self.route, host, port, CONNECT_TIMEOUT)
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            else:
                host, port, request = origin_head(method, target, version, fields)
                where = f"{host}:{port}"
                up = net_route.open_connection(self.route, host, port, CONNECT_TIMEOUT)
                up.sendall(request)
            if rest:
                up.sendall(rest)
            self.served += 1
            client.settimeout(None)
            up.settimeout(None)
            pipe(client, up)
        except Refused as e:
            self._failed(f"{where}: {e}")
            _reply(client, 502 if where != "?" else 400, str(e))
        except doh.DohError as e:
            self._failed(f"{where}: {e}")
            _reply(client, 502, str(e))
        except (socket.timeout, TimeoutError) as e:
            self._failed(f"{where}: timed out ({e})")
            _reply(client, 504, f"{where}: timed out")
        except OSError as e:
            self._failed(f"{where}: {type(e).__name__}: {e}")
            _reply(client, 502, f"{where}: {e}")
        finally:
            for s in (up, client):
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass


_bridges: dict[Route, Bridge] = {}
_lock = threading.Lock()
MAX_BRIDGES = 8


def bridge_for(route: Route) -> Bridge:
    with _lock:
        b = _bridges.get(route)
        if b is None:
            if len(_bridges) >= MAX_BRIDGES:
                # The oldest stops taking connections (those under way go
                # on); eight routes at once is more than any setup has.
                _bridges.pop(next(iter(_bridges))).close()
            b = _bridges[route] = Bridge(route)
            log.info("net bridge for the player on %s (%s)", b.where, describe(route))
        return b


def describe(route: Route) -> str:
    parts = []
    if route.servers:
        parts.append(f"own DNS: {route.dns_label}" + (" (else the system's)" if route.fallback else ""))
    if route.family:
        parts.append(f"IP: {route.family}")
    if route.pins:
        parts.append("pinned certificate")
    return ", ".join(parts) or "-"


def checks_pin(source: dict | None, url: str, n: object = None) -> bool:
    """Does the bridge check a certificate *source* pins for the stream at
    *url* (https, to the pinned host and port)? Then that check is the trust
    for it, and the player and the recorder are told not to check the chain
    themselves (see the module's docstring)."""
    route = net_route.route_for(source, n)
    where = net_route.url_host(url)
    return (route.active and where is not None and where[0] == "https"
            and route.pin(where[1], where[2]) is not None)


def recorder_options(kind: str, address: str, pinned: bool) -> list[str]:
    """What a recorder (ffmpeg or mpv) is told to go through the bridge at
    *address*; *pinned*: it checks the stream's pinned certificate."""
    if not address:
        return []
    if kind == "ffmpeg":
        return ["-http_proxy", address, *(["-tls_verify", "0"] if pinned else [])]
    return [f"--http-proxy={address}", *(["--tls-verify=no"] if pinned else [])]


def address_for(source: dict | None, url: str, n: object = None) -> str:
    """The bridge's address for a stream of *source* at *url*, or "" when
    its route changes nothing there (the player connects as it always did)."""
    route = net_route.route_for(source, n)
    where = net_route.url_host(url)
    if not route.active or where is None:
        return ""
    scheme, host, port = where
    if not route.bridges(scheme, host, port):
        return ""
    try:
        return bridge_for(route).address
    except OSError as e:
        log.warning("net bridge: not started (%s) - the player connects itself", e)
        return ""
