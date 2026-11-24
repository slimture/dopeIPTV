"""Offline mode: what is downloaded is what the app shows, and nothing it
does goes to the internet. No Qt.

**One state.** Off, on by hand, or on by itself (``OFF``, ``HAND``,
``ITSELF``), why (``WHY_*``) and since when - kept here, read from any
thread (``on``), changed only on the window's thread (ui/mw_offline.py).
On by hand lasts the session: the next start begins as the network is.

**By itself, without asking anyone.** Nothing is pinged, probed or
connected to find out: the system's own word on the network (Qt's
QNetworkInformation, read by the window) is fed to ``Watch``, which turns
offline mode on once the system has said "no network" - or, for a source
on the internet, "only the local network" - for ``SETTLE_S``, and off
again once it has said "online" for as long (a Wi-Fi that flaps toggles
nothing). The second hint is the source's login failing for want of a
network (``no_network``: a name that cannot be looked up for now, a network
that cannot be reached) - at the start, or as offline mode ends; offline
mode then stays on until the system's word
changes to online, or the person turns it off. Turned off by hand while the
system still says there is no network, it stays off until the system's
word changes. Where Qt's network information cannot load (#36's runtime
without Kerberos' library) there is no detection at all: never a crash,
never a wait. ``DOPEIPTV_NETWORK`` overrides it - "0" for no detection,
"disconnected", "local", "site", "online" or "unknown" to stand in for the
system's word (the tests, and to try it out).

**Nothing out, in one place that fails closed** (``refusal``,
``host_refusal``, ``peer_refusal``): asked by core/_lazy_requests.py's
adapter for every request the app makes (core/net_adapter.py's too), by
core/dest_guard.check_peer for every connection it opens, by the bridge
(core/net_bridge.py) for every connection a program the app starts makes
through it, and by the events (core/events.py) and the player's door
(ui/mw_offline.py). While offline mode is on a request may go only to this
computer, a link-local host or the home network: by the host as written - a
private address in any spelling (an IPv4 address inside an IPv6 one read as
itself, never by its string), a name without a dot or with a home ending -
or, for a public name the person gave themselves (a source's own server,
dest_guard.note_own) or Plex's ``.plex.direct``, by every address it
resolves to (one on the internet, or none, and it is refused). Any other
public name is refused without being looked up. The connection is judged
once more on its socket, before a byte is sent: a home name whose answer
is on the internet goes nowhere. A refusal is said once per host per
session, at debug.
"""

from __future__ import annotations

import errno
import os
import socket
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from .log import log

OFF, HAND, ITSELF = "off", "hand", "itself"
MODES = (OFF, HAND, ITSELF)

#: Why offline mode is on.
WHY_HAND = "hand"                 # the person turned it on
WHY_NO_NETWORK = "no_network"     # the system says there is no network
WHY_LOCAL = "local"               # the system says only the local network
WHY_UNREACHED = "unreached"       # the source could not be reached: no network

#: The system's word on the network (QNetworkInformation's Reachability).
DISCONNECTED, LOCAL, SITE, ONLINE, UNKNOWN = "disconnected", "local", "site", "online", "unknown"
WORDS = (DISCONNECTED, LOCAL, SITE, ONLINE, UNKNOWN)

#: How long the system's word must hold before offline mode follows it.
SETTLE_S = 5.0

#: What a refusal says it was refused for.
INTERNET = "the internet (offline mode)"

#: The environment's word on detection (the module's docstring).
ENV = "DOPEIPTV_NETWORK"

_lock = threading.Lock()
_mode = OFF
_why = ""
_since = 0.0
#: The hosts a refusal was said for this session (at most _SAID_MAX).
_said: set[str] = set()
_SAID_MAX = 512
#: A name of the person's own looked up while offline: (when, why), kept
#: _RESOLVED_TTL - pictures are many requests to a few hosts.
_resolved: dict[tuple[str, int], tuple[float, str]] = {}
_RESOLVED_TTL = 30.0
_RESOLVED_MAX = 256


# -- the state ----------------------------------------------------------------------

def on() -> bool:
    """Whether offline mode is on (any thread)."""
    return _mode != OFF


def mode() -> str:
    return _mode


def why() -> str:
    return _why


def since() -> float:
    """When offline mode began (time.time()), 0 while it is off."""
    return _since


def set_mode(new: str, reason: str = "", now: float | None = None) -> bool:
    """Offline mode *new* (``MODES``) for *reason* - the window's thread.
    True when it changed."""
    global _mode, _why, _since
    if new not in MODES:
        raise ValueError(new)
    with _lock:
        if new == _mode and (new == OFF or reason == _why):
            return False
        was = _mode
        _mode = new
        _why = "" if new == OFF else str(reason or "")
        if new == OFF:
            _since = 0.0
        elif was == OFF:
            _since = time.time() if now is None else now
        _resolved.clear()
    log.info("offline mode %s%s", "off" if new == OFF else f"on ({new})",
             f": {_why}" if _why else "")
    return True


def reset() -> None:
    """Off, nothing said (the tests)."""
    global _mode, _why, _since
    with _lock:
        _mode, _why, _since = OFF, "", 0.0
        _said.clear()
        _resolved.clear()


def env_word(environ: Any = None) -> str | None:
    """What ``DOPEIPTV_NETWORK`` says: "" for no detection at all, one of
    ``WORDS`` to stand in for the system's word, None to watch the system."""
    raw = str((environ if environ is not None else os.environ).get(ENV, "") or "")
    word = raw.strip().lower()
    if not word or word == "watch":
        return None
    if word in ("0", "off", "no", "false"):
        return ""
    return word if word in WORDS else None


# -- the system's word, settled ------------------------------------------------------

def offline_word(word: str, home_source: bool = False) -> bool | None:
    """What the system's *word* means for offline mode: True - offline;
    False - online; None - it says nothing. Only the local network counts
    as offline for a source on the internet - a source at home still
    answers there (*home_source*)."""
    if word == DISCONNECTED:
        return True
    if word in (LOCAL, SITE):
        return None if home_source else True
    if word == ONLINE:
        return False
    return None


class Watch:
    """The system's word, settled, and what it asks of offline mode. Pure:
    the clock is passed in. The window feeds it (``said``), ticks it
    (``tick``) and is told the mode to take (``tick``'s answer); the person's
    switch (``by_hand``) and the login's hint (``unreached``) go through it
    too, so one place decides."""

    def __init__(self, home_source: bool = False) -> None:
        self.word = UNKNOWN
        self.word_at = 0.0
        self.home_source = home_source
        #: How often the word changed: a hint holds until it changes.
        self.changes = 0
        #: Turned off by hand while the system said offline: by itself
        #: only once the word changed since (``changes`` then).
        self.held_off_at: int | None = None
        #: The word's ``changes`` when the login's hint turned it on.
        self.hint_at: int | None = None

    def said(self, word: str, now: float) -> None:
        """The system said *word* at *now*."""
        word = word if word in WORDS else UNKNOWN
        if word != self.word:
            self.word = word
            self.word_at = now
            self.changes += 1

    def settled(self, now: float) -> bool | None:
        """The word's meaning (``offline_word``) once it held SETTLE_S;
        None while it has not, or says nothing."""
        if now - self.word_at < SETTLE_S:
            return None
        return offline_word(self.word, self.home_source)

    def wait_left(self, now: float) -> float:
        """Seconds until the word has settled (0: it has)."""
        return max(0.0, SETTLE_S - (now - self.word_at))

    def tick(self, now: float, current: str, reason: str = "") -> tuple[str, str] | None:
        """The mode to take now - (mode, why) - or None to stay as it is,
        offline mode being *current* for *reason*."""
        verdict = self.settled(now)
        if current == HAND:
            return None
        if current == ITSELF:
            if verdict is not False:
                return None
            if reason == WHY_UNREACHED and self.hint_at is not None \
                    and self.changes <= self.hint_at:
                return None             # the login's hint: until the word changes
            self.hint_at = None
            return (OFF, "")
        if verdict is True:
            if self.held_off_at is not None and self.changes <= self.held_off_at:
                return None             # turned off by hand: not by itself again yet
            self.held_off_at = None
            return (ITSELF, WHY_NO_NETWORK if self.word == DISCONNECTED else WHY_LOCAL)
        if verdict is False:
            self.held_off_at = None
        return None

    def by_hand(self, turn_on: bool, now: float) -> tuple[str, str]:
        """The person's switch: (mode, why)."""
        self.hint_at = None
        if turn_on:
            return (HAND, WHY_HAND)
        if offline_word(self.word, self.home_source) is True or self.settled(now) is None:
            self.held_off_at = self.changes
        return (OFF, "")

    def unreached(self, current: str) -> tuple[str, str] | None:
        """The source's login failed for want of a network: (mode, why) to
        take, or None (on by hand already, or turned off by hand while the
        system's word stays as it was)."""
        if current == HAND:
            return None
        if self.held_off_at is not None and self.changes <= self.held_off_at:
            return None
        self.hint_at = self.changes
        return (ITSELF, WHY_UNREACHED)


# -- for want of a network -----------------------------------------------------------

#: getaddrinfo's "not now" (the resolver cannot be reached): EAI_AGAIN and
#: EAI_FAIL where the platform has them, Windows' WSATRY_AGAIN.
_DNS_AGAIN = frozenset(n for n in (getattr(socket, "EAI_AGAIN", None),
                                   getattr(socket, "EAI_FAIL", None), 11002) if n is not None)
#: A network that cannot be reached at all.
_NO_ROUTE = frozenset(n for n in (getattr(errno, "ENETUNREACH", None),
                                  getattr(errno, "ENETDOWN", None), 10050, 10051)
                      if n is not None)


def no_network(exc: BaseException | None, depth: int = 0) -> bool:
    """Whether *exc* - or what it was raised from, however deep requests
    and urllib3 wrapped it - says there is no network: a name that cannot
    be looked up for now, a network that cannot be reached. A server that
    answers no, a name that does not exist, a timeout: not this."""
    if exc is None or depth > 8:
        return False
    if isinstance(exc, socket.gaierror):
        return exc.errno in _DNS_AGAIN
    if isinstance(exc, OSError) and exc.errno in _NO_ROUTE:
        return True
    text = str(exc)
    if "Temporary failure in name resolution" in text or "Network is unreachable" in text:
        return True
    seen: list[BaseException] = []
    for inner in (exc.__cause__, exc.__context__, getattr(exc, "reason", None),
                  *[a for a in getattr(exc, "args", ()) if isinstance(a, BaseException)]):
        if isinstance(inner, BaseException) and inner is not exc and inner not in seen:
            seen.append(inner)
    return any(no_network(e, depth + 1) for e in seen)


# -- nothing out -----------------------------------------------------------------------

def _host_port(url: str) -> tuple[str, int] | None:
    try:
        parts = urlsplit(str(url or ""))
        host = (parts.hostname or "").rstrip(".").lower()
        port = parts.port or (443 if parts.scheme.lower() in ("https", "wss") else 80)
    except ValueError:
        return None
    return host, port


def _given(name: str) -> bool:
    """A name the person gave themselves (dest_guard.note_own), or Plex's
    for a server at home."""
    from . import dest_guard
    if name.endswith(dest_guard._HOME_BY_DESIGN):
        return True
    with dest_guard._NAMES_LOCK:
        return name in dest_guard._OWN


def host_refusal(host: str, port: int = 0) -> str:
    """Why a connection to *host* may not be made now - "" when it may:
    always while offline mode is off; while it is on, only to this
    computer, a link-local host or the home network (the module's
    docstring). Never raises."""
    if _mode == OFF:
        return ""
    from . import dest_guard, stream_address
    name = str(host or "").strip().strip("[]").rstrip(".").lower()
    if not name:
        return INTERNET                 # nothing to judge: nothing goes
    try:
        if stream_address._ip(name) is not None:
            return "" if dest_guard.ip_kind(name) else INTERNET
        if dest_guard.named_kind(name):
            return ""                   # this computer or a home name as written
        if not _given(name):
            return INTERNET             # a public name: not even looked up
        return _resolved_refusal(name, port)
    except Exception:
        return INTERNET


def _resolved_refusal(name: str, port: int) -> str:
    from . import dest_guard
    now = time.monotonic()
    key = (name, int(port or 0))
    with _lock:
        hit = _resolved.get(key)
    if hit is not None and now - hit[0] < _RESOLVED_TTL:
        return hit[1]
    answers = dest_guard._resolved(name, int(port or 80))
    why = "" if answers and all(dest_guard.ip_kind(a) for a in answers) else INTERNET
    with _lock:
        if len(_resolved) >= _RESOLVED_MAX:
            _resolved.clear()
        _resolved[key] = (now, why)
    return why


def refusal(url: str) -> str:
    """Why a request for *url* may not be sent now ("" when it may):
    ``host_refusal`` of its host. An address that cannot be read is
    refused while offline."""
    if _mode == OFF:
        return ""
    where = _host_port(url)
    if where is None:
        return INTERNET
    return host_refusal(*where)


def stream_refusal(url: str) -> str:
    """Why a stream at *url* may not be opened now - by the player, a
    recording, server mode: ``refusal``'s rule for an address with a host;
    a file, or an address with none (a socket listening here), is this
    computer's."""
    if _mode == OFF:
        return ""
    text = str(url or "").strip()
    if "://" not in text or text.lower().startswith("file:"):
        return ""
    where = _host_port(text)
    if where is None:
        return INTERNET
    if not where[0]:
        return ""
    return host_refusal(*where)


def peer_refusal(peer: str) -> str:
    """Why a connection that reached *peer* (an address) may not be used
    now: one on the internet while offline mode is on."""
    if _mode == OFF:
        return ""
    from . import dest_guard
    try:
        return "" if dest_guard.ip_kind(str(peer or "")) else INTERNET
    except Exception:
        return INTERNET


def note_refused(host: str, what: str = "a request") -> None:
    """A refusal said - once per host per session, at debug."""
    name = str(host or "?").strip().lower()
    with _lock:
        if name in _said or len(_said) >= _SAID_MAX:
            return
        _said.add(name)
    log.debug("offline mode: %s to %s not sent", what, name)
