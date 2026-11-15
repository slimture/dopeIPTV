"""Your viewing statistics: how much was watched, when, and what most -
kept on this computer only, never sent anywhere.

The history (HistoryStore) keeps one row per title with the last time it
played, and the resume positions how far into a film or episode one got;
neither says for how long anything was watched. So the window counts the
time a stream actually plays (not while paused) into a small log of its
own, beside the history and under the same rules: private viewing adds
nothing (``ViewingLog.add`` checks it itself, as HistoryStore.add does),
Clear recent history clears it for the same range (core/privacy.py), and
it is not one of the settings that sync between computers (core/sync.py).
A backup, the user's own file, takes it with the rest. The first time the
log is made it takes what the resume positions already know - a film or
episode watched up to its saved position, at the hour it was saved - so
someone who has used the app a while does not start from nothing.

One settings value (``KEY``), JSON: per title its name, its kind and the
seconds watched in each hour of the day it was watched (local time,
"YYYYMMDDHH"). Everything is summed from that: this week, month and year,
by day of the week, by hour, the top channels, films and series, the
longest streak of days. ``RETAIN_DAYS`` are kept; titles past
``MAX_TITLES`` go, least recently watched first.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, NamedTuple, Protocol

from . import privacy

KEY = "viewing_stats"
FORMAT = 1
RETAIN_DAYS = 400
MAX_TITLES = 2000
#: A day counts toward a streak with at least this much watched.
STREAK_MIN_S = 60
#: How many a top list shows.
TOP_N = 5

CHANNEL = "live"
FILM = "movie"
SERIES = "series"
OTHER = "other"
KINDS = (CHANNEL, FILM, SERIES, OTHER)

#: The periods the window offers.
WEEK, MONTH, YEAR, ALL = "week", "month", "year", "all"
PERIODS = (WEEK, MONTH, YEAR, ALL)


class Settings(Protocol):
    def value(self, key: str, default: Any = None) -> Any: ...

    def setValue(self, key: str, value: Any) -> None: ...  # noqa: N802 - Qt's name


def kind_of(play_kind: str) -> str:
    """The statistics' kind for what the window plays: a channel (its
    catch-up too), a film, an episode's series, or anything else (files,
    recordings)."""
    return {"live": CHANNEL, "catchup": CHANNEL, "movie": FILM, "vod": FILM,
            "episode": SERIES, "series": SERIES}.get(play_kind or "", OTHER)


def ident(kind: str, source: str, key: Any) -> str:
    """A title's id in the log: its kind, its source and its key there."""
    return f"{kind}:{source or ''}:{key}"


def _hour_key(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%Y%m%d%H")


def _hour_start(t: float) -> float:
    d = datetime.fromtimestamp(t)
    return d.replace(minute=0, second=0, microsecond=0).timestamp()


def split_hours(start: float, end: float) -> list[tuple[str, float]]:
    """[start, end] cut at the clock's hours (local time): each hour's key
    and the seconds of it inside."""
    out: list[tuple[str, float]] = []
    t = start
    while t < end:
        nxt = _hour_start(t) + 3600
        if nxt <= t:            # a clock change at this very hour
            nxt = t + 3600
        step = min(end, nxt) - t
        if step > 0:
            key = _hour_key(t)
            if out and out[-1][0] == key:
                out[-1] = (key, out[-1][1] + step)
            else:
                out.append((key, step))
        t = min(end, nxt)
    return out


def _parse_hour(key: str) -> tuple[date, int] | None:
    try:
        return date(int(key[0:4]), int(key[4:6]), int(key[6:8])), int(key[8:10])
    except (ValueError, IndexError):
        return None


class ViewingLog:
    """The seconds watched, per title and hour, in the app's settings."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        try:
            raw = settings.value(KEY, None)
            data = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            data = None
        #: Made just now (nothing stored, or nothing readable): seed() fills it.
        self.fresh = not isinstance(data, dict)
        if not isinstance(data, dict):
            data = {}
        titles = data.get("t")
        self.titles: dict[str, dict] = {}
        for tid, t in (titles.items() if isinstance(titles, dict) else []):
            if not isinstance(t, dict) or not isinstance(t.get("h"), dict):
                continue
            hours = {str(k): float(v) for k, v in t["h"].items()
                     if isinstance(v, (int, float)) and v > 0 and _parse_hour(str(k))}
            self.titles[str(tid)] = {"n": str(t.get("n") or ""),
                                     "k": t.get("k") if t.get("k") in KINDS else OTHER,
                                     "h": hours}
        try:
            self.since = float(data.get("since") or 0)
        except (TypeError, ValueError):
            self.since = 0.0
        self._dirty = False

    # -- writing ---------------------------------------------------------

    def add(self, tid: str, name: str, kind: str, seconds: float,
            end: float | None = None) -> None:
        """*seconds* watched of the title *tid*, up to *end* (now), put in
        the hours they fell in. Nothing while private viewing is on."""
        if privacy.private():
            return
        self._put(tid, name, kind, seconds, time.time() if end is None else end)

    def _put(self, tid: str, name: str, kind: str, seconds: float, end: float) -> None:
        if seconds <= 0 or not tid:
            return
        t = self.titles.setdefault(tid, {"n": name or "", "k": kind if kind in KINDS else OTHER,
                                         "h": {}})
        if name:
            t["n"] = name
        for key, secs in split_hours(end - seconds, end):
            t["h"][key] = t["h"].get(key, 0.0) + secs
        if not self.since or end - seconds < self.since:
            self.since = end - seconds
        self._dirty = True

    def seed(self, positions: Iterable[tuple[str, str, str, float, float]]) -> int:
        """A new log takes what is known already: (id, name, kind, seconds
        watched, when) - a film or episode up to its saved position, at the
        time it was saved. Once, and only into a log made just now."""
        if not self.fresh:
            return 0
        self.fresh = False
        n = 0
        for tid, name, kind, secs, when in positions:
            if secs > 0 and when > 0:
                # What was kept already: not new viewing, private or not.
                self._put(tid, name, kind, float(secs), float(when))
                n += 1
        self._dirty = True
        return n

    def clear(self, since: float | None, kinds: Iterable[str] | None = None) -> int:
        """Take out what was watched since *since* (all of it for None), of
        *kinds* only when given: whole hours, the one *since* falls in
        included. Returns how many titles lost time."""
        want = set(kinds) if kinds is not None else None
        cut = _hour_key(_hour_start(since)) if since is not None else ""
        hit = 0
        for tid in list(self.titles):
            t = self.titles[tid]
            if want is not None and t["k"] not in want:
                continue
            keep = {k: v for k, v in t["h"].items() if cut and k < cut}
            if len(keep) != len(t["h"]):
                hit += 1
                t["h"] = keep
                if not keep:
                    del self.titles[tid]
        if hit:
            self._dirty = True
            if not self.titles:
                self.since = 0.0
        return hit

    def prune(self, now: float | None = None) -> None:
        """Older than RETAIN_DAYS goes; past MAX_TITLES, the least recent."""
        now = time.time() if now is None else now
        cut = _hour_key(now - RETAIN_DAYS * 86400)
        for tid in list(self.titles):
            t = self.titles[tid]
            keep = {k: v for k, v in t["h"].items() if k >= cut}
            if len(keep) != len(t["h"]):
                self._dirty = True
                t["h"] = keep
            if not keep:
                del self.titles[tid]
        if len(self.titles) > MAX_TITLES:
            order = sorted(self.titles, key=lambda tid: max(self.titles[tid]["h"]), reverse=True)
            for tid in order[MAX_TITLES:]:
                del self.titles[tid]
            self._dirty = True

    def save(self, force: bool = False) -> None:
        if not (self._dirty or force):
            return
        self.prune()
        self._settings.setValue(KEY, json.dumps(
            {"v": FORMAT, "since": int(self.since), "t": self.titles},
            separators=(",", ":"), ensure_ascii=False))
        self._dirty = False

    def empty(self) -> bool:
        return not any(t["h"] for t in self.titles.values())

    def rows(self) -> Iterator["Row"]:
        for tid, t in self.titles.items():
            for key, secs in t["h"].items():
                at = _parse_hour(key)
                if at is not None:
                    yield Row(tid, t["n"], t["k"], at[0], at[1], secs)


class Row(NamedTuple):
    tid: str
    name: str
    kind: str
    day: date
    hour: int
    secs: float


# -- what the window shows ------------------------------------------------------


def period_start(period: str, today: date, week_start: int = 0) -> date | None:
    """The first day of *period* that has *today* in it (None for all
    time). *week_start*: the locale's first day, 0 = Monday ... 6 = Sunday."""
    if period == WEEK:
        return today - timedelta(days=(today.weekday() - week_start) % 7)
    if period == MONTH:
        return today.replace(day=1)
    if period == YEAR:
        return today.replace(month=1, day=1)
    return None


@dataclass
class Summary:
    """Everything the statistics window shows."""
    totals: dict[str, float] = field(default_factory=dict)
    #: For the period chosen: seconds by weekday (Monday first) and hour.
    by_weekday: list[float] = field(default_factory=lambda: [0.0] * 7)
    by_hour: list[float] = field(default_factory=lambda: [0.0] * 24)
    #: (name, seconds) per kind, most first.
    top: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    longest: int = 0
    longest_end: date | None = None
    current: int = 0
    days: int = 0
    first: date | None = None

    @property
    def empty(self) -> bool:
        return self.first is None


def summarize(rows: Iterable[Row], today: date, period: str = WEEK,
              week_start: int = 0, top_n: int = TOP_N) -> Summary:
    """The statistics of *rows* as of *today*: the totals of this week,
    month and year (and all of it); for *period*, the time by weekday and by
    hour, the most watched titles of each kind (one name however many
    sources carry it); the longest streak of days and the one going now
    (it may end yesterday: today is not over)."""
    starts = {p: period_start(p, today, week_start) for p in PERIODS}
    out = Summary(totals=dict.fromkeys(PERIODS, 0.0))
    lo = starts.get(period)
    per_day: dict[date, float] = {}
    by_name: dict[tuple[str, str], dict[str, float]] = {}
    for r in rows:
        if r.day > today:
            continue
        per_day[r.day] = per_day.get(r.day, 0.0) + r.secs
        for p, start in starts.items():
            if start is None or r.day >= start:
                out.totals[p] += r.secs
        if lo is not None and r.day < lo:
            continue
        out.by_weekday[r.day.weekday()] += r.secs
        out.by_hour[r.hour] += r.secs
        if r.kind in (CHANNEL, FILM, SERIES) and r.name.strip():
            names = by_name.setdefault((r.kind, " ".join(r.name.split()).casefold()), {})
            names[r.name] = names.get(r.name, 0.0) + r.secs
    for kind in (CHANNEL, FILM, SERIES):
        ranked = [(max(names, key=lambda n: names[n]), sum(names.values()))
                  for (k, _norm), names in by_name.items() if k == kind]
        ranked.sort(key=lambda x: (-x[1], x[0].casefold()))
        out.top[kind] = ranked[:top_n]
    days = sorted(d for d, s in per_day.items() if s >= STREAK_MIN_S)
    out.days = len(days)
    out.first = min(per_day) if per_day else None
    run, prev = 0, None
    for d in days:
        run = run + 1 if prev is not None and d - prev == timedelta(days=1) else 1
        if run > out.longest:
            out.longest, out.longest_end = run, d
        prev = d
    if days and (today - days[-1]) <= timedelta(days=1):
        n, d = 0, days[-1]
        have = set(days)
        while d in have:
            n += 1
            d -= timedelta(days=1)
        out.current = n
    return out


def resume_seed(data: Any, source: str = "") -> list[tuple[str, str, str, float, float]]:
    """What a source's resume positions (ResumeStore's JSON) say was
    watched: for ViewingLog.seed. An episode goes to its series."""
    out: list[tuple[str, str, str, float, float]] = []
    if not isinstance(data, dict):
        return out
    for rkey, v in data.items():
        if not isinstance(v, dict):
            continue
        group, _, key = str(rkey).partition(":")
        got = v.get("item")
        item: dict = got if isinstance(got, dict) else {}
        try:
            pos, when = float(v.get("pos") or 0), float(v.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if group == "vod":
            name = str(item.get("name") or item.get("title") or "")
            out.append((ident(FILM, source, key), name, FILM, pos, when))
        elif group == "episode":
            got = v.get("ctx")
            ctx: dict = got if isinstance(got, dict) else {}
            name = str(ctx.get("name") or ctx.get("title") or item.get("name") or "")
            sid = ctx.get("series_id") or name or key
            out.append((ident(SERIES, source, sid), name, SERIES, pos, when))
    return out
