"""A film as the Movies place and the detail column show it, no Qt (the
window's side is ui/mw_place_films.py and ui/mw_detail.py): its year,
length and rating from what the source says - its list row, its vod_info,
TMDB where those say nothing - whether it came in lately (New), how far it
got, and whether it goes on from where it stopped (Continue).

Pure: nothing here plays, fetches or stores.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from . import series_view as sv

#: A film the source added within this many days wears the New pill.
NEW_DAYS = 14
#: A film "added" this far ahead of the clock still counts as new (a
#: source's clock a little off), anything further is a wrong date.
_FUTURE_SLACK_S = 24 * 60 * 60

_DIGITS = re.compile(r"\d+")
_YEAR_FIELDS = ("year", "releaseDate", "release_date", "releasedate", "releaseYear")
_BRACKET_YEAR = re.compile(r"[\[(]\s*((?:19|20)\d{2})\s*[\])]")
_TAIL_YEAR = re.compile(r"\s[\-–|]\s*((?:19|20)\d{2})\s*$")


def _int(value: Any) -> int | None:
    m = _DIGITS.search(str(value or ""))
    return int(m.group(0)) if m else None


def _dicts(*values: Any) -> list[dict]:
    return [v for v in values if isinstance(v, dict)]


def key_of(row: dict) -> str:
    """A film's stable key, as the window's _item_key gives it (its
    stream id; a History-made row's own key), else its address."""
    v = row.get("stream_id") or row.get("series_id") or row.get("id") or row.get("_key")
    if v is None or v == "":
        v = row.get("_url")
    return "" if v is None else str(v)


def is_movie(row: dict) -> bool:
    """A real film row: a stream id, and no live or radio channel a
    provider mixed into its films."""
    if row.get("stream_id") is None:
        return False
    return str(row.get("stream_type") or "").lower() not in ("live", "radio", "created_live")


def year_of(row: dict, *more: Any) -> str:
    """The film's year: a field of its own (the row's, then *more*: its
    vod_info "info", TMDB's details), else one in brackets in its name
    ("Dune (2021)") or set off at its end ("Dune - 2021") - never a number
    that may be the title's own ("Wonder Woman 1984")."""
    for d in _dicts(row, *more):
        for k in _YEAR_FIELDS:
            got = sv.year_of(d.get(k))
            if got:
                return got
    name = str(row.get("name") or row.get("title") or "")
    m = _BRACKET_YEAR.search(name) or _TAIL_YEAR.search(name)
    if m:
        year = int(m.group(1))
        if year <= time.localtime().tm_year + 1:
            return str(year)
    return ""


def minutes_of(row: dict, *more: Any, dur_secs: float = 0.0) -> int:
    """The film's length in minutes (0: not known) - its vod_info's or its
    row's own (seconds, "01:52:00", "112 min"), else the length its resume
    point was kept with (*dur_secs*)."""
    for d in _dicts(*more, row):
        got = sv.minutes_of({"info": d})
        if got:
            return got
        for k in ("runtime", "episode_run_time"):
            n = _int(d.get(k))
            if n and 0 < n < 1000:
                return n
    if dur_secs and dur_secs > 0:
        return max(1, round(dur_secs / 60))
    return 0


def rating_of(row: dict, *more: Any) -> float | None:
    """A rating out of ten: the source's (its own ten, else its five
    doubled), then *more* (vod_info, TMDB)."""
    values: list[Any] = []
    for d in _dicts(row, *more):
        values.append(d.get("rating"))
        five = d.get("rating_5based")
        try:
            if five is not None and five != "" and 0 < float(five) <= 5:
                values.append(float(five) * 2)
        except (TypeError, ValueError):
            pass
    return sv.rating_of(*values)


def plot_of(*values: Any) -> str:
    for d in _dicts(*values):
        for k in ("plot", "description", "overview"):
            text = " ".join(str(d.get(k) or "").split())
            if text:
                return text
    return ""


def added_at(row: dict) -> int:
    try:
        return int(float(row.get("added") or 0))
    except (TypeError, ValueError):
        return 0


def is_new(row: dict, now: float | None = None, days: int = NEW_DAYS) -> bool:
    """Whether the source added the film within *days*."""
    at = added_at(row)
    if at <= 0:
        return False
    now = time.time() if now is None else now
    age = now - at
    return -_FUTURE_SLACK_S <= age <= days * 24 * 60 * 60


@dataclass(frozen=True)
class Primary:
    """The page's one button: Continue from *pos* (*resume*), or Play."""
    resume: bool = False
    pos: float = 0.0
    progress: sv.Progress = field(default_factory=sv.Progress)


def primary(pos: float, dur: float, minutes: int = 0) -> Primary:
    """Continue while the film is in progress - past its first minute, not
    at its end - else Play from the start. *minutes*: its length when the
    resume point kept none."""
    if dur <= 0 and minutes > 0:
        dur = minutes * 60.0
    prog = sv.Progress(max(0.0, float(pos or 0.0)), max(0.0, float(dur or 0.0)))
    if prog.started:
        return Primary(True, prog.pos, prog)
    return Primary(False, 0.0, prog)
