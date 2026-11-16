"""When the app asks for a donation: one week a month, once it is used for
real - and not again for a while once it has been answered.

A *period* is ``PERIOD_DAYS`` days. One begins the first time the app runs
in a calendar month while all of this holds:

- the app is used for real: ``MIN_WATCH_S`` watched in the last
  ``WATCH_DAYS`` days (core/viewing_stats.py, where private viewing adds
  nothing), and it was first run ``FIRST_AFTER_DAYS`` days ago or more - a
  new user is first asked after about two weeks;
- no period began this month, nor in the last ``GAP_DAYS`` days (one that
  began late in a month does not run into the next one);
- nothing put it away: "Not right now" and "Hide for three months"
  (``LATER_S``), "I've donated" (``DONATED_S``, taken at the person's word -
  the app has no way to know, and does not try).

The window checks the rest: asking is switched on (Settings, General;
``asking``), there is no supporter key, and nothing would be disturbed - a
period begins only when it can be seen, and while it runs the ask is simply
not drawn in full screen, TV mode or private viewing (its days still count).

Left alone, a period ends by itself and the next month brings the next
one. The first day it is in the heart's colour (``BRIGHT``), the other days
muted (``MUTED``). Donate pressed ends the period: asked and answered for
the month.

Every key here is the household's: a donation is for the app, not for one
person, so none is one of a profile's own (core/profiles.py keeps them out
of its split), and settings sync leaves them to each computer (the
``donate_`` prefix, core/sync.py).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

DAY = 86400
#: A new user is first asked this long after the first run.
FIRST_AFTER_DAYS = 14
#: Used for real: this much watched in the last WATCH_DAYS days.
MIN_WATCH_S = 5 * 3600
WATCH_DAYS = 30
#: How long a period lasts, and how soon after one the next may begin.
PERIOD_DAYS = 7
GAP_DAYS = 21
#: The first day of a period: the heart in its colour (and Home's card).
BRIGHT_S = DAY
#: "Not right now" / "Hide for three months".
LATER_S = 90 * DAY
#: "I've donated".
DONATED_S = 365 * DAY

#: Asking switched off in Settings ("false"); on when not stored.
K_ASK = "donate_ask"
#: Put away until then (epoch seconds).
K_UNTIL = "donate_until"
#: When the current, or the last, period began.
K_PERIOD = "donate_period"
#: When that period was ended early (Donate pressed).
K_ENDED = "donate_period_end"
#: The first run (or the first thing ever watched, for someone who has used
#: the app longer than this key has existed).
K_FIRST = "donate_first_seen"
KEYS = (K_ASK, K_UNTIL, K_PERIOD, K_ENDED, K_FIRST)
#: The version before the rhythm: "I've already donated" for good, the
#: heart's and the card's own snoozes, the card's week, minutes watched.
OLD_KEYS = ("donate_thanked", "donate_heart_until", "donate_card_until",
            "donate_card_shown", "donate_watch_min")

OFF, BRIGHT, MUTED = "off", "bright", "muted"


class Settings(Protocol):
    def value(self, key: str, default: Any = None) -> Any: ...

    def setValue(self, key: str, value: Any) -> None: ...  # noqa: N802 - Qt's name

    def remove(self, key: str) -> None: ...


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _month(t: float) -> int:
    """The calendar month *t* falls in (local time), as one number."""
    d = datetime.fromtimestamp(t)
    return d.year * 12 + d.month - 1


def asking(settings: Settings) -> bool:
    """Whether asking is on (the Settings switch; on unless turned off)."""
    return str(settings.value(K_ASK, "true")).lower() != "false"


@dataclass
class Rhythm:
    """What is stored, read once: the first run, the snooze, the period."""
    first: float = 0.0
    until: float = 0.0
    period: float = 0.0
    ended: float = 0.0

    @classmethod
    def read(cls, settings: Settings) -> Rhythm:
        return cls(first=_num(settings.value(K_FIRST)),
                   until=_num(settings.value(K_UNTIL)),
                   period=_num(settings.value(K_PERIOD)),
                   ended=_num(settings.value(K_ENDED)))

    def end(self) -> float:
        """When the period that began last is over."""
        end = self.period + PERIOD_DAYS * DAY
        if self.ended >= self.period:
            end = min(end, self.ended)
        return end

    def phase(self, now: float) -> str:
        """OFF, or within a period: BRIGHT its first day, MUTED after."""
        if not self.period or now < self.period or now < self.until:
            return OFF
        if now >= self.end():
            return OFF
        return BRIGHT if now - self.period < BRIGHT_S else MUTED

    def may_begin(self, now: float, watched: Callable[[], float]) -> bool:
        """Whether a new period may begin at *now*. *watched* (the seconds
        watched in the last WATCH_DAYS days) is asked last, only when
        everything else allows one."""
        if not self.first or now < self.first + FIRST_AFTER_DAYS * DAY:
            return False
        if now < self.until:
            return False
        if self.period:
            if now < self.period + GAP_DAYS * DAY:
                return False
            if _month(now) <= _month(self.period):
                return False
        return watched() >= MIN_WATCH_S


def step(settings: Settings, now: float, watched: Callable[[], float],
         can_begin: bool = True) -> str:
    """The phase at *now* - with a period begun (and stored) when one may
    and *can_begin* (it would be seen)."""
    r = Rhythm.read(settings)
    got = r.phase(now)
    if got == OFF and can_begin and r.may_begin(now, watched):
        settings.setValue(K_PERIOD, now)
        settings.remove(K_ENDED)
        return BRIGHT
    return got


def put_away(settings: Settings, now: float, secs: float) -> None:
    """Not asked again for *secs* ("Not right now", "I've donated"): the
    heart and Home's card alike."""
    settings.setValue(K_UNTIL, now + secs)


def end_period(settings: Settings, now: float) -> None:
    """Donate pressed: this month's asking is over."""
    r = Rhythm.read(settings)
    if r.phase(now) != OFF:
        settings.setValue(K_ENDED, now)


def migrate(settings: Settings, now: float, earliest: float = 0.0) -> None:
    """At every start, once each: the keys of the version before the
    rhythm become its snooze (the later of the heart's and the card's;
    "I've already donated", which was for good, a year from now), and the
    first run is noted - at *earliest* (the first thing ever watched) when
    that was before, so someone who has used the app a while is not taken
    for new."""
    watched_before = False
    old = {k: settings.value(k) for k in OLD_KEYS}
    if any(v is not None for v in old.values()):
        until = _num(settings.value(K_UNTIL))
        if str(old["donate_thanked"]).lower() == "true":
            until = max(until, now + DONATED_S)
        for k in ("donate_heart_until", "donate_card_until"):
            until = max(until, _num(old[k]))
        if until > now and until > _num(settings.value(K_UNTIL)):
            settings.setValue(K_UNTIL, until)
        watched_before = _num(old["donate_watch_min"]) > 0
        for k in OLD_KEYS:
            settings.remove(k)
    if not _num(settings.value(K_FIRST)):
        first = now
        if earliest > 0:
            first = min(first, earliest)
        if watched_before:
            first = min(first, now - FIRST_AFTER_DAYS * DAY)
        settings.setValue(K_FIRST, first)
