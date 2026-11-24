"""One channel list across every source, no Qt (ui/mw_every_source.py puts
it in the window; services/search_sources.py reads the sources).

With two sources or more the TV place's category column has "Every source"
(``CATEGORY``): every source's live channels in one list, as each source's
own lists show them (``search_all.visible`` already left out what they
leave out), under the source's name as a heading, the sources in the
user's order.

- A row of the source in use is the source's own row: it plays, is
  favourited and has its menu as in any of its lists.
- A row of another source is ``wrap``'s: what the list shows of it, under
  the key it plays under (``play_key``, the one ui/mw_failover.py plays
  another source's channel under - so the list marks it playing), and no
  ``stream_id``: nothing of the source in use - its address, its history,
  its favourites, its health, its numbers - is ever made from it by
  mistake. Its own row stays with its section (``Section.originals``) and
  is asked for its address only when it is played.
- ``arrange`` lays the sections out: each one's rows as the filter and the
  order leave them, under a heading row (``heading``) - in a grid of tiles
  none. A source read only in part, or not at all, says so in its heading.
- ``NowTable``: another source's guide around now, compact, by row key -
  what its rows' now and next are drawn from, built off the window's
  thread from the guide this computer has on disk (never a download) and
  the guide itself let go. It keeps no description: the row picked's
  programmes are read with theirs on their own (``listings_of``, each cut
  to TEXT_MAX, the last TEXTS_KEPT rows of a source kept).
"""

from __future__ import annotations

from array import array
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import search_all as sa

#: The TV place's category of every source's channels (ui/mw_lists.py).
CATEGORY = "__sources__"
#: It is offered from this many sources of one's own on.
MIN_SOURCES = 2
#: Of another source's row, what the list shows (its address, catch-up and
#: id stay with the source's own row).
_SHOWN = ("name", "stream_icon", "epg_channel_id", "category_id", "num", "added")

#: Of another source's guide, the programmes kept: what airs from a day
#: back (an airing that began up to a day ago may be on still) to this far
#: ahead - read again long before it runs out (search_sources.GUIDE_FRESH_SECS).
AHEAD_SECS = 36 * 3600
_MAX_PROG = 86_400
#: A source's table holds at most this many airings; channels past it get
#: none (``NowTable.cut``).
MAX_AIRINGS = 400_000
#: A row's programmes in the detail column, at most.
LISTINGS = 24
#: The row picked's programmes are read with their descriptions (``listings_of``),
#: each kept this long at most - for the last few rows picked of a source.
TEXT_MAX = 1000
TEXTS_KEPT = 16


def listings_of(sched: sa.Schedule | None, now: float, limit: int = LISTINGS) -> list[dict]:
    """A channel's programmes - a guide's schedule (starts, stops, titles,
    descriptions) - from the one on at *now*, as the guide gives them
    (``_plain`` entries): at most *limit*, each description cut to
    TEXT_MAX. What the detail column shows of the row picked of another
    source, read from its own guide."""
    if not sched:
        return []
    starts, stops, titles = sched[0], sched[1], sched[2]
    descs = sched[3] if len(sched) > 3 else ()
    out: list[dict] = []
    j = bisect_left(starts, now - _MAX_PROG)
    while j < len(starts) and len(out) < limit:
        if stops[j] > now:
            text = str(descs[j] or "") if j < len(descs) else ""
            out.append({"_plain": True, "title": str(titles[j] or ""),
                        "description": text[:TEXT_MAX],
                        "start_timestamp": int(starts[j]), "stop_timestamp": int(stops[j])})
        j += 1
    return out


def play_key(source_id: Any, stream_id: Any) -> str:
    """The key a channel of another source plays under, here and in
    ui/mw_failover.py - its row's key in the list too."""
    return f"failover:{source_id}:{stream_id}"


def offered(playlists: Iterable[Mapping[str, Any]]) -> bool:
    """Whether "Every source" is offered: two sources of one's own or more
    (files of one's own and a source turned off not counted)."""
    own = [p for p in playlists or () if isinstance(p, Mapping) and p.get("id")
           and p.get("kind") != "local" and not p.get("disabled")]
    return len(own) >= MIN_SOURCES


def is_other(row: Mapping[str, Any] | None) -> bool:
    """Whether *row* is another source's (``wrap``'s)."""
    return bool(row and row.get("_xs"))


def wrap(source: sa.Source, row: Mapping[str, Any], title: str) -> dict:
    """Row *row* of *source* (not the one in use) as the list shows it:
    *title* its name as that source's lists show it, the key it plays
    under, which source it is from - and no id of the source in use's."""
    out: dict[str, Any] = {k: row.get(k) for k in _SHOWN if row.get(k) not in (None, "")}
    out["name"] = title or str(row.get("name") or "")
    out["_key"] = play_key(source.id, row.get("stream_id"))
    out["_xs"] = source.id
    out["_xs_name"] = source.name
    return out


def heading(text: str, source_id: str) -> dict:
    """A source's heading row: never picked or played (``_header``), as
    tall as a channel's row (``_tv_head``: the list stays one of uniform
    rows)."""
    return {"_header": text, "_tv_head": True, "_xs_head": source_id}


@dataclass
class Section:
    """One source's channels as the list shows them: *rows* (the source in
    use's own rows, another's ``wrap``'s), *here* for the source in use,
    *partial* why only part of them is here (search_all.PAGED / FAILED, ""
    for all), *originals* another source's own rows by their key here."""
    source: sa.Source
    rows: list[dict] = field(default_factory=list)
    here: bool = False
    partial: str = ""
    originals: dict[str, dict] = field(default_factory=dict)


def section_of(cat: sa.Catalogue, here: bool) -> Section:
    """*cat*'s channels as a section: the source in use's own rows as they
    are, another source's each ``wrap``-ped under its own name."""
    if here:
        return Section(cat.source, list(cat.channels), True, cat.partial)
    titles = cat.titles.get("channels") or []
    rows: list[dict] = []
    originals: dict[str, dict] = {}
    for i, row in enumerate(cat.channels):
        if row.get("stream_id") is None:
            continue
        shown = wrap(cat.source, row, titles[i] if i < len(titles) else "")
        rows.append(shown)
        originals[shown["_key"]] = row
    return Section(cat.source, rows, False, cat.partial, originals)


def arrange(sections: Sequence[Section],
            keep: Callable[[Section, list[dict]], list[dict]],
            heading_text: Callable[[Section, int], str],
            headings: bool = True, filtered: bool = False
            ) -> tuple[list[dict], dict[str, tuple[int, int]], int]:
    """The list's rows of *sections* (in the sources' order): each
    section's rows as *keep* leaves them (the filter typed, the order),
    under its heading (*heading_text*: its words for the rows shown) unless
    *headings* is off (a grid of tiles). A section with nothing to show is
    left out - unless, with nothing typed (*filtered*), it has something to
    say: a source read only in part, or not at all. (rows, per source id
    where its block starts and how long it is, how many channels.)"""
    out: list[dict] = []
    blocks: dict[str, tuple[int, int]] = {}
    total = 0
    for sec in sections:
        rows = keep(sec, sec.rows)
        if not rows and (filtered or not sec.partial):
            continue
        start = len(out)
        if headings:
            out.append(heading(heading_text(sec, len(rows)), sec.source.id))
        out.extend(rows)
        total += len(rows)
        blocks[sec.source.id] = (start, len(out) - start)
    return out, blocks, total


class NowTable:
    """Another source's guide around now, compact: per row key (the
    ``play_key`` of each channel) its programmes from a day back to
    AHEAD_SECS ahead - starts, stops and titles; two channels on one guide
    id share theirs. The same face the window's guide shows a row's painter
    (``schedule``, ``is_loaded``, ``delay_minutes``), and the detail
    column's listings. At most *limit* airings (``cut``: there were more)."""

    def __init__(self, channels: Iterable[Mapping[str, Any]],
                 key_of: Callable[[Mapping[str, Any]], str],
                 schedule_of: Callable[[Any], sa.Schedule | None], now: float,
                 ahead: float = AHEAD_SECS, limit: int = MAX_AIRINGS) -> None:
        self.delay_minutes = 0
        self.built = now
        self.cut = False
        self._by_key: dict[str, tuple[array, array, tuple[str, ...], tuple[str, ...]]] = {}
        # Each schedule seen once, by its object - kept here while the table
        # is built, so that no other one can take its id meanwhile.
        made: dict[int, tuple[Any, tuple[array, array, tuple[str, ...], tuple[str, ...]]]] = {}
        held = 0
        for ch in channels:
            try:
                sched = schedule_of(ch)
            except Exception:
                sched = None
            if not sched:
                continue
            seen = made.get(id(sched))
            got = seen[1] if seen is not None and seen[0] is sched else None
            if got is None:
                starts, stops, titles = sched[0], sched[1], sched[2]
                lo = bisect_left(starts, now - _MAX_PROG)
                hi = bisect_right(starts, now + ahead)
                while lo < hi and stops[lo] <= now:
                    lo += 1                     # over before now
                if hi <= lo:
                    continue
                if held + (hi - lo) > limit:
                    self.cut = True
                    continue
                held += hi - lo
                # No description is kept (the guide's own strings would stay
                # with it): one empty one per airing, as the guide's shape has.
                got = (array("q", (int(s) for s in starts[lo:hi])),
                       array("q", (int(s) for s in stops[lo:hi])),
                       tuple(str(t or "") for t in titles[lo:hi]), ("",) * (hi - lo))
                made[id(sched)] = (sched, got)
            self._by_key[str(key_of(ch))] = got

    def __len__(self) -> int:
        return len(self._by_key)

    def is_loaded(self) -> bool:
        return True

    def schedule(self, item: Mapping[str, Any]) -> sa.Schedule | None:
        """The programmes of row *item* (by its key), or None."""
        return self._by_key.get(str(item.get("_key") or ""))

    def listings(self, item: Mapping[str, Any], now: float, limit: int = LISTINGS) -> list[dict]:
        """Row *item*'s programmes from the one on now, as the guide gives
        them (``_plain`` entries: no description is kept here - the row
        picked's are read on their own, ``listings_of``)."""
        return listings_of(self.schedule(item), now, limit)
