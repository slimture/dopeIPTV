"""Telling the person before a source's account runs out.

What the provider's own login answer says - Xtream's user_info
``exp_date``, a portal's end date as the Stalker client gives it the same
way - read once as the app logs in (no request of its own). Within
``WARN_DAYS`` of the end, or after it, the window says so - at most once a
day per source (``to_tell``, the days told kept under ``KEY``). No Qt."""
from __future__ import annotations

import datetime as _dt
import json
from typing import Any, Mapping

from .num import number

#: From how many days before the end the window says it.
WARN_DAYS = 7
#: The day each source was last told about, {source id: "YYYY-MM-DD"}
#: (the household's: the account is the household's).
KEY = "account_expiry_told"
#: An end further away than this is none a provider means (a placeholder
#: year 2100 or a millisecond time) - nothing is said of it.
_LATEST = 4102444800          # 2100-01-01


def expires_at(user_info: Any) -> float | None:
    """When the account ends, as a Unix time - None when the answer says
    no end (none, 0, "null", unlimited) or nothing that reads as one."""
    if not isinstance(user_info, Mapping):
        return None
    when = number(user_info.get("exp_date"))
    if when is None or when <= 0 or when >= _LATEST:
        return None
    return float(when)


def days_left(ends: float, now: float) -> int:
    """Calendar days from today to the end's day, in local time: 0 on the
    day itself, below 0 after it."""
    return (_dt.date.fromtimestamp(ends) - _dt.date.fromtimestamp(now)).days


def to_tell(ends: float | None, now: float, told: str | None) -> str | None:
    """What to say now: "past" (it ended), "today", "soon" (within
    WARN_DAYS) - or None: further off, no end, or said today already
    (*told*: the day it was last said, "YYYY-MM-DD")."""
    if ends is None:
        return None
    days = days_left(ends, now)
    if days > WARN_DAYS:
        return None
    if told == today(now):
        return None
    return "past" if days < 0 else "today" if days == 0 else "soon"


def today(now: float) -> str:
    return _dt.date.fromtimestamp(now).isoformat()


def read_told(raw: Any) -> dict[str, str]:
    """The days told, from the settings' value - anything else none."""
    try:
        data = json.loads(raw) if isinstance(raw, str) and raw else {}
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k)[:64]: str(v)[:10] for k, v in list(data.items())[:256]
            if isinstance(v, str)}


def write_told(told: Mapping[str, str], source: str, now: float) -> str:
    """The settings' value with *source* told today; the oldest dropped
    past 256 sources."""
    kept = dict(told)
    kept.pop(source, None)
    kept[source] = today(now)
    while len(kept) > 256:
        kept.pop(next(iter(kept)))
    return json.dumps(kept, sort_keys=False)
