"""The one gate in front of the app's servers for the home network: the
phone remote (core/phone_remote.py), the remote API and shared lists
(core/remote_api.py) and server mode (services/home_server.py).

What each of them asks of a request before it does anything with it is
asked here, so the three cannot drift apart:

* who asks - this computer or the home network, never the internet
  (``at_home``; ``this_computer`` for what is never shut out or counted);
* under which name - a Host of the home, never the public name of a web
  page a DNS rebinding pointed here (``host_ok``);
* how often - a budget per address and one for every address together,
  and a shut-out after too many wrong tokens (``RateLimiter``; ``front``
  asks those three in that order: a rebinding page's requests never use up
  the phone's budget);
* from where - a web page of another site, by the browser's own headers
  (``cross_site``);
* with what - the token, compared in constant time with every token that
  opens the door (``given_token``, ``token_ok``), a wrong one counted
  towards the shut-out, never this computer's nor a web page's
  (``wrong_token``).

Each server answers in its own words and status codes; the checks are
these.
"""
from __future__ import annotations

import hmac
import ipaddress
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable, Mapping

from . import home_net
from .log import log

#: Every address together, beside each one's own bucket: a device that asks
#: from address after address cannot keep the server - and the window's
#: thread - busy. Four phones at full tilt; a phone polls about once a
#: second, Home Assistant every few. This computer is not counted.
SERVER_RATE = 60.0
SERVER_BURST = 360.0

#: What ``front`` says of a request it turns away.
AWAY = "away"          # not this computer nor the home network
FOREIGN_HOST = "host"  # a Host that is no name of the home
TOO_OFTEN = "often"    # shut out, or over its budget


def at_home(who: str, own: str = "") -> bool:
    """*who* is this computer or on the home network, whichever part of a
    network split in VLANs (*own*: the address it reached us at - the same
    is this computer); never an address on the internet."""
    return home_net.is_home_client(who, own)


def own_address(handler: BaseHTTPRequestHandler) -> str:
    """The address a client reached this computer at."""
    try:
        return str(handler.connection.getsockname()[0])
    except (OSError, AttributeError, IndexError):
        return ""


def host_ok(host: str | None) -> bool:
    """The ``Host`` a request came under is one the home uses: none (an
    HTTP/1.0 client), an address, or a name of the home
    (home_net.home_host). A web page whose own name a DNS rebinding pointed
    at this computer asks under that public name."""
    return not host or home_net.home_host(host)


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


def this_computer(who: str) -> bool:
    """Whether *who* is this computer (loopback, however it is written)."""
    try:
        ip = ipaddress.ip_address(who.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped.is_loopback
    return ip.is_loopback


class RateLimiter:
    """Per client address: *rate* requests a second with *burst* at once,
    and a lock-out of *lockout* seconds after *fails* wrong tokens within
    *window* seconds - never of an address *spare* names (this_computer:
    loopback, the computer's own scripts). With *total_rate*, every address
    but those together has a budget too (*total_rate* a second,
    *total_burst* at once): one device asking from address after address
    gets a fresh bucket with each, never more than the whole server's.
    *clock* is time.monotonic, or a test's."""

    def __init__(self, rate: float = 15.0, burst: float = 90.0, fails: int = 10,
                 window: float = 60.0, lockout: float = 600.0, clients: int = 1024,
                 clock: Callable[[], float] = time.monotonic,
                 total_rate: float = 0.0, total_burst: float = 0.0,
                 spare: Callable[[str], bool] | None = None) -> None:
        self.rate, self.burst = rate, burst
        self.fails, self.window, self.lockout = fails, window, lockout
        self.clients, self.clock = clients, clock
        self.total_rate, self.total_burst = total_rate, max(total_burst, 1.0)
        self.spare = spare
        self._lock = threading.Lock()
        self._buckets: OrderedDict[str, list[float]] = OrderedDict()
        self._total = [self.total_burst, clock()]
        self._said_full = -1e18
        self._failures: dict[str, list[float]] = {}
        self._blocked: dict[str, float] = {}

    def _spared(self, who: str) -> bool:
        return self.spare is not None and self.spare(who)

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
        """One request from *who*: False when it is asking too often - or,
        with a whole-server budget, when every address together is."""
        now = self.clock()
        with self._lock:
            b = self._buckets.pop(who, None)
            if b is None:
                b = [self.burst, now]
            tokens = min(self.burst, b[0] + (now - b[1]) * self.rate)
            ok = tokens >= 1.0
            whole = self.total_rate > 0 and not self._spared(who)
            if ok and whole:
                t = self._total
                t[0], t[1] = min(self.total_burst, t[0] + (now - t[1]) * self.total_rate), now
                ok = t[0] >= 1.0
                if ok:
                    t[0] -= 1.0
                elif now - self._said_full >= 60.0:
                    self._said_full = now
                    log.warning("a server of the app was asked more often than its whole budget - "
                                "too many requests, from too many addresses; answered 429")
            b[0], b[1] = (tokens - 1.0 if ok else tokens), now
            self._buckets[who] = b
            while len(self._buckets) > self.clients:
                self._buckets.popitem(last=False)
            return ok

    def failed(self, who: str) -> bool:
        """A wrong token from *who*; True when that shut it out. An address
        *spare* names is never shut out."""
        if self._spared(who):
            return False
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


def front(who: str, own: str, host: str | None, limiter: RateLimiter,
          bucket: RateLimiter | None = None,
          admits: Callable[[str, str], bool] = at_home) -> str | None:
    """The first look at a request, in this order: who asks (``AWAY``;
    *admits* the server's own judge of that, ``at_home``'s rule), the Host
    it came under (``FOREIGN_HOST`` - before anything is counted, so a
    rebinding page's tries never use up a phone's budget), then whether
    *who* is shut out or over its budget (``TOO_OFTEN``; *bucket* the
    budget this kind of request draws on, *limiter* by default). None: go
    on."""
    if not admits(who, own):
        return AWAY
    if not host_ok(host):
        return FOREIGN_HOST
    if limiter.blocked(who) or not (bucket or limiter).allow(who):
        return TOO_OFTEN
    return None


def given_token(headers: Any, query: Mapping[str, list[str]]) -> str:
    """The token a request brings: ``Authorization: Bearer``, else the
    ``X-Dopeiptv-Token`` header, else ``?token=``."""
    auth = headers.get("Authorization") or ""
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return (headers.get("X-Dopeiptv-Token") or "").strip() or (query.get("token") or [""])[0]


def token_ok(given: str, *tokens: str) -> bool:
    """*given* is one of *tokens* (an empty one opens nothing), compared in
    constant time with each of them."""
    got = given.encode("utf-8", "replace")
    ok = False
    for t in tokens:
        if t:
            ok |= hmac.compare_digest(got, t.encode("utf-8"))
    return ok


def wrong_token(limiter: RateLimiter, who: str, headers: Any, *, here: bool,
                said: str) -> bool:
    """A wrong token from *who*, counted towards its shut-out - never this
    computer's (*here*: its own scripts ask by loopback) nor a web page's:
    a page the person visits would shut their own browser, or a phone, out.
    *said* names the server and what was wrong in the warning logged when
    it shuts *who* out. True when it did."""
    if here or cross_site(headers, headers.get("Host")) or not limiter.failed(who):
        return False
    log.warning("%s; it is shut out for %d minutes", said, int(limiter.lockout // 60))
    return True
