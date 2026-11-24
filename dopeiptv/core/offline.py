"""Offline mode: what is downloaded is what the app shows, and nothing it
does goes to the internet. No Qt.

**One state.** Off, on by hand, or on by itself (``OFF``, ``HAND``,
``ITSELF``), why (``WHY_*``) and since when - kept here, read from any
thread (``on``), changed only on the window's thread (ui/mw_offline.py).
On by hand lasts the session: the next start begins as the network is.

**By itself, without asking anyone.** Nothing is pinged, probed or
connected to find out: the system's own word on the network (Qt's
QNetworkInformation, read by the window) is fed to ``Watch``, which turns
offline mode on once the system has said "no network" for ``SETTLE_S``,
and off again once it has said anything else for as long (a Wi-Fi that
flaps toggles nothing). "Only the local network" and "no internet" (Qt's
Local and Site) say nothing alone: NetworkManager and Windows say them
whenever their own check of the internet fails - behind a DNS blocker, a
VPN or a privacy tool with the internet working too. The other hint is the
source's login failing for want of a network (``no_network``: a name that
cannot be looked up for now, a network that cannot be reached) - at the
start, or as offline mode ends; offline mode then stays on until the
system's word changes to online, or the person turns it off. By itself it
never stops what plays: while something plays from the internet
(``needs_network``) it waits, and comes on once that has ended (``Watch``'s
*playing*, ``waiting``). Turned off by hand ("Go online"), it stays off -
the login's hint too - until the system says something new and definite:
"online", or "no network" after "online". Where Qt's network information
cannot load (#36's runtime without Kerberos' library) there is no detection
at all: never a crash, never a wait. ``DOPEIPTV_NETWORK`` overrides it -
"0" for no detection, "disconnected", "local", "site", "online" or
"unknown" to stand in for the system's word (the tests, and to try it out).

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

**What a program opens itself** (the player, the recorder, yt-dlp): a
stream's address with every address written inside it - mpv's ytdl://,
lavf://, edl://, FFmpeg's concat: - each by its host as its readers take it
(``_written``); and a file on this computer that is a playlist or a
stream's manifest - by its name or its first bytes - by the addresses it
names (``file_refusal``: read at most ``LIST_MAX``, never a pipe waited on;
one longer than that, one that names another such list here or spells an
address in XML's character references is refused, as it cannot be judged).
"""

from __future__ import annotations

import errno
import os
import socket
import stat
import string
import threading
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

from .log import log

OFF, HAND, ITSELF = "off", "hand", "itself"
MODES = (OFF, HAND, ITSELF)

#: Why offline mode is on.
WHY_HAND = "hand"                 # the person turned it on
WHY_NO_NETWORK = "no_network"     # the system says there is no network
WHY_UNREACHED = "unreached"       # the source could not be reached: no network

#: The system's word on the network (QNetworkInformation's Reachability).
DISCONNECTED, LOCAL, SITE, ONLINE, UNKNOWN = "disconnected", "local", "site", "online", "unknown"
WORDS = (DISCONNECTED, LOCAL, SITE, ONLINE, UNKNOWN)
#: The words that say something for certain.
DEFINITE = (DISCONNECTED, ONLINE)

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

def offline_word(word: str) -> bool | None:
    """What the system's *word* means for offline mode: True - offline (no
    network at all); False - online; None - it says nothing. "Only the
    local network" and "no internet" (LOCAL, SITE) are what NetworkManager
    and Windows say when their own check of the internet fails, whether the
    internet works or not: alone they say nothing - a request that fails
    for want of a network says it then (``Watch.unreached``)."""
    if word == DISCONNECTED:
        return True
    if word == ONLINE:
        return False
    return None


class Watch:
    """The system's word, settled, and what it asks of offline mode. Pure:
    the clock is passed in. The window feeds it (``said``), ticks it
    (``tick``) and is told the mode to take (``tick``'s answer); the person's
    switch (``by_hand``) and the login's hint (``unreached``) go through it
    too, so one place decides. Offline mode by itself never comes on while
    something plays from the internet (*playing*): it waits - ``waiting``,
    the window looks again - until that has ended."""

    def __init__(self) -> None:
        self.word = UNKNOWN
        self.word_at = 0.0
        #: How often the word changed: the login's hint holds until it changes.
        self.changes = 0
        #: The last definite word that held SETTLE_S (DEFINITE), and how
        #: often that changed: turned off by hand, offline mode is not on by
        #: itself again until it has (``held_off_at``: ``turns`` then).
        self.definite = ""
        self.turns = 0
        self.held_off_at: int | None = None
        #: The word's ``changes`` when the login's hint turned it on.
        self.hint_at: int | None = None
        #: The login's hint held back while something plays: the word's
        #: ``changes`` then (the system saying online since drops it).
        self.hint_waits: int | None = None
        #: Offline mode by itself held back while something plays: the
        #: window looks again.
        self.waiting = False

    def said(self, word: str, now: float) -> None:
        """The system said *word* at *now*."""
        word = word if word in WORDS else UNKNOWN
        if word != self.word:
            self.word = word
            self.word_at = now
            self.changes += 1

    def settled(self, now: float) -> bool | None:
        """The word's meaning (``offline_word``) once it held SETTLE_S -
        a definite one counted as such (``definite``, ``turns``); None
        while it has not, or says nothing."""
        if now - self.word_at < SETTLE_S:
            return None
        self._count_definite()
        return offline_word(self.word)

    def _count_definite(self) -> None:
        if self.word in DEFINITE and self.word != self.definite:
            self.definite = self.word
            self.turns += 1

    def wait_left(self, now: float) -> float:
        """Seconds until the word has settled (0: it has)."""
        return max(0.0, SETTLE_S - (now - self.word_at))

    def _held(self) -> bool:
        """Turned off by hand, and the system said nothing new and definite
        since."""
        return self.held_off_at is not None and self.turns <= self.held_off_at

    def tick(self, now: float, current: str, reason: str = "",
             playing: bool = False) -> tuple[str, str] | None:
        """The mode to take now - (mode, why) - or None to stay as it is,
        offline mode being *current* for *reason* and *playing* whether
        something plays from the internet."""
        verdict = self.settled(now)
        if current != OFF:
            self.waiting, self.hint_waits = False, None
        if current == HAND:
            return None
        if current == ITSELF:
            if reason == WHY_UNREACHED:
                if verdict is not False:
                    return None
                if self.hint_at is not None and self.changes <= self.hint_at:
                    return None         # the login's hint: until the word changes
                self.hint_at = None
                return (OFF, "")
            # The system's "no network": over once it says anything else.
            if self.word in (DISCONNECTED, UNKNOWN) or now - self.word_at < SETTLE_S:
                return None
            return (OFF, "")
        if verdict is False and self.hint_waits is not None and self.changes > self.hint_waits:
            self.hint_waits = None
        want: tuple[str, str] | None = None
        if not self._held():
            if verdict is True:
                want = (ITSELF, WHY_NO_NETWORK)
            elif self.hint_waits is not None:
                want = (ITSELF, WHY_UNREACHED)
        self.waiting = want is not None and playing
        if want is None or playing:
            return None
        if want[1] == WHY_UNREACHED:
            self.hint_waits = None
            self.hint_at = self.changes
        return want

    def by_hand(self, turn_on: bool, now: float) -> tuple[str, str]:
        """The person's switch: (mode, why). Turned off ("Go online"), it
        holds until the system says something new and definite."""
        self.hint_at = self.hint_waits = None
        self.waiting = False
        if turn_on:
            return (HAND, WHY_HAND)
        # What the system says as the person decides is nothing new, settled
        # or not.
        self._count_definite()
        self.held_off_at = self.turns
        return (OFF, "")

    def unreached(self, current: str, playing: bool = False) -> tuple[str, str] | None:
        """The source's login failed for want of a network: (mode, why) to
        take, or None - on by hand already, turned off by hand since the
        system last said something new and definite, or waiting while
        something plays from the internet (*playing*: ``waiting``)."""
        if current == HAND or self._held():
            return None
        if playing:
            self.hint_waits, self.waiting = self.changes, True
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
    return _judged_host(host, port)


def _judged_host(host: str, port: int = 0, look: bool = True) -> str:
    """``host_refusal``'s rule, whatever the mode; without *look* a name the
    person gave counts as the internet (nothing looked up). Never raises."""
    from . import dest_guard, stream_address
    name = str(host or "").strip().strip("[]").rstrip(".").lower()
    if not name:
        return INTERNET                 # nothing to judge: nothing goes
    try:
        ip = stream_address._ip(name)
        if ip is not None:
            # A multicast group is the local network's (IPTV through the
            # router): joined there, never sent anywhere.
            return "" if dest_guard.ip_kind(name) or ip.is_multicast else INTERNET
        if dest_guard.named_kind(name):
            return ""                   # this computer or a home name as written
        if not look or not _given(name):
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
    return _judged_host(*where)


def stream_refusal(url: str) -> str:
    """Why a stream at *url* may not be opened now - by the player, a
    recording, an external player, server mode: ``refusal``'s rule for an
    address with a host, and for every address written inside it (mpv's
    ytdl://, lavf://, edl://...: the module's docstring); an address with
    none (a socket listening here) is this computer's, a file too - unless
    it is a playlist or a manifest that names the internet
    (``file_refusal``)."""
    if _mode == OFF:
        return ""
    return _judged_stream(url, True)


def needs_network(url: str) -> bool:
    """Whether a stream at *url* is one offline mode would refuse - asked
    while it is off too (what plays keeps offline mode by itself waiting):
    as written, nothing looked up (a name the person gave counts as the
    internet), no file read (a file is this computer's). Never raises."""
    try:
        return bool(_judged_stream(url, False))
    except Exception:
        return True


def _judged_stream(url: str, look: bool) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        scheme = urlsplit(text).scheme.lower() if "://" in text else ""
    except ValueError:
        return INTERNET
    if len(scheme) <= 1 or scheme == "file":
        # A path (a Windows drive's letter is no scheme), or file:// - a
        # file://host/ is a network path on Windows: judged by its host.
        if scheme == "file":
            why = _written_refusal(text, look)
            if why:
                return why
            from .commands import file_url_path
            text = file_url_path(text)
        return _list_refusal(text) if look else ""
    where = _host_port(text)
    if where is None:
        return INTERNET
    if where[0]:
        why = _judged_host(*where, look=look)
        if why:
            return why
    return _written_refusal(text, look)


# -- every address written in a text --------------------------------------------------

#: A scheme's characters (RFC 3986), and at most how long one is.
_SCHEME_CHARS = frozenset(string.ascii_letters + string.digits + "+.-")
_SCHEME_MAX = 32
#: What a host, a login and a port are written with (RFC 3986's authority).
_AUTHORITY = frozenset(string.ascii_letters + string.digits + "-._~%!$&()*+,;=:@[]")
#: Where an authority ends; one longer than _AUTHORITY_MAX is not read.
_AUTHORITY_ENDS = frozenset("/?#")
_AUTHORITY_MAX = 1024
#: At most this many addresses inside one stream's address.
_INNER_MAX = 16


def _cut(text: str) -> str:
    """*text* up to its first character no authority is written with."""
    for i, ch in enumerate(text):
        if ch not in _AUTHORITY:
            return text[:i]
    return text


def _written(text: str) -> Iterator[tuple[str, int] | None]:
    """(host, port) of every address written in *text* - wherever a
    "scheme://" begins - or None for one that cannot be read. An authority
    with a character no host is written with is read both ways its readers
    may: cut there, and after its last "@" (``nas"@cdn.example.com``)."""
    i = text.find("://")
    while i != -1:
        j = i
        while j > 0 and i - j < _SCHEME_MAX and text[j - 1] in _SCHEME_CHARS:
            j -= 1
        while j < i and text[j] not in string.ascii_letters:
            j += 1
        if j < i:
            start = k = i + 3
            end = min(len(text), start + _AUTHORITY_MAX + 1)
            while k < end and text[k] not in _AUTHORITY_ENDS and not text[k].isspace():
                k += 1
            if k - start > _AUTHORITY_MAX:
                yield None
            else:
                raw = text[start:k]
                scheme = text[j:i].lower()
                for auth in {_cut(raw).rpartition("@")[2], _cut(raw.rpartition("@")[2])}:
                    yield _host_port(f"{scheme}://{auth}/")
        i = text.find("://", i + 3)


def _written_refusal(text: str, look: bool, most: int = _INNER_MAX) -> str:
    """Why an address written in *text* may not be opened: the first one
    refused, or *text* names more than *most* (it is refused)."""
    seen: dict[tuple[str, int], str] = {}
    for n, where in enumerate(_written(text)):
        if where is None or n >= 2 * most:
            return INTERNET
        if not where[0]:
            continue                    # no host: a socket listening here
        why = seen.get(where)
        if why is None:
            why = seen[where] = _judged_host(*where, look=look)
        if why:
            return why
    return ""


# -- a playlist on this computer -------------------------------------------------------

#: A file mpv or FFmpeg reads as a list of addresses - a playlist or a
#: stream's manifest - by its name...
LIST_ENDINGS = (".m3u", ".m3u8", ".pls", ".xspf", ".asx", ".wax", ".wvx", ".wpl", ".smil",
                ".mpd", ".edl", ".cue", ".ffconcat")
#: ...or by its first characters (after a byte order mark and blank space).
_LIST_HEADS = ("#extm3u", "[playlist]", "[reference]", "<?xml", "<asx", "<smil", "<mpd",
               "<playlist", "# mpv edl", "ffconcat", "rtsptext")
#: How much of one is read to judge it - a longer one is refused - and of
#: any other file to tell.
LIST_MAX = 1 << 20
_HEAD = 512
#: At most this many addresses judged in one list.
_LIST_ADDRESSES = 50_000
#: A list's marks between its words, for the names it lists.
_APART = str.maketrans(dict.fromkeys("\"'<>=,|()", " "))
_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)


def file_refusal(path: str) -> str:
    """Why the file at *path* may not be played now - "" when it may:
    always while offline mode is off; while it is on, a playlist or a
    stream's manifest that names an address the guard refuses (the
    module's docstring). Never raises."""
    if _mode == OFF:
        return ""
    return _list_refusal(path)


def _text_of(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    return data.decode("utf-8-sig", "replace")


def _list_refusal(path: str) -> str:
    try:
        return _read_list(str(path or ""))
    except Exception:
        return INTERNET


def _read_list(path: str) -> str:
    if not path:
        return ""
    try:
        fd = os.open(path, _OPEN_FLAGS)
    except (OSError, ValueError):
        return ""                       # not there, not readable: nothing for anyone
    try:
        regular = stat.S_ISREG(os.fstat(fd).st_mode)
    except OSError:
        regular = False
    if not regular:
        os.close(fd)
        return ""                       # a folder, a pipe, a device: never waited on
    with os.fdopen(fd, "rb") as fh:
        try:
            data = fh.read(_HEAD)
            if not (path.lower().endswith(LIST_ENDINGS)
                    or _text_of(data).lstrip().lower().startswith(_LIST_HEADS)):
                return ""               # a media file: this computer's
            data += fh.read(LIST_MAX + 1 - len(data))
        except OSError:
            return ""
    if len(data) > LIST_MAX:
        return INTERNET                 # too long to judge: not opened offline
    return _names_refusal(_text_of(data))


def _names_refusal(text: str) -> str:
    """Why a list's *text* may not be played offline: an address it names
    refused, a network path to a host on the internet, another list on this
    computer (not looked into), an address XML may spell otherwise."""
    low = text.lower()
    if low.lstrip().startswith("<") and ("&#" in low or "<!entity" in low or "<!doctype" in low):
        return INTERNET
    for word in low.translate(_APART).split():
        bare = word.split("?", 1)[0].split("#", 1)[0]
        if bare.endswith(LIST_ENDINGS) and ("://" not in bare or bare.startswith("file:")):
            return INTERNET
        if word.startswith(("//", "\\\\")) and not word.startswith(("\\\\?\\", "\\\\.\\")):
            host = _cut(word[2:].replace("\\", "/").split("/", 1)[0]).rpartition("@")[2]
            host = host.split(":", 1)[0]
            if host and _judged_host(host):
                return INTERNET         # \\host\share, //host/x: a network path
    return _written_refusal(text, True, _LIST_ADDRESSES)


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
