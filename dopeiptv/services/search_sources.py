"""The search across sources, the sources' side (core/search_all.py does
the matching): each source's client, its lists and the guide this computer
has of it - read off the window's thread and kept for the session.

- The source in use: the window's own client and its guide in memory.
- Every other source: a client of its own (providers/client.make_client),
  made once and kept while the source is as it was. Its lists are asked
  for once a session at most - a portal's never (``paged_lists``: a page
  at a time, hundreds of requests): only what was fetched before is read.
- Its guide: the copy on disk, never downloaded - read one guide at a time,
  its week put in a compact ``GuideTable``, the guide itself let go.

What a source's lists leave out (services/failover.Shown, per kind) and
what a kids profile never sees are left out before anything is matched.
Nothing here opens a stream: a play address is asked for only when a hit
is played (``play_address``).

The TV place's "Every source" (core/every_source.py, ui/mw_every_source.py)
reads its channels here too, the same once a session: ``channels`` asks a
source for its live lists alone (a full catalogue made since, or before,
takes them from what was read), and ``now_table`` another source's guide
around now from its copy on disk - never a download. The row picked there
has its programmes read with their descriptions on their own (``texts``):
that row's alone, the last few kept - from the source's guide written down
a channel at a time beside its copy on disk (providers/epg_texts.py), once
per copy: the guide is read whole once, not for every row.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from ..core import every_source as es
from ..core import search_all as sa
from ..core.log import log
from .failover import Shown

#: A guide's week is read again after this long (the week moves on).
GUIDE_FRESH_SECS = 3 * 3600
#: The moments (in a source's own guide time) its texts on disk cover: from
#: an hour before the first asked for (its guide shift made that much
#: longer meanwhile) to a day after it; outside that they are written again.
#: What ended before is never shown, so none of it is kept.
TEXTS_BEFORE = 3600
TEXTS_AHEAD = 86_400

#: The kinds of row, what lists them and their categories, and their id.
_LISTS = (("channels", "live", "live_streams", "live_categories", "stream_id"),
          ("movies", "vod", "vod_streams", "vod_categories", "stream_id"),
          ("series", "series", "series_list", "series_categories", "series_id"))


def signature(playlist: Mapping[str, Any]) -> str:
    """What a source's client is made from: another one when it changes."""
    keep = {k: playlist.get(k) for k in ("kind", "server", "username", "password",
                                         "mac", "token", "options")}
    return json.dumps(keep, sort_keys=True, default=str)


def source_of(playlist: Mapping[str, Any]) -> sa.Source:
    return sa.Source(str(playlist.get("id") or ""),
                     str(playlist.get("name") or playlist.get("server") or ""),
                     str(playlist.get("kind") or "xtream"))


def searchable(playlists: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The sources searched: every one the user keeps, but files of one's
    own and one turned off."""
    return [p for p in playlists if p.get("id") and p.get("kind") != "local"
            and not p.get("disabled")]


@dataclass
class Plan:
    """One source as the window has it, made on its thread for a worker:
    what its lists leave out of each kind (*shown*, by "live", "vod",
    "series"), whether a kids profile asks, and - for the source in use -
    the window's own client and guide. A catalogue made for another *key*
    (the profile, the lock, the source's rules) is made anew."""
    source: sa.Source
    playlist: Mapping[str, Any]
    shown: Mapping[str, Shown] = field(default_factory=dict)
    kids: bool = False
    #: "Only what a grown-up allows": by "live", "vod", "series" the
    #: categories of this source the kids profile may see (None, or no
    #: entry: every one not for adults).
    allowed: Mapping[str, frozenset[str] | None] = field(default_factory=dict)
    client: Any = None
    guide: Any = None
    key: tuple = ()


def _read(client: Any, mode: str, list_fn: str, cats_fn: str
          ) -> tuple[list, list | None, str]:
    """(rows, categories or None when they could not be read, why only in
    part) of one kind, as *client* has it."""
    if getattr(client, "paged_lists", False):
        loaded = getattr(client, "loaded", None)
        if not callable(loaded):
            return [], None, sa.PAGED
        try:
            got, held, whole = loaded(mode)
        except Exception as e:
            log.info("search: a portal's %s lists could not be read: %s", mode, type(e).__name__)
            return [], None, sa.PAGED
        return list(got or []), list(held or []), "" if whole else sa.PAGED
    fn = getattr(client, list_fn, None)
    if not callable(fn):
        return [], [], ""
    try:
        rows = list(fn(None) or [])
    except Exception as e:
        log.info("search: %s did not come (%s)", list_fn, type(e).__name__)
        return [], None, sa.FAILED
    cats: list | None
    try:
        cfn = getattr(client, cats_fn, None)
        cats = list(cfn() or []) if callable(cfn) else []
    except Exception:
        cats = None
    return rows, cats, ""


#: Every group of rows a catalogue has.
_GROUPS = tuple(g for g, *_rest in _LISTS)


def build_catalogue(plan: Plan, client: Any,
                    read: Callable[[Any, str, str, str], tuple[list, list | None, str]]
                    | None = None, groups: Iterable[str] = _GROUPS) -> sa.Catalogue:
    """(worker) The source's channels, films and series as its lists show
    them - and, for a kids profile, without what is for adults. *read*: one
    kind of its lists (``_read`` by default); *groups*: only these are
    asked for (the rest is empty)."""
    read = read or _read
    wanted = frozenset(groups)
    found: dict[str, list[dict]] = {g: [] for g in _GROUPS}
    names: dict[str, dict[str, str]] = {}
    renamed: dict[str, dict[str, str]] = {}
    partial = ""
    for group, mode, list_fn, cats_fn, ident in _LISTS:
        if group not in wanted:
            continue
        rows, cats, why = read(client, mode, list_fn, cats_fn)
        partial = partial or why
        shown = plan.shown.get(mode) or Shown()
        given = dict(shown.names)
        adult = sa.adult_ids(cats, given) if cats is not None else None
        known = (frozenset(str(c.get("category_id")) for c in cats
                           if isinstance(c, dict) and c.get("category_id") is not None)
                 if cats is not None else None)
        keep = shown.keep
        found[group] = [r for r in sa.visible(rows, keep, plan.kids, adult, known,
                                              plan.allowed.get(mode))
                        if r.get(ident) is not None]
        names[mode] = {str(c.get("category_id")): given.get(str(c.get("category_id")))
                       or str(c.get("category_name") or "")
                       for c in (cats or []) if isinstance(c, dict)}
        renamed[mode] = dict(shown.titles)
    return sa.Catalogue(plan.source, found["channels"], found["movies"], found["series"],
                        names, renamed, partial)


def _open_guide(path: str) -> Any:
    from ..providers.epg import XmltvGuide
    no_client: Any = None       # read from disk only: no client asked
    return XmltvGuide(no_client, None, cache_path=path)


def _guide_path(pid: str) -> str:
    from ..providers.epg import epg_cache_path
    return epg_cache_path(pid)


class _Writing:
    """A source's guide read to have its texts written down
    (providers/epg_texts.py): *guide* in memory while they are - None while
    it loads -, *ready* set once it is in, or there is none."""

    def __init__(self) -> None:
        self.guide: Any = None
        self.ready = threading.Event()


#: A source's texts on disk not looked for yet (``Sources._tx``).
_UNREAD = object()


def play_address(client: Any, row: Mapping[str, Any], fmt: str = "ts") -> str:
    """(worker) A live channel's address from *client*, to play: what a
    box that transcodes gives, else its live_url - only now, as it plays."""
    sid = row.get("stream_id")
    if sid is None:
        return ""
    play = getattr(client, "play_url", None)
    if callable(play):
        return str(play(sid, "play") or "")
    return str(client.live_url(sid, fmt) or "")


class Sources:
    """Every source's client, catalogue and guide week for the search, this
    session. Workers call ``catalogue`` and ``guide``, one at a time per
    source (and one guide read at a time); the window reads what is in
    with ``ready`` / ``guide_in``, and ``done`` says nothing is left to
    read."""

    def __init__(self, make_client: Callable[[Mapping[str, Any]], Any] | None = None,
                 guide_path: Callable[[str], str] | None = None,
                 open_guide: Callable[[str], Any] | None = None) -> None:
        if make_client is None:
            from ..providers.client import make_client as _make

            def make_client(playlist: Mapping[str, Any]) -> Any:
                return _make(dict(playlist))
        self._make: Callable[[Mapping[str, Any]], Any] = make_client
        self._guide_path = guide_path or _guide_path
        self._open_guide = open_guide or _open_guide
        self._lock = threading.Lock()
        self._guide_lock = threading.Lock()
        self._busy: dict[str, threading.Lock] = {}
        self._clients: dict[str, tuple[str, Any]] = {}
        self._cats: dict[str, tuple[tuple, sa.Catalogue]] = {}
        # A source's channels alone (Every source), under the key they were
        # made for - a full catalogue for the same key stands in for them.
        self._chans: dict[str, tuple[tuple, sa.Catalogue]] = {}
        # A source's guide around now (Every source): (key, table or None,
        # when) - the key the plan's and the copy on disk's.
        self._now: dict[str, tuple[tuple, es.NowTable | None, float]] = {}
        # The rows picked of a source (Every source's detail column): their
        # programmes with descriptions by row key - ((the copy on disk's key,
        # the guide offset), the listings, when) - the last es.TEXTS_KEPT.
        self._texts: dict[str, OrderedDict[str, tuple[tuple, list[dict], float]]] = {}
        # A source's texts on disk (providers/epg_texts.py), under the key
        # of the copy they are of: a GuideTexts, a _Writing while its guide
        # is read and they are written, None where they cannot be (a guide
        # without its tables, too many channels, a folder not written to:
        # read whole for each row, as before).
        self._tx: dict[str, tuple[tuple, Any]] = {}
        # A source's lists as read, per kind, while it is as it was: a
        # catalogue made for another key (a profile, the lock) filters
        # these again instead of asking the source once more.
        self._rows: dict[str, tuple[str, dict[str, tuple[list, list | None, str]]]] = {}
        # A source's guide week (None: it has none here), under the key it
        # was read for, and when.
        self._guides: dict[str, tuple[tuple, sa.GuideTable | None, float]] = {}
        # What a source was read as (signature()), to let it go once changed.
        self._sigs: dict[str, str] = {}
        # Generations: a source's moves on as what was read of it is let go
        # (keep), the weeks' as every guide week is (release_guides). What
        # a worker read under an older one is not kept: it uses it, once.
        self._gen: dict[str, int] = {}
        self._weeks_gen = 0

    # -- clients --------------------------------------------------------------------

    def client(self, playlist: Mapping[str, Any]) -> Any:
        """The search's own client of *playlist*, made once while the source
        is as it was (on any thread: making one asks nothing of it)."""
        pid, sig = str(playlist.get("id") or ""), signature(playlist)
        with self._lock:
            have = self._clients.get(pid)
            if have is not None and have[0] == sig:
                return have[1]
        made = self._make(dict(playlist))
        with self._lock:
            have = self._clients.get(pid)
            if have is not None and have[0] == sig:
                return have[1]
            self._clients[pid] = (sig, made)
        return made

    def _busy_for(self, pid: str) -> threading.Lock:
        with self._lock:
            return self._busy.setdefault(pid, threading.Lock())

    # -- catalogues -----------------------------------------------------------------

    def ready(self, plan: Plan) -> sa.Catalogue | None:
        with self._lock:
            got = self._cats.get(plan.source.id)
        return got[1] if got is not None and got[0] == plan.key else None

    def catalogue(self, plan: Plan) -> sa.Catalogue:
        """(worker) The source's catalogue - made now unless one for the
        same key is in. A source that cannot be read gives an empty one
        (FAILED), read again only once its lists are refreshed (drop)."""
        pid = plan.source.id
        with self._busy_for(pid):
            have = self.ready(plan)
            if have is not None:
                return have
            with self._lock:
                gen = self._gen.get(pid, 0)
            try:
                client = plan.client if plan.client is not None else self.client(plan.playlist)
                cat = build_catalogue(plan, client, partial(self._read_kept, plan, gen))
            except Exception as e:
                log.info("search: a source could not be read (%s)", type(e).__name__)
                cat = sa.Catalogue(plan.source, partial=sa.FAILED)
            with self._lock:
                if gen == self._gen.get(pid, 0):
                    self._cats[pid] = (plan.key, cat)
                    self._sigs[pid] = signature(plan.playlist)
            log.info("search: %s - %d channels, %d films, %d series%s", plan.source.kind,
                     len(cat.channels), len(cat.movies), len(cat.series),
                     f" (only in part: {cat.partial})" if cat.partial else "")
            return cat

    def _read_kept(self, plan: Plan, gen: int, client: Any, mode: str, list_fn: str,
                   cats_fn: str) -> tuple[list, list | None, str]:
        """(worker) One kind of the source's lists: asked of it once while
        the source is as it was (its client's list cache lasts minutes - asked
        again, a big catalogue came down once more). A portal's is read anew
        each time: only what it holds, nothing asked. A read that failed is
        not kept."""
        if getattr(client, "paged_lists", False):
            return _read(client, mode, list_fn, cats_fn)
        pid, sig = plan.source.id, signature(plan.playlist)
        with self._lock:
            have = self._rows.get(pid)
            kept = have[1].get(mode) if have is not None and have[0] == sig else None
        if kept is not None:
            return kept
        got = _read(client, mode, list_fn, cats_fn)
        if got[2] != sa.FAILED:
            with self._lock:
                if gen != self._gen.get(pid, 0):
                    return got              # the source was let go meanwhile
                have = self._rows.get(pid)
                if have is None or have[0] != sig:
                    have = self._rows[pid] = (sig, {})
                have[1][mode] = got
        return got

    # -- channels alone (Every source) ----------------------------------------------

    def channels_ready(self, plan: Plan) -> sa.Catalogue | None:
        """The source's channels as its lists show them, made for the plan's
        key - a full catalogue's (the Search page's), else the channels'
        own - or None while there are none."""
        with self._lock:
            full = self._cats.get(plan.source.id)
            own = self._chans.get(plan.source.id)
        if full is not None and full[0] == plan.key:
            return full[1]
        if own is not None and own[0] == plan.key:
            return own[1]
        return None

    def channels(self, plan: Plan) -> sa.Catalogue:
        """(worker) The source's channels as its lists show them: its live
        lists asked for once while the source is as it was (its films and
        series never, for this), a portal's only as loaded before. A source
        that cannot be read gives none (FAILED)."""
        pid = plan.source.id
        with self._busy_for(pid):
            have = self.channels_ready(plan)
            if have is not None:
                return have
            with self._lock:
                gen = self._gen.get(pid, 0)
            try:
                client = plan.client if plan.client is not None else self.client(plan.playlist)
                cat = build_catalogue(plan, client, partial(self._read_kept, plan, gen),
                                      groups=("channels",))
            except Exception as e:
                log.info("every source: a source could not be read (%s)", type(e).__name__)
                cat = sa.Catalogue(plan.source, partial=sa.FAILED)
            with self._lock:
                if gen == self._gen.get(pid, 0):
                    self._chans[pid] = (plan.key, cat)
                    self._sigs[pid] = signature(plan.playlist)
            log.info("every source: %s - %d channels%s", plan.source.kind, len(cat.channels),
                     f" (only in part: {cat.partial})" if cat.partial else "")
            return cat

    def now_in(self, plan: Plan, now: float | None = None) -> es.NowTable | None:
        """The source's guide around now as it is in, for the plan's key."""
        now = time.time() if now is None else now
        with self._lock:
            got = self._now.get(plan.source.id)
        if got is None or got[0][0] != plan.key or now - got[2] >= GUIDE_FRESH_SECS:
            return None
        return got[1]

    def now_looked_for(self, plan: Plan, now: float | None = None, behind: float = 0.0) -> bool:
        """(the window's thread) Whether the source's guide around now was
        looked for as it is now (*behind*: as now_table's): a table in, or
        none on disk then."""
        now = time.time() if now is None else now
        key = self._disk_key(plan.source.id)
        with self._lock:
            got = self._now.get(plan.source.id)
        return (got is not None and got[0] == (plan.key, key, max(0.0, behind))
                and now - got[2] < GUIDE_FRESH_SECS)

    def now_table(self, plan: Plan, cat: sa.Catalogue, now: float | None = None,
                  behind: float = 0.0) -> es.NowTable | None:
        """(worker) Another source's guide around now over *cat*'s channels,
        by the keys they play under (core/every_source.play_key): from the
        copy this computer has on disk - never a download, never the
        window's guide (the source in use's rows are drawn from that) - one
        guide from disk read at a time and let go once its table is made.
        *behind*: how many seconds the source's guide runs behind (its own
        shift): what is on in its time is kept though it ended by now.
        None without one (remembered until the copy on disk changes)."""
        now = time.time() if now is None else now
        pid = plan.source.id
        with self._busy_for(pid):
            key = self._disk_key(pid)
            with self._lock:
                got = self._now.get(pid)
                gen = self._gen.get(pid, 0)
            made = (plan.key, key, max(0.0, behind))
            if got is not None and got[0] == made and now - got[2] < GUIDE_FRESH_SECS:
                return got[1]
            table: es.NowTable | None = None
            try:
                if key is not None:
                    with self._guide_lock:
                        g = self._open_guide(str(key[1]))
                        if g.load_cached():
                            table = es.NowTable(
                                cat.channels, lambda ch: es.play_key(pid, ch.get("stream_id")),
                                g.schedule, now - max(0.0, behind),
                                es.AHEAD_SECS + max(0.0, behind))
                        del g
            except Exception as e:
                log.info("every source: a guide could not be read (%s)", type(e).__name__)
                table = None
            with self._lock:
                if gen != self._gen.get(pid, 0):
                    return table            # the source was let go meanwhile
                self._now[pid] = (made, table, now)
                self._sigs[pid] = signature(plan.playlist)
            if table is not None:
                log.info("every source: a guide around now - %d channels%s", len(table),
                         " (cut short)" if table.cut else "")
            return table

    def texts_in(self, pid: str, key: str, delay_minutes: int = 0,
                 now: float | None = None) -> list[dict] | None:
        """(the window's thread) The programmes of row *key* of source *pid*
        with their descriptions, as ``texts`` read them for it at the guide
        offset *delay_minutes* - None while they were not, or the copy on
        disk or the offset changed since."""
        now = time.time() if now is None else now
        disk = self._disk_key(pid)
        with self._lock:
            kept = self._texts.get(pid)
            got = kept.get(key) if kept is not None else None
            if got is not None and kept is not None:
                kept.move_to_end(key)
        if (got is None or got[0] != (disk, int(delay_minutes))
                or now - got[2] >= GUIDE_FRESH_SECS):
            return None
        return got[1]

    def texts(self, pid: str, row: Mapping[str, Any], key: str, delay_minutes: int = 0,
              now: float | None = None) -> list[dict]:
        """(worker) Row *row* of source *pid* - its own row, listed under
        *key* -: its programmes from the one on now in that source's own
        time (*delay_minutes* its guide offset) with their descriptions, for
        the detail column. From the copy this computer has on disk - never a
        download -: its texts written down a channel at a time (``_texts_of``),
        else the guide read whole, one at a time and let go once this row's
        are taken; only this row's kept, for the last es.TEXTS_KEPT rows of
        the source. [] without one."""
        now = time.time() if now is None else now
        disk = self._disk_key(pid)
        made = (disk, int(delay_minutes))
        with self._lock:
            kept = self._texts.get(pid)
            got = kept.get(key) if kept is not None else None
            gen = self._gen.get(pid, 0)
        if got is not None and got[0] == made and now - got[2] < GUIDE_FRESH_SECS:
            return got[1]
        out: list[dict] = []
        try:
            if disk is not None:
                out = self._texts_of(pid, disk, gen, dict(row), now - int(delay_minutes) * 60)
        except Exception as e:
            log.info("every source: a programme's words could not be read (%s)",
                     type(e).__name__)
            out = []
        with self._lock:
            if gen != self._gen.get(pid, 0):
                return out                  # the source was let go meanwhile
            kept = self._texts.setdefault(pid, OrderedDict())
            kept[key] = (made, out, now)
            kept.move_to_end(key)
            while len(kept) > es.TEXTS_KEPT:
                kept.popitem(last=False)
        return out

    def _texts_of(self, pid: str, disk: tuple, gen: int, row: dict, at: float) -> list[dict]:
        """(worker) Row *row*'s programmes from *at* (its guide's own time)
        with their descriptions: from the source's texts on disk
        (providers/epg_texts.py) - that channel's block alone; while they are
        being written, from the guide read for them; else the guide is read
        now, this row's taken from it, and its texts written from it on a
        thread of their own (``_texts_write``). A guide whose texts cannot be
        written is read whole for each row, as before (``_texts_whole``)."""
        from ..providers import epg_texts
        for _ in range(4):
            with self._lock:
                have = self._tx.get(pid)
                got = have[1] if have is not None and have[0] == disk else _UNREAD
            if isinstance(got, _Writing):
                got.ready.wait()
                guide = got.guide
                if guide is not None:
                    return es.listings_of(guide.schedule(row), at)
                continue                    # written by now, or none can be
            if got is None:
                return self._texts_whole(disk, row, at)
            if got is _UNREAD:
                got = epg_texts.read(epg_texts.path_for(str(disk[1])), (disk[3], disk[2]))
                with self._lock:
                    have = self._tx.get(pid)
                    if (got is not None and gen == self._gen.get(pid, 0)
                            and (have is None or have[0] != disk)):
                        self._tx[pid] = (disk, got)
            if isinstance(got, epg_texts.GuideTexts) and got.covers(at):
                try:
                    return es.listings_of(got.schedule(row), at)
                except (ValueError, OSError) as e:
                    # Made again by another copy of the app (read it again),
                    # or a block that cannot be read: written anew.
                    stale = isinstance(e, epg_texts.Stale)
                    log.info("every source: a guide's words on disk %s (%s)",
                             "changed" if stale else "could not be read", type(e).__name__)
                    with self._lock:
                        if self._tx.get(pid, (None, None))[1] is got:
                            del self._tx[pid]
                    if not stale:
                        self._texts_drop(got.path)
                    continue
            mine = self._texts_write(pid, disk, row, at)
            if mine is not None:
                return mine
        return self._texts_whole(disk, row, at)

    def _texts_whole(self, disk: tuple, row: dict, at: float) -> list[dict]:
        """(worker) Row *row*'s programmes from the source's guide read whole
        from disk, one guide at a time, let go once they are taken."""
        out: list[dict] = []
        with self._guide_lock:
            g = self._open_guide(str(disk[1]))
            if g.load_cached():
                out = es.listings_of(g.schedule(row), at)
            del g
        return out

    def _texts_write(self, pid: str, disk: tuple, row: dict, at: float) -> list[dict] | None:
        """(worker) The source's guide read whole now - this row's taken
        from it - and handed to a thread of its own that writes its texts
        down (``_texts_writer``), the one-guide lock held until that is done:
        rows picked meanwhile are read from it. None when another worker
        took this on first (look again)."""
        job = _Writing()
        with self._lock:
            have = self._tx.get(pid)
            if have is not None and have[0] == disk and isinstance(have[1], _Writing):
                return None
            self._tx[pid] = (disk, job)
        handed = False
        self._guide_lock.acquire()
        try:
            g = self._open_guide(str(disk[1]))
            if not g.load_cached():
                return []
            out = es.listings_of(g.schedule(row), at)
            if callable(getattr(g, "tables", None)):
                job.guide = g
                try:
                    threading.Thread(target=self._texts_writer, args=(pid, disk, job, int(at)),
                                     name="dopeiptv-guide-texts", daemon=True).start()
                    handed = True
                except RuntimeError:
                    log.info("every source: no thread to write a guide's words down")
            return out
        finally:
            if not handed:
                with self._lock:
                    if self._tx.get(pid, (None, None))[1] is job:
                        self._tx[pid] = (disk, None)
                job.guide = None
                self._guide_lock.release()
            job.ready.set()

    def _texts_writer(self, pid: str, disk: tuple, job: _Writing, at: int) -> None:
        """(its own thread) The texts of the guide *job* holds written down
        beside its copy on disk; the guide let go and the one-guide lock
        released after, whatever happens."""
        from ..providers import epg_texts
        made: Any = None
        try:
            made = epg_texts.write(job.guide, epg_texts.path_for(str(disk[1])), (disk[3], disk[2]),
                                   at - TEXTS_BEFORE, at + TEXTS_AHEAD, es.LISTINGS, es.TEXT_MAX)
        except Exception as e:
            log.info("every source: a guide's words could not be written down (%s)",
                     type(e).__name__)
        finally:
            with self._lock:
                if self._tx.get(pid, (None, None))[1] is job:
                    self._tx[pid] = (disk, made)
            job.guide = None
            self._guide_lock.release()
        if made is not None:
            log.info("every source: a guide's words written down - %d channels", len(made))

    @staticmethod
    def _texts_drop(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass

    # -- guides ---------------------------------------------------------------------

    def _guide_key(self, plan: Plan) -> tuple | None:
        """What tells a guide's week apart: the window's guide in memory
        (the source in use), else the copy on disk and when it was written;
        None when there is none."""
        if plan.guide is not None:
            loaded = getattr(plan.guide, "is_loaded", None)
            return ("memory", id(plan.guide)) if callable(loaded) and loaded() else None
        return self._disk_key(plan.source.id)

    def _disk_key(self, pid: str) -> tuple | None:
        """The source's guide on disk, when it was written (ns) and how large
        it is; None without."""
        path = self._guide_path(pid)
        try:
            st = os.stat(path)
        except OSError:
            return None
        return ("disk", path, st.st_mtime_ns, st.st_size)

    def _guide_entry(self, plan: Plan, now: float, key: tuple | None
                     ) -> tuple[tuple, sa.GuideTable | None, float] | None:
        with self._lock:
            got = self._guides.get(plan.source.id)
        if got is None or got[0] != (plan.key, key) or now - got[2] >= GUIDE_FRESH_SECS:
            return None
        return got

    def guide(self, plan: Plan, cat: sa.Catalogue, now: float | None = None
              ) -> sa.GuideTable | None:
        """(worker) A week of the source's guide from now, over *cat*'s
        channels - from the window's guide in memory, or the copy on disk;
        never a download. None without one (remembered: not looked for
        again until there is one). A week read while the weeks or the source
        were let go is this caller's to use, not kept."""
        now = time.time() if now is None else now
        pid = plan.source.id
        with self._busy_for(pid):
            key = self._guide_key(plan)
            got = self._guide_entry(plan, now, key)
            if got is not None:
                return got[1]
            with self._lock:
                gen = (self._gen.get(pid, 0), self._weeks_gen)
            table: sa.GuideTable | None = None
            try:
                if key is not None:
                    # One week built at a time, and one guide from disk in
                    # memory at a time: building one is the peak.
                    with self._guide_lock:
                        if plan.guide is not None:
                            table = sa.GuideTable(plan.source, cat.channels,
                                                  plan.guide.schedule, now)
                        else:
                            g = self._open_guide(str(key[1]))
                            if g.load_cached():
                                table = sa.GuideTable(plan.source, cat.channels, g.schedule, now)
                            del g
            except Exception as e:
                log.info("search: a guide could not be read (%s)", type(e).__name__)
                table = None
            with self._lock:
                if gen != (self._gen.get(pid, 0), self._weeks_gen):
                    return table
                self._guides[pid] = ((plan.key, key), table, now)
                self._sigs[pid] = signature(plan.playlist)
            if table is not None:
                log.info("search: a guide's week - %d airings%s", len(table),
                         " (cut short)" if table.cut else "")
            return table

    def guide_in(self, plan: Plan, now: float | None = None) -> sa.GuideTable | None:
        """The source's guide week as it is in, for its catalogue's key."""
        now = time.time() if now is None else now
        with self._lock:
            got = self._guides.get(plan.source.id)
        if got is None or got[0][0] != plan.key or now - got[2] >= GUIDE_FRESH_SECS:
            return None
        return got[1]

    def done(self, plan: Plan, now: float | None = None) -> bool:
        """(the window's thread) Whether the source's lists are in and its
        guide was looked for, as it is now - nothing more to read."""
        if self.ready(plan) is None:
            return False
        now = time.time() if now is None else now
        return self._guide_entry(plan, now, self._guide_key(plan)) is not None

    # -- letting go -----------------------------------------------------------------

    def _stores(self) -> tuple[dict, ...]:
        """Everything kept per source."""
        return (self._clients, self._cats, self._chans, self._guides, self._now,
                self._rows, self._sigs, self._texts, self._tx)

    def forget(self, keep: Iterable[str] = ()) -> None:
        """Everything of the sources not in *keep* let go (removed ones)."""
        ids = set(keep)
        with self._lock:
            for store in self._stores():
                for pid in [p for p in store if p not in ids]:
                    store.pop(pid, None)

    def keep(self, signatures: Mapping[str, str]) -> None:
        """(the window's thread) Only the sources in *signatures* (id ->
        signature()) as they are now: everything read of one removed or
        changed since let go at once - and what a worker is reading of it
        now is not kept."""
        with self._lock:
            gone = set()
            for pid in (set(self._busy) | set(self._clients) | set(self._cats)
                        | set(self._guides) | set(self._rows) | set(self._chans)
                        | set(self._now) | set(self._texts) | set(self._tx)):
                sig = signatures.get(pid)
                had = [self._sigs.get(pid)] + [h[0] for h in (self._clients.get(pid),
                                                              self._rows.get(pid)) if h]
                if sig is None or any(h is not None and h != sig for h in had):
                    gone.add(pid)
            for pid in gone:
                self._gen[pid] = self._gen.get(pid, 0) + 1
                for store in self._stores():
                    store.pop(pid, None)

    def release_guides(self) -> int:
        """(any thread) Every guide week let go - the source in use's too, it
        is made again from the guide the window has - and one being read now
        not kept; each is read again when next asked for. How many went."""
        with self._lock:
            self._weeks_gen += 1
            gone = [pid for pid, got in self._guides.items() if got[1] is not None]
            for pid in gone:
                del self._guides[pid]
        return len(gone)

    def drop(self, pid: str) -> None:
        """One source's catalogue and guide week made anew next time (its
        lists were refreshed)."""
        with self._lock:
            for store in (self._cats, self._chans, self._guides, self._now, self._rows,
                          self._texts):
                store.pop(pid, None)
