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
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from ..core import search_all as sa
from ..core.log import log
from .failover import Shown

#: A guide's week is read again after this long (the week moves on).
GUIDE_FRESH_SECS = 3 * 3600

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


def build_catalogue(plan: Plan, client: Any,
                    read: Callable[[Any, str, str, str], tuple[list, list | None, str]]
                    | None = None) -> sa.Catalogue:
    """(worker) The source's channels, films and series as its lists show
    them - and, for a kids profile, without what is for adults. *read*: one
    kind of its lists (``_read`` by default)."""
    read = read or _read
    found: dict[str, list[dict]] = {}
    names: dict[str, dict[str, str]] = {}
    renamed: dict[str, dict[str, str]] = {}
    partial = ""
    for group, mode, list_fn, cats_fn, ident in _LISTS:
        rows, cats, why = read(client, mode, list_fn, cats_fn)
        partial = partial or why
        shown = plan.shown.get(mode) or Shown()
        given = dict(shown.names)
        adult = sa.adult_ids(cats, given) if cats is not None else None
        keep = shown.keep
        found[group] = [r for r in sa.visible(rows, keep, plan.kids, adult)
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
        # A source's lists as read, per kind, while it is as it was: a
        # catalogue made for another key (a profile, the lock) filters
        # these again instead of asking the source once more.
        self._rows: dict[str, tuple[str, dict[str, tuple[list, list | None, str]]]] = {}
        # A source's guide week (None: it has none here), under the key it
        # was read for, and when.
        self._guides: dict[str, tuple[tuple, sa.GuideTable | None, float]] = {}

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
        with self._busy_for(plan.source.id):
            have = self.ready(plan)
            if have is not None:
                return have
            try:
                client = plan.client if plan.client is not None else self.client(plan.playlist)
                cat = build_catalogue(plan, client, partial(self._read_kept, plan))
            except Exception as e:
                log.info("search: a source could not be read (%s)", type(e).__name__)
                cat = sa.Catalogue(plan.source, partial=sa.FAILED)
            with self._lock:
                self._cats[plan.source.id] = (plan.key, cat)
            log.info("search: %s - %d channels, %d films, %d series%s", plan.source.kind,
                     len(cat.channels), len(cat.movies), len(cat.series),
                     f" (only in part: {cat.partial})" if cat.partial else "")
            return cat

    def _read_kept(self, plan: Plan, client: Any, mode: str, list_fn: str, cats_fn: str
                   ) -> tuple[list, list | None, str]:
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
                have = self._rows.get(pid)
                if have is None or have[0] != sig:
                    have = self._rows[pid] = (sig, {})
                have[1][mode] = got
        return got

    # -- guides ---------------------------------------------------------------------

    def _guide_key(self, plan: Plan) -> tuple | None:
        """What tells a guide's week apart: the window's guide in memory
        (the source in use), else the copy on disk and when it was written;
        None when there is none."""
        if plan.guide is not None:
            loaded = getattr(plan.guide, "is_loaded", None)
            return ("memory", id(plan.guide)) if callable(loaded) and loaded() else None
        path = self._guide_path(plan.source.id)
        try:
            return ("disk", path, os.stat(path).st_mtime)
        except OSError:
            return None

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
        again until there is one)."""
        now = time.time() if now is None else now
        with self._busy_for(plan.source.id):
            key = self._guide_key(plan)
            got = self._guide_entry(plan, now, key)
            if got is not None:
                return got[1]
            table: sa.GuideTable | None = None
            try:
                if key is None:
                    table = None
                elif plan.guide is not None:
                    table = sa.GuideTable(plan.source, cat.channels, plan.guide.schedule, now)
                else:
                    with self._guide_lock:          # one guide in memory at a time
                        g = self._open_guide(str(key[1]))
                        if g.load_cached():
                            table = sa.GuideTable(plan.source, cat.channels, g.schedule, now)
                        del g
            except Exception as e:
                log.info("search: a guide could not be read (%s)", type(e).__name__)
                table = None
            with self._lock:
                self._guides[plan.source.id] = ((plan.key, key), table, now)
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

    def forget(self, keep: Iterable[str] = ()) -> None:
        """Everything of the sources not in *keep* let go (removed ones)."""
        ids = set(keep)
        with self._lock:
            for store in (self._clients, self._cats, self._guides, self._rows):
                for pid in [p for p in store if p not in ids]:
                    store.pop(pid, None)

    def drop(self, pid: str) -> None:
        """One source's catalogue and guide week made anew next time (its
        lists were refreshed)."""
        with self._lock:
            self._cats.pop(pid, None)
            self._guides.pop(pid, None)
            self._rows.pop(pid, None)
