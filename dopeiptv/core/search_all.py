"""One search across every source: what a query finds in each source's
channels, the programmes of its guide, its films and its series - four
groups, the best first, each hit with the source it is from.

Pure logic, no Qt and no network (services/search_sources.py reads the
sources). A ``Catalogue`` is one source's rows as its lists show them
(``visible``: what the lists leave out and, for a kids profile, what is for
adults, already taken out), its names folded once; a ``GuideTable`` is a
week of its guide from now, one airing per programme and schedule, titles
and descriptions folded once and kept compact. ``search`` goes through
them for a query - on a worker, given up as soon as a newer query is typed
(``Latest``) - with the command palette's matching (core/palette_match.py):
case, accents and punctuation do not count, every word has to be there, a
word's start weighs more, the exact title most.
"""

from __future__ import annotations

import heapq
import threading
from array import array
from bisect import bisect_left
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .palette_match import fold, score, words
from .profiles import adult_category, adult_item

#: The groups, in the order the page shows them.
GROUPS = ("channels", "programmes", "movies", "series")
#: A group's rows are of this catalogue (a category's kind in the lists).
MODES = {"channels": "live", "movies": "vod", "series": "series"}
#: The rows of each group kept for a query, the best first.
CAP = 300
#: Shorter queries find nothing (one letter is in everything).
MIN_QUERY = 2
#: The guide is searched from now on for this long.
GUIDE_DAYS = 7
#: Of a description this much is folded and matched.
DESC_CHARS = 240
#: A source's week of guide is kept to this many airings, the soonest.
MAX_AIRINGS = 250_000
#: No programme runs longer than a day (how far back an airing may start).
_MAX_PROG = 86_400

#: Why a source was searched only in part (Catalogue.partial).
PAGED = "paged"          # a portal: only the lists opened so far
FAILED = "failed"        # its lists did not come
GUIDE_CUT = "guide_cut"  # its guide is bigger than MAX_AIRINGS

#: (starts, stops, titles, descriptions) of one channel, as
#: providers/epg.py keeps them.
Schedule = tuple[Sequence[int], Sequence[int], Sequence[str], Sequence[str]]


@dataclass(frozen=True)
class Source:
    """A source as the search names it."""
    id: str
    name: str
    kind: str = "xtream"


def row_key(row: Mapping[str, Any]) -> str | None:
    """A row's id as the lists keep their marks under it."""
    key = (row.get("stream_id") or row.get("series_id") or row.get("id")
           or row.get("_key"))
    return None if key is None else str(key)


def adult_ids(categories: Iterable[Any], names: Mapping[str, str] | None = None) -> frozenset[str]:
    """The ids of *categories* named for adults, by the name the lists show
    them under (*names*: the user's) or the provider's own - a new name
    never takes one out."""
    given = names or {}
    out: set[str] = set()
    for c in categories or []:
        if not isinstance(c, Mapping) or c.get("category_id") is None:
            continue
        cid = str(c.get("category_id"))
        if adult_category(given.get(cid), c.get("category_name")):
            out.add(cid)
    return frozenset(out)


def visible(rows: Iterable[Any], keep: Callable[[dict], bool] | None = None,
            kids: bool = False, adult: frozenset[str] | None = frozenset(),
            known: frozenset[str] | None = None,
            allowed: frozenset[str] | None = None) -> list[dict]:
    """*rows* as a source's lists show them: none *keep* turns down (its
    hidden categories, locked ones while locked, rows hidden by hand) and,
    for a kids profile, as its lists have them (ui/mw_profiles.KidsFilter):
    none marked for adults, none without a category or of one the source
    does not list (*known*, its category ids, where given), none in a
    category named for adults (*adult* those ids; None when they could not
    be read: then none at all) - and with "Only what a grown-up allows"
    only those of *allowed*."""
    out: list[dict] = []
    for r in rows or []:
        if not isinstance(r, dict) or (keep is not None and not keep(r)):
            continue
        if kids:
            if adult_item(r):
                continue
            cid = r.get("category_id")
            if cid in (None, "") or adult is None or str(cid) in adult:
                continue
            if known is not None and str(cid) not in known:
                continue
            if allowed is not None and str(cid) not in allowed:
                continue
        out.append(r)
    return out


def title_of(row: Mapping[str, Any], renamed: Mapping[str, str] | None = None) -> str:
    """A row's name as the lists show it: the user's, else the source's."""
    key = row_key(row)
    if renamed and key is not None and renamed.get(key):
        return str(renamed[key])
    return str(row.get("name") or row.get("title") or "").strip()


def window(now: float, days: int = GUIDE_DAYS) -> tuple[float, float]:
    """The span the guide is searched in: from *now*, *days* ahead."""
    return now, now + days * 86_400


class Latest:
    """The newest query's number: a search on a worker gives up as soon as
    another has been asked for."""

    def __init__(self) -> None:
        self._n = 0
        self._lock = threading.Lock()

    def bump(self) -> int:
        with self._lock:
            self._n += 1
            return self._n

    def current(self, gen: int) -> bool:
        return gen == self._n

    @property
    def value(self) -> int:
        return self._n


@dataclass
class Catalogue:
    """One source's channels, films and series as its lists show them, and
    their categories' names (*categories*: mode -> id -> name). *renamed*:
    mode -> row key -> the user's name. *partial*: why only part of it is
    here ("" when all of it is)."""
    source: Source
    channels: list[dict] = field(default_factory=list)
    movies: list[dict] = field(default_factory=list)
    series: list[dict] = field(default_factory=list)
    categories: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    renamed: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    partial: str = ""

    def __post_init__(self) -> None:
        self.titles: dict[str, list[str]] = {}
        self.folded: dict[str, list[str]] = {}
        for group in MODES:
            names = self.renamed.get(MODES[group]) or {}
            shown = [title_of(r, names) for r in self.rows(group)]
            self.titles[group] = shown
            self.folded[group] = [fold(t) for t in shown]

    def rows(self, group: str) -> list[dict]:
        return {"channels": self.channels, "movies": self.movies,
                "series": self.series}.get(group, [])

    def category(self, group: str, row: Mapping[str, Any]) -> str:
        names = self.categories.get(MODES.get(group, "")) or {}
        return str(names.get(str(row.get("category_id")), "") or "")

    def size(self) -> int:
        return sum(len(v) for v in self.folded.values())


class GuideTable:
    """A week of one source's guide from *now*, compact: each programme
    once per schedule (two channels with one guide id share it), on the
    first of *channels* that has it; at most *limit* airings, the soonest
    (``cut`` when there were more). *schedule_of* gives a channel's
    Schedule, or None."""

    def __init__(self, source: Source, channels: Sequence[dict],
                 schedule_of: Callable[[dict], Schedule | None], now: float,
                 days: int = GUIDE_DAYS, limit: int = MAX_AIRINGS) -> None:
        self.source = source
        self.channels = list(channels)
        self.built = now
        start, stop = window(now, days)
        seen: set[int] = set()
        found: list[tuple[int, int, int, str, str]] = []
        for ci, ch in enumerate(self.channels):
            sched = schedule_of(ch)
            if not sched or id(sched) in seen:
                continue
            seen.add(id(sched))
            starts, stops, titles, descs = sched
            n = len(starts)
            j = bisect_left(starts, start - _MAX_PROG)
            while j < n and starts[j] < stop:
                if stops[j] > start and titles[j]:
                    found.append((int(starts[j]), int(stops[j]), ci, titles[j], descs[j]))
                j += 1
        self.cut = len(found) > limit
        if self.cut:
            found = heapq.nsmallest(limit, found, key=lambda a: a[0])
        # Building a big week is the peak of its memory: what is only needed
        # meanwhile goes as soon as it is done with, and a description is
        # kept once by what is matched (folded), not once more as it was.
        title_at: dict[str, int] = {}
        seen_desc: dict[str, int] = {}      # by the guide's own strings: no copies
        desc_at: dict[str, int] = {}        # by the folded text kept
        self.title_text: list[str] = []
        self.ftitles: list[str] = []
        self.fdescs: list[str] = []
        rows: list[Any] = found             # turned into the rows in place
        for k, (st, sp, ci, title, desc) in enumerate(found):
            t = title_at.get(title)
            if t is None:
                t = title_at[title] = len(self.title_text)
                self.title_text.append(title)
                self.ftitles.append(fold(title))
            d = seen_desc.get(desc)
            if d is None:
                d = -1
                text = " ".join(str(desc or "").split())[:DESC_CHARS]
                if text:
                    f = fold(text)
                    d = desc_at.get(f, -1)
                    if d < 0:
                        d = desc_at[f] = len(self.fdescs)
                        self.fdescs.append(f)
                seen_desc[desc] = d
            rows[k] = (t, st, sp, ci, d)
        del found, title_at, seen_desc, desc_at
        rows.sort()
        self.starts = array("q", (r[1] for r in rows))
        self.stops = array("q", (r[2] for r in rows))
        self.chans = array("i", (r[3] for r in rows))
        self.tix = array("i", (r[0] for r in rows))
        self.dix = array("i", (r[4] for r in rows))
        n = len(rows)
        del rows
        # Where each title's airings begin: they are in title order, and
        # every title has one at least.
        self._t_first = array("i", [0] * (len(self.title_text) + 1))
        prev = -1
        for pos, t in enumerate(self.tix):
            if t != prev:
                self._t_first[t] = pos
                prev = t
        self._t_first[len(self.title_text)] = n
        # The airings by description, for a word found only there: counted
        # per description, then placed in air-position order within each.
        first = array("i", [0]) * (len(self.fdescs) + 1)
        for d in self.dix:
            if d >= 0:
                first[d + 1] += 1
        for d in range(len(self.fdescs)):
            first[d + 1] += first[d]
        self._d_first = first
        nxt = array("i", first)
        self._by_desc = array("i", [0]) * first[len(self.fdescs)]
        for pos, d in enumerate(self.dix):
            if d >= 0:
                self._by_desc[nxt[d]] = pos
                nxt[d] += 1

    def __len__(self) -> int:
        return len(self.starts)

    def airings_of(self, title: int) -> range:
        return range(self._t_first[title], self._t_first[title + 1])

    def matches(self, terms: Sequence[str], now: float, stop: float) -> list[tuple[int, int]]:
        """(score, airing) of what *terms* find, airing from *now* to
        *stop*: a title's match scores 3 and more, one only in the
        description 1."""
        out: list[tuple[int, int]] = []
        if not terms:
            return out
        first = terms[0]
        stops, starts = self.stops, self.starts
        matched: set[int] = set()
        for t, f in enumerate(self.ftitles):
            if first in f:
                s = score(terms, f)
                if s:
                    matched.add(t)
                    for pos in self.airings_of(t):
                        if stops[pos] > now and starts[pos] < stop:
                            out.append((2 * s + 1, pos))
        for d, f in enumerate(self.fdescs):
            if first in f and all(term in f for term in terms):
                for k in range(self._d_first[d], self._d_first[d + 1]):
                    pos = self._by_desc[k]
                    if (self.tix[pos] not in matched and stops[pos] > now
                            and starts[pos] < stop):
                        out.append((1, pos))
        return out

    def programme(self, pos: int) -> dict:
        """Airing *pos* as the guide gives a programme."""
        return {"title": self.title_text[self.tix[pos]],
                "start_timestamp": int(self.starts[pos]),
                "stop_timestamp": int(self.stops[pos])}


@dataclass
class Hit:
    """One row of the results: its *group*, the *source* it is from, the
    *row* (the channel, for a programme) and what it shows."""
    group: str
    source: Source
    row: dict
    title: str
    score: int = 0
    category: str = ""
    programme: dict | None = None

    def on_air(self, now: float) -> bool:
        p = self.programme
        return p is not None and p["start_timestamp"] <= now < p["stop_timestamp"]

    def later(self, now: float) -> bool:
        p = self.programme
        return p is not None and p["start_timestamp"] > now


@dataclass
class Results:
    """What a query found: per group its best hits (at most CAP) and how
    many there were in all; the sources searched only in part, and why."""
    query: str
    hits: dict[str, list[Hit]] = field(default_factory=dict)
    totals: dict[str, int] = field(default_factory=dict)
    partial: list[tuple[Source, str]] = field(default_factory=list)

    def count(self) -> int:
        return sum(self.totals.values())


def _searchable(query: str) -> list[str]:
    terms = words(query)
    if not terms or len(" ".join(terms)) < MIN_QUERY:
        return []
    return terms


def search(query: str, catalogues: Sequence[Catalogue],
           guides: Sequence[GuideTable] = (), now: float = 0.0,
           latest: Latest | None = None, gen: int = 0,
           cap: int = CAP, active: str = "") -> Results | None:
    """What *query* finds in *catalogues* and *guides* (the sources in the
    user's order; *active* the id of the one in use). Per group the best
    first: the higher score, then (programmes) what is on now and the
    sooner, (the rest) the shorter name; then the source in use, then the
    sources' order. None when a newer query came meanwhile (*latest*, this
    one *gen*)."""
    out = Results(query, {g: [] for g in GROUPS}, dict.fromkeys(GROUPS, 0))
    terms = _searchable(query)
    for cat in catalogues:
        if cat.partial:
            out.partial.append((cat.source, cat.partial))
    for table in guides:
        if table.cut:
            out.partial.append((table.source, GUIDE_CUT))
    if not terms:
        return out

    def stale() -> bool:
        return latest is not None and not latest.current(gen)

    first = terms[0]
    for group in MODES:
        ranked: list[tuple[tuple[int, int, int, int, int], Catalogue, int]] = []
        total = 0
        for order, cat in enumerate(catalogues):
            if stale():
                return None
            folded = cat.folded.get(group) or []
            here = 1 if active and cat.source.id == active else 0
            for i, f in enumerate(folded):
                if first in f:
                    s = score(terms, f)
                    if s:
                        total += 1
                        ranked.append(((s, -len(f), here, -order, -i), cat, i))
        best = heapq.nlargest(cap, ranked, key=lambda r: r[0])
        out.totals[group] = total
        out.hits[group] = [
            Hit(group, cat.source, cat.rows(group)[i], cat.titles[group][i],
                key[0], cat.category(group, cat.rows(group)[i]))
            for key, cat, i in best]
    progs: list[tuple[tuple[int, int, int, int, int], GuideTable, int]] = []
    _start, span_stop = window(now)
    # The same airing on two of a source's channels (one listed twice under
    # two guide ids): once, on the first.
    seen: set[tuple[str, int, int]] = set()
    for order, table in enumerate(guides):
        if stale():
            return None
        here = 1 if active and table.source.id == active else 0
        for s, pos in table.matches(terms, now, span_stop):
            start = int(table.starts[pos])
            once = (table.source.id, start, table.tix[pos])
            if once in seen:
                continue
            seen.add(once)
            on = 1 if start <= now < table.stops[pos] else 0
            progs.append(((s, on, -start, here, -order), table, pos))
    best_progs = heapq.nlargest(cap, progs, key=lambda r: r[0])
    out.totals["programmes"] = len(progs)
    out.hits["programmes"] = [
        Hit("programmes", table.source, table.channels[table.chans[pos]],
            table.title_text[table.tix[pos]], key[0],
            str(table.channels[table.chans[pos]].get("name") or ""),
            table.programme(pos))
        for key, table, pos in best_progs]
    if stale():
        return None
    return out
