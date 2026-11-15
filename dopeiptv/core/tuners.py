"""An Enigma2 box's tuners: which transponder a channel is on, how many
tuners the box has, what it records and shows on its TV now - and whether
one more channel can be watched without cutting either off.

Channels on one transponder share a tuner: their service references
(``1:0:19:SID:TSID:ONID:NS:0:0:0:``) carry the same TSID, ONID and
namespace. An IPTV entry (4097, 5001, 5002...) or a reference that carries
another address needs none - except OSCam's stream relay on the box itself
(``http://127.0.0.1:17999/<reference>``), which is the channel it relays.
The box says its tuners in ``/web/about`` (``e2nim`` from OpenWebif and the
Dreambox WebInterface, or ``e2frontend``), its timers in ``/web/timerlist``
(running recordings: ``e2state`` 2, not a zap timer, not disabled) and what
its TV shows in ``/web/getcurrent``. The app's own streams from the box -
the player, its rewind buffer, multiview cells, recordings over their own
connection, a cast - are core/connections.py's count, each with the
address it plays.

It only says: nothing here stops a stream. A box that does not say how
many tuners it has says nothing at all.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import unquote, urlsplit

#: What a verdict says: free to watch, it takes the last free tuner, or it
#: would cut something off.
FREE, LAST, CUTS = "free", "last", "cuts"
#: What holds a tuner besides the app's own kinds (core/connections.KINDS):
#: the box's TV, and a recording (the box's own, or the app's).
TV, RECORDING = "tv", "recording"
# What gives way first when no tuner is free: the TV, then a stream of the
# app's own, then a recording.
_WEIGHT = {TV: 1, RECORDING: 3}
_OWN_WEIGHT = 2
#: A box's state older than this is asked again (in the background).
ASK_SECS = 5.0
#: Older than this it says nothing.
USE_SECS = 60.0
#: After the box did not answer, not asked again for this long.
RETRY_SECS = 20.0
_LOOPBACK = ("127.0.0.1", "localhost", "::1")


@dataclass(frozen=True)
class Tune:
    """One thing holding a tuner: *what* (TV, RECORDING or one of the app's
    own kinds), its *name* (a channel's, a recording's; "" when not
    known) and the transponder it is on (None: no tuner)."""

    what: str
    name: str = ""
    key: str | None = None


@dataclass(frozen=True)
class BoxState:
    """What the box said: its tuners (None: not said), what its TV shows
    and what it records now - each only where it holds a tuner."""

    tuners: int | None
    tv: Tune | None = None
    recordings: tuple[Tune, ...] = ()

    def users(self, own: Iterable[Tune] = ()) -> list[Tune]:
        """Everything holding a tuner: the TV, the recordings, then the
        app's own *own*."""
        found = ([self.tv] if self.tv is not None else []) + list(self.recordings) + list(own)
        return [t for t in found if t.key]


@dataclass(frozen=True)
class Verdict:
    """One more channel on the box: *kind* FREE, LAST or CUTS; *tuners* the
    box has, *in_use* now, and what it would *cut* off."""

    kind: str
    tuners: int
    in_use: int
    cut: tuple[Tune, ...] = ()

    @property
    def cuts_recording(self) -> bool:
        return any(t.what == RECORDING for t in self.cut)

    @property
    def cuts_tv(self) -> bool:
        return any(t.what == TV for t in self.cut)


# -- service references ------------------------------------------------------------

def _decoded(text: str) -> str:
    """*text* with its percent-escapes undone, however often it was quoted
    (OpenWebif quotes what was quoted already)."""
    for _ in range(3):
        plain = unquote(text)
        if plain == text:
            break
        text = plain
    return text


def transponder(sref: str | None, _depth: int = 0) -> str | None:
    """The transponder a service reference is on, "TSID:ONID:NS" (hex, as
    Enigma2 writes them) - None for an IPTV entry, a file, a bouquet, a
    marker, or anything that is not a DVB service."""
    f = (sref or "").strip().split(":")
    if len(f) < 7:
        return None
    rest = _decoded(":".join(f[10:])) if len(f) > 10 else ""
    if "://" in rest:
        # An address of its own: no tuner - unless it is the box's own
        # stream relay (or stream port) on its loopback, which tunes the
        # channel it serves.
        try:
            u = urlsplit(rest)
            host = (u.hostname or "").lower()
        except ValueError:
            return None
        if _depth or host not in _LOOPBACK:
            return None
        return transponder(u.path.lstrip("/"), _depth + 1)
    try:
        kind, flags = int(f[0]), int(f[1] or "0")
        tsid, onid, ns = (int(x or "0", 16) for x in f[4:7])
    except ValueError:
        return None
    if kind != 1 or flags & 7 == 7 or not (tsid or onid or ns) or rest.startswith("/"):
        return None                     # not a DVB service, or a file on the box's disk
    return f"{tsid:X}:{onid:X}:{ns:X}"


def stream_ref(url: str | None) -> str:
    """The service reference in the address of a stream from the box (its
    stream port, its encoder's): the path, unquoted - "" when there is
    none."""
    try:
        path = urlsplit(url or "").path
    except ValueError:
        return ""
    return _decoded(path.lstrip("/"))


def stream_key(url: str | None) -> str | None:
    """The transponder of what a stream from the box plays."""
    return transponder(stream_ref(url))


# -- what the box says ------------------------------------------------------------

def _text(el: ET.Element | None, *tags: str) -> str:
    for tag in tags:
        found = el.find(tag) if el is not None else None
        if found is not None and (found.text or "").strip():
            return " ".join((found.text or "").split())
    return ""


def tuner_names(about: ET.Element | None) -> list[str]:
    """The tuners /web/about lists: OpenWebif's and the Dreambox
    WebInterface's ``e2nim`` (name), else ``e2frontend`` (e2name / name);
    one without a name is "Tuner N"."""
    if about is None:
        return []
    found = list(about.iter("e2nim")) or list(about.iter("e2frontend"))
    return [_text(n, "name", "e2name") or f"Tuner {i + 1}" for i, n in enumerate(found)]


def running_recordings(timerlist: ET.Element | None) -> tuple[Tune, ...]:
    """The timers that record now (/web/timerlist): running (e2state 2),
    not a zap timer, not disabled - each with its name and transponder."""
    out: list[Tune] = []
    for t in (timerlist.iter("e2timer") if timerlist is not None else []):
        if (_text(t, "e2state") != "2" or _text(t, "e2justplay") == "1"
                or _text(t, "e2disabled") == "1"):
            continue
        key = transponder(_text(t, "e2servicereference"))
        if key:
            out.append(Tune(RECORDING, _text(t, "e2name") or _text(t, "e2servicename"), key))
    return tuple(out)


def on_tv(current: ET.Element | None) -> Tune | None:
    """What the box shows on its TV (/web/getcurrent), where that holds a
    tuner - None in standby, for an IPTV channel or a recording played."""
    if current is None:
        return None
    svc = current if current.tag == "e2service" else current.find(".//e2service")
    key = transponder(_text(svc, "e2servicereference"))
    return Tune(TV, _text(svc, "e2servicename"), key) if key else None


# -- the verdict ------------------------------------------------------------------

def groups(users: Iterable[Tune]) -> dict[str, list[Tune]]:
    """What holds a tuner, by transponder (one tuner each), as first seen."""
    out: dict[str, list[Tune]] = {}
    for t in users:
        if t.key:
            out.setdefault(t.key, []).append(t)
    return out


def _weight(users: list[Tune]) -> int:
    return max(_WEIGHT.get(t.what, _OWN_WEIGHT) for t in users)


def verdict(state: BoxState | None, key: str | None,
            own: Iterable[Tune] = ()) -> Verdict | None:
    """One more channel on transponder *key* next to what the box does and
    the app's own *own* (as they will be: one let go first left out).
    None when the box does not say how many tuners it has."""
    if state is None or not state.tuners:
        return None
    held = groups(state.users(own))
    in_use = len(held)
    if key is None or key in held:
        return Verdict(FREE, state.tuners, in_use)
    free = state.tuners - in_use
    if free >= 2:
        return Verdict(FREE, state.tuners, in_use)
    if free == 1:
        return Verdict(LAST, state.tuners, in_use)
    # min() keeps the first of equals: the TV before a recording before
    # the app's own, in the order the box listed them.
    gives_way = min(held.values(), key=_weight)
    return Verdict(CUTS, state.tuners, in_use, tuple(gives_way))


# -- each box's state, kept a few seconds ------------------------------------------------

class StateCache:
    """Each box's last state, by source id: used while it is at most
    USE_SECS old, asked again once it is ASK_SECS old - never twice at a
    time, and not again for RETRY_SECS after the box did not answer."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._got: dict[str, tuple[BoxState, float]] = {}
        self._asking: set[str] = set()
        self._failed: dict[str, float] = {}

    def state(self, source: str) -> BoxState | None:
        got = self._got.get(source)
        if got is None or self._clock() - got[1] > USE_SECS:
            return None
        return got[0]

    def due(self, source: str, every: float = ASK_SECS) -> bool:
        """Whether to ask the box again now."""
        if source in self._asking:
            return False
        now = self._clock()
        failed = self._failed.get(source)
        if failed is not None and now - failed < RETRY_SECS:
            return False
        got = self._got.get(source)
        return got is None or now - got[1] >= every

    def asking(self, source: str) -> None:
        self._asking.add(source)

    def is_asking(self, source: str) -> bool:
        return source in self._asking

    def put(self, source: str, state: BoxState) -> None:
        self._asking.discard(source)
        self._failed.pop(source, None)
        self._got[source] = (state, self._clock())

    def failed(self, source: str) -> None:
        self._asking.discard(source)
        self._failed[source] = self._clock()

    def did_not_answer(self, source: str) -> bool:
        return source in self._failed

    def forget(self, source: str) -> None:
        self._got.pop(source, None)
        self._asking.discard(source)
        self._failed.pop(source, None)
