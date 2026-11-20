"""A series as its page shows it (ui/series_page.py draws it,
ui/mw_series_page.py fills it): its seasons in order with Specials last,
the episode to watch next, and the short words a card needs - no Qt.

Every source hands its episodes over in the shape of Xtream's
get_series_info (Stalker portals, Jellyfin, Emby and Plex alike):
``{"info": {...}, "episodes": {"1": [...], "2": [...]}}``, an episode
``{"id", "episode_num", "title", "container_extension", "info": {"plot",
"duration", "duration_secs", "movie_image", ...}}``. A local library's
series is built into the same shape by the window.

The next episode (``next_up``): one in progress after the last one
watched (the newest of them), else the one after the last watched, else
one in progress anywhere (the newest), else the first - "Play S1 E1".
Specials are left out of that order unless they are all there is.

The season a page opens on (``opening_season``): a season the person
picked themselves, kept with the episode that was next then
(``remember``), while that is still the next one; else the next
episode's season; else the first one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .num import digits, number

#: The season number providers give their specials.
SPECIALS = 0
#: Seasons remembered at most (the oldest go first).
REMEMBER_MAX = 400

_DIGITS = re.compile(r"\d+")
_SXXEYY = re.compile(r"\bS\d{1,3}\s*[ .x_-]?\s*E\d{1,4}\b", re.IGNORECASE)
_SEP = re.compile(r"^[\s\-–—:|·*]+|[\s\-–—:|·*]+$")


def _info(ep: dict) -> dict:
    info = ep.get("info")
    return info if isinstance(info, dict) else {}


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    m = _DIGITS.search(str(value or ""))
    return digits(m.group(0)) if m else None


def season_of(value: Any) -> int | None:
    """A season's number from what a source keys it by: 1, "1", "S01",
    "Season 2"; None when there is none."""
    return _int(value)


def episode_number(ep: dict) -> int | None:
    return _int(ep.get("episode_num"))


def episode_key(ep: dict) -> str:
    """An episode's identity as a string - the window's item key (its id;
    a local file's path), so it matches what plays."""
    for k in ("stream_id", "series_id", "id", "_key", "_path"):
        v = ep.get(k)
        if v:
            return str(v)
    return ""


@dataclass
class Season:
    """One season: its number (SPECIALS, or None for one without) and its
    episodes in order."""
    number: int | None
    episodes: list[dict] = field(default_factory=list)

    @property
    def specials(self) -> bool:
        return self.number == SPECIALS


def _season_order(number: int | None) -> tuple[int, int]:
    if number == SPECIALS:
        return (2, 0)
    if number is None:
        return (1, 0)
    return (0, number)


def _episode_order(ep: dict) -> tuple[int, str]:
    n = episode_number(ep)
    return (n if n is not None else 10 ** 6, str(ep.get("title") or "").lower())


def seasons(episodes: Any) -> list[Season]:
    """*episodes* - get_series_info's "episodes": a dict of season -> list,
    or one list with a "season" on each - as seasons in order: 1, 2, ...,
    one without a number, then Specials. Rows that are not dicts go."""
    groups: dict[int | None, list[dict]] = {}
    if isinstance(episodes, dict):
        for key, eps in episodes.items():
            for ep in eps or []:
                if isinstance(ep, dict):
                    s = season_of(ep.get("season", key))
                    groups.setdefault(s, []).append(ep)
    elif isinstance(episodes, list):
        for ep in episodes:
            if isinstance(ep, dict):
                groups.setdefault(season_of(ep.get("season")), []).append(ep)
    out = [Season(n, sorted(eps, key=_episode_order))
           for n, eps in groups.items() if eps]
    out.sort(key=lambda s: _season_order(s.number))
    return out


def flow(all_seasons: list[Season], specials: bool = False) -> list[dict]:
    """The episodes in watching order: every season but Specials - or the
    Specials alone (*specials*, or when they are all there is)."""
    regular = [s for s in all_seasons if not s.specials]
    if specials or not regular:
        return [ep for s in all_seasons if s.specials for ep in s.episodes]
    return [ep for s in regular for ep in s.episodes]


@dataclass
class Progress:
    """How far an episode got: seconds in, its length, when (Unix time)."""
    pos: float = 0.0
    dur: float = 0.0
    at: int = 0

    @property
    def started(self) -> bool:
        return self.pos > 60 and (self.dur <= 0 or self.pos < self.dur * 0.95)

    @property
    def fraction(self) -> float:
        if self.dur <= 0:
            return 0.0
        return max(0.0, min(1.0, self.pos / self.dur))

    @property
    def minutes_left(self) -> int:
        if self.dur <= self.pos or self.dur <= 0:
            return 0
        return max(1, round((self.dur - self.pos) / 60))


@dataclass
class NextUp:
    """The primary button's episode: *resume* when it is in progress (then
    *progress* says how far), *first* when nothing is watched yet (or all of
    it: "Play S1 E1")."""
    episode: dict | None
    resume: bool = False
    first: bool = True
    progress: Progress = field(default_factory=Progress)


def next_up(order: list[dict], watched: Callable[[dict], bool],
            progress: Callable[[dict], Progress],
            playing: Callable[[dict], bool] | None = None) -> NextUp:
    if not order:
        return NextUp(None)
    seen = [bool(watched(ep)) for ep in order]
    prog = [progress(ep) for ep in order]
    going = [i for i, p in enumerate(prog) if p.started and not seen[i]]
    last = max((i for i, s in enumerate(seen) if s), default=-1)

    def newest(idx: list[int]) -> int:
        return max(idx, key=lambda i: (prog[i].at, -i))

    after = [i for i in going if i > last]
    if after:
        i = newest(after)
        return NextUp(order[i], resume=True, first=False, progress=prog[i])
    # Else what plays now is where you are, however little of it has
    # played: a minute into E2 the next one up was E1 (nothing "started").
    if playing is not None:
        now = next((i for i, ep in enumerate(order) if playing(ep)), -1)
        if now >= 0 and not seen[now]:
            return NextUp(order[now], resume=True, first=False, progress=prog[now])
    if 0 <= last < len(order) - 1:
        return NextUp(order[last + 1], first=False)
    if going:
        i = newest(going)
        return NextUp(order[i], resume=True, first=False, progress=prog[i])
    return NextUp(order[0])


def season_index(all_seasons: list[Season], ep: dict | None) -> int | None:
    """Which of *all_seasons* holds *ep* (by identity, else by its key)."""
    if ep is None:
        return None
    key = episode_key(ep)
    for i, s in enumerate(all_seasons):
        if any(e is ep for e in s.episodes):
            return i
    for i, s in enumerate(all_seasons):
        if key and any(episode_key(e) == key for e in s.episodes):
            return i
    return None


def find_number(all_seasons: list[Season], number: int | None) -> int | None:
    return next((i for i, s in enumerate(all_seasons) if s.number == number), None)


# -- the season picked, kept per series ----------------------------------------

def _load(raw: Any) -> dict:
    try:
        data = json.loads(str(raw or "") or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def remembered(raw: Any, series: str) -> tuple[int | None, str] | None:
    """(season number, the next episode's key then) kept for *series* in
    the stored JSON *raw*, or None."""
    got = _load(raw).get(series)
    if not isinstance(got, list) or len(got) != 2:
        return None
    number = got[0] if isinstance(got[0], int) or got[0] is None else _int(got[0])
    return number, str(got[1] or "")


def remember(raw: Any, series: str, number: int | None, next_key: str,
             cap: int = REMEMBER_MAX) -> str:
    """The stored JSON with *series*' picked season (and the episode next
    then) as the newest entry, the oldest dropped past *cap*."""
    data = _load(raw)
    data.pop(series, None)
    data[series] = [number, next_key]
    while len(data) > cap:
        data.pop(next(iter(data)))
    return json.dumps(data, separators=(",", ":"))


def opening_season(all_seasons: list[Season], nxt: NextUp,
                   kept: tuple[int | None, str] | None,
                   focus: dict | None = None) -> int:
    """The season the page opens on: the one holding *focus* (an episode
    asked for - what plays, a new one), else the one picked last while its
    next episode is unchanged, else the next episode's, else the first."""
    if not all_seasons:
        return 0
    at = season_index(all_seasons, focus)
    if at is not None:
        return at
    nxt_key = episode_key(nxt.episode) if nxt.episode is not None else ""
    if kept is not None and kept[1] == nxt_key:
        at = find_number(all_seasons, kept[0])
        if at is not None:
            return at
    at = season_index(all_seasons, nxt.episode)
    if at is not None:
        return at
    return next((i for i, s in enumerate(all_seasons) if not s.specials), 0)


# -- the words on a card ------------------------------------------------------------

def episode_title(ep: dict, series_name: str = "") -> str:
    """The episode's own title: without the series' name a provider puts
    in front, an "S01E02" in it, or the separators left over."""
    title = str(ep.get("title") or "").strip()
    name = series_name.strip()
    if name and title.lower().startswith(name.lower()):
        title = title[len(name):]
    title = _SXXEYY.sub(" ", title)
    title = _SEP.sub("", " ".join(title.split()))
    if episode_number(ep) is not None and digits(title) == episode_number(ep):
        return ""
    return title


def plot_of(ep: dict) -> str:
    info = _info(ep)
    for v in (info.get("plot"), info.get("description"), info.get("overview"),
              ep.get("plot"), ep.get("_sp_plot")):
        text = " ".join(str(v or "").split())
        if text:
            return text
    return ""


def minutes_of(ep: dict) -> int:
    """An episode's length in minutes from what its source says (0: not
    known): seconds, "01:02:03", "45:10", "45 min", "2700"."""
    info = _info(ep)
    secs = number(info.get("duration_secs"), lo=0)
    if secs:
        return max(1, round(secs / 60))
    text = str(info.get("duration") or ep.get("_sp_runtime") or "").strip()
    if not text:
        return 0
    if ":" in text:
        parts = [_int(p) or 0 for p in text.split(":")]
        if len(parts) == 3:
            return max(0, round(parts[0] * 60 + parts[1] + parts[2] / 60))
        if len(parts) == 2:
            return max(0, round(parts[0] + parts[1] / 60))
    n = _int(text)
    if n is None:
        return 0
    if "h" in text.lower() and "min" not in text.lower():
        return n * 60
    return round(n / 60) if n > 600 else n


def year_of(*values: Any) -> str:
    """The first four-digit year among *values* (dates, "2019", ...)."""
    for v in values:
        m = re.search(r"(19|20)\d{2}", str(v or ""))
        if m:
            return m.group(0)
    return ""


def rating_of(*values: Any) -> float | None:
    """A rating out of ten from the first value that is one."""
    for v in values:
        r = number(str(v).replace(",", "."), lo=0, hi=10)
        if r:
            return round(r, 1)
    return None


def backdrop_of(*values: Any) -> str:
    """The first web address among *values* (each a string or a list of
    them, as Xtream's backdrop_path is)."""
    for v in values:
        items: Iterable[Any] = v if isinstance(v, (list, tuple)) else [v]
        for item in items:
            text = str(item or "").strip()
            if text.lower().startswith(("http://", "https://")):
                return text
    return ""


def still_of(ep: dict) -> str:
    """The episode's own picture (its source's, else one TMDB gave; a
    local file's thumbnail on disk)."""
    info = _info(ep)
    for v in (info.get("movie_image"), ep.get("_sp_still")):
        text = str(v or "").strip()
        if text.lower().startswith(("http://", "https://", "file://")) \
                or text.startswith("/"):
            return text
    return ""
