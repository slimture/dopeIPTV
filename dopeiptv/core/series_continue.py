"""The Series place's Continue row, worked out from what the app keeps
already - never asked of the source (no Qt).

One entry per series that is under way, the newest first: its episode to
watch next and how far it got. Where that episode comes from:

* the series page's cache holds the series' episodes (*episodes_of*): the
  next one as the page's own Continue works it out (core/series_view.py
  ``next_up``: in progress after the last watched, else the one after it);
  a series watched to its end is left out;
* else a resume point of one of its episodes (Continue watching): that
  episode, from where it got to;
* else History's last episode of it, when it was watched: that one, as
  the last watched - the next is not known without asking the source, and
  a row of series_info requests is what this module is there to avoid.

Pure: the window passes its stores' rows and two small callables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from . import series_view as sv

#: At most this many series in the row.
LIMIT = 12

#: "S2 * E5 - Title", "Show · S02 * E05", "S1E3": a History row's season
#: and episode, which it keeps only in its name.
_SE = re.compile(r"\bS(\d{1,3})\s*\*?\s*E(\d{1,4})\b", re.IGNORECASE)


@dataclass
class Entry:
    """One series under way: *episode* is the one to watch next (*known*:
    worked out from its episodes; else its resume point's or the last one
    watched), *progress* how far it got, *resume* when it is in progress
    (Continue starts it there, without the question), *watched* when it is
    the last one watched (its page says what is next); *seasons* the
    series' seasons when known (autoplay's queue), *at* the newest moment
    it was watched (Unix time)."""
    series: dict
    episode: dict
    progress: sv.Progress = field(default_factory=sv.Progress)
    resume: bool = False
    known: bool = False
    watched: bool = False
    seasons: list = field(default_factory=list)
    at: float = 0.0

    @property
    def series_id(self) -> str:
        return str(self.series.get("series_id"))

    def season_episode(self) -> tuple[int | None, int | None]:
        return se_of(self.episode)


def se_of(ep: dict) -> tuple[int | None, int | None]:
    """An episode's (season, number): its own fields, else read from its
    name (a History row keeps them only there)."""
    season = sv.season_of(ep.get("season"))
    number = sv.episode_number(ep)
    if season is not None and number is not None:
        return season, number
    m = _SE.search(str(ep.get("name") or ep.get("title") or ""))
    if m:
        return int(m.group(1)), int(m.group(2))
    return season, number


def _when(row: dict) -> float:
    """When a resume point was kept or a History row played (Unix time)."""
    ts = row.get("_ts")
    try:
        if ts:
            return float(ts)
    except (TypeError, ValueError):
        pass
    wa = row.get("_watched_at")
    if wa:
        try:
            return datetime.fromisoformat(str(wa)).timestamp()
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def own_episode(row: dict) -> dict:
    """A Continue-watching row as the episode it is: the series' name it
    put in front of the episode's own taken off again (kept with a new
    resume point, the row would say it twice next time)."""
    ep = dict(row)
    ctx = row.get("_series_ctx") or {}
    sname = str(ctx.get("name") or ctx.get("title") or "")
    name = str(ep.get("name") or "")
    lead = f"{sname} · "
    if sname and name.startswith(lead):
        ep["name"] = name[len(lead):]
    return ep


def gather(resume_rows: list[dict], history_rows: list[dict], *,
           episodes_of: Callable[[dict], list[dict] | None],
           state_of: Callable[[dict, dict], tuple[bool, sv.Progress]],
           keep: Callable[[dict], bool] = lambda _s: True,
           limit: int = LIMIT) -> list[Entry]:
    """The row's entries, the newest first. *resume_rows*: Continue
    watching's (ResumeStore.continue_watching, with ``_series_ctx``);
    *history_rows*: History's entries; *episodes_of(series)*: the series'
    episodes when the app holds them already, else None; *state_of(ep,
    series)*: (watched, how far) of one episode; *keep(series)*: whether
    the profile in use may see the series (hidden, locked, a kids
    profile)."""
    found: dict[str, dict[str, Any]] = {}
    for row in resume_rows or []:
        if not isinstance(row, dict) or row.get("_kind", "episode") != "episode":
            continue
        ctx = row.get("_series_ctx") or {}
        sid = ctx.get("series_id")
        if sid is None:
            continue
        got = found.setdefault(str(sid), {"series": ctx, "resume": None, "hist": None,
                                          "at": 0.0})
        when = _when(row)
        if got["resume"] is None or when > _when(got["resume"]):
            got["resume"] = row
        got["at"] = max(got["at"], when)
    for row in history_rows or []:
        if not isinstance(row, dict) or row.get("_kind") != "episode":
            continue
        ctx = row.get("_series_ctx") or {}
        sid = ctx.get("series_id")
        if sid is None or row.get("_key") is None:
            continue
        got = found.setdefault(str(sid), {"series": ctx, "resume": None, "hist": None,
                                          "at": 0.0})
        when = _when(row)
        if got["hist"] is None or when > _when(got["hist"]):
            got["hist"] = row
        got["at"] = max(got["at"], when)
    out: list[Entry] = []
    for got in sorted(found.values(), key=lambda g: g["at"], reverse=True):
        if len(out) >= limit:
            break
        series = dict(got["series"])
        if not keep(series):
            continue
        entry = _entry(series, got, episodes_of, state_of)
        if entry is not None:
            out.append(entry)
    return out


def _entry(series: dict, got: dict, episodes_of, state_of) -> Entry | None:
    at = float(got["at"])
    eps = episodes_of(series)
    if eps:
        seasons = sv.seasons(eps)
        order = sv.flow(seasons)
        if order:
            states = {id(ep): state_of(ep, series) for ep in order}
            if all(states[id(ep)][0] for ep in order):
                return None                     # watched to its end
            nxt = sv.next_up(order, lambda e: states[id(e)][0], lambda e: states[id(e)][1])
            if nxt.episode is not None and (nxt.resume or not nxt.first):
                return Entry(series, nxt.episode, nxt.progress if nxt.resume else sv.Progress(),
                             resume=nxt.resume, known=True, seasons=seasons, at=at)
    row = got["resume"]
    if row is not None:
        try:
            pos, dur = float(row.get("_resume_pos") or 0), float(row.get("_resume_dur") or 0)
        except (TypeError, ValueError):
            pos, dur = 0.0, 0.0
        return Entry(series, own_episode(row), sv.Progress(pos, dur, int(_when(row))),
                     resume=True, at=at)
    row = got["hist"]
    if row is None:
        return None
    season, number = se_of(row)
    probe = {"id": row.get("_key"), "season": season, "episode_num": number,
             "name": row.get("name"), "stream_icon": row.get("stream_icon"),
             "_url": row.get("_url"), "_series_ctx": row.get("_series_ctx")}
    watched, prog = state_of(probe, series)
    if prog.started:
        return Entry(series, probe, prog, resume=True, at=at)
    if watched:
        return Entry(series, probe, watched=True, at=at)
    return None                                 # barely begun: not under way
