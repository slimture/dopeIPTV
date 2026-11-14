"""Stream health: how each live channel has been starting and playing.

Part of Nerd, and off until both Nerd and its own switch (``nerd_health``)
are on: with either off nothing is recorded. Per channel - the source's id
and the stream's id - it keeps how often the channel was started, how
often it did not play (never showed a picture, or the reconnects gave up),
how long the picture took (the last time and on average), the drops and
reconnects while it played, the last error (scrubbed of addresses and
logins as an error report is) and when it last played.

It only watches. The window tells it what happened (``begin``, ``frame``,
``drop``, ``fail``); nothing in playback asks it anything, so how a stream
plays never depends on it. What it keeps is one JSON value in the app's
settings, at most ``CAP`` channels (those played longest ago go first), and
nothing at all during private viewing.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Protocol

from . import nerd, privacy
from .report import scrub

SWITCH = "nerd_health"
STORE_KEY = "nerd_health_data"
CAP = 500
MAX_ERROR = 240
MAX_NAME = 120
#: An average start this slow, in seconds, is worth a look.
SLOW_START = 5.0


class Settings(Protocol):
    def value(self, key: str, default: Any = None) -> Any: ...

    def setValue(self, key: str, value: Any) -> None: ...  # noqa: N802 - Qt's name


def enabled(n: nerd.Nerd | None = None) -> bool:
    """Nerd on, and its stream-health switch on."""
    n = n or nerd.current()
    return n.on and n.flag(SWITCH)


def channel_key(source: object, stream: object) -> str:
    """A channel wherever it is listed: its source's id and its stream's id."""
    return f"{source or ''}:{stream}"


def clean_error(text: str) -> str:
    """What is kept of an error: one line, without an address, login or token."""
    return scrub(" ".join(str(text or "").split()))[:MAX_ERROR]


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _float(v: Any) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def counts(entry: dict) -> tuple[int, int, int]:
    """(starts, fails, drops) of a channel, whatever was stored."""
    return _int(entry.get("starts")), _int(entry.get("fails")), _int(entry.get("drops"))


def average(entry: dict) -> float | None:
    """The average time to the picture, in seconds; None before the first."""
    n = _int(entry.get("ttff_n"))
    return _float(entry.get("ttff_sum")) / n if n > 0 else None


def last_start(entry: dict) -> float | None:
    """The last time to the picture, in seconds; None before the first."""
    return _float(entry.get("ttff")) if _int(entry.get("ttff_n")) > 0 else None


def when(entry: dict, field: str) -> int:
    """A stored moment (a Unix time), 0 when there is none."""
    return _int(entry.get(field))


def grade(entry: dict | None) -> str:
    """"error" (the last try failed, or half of them did), "warn" (a failure,
    a slow start, or drops about once a start), "ok" (it played), or "" while
    too little is known."""
    if not entry:
        return ""
    starts, fails, drops = counts(entry)
    if entry.get("bad") or (fails and fails * 2 >= max(1, starts)):
        return "error"
    avg = average(entry)
    if fails or drops >= max(2, starts) or (avg is not None and avg >= SLOW_START):
        return "warn"
    return "ok" if avg is not None else ""


def rank(entry: dict) -> tuple[float, float, float, float]:
    """Worst first: more that did not play, a larger share of the starts,
    the slower start, the more drops."""
    fails = _int(entry.get("fails"))
    return (-fails, -fails / max(1, _int(entry.get("starts"))),
            -(average(entry) or 0.0), -_int(entry.get("drops")))


def parse(raw: Any) -> dict[str, dict]:
    """The stored value back as channels; anything else as none."""
    if not isinstance(raw, (str, bytes)) or not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def capped(entries: dict[str, dict], limit: int = CAP) -> dict[str, dict]:
    """The *limit* channels played most recently, in the order they were."""
    if len(entries) <= limit:
        return entries
    newest = sorted(entries, key=lambda k: _float(entries[k].get("at")), reverse=True)
    keep = set(newest[:max(0, limit)])
    return {k: v for k, v in entries.items() if k in keep}


class _Attempt:
    """The channel being tried now: since when, and how it has gone."""

    __slots__ = ("key", "t0", "framed", "playing", "failed")

    def __init__(self, key: str, t0: float) -> None:
        self.key = key
        self.t0 = t0
        self.framed = False     # a picture, at any time in this attempt
        self.playing = False    # a picture since the last (re)connect
        self.failed = False


class StreamHealth:
    """Every channel's numbers, and the one being tried now."""

    def __init__(self, settings: Settings | None, key: str = STORE_KEY, cap: int = CAP,
                 clock: Callable[[], float] = time.time,
                 mono: Callable[[], float] = time.monotonic,
                 on: Callable[[], bool] | None = None) -> None:
        self.settings = settings
        self.key = key
        self.cap = cap
        self._clock = clock
        self._mono = mono
        self._on = on or enabled
        self._entries: dict[str, dict] | None = None
        self._attempt: _Attempt | None = None
        self._retry = False

    def active(self) -> bool:
        """Recording now: Nerd and the switch on, and not private viewing."""
        return self._on() and not privacy.private()

    # -- what is kept ---------------------------------------------------------

    def entries(self) -> dict[str, dict]:
        if self._entries is None:
            raw = self.settings.value(self.key, "") if self.settings is not None else ""
            self._entries = parse(raw)
        return self._entries

    def entry(self, key: str) -> dict | None:
        return self.entries().get(key)

    def worst(self, n: int = 10) -> list[tuple[str, dict]]:
        return sorted(self.entries().items(), key=lambda kv: rank(kv[1]))[:n]

    def clear(self) -> None:
        """Forget every channel (the stored value goes too)."""
        self._entries = {}
        self._attempt = None
        self._retry = False
        if self.settings is None:
            return
        remove = getattr(self.settings, "remove", None)
        if callable(remove):
            remove(self.key)
        else:
            self.settings.setValue(self.key, "")

    def _touch(self, key: str) -> dict:
        return self.entries().setdefault(key, {})

    def _save(self) -> None:
        self._entries = capped(self.entries(), self.cap)
        if self.settings is not None:
            self.settings.setValue(self.key, json.dumps(
                self._entries, separators=(",", ":"), ensure_ascii=False))

    # -- the channel being tried now ------------------------------------------

    @property
    def current(self) -> str | None:
        return self._attempt.key if self._attempt is not None else None

    def expect_retry(self) -> None:
        """The next begin is the app reconnecting what it plays, not a new
        start."""
        self._retry = True

    def begin(self, key: str | None, name: str = "") -> bool:
        """The player was asked to play *key*. True when that counts as a
        start; the app reconnecting the channel being tried does not."""
        retry, self._retry = self._retry, False
        a = self._attempt
        if retry and a is not None and a.key == key:
            a.playing = False
            return False
        self._attempt = None
        if not key or not self.active():
            return False
        self._attempt = _Attempt(key, self._mono())
        e = self._touch(key)
        if name:
            e["name"] = str(name)[:MAX_NAME]
        e["starts"] = _int(e.get("starts")) + 1
        e["at"] = int(self._clock())
        self._save()
        return True

    def leave(self) -> None:
        """Nothing live is being tried any more (a film, an archive...)."""
        self._attempt = None
        self._retry = False

    def frame(self, ahead: float = 0.0) -> float | None:
        """The picture is on. The first time in an attempt that had not
        failed, the time it took (less *ahead*: how long it had already
        played when this was noticed) is kept and returned."""
        a = self._attempt
        if a is None or not self.active():
            return None
        a.playing = True
        took: float | None = None
        e = self._touch(a.key)
        if not a.framed and not a.failed:
            took = round(max(0.0, self._mono() - a.t0 - max(0.0, ahead)), 2)
            e["ttff"] = took
            e["ttff_sum"] = round(_float(e.get("ttff_sum")) + took, 2)
            e["ttff_n"] = _int(e.get("ttff_n")) + 1
        a.framed = True
        if took is not None or e.get("bad"):
            e["bad"] = False
            self._save()
        return took

    def drop(self) -> bool:
        """What played stopped (dropped, froze, ended). Once per stretch of
        play: a reconnect that has shown no picture yet does not drop
        again. True when counted."""
        a = self._attempt
        if a is None or not a.playing or not self.active():
            return False
        a.playing = False
        e = self._touch(a.key)
        e["drops"] = _int(e.get("drops")) + 1
        self._save()
        return True

    def fail(self, text: str = "", key: str | None = None) -> bool:
        """It did not play, or the reconnects gave up: counted once per
        attempt, and *text* (scrubbed) is the last error either way. *key*:
        the channel it is about when that was known earlier (a diagnosis
        that comes back later) - for any other than the one being tried,
        only its error is kept. True when counted."""
        if not self.active():
            return False
        a = self._attempt
        key = key or (a.key if a is not None else None)
        if not key:
            return False
        counted = False
        e = self.entries().get(key)
        if a is not None and a.key == key:
            e = self._touch(key)
            if not a.failed:
                a.failed = True
                a.playing = False
                e["fails"] = _int(e.get("fails")) + 1
                e["bad"] = True
                counted = True
        if e is None:
            return False
        err = clean_error(text)
        if err:
            e["err"] = err
            e["err_at"] = int(self._clock())
        if counted or err:
            self._save()
        return counted

    def stuck(self, text: str = "") -> bool:
        """The reconnects are spent on a start that never showed a picture:
        a channel that did not play. One that played and keeps dropping is
        drop()'s."""
        a = self._attempt
        if a is None or a.framed:
            return False
        return self.fail(text)
