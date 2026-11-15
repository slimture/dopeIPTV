"""The phone as a remote control: a small HTTP server on the home network
that serves one page - the remote (core/phone_page.py) - and the little it
asks for: what plays, the channels, the commands, TV mode on or off and its
keys, a channel's logo from the app's own cache and the calendar
(core/ical.py). The touchpad's gestures are read here (``gesture``): a
swipe is an arrow, a tap OK, a long press Back.

Off until the person turns it on ("Phone remote…"). Every address starts
with a long random token, /<token>/, which is what the code on the screen
holds: a request without it gets 403 and nothing else, and an address that
keeps guessing is shut out for a while. Each address may ask only so often
(a token bucket). Nothing else is served - not the remote API, whose own
server stays on this computer unless the person chose otherwise.

Like remote_api.py, it knows nothing of the window: *call(action, params)*
does the work (the window hands it to its own thread). Reading is GET,
anything that changes something is POST.
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from .log import log
from .remote_api import _Server

DEFAULT_PORT = 3674           # the remote API's 3673, and one
TOKEN_BYTES = 24              # 32 characters, 192 bits
MAX_BODY = 4096
REQUEST_TIMEOUT = 15
LOGO_MAX = 512 * 1024

READ = frozenset({"state", "channels"})
WRITE = frozenset({"play", "toggle", "stop", "next", "previous", "volume", "mute",
                   "tv", "tv_key", "touch"})

#: The touchpad, in CSS pixels and milliseconds: a swipe moves this far at
#: least (the larger axis gives its direction), a tap no more than
#: TAP_SLOP, and a press held HOLD_MS is Back.
SWIPE_MIN = 40
TAP_SLOP = 12
HOLD_MS = 550

#: What every answer says: never cached, never framed, never sniffed, and
#: no address of ours handed on as a referrer.
SAFE_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
                "Cross-Origin-Resource-Policy": "same-origin"}

_IMAGES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"),
           (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"))


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def page_url(ip: str, port: int, token: str) -> str:
    host = f"[{ip}]" if ":" in ip else ip
    return f"http://{host}:{int(port)}/{token}/"


def image_type(data: bytes) -> str | None:
    """A raster image's type by its first bytes (never an SVG: it can hold
    a script)."""
    for magic, ctype in _IMAGES:
        if data.startswith(magic):
            return ctype
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


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
    Content-Security-Policy)."""

    def __init__(self, port: int, token: str, call: Callable[[str, dict], Any],
                 page: Callable[[], tuple[bytes, str]], host: str = "0.0.0.0",
                 limiter: RateLimiter | None = None) -> None:
        self.host, self.port, self.token = host, int(port), token
        self.call, self.page = call, page
        self.limiter = limiter or RateLimiter()
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
        return bool(self.token) and hmac.compare_digest(given.encode("utf-8", "replace"),
                                                        self.token.encode("utf-8"))


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

        def _body(self) -> dict[str, Any] | None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None
            if length < 0 or length > MAX_BODY:
                return None
            raw = self.rfile.read(length) if length else b""
            if not raw.strip():
                return {}
            try:
                body = json.loads(raw)
            except ValueError:
                return None
            return body if isinstance(body, dict) else None

        def _handle(self, method: str) -> None:
            who = self.client_address[0]
            lim = server.limiter
            if lim.blocked(who) or not lim.allow(who):
                self._send(429, b"too many requests", "text/plain; charset=utf-8", {"Retry-After": "10"})
                return
            path = urlsplit(self.path).path
            segs = path.split("/")
            given = unquote(segs[1]) if len(segs) > 1 else ""
            if not server.authorized(given):
                # Only what looks like a try at the token counts: a phone's
                # browser asks for /favicon.ico by itself.
                if len(given) >= 16 and lim.failed(who):
                    log.warning("phone remote: an address on the network kept guessing; "
                                "it is shut out for %d minutes", int(lim.lockout // 60))
                self._text(403, "forbidden")
                return
            if len(segs) == 2:
                self._send(301, b"", "text/plain", {"Location": f"/{given}/"})
                return
            rest = "/".join(segs[2:])
            reading = method in ("GET", "HEAD")
            try:
                if rest == "" and reading:
                    html, csp = server.page()
                    self._send(200, html, "text/html; charset=utf-8",
                               {"Content-Security-Policy": csp})
                elif rest.startswith("api/"):
                    self._api(rest[4:], method)
                elif rest.startswith("logo/") and reading:
                    self._logo(unquote(rest[5:]))
                elif rest == "calendar.ics" and reading:
                    text = server.call("calendar", {})
                    self._send(200, str(text).encode("utf-8"), "text/calendar; charset=utf-8",
                               {"Content-Disposition": 'inline; filename="dopeIPTV.ics"'})
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

        def _api(self, action: str, method: str) -> None:
            if action in READ:
                if method not in ("GET", "HEAD"):
                    self._json(405, {"error": "use GET"})
                    return
                self._json(200, server.call(action, {}))
                return
            if action not in WRITE:
                self._json(404, {"error": "not found"})
                return
            if method != "POST":
                self._json(405, {"error": "use POST"})
                return
            params = self._body()
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
