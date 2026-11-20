"""The phone as a remote control: a small HTTP server on the home network
that serves one page - the remote (core/phone_page.py) - and the little it
asks for: what plays, the channels, the commands, TV mode on or off and its
keys, a channel's logo from the app's own cache (the window fetches one
into it, on this server's thread, when it is not there yet) and the calendar
(core/ical.py). The touchpad's gestures are read here (``gesture``): a
swipe is an arrow, a tap OK, a long press Back. Everything else the app
plays: a page of a list (api/browse: films, series, Recent, local files,
radio, recordings), a series' episodes (api/episodes), a row played by the
id it was listed with (api/play_ref), a poster made small (thumb/) - the
query string only the few keys they take (``query_params``).

Off until the person turns it on ("Phone remote…"). Only a phone at a
private address gets an answer - any part of a home network split in
VLANs, never the internet (core/home_net.py). The code on the screen holds
the address /r/ and, after its #, a long random key (``page_url``): a
browser never sends what follows the #, so the key never crosses the
network. The page keeps it on the phone and signs every request with it
(HMAC-SHA256 over the method, the address, the time and a nonce,
``signed``) - one seen on the way can be neither changed nor sent again,
and no request carries anything that would let another be made. A
picture's address is signed the same way, for a quarter of an hour
(``picture_ok``). The page itself, its manifest and icons hold nothing of
the person's and need no key. An address of before, with the key in its
path (/<key>/), gets a page that says to scan the code again - and, the
key having crossed the network, a new key at once (``on_leaked``). A
request whose signature does not hold gets 401, and an address that keeps
trying is shut out for a while. Each address may ask only so often
(a token bucket; a list's pictures, many at once as it scrolls, in a more
generous bucket of their own). Nothing else is served - not the remote API, whose own
server stays on this computer unless the person chose otherwise.

Like remote_api.py, it knows nothing of the window: *call(action, params)*
does the work (the window hands it to its own thread). Reading is GET,
anything that changes something is POST. The state carries an ETag that
leaves its clock out (``state_tag``): the page asks with it, and the same
state again is a 304 with nothing to read - a page of a list and a series'
episodes too. A live channel's rewind buffer is told the same way: how far
behind live and how far back, which the page reckons on by the clock
(``BufferTold``), so a channel played behind live keeps its tag too.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

from . import home_net
from .log import log
from .remote_api import _own, _Server

DEFAULT_PORT = 3674           # the remote API's 3673, and one
#: The port it listened on last (it moves on to the next when that one is taken).
PORT_KEY = "phone_remote_port"
TOKEN_BYTES = 24              # 32 characters, 192 bits
MAX_BODY = 4096
REQUEST_TIMEOUT = 15
LOGO_MAX = 2 * 1024 * 1024    # a logo served, or fetched for the phone
LOGO_TIMEOUT = 5.0            # a logo fetched for the phone (not on disk yet)

READ = frozenset({"state", "channels", "browse", "episodes", "tracks"})
WRITE = frozenset({"play", "toggle", "stop", "next", "previous", "volume", "mute",
                   "tv", "tv_key", "touch", "profile", "play_ref", "seek",
                   "tv_text", "track", "sleep", "rec", "rec_stop", "rec_next", "rec_go",
                   "rec_ask"})
#: What a reading request may say in its query string (core/phone_browse.py
#: checks each further), and how long each may be.
QUERY_KEYS = frozenset({"p", "o", "q", "sid", "v", "ref"})
QUERY_MAX = 400
#: The pictures a list shows (logos, posters) come many at once as it is
#: scrolled: a bucket of their own, more generous than the rest's.
PICTURES_RATE = 60.0
PICTURES_BURST = 300.0

#: The touchpad, in CSS pixels and milliseconds: a swipe moves this far at
#: least (the larger axis gives its direction), a tap no more than
#: TAP_SLOP, and a press held HOLD_MS is Back.
SWIPE_MIN = 40
TAP_SLOP = 12
HOLD_MS = 550

#: What every answer says: never cached, never framed, never sniffed, and
#: no address of ours handed on as a referrer.
#: What a phone's home screen asks for beside the page (core/phone_page.py).
ASSETS = frozenset({"manifest.webmanifest", "icon-180.png", "icon-192.png",
                    "icon-512.png", "icon-maskable-512.png"})
SAFE_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
                "Cross-Origin-Resource-Policy": "same-origin"}

_IMAGES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"),
           (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"))


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


#: The page's address on the server; its key is after the # (page_url).
PREFIX = "r"
#: How far a request's time may be from this computer's: phones keep the
#: network's time, so a minute either way.
SIGN_WINDOW_MS = 60_000
#: How long a picture's signed address holds (the page signs a new one).
PICTURE_TTL_MS = 15 * 60_000
#: Nonces remembered against a request sent again: a minute's at most.
NONCES_MAX = 20_000
_STAMP = re.compile(r"\d{1,15}")
_NONCE = re.compile(r"[A-Za-z0-9_-]{16,64}")
_SIGN = re.compile(r"[0-9a-f]{64}")


def _host(ip: str) -> str:
    return f"[{ip}]" if ":" in ip else ip


def page_url(ip: str, port: int, token: str) -> str:
    """The address the code holds: the page, and its key after the # -
    which the phone's browser never sends."""
    return f"http://{_host(ip)}:{int(port)}/{PREFIX}/#{token}"


def calendar_token(token: str) -> str:
    """The calendar's own address part: a calendar app cannot sign, so it
    gets a key that opens the calendar alone - read only, never the remote."""
    raw = hmac.new(token.encode("utf-8"), b"calendar", hashlib.sha256).digest()[:24]
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def calendar_url(ip: str, port: int, token: str) -> str:
    return f"http://{_host(ip)}:{int(port)}/c/{calendar_token(token)}/calendar.ics"


def signature(token: str, method: str, target: str, stamp: str, nonce: str, body: bytes = b"") -> str:
    """What the page signs a request with: HMAC-SHA256 of the method, the
    address asked for (its path and query), the time, a nonce and the body,
    each on a line - with the key the code holds."""
    msg = f"{method}\n{target}\n{stamp}\n{nonce}\n".encode("utf-8") + body
    return hmac.new(token.encode("utf-8"), msg, hashlib.sha256).hexdigest()


#: What an address of before is answered: the code is to be scanned again.
OLD_ADDRESS = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
               "<title>dopeIPTV Remote</title><body style='font:16px system-ui;margin:2em'>"
               "<p>This address of the remote is out of date. Open <b>Phone remote</b> in dopeIPTV and "
               "scan its code again.</p>").encode("utf-8")


def image_type(data: bytes) -> str | None:
    """A raster image's type by its first bytes (never an SVG: it can hold
    a script)."""
    for magic, ctype in _IMAGES:
        if data.startswith(magic):
            return ctype
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def query_params(query: str) -> dict[str, str]:
    """A reading request's query string: only the keys the page sends,
    each once and short."""
    out: dict[str, str] = {}
    try:
        got = parse_qs(query or "", keep_blank_values=False, max_num_fields=16)
    except ValueError:
        return out
    for key, values in got.items():
        if key in QUERY_KEYS and values and len(values[0]) <= QUERY_MAX:
            out[key] = values[0]
    return out


def picture_query(query: str) -> dict[str, str]:
    """A picture's signed address: its "x" (until when) and "s" alone."""
    try:
        got = parse_qs(query or "", keep_blank_values=False, max_num_fields=4)
    except ValueError:
        return {}
    return {k: v[0][:80] for k, v in got.items() if k in ("x", "s") and v}


def state_tag(state: dict[str, Any]) -> str:
    """The state's ETag: the same for the same state whatever the clock
    says ("time" changes with every answer and is left out)."""
    rest = {k: v for k, v in state.items() if k != "time"}
    raw = json.dumps(rest, sort_keys=True, ensure_ascii=False, default=str)
    return '"' + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20] + '"'


def buffer_reckoned(told: dict[str, Any], now: float, playing: bool) -> tuple[float, float]:
    """(seconds behind live, how far back the buffer reaches) at *now*, as
    the page reckons them on from what it was told (``BufferTold``) - the
    page's script does the same: behind live grows only while paused, the
    reach only while the buffer still fills, and never behind past it."""
    elapsed = max(0.0, now - float(told.get("t") or 0.0))
    reach = max(0.0, float(told.get("reach") or 0.0) + (elapsed if told.get("grows") else 0.0))
    behind = float(told.get("behind") or 0.0) + (0.0 if playing else elapsed)
    return min(max(0.0, behind), reach), reach


class BufferTold:
    """A live channel's rewind buffer as the phone is told it (the state's
    ``dvr``): how far behind live it plays, how far back the buffer reaches,
    whether it follows live, whether the buffer still fills, and when that
    was (``t``). The page reckons on from it by the clock
    (``buffer_reckoned``), so the same dict is told again - the same tag, a
    304 - while that reckoning stays within SLACK seconds of the player's;
    a pause or play, a seek, LIVE, a buffer full or filling again, another
    channel tell it anew."""

    SLACK = 3.0
    #: Whether the buffer still fills is looked at again this often (its end
    #: moves by whole segments, so a shorter look says little).
    EVERY = 10.0

    def __init__(self) -> None:
        self.told: dict[str, Any] | None = None
        self._key: Any = None
        self._playing = False
        self._mark: tuple[float, float] | None = None
        self._grows = True

    def forget(self) -> None:
        self.told, self._key, self._mark, self._grows = None, None, None, True

    def tell(self, key: Any, behind: float, reach: float, live: bool, playing: bool,
             now: float) -> dict[str, Any]:
        """The ``dvr`` for the player's reading (*behind*, *reach*, *live*)
        of the stream *key* at *now*: the last one told while it still holds."""
        if key != self._key:
            self.forget()
            self._key = key
        reach = max(0.0, reach)
        behind = min(max(0.0, behind), reach)
        mark = self._mark
        if mark is None or now < mark[0]:
            self._mark = (now, reach)
        elif now - mark[0] >= self.EVERY:
            self._grows = reach - mark[1] > 0.5 * (now - mark[0])
            self._mark = (now, reach)
        told = self.told
        if told is not None and told["live"] == bool(live) and self._playing == playing:
            then_behind, then_reach = buffer_reckoned(told, now, playing)
            if abs(then_behind - behind) <= self.SLACK and abs(then_reach - reach) <= self.SLACK:
                return told
        self.told = {"behind": round(behind, 1), "reach": round(reach, 1), "live": bool(live),
                     "grows": self._grows, "t": round(now, 1)}
        self._playing = playing
        return self.told


def gesture(dx: float, dy: float, ms: float) -> str | None:
    """The TV key of one touch on the touchpad - from where the finger went
    down to where it came up (*dx*, *dy*) in *ms*: an arrow, OK, Back, or
    None for one that is neither a swipe nor a tap."""
    ax, ay = abs(dx), abs(dy)
    far = max(ax, ay)
    if far >= SWIPE_MIN:
        if ax > ay:
            return "right" if dx > 0 else "left"
        return "down" if dy > 0 else "up"
    if far <= TAP_SLOP:
        return "back" if ms >= HOLD_MS else "ok"
    return None


#: Fetch Metadata's word for a request a web page of another site made; the
#: page's own say same-origin, an address typed, a bookmark or a QR code none.
_OTHER_SITE = ("cross-site", "same-site")


def cross_site(headers: Any, host: str | None = None) -> bool:
    """Whether a browser says a web page of another site asked: its
    ``Sec-Fetch-Site`` is cross-site or same-site, or it names an ``Origin``
    other than the server's own - ``http://`` and *host*, the Host it was
    asked under; with *host* None every Origin is another's. Media servers,
    players and the app's own pages (same-origin) send neither."""
    if (headers.get("Sec-Fetch-Site") or "").strip().lower() in _OTHER_SITE:
        return True
    origin = (headers.get("Origin") or "").strip().lower()
    return bool(origin) and (host is None or origin != "http://" + host.strip().lower())


class RateLimiter:
    """Per client address: *rate* requests a second with *burst* at once,
    and a lock-out of *lockout* seconds after *fails* wrong tokens within
    *window* seconds. *clock* is time.monotonic, or a test's."""

    def __init__(self, rate: float = 15.0, burst: float = 90.0, fails: int = 10,
                 window: float = 60.0, lockout: float = 600.0, clients: int = 1024,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.rate, self.burst = rate, burst
        self.fails, self.window, self.lockout = fails, window, lockout
        self.clients, self.clock = clients, clock
        self._lock = threading.Lock()
        self._buckets: OrderedDict[str, list[float]] = OrderedDict()
        self._failures: dict[str, list[float]] = {}
        self._blocked: dict[str, float] = {}

    def blocked(self, who: str) -> bool:
        with self._lock:
            until = self._blocked.get(who)
            if until is None:
                return False
            if self.clock() >= until:
                del self._blocked[who]
                return False
            return True

    def allow(self, who: str) -> bool:
        """One request from *who*: False when it is asking too often."""
        now = self.clock()
        with self._lock:
            b = self._buckets.pop(who, None)
            if b is None:
                b = [self.burst, now]
            tokens = min(self.burst, b[0] + (now - b[1]) * self.rate)
            ok = tokens >= 1.0
            b[0], b[1] = (tokens - 1.0 if ok else tokens), now
            self._buckets[who] = b
            while len(self._buckets) > self.clients:
                self._buckets.popitem(last=False)
            return ok

    def failed(self, who: str) -> bool:
        """A wrong token from *who*; True when that shut it out."""
        now = self.clock()
        with self._lock:
            seen = [t for t in self._failures.get(who, []) if now - t < self.window]
            seen.append(now)
            if len(seen) >= self.fails:
                self._failures.pop(who, None)
                self._blocked[who] = now + self.lockout
                if len(self._blocked) > self.clients:
                    oldest = min(self._blocked, key=self._blocked.__getitem__)
                    del self._blocked[oldest]
                return True
            self._failures[who] = seen
            if len(self._failures) > self.clients:
                self._failures.pop(next(iter(self._failures)))
            return False


class PhoneServer:
    """The phone remote's server. *page* gives the page as (HTML bytes, its
    Content-Security-Policy); *asset(name)* the home-screen manifest and
    icons as (bytes, content type), or None."""

    def __init__(self, port: int, token: str, call: Callable[[str, dict], Any],
                 page: Callable[[], tuple[bytes, str]], host: str = "0.0.0.0",
                 limiter: RateLimiter | None = None,
                 asset: Callable[[str], tuple[bytes, str] | None] | None = None,
                 pictures: RateLimiter | None = None,
                 on_client: Callable[[str], Any] | None = None,
                 on_leaked: Callable[[str], Any] | None = None) -> None:
        self.host, self.port, self.token = host, int(port), token
        #: Told (on the server's thread) of the new key made when an address
        #: of before brought the old one across the network.
        self.on_leaked = on_leaked
        self._nonces: OrderedDict[str, int] = OrderedDict()
        self._nonce_lock = threading.Lock()
        self.call, self.page = call, page
        self.asset = asset
        #: Told (on the server's thread) of the first request with the token
        #: from each address while this server runs: a phone connected.
        self.on_client = on_client
        self._clients: set[str] = set()
        self._clients_lock = threading.Lock()
        self.limiter = limiter or RateLimiter()
        self.pictures = pictures or RateLimiter(rate=PICTURES_RATE, burst=PICTURES_BURST)
        self._httpd: _Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self) -> None:
        """Listen; OSError when the port is taken (the caller says so)."""
        httpd = _Server((self.host, self.port), _make_handler(self))
        self.port = httpd.server_address[1]
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="dopeiptv-phone",
                                        daemon=True)
        self._thread.start()
        log.info("phone remote: listening on port %d", self.port)

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is None:
            return
        httpd.shutdown()
        httpd.server_close()
        log.info("phone remote: stopped")

    def authorized(self, given: str) -> bool:
        """Whether *given* is the key itself - only an address of before
        carries it, in its path (leaked)."""
        return bool(self.token) and hmac.compare_digest(given.encode("utf-8", "replace"),
                                                        self.token.encode("utf-8"))

    def signed(self, method: str, target: str, headers: Any, body: bytes = b"",
               now_ms: int | None = None) -> str:
        """"" for a request the page signed with the key - in its time, its
        nonce new - else why not: "missing", "stale", "bad" or "again"."""
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        stamp = str(headers.get("X-Remote-Time") or "")
        nonce = str(headers.get("X-Remote-Nonce") or "")
        sign = str(headers.get("X-Remote-Sign") or "")
        if not (_STAMP.fullmatch(stamp) and _NONCE.fullmatch(nonce) and _SIGN.fullmatch(sign)):
            return "missing"
        if abs(now_ms - int(stamp)) > SIGN_WINDOW_MS:
            return "stale"
        if not self.token or not hmac.compare_digest(
                signature(self.token, method, target, stamp, nonce, body), sign):
            return "bad"
        with self._nonce_lock:
            while self._nonces and (len(self._nonces) >= NONCES_MAX
                                    or next(iter(self._nonces.values())) < now_ms):
                self._nonces.popitem(last=False)
            if nonce in self._nonces:
                return "again"
            self._nonces[nonce] = now_ms + 2 * SIGN_WINDOW_MS
        return ""

    def picture_ok(self, path: str, query: dict[str, str], now_ms: int | None = None) -> bool:
        """A picture's address the page signed: *path* until its "x" (ms),
        at most PICTURE_TTL_MS ahead. A picture asked again from it is the
        browser's own, so no nonce."""
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        until, sign = query.get("x", ""), query.get("s", "")
        if not (_STAMP.fullmatch(until) and _SIGN.fullmatch(sign)):
            return False
        if not now_ms <= int(until) <= now_ms + PICTURE_TTL_MS + SIGN_WINDOW_MS:
            return False
        return bool(self.token) and hmac.compare_digest(
            signature(self.token, "GET", path, until, "picture"), sign)

    def calendar_ok(self, given: str) -> bool:
        return bool(self.token) and hmac.compare_digest(given.encode("utf-8", "replace"),
                                                        calendar_token(self.token).encode("utf-8"))

    def leaked(self) -> str:
        """The key crossed the network (an address of before): a new one at
        once, told to the window; every phone scans the code again."""
        self.token = new_token()
        with self._nonce_lock:
            self._nonces.clear()
        log.warning("phone remote: a phone used the address of before, which carries the key - "
                    "a new key was made; scan the code again")
        if self.on_leaked is not None:
            try:
                self.on_leaked(self.token)
            except Exception:
                log.debug("phone remote: on_leaked failed", exc_info=True)
        return self.token

    #: Addresses remembered as connected, at most.
    CLIENTS_MAX = 256

    def note_client(self, who: str) -> bool:
        """A request with the token from *who*: True (and on_client told)
        the first time this server sees that address."""
        with self._clients_lock:
            if who in self._clients or len(self._clients) >= self.CLIENTS_MAX:
                return False
            self._clients.add(who)
        if self.on_client is not None:
            try:
                self.on_client(who)
            except Exception:
                log.debug("phone remote: on_client failed", exc_info=True)
        return True

    @staticmethod
    def client_ok(address: str, own: str = "") -> bool:
        """A phone at a private address, on whichever part of a network
        split in VLANs; never one from the internet (*own*: the address it
        reached us at - the same is this computer)."""
        return home_net.is_home_client(address, own)


def _make_handler(server: PhoneServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "dopeIPTV"
        sys_version = ""
        timeout = REQUEST_TIMEOUT

        def log_message(self, fmt, *args):     # noqa: A003 - stdlib hook
            pass

        def _send(self, code: int, body: bytes, ctype: str,
                  extra: dict[str, str] | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in {**SAFE_HEADERS, **(extra or {})}.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _text(self, code: int, text: str) -> None:
            self._send(code, text.encode("utf-8"), "text/plain; charset=utf-8")

        def _raw(self) -> bytes | None:
            """The request's body as sent, MAX_BODY at most; None past it."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None
            if length < 0 or length > MAX_BODY:
                return None
            return self.rfile.read(length) if length else b""

        @staticmethod
        def _json_of(raw: bytes) -> dict[str, Any] | None:
            if not raw.strip():
                return {}
            try:
                body = json.loads(raw)
            except ValueError:
                return None
            return body if isinstance(body, dict) else None

        def _refused(self, who: str, why: str) -> None:
            """401 for a request the page did not sign, with this computer's
            time so the page can set its clock right once; a wrong signature
            counts towards the shut-out, never a web page's."""
            lim = server.limiter
            if why == "bad" and not cross_site(self.headers, self.headers.get("Host")) \
                    and lim.failed(who):
                log.warning("phone remote: an address on the network kept sending wrong "
                            "signatures; it is shut out for %d minutes", int(lim.lockout // 60))
            self._send(401, b'{"error": "not signed"}', "application/json; charset=utf-8",
                       {"X-Remote-Now": str(int(time.time() * 1000))})

        def _handle(self, method: str) -> None:
            who = self.client_address[0]
            if not server.client_ok(str(who), _own(self)):
                self._text(403, "forbidden")
                return
            lim = server.limiter
            parts = urlsplit(self.path)
            path = parts.path
            segs = path.split("/")
            # A list's pictures in a bucket of their own (a shut-out is one).
            bucket = server.pictures if len(segs) > 3 and segs[2] in ("thumb", "logo") else lim
            if lim.blocked(who) or not bucket.allow(who):
                self._send(429, b"too many requests", "text/plain; charset=utf-8", {"Retry-After": "10"})
                return
            first = unquote(segs[1]) if len(segs) > 1 else ""
            if first == "c" and len(segs) == 4 and segs[3] == "calendar.ics" and method in ("GET", "HEAD"):
                if not server.calendar_ok(unquote(segs[2])):
                    self._text(403, "forbidden")
                    return
                text = server.call("calendar", {})
                self._send(200, str(text).encode("utf-8"), "text/calendar; charset=utf-8",
                           {"Content-Disposition": 'inline; filename="dopeIPTV.ics"'})
                return
            if first != PREFIX:
                if first and server.authorized(first):
                    # An address of before: the key in its path, seen by
                    # whatever carried the request. Never again.
                    server.leaked()
                    self._send(410, OLD_ADDRESS, "text/html; charset=utf-8",
                               {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})
                    return
                # Only what looks like a try at the key counts: a phone's
                # browser asks for /favicon.ico by itself. Never a web
                # page's: one the phone visits would shut it out of its remote.
                if len(first) >= 16 and not cross_site(self.headers, self.headers.get("Host")) \
                        and lim.failed(who):
                    log.warning("phone remote: an address on the network kept guessing; "
                                "it is shut out for %d minutes", int(lim.lockout // 60))
                self._text(403, "forbidden")
                return
            if len(segs) == 2:
                self._send(301, b"", "text/plain", {"Location": f"/{PREFIX}/"})
                return
            rest = "/".join(segs[2:])
            reading = method in ("GET", "HEAD")
            try:
                if rest == "" and reading:
                    # The page holds nothing of the person's: its key is the
                    # phone's own (after the # of the code's address).
                    html, csp = server.page()
                    self._send(200, html, "text/html; charset=utf-8",
                               {"Content-Security-Policy": csp})
                elif rest in ASSETS and reading:
                    got = server.asset(rest) if server.asset is not None else None
                    if not got:
                        self._json(404, {"error": "not found"})
                    else:
                        self._send(200, got[0], got[1],
                                   {"Cache-Control": "private, max-age=86400"})
                elif (rest.startswith("logo/") or rest.startswith("thumb/")) and reading:
                    if not server.picture_ok(path, picture_query(parts.query)):
                        self._refused(str(who), "picture")
                    elif rest.startswith("logo/"):
                        self._logo(unquote(rest[5:]))
                    else:
                        self._thumb(unquote(rest[6:]))
                elif rest.startswith("api/"):
                    raw = self._raw() if method == "POST" else b""
                    if raw is None:
                        self._json(400, {"error": "the body is too large"})
                        return
                    target = path + ("?" + parts.query if parts.query else "")
                    why = server.signed(method, target, self.headers, raw)
                    if why:
                        self._refused(str(who), why)
                        return
                    server.note_client(str(who))
                    self._api(rest[4:], method, parts.query, raw)
                else:
                    self._json(404, {"error": "not found"})
            except TimeoutError:
                self._json(503, {"error": "busy"})
            except ValueError as e:
                self._json(400, {"error": str(e)})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                log.warning("phone remote: %s failed: %s", rest.split("/")[0] or "page", e)
                self._json(500, {"error": type(e).__name__})

        def _api(self, action: str, method: str, query: str = "", raw: bytes = b"") -> None:
            if action in READ:
                if method not in ("GET", "HEAD"):
                    self._json(405, {"error": "use GET"})
                    return
                result = server.call(action, query_params(query) if action in ("browse", "episodes") else {})
                if action == "channels" or not isinstance(result, dict):
                    self._json(200, result)
                    return
                # The page asks with the tag of what it shows (the state, a
                # page of a list, a series' episodes): the same again is a
                # 304 and nothing to read.
                tag = state_tag(result)
                if self.headers.get("If-None-Match", "").strip() == tag:
                    self._send(304, b"", "application/json; charset=utf-8", {"ETag": tag})
                    return
                self._send(200, json.dumps(result, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8", {"ETag": tag})
                return
            if action not in WRITE:
                self._json(404, {"error": "not found"})
                return
            if method != "POST":
                self._json(405, {"error": "use POST"})
                return
            if cross_site(self.headers, self.headers.get("Host")):
                # Changing something is the phone's own page alone, never a
                # web page's form or fetch (a text/plain body can be JSON).
                self._json(403, {"error": "not from another site"})
                return
            params = self._json_of(raw)
            if params is None:
                self._json(400, {"error": "the body is not a JSON object"})
                return
            if action == "touch":
                try:
                    key = gesture(float(params.get("dx", 0)), float(params.get("dy", 0)),
                                  float(params.get("ms", 0)))
                except (TypeError, ValueError):
                    self._json(400, {"error": '"dx", "dy" and "ms" are numbers'})
                    return
                if key is None:
                    self._json(200, {"ok": False})
                    return
                action, params = "tv_key", {"action": key}
            result = server.call(action, params)
            self._json(200, result if isinstance(result, dict) else {"ok": True})

        def _thumb(self, ident: str) -> None:
            """A poster's or a station's small picture, made on the server's
            thread by the window (bytes), never decoded on its own thread."""
            got = server.call("thumb", {"id": ident}) if ident else None
            data = got if isinstance(got, (bytes, bytearray)) else b""
            ctype = image_type(bytes(data[:16])) if 0 < len(data) <= LOGO_MAX else None
            if ctype is None:
                self._json(404, {"error": "no picture"})
                return
            self._send(200, bytes(data), ctype, {"Cache-Control": "private, max-age=86400"})

        def _logo(self, ident: str) -> None:
            path = server.call("logo", {"id": ident}) if ident else None
            data = b""
            if path:
                try:
                    with open(path, "rb") as fh:
                        data = fh.read(LOGO_MAX + 1)
                except OSError:
                    data = b""
            ctype = image_type(data) if 0 < len(data) <= LOGO_MAX else None
            if ctype is None:
                self._json(404, {"error": "no logo"})
                return
            self._send(200, data, ctype, {"Cache-Control": "private, max-age=86400"})

        def do_GET(self):      # noqa: N802 - stdlib hook
            self._handle("GET")

        def do_HEAD(self):     # noqa: N802
            self._handle("HEAD")

        def do_POST(self):     # noqa: N802
            self._handle("POST")

        def do_PUT(self):      # noqa: N802
            self._handle("PUT")

        do_DELETE = do_PUT
        do_PATCH = do_PUT

    return Handler
