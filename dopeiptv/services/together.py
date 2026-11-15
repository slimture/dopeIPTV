"""Watch together, the catalogue side: what plays here said as a Media
(core/together.py) any source would know, and a Media from another
computer found in this one's own source.

A film is the same film by its TMDB id where both know one, else by its
title (as PosterResolver cleans a provider's) and year - a year apart
still counts, release years differ by country. An episode: its series so,
then the season and the episode number. A live channel: by guide id, else
by name, as failover does (services/failover.py).

locate() asks the provider for its lists: off the window's thread.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ..core.log import log
from ..core.privacy import for_log
from ..core.together import TEXT_MAX, Media
from .failover import name_key, same_channel

#: (title, kind "vod" | "series") -> a TMDB id already known here, or None.
TmdbOf = Callable[[str, str], Optional[int]]


def clean_title(raw: str) -> tuple[str, int | None]:
    from ..providers.metadata import PosterResolver
    return PosterResolver.clean_title(raw)


def title_key(text: str) -> str:
    """A title as two sources would both write it: case, spaces and
    punctuation set aside, a leading article too."""
    s = (text or "").casefold()
    s = re.sub(r"^(the|a|an)\s+", "", s.strip())
    return re.sub(r"[\W_]+", "", s)


def _int(value: Any, lo: int = 1) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if n >= lo else None


def row_tmdb(row: dict, tmdb_of: TmdbOf | None = None, kind: str = "vod") -> int | None:
    for key in ("_tmdb_id", "tmdb", "tmdb_id"):
        tid = _int(row.get(key))
        if tid is not None:
            return tid
    if tmdb_of is not None:
        name = str(row.get("name") or row.get("title") or "")
        try:
            return tmdb_of(name, kind) if name else None
        except Exception:
            return None
    return None


def row_year(row: dict, fallback: int | None = None) -> int | None:
    for key in ("year", "releaseDate", "releasedate", "release_date"):
        m = re.match(r"\s*((?:18|19|20|21)\d\d)", str(row.get(key) or ""))
        if m:
            return int(m.group(1))
    return fallback


def _titled(row: dict) -> tuple[str, int | None]:
    raw = str(row.get("name") or row.get("title") or "")
    title, year = clean_title(raw)
    return (title or raw).strip(), row_year(row, year)


# -- what plays here -------------------------------------------------------------------

def describe_movie(row: dict, tmdb_of: TmdbOf | None = None) -> Media | None:
    title, year = _titled(row)
    if not title:
        return None
    name = f"{title} ({year})" if year else title
    return Media("movie", name[:TEXT_MAX], title=title[:TEXT_MAX], year=year,
                 tmdb=row_tmdb(row, tmdb_of, "vod"))


def describe_episode(ep: dict, series: dict | None, tmdb_of: TmdbOf | None = None) -> Media | None:
    series = series or {}
    title, year = _titled(series) if (series.get("name") or series.get("title")) \
        else clean_title(str(ep.get("_series_title") or ""))
    season, number = _int(ep.get("season"), 0), _int(ep.get("episode_num"), 0)
    if not title or season is None or number is None:
        return None
    name = f"{title} S{season:02d}E{number:02d}"
    return Media("episode", name[:TEXT_MAX], title=title[:TEXT_MAX], year=year,
                 tmdb=row_tmdb(series, tmdb_of, "series") if series else None,
                 season=season, episode=number)


def describe_live(row: dict) -> Media | None:
    name = str(row.get("name") or "").strip()
    if not name:
        return None
    guide = str(row.get("epg_channel_id") or row.get("_epg_own") or "").strip()
    return Media("live", name[:TEXT_MAX], guide=guide[:TEXT_MAX])


def describe(kind: str, item: dict | None, series: dict | None = None,
             tmdb_of: TmdbOf | None = None) -> Media | None:
    """What a playback is, for the room - or None for what cannot be
    watched together (a file or a recording of this computer's own, the
    archive of a channel)."""
    if not item:
        return None
    try:
        if kind == "movie":
            return describe_movie(item, tmdb_of)
        if kind == "episode":
            return describe_episode(item, series, tmdb_of)
        if kind == "live":
            return describe_live(item)
    except Exception:
        log.debug("together: not described", exc_info=True)
    return None


def _years_agree(a: int | None, b: int | None) -> bool:
    return a is None or b is None or abs(a - b) <= 1


def same(a: Media | None, b: Media | None) -> bool:
    """Whether two computers play the same thing."""
    if a is None or b is None or a.kind != b.kind:
        return False
    if a.kind == "live":
        if a.guide and b.guide and a.guide.casefold() == b.guide.casefold():
            return True
        return bool(name_key(a.name)) and name_key(a.name) == name_key(b.name)
    if a.kind == "episode" and (a.season, a.episode) != (b.season, b.episode):
        return False
    if a.tmdb and b.tmdb:
        return a.tmdb == b.tmdb
    key = title_key(a.title)
    return bool(key) and key == title_key(b.title) and _years_agree(a.year, b.year)


# -- what plays there, found here --------------------------------------------------------

def best_title(rows: list[dict], want: Media, kind: str,
               tmdb_of: TmdbOf | None = None) -> dict | None:
    """The row of *rows* that is *want*'s film (kind "vod") or series
    ("series"): the same TMDB id, else the same title - the same year
    first, then a year apart, then one without a year."""
    key = title_key(want.title)
    best: tuple[int, int] | None = None
    found: dict | None = None
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        raw = str(row.get("name") or row.get("title") or "")
        tid = None
        for k in ("_tmdb_id", "tmdb", "tmdb_id"):
            tid = tid or _int(row.get(k))
        if want.tmdb and tid:
            if tid == want.tmdb:
                return row
            continue
        if not key or key[:6] not in title_key(raw):
            continue                # cheap: most rows are not even close
        title, year = _titled(row)
        if title_key(title) != key:
            continue
        if want.tmdb and tmdb_of is not None:
            other = row_tmdb(row, tmdb_of, kind)
            if other is not None and other != want.tmdb:
                continue
        if want.year is not None and year is not None:
            gap = abs(want.year - year)
            if gap > 1:
                continue
            score = 3 if gap == 0 else 2
        else:
            score = 1
        if best is None or (score, -i) > best:
            best, found = (score, -i), row
    return found


def find_episode(info: Any, season: int | None, number: int | None) -> dict | None:
    eps = (info or {}).get("episodes") if isinstance(info, dict) else None
    groups = list(eps.items()) if isinstance(eps, dict) else [(None, eps)] if isinstance(eps, list) else []
    for group, items in groups:
        for ep in items or []:
            if not isinstance(ep, dict) or ep.get("id") in (None, ""):
                continue
            s = _int(ep.get("season"), 0)
            if s is None:
                s = _int(group, 0)
            if s == season and _int(ep.get("episode_num"), 0) == number:
                return ep
    return None


def episode_queue(info: Any, series: dict) -> list[dict]:
    """The series' episodes in order, as the window lists them, for the
    next one to follow."""
    eps = (info or {}).get("episodes") if isinstance(info, dict) else None
    if not isinstance(eps, dict):
        return []
    cover = series.get("cover") or series.get("cover_big") or series.get("stream_icon") or ""
    title = series.get("name") or series.get("title") or ""
    out = []
    for season, items in sorted(eps.items(), key=lambda kv: _int(kv[0], 0) or 0):
        for ep in sorted((e for e in items or [] if isinstance(e, dict)),
                         key=lambda e: _int(e.get("episode_num"), 0) or 0):
            ep = dict(ep)
            ep["season"] = ep.get("season") or season
            ep["name"] = f"S{ep['season']} * E{ep.get('episode_num', '?')} - {ep.get('title') or ''}".rstrip(" -")
            still = (ep.get("info") or {}).get("movie_image") if isinstance(ep.get("info"), dict) else ""
            ep["stream_icon"] = ep.get("stream_icon") or still or cover
            ep["_series_title"] = title
            out.append(ep)
    return out


@dataclass
class Found:
    """*want* in this source: the row to play (a channel, a film or an
    episode), its address, and for an episode its series and the season's
    episodes in order."""
    kind: str
    row: dict
    url: str
    series: dict | None = None
    queue: list[dict] | None = None


def locate(client: Any, want: Media, tmdb_of: TmdbOf | None = None) -> Found | None:
    """*want* in *client*'s source, or None. Blocking (the provider's lists)."""
    try:
        if want.kind == "live":
            rows = list(client.live_streams(None) or [])
            row = same_channel(rows, {"epg_channel_id": want.guide, "name": want.name})
            if row is None:
                return None
            return Found("live", row, "")
        if want.kind == "movie":
            if not hasattr(client, "vod_streams"):
                return None
            row = best_title(list(client.vod_streams(None) or []), want, "vod", tmdb_of)
            if row is None or row.get("stream_id") is None:
                return None
            url = client.vod_url(row["stream_id"], row.get("container_extension"))
            return Found("movie", row, url) if url else None
        if want.kind == "episode":
            if not hasattr(client, "series_list"):
                return None
            series = best_title(list(client.series_list(None) or []), want, "series", tmdb_of)
            if series is None or series.get("series_id") is None:
                return None
            info = client.series_info(series["series_id"])
            ep = find_episode(info, want.season, want.episode)
            if ep is None:
                return None
            url = client.episode_url(ep["id"], ep.get("container_extension"))
            if not url:
                return None
            queue = episode_queue(info, series)
            row = next((e for e in queue if str(e.get("id")) == str(ep["id"])), dict(ep))
            return Found("episode", row, url, series=series, queue=queue)
    except Exception as e:
        log.info("together: %s not looked up: %s", for_log(want.name), type(e).__name__)
    return None
