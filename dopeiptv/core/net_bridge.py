"""The one way out for the programs the app starts on a provider's stream:
a small HTTP proxy on this computer that ffmpeg and mpv are pointed at - a
recorder in its environment (``child_env``: http_proxy, which only this user
can read), the in-app player in its http-proxy option. Three things it does.

**Where a connection may land** (``Policy``). FFmpeg and mpv fetch an HLS
playlist's segments, its keys and the redirects they meet themselves, so a
playlist the provider wrote could send them to this computer's own services,
a link-local host (a cloud's metadata service, the router's own address) or
a device at home. Every connection the bridge opens - a CONNECT tunnel, a
plain http request, each hop of a redirect the client follows, a pin's or a
certificate's check - is judged by its host as written first (no lookup),
then by where the connected socket landed, before a byte is sent: a name
whose answer changes between the two (DNS rebinding) is caught there. By
where the stream's source is (core/stream_address.py):

- a source on the internet: never this computer, never link-local or a
  metadata service, never the home network - except the stream's own server
  (``Policy.entry``: the host and port of the address core/stream_address.py
  let play - AceStream's engine on 127.0.0.1:6878, udpxy at home, a home
  device the source's ``home_addresses`` lets in, a public name the home's
  own DNS answers with a home address), the names the person gave
  (dest_guard.note_own) and Plex's ``.plex.direct``; with the source's
  ``home_addresses`` on, the home network too;
- a source of the home network, or a playlist file: core/dest_guard.py's
  rule - this computer only by a name that says so, link-local only by a
  link-local or home name, a metadata service only by its address, a name a
  source on the internet named for its pictures never at home.

A refusal is a 502 to the client, said in the log once per host and reason.

**An address that names nothing of the real one** (``Bridge.alias``). Every
account on a Linux computer reads a program's arguments (/proc/<pid>/cmdline),
and an Xtream address there is its login. A recorder that must read an
address itself - an HLS playlist, segment by segment - is handed an alias,
http://<192-bit token>.stream.invalid/~/~/~/~.m3u8: a host that resolves
nowhere, so it works only through the bridge. Its path has the real one's
shape - each folder a "~", the last part "~" with its extension - and a
request to it is mapped back by position (``real_target``): a "~" at place i
is the real folder i, the alias's own last part the real last part, the rest
as it came. So a playlist's relative addresses ("seg_1.ts", "../key.bin",
"/hls/x.ts") resolve exactly as they would against the real address, and no
playlist is rewritten. The real query goes only with the alias's own path
(a resolved relative address carries its own). The bridge sends the
source's login from the address (basic at once, digest when the server asks
for it, as core/http_login.py), and for an https address makes the TLS
connection itself, with the stream's own trust: checked as the player
checks it (core/nerd.checks_certificate - certifi's authorities, the name),
by the pin where the route pins that host, and otherwise - a server at
home, a source the person let play unchecked - not checked, exactly the
trust the recorder had when it read the address itself. Redirects go back
to the client as they came, and it follows them through the bridge, where
they are judged as any connection.

**A source's route** (Nerd, core/net_route.py): its own DNS, its IP version
and a pinned certificate. Through a proxy, ffmpeg asks for an https stream
with CONNECT host:port and makes the TLS connection itself, through the
tunnel: the server name it sends (SNI), its Host header and its certificate
check stay the stream's own, and only the lookup and the TCP connection are
made here, as the route says. That is why this is a proxy and not the
address with the IP put in: ffmpeg's TLS takes the server name from the
address, so with an IP there it sends none, and a server that picks its
certificate (or its site) by name - most behind a CDN - refuses. A plain
http stream comes as an absolute address (GET http://...); it goes on in
the form a server expects, with Connection: close, so that each request is
looked up for its own host (an HLS playlist's segments are often on another,
a panel redirects to its load balancer). Without a route the bridge connects
as the system does - it is there for the guard.

**Through the person's own proxy** (Nerd's, ``upstream``): every connection
goes through it - a tunnel (CONNECT) for https and for what the client
tunnels, the absolute address for plain http, the proxy's login sent with
each - and there the proxy connects: what is judged is the address as
written (dest_guard's rule for a proxied connection too). An http proxy
only; another (socks) is none the bridge can take.

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

Only this computer connects (127.0.0.1), each route and policy has a bridge
of its own on a port of its own, and what is not a proxy's request (a web
page's GET /...) is refused.

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
from collections import OrderedDict
from dataclasses import dataclass, field
from urllib.parse import quote, unquote, urlsplit

from . import cert_pin, dest_guard, doh, net_route, stream_address
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
#: How long the server an alias stands for may take to answer a request's
#: head (a login's challenge is read there).
ANSWER_TIMEOUT = 20.0
_HOP = (b"proxy-connection", b"proxy-authorization", b"connection", b"keep-alive")
#: The name in the bridge's login; its key is the password (Bridge.secret).
USER = "dopeiptv"
_CHALLENGE = (b"HTTP/1.1 407 Proxy Authentication Required\r\n"
              b'Proxy-Authenticate: Basic realm="dopeIPTV"\r\n'
              b"Content-Length: 0\r\nConnection: close\r\n\r\n")

#: An alias's host ends so: .invalid resolves nowhere (RFC 6761), so the
#: address works through the bridge alone.
ALIAS_HOST = ".stream.invalid"
#: Aliases a bridge keeps; past them the oldest is forgotten.
MAX_ALIASES = 256
#: How an alias's https server is trusted: as the player checks it, by the
#: route's pin, or - where the player and recorder would not check either -
#: not checked.
CHECKED, PINNED, UNCHECKED = "checked", "pinned", "unchecked"
#: How an alias's path stands for a folder of the real one.
_PLACE = "~"
_PLACES = ("~", "%7E", "%7e")
#: Each refusal of where a connection would land is said once per host and
#: reason; at most this many kept.
_SAID_MAX = 256


class Refused(Exception):
    pass


class Guarded(Refused):
    """A connection the policy does not let land where it would: *why* is
    dest_guard's word for the place (this computer, a link-local host, the
    home network)."""

    def __init__(self, host: str, port: int, why: str, internet: bool) -> None:
        self.host, self.port, self.why, self.internet = host, port, why, internet
        whose = "a source on the internet's stream" if internet else "a stream"
        super().__init__(f"{whose} may not reach {why} ({_shown(host)}:{port})")


def _shown(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def _name(host: str) -> str:
    return str(host or "").strip().strip("[]").rstrip(".").lower()


def place_of(address: str) -> str:
    """Where a connected peer is: dest_guard.ip_kind's THIS_COMPUTER,
    LINK_LOCAL, HOME or "" for the internet. (The tests stand their loopback
    servers in for the internet or the home network here - the policy itself
    is the same.)"""
    return dest_guard.ip_kind(address)


def _metadata(address: str) -> bool:
    return stream_address._ip(address) in dest_guard._METADATA


def _given(host: str) -> bool:
    """A name the person gave themselves (a source's own server, its backup
    addresses: dest_guard.note_own) or Plex's for a server at home."""
    name = _name(host)
    if not name:
        return False
    if name.endswith(dest_guard._HOME_BY_DESIGN):
        return True
    with dest_guard._NAMES_LOCK:
        return name in dest_guard._OWN


@dataclass(frozen=True)
class Policy:
    """Where a bridge's connections may land (the module's docstring):
    *internet* - the stream's source is on the internet; *home_ok* - its
    ``home_addresses`` lets it into the home network; *entry* - the host
    (lower case, no brackets) and port of the stream itself, which the
    address's own judgement let play."""

    internet: bool = False
    home_ok: bool = False
    entry: tuple[str, int] | None = None

    def is_entry(self, host: str, port: int) -> bool:
        return self.entry is not None and self.entry == (_name(host), int(port))

    def written(self, host: str, port: int) -> str:
        """Why *host*:*port* is not to be connected to, by the host as
        written - "" when it may be tried."""
        if not self.internet or self.is_entry(host, port):
            return ""
        said = dest_guard.named_kind(host)
        if said in (dest_guard.THIS_COMPUTER, dest_guard.LINK_LOCAL):
            return said
        if said == dest_guard.HOME and not self.home_ok and not _given(host):
            return said
        return ""

    def landed(self, host: str, port: int, peer: str) -> str:
        """Why a connection to *host*:*port* that reached *peer* may not be
        used - "" when it may (dest_guard.peer_refusal's rule, and for a
        source on the internet no home device by a public name)."""
        kind = place_of(peer)
        said = dest_guard.named_kind(host)
        entry = self.is_entry(host, port)
        if kind == dest_guard.THIS_COMPUTER and said != dest_guard.THIS_COMPUTER:
            return kind
        if kind == dest_guard.LINK_LOCAL and said not in (dest_guard.LINK_LOCAL, dest_guard.HOME):
            return kind
        if kind == dest_guard.LINK_LOCAL and _metadata(peer) and said != dest_guard.LINK_LOCAL:
            return kind             # a cloud's metadata service, never by a home name
        if kind == dest_guard.HOME and said == "" and not entry:
            if dest_guard._outside_name(host):
                return kind
            if self.internet and not self.home_ok and not _given(host):
                return kind
        return ""

    def describe(self) -> str:
        whose = "a source on the internet" if self.internet else "the home network or a file"
        more = ", its home addresses let in" if self.home_ok else ""
        entry = f", the stream on {_shown(self.entry[0])}:{self.entry[1]}" if self.entry else ""
        return whose + more + entry


HOME_POLICY = Policy()


def entry_of(url: str) -> tuple[str, int] | None:
    """(host, port) of an http(s) address as written - the stream's own
    server for its policy."""
    where = net_route.url_host(url)
    if where is None:
        return None
    return _name(where[1]), where[2]


def _source_on_internet(kind: str, server: str, options: dict) -> bool:
    """providers/client._on_internet: a source is the home network's only
    when every address it is read from is - its own and its backup ones."""
    from . import mirrors
    found = [server]
    if kind in mirrors.KINDS and options.get(mirrors.KEY):
        try:
            found += mirrors.parse(options.get(mirrors.KEY), kind)
        except Exception:
            return True
    return any(stream_address.on_internet(a) for a in found)


def policy_for(source: dict | None, url: str) -> Policy:
    """The policy for a stream of *source* (its options, kind and - in
    make_client's form - its server, ``_server``) at *url*. A source that
    does not say where its server is (nerd.source()'s form) is judged by
    where the stream is."""
    src = source if isinstance(source, dict) else {}
    kind = str(src.get("_kind") or "")
    server = src.get("_server")
    if isinstance(server, str) and server.strip():
        remote = kind != "m3u" or server.strip().lower().startswith(("http://", "https://"))
        internet = remote and _source_on_internet(kind, server, src)
    else:
        internet = stream_address.on_internet(url)
    return Policy(internet=internet, home_ok=stream_address.home_allowed(src),
                  entry=entry_of(url))


# -- an alias's address ------------------------------------------------------------

def _without_dots(path: str) -> str:
    """*path* with its "." and ".." parts taken out, as a client resolving
    a relative address does (RFC 3986, 5.2.4)."""
    out: list[str] = []
    parts = path.split("/")
    for i, part in enumerate(parts):
        if part == ".":
            if i == len(parts) - 1:
                out.append("")
            continue
        if part == "..":
            if len(out) > 1:
                out.pop()
            if i == len(parts) - 1:
                out.append("")
            continue
        out.append(part)
    text = "/".join(out)
    return text if text.startswith("/") else "/" + text


def _neutral(last: str) -> str:
    """An alias's last part for the real one *last*: "~" and its extension
    (a short one of letters and digits), which the recorder may tell the
    format by - nothing else of it."""
    stem, dot, ext = last.rpartition(".")
    if dot and stem and 1 <= len(ext) <= 5 and ext.isascii() and ext.isalnum():
        return f"{_PLACE}.{ext.lower()}"
    return _PLACE


@dataclass(frozen=True)
class Alias:
    """What an alias stands for (``Bridge.alias``) - shown without what may
    hold a login (its repr, in a traceback or a test's words)."""

    token: str = field(repr=False)
    scheme: str
    host: str               # lower case, no brackets
    port: int
    netloc: str             # the Host header's: host and a port it named
    dirs: tuple[str, ...] = field(repr=False)   # the real path's folders, as written
    last: str = field(repr=False)               # its last part
    neutral: str = ""                           # the alias's last part
    query: str = field(default="", repr=False)
    user: str | None = field(default=None, repr=False)
    password: str = field(default="", repr=False)
    trust: str = CHECKED
    pin: str | None = None

    @property
    def path(self) -> str:
        return "/" + "/".join([_PLACE] * len(self.dirs) + [self.neutral])

    @property
    def address(self) -> str:
        return f"http://{self.token}{ALIAS_HOST}{self.path}"


#: What stays as it is when a real address's path or query is written for a
#: request line: printable ASCII; the rest (a non-ASCII name, a space) as
#: UTF-8 in %XX, as a browser sends it.
_KEPT = "".join(chr(c) for c in range(33, 127))


def _ascii(text: str) -> str:
    return quote(text, safe=_KEPT)


def make_alias(url: str, token: str, trust: str = CHECKED, pin: str | None = None) -> Alias:
    """The alias *token* stands for at *url* (http or https, a host)."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    port = parts.port
    if scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("an alias is for an http(s) address")
    host = _name(parts.hostname)
    shown = host.encode("idna").decode("ascii") if not host.isascii() else host
    shown = f"[{shown}]" if ":" in shown else shown
    netloc = shown + (f":{port}" if port else "")
    path = _ascii(parts.path or "/")
    segs = (path[1:] if path.startswith("/") else path).split("/")
    user = unquote(parts.username) if parts.username is not None else None
    return Alias(token=token, scheme=scheme, host=host,
                 port=port or (443 if scheme == "https" else 80), netloc=netloc,
                 dirs=tuple(segs[:-1]), last=segs[-1], neutral=_neutral(segs[-1]),
                 query=_ascii(parts.query), user=user, password=unquote(parts.password or ""),
                 trust=trust, pin=pin)


def real_target(alias: Alias, path: str, query: str = "") -> str:
    """The request's target on the real server ("/live/u/p/seg.ts?x") for a
    request to *alias* at *path* and *query*: mapped by position (the
    module's docstring), the real query only on the alias's own path."""
    path = _without_dots(path or "/")
    own = path == alias.path and not query
    parts = path[1:].split("/")
    n, last = len(alias.dirs), len(parts) - 1
    out: list[str] = []
    for i, seg in enumerate(parts):
        if i < n and seg in _PLACES:
            out.append(alias.dirs[i])
        elif i == n == last and seg == alias.neutral:
            out.append(alias.last)
        else:
            out.append(seg)
    q = alias.query if own else query
    return "/" + "/".join(out) + (f"?{q}" if q else "")


def _basic(user: str, password: str) -> bytes:
    word = f"{user}:{password}"
    try:
        raw = word.encode("latin-1")
    except UnicodeEncodeError:
        raw = word.encode("utf-8")
    return b"Basic " + base64.b64encode(raw)


def alias_head(method: str, target: str, version: str, fields: bytes, alias: Alias,
               authorization: bytes | None = None) -> bytes:
    """The request to the real server for a client's request to *alias*:
    *target* on its first line, the proxy's own headers and the Host left
    out, the real Host, the source's login (*authorization*, else basic
    from the address), Connection: close."""
    out = [f"{method} {target} {version}".encode("latin-1")]
    login = authorization
    if login is None and alias.user is not None:
        login = _basic(alias.user, alias.password)
    for raw in fields.split(b"\r\n"):
        name = raw.split(b":", 1)[0].strip().lower()
        if (not raw or name in _HOP or name == b"host"
                or (login is not None and name == b"authorization")):
            continue
        out.append(raw)
    out.append(b"Host: " + alias.netloc.encode("ascii"))
    if login is not None:
        out.append(b"Authorization: " + login)
    out.append(b"Connection: close")
    return b"\r\n".join(out) + b"\r\n\r\n"


def _digest_challenge(head: bytes) -> str:
    """A 401's Digest challenge (the words after "Digest"), "" without one."""
    line, _sep, fields = head.partition(b"\r\n")
    status = line.split(b" ", 2)
    if len(status) < 2 or status[1] != b"401":
        return ""
    for raw in fields.split(b"\r\n"):
        name, sep, value = raw.partition(b":")
        if sep and name.strip().lower() == b"www-authenticate":
            text = value.decode("latin-1").strip()
            if text[:7].lower() == "digest ":
                return text[7:]
    return ""


def _digest(alias: Alias, challenge: str, method: str, target: str) -> bytes | None:
    """The Authorization for a Digest *challenge*, as requests answers it
    (core/http_login.py's way) - None when it cannot be answered."""
    try:
        from requests.auth import HTTPDigestAuth
        from requests.utils import parse_dict_header
        auth = HTTPDigestAuth(alias.user or "", alias.password)
        auth.init_per_thread_state()
        auth._thread_local.chal = parse_dict_header(challenge)
        made = auth.build_digest_header(method, f"{alias.scheme}://{alias.netloc}{target}")
    except Exception as e:
        log.debug("net bridge: a digest login not answered: %s", e)
        return None
    return made.encode("latin-1") if made else None


# -- moving bytes -------------------------------------------------------------------

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
    reason = {400: "Bad Request", 404: "Not Found", 502: "Bad Gateway",
              504: "Gateway Timeout"}.get(code, "Error")
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


def _drained(sock: socket.socket) -> bytes:
    data = sock.recv(65536)
    # A TLS socket may hold more than it said it had: what is decrypted
    # already is read now, or the selector would wait on it for ever.
    while data and isinstance(sock, ssl.SSLSocket) and sock.pending():
        data += sock.recv(65536)
    return data


def pipe(a: socket.socket, b: socket.socket) -> None:
    """Bytes both ways until both have ended (one side ending its sending
    is passed on to the other, as TCP does - but not to a TLS connection,
    whose shutdown would end its reading too)."""
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
                    data = _drained(src)
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
                if isinstance(dst, ssl.SSLSocket):
                    continue
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
    finally:
        sel.close()


def _unchecked_context() -> ssl.SSLContext:
    """A TLS client that checks no certificate. Two uses, each with its
    reason: a certificate read before it is compared with the pin (the pin
    is the trust, as core/cert_pin.fetch), and an alias's server whose
    certificate neither the player nor the recorder would check (a server
    at home without Nerd's strict check, a source the person let play
    unchecked) - exactly the trust the recorder had when it read the address
    itself; never for one the player checks (Alias.trust)."""
    ctx = cert_pin.at_least_tls12(ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _is_ip(host: str) -> bool:
    return net_route.is_ip(host)


@dataclass(frozen=True)
class Upstream:
    """An http proxy a bridge's connections go through (``upstream_of``)."""

    host: str
    port: int
    #: Its Proxy-Authorization, from the address's login (never shown).
    login: bytes | None = field(default=None, repr=False)


def upstream_of(proxy: str) -> Upstream | None:
    """The http proxy at *proxy* (http://[user:password@]host[:port], Nerd's
    as nerd.proxy gives it) as a bridge goes through it; None for "".
    ValueError for one a bridge cannot go through (socks, https, no host)."""
    text = str(proxy or "").strip()
    if not text:
        return None
    parts = urlsplit(text if "://" in text else "http://" + text)
    if parts.scheme.lower() != "http" or not parts.hostname:
        raise ValueError("only an http:// proxy")
    login = (_basic(unquote(parts.username), unquote(parts.password or ""))
             if parts.username is not None else None)
    return Upstream(_name(parts.hostname), parts.port or 8080, login)


def _via(head: bytes, absolute: str, login: bytes | None) -> bytes:
    """A request's *head* as a proxy takes it: *absolute* on its first line,
    the proxy's login beside."""
    line, _sep, rest = head.partition(b"\r\n")
    method, _target, version = line.split(b" ", 2)
    first = method + b" " + absolute.encode("latin-1") + b" " + version + b"\r\n"
    return first + (b"Proxy-Authorization: " + login + b"\r\n" if login else b"") + rest


class Bridge:
    """One route's and policy's proxy on 127.0.0.1. *cafile*: the
    authorities another host than the pinned one, and an alias's server the
    player would check, are checked against, in place of the system's and
    certifi's. *policy*: where its connections may land (HOME_POLICY - the
    least any bridge checks - when none is given). *upstream*: the person's
    http proxy every connection goes through (the module's docstring), ""
    for none; ValueError for one it cannot go through."""

    def __init__(self, route: Route, cafile: str | None = None,
                 policy: Policy | None = None, upstream: str = "") -> None:
        self.route = route
        self.cafile = cafile
        self.policy = policy if policy is not None else HOME_POLICY
        self._up = upstream_of(upstream)
        self._trust_ctx: ssl.SSLContext | None = None
        self._checked_ctx: ssl.SSLContext | None = None
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
        #: Connections not made for where they would land (Policy).
        self.guarded = 0
        self.failures: list[str] = []        # the last few, for the log and the tests
        self._pins_ok: dict[tuple[str, int], float] = {}
        self._aliases: OrderedDict[str, Alias] = OrderedDict()
        self._said: set[tuple[str, str]] = set()
        self._lock = threading.Lock()
        self._closed = False
        self._slots = threading.BoundedSemaphore(MAX_SERVING)
        #: Connections closed because MAX_SERVING were being served.
        self.turned_away = 0
        threading.Thread(target=self._accept, name="net-bridge", daemon=True).start()

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        self._closed = True
        try:
            self._sock.close()
        except OSError:
            pass

    # -- aliases ------------------------------------------------------------------

    def alias(self, url: str, source: dict | None = None) -> str:
        """An address for a program the app starts that stands for *url*
        (http or https) through this bridge alone and names nothing of it:
        neither its host nor its folders, login or query (the module's
        docstring). *source*: whose stream it is, for its certificate's
        check (core/nerd.checks_certificate). ValueError for an address
        that is no http(s) one. Forget it once the program is done."""
        from . import nerd
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("an alias is for an http(s) address")
        port = parts.port or (443 if scheme == "https" else 80)
        pin = self.route.pin(parts.hostname, port) if scheme == "https" else None
        trust = (PINNED if pin else CHECKED if scheme == "https"
                 and nerd.checks_certificate(source, url) else UNCHECKED)
        made = make_alias(url, secrets.token_hex(24), trust, pin)
        with self._lock:
            self._aliases[made.token] = made
            while len(self._aliases) > MAX_ALIASES:
                self._aliases.popitem(last=False)
        return made.address

    def forget(self, alias: str) -> None:
        """The alias *alias* (an address ``alias`` gave) answers no more."""
        try:
            host = (urlsplit(str(alias or "")).hostname or "").lower()
        except ValueError:
            return
        if host.endswith(ALIAS_HOST):
            with self._lock:
                self._aliases.pop(host[:-len(ALIAS_HOST)], None)

    @property
    def aliases(self) -> int:
        with self._lock:
            return len(self._aliases)

    def _alias_of(self, token: str) -> Alias | None:
        if not token.isascii():
            return None
        with self._lock:
            kept = list(self._aliases.items())
        found = None
        for key, value in kept:
            if hmac.compare_digest(key, token):
                found = value
        return found

    # -- serving ------------------------------------------------------------------

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

    def _refused(self, e: Guarded) -> None:
        """A connection the policy refused: said once per host and reason
        (an HLS player asks again for every segment)."""
        key = (_name(e.host), e.why)
        with self._lock:
            self.guarded += 1
            self.failures = [*self.failures[-9:], str(e)]
            first = key not in self._said and len(self._said) < _SAID_MAX
            if first:
                self._said.add(key)
        if first:
            hint = ("; if it is your own list, let it in: the source's settings > Advanced > "
                    "Addresses on my home network" if e.internet and e.why == dest_guard.HOME else "")
            log.warning("net bridge: %s - not connected%s", e, hint)

    def _open(self, host: str, port: int) -> socket.socket:
        """A connection to *host*:*port* on this bridge's route, where its
        policy lets it land: judged as written, then by the socket's peer
        before a byte is sent (Guarded otherwise) - or, through the person's
        proxy, a tunnel it opens there."""
        why = self.policy.written(host, port)
        if why:
            raise Guarded(host, port, why, self.policy.internet)
        if self._up is not None:
            return self._tunnel(host, port)
        sock = net_route.open_connection(self.route, host, port, CONNECT_TIMEOUT)
        try:
            peer = str(sock.getpeername()[0])
            why = self.policy.landed(host, port, peer)
        except OSError as e:
            sock.close()
            raise Refused(f"no peer to judge ({e})") from e
        if why:
            sock.close()
            raise Guarded(host, port, why, self.policy.internet)
        return sock

    def _open_plain(self, host: str, port: int) -> tuple[socket.socket, bool]:
        """For a plain http request to *host*:*port*: (the connection, whether
        it is to the person's proxy - the request then goes there in its
        absolute form, ``_via``)."""
        if self._up is None:
            return self._open(host, port), False
        why = self.policy.written(host, port)
        if why:
            raise Guarded(host, port, why, self.policy.internet)
        return socket.create_connection((self._up.host, self._up.port), CONNECT_TIMEOUT), True

    def _tunnel(self, host: str, port: int) -> socket.socket:
        """A tunnel to *host*:*port* through the person's proxy."""
        up = self._up
        if up is None:
            raise Refused("no proxy to go through")
        sock = socket.create_connection((up.host, up.port), CONNECT_TIMEOUT)
        name = host if host.isascii() else host.encode("idna").decode("ascii")
        target = f"{_shown(name)}:{port}"
        try:
            sock.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n".encode("ascii")
                         + (b"Proxy-Authorization: " + up.login + b"\r\n" if up.login else b"")
                         + b"\r\n")
            got = _read_head(sock, CONNECT_TIMEOUT)
            status = got[0].split(b" ", 2)[1:2] if got is not None else []
            if got is None or not status or not status[0].startswith(b"2") or got[1]:
                raise Refused(f"the proxy opened no tunnel to {target} "
                              f"({status[0].decode('latin-1') if status else 'no answer'})")
        except BaseException:
            sock.close()
            raise
        return sock

    def _peer_certificate(self, host: str, port: int) -> bytes:
        """The certificate *host*:*port* shows, read unchecked (the pin is
        what judges it) over a guarded connection."""
        sock = self._open(host, port)
        try:
            with _unchecked_context().wrap_socket(
                    sock, server_hostname=None if _is_ip(host) else host) as tls:
                der = tls.getpeercert(binary_form=True)
        finally:
            sock.close()
        if not der:
            raise ssl.SSLError("the server showed no certificate")
        return der

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
        got = cert_pin.fingerprint(self._peer_certificate(host, port))
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
        sock = self._open(host, port)
        try:
            with ctx.wrap_socket(sock, server_hostname=host):
                pass
        except ssl.SSLCertVerificationError as e:
            raise Refused(f"its certificate is not trusted ({e.verify_message or e})") from e
        finally:
            sock.close()
        with self._lock:
            self._trust_ok[(host, port)] = now + TRUST_RECHECK

    def _checked(self) -> ssl.SSLContext:
        """What an alias's server is checked against where the player would
        check it: the authorities the player is given (certifi's,
        nerd.ca_file), or *cafile*."""
        with self._lock:
            if self._checked_ctx is None:
                from . import nerd
                self._checked_ctx = cert_pin.trust_context(self.cafile or nerd.ca_file() or None)
            return self._checked_ctx

    def _upstream(self, alias: Alias) -> tuple[socket.socket, bool]:
        """A connection to the server *alias* stands for - TLS made here for
        an https one, with its own trust (Alias.trust) - and whether it is to
        the person's proxy for a plain http request (``_open_plain``)."""
        if alias.scheme != "https":
            return self._open_plain(alias.host, alias.port)
        sock = self._open(alias.host, alias.port)
        name = None if _is_ip(alias.host) else alias.host
        try:
            if alias.trust == CHECKED:
                tls = self._checked().wrap_socket(sock, server_hostname=alias.host)
            else:
                tls = _unchecked_context().wrap_socket(sock, server_hostname=name)
        except BaseException:
            sock.close()
            raise
        if alias.trust == PINNED:
            got = cert_pin.fingerprint(tls.getpeercert(binary_form=True) or b"")
            if got != alias.pin:
                tls.close()
                cert_pin.record_mismatch(alias.host, alias.port, got)
                raise Refused(str(cert_pin.PinMismatch(alias.host, alias.port,
                                                       alias.pin or "", got)))
            cert_pin.clear_mismatch(alias.host, alias.port)
        return tls, False

    def _alias_request(self, alias: Alias, proxied: bool, method: str, real: str,
                       version: str, fields: bytes, login: bytes | None = None) -> bytes:
        head = alias_head(method, real, version, fields, alias, login)
        if not proxied:
            return head
        return _via(head, f"http://{alias.netloc}{real}", self._up.login if self._up else None)

    def _serve_alias(self, client: socket.socket, method: str, target: str, version: str,
                     fields: bytes, rest: bytes) -> socket.socket | None:
        """A request to an alias: sent to the server it stands for, its
        login answered; the connection to pipe, its answer's head (when it
        was read here) already handed on. None when there is no such alias
        (404 said)."""
        u = urlsplit(target)
        host = (u.hostname or "").lower()
        alias = self._alias_of(host[:-len(ALIAS_HOST)])
        if alias is None:
            _reply(client, 404, "no such stream")
            return None
        real = real_target(alias, u.path, u.query)
        up, proxied = self._upstream(alias)
        try:
            up.sendall(self._alias_request(alias, proxied, method, real, version, fields) + rest)
            if alias.user is None or method.upper() not in ("GET", "HEAD"):
                return up
            # The login: basic went with the request; a 401 that asks for
            # digest is answered once, on a new connection.
            up.settimeout(ANSWER_TIMEOUT)
            got = _read_head(up, ANSWER_TIMEOUT)
            if got is None:
                raise Refused("the server sent no answer")
            head, more = got
            challenge = _digest_challenge(head)
            login = _digest(alias, challenge, method, real) if challenge else None
            if login is None:
                client.sendall(head + b"\r\n\r\n" + more)
                return up
        except BaseException:
            up.close()
            raise
        up.close()
        up, proxied = self._upstream(alias)
        try:
            up.sendall(self._alias_request(alias, proxied, method, real, version, fields, login))
        except BaseException:
            up.close()
            raise
        return up

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
                where = f"{_shown(host)}:{port}"
                if host.endswith(ALIAS_HOST):
                    raise Refused("an alias is asked for over plain http")
                self._check_pin(host, port)
                up = self._open(host, port)
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            else:
                try:
                    aliased = (urlsplit(target).hostname or "").lower().endswith(ALIAS_HOST)
                except ValueError:
                    aliased = False
                if aliased:
                    where = "a stream's alias"
                    up = self._serve_alias(client, method, target, version, fields, rest)
                    rest = b""
                    if up is None:
                        return
                else:
                    host, port, request = origin_head(method, target, version, fields)
                    where = f"{_shown(host)}:{port}"
                    up, proxied = self._open_plain(host, port)
                    if proxied:
                        u = urlsplit(target)
                        absolute = u._replace(netloc=u.netloc.rsplit("@", 1)[-1],
                                              fragment="").geturl()
                        request = _via(request, absolute, self._up.login if self._up else None)
                    up.sendall(request)
            if rest:
                up.sendall(rest)
            self.served += 1
            client.settimeout(None)
            up.settimeout(None)
            pipe(client, up)
        except Guarded as e:
            self._refused(e)
            _reply(client, 502, str(e))
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
        except Exception as e:          # a request the bridge cannot read: its kind alone
            self._failed(f"{where}: {type(e).__name__}")
            _reply(client, 400 if where == "?" else 502, f"{where}: {type(e).__name__}")
        finally:
            for s in (up, client):
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass


_bridges: dict[tuple[Route, Policy, str], Bridge] = OrderedDict()
_lock = threading.Lock()
#: Bridges kept at once: one per route and policy - a source's stream host
#: in it - in use this session. Past them the one used longest ago that
#: holds no alias stops taking connections (those under way go on).
MAX_BRIDGES = 32


def bridge_for(route: Route, policy: Policy | None = None, upstream: str = "") -> Bridge:
    """The bridge for *route*, *policy* (HOME_POLICY when none) and the
    person's *upstream* proxy ("" none), made when there is none yet;
    ValueError for a proxy a bridge cannot go through."""
    key = (route, policy if policy is not None else HOME_POLICY, upstream)
    with _lock:
        b = _bridges.pop(key, None)
        if b is not None and not b.closed:
            _bridges[key] = b           # the one used last at the end
            return b
        while len(_bridges) >= MAX_BRIDGES:
            idle = next((k for k, v in _bridges.items() if not v.aliases), None)
            _bridges.pop(idle if idle is not None else next(iter(_bridges))).close()
        b = _bridges[key] = Bridge(route, policy=key[1], upstream=upstream)
        log.info("net bridge on %s (%s; %s%s)", b.where, describe(route), key[1].describe(),
                 "; through a proxy" if upstream else "")
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
    """What a recorder (ffmpeg or mpv) going through the bridge at *address*
    is told in its command line: only that the bridge checks the stream's
    pinned certificate (*pinned*). The bridge's address - its key in it -
    goes in the recorder's environment (``recorder_env``), which only this
    user can read: every account on a Linux computer reads a process's
    command line, and with the key one could use the bridge."""
    if not address or not pinned:
        return []
    return ["-tls_verify", "0"] if kind == "ffmpeg" else ["--tls-verify=no"]


def recorder_env(address: str) -> dict[str, str]:
    """The environment a recorder goes through the bridge at *address* by:
    ffmpeg's http and tls read ``http_proxy`` (and mpv's FFmpeg with them)
    when no proxy option is given; ``no_proxy`` emptied so no host of the
    stream is let past it. {} without a bridge."""
    return {"http_proxy": address, "no_proxy": ""} if address else {}


def child_env(bridge: Bridge | None) -> dict[str, str]:
    """What a program the app starts (ffmpeg, mpv) on a stream is given in
    its environment to go through *bridge* - never in its arguments, which
    every account on a Linux computer reads: its address with its key
    (recorder_env). {} without one."""
    return recorder_env(bridge.address) if bridge is not None else {}


def guard_for(source: dict | None, url: str, n: object = None,
              proxy: str = "") -> Bridge | None:
    """The bridge for a stream of *source* (its options and kind as
    core/nerd.set_source keeps them - with ``_server``, make_client's form,
    where its server is) at *url*: its route (Nerd's, else none) and the
    policy of where the source is, the stream's own host and port let in;
    *proxy*: the person's http proxy it goes through (what the caller would
    have handed the program as -http_proxy, nerd.proxy's), "" for none.
    None when *url* is no http(s) address, *proxy* no http proxy, or the
    bridge cannot start: a program the app starts must then not be given
    the address (a recording fails, say why); the in-app player, which takes
    no arguments, may play it as before."""
    if net_route.url_host(url) is None:
        return None
    route = net_route.route_for(source, n)
    try:
        return bridge_for(route, policy_for(source, url), proxy)
    except ValueError as e:
        log.warning("net bridge: not through that proxy (%s)", e)
        return None
    except OSError as e:
        log.warning("net bridge: not started (%s)", e)
        return None


def proxy_for(source: dict | None, url: str, n: object = None) -> str:
    """The in-app player's http-proxy for a stream of *source* at *url*:
    guard_for's bridge's address with its key - set on the mpv core inside
    the app's own process, an option and no program's argument. "" for an
    address that is no http(s) one, or when the bridge could not start: the
    player then connects itself, as it always did (no stream left unplayed
    for want of a bridge)."""
    bridge = guard_for(source, url, n)
    return bridge.address if bridge is not None else ""


def address_for(source: dict | None, url: str, n: object = None) -> str:
    """The bridge's address for a stream of *source* at *url*, or "" when
    its route changes nothing there (the player connects as it always did).
    The bridge guards where the stream's connections land as guard_for's."""
    route = net_route.route_for(source, n)
    where = net_route.url_host(url)
    if not route.active or where is None:
        return ""
    scheme, host, port = where
    if not route.bridges(scheme, host, port):
        return ""
    try:
        return bridge_for(route, policy_for(source, url)).address
    except OSError as e:
        log.warning("net bridge: not started (%s) - the player connects itself", e)
        return ""
