"""Mine ("Mitt"): the page that holds what is a person's own - Continue,
the favourite channels live now, the Watch later queue, new episodes of the
series they follow and History as a timeline - as plain data, no Qt.

The window (ui/mw_mitt.py) reads the stores (core/stores.py,
services/resume.py) and hands their rows here, on a worker; what comes back
is drawn by ui/mitt_page.py. Nothing here stores anything but the queue's
order (``ORDER_KEY``, a profile's own: core/profiles.py).

* ``Entry``: one title of a section - its kind, its key at its source, the
  store's own row (what the window plays), its source, when it was played,
  how far it got. ``key`` is unique across sources and is what the player's
  "what plays" is compared with (``playing_keys``).
* The filter: one chip per kind (``FILTERS``) and the words typed, folded
  as the command palette folds them (``matches``).
* The favourite channels by habit: what was watched at this hour - and on
  this weekday most - in the viewing log (core/viewing_stats.py), the
  recent weeks weighing more (``habit_score``, ``by_habit``); "usually on
  now" for the top few watched there on more than one day (``habitual``).
* The queue: Watch later in an order of the person's own - the order kept,
  a title added since at its end, moved up, down or to a place
  (``queue_order``, ``moved``, ``moved_to``); how much it is, films and
  series and the time they take where known (``queue_summary``).
* The timeline: History by day - today, yesterday, earlier this week (the
  locale's first day of the week), last week, then by month (``timeline``).
* The favourites' folders across every source (``fav_folders``): one folder
  of a name whatever source and kind hold it, each title with its own
  source and store (``FavStore``, ``Folder``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .num import digits, number
from .palette_match import fold, words

__all__ = [
    "ALL", "CHANNEL", "EPISODE", "FAV_SECTIONS", "FILM", "FILTERS", "LOCAL", "RADIO", "SERIES",
    "WEB", "Entry", "FavStore", "Folder", "QueueSummary", "action_of", "by_habit",
    "continue_entries", "day_group", "entry_key", "fav_folders", "fav_rows", "folder_entry",
    "habit_score", "habitual", "history_entries", "item_key", "length_of", "matches", "merged",
    "moved", "moved_to", "new_entries", "parse_order", "dump_order", "playing_keys",
    "queue_ident", "queue_order", "queue_summary", "reordered_within", "terms_of", "timeline",
    "when_of",
]

CHANNEL, FILM, SERIES, EPISODE = "channel", "film", "series", "episode"
RADIO, LOCAL, WEB = "radio", "local", "web"
ALL = "all"
#: The page's chips, in order.
FILTERS = (ALL, CHANNEL, FILM, SERIES, RADIO, LOCAL)
#: The chip each kind is under (a web stream only under All).
CHIP = {CHANNEL: CHANNEL, FILM: FILM, SERIES: SERIES, EPISODE: SERIES, RADIO: RADIO,
        LOCAL: LOCAL, WEB: ""}
#: History's and the resume points' kinds, as ours.
STORE_KIND = {"live": CHANNEL, "catchup": CHANNEL, "movie": FILM, "vod": FILM,
              "episode": EPISODE, "series": SERIES, "radio": RADIO, "local": LOCAL,
              "web": WEB}
#: The player's group for each kind (MainWindow._playing_group).
GROUP = {CHANNEL: "live", FILM: "vod", EPISODE: "episode", SERIES: "series",
         RADIO: "radio", LOCAL: "local", WEB: "web"}
#: Kinds that belong to no source: they play whichever source is in use.
SOURCELESS = (RADIO, LOCAL, WEB)

#: Where the queue's order is kept: a JSON list of ``queue_ident``s.
ORDER_KEY = "watch_later_order"
ORDER_MAX = 500
#: History's rows shown at a time; "Show more" adds as many again.
HISTORY_PAGE = 150
#: The favourite channels shown until "Show all".
LIVE_SHOWN = 12
#: Continue's, the favourites' and the new episodes' rows hold at most this many.
ROW_MAX = 40


@dataclass(slots=True)
class Entry:
    """One title on the page. *ident* is its key at its source (a stream
    id, an episode id, a file's path, "radio:<uuid>"); *row* the store's
    own row; *source* its source's id ("" for radio, files and web
    streams, which belong to none); *when* when it was played (or added);
    *pos* / *dur* how far it got; *extra* more words to match (a channel's
    programme on now)."""
    kind: str
    ident: str
    title: str
    row: dict
    source: str = ""
    when: float = 0.0
    pos: float = 0.0
    dur: float = 0.0
    extra: str = ""
    text: str = ""

    @property
    def key(self) -> str:
        return entry_key(self.source, self.kind, self.ident)

    @property
    def progress(self) -> float | None:
        return self.pos / self.dur if self.dur > 0 and self.pos > 0 else None

    def folded(self) -> str:
        if not self.text:
            self.text = fold(f"{self.title} {self.extra}")
        return self.text


def offline_keeps(e: Entry, downloaded: frozenset, series: frozenset) -> bool:
    """Whether *e* plays while offline mode is on (core/offline.py): a film
    or an episode whose file is downloaded (*downloaded*: (source, "movie" or
    "episode", stream id)), a series with one (*series*: (source, series
    id)), a file of this computer's - never what needs the network: a
    channel, a station, a web stream, a file on a network address."""
    if e.kind in (FILM, EPISODE):
        kind = "movie" if e.kind == FILM else "episode"
        own = e.row.get("stream_id" if e.kind == FILM else "id") if isinstance(e.row, dict) \
            else None
        return any((e.source, kind, str(i)) in downloaded
                   for i in (e.ident, own) if i not in (None, ""))
    if e.kind == SERIES:
        return (e.source, str(e.ident)) in series
    if e.kind == LOCAL:
        return "://" not in str(e.ident)
    return False


def entry_key(source: str, kind: str, ident: Any) -> str:
    """A title's key on the page: its source, the player's group, its key."""
    return f"{source or ''}|{GROUP.get(kind, kind)}:{ident}"


def item_key(row: Mapping[str, Any]) -> Any:
    """MainWindow._item_key: a row's id the way the player keys it."""
    return (row.get("stream_id") or row.get("series_id") or row.get("id")
            or row.get("_key"))


def playing_keys(group: str | None, key: Any, active: str,
                 series_id: Any = None) -> set[str]:
    """The page's keys of what plays: its title, the same channel played
    from another source (failover's "failover:<source>:<stream>"), and the
    series an episode is of."""
    out: set[str] = set()
    if group and key is not None:
        text = str(key)
        if group == "live" and text.startswith("failover:"):
            _f, pid, sid = (text.split(":", 2) + ["", ""])[:3]
            out.add(f"{pid}|live:{sid}")
        else:
            out.add(f"{active}|{group}:{text}")
            out.add(f"|{group}:{text}")
    if series_id is not None and group == "episode":
        out.add(f"{active}|series:{series_id}")
    return out


# -- what is matched ----------------------------------------------------------------

def terms_of(query: str) -> list[str]:
    return words(query)


def matches(e: Entry, chip: str, terms: Sequence[str]) -> bool:
    """*e* under the chip *chip* and with every word of *terms* in its
    title (or its programme on now)."""
    if chip != ALL and CHIP.get(e.kind, "") != chip:
        return False
    if not terms:
        return True
    text = e.folded()
    return all(t in text for t in terms)


# -- from the stores' rows ----------------------------------------------------------

def when_of(row: Mapping[str, Any]) -> float:
    """When a History row was played (its ISO time), 0 when it does not say."""
    stamp = row.get("_watched_at")
    if not isinstance(stamp, str) or not stamp:
        return 0.0
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        return 0.0


def _num(v: Any) -> float:
    return number(v or 0, 0.0)


def history_entries(rows: Iterable[Any], source: str) -> list[Entry]:
    """History's rows (HistoryStore.entries) as entries, as they are kept:
    the newest first. An episode kept before its series was is a film (it
    replays as one, as Home's did)."""
    out: list[Entry] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        kind = STORE_KIND.get(str(r.get("_kind") or ""))
        ident = r.get("_key")
        if kind is None or ident in (None, ""):
            continue
        ctx = r.get("_series_ctx")
        if kind == EPISODE and not (isinstance(ctx, dict) and ctx.get("series_id") is not None):
            kind = FILM
        if kind == SERIES:
            continue                    # History keeps what played: never a whole series
        out.append(Entry(kind, str(ident), str(r.get("name") or r.get("title") or ""), r,
                         "" if kind in SOURCELESS else source, when_of(r)))
    return out


def continue_entries(rows: Iterable[Any], source: str) -> list[Entry]:
    """Titles partly watched (ResumeStore.continue_watching and the files'
    own positions) as entries."""
    out: list[Entry] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        kind = STORE_KIND.get(str(r.get("_kind") or ""))
        if kind not in (FILM, EPISODE, LOCAL):
            continue
        ident = item_key(r)
        if ident in (None, ""):
            continue
        out.append(Entry(kind, str(ident), str(r.get("name") or r.get("title") or ""), r,
                         "" if kind == LOCAL else source, _num(r.get("_ts")),
                         _num(r.get("_resume_pos")), _num(r.get("_resume_dur"))))
    return out


def fav_rows(groups: Mapping[str, Sequence[Any]], id_key: str, locked: Iterable[str] = (),
             default: str = "all") -> list[dict]:
    """A FavoriteStore's rows each once, the default bucket's first, then
    its folders' by name - none from a folder that is *locked*."""
    shut = set(locked)
    names = [default] + sorted((g for g in groups if g != default), key=str.lower)
    seen: set[str] = set()
    out: list[dict] = []
    for g in names:
        if g in shut:
            continue
        for it in groups.get(g) or ():
            if not isinstance(it, dict):
                continue
            ident = it.get(id_key)
            if ident is None or str(ident) in seen:
                continue
            seen.add(str(ident))
            out.append(it)
    return out


def new_entries(rows: Iterable[Any], source: str) -> list[Entry]:
    """FollowedSeriesStore.with_new() as entries: one per series, its
    newest episode's time."""
    out: list[Entry] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        snap, new = r.get("snap"), r.get("new")
        if not isinstance(snap, dict) or not new or snap.get("series_id") is None:
            continue
        when = max((_num(e.get("added")) for e in new if isinstance(e, dict)), default=0.0)
        out.append(Entry(SERIES, str(snap["series_id"]),
                         str(snap.get("name") or snap.get("title") or ""),
                         {**snap, "_new": list(new)}, source, when))
    return out


def merged(*lists: Iterable[Entry], newest_first: bool = True) -> list[Entry]:
    """Entries of several sources as one list: each key once (the newest
    kept), the newest first."""
    best: dict[str, Entry] = {}
    for lst in lists:
        for e in lst:
            had = best.get(e.key)
            if had is None or e.when > had.when:
                best[e.key] = e
    return sorted(best.values(), key=lambda e: e.when, reverse=newest_first)


# -- the favourites' folders across every source ------------------------------------------

#: The favourites' stores, in the order a folder lists them: the section's
#: name (the window's), the kind of title it keeps, the id its rows go by.
FAV_SECTIONS = (("chan", CHANNEL, "stream_id"), ("movie", FILM, "stream_id"),
                ("series", SERIES, "series_id"), ("radio", RADIO, "stationuuid"),
                ("web", WEB, "url"))
_SECTION = {s: (kind, ident) for s, kind, ident in FAV_SECTIONS}
#: The catalogue a section's titles are of (the parental lock on
#: categories, a kids profile's test); stations and web streams none.
SECTION_MODE = {"chan": "live", "movie": "vod", "series": "series"}


@dataclass
class FavStore:
    """One favourites store as Mine's folders read it: its *source* (""
    for the stations' and the web streams', which are no source's), its
    *section* (``FAV_SECTIONS``), its *groups* - a folder's name -> its rows
    in their order, the default bucket the favourites in no folder -, the
    folders *locked* in it and their *colours* ({"color", "bgcolor"} by
    folder)."""
    source: str
    section: str
    groups: Mapping[str, Sequence[Any]]
    locked: frozenset[str] = frozenset()
    colours: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class Folder:
    """One folder as Mine shows it, whatever source and kind hold it: its
    titles in order (*entries*), how many of each section (*counts*),
    whether any store keeps it locked, its colour (the first store's that
    gives one) and the stores that hold it (*places*: (source, section))."""
    name: str
    entries: list[Entry] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    locked: bool = False
    colour: dict[str, str] = field(default_factory=dict)
    places: list[tuple[str, str]] = field(default_factory=list)


def folder_entry(source: str, section: str, group: str, row: Any) -> Entry | None:
    """A favourite of folder *group* in store (*source*, *section*) as a
    title: its row tagged with its section and folder (what the folders'
    moves act on); None for a row without its id."""
    if not isinstance(row, Mapping) or section not in _SECTION:
        return None
    kind, id_key = _SECTION[section]
    ident = row.get(id_key)
    if ident is None or ident == "":
        return None
    tagged = {**row, "_section": section, "_group": group}
    if kind == RADIO:
        ident = f"radio:{ident}"
        tagged.update(_kind="radio", _key=ident)
    elif kind == WEB:
        tagged.update(_kind="web", _key=row.get("url"))
    return Entry(kind, str(ident), str(row.get("name") or row.get("title") or ""), tagged,
                 "" if kind in SOURCELESS else str(source or ""))


def _colour(colours: Mapping[str, Any], name: str) -> dict[str, str]:
    got = colours.get(name) if isinstance(colours, Mapping) else None
    if not isinstance(got, Mapping):
        return {}
    return {k: str(v) for k, v in got.items() if k in ("color", "bgcolor") and v}


def fav_folders(stores: Sequence[FavStore], keep: Callable[[Entry], bool] | None = None,
                default: str = "all") -> tuple[Folder, dict[str, Folder]]:
    """Every store's favourites as Mine's folders: those in no folder
    (*default*) and every folder by name - one of a name across every
    source and kind ("Sport" of two sources is one folder). A folder lists
    the channels first, then the films, the series, the stations and the
    web streams; each kind's from the stores in the order given (the source
    in use's first), each store's in its own order, a title once per store.
    *keep* says whether a title may be shown (a kids profile's test, the
    parental lock on categories): one it leaves out is neither listed nor
    counted. (The favourites in no folder, the folders by name in the
    order of their names.)"""
    names: set[str] = set()
    for st in stores:
        names.update(str(g) for g in (st.groups or {}) if g != default)
    named = {n: Folder(n) for n in sorted(names, key=str.lower)}
    loose = Folder(default)
    order = {s: i for i, (s, _k, _i) in enumerate(FAV_SECTIONS)}
    ranked = sorted(range(len(stores)), key=lambda i: (order.get(stores[i].section, 99), i))
    for i in ranked:
        st = stores[i]
        if st.section not in _SECTION:
            continue
        for group, rows in (st.groups or {}).items():
            folder = loose if group == default else named.get(str(group))
            if folder is None:
                continue
            folder.places.append((st.source, st.section))
            seen: set[str] = set()
            for row in rows or ():
                e = folder_entry(st.source, st.section, str(group), row)
                if e is None or e.key in seen:
                    continue
                seen.add(e.key)
                if keep is not None:
                    try:
                        if not keep(e):
                            continue
                    except Exception:
                        continue            # fails closed: a test that broke shows nothing
                folder.entries.append(e)
                folder.counts[st.section] = folder.counts.get(st.section, 0) + 1
    for st in stores:                       # the given order: the source in use's first
        for name in st.locked or ():
            if name in named:
                named[name].locked = True
        for name, folder in named.items():
            if not folder.colour:
                folder.colour = _colour(st.colours, name)
    return loose, named


def reordered_within(entries: Sequence[Entry], keys: Sequence[str]) -> list[Entry]:
    """A folder's *entries* after titles were dragged into the order
    *keys* (the keys shown, in their new order), as their stores keep it:
    each store's titles among *keys* take the places they held, in the new
    order - one of another source or kind keeps its own place, as
    FavoriteStore.reorder keeps a row the view did not show."""
    rank = {k: i for i, k in enumerate(keys)}
    out = list(entries)
    slots: dict[tuple[str, str], list[int]] = {}
    for i, e in enumerate(entries):
        if e.key in rank:
            slots.setdefault((e.source, str(e.row.get("_section") or e.kind)), []).append(i)
    for held in slots.values():
        moved = sorted((entries[i] for i in held), key=lambda e: rank[e.key])
        for i, e in zip(held, moved, strict=True):
            out[i] = e
    return out


# -- History's action -----------------------------------------------------------------

PLAY, CONTINUE, AGAIN = "play", "continue", "again"


def action_of(e: Entry, pos: float = 0.0) -> str:
    """What a History row's button does: a channel, a station or a web
    stream plays; a title with a resume point continues; the rest plays
    again from the start."""
    if e.kind in (CHANNEL, RADIO, WEB):
        return PLAY
    return CONTINUE if pos > 0 else AGAIN


# -- the favourite channels by habit -----------------------------------------------------

#: How far back the habits are looked for.
HABIT_DAYS = 56
#: Half-life of a habit: a week four weeks back weighs half.
HABIT_HALF_LIFE = 28.0
#: "Usually on now": watched around this hour on at least this many days,
#: for this long (weighed), the top few only.
HABIT_MIN_DAYS = 2
HABIT_MIN_SCORE = 600.0
HABIT_TOP = 3


def _hour_of(key: str) -> tuple[date, int] | None:
    try:
        return date(int(key[0:4]), int(key[4:6]), int(key[6:8])), int(key[8:10])
    except (ValueError, IndexError):
        return None


def habit_score(hours: Mapping[str, float], now: datetime) -> tuple[float, int]:
    """(score, days) of one title's viewing log hours ("YYYYMMDDHH" ->
    seconds): the seconds watched within an hour of now's hour of the
    day, the same weekday weighing the most, a day of the same kind
    (weekday, weekend) more than another, the last weeks more than older
    ones; *days* the days it was watched at that time."""
    today = now.date()
    score = 0.0
    days: set[date] = set()
    weekend = today.weekday() >= 5
    for key, secs in hours.items():
        at = _hour_of(str(key))
        if at is None:
            continue
        day, hour = at
        age = (today - day).days
        if age < 0 or age > HABIT_DAYS:
            continue
        dist = abs(hour - now.hour)
        dist = min(dist, 24 - dist)
        if dist > 1:
            continue
        w_hour = 1.0 if dist == 0 else 0.5
        if day.weekday() == today.weekday():
            w_day = 1.0
        elif (day.weekday() >= 5) == weekend:
            w_day = 0.4
        else:
            w_day = 0.15
        s = _num(secs)
        score += s * w_hour * w_day * 0.5 ** (age / HABIT_HALF_LIFE)
        if s >= 60:
            days.add(day)
    return score, len(days)


def by_habit(entries: Sequence[Entry], scores: Mapping[str, tuple[float, int]]) -> list[Entry]:
    """*entries* the most watched at this time first; the rest as they
    were (the favourites' own order)."""
    order = {e.key: i for i, e in enumerate(entries)}
    return sorted(entries, key=lambda e: (-(scores.get(e.key, (0.0, 0))[0]), order[e.key]))


def habitual(entries: Sequence[Entry], scores: Mapping[str, tuple[float, int]],
             top: int = HABIT_TOP) -> set[str]:
    """The keys "usually on now" goes on: of the first *top* by habit,
    those watched at this time on more than one day, for long enough."""
    out: set[str] = set()
    for e in by_habit(entries, scores)[:top]:
        score, days = scores.get(e.key, (0.0, 0))
        if days >= HABIT_MIN_DAYS and score >= HABIT_MIN_SCORE:
            out.add(e.key)
    return out


def stats_ids(e: Entry) -> tuple[str, str]:
    """How the viewing log names *e*: (exact id, or "" ; a suffix any
    source's id of it ends with, or ""). A channel is its source's; a
    station is counted under whichever source was in use."""
    if e.kind == CHANNEL:
        return f"live:{e.source}:{e.ident}", ""
    if e.kind == RADIO:
        return "", f":{e.ident}"
    return "", ""


def habit_hours(entries: Sequence[Entry], titles: Mapping[str, Mapping[str, Any]]
                ) -> dict[str, dict[str, float]]:
    """Each entry's hours in the viewing log's *titles* (ViewingLog.titles:
    id -> {"k", "h"}), a station's of every source summed."""
    exact: dict[str, str] = {}
    suffix: dict[str, str] = {}
    for e in entries:
        tid, tail = stats_ids(e)
        if tid:
            exact[tid] = e.key
        elif tail:
            suffix[tail] = e.key
    out: dict[str, dict[str, float]] = {}
    for tid, t in titles.items():
        key = exact.get(tid)
        if key is None and suffix and tid.startswith("other:"):
            key = next((k for tail, k in suffix.items() if tid.endswith(tail)), None)
        if key is None or not isinstance(t, Mapping):
            continue
        hours = t.get("h")
        if not isinstance(hours, Mapping):
            continue
        mine = out.setdefault(key, {})
        for h, secs in hours.items():
            mine[str(h)] = mine.get(str(h), 0.0) + _num(secs)
    return out


# -- the queue ------------------------------------------------------------------------

def queue_ident(row: Mapping[str, Any], kind: str) -> str:
    """A Watch later title's id in the queue's order: a film by its stream
    id (else its TMDB id), a series by its series id."""
    if kind == FILM:
        sid = row.get("stream_id")
        return f"vod:{sid}" if sid is not None else f"vod:tmdb:{row.get('_tmdb_id')}"
    sid = row.get("series_id")
    return f"series:{sid}" if sid is not None else f"series:tmdb:{row.get('_tmdb_id')}"


def parse_order(raw: Any) -> list[str]:
    """The kept order, read back; anything else reads as none."""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw else []
        except ValueError:
            return []
    if not isinstance(raw, list):
        return []
    return list(dict.fromkeys(str(x) for x in raw if isinstance(x, (str, int))))[:ORDER_MAX]


def dump_order(order: Sequence[str]) -> str:
    return json.dumps(list(dict.fromkeys(order))[:ORDER_MAX], separators=(",", ":"))


def queue_order(items: Sequence[tuple[str, Any]], stored: Sequence[str]) -> list[tuple[str, Any]]:
    """*items* ((ident, value), in the order they were added - the first
    first) in the queue's order: those in the *stored* order as it says,
    then the rest - added since - after them, as they came."""
    have = dict(items)
    out = [(i, have[i]) for i in dict.fromkeys(stored) if i in have]
    placed = {i for i, _v in out}
    out += [(i, v) for i, v in items if i not in placed]
    return out


def moved(order: Sequence[str], ident: str, delta: int) -> list[str]:
    """*order* with *ident* moved *delta* places (negative: up)."""
    out = list(order)
    if ident not in out:
        return out
    i = out.index(ident)
    j = max(0, min(len(out) - 1, i + delta))
    out.insert(j, out.pop(i))
    return out


def moved_to(order: Sequence[str], idents: Sequence[str], index: int) -> list[str]:
    """*order* with *idents* taken out and put back before what was at
    *index* (a row dragged there); at the end past the last."""
    moving = [i for i in dict.fromkeys(idents) if i in order]
    if not moving:
        return list(order)
    gone = set(moving)
    before = [i for i in order[:max(0, index)] if i not in gone]
    after = [i for i in order[max(0, index):] if i not in gone]
    return before + moving + after


_HMS = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2})\s*$")


def length_of(row: Mapping[str, Any]) -> float:
    """A title's length in seconds as its row says it (a provider's
    duration_secs, a "01:52:00" or a number of minutes); 0 when unknown."""
    secs = _num(row.get("duration_secs"))
    if secs > 0:
        return secs
    for key in ("duration", "episode_run_time", "runtime"):
        v = row.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            minutes = number(v, lo=0)
            if minutes:
                return minutes * 60.0
        if isinstance(v, str) and v.strip():
            m = _HMS.match(v)
            if m:
                h, mm, ss = (digits(g or "0") for g in m.groups())
                total = (h or 0) * 3600 + (mm or 0) * 60 + (ss or 0)
                if total > 0:
                    return float(total)
            elif digits(v.strip()):
                return float(digits(v.strip()) or 0) * 60.0
    return 0.0


@dataclass(frozen=True)
class QueueSummary:
    """How much the queue holds: films and series, the time they take
    (left of those under way) where known, and how many say no length."""
    films: int = 0
    series: int = 0
    seconds: float = 0.0
    unknown: int = 0


def queue_summary(entries: Iterable[Entry]) -> QueueSummary:
    films = series = unknown = 0
    secs = 0.0
    for e in entries:
        if e.kind == FILM:
            films += 1
        else:
            series += 1
        length = e.dur or length_of(e.row)
        if length > 0:
            secs += max(0.0, length - e.pos) if 0 < e.pos < length else length
        else:
            unknown += 1
    return QueueSummary(films, series, secs, unknown)


# -- the timeline ---------------------------------------------------------------------

TODAY, YESTERDAY, WEEK, LAST_WEEK, MONTH, EARLIER = (
    "today", "yesterday", "week", "last_week", "month", "earlier")


def week_start_of(today: date, week_start: int = 0) -> date:
    """The first day of *today*'s week (*week_start*: 0 Monday ... 6 Sunday)."""
    return today - timedelta(days=(today.weekday() - week_start) % 7)


def day_group(when: float, now: datetime, week_start: int = 0) -> tuple[Any, ...]:
    """The timeline's group of a moment: (TODAY,), (YESTERDAY,), (WEEK,) -
    earlier this week -, (LAST_WEEK,), (MONTH, year, month); (EARLIER,)
    for a row that does not say when."""
    if when <= 0:
        return (EARLIER,)
    day = datetime.fromtimestamp(when).date()
    today = now.date()
    if day >= today:
        return (TODAY,)
    if day == today - timedelta(days=1):
        return (YESTERDAY,)
    start = week_start_of(today, week_start)
    if day >= start:
        return (WEEK,)
    if day >= start - timedelta(days=7):
        return (LAST_WEEK,)
    return (MONTH, day.year, day.month)


def timeline(entries: Iterable[Entry], now: datetime, week_start: int = 0
             ) -> list[tuple[tuple[Any, ...], list[Entry]]]:
    """*entries* (the newest first) in their days' groups, in order."""
    groups: dict[tuple[Any, ...], list[Entry]] = {}
    for e in entries:
        groups.setdefault(day_group(e.when, now, week_start), []).append(e)
    tail = groups.pop((EARLIER,), None)
    out = list(groups.items())
    if tail:
        out.append(((EARLIER,), tail))
    return out
