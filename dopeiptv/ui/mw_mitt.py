"""MainWindow mixin: Mine ("Mitt") - favorites, Watch later and History in
one page in the middle pane (ui/mitt_page.py draws it, core/mitt.py works
out what it shows). ``_mitt_show`` puts it there (the sidebar's entry).

What it reads. The stores the window holds for the profile in use and the
source in use (favorites, History, resume points, followed series, Watch
later, the stations' and web streams' favorites), and the same stores of
every other source read from the settings (the profile's own: pinned()) -
copied on the window's thread, parsed, matched and sorted on a worker of
the page's own; the viewing log (core/viewing_stats.py) for the habits. A
kids profile sees the source in use alone, as the lists do, with nothing
for adults; a favorites folder locked stays out while the lock is shut, and
so does a favourite of a category locked (every source's, as History's).
Nothing is written but the queue's order (core/mitt.ORDER_KEY, a profile's
own) and what the person does: a heart, a move, a removal.

The Favorite folders tab is every source's too: one folder of a name
whatever source and kind hold it (core/mitt.fav_folders, built on the
worker from the stores copied: _mitt_fav_snapshot), each title saying its
source and playing from it as the Overview plays it. What is done to a
title acts on its own source's store; a folder renamed, removed, locked,
opened or coloured is so in every source that holds it - all of them or
none (_mitt_change puts back what it touched when one store fails, and
says so). A folder locked in any source is locked; its PIN is asked as the
lists ask it.

What it plays. Everything through the window's own paths, as the lists,
Home's shelves and the series page play it: a channel through
play_live_channel (one of another source as the Search page plays it,
without switching: mw_search_all._play_channel_elsewhere), a film and an
episode through _start_playback with their resume points, marks, Trakt and
Simkl, History and a media server's reports - Continue (and History's
Continue) where it got to without the question, Play again from the start;
a series opens its page (Back comes here); a station, a web stream and a
local file as Radio, Open address and Local files play them. A film or a
series of another source switches to it first (_sa_on_source). The page
stays; what plays is marked on it.

A title of Watch later watched to the end (or marked watched) leaves the
queue by itself (``_mitt_watched``), said in a note. Private viewing keeps
nothing new anyway (the stores check it); what was typed in the page's
search is forgotten as the page is left.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import partial
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QObject, QPoint, QThreadPool, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QInputDialog, QMenu

from ..core import mitt, places, privacy, profiles
from ..core.log import log
from ..core.privacy import for_log
from ..core.stores import (
    CategoryOverrides, FAV_DEFAULT_GROUP, FavoriteStore, FollowedSeriesStore, HistoryStore,
    kept_place, lock_hides,
)
from ..core.workers import run_async
from ..i18n import tr
from ..media.players import launch_player
from .media_cards import Card, ChannelRow, NowNext
from .mitt_page import FolderItemsView, FolderTileView, Heading, MittPage
from .mw_pane_follow import from_pane
from .qt_i18n import app_locale, format_month, format_when

#: The favorites' sections: (section, the window's store, its id key, the
#: kind of entry it holds).
_FAV_SECTIONS = (("chan", "favs", "stream_id", mitt.CHANNEL),
                 ("movie", "movie_favs", "stream_id", mitt.FILM),
                 ("series", "series_favs", "series_id", mitt.SERIES),
                 ("radio", "radio_favs", "stationuuid", mitt.RADIO),
                 ("web", "web_favs", "url", mitt.WEB))
_KIND_WORD = {mitt.CHANNEL: "mitt_kind_channel", mitt.FILM: "mitt_kind_film",
              mitt.SERIES: "mitt_kind_series", mitt.EPISODE: "misc_episode",
              mitt.RADIO: "nav_radio", mitt.LOCAL: "mitt_kind_local", mitt.WEB: "mitt_kind_web"}
#: The favorites that are no folder's (the default bucket of every kind).
LOOSE = "__loose__"
#: How often the page looks at what plays while it is on screen.
TICK_MS = 1000
#: A change on the page drawn again after this long (a few at once: once).
REFRESH_MS = 400


class _Raw:
    """A store's settings for a worker: the values copied on the window's
    thread, nothing written anywhere (a store healing its rows on load)."""

    def __init__(self, values: dict[str, Any]) -> None:
        self._v = dict(values)

    def value(self, key: str, default: Any = None) -> Any:
        return self._v.get(key, default)

    def setValue(self, key: str, value: Any) -> None:  # noqa: N802 - Qt's name
        self._v[key] = value

    def remove(self, key: str) -> None:
        self._v.pop(key, None)

    def allKeys(self) -> list[str]:  # noqa: N802
        return list(self._v)


@dataclass
class _Source:
    """One source's stores as copied on the window's thread."""
    pid: str
    name: str
    active: bool
    history: list = field(default_factory=list)
    resume_raw: str = ""
    cont: list | None = None
    favs: dict = field(default_factory=dict)
    locked: set = field(default_factory=set)
    new: list | None = None
    raw: dict = field(default_factory=dict)
    #: Another source's categories locked while the lock is shut, per mode
    #: (the source in use's rows come filtered).
    shut: dict = field(default_factory=dict)


def _lock_keeps(row: Any, shut: dict) -> bool:
    """(the worker) Another source's kept row as its lists may show it:
    none of a category locked while the lock is shut - nor, while one of
    its kind is, one kept without its category (no catalogue to ask)."""
    return not shut or lock_hides(row, shut) is False


#: The key each section's store keeps a source's favourites under, and the
#: id its rows go by (MainWindow._build_source_stores).
_FAV_KEY = {"chan": "favorites_{}", "movie": "movie_favorites_{}",
            "series": "series_favorites_{}"}
_ID_KEY = {s: ident for s, _k, ident in mitt.FAV_SECTIONS}


def _fav_key(section: str, pid: str) -> str:
    return _FAV_KEY[section].format(pid)


def _json_of(raw: Any, kind: type) -> Any:
    """A JSON value kept in the settings as *kind* (a list, a dict), or an
    empty one when it is anything else."""
    try:
        got = json.loads(raw) if isinstance(raw, (str, bytes)) and raw else None
    except ValueError:
        got = None
    return got if isinstance(got, kind) else kind()


@dataclass
class _FavCopy:
    """One favourites store as copied on the window's thread for Mine's
    folders: the window's own store's folders (*groups*, the source in use's
    and the stations' and web streams'), or another source's as kept (*raw*,
    parsed on the worker); the folders locked in it and their colours, as
    kept."""
    source: str
    section: str
    key: str
    groups: dict | None = None
    raw: str = ""
    locked: str = ""
    colours: str = ""


@dataclass
class _FavSnap:
    """What Mine's folders are made of, copied on the window's thread:
    every store (the source in use's first, then every other source's in
    the user's order, then the stations' and web streams'), a kids
    profile's test, per source the categories locked while the lock is
    shut, and what the source in use's catalogue said of kept rows."""
    made_for: tuple
    gen: int
    stamp: tuple
    stores: list
    active: str
    kids: Any = None
    shut: dict = field(default_factory=dict)
    found: dict = field(default_factory=dict)


@dataclass
class _Folders:
    """Mine's folders as the worker made them of a snapshot (*gen*: the
    newer wins)."""
    made_for: tuple
    gen: int
    stamp: tuple
    loose: mitt.Folder
    named: dict
    wanted: dict = field(default_factory=dict)


def _kids_ok(kids: Any, e: mitt.Entry) -> bool:
    """(the worker) A kids profile's test for one title (none: every one)."""
    if kids is None:
        return True
    mode = {mitt.CHANNEL: "live", mitt.RADIO: "live", mitt.WEB: "live",
            mitt.SERIES: "series", mitt.EPISODE: "series"}.get(e.kind, "vod")
    row = e.row if e.row.get("_kind") else {**e.row, "_kind": mode}
    try:
        return bool(kids(row))
    except Exception:
        return False


class _LockTest:
    """(the worker) The parental lock on categories for the favourites of
    every source, as the lists leave a category's rows out: none of a
    category locked while the lock is shut. Another source's row kept
    without its category stays out while one of its kind is (no catalogue
    to ask); the source in use's is held back and its category asked of
    its catalogue (*wanted*, mw_lists._lock_learn)."""

    def __init__(self, shut: dict, found: dict, active: str) -> None:
        self.shut, self.found, self.active = shut, found, active
        self.wanted: dict[str, set[str]] = {}

    def __call__(self, source: str, mode: str | None, row: Any) -> bool:
        shut = self.shut.get(source) if mode else None
        if not shut or not isinstance(row, dict):
            return True
        kept = {**row, "_kind": mode}
        here = source == self.active
        hides = lock_hides(kept, shut, self.found if here else None)
        if hides is None and here:
            place = kept_place(kept)
            if place is not None and place[2]:
                self.wanted.setdefault(place[0], set()).add(place[2])
        return hides is False


def _parse_store(copy: _FavCopy) -> mitt.FavStore:
    """(the worker) A store's copy as Mine's folders read it."""
    if copy.groups is not None:
        groups = copy.groups
    else:
        groups = FavoriteStore(_Raw({copy.key: copy.raw}), copy.key,
                               _ID_KEY.get(copy.section, "stream_id")).groups
    locked = frozenset(str(x) for x in _json_of(copy.locked, list) if isinstance(x, str))
    return mitt.FavStore(copy.source, copy.section, groups, locked, _json_of(copy.colours, dict))


def _build_folders(snap: _FavSnap, lock: _LockTest | None = None
                   ) -> tuple[_Folders, dict[tuple[str, str], Any]]:
    """(the page's worker) Mine's folders across every source - and each
    store's folders as parsed, by (source, section), for the Overview."""
    lock = lock if lock is not None else _LockTest(snap.shut, snap.found, snap.active)
    stores = [_parse_store(c) for c in snap.stores]

    def keep(e: mitt.Entry) -> bool:
        return (_kids_ok(snap.kids, e)
                and lock(e.source, mitt.SECTION_MODE.get(str(e.row.get("_section"))), e.row))

    loose, named = mitt.fav_folders(stores, keep, FAV_DEFAULT_GROUP)
    parsed = {(s.source, s.section): s.groups for s in stores}
    return (_Folders(snap.made_for, snap.gen, snap.stamp, loose, named,
                     {m: set(v) for m, v in lock.wanted.items()}), parsed)


def _build_folders_only(snap: _FavSnap) -> _Folders:
    return _build_folders(snap)[0]


@dataclass
class _Snap:
    made_for: tuple
    sources: list
    radio: list
    web: list
    movies: list
    shows: list
    order: list
    titles: list
    kids: Any
    now: datetime
    favs: _FavSnap | None = None


@dataclass
class _Data:
    """What the worker made of a snapshot."""
    made_for: tuple
    cont: list = field(default_factory=list)
    live: list = field(default_factory=list)
    habit: set = field(default_factory=set)
    habits_known: bool = False
    favs: list = field(default_factory=list)
    queue: list = field(default_factory=list)
    new: list = field(default_factory=list)
    history: list = field(default_factory=list)
    folders: _Folders | None = None
    #: Per catalogue, the source in use's ids a kept row's category is
    #: asked for (mw_lists._lock_learn) - shown once it is said.
    wanted: dict = field(default_factory=dict)


def _build(snap: _Snap) -> _Data:
    """(the page's worker) The page's sections out of the stores' copies."""
    from ..services.resume import ResumeStore
    kids = snap.kids
    data = _Data(snap.made_for)
    parsed: dict[tuple[str, str], Any] = {}
    lock: _LockTest | None = None
    if snap.favs is not None:
        lock = _LockTest(snap.favs.shut, snap.favs.found, snap.favs.active)
        data.folders, parsed = _build_folders(snap.favs, lock)

    def lock_ok(source: str, mode: str, row: Any) -> bool:
        return lock is None or lock(source, mode, row)
    cont: list[list[mitt.Entry]] = []
    hist: list[list[mitt.Entry]] = []
    new: list[list[mitt.Entry]] = []
    live: list[mitt.Entry] = []
    favs: list[mitt.Entry] = []
    seen_live: set[str] = set()
    for src in snap.sources:
        raw = _Raw(src.raw)
        pid = src.pid
        rkey = f"resume_positions_{pid}" if pid else "resume_positions"
        resume = ResumeStore(_Raw({rkey: src.resume_raw}), pid or None)
        rows = src.cont if src.cont is not None else resume.continue_watching()
        rows = [r for r in rows if _lock_keeps(r, src.shut)]
        local = [{"_kind": "local", "_key": key, "_url": key,
                  "name": os.path.splitext(os.path.basename(str(key)))[0],
                  "_resume_pos": pos, "_resume_dur": dur, "_ts": ts}
                 for key, pos, dur, ts in resume.positions("local")]
        cont.append(mitt.continue_entries(list(rows) + local, pid))
        history = src.history if src.active else HistoryStore(raw, f"history_{pid}").items()
        history = [h for h in history if _lock_keeps(h, src.shut)]
        entries = mitt.history_entries(history, pid)
        for e in entries:
            kind = {mitt.FILM: "movie", mitt.EPISODE: "episode", mitt.LOCAL: "local"}.get(e.kind)
            if kind:
                pos, dur, _at = resume.progress(e.ident, kind)
                if pos > 60:
                    e.pos, e.dur = pos, dur
        hist.append(entries)
        fav_groups, locked = src.favs, src.locked
        if not src.active:
            got = parsed.get((pid, "chan"))
            fav_groups = got if got is not None else \
                FavoriteStore(raw, f"favorites_{pid}").groups
            locked = locked or {str(x) for x in _json_of(raw.value(f"favorites_{pid}_locked"),
                                                        list)}
        for row in mitt.fav_rows(fav_groups, "stream_id", locked):
            if not lock_ok(pid, "live", row):
                continue
            e = mitt.Entry(mitt.CHANNEL, str(row.get("stream_id")),
                           str(row.get("name") or ""), row, pid)
            if e.key not in seen_live:
                seen_live.add(e.key)
                live.append(e)
        news = src.new if src.new is not None else \
            FollowedSeriesStore(raw, pid or None).with_new()
        new.append(mitt.new_entries(news, pid))
        for section, kind, id_key in (("movie", mitt.FILM, "stream_id"),
                                      ("series", mitt.SERIES, "series_id")):
            groups = src.raw.get(f"_{section}_groups") if src.active else \
                parsed.get((pid, section))
            if groups is None and not src.active:
                groups = FavoriteStore(raw, f"{section}_favorites_{pid}", id_key).groups
            for row in mitt.fav_rows(groups or {}, id_key):
                if not lock_ok(pid, mitt.SECTION_MODE[section], row):
                    continue
                favs.append(mitt.Entry(kind, str(row.get(id_key)),
                                       str(row.get("name") or row.get("title") or ""),
                                       {**row, "_kind": "vod" if kind == mitt.FILM
                                        else "series"}, pid))
    for row in snap.radio:
        uuid = row.get("stationuuid")
        if uuid:
            live.append(mitt.Entry(mitt.RADIO, f"radio:{uuid}", str(row.get("name") or ""),
                                   {**row, "_kind": "radio", "_key": f"radio:{uuid}"}))
    for row in snap.web:
        url = row.get("url")
        if not url:
            continue
        e = mitt.Entry(mitt.WEB, str(url), str(row.get("name") or url),
                       {**row, "_kind": "web", "_key": url})
        (live if row.get("_live") else favs).append(e)

    allowed = partial(_kids_ok, kids)
    data.cont = [e for e in mitt.merged(*cont) if allowed(e)][:mitt.ROW_MAX * 3]
    data.history = [e for e in mitt.merged(*hist) if allowed(e)]
    data.new = [e for e in mitt.merged(*new) if allowed(e)]
    live = [e for e in live if allowed(e)]
    titles = {}
    for tid, t in snap.titles:
        try:
            titles[tid] = {"h": dict(t.get("h") or {})}
        except Exception:
            continue
    hours = mitt.habit_hours(live, titles)
    scores = {k: mitt.habit_score(h, snap.now) for k, h in hours.items()}
    data.live = mitt.by_habit(live, scores)
    data.habit = mitt.habitual(live, scores)
    data.habits_known = any(s > 0 for s, _d in scores.values())
    data.favs = [e for e in favs if allowed(e)]
    # Watch later keeps each kind the newest first: the first added first,
    # the films before the series.
    items: list[tuple[str, Any]] = []
    for row in reversed(snap.movies):
        if row.get("stream_id") is not None:
            items.append((mitt.queue_ident(row, mitt.FILM), (mitt.FILM, row)))
    for row in reversed(snap.shows):
        if row.get("series_id") is not None:
            items.append((mitt.queue_ident(row, mitt.SERIES), (mitt.SERIES, row)))
    active = next((s for s in snap.sources if s.active), None)
    queue = []
    for ident, (kind, row) in mitt.queue_order(items, snap.order):
        key = row.get("stream_id") if kind == mitt.FILM else row.get("series_id")
        e = mitt.Entry(kind, str(key), str(row.get("name") or row.get("title") or ""),
                       {**row, "_kind": "vod" if kind == mitt.FILM else "series", "_qid": ident},
                       active.pid if active is not None else "")
        if allowed(e):
            queue.append(e)
    data.queue = queue
    if lock is not None:
        data.wanted = {m: set(v) for m, v in lock.wanted.items()}
    return data


class _MittMixin:
    """MainWindow mixin: the Mine page."""

    _mitt_page: MittPage | None = None
    _mitt_gen = 0
    _mitt_data: _Data | None = None
    _mitt_pool: QThreadPool | None = None
    _mitt_timer: QTimer | None = None
    _mitt_refresh_timer: QTimer | None = None
    _mitt_playing: frozenset = frozenset()
    _mitt_live_all = False
    _mitt_history_n = mitt.HISTORY_PAGE
    _mitt_off: dict | None = None
    _mitt_folder = LOOSE
    _mitt_words: dict | None = None
    _mitt_built_at = 0.0
    _mitt_came_from = ""
    #: The folders' snapshots so far (the newest is the last made).
    _mitt_fgen = 0
    #: A folder was changed since the Overview was read (it is read again).
    _mitt_folders_dirty = False
    #: The window's own favourites as the last snapshot of them found them.
    _mitt_fstamp_asked: tuple = ()

    # -- where it is ------------------------------------------------------------------

    def _mitt_showing(self) -> bool:
        page = self._mitt_page
        stack = getattr(self, "_mid_stack", None)
        return page is not None and stack is not None and stack.currentWidget() is page

    def _mitt_page_visible(self) -> bool:
        return self._mitt_showing() and self._mitt_page.isVisible()

    def _ensure_mitt_page(self) -> MittPage:
        page = self._mitt_page
        if page is None:
            page = MittPage(self.logos, self.poster_art)
            page.retranslate()
            page.activated.connect(self._mitt_activate)
            page.menu_requested.connect(self._mitt_menu)
            page.history_remove.connect(self._mitt_forget_history_cards)
            page.set_click_plays(self._autoplay_preview)
            page.heart.connect(self._mitt_heart)
            page.queue_nudged.connect(self._mitt_queue_nudge)
            page.queue_moved.connect(self._mitt_queue_move)
            page.queue_removed.connect(self._mitt_queue_remove)
            page.filter_changed.connect(self._mitt_render)
            page.tab_changed.connect(self._mitt_tab_changed)
            page.more.connect(self._mitt_more)
            page.back.connect(self._mitt_leave)
            page.folder_picked.connect(self._mitt_folder_picked)
            page.folder_new.connect(self._mitt_folder_new)
            page.folder_menu.connect(self._mitt_folder_menu)
            page.folder_drop.connect(self._mitt_folder_drop)
            page.folder_reorder.connect(self._mitt_folder_reorder)
            page.tiles.guide = self._mitt_guide
            page.tiles.line_for = self._mitt_tile_line
            page.timeline.words_for = self._mitt_row_words
            page.folder_tiles.swatch_for = self._mitt_swatch
            # New episodes only open a series' page: one click there; the rest
            # play, so one click plays only with autoplay (set_click_plays).
            page.rows["new"].strip.click_plays = None
            self._mitt_page = page
            self._mid_stack.addWidget(page)
            cats = getattr(self, "cat_list", None)
            if cats is not None:
                cats.itemClicked.connect(self._mitt_category_clicked)
            parent = self if isinstance(self, QObject) else None
            t = QTimer(parent)
            t.setInterval(TICK_MS)
            t.timeout.connect(self._mitt_tick)
            self._mitt_timer = t
            r = QTimer(parent)
            r.setSingleShot(True)
            r.setInterval(REFRESH_MS)
            r.timeout.connect(self._mitt_build)
            self._mitt_refresh_timer = r
        return page

    def _mitt_show(self) -> bool:
        """Mine in the middle pane (core/places: the place's own page), read
        anew from the stores - what it showed before stays until that is
        in. Back from a page over it (a series', the Guide, Search) it is as
        it was left. True when it shows; False in TV mode, which holds the
        window."""
        if getattr(self, "_tv_on", False):
            return False
        if getattr(self, "_player_fs", False):
            self._exit_player_fullscreen()
        if getattr(self, "_focus_mode", False):
            self._set_focus_mode(False)
        here = getattr(self, "_place", "")
        back = here == "mitt" or (here in places.PAGE_PLACES
                                  and getattr(self, "_list_place", "") == "mitt")
        if not back:
            self._mitt_came_from = here if places.valid(here) else ""
        self._pages_give_way()
        if self.mode not in places.MITT_MODES:
            # The list under the page is Mine's own (its folders in the
            # sidebar), as the place's list would be.
            self.switch_mode(self._mitt_tab_mode())
        page = self._ensure_mitt_page()
        self._mid_stack.setCurrentWidget(page)
        self._place_entered("mitt")
        self._mitt_playing = self._mitt_keys()
        if self._mitt_timer is not None:
            self._mitt_timer.start()
        if not back or self._mitt_data is None:
            self._mitt_off = {}
            self._mitt_live_all = False
            self._mitt_history_n = mitt.HISTORY_PAGE
            page.place.scroll_to_top()
        self._mitt_build()
        view = page.first_view() if back and self._mitt_data is not None else None
        (view or page.field).setFocus()
        return True

    def _close_mitt_page(self) -> None:
        """The list back in the middle pane; the page kept, hidden."""
        if self._mitt_timer is not None:
            self._mitt_timer.stop()
        if self._mitt_showing():
            self._mid_stack.setCurrentIndex(0)
            self._mitt_forget_if_private()

    def _mitt_leave(self) -> None:
        """Back: the place Mine was come to from (none: it stays)."""
        if not self._mitt_showing():
            return
        came = self._mitt_came_from
        if came and came != "mitt":
            self._show_place(came)

    def _mitt_escape(self) -> None:
        page = self._mitt_page
        if page is not None and page.escape():
            return
        self._mitt_leave()

    def _mitt_category_clicked(self, _item=None) -> None:
        """A category picked in the sidebar: its list, in the page's place."""
        if self._mitt_showing():
            self._close_mitt_page()
            self._place_entered(places.place_of_mode(self.mode))

    def _mitt_forget_if_private(self) -> None:
        page = self._mitt_page
        if privacy.private() and page is not None and page.query():
            page.field.blockSignals(True)
            page.field.clear()
            page.field.blockSignals(False)

    def _mitt_restyle(self) -> None:
        page = self._mitt_page
        if page is not None and not sip.isdeleted(page):
            page.restyle()
            page.retranslate()
            if self._mitt_data is not None:
                self._mitt_render()

    def _mitt_refresh(self) -> None:
        """(core/places' refresh hook) What Mine shows changed - another
        profile or source, a followed series, a sync, History cleared, a
        kids profile's categories known: read again (now, when on screen)."""
        self._mitt_gen += 1
        if self._mitt_data is not None and self._mitt_data.made_for != self._mitt_made_for():
            self._mitt_data = None
            self._mitt_off = {}
        if self._mitt_showing():
            self._mitt_build()

    def _mitt_refresh_soon(self) -> None:
        if self._mitt_refresh_timer is not None and self._mitt_showing():
            self._mitt_refresh_timer.start()

    # -- what it reads ----------------------------------------------------------------

    def _mitt_made_for(self) -> tuple:
        kids = bool(getattr(self, "_profile_kids", lambda: False)())
        unlocked = bool(getattr(self.parental, "session_unlocked", False))
        store = getattr(self, "playlist_store", None)
        active = str(getattr(store, "active_id", "") or "") if store is not None else ""
        ids = tuple(str(p.get("id")) for p in (store.playlists() if store is not None else []))
        return (profiles.active_id(), kids, unlocked, active, ids)

    def _mitt_active(self) -> str:
        store = getattr(self, "playlist_store", None)
        return str(getattr(store, "active_id", "") or "") if store is not None else ""

    def _mitt_names(self) -> dict[str, str]:
        store = getattr(self, "playlist_store", None)
        return {str(p.get("id")): str(p.get("name") or p.get("server") or "")
                for p in (store.playlists() if store is not None else [])}

    def _mitt_snapshot(self) -> _Snap:
        """(the window's thread) What the page needs, copied: the source in
        use's stores as the window holds them, every other source's values
        as they are kept - parsed on the worker."""
        made_for = self._mitt_made_for()
        _who, kids, unlocked, active, ids = made_for
        names = self._mitt_names()
        own = self._own(self.settings)
        resume_s = self._own(getattr(self, "_resume_settings", None) or self.settings)
        follow_s = self._own(getattr(self, "_follow_settings", None) or self.settings)
        locked = set() if unlocked else set(self.favs.locked_groups())
        try:
            cont = list(self._continue_rows())
        except Exception:
            log.debug("mine: continue", exc_info=True)
            cont = []
        # The parental lock as the lists have it: none of a locked category
        # (Continue watching's are left out by _continue_rows).
        here = _Source(active, names.get(active, ""), True,
                       history=self._lock_filter(getattr(self.history, "entries", []) or []),
                       resume_raw=str(resume_s.value(
                           f"resume_positions_{active}" if active else "resume_positions", "")
                           or ""),
                       cont=cont,
                       favs={g: list(v) for g, v in self.favs.groups.items()},
                       locked=locked, new=list(self.followed.with_new()))
        here.raw["_movie_groups"] = {g: list(v) for g, v in self.movie_favs.groups.items()}
        here.raw["_series_groups"] = {g: list(v) for g, v in self.series_favs.groups.items()}
        sources = [here]
        if not kids:
            for pid in ids:
                if pid == active:
                    continue
                raw = {f"history_{pid}": own.value(f"history_{pid}", "") or "",
                       f"favorites_{pid}": own.value(f"favorites_{pid}", "") or "",
                       f"favorites_{pid}_locked": ("[]" if unlocked else
                                                   own.value(f"favorites_{pid}_locked", "")
                                                   or ""),
                       f"followed_series_{pid}": follow_s.value(f"followed_series_{pid}", "")
                       or ""}
                for section in ("movie", "series"):
                    key = f"{section}_favorites_{pid}"
                    raw[key] = own.value(key, "") or ""
                sources.append(_Source(pid, names.get(pid, ""), False,
                                       resume_raw=str(resume_s.value(
                                           f"resume_positions_{pid}", "") or ""),
                                       raw=raw, shut=self._mitt_shut(pid, unlocked)))
        radio_locked = set() if unlocked else set(self.radio_favs.locked_groups())
        web_locked = set() if unlocked else set(self.web_favs.locked_groups())
        radio = mitt.fav_rows(self.radio_favs.groups, "stationuuid", radio_locked)
        web = mitt.fav_rows(self.web_favs.groups, "url", web_locked)
        wl = self.watchlist
        try:
            log_titles = list(self._stats_store().titles.items())
        except Exception:
            log_titles = []
        kids_test = getattr(self, "_kids_keep", lambda _m: None)("live")
        return _Snap(made_for, sources, radio, web, list(wl.movies), list(wl.shows),
                     mitt.parse_order(own.value(mitt.ORDER_KEY, "")), log_titles,
                     kids_test, datetime.now(),
                     self._mitt_fav_snapshot(made_for, kids_test,
                                             {s.pid: s.shut for s in sources}))

    # -- the favourites' stores, for the folders ---------------------------------------

    def _mitt_own_stores(self) -> list[tuple[str, str, Any]]:
        """(source, section, store) of the window's own favourites: the
        source in use's, the stations' and the web streams'."""
        active = self._mitt_active()
        out = []
        for section, attr, _k, kind in _FAV_SECTIONS:
            store = getattr(self, attr, None)
            if store is not None:
                out.append(("" if kind in mitt.SOURCELESS else active, section, store))
        return out

    def _mitt_other_ids(self) -> list[str]:
        """Every other source's id in the user's order - none for a kids
        profile, which sees the source in use alone (as the lists do)."""
        if getattr(self, "_profile_kids", lambda: False)():
            return []
        active = self._mitt_active()
        return [pid for pid in self._mitt_names() if pid and pid != active]

    def _mitt_store_keys(self) -> list[tuple[str, str, str]]:
        """(source, section, key) of every favourites store the folders
        hold, in their order: the source in use's, every other source's,
        then the stations' and web streams'."""
        own = [(src, sec, st.key) for src, sec, st in self._mitt_own_stores()]
        out = [x for x in own if x[1] in _FAV_KEY]
        for pid in self._mitt_other_ids():
            out += [(pid, sec, _fav_key(sec, pid)) for sec in _FAV_KEY]
        return out + [x for x in own if x[1] not in _FAV_KEY]

    def _mitt_fav_stamp(self) -> tuple:
        """The window's own favourites as they are now: another stamp once
        any of them changed (the sidebar's menus, the lists' hearts)."""
        return tuple((st.key, st.version, id(st)) for _s, _sec, st in self._mitt_own_stores())

    def _mitt_fav_snapshot(self, made_for: tuple | None = None, kids: Any = None,
                           shut: dict | None = None) -> _FavSnap:
        """(the window's thread) Every favourites store the folders hold,
        copied: the window's own as it holds them, every other source's as
        kept (this profile's) - parsed on the worker."""
        made_for = made_for if made_for is not None else self._mitt_made_for()
        unlocked = bool(made_for[2])
        own = self._own(self.settings)
        mine = {(src, sec): st for src, sec, st in self._mitt_own_stores()}
        copies = []
        for src, sec, key in self._mitt_store_keys():
            st = mine.get((src, sec))
            if st is not None:
                copies.append(_FavCopy(src, sec, key, groups={g: list(v) for g, v
                                                              in st.groups.items()},
                                       locked=str(own.value(f"{key}_locked", "") or ""),
                                       colours=str(own.value(f"{key}_colors", "") or "")))
            else:
                copies.append(_FavCopy(src, sec, key, raw=str(own.value(key, "") or ""),
                                       locked=str(own.value(f"{key}_locked", "") or ""),
                                       colours=str(own.value(f"{key}_colors", "") or "")))
        active = self._mitt_active()
        if shut is None:
            shut = {pid: self._mitt_shut(pid, unlocked) for pid in self._mitt_other_ids()}
        shut = {pid: s for pid, s in shut.items() if pid != active and s}
        try:
            here = self._lock_shut()
        except Exception:
            here = {}
        if here:
            shut[active] = here
        try:
            found = {m: dict(v) for m, v in self._lock_known().items()} if here else {}
        except Exception:
            found = {}
        if kids is None:
            kids = getattr(self, "_kids_keep", lambda _m: None)("live")
        self._mitt_fgen += 1
        stamp = self._mitt_fav_stamp()
        self._mitt_fstamp_asked = stamp
        return _FavSnap(made_for, self._mitt_fgen, stamp, copies, active, kids, shut, found)

    def _mitt_shut(self, pid: str, unlocked: bool) -> dict[str, frozenset[str]]:
        """Per mode, another source's categories locked while the lock is
        shut - from its own lock rules, as the window keeps them under its id."""
        if unlocked:
            return {}
        cats = CategoryOverrides(self.settings, f"category_overrides_{pid}")
        return {m: ids for m in ("live", "vod", "series") if (ids := cats.locked_ids(m))}

    def _mitt_worker(self) -> QThreadPool:
        if self._mitt_pool is None:
            pool = QThreadPool(self if isinstance(self, QObject) else None)
            pool.setMaxThreadCount(1)
            self._mitt_pool = pool
        return self._mitt_pool

    def _mitt_build(self) -> None:
        """The page's sections read again off the window's thread; drawn
        when they are in (if nothing newer was asked for)."""
        if self._mitt_page is None:
            return
        self._mitt_gen += 1
        gen = self._mitt_gen
        try:
            snap = self._mitt_snapshot()
        except Exception:
            log.exception("mine: the stores were not read")
            return
        started = time.monotonic()
        run_async(self._mitt_worker(), partial(_build, snap),
                  partial(self._mitt_in, gen, started),
                  partial(self._mitt_failed, gen))

    def _mitt_in(self, gen: int, started: float, data: _Data) -> None:
        page = self._mitt_page
        if gen != self._mitt_gen or page is None or sip.isdeleted(page):
            return
        self._mitt_built_at = time.monotonic() - started
        log.debug("mine: built in %.0f ms", self._mitt_built_at * 1000)
        old = self._mitt_data
        if (old is not None and old.folders is not None and data.folders is not None
                and old.folders.made_for == data.folders.made_for
                and old.folders.gen > data.folders.gen):
            data.folders = old.folders      # the folders read since are newer
        self._mitt_data = data
        self._mitt_folders_dirty = False
        self._mitt_learn(data.wanted)
        self._mitt_render()

    def _mitt_failed(self, gen: int, err: Any = None) -> None:
        if gen == self._mitt_gen:
            log.warning("mine: the page was not built: %s", err)

    def _mitt_learn(self, wanted: dict) -> None:
        """The source in use's kept favourites whose categories are not
        known while one of their kind is locked: held back, their categories
        asked of its catalogue - shown once it says (mw_lists._lock_learn)."""
        if not wanted:
            return
        learn = getattr(self, "_lock_learn", None)
        if callable(learn):
            self._lock_held = True
            try:
                learn({m: set(v) for m, v in wanted.items()})
            except Exception:
                log.debug("mine: the categories not asked for", exc_info=True)

    def _mitt_folders_build(self) -> None:
        """The folders alone read again off the window's thread (a folder
        changed): drawn when they are in, unless newer ones are."""
        if self._mitt_page is None or self._mitt_data is None:
            return
        try:
            snap = self._mitt_fav_snapshot()
        except Exception:
            log.exception("mine: the favourites were not read")
            return
        run_async(self._mitt_worker(), partial(_build_folders_only, snap),
                  self._mitt_folders_in, self._mitt_folders_failed)

    def _mitt_folders_failed(self, err: Any = None) -> None:
        log.warning("mine: the folders were not read: %s", err)

    def _mitt_folders_in(self, folders: _Folders) -> None:
        data, page = self._mitt_data, self._mitt_page
        if data is None or page is None or sip.isdeleted(page) \
                or folders.made_for != data.made_for:
            return
        if data.folders is not None and data.folders.gen >= folders.gen:
            return                          # newer ones are in
        data.folders = folders
        self._mitt_learn(folders.wanted)
        if self._mitt_showing() and page.tab() == "folders":
            self._mitt_folders_fill()

    def _mitt_folders_pending(self) -> bool:
        """Whether folders newer than those shown are being read."""
        data = self._mitt_data
        have = data.folders.gen if data is not None and data.folders is not None else -1
        return self._mitt_fgen > have

    # -- what it shows ------------------------------------------------------------------

    def _mitt_render(self) -> None:
        """The sections as the chips and the search leave them."""
        page, data = self._mitt_page, self._mitt_data
        if page is None or sip.isdeleted(page) or data is None:
            return
        if data.made_for != self._mitt_made_for():
            self._mitt_build()          # another profile, the lock, a source since
            return
        if page.tab() == "folders":
            self._mitt_folders_fill()
            return
        chip = page.chip()
        terms = mitt.terms_of(page.query())
        many = len(self._mitt_names()) > 1
        self._mitt_words = {}

        def shown(entries: list) -> list:
            return [e for e in entries if mitt.matches(e, chip, terms)]

        if terms:
            for e in data.live:
                nn = self._mitt_now(e)
                extra = nn.title if nn is not None else ""
                if extra != e.extra:
                    e.extra, e.text = extra, ""
        cont = shown(data.cont)[:mitt.ROW_MAX]
        page.set_section("continue", [self._mitt_card_cont(e, many) for e in cont])
        live = shown(data.live)
        cut = live if self._mitt_live_all else live[:mitt.LIVE_SHOWN]
        page.tiles.habit = set(data.habit)
        page.tiles.off = set(self._mitt_off or {})
        button = ""
        if len(live) > mitt.LIVE_SHOWN:
            button = (tr("mitt_show_fewer") if self._mitt_live_all
                      else tr("mitt_show_all", n=len(live)))
        page.set_section("live", [self._mitt_tile(e, many) for e in cut],
                         tr("mitt_live_note") if data.habits_known and live else "", button)
        favs = shown(data.favs)[:200]
        page.set_section("favorites", [self._mitt_card_fav(e) for e in favs])
        queue = shown(data.queue)
        page.set_section("queue", [self._mitt_card_queue(e) for e in queue],
                         self._mitt_queue_text(queue))
        new = shown(data.new)[:mitt.ROW_MAX]
        page.set_section("new", [self._mitt_card_new(e, many) for e in new])
        hist = shown(data.history)
        rows = self._mitt_timeline(hist[:self._mitt_history_n], many)
        more = tr("card_scroll_on") if len(hist) > self._mitt_history_n else ""
        page.set_section("history", rows, "", more)
        anything = any((cont, live, favs, queue, new, hist))
        page.set_empty("" if anything else
                       tr("mitt_nothing") if (terms or chip != mitt.ALL) else tr("mitt_empty"))
        self._mitt_mark()

    def _mitt_tab_changed(self, tab: str = "") -> None:
        """The other tab: the Overview read again when a folder was changed
        meanwhile (its favourites are in folders too)."""
        if tab != "folders" and self._mitt_folders_dirty:
            self._mitt_folders_dirty = False
            self._mitt_build()
        self._mitt_render()

    def _mitt_more(self, section: str) -> None:
        if section == "live":
            self._mitt_live_all = not self._mitt_live_all
        elif section == "history":
            self._mitt_history_n += mitt.HISTORY_PAGE
        self._mitt_render()

    # -- the cards ------------------------------------------------------------------------

    def _mitt_from(self, e: mitt.Entry, many: bool) -> str:
        """"From Pirat" for one of another source, with two sources or more."""
        if not many or not e.source or e.source == self._mitt_active():
            return ""
        return tr("sa_from_source", source=self._mitt_names().get(e.source, "?"))

    @staticmethod
    def _mitt_join(*parts: str) -> str:
        return " · ".join(p for p in parts if p)

    def _mitt_art(self, row: dict, kind: str) -> str:
        ctx = row.get("_series_ctx") or {}
        raw = (row.get("cover") or row.get("stream_icon") or ctx.get("cover")
               or ctx.get("stream_icon") or "")
        try:
            return self.cover.cover_url(row, kind) or raw
        except Exception:
            return raw

    @staticmethod
    def _mitt_minutes(secs: float) -> str:
        mins = max(1, round(secs / 60))
        h, m = divmod(mins, 60)
        return tr("mitt_hours_min", h=h, m=f"{m:02d}") if h else tr("mitt_min", m=m)

    def _mitt_left(self, e: mitt.Entry) -> str:
        if e.dur > e.pos > 0:
            return tr("home_min_left", n=max(1, round((e.dur - e.pos) / 60)))
        return ""

    def _mitt_se(self, row: dict) -> str:
        s, n = row.get("season"), row.get("episode_num")
        try:
            return tr("sp_se", s=int(s), e=int(n))
        except (TypeError, ValueError):
            return ""

    def _mitt_dl_pills(self, e: mitt.Entry) -> tuple:
        """Downloaded, for a film or an episode whose file is (of its own
        source: mw_downloads)."""
        kind = {mitt.FILM: "movie", mitt.EPISODE: "episode"}.get(e.kind)
        find = getattr(self, "_dl_find", None)
        if kind is None or not callable(find):
            return ()
        try:
            return ("dl",) if find(kind, e.row, e.ident, source=e.source) else ()
        except Exception:
            return ()

    def _mitt_card_cont(self, e: mitt.Entry, many: bool) -> Card:
        row = e.row
        if e.kind == mitt.EPISODE:
            ctx = row.get("_series_ctx") or {}
            title = str(ctx.get("name") or ctx.get("title") or e.title)
            line = self._mitt_join(self._mitt_se(row), self._mitt_left(e), self._mitt_from(e, many))
            image = self._mitt_art(row, "episode")
        elif e.kind == mitt.LOCAL:
            title, image = e.title, ""
            line = self._mitt_join(tr("mitt_kind_local"), self._mitt_left(e))
        else:
            title = e.title
            line = self._mitt_join(self._mitt_left(e), self._mitt_from(e, many))
            image = self._mitt_art(row, "vod")
        return Card(key=e.key, title=title, line=line, image=image or "",
                    progress=e.progress, pills=self._mitt_dl_pills(e), data=e)

    def _mitt_tile(self, e: mitt.Entry, many: bool) -> ChannelRow:
        name = e.title
        if e.kind == mitt.CHANNEL and e.source == self._mitt_active():
            try:
                name = self.channel_display_name(e.row) or name
            except Exception:
                pass
        return ChannelRow(key=e.key, name=name, logo=str(e.row.get("stream_icon")
                                                         or e.row.get("favicon") or ""),
                          data=e)

    def _mitt_now(self, e: mitt.Entry) -> NowNext | None:
        if e.kind != mitt.CHANNEL or e.source != self._mitt_active():
            return None
        guide = getattr(self, "xmltv", None)
        if guide is None:
            return None
        try:
            got = guide.listings_for(e.row, 2)
        except Exception:
            return None
        if not got:
            return None
        now, nxt = got[0], (got[1] if len(got) > 1 else {})
        if float(now.get("start_timestamp") or 0) > time.time():
            return None
        return NowNext(str(now.get("title") or ""), float(now.get("start_timestamp") or 0),
                       float(now.get("stop_timestamp") or 0), str(nxt.get("title") or ""),
                       float(nxt.get("start_timestamp") or 0))

    def _mitt_guide(self, row: ChannelRow) -> NowNext | None:
        e = row.data
        return self._mitt_now(e) if isinstance(e, mitt.Entry) else None

    def _mitt_tile_line(self, row: ChannelRow) -> str:
        e = row.data
        if not isinstance(e, mitt.Entry):
            return ""
        many = len(self._mitt_names()) > 1
        if e.kind == mitt.CHANNEL:
            return self._mitt_from(e, many)
        return tr(_KIND_WORD.get(e.kind, "mitt_kind_web"))

    def _mitt_card_fav(self, e: mitt.Entry) -> Card:
        row = e.row
        if e.kind == mitt.WEB:
            return Card(key=e.key, title=e.title, line=tr("mitt_kind_web"), data=e)
        kind = "vod" if e.kind == mitt.FILM else "series"
        pills: tuple = ()
        try:
            if e.kind == mitt.FILM and self.is_movie_watched(row):
                pills = ("seen",)
        except Exception:
            pass
        pills += self._mitt_dl_pills(e)
        return Card(key=e.key, title=e.title, line=tr(_KIND_WORD[e.kind]),
                    image=self._mitt_art(row, kind), pills=pills, data=e)

    def _mitt_length(self, e: mitt.Entry) -> float:
        """A queued film's length: its resume point's, its row's, or the
        detail panel's answer for it in memory - never asked for."""
        if e.kind == mitt.FILM:
            try:
                key = self._item_key(e.row)
                pos, dur, _at = self.resume.progress(key, "movie")
                if dur > 0:
                    e.pos, e.dur = pos, dur
                    return dur
            except Exception:
                pass
            info = (getattr(self, "_info_cache", {}) or {}).get(("vod", e.row.get("stream_id")))
            if isinstance(info, dict):
                length = mitt.length_of(info)
                if length > 0:
                    return length
        return mitt.length_of(e.row)

    def _mitt_card_queue(self, e: mitt.Entry) -> Card:
        length = self._mitt_length(e)
        if length > 0 and not e.dur:
            e.dur = length
        word = tr(_KIND_WORD[e.kind])
        line = self._mitt_join(word, self._mitt_minutes(length) if length > 0 else "")
        kind = "vod" if e.kind == mitt.FILM else "series"
        return Card(key=e.key, title=e.title, line=line, image=self._mitt_art(e.row, kind),
                    progress=e.progress, data=e)

    def _mitt_queue_text(self, entries: list) -> str:
        if not entries:
            return ""
        s = mitt.queue_summary(entries)
        parts = []
        if s.films:
            parts.append(tr("mitt_n_film" if s.films == 1 else "mitt_n_films", n=s.films))
        if s.series:
            parts.append(tr("mitt_n_show" if s.series == 1 else "mitt_n_shows", n=s.series))
        text = ", ".join(parts)
        if s.seconds > 0:
            when = self._mitt_minutes(s.seconds)
            text = f"{text} · " + (tr("mitt_at_least", time=when) if s.unknown else when)
        return text

    def _mitt_card_new(self, e: mitt.Entry, many: bool) -> Card:
        row = e.row
        new = [x for x in row.get("_new") or [] if isinstance(x, dict)]
        if len(new) == 1:
            ep = new[0]
            label = self._mitt_se(ep) or str(ep.get("title") or "")
            line = self._mitt_join(label, self._mitt_day(float(ep.get("added") or 0)))
        else:
            line = tr("home_new_count", n=len(new))
        return Card(key=e.key, title=e.title, line=self._mitt_join(line, self._mitt_from(e, many)),
                    image=self._mitt_art(row, "series"), pills=("new",), data=e)

    def _mitt_day(self, when: float) -> str:
        if when <= 0:
            return ""
        g = mitt.day_group(when, datetime.now())
        if g[0] == mitt.TODAY:
            return tr("rec_today")
        if g[0] == mitt.YESTERDAY:
            return tr("rec_yesterday")
        return format_when(when, "d MMM")

    def _mitt_week_start(self) -> int:
        try:
            return (app_locale().firstDayOfWeek().value - 1) % 7
        except Exception:
            return 0

    def _mitt_timeline(self, entries: list, many: bool) -> list:
        out: list = []
        words = self._mitt_words if self._mitt_words is not None else {}
        for group, rows in mitt.timeline(entries, datetime.now(), self._mitt_week_start()):
            out.append(Heading(key=f"head:{group}", title=self._mitt_group_title(group)))
            fmt = "HH:mm" if group[0] in (mitt.TODAY, mitt.YESTERDAY) else \
                "ddd HH:mm" if group[0] in (mitt.WEEK, mitt.LAST_WEEK) else "d MMM HH:mm"
            for e in rows:
                when = format_when(e.when, fmt) if e.when else ""
                kind = tr(_KIND_WORD.get(e.kind, "mitt_kind_web"))
                if many and e.source and e.source != self._mitt_active():
                    kind = f"{kind} · {self._mitt_names().get(e.source, '?')}"
                act = mitt.action_of(e, e.pos)
                action = {mitt.PLAY: tr("btn_play"), mitt.CONTINUE: tr("card_continue"),
                          mitt.AGAIN: tr("mitt_play_again")}[act]
                words[e.key] = (kind, action, when)
                line = self._mitt_join(when, kind, self._mitt_from(e, many))
                out.append(Card(key=e.key, title=e.title, line=line, data=e))
        return out

    def _mitt_group_title(self, group: tuple) -> str:
        what = group[0]
        if what == mitt.TODAY:
            return tr("rec_today")
        if what == mitt.YESTERDAY:
            return tr("rec_yesterday")
        if what == mitt.WEEK:
            return tr("mitt_earlier_week")
        if what == mitt.LAST_WEEK:
            return tr("mitt_last_week")
        if what == mitt.MONTH:
            return format_month(date(group[1], group[2], 1))
        return tr("rec_earlier")

    def _mitt_row_words(self, card: Card) -> tuple[str, str, str]:
        return (self._mitt_words or {}).get(card.key, ("", tr("btn_play"), ""))

    # -- what plays, marked -----------------------------------------------------------

    def _mitt_keys(self) -> frozenset:
        """The page's keys of what plays now, here or on a TV (none while
        nothing does)."""
        now = self._now_playing()
        if now is None:
            return frozenset()
        if not now.on_tv:
            p = getattr(self, "player", None)
            if p is None or not getattr(p, "current_url", None):
                return frozenset()
        series = (now.series_ctx or {}).get("series_id")
        return frozenset(mitt.playing_keys(now.group or None, now.key,
                                           self._mitt_active(), series))

    def _mitt_mark(self) -> None:
        page = self._mitt_page
        if page is not None and not sip.isdeleted(page):
            page.set_playing(set(self._mitt_keys()))

    def _mitt_tick(self) -> None:
        """What plays looked at: marked where it shows; something else
        started meanwhile heads History and Continue - the page read again."""
        page = self._mitt_page
        if page is None or sip.isdeleted(page) or not self._mitt_showing():
            if self._mitt_timer is not None:
                self._mitt_timer.stop()
            return
        if page.tab() == "folders":
            self._mitt_folders_stale()
        keys = self._mitt_keys()
        if keys == self._mitt_playing:
            return
        before, self._mitt_playing = self._mitt_playing, keys
        self._mitt_mark()
        if keys - before:
            self._mitt_refresh_soon()

    # -- what an item does ------------------------------------------------------------

    def _mitt_entry(self, item: Any) -> mitt.Entry | None:
        e = getattr(item, "data", None)
        return e if isinstance(e, mitt.Entry) else None

    def _mitt_is_active(self, e: mitt.Entry) -> bool:
        return not e.source or e.source == self._mitt_active() or e.kind in mitt.SOURCELESS

    def _mitt_activate(self, section: str, item: Any) -> None:
        if section == "folder":
            self._mitt_folder_play(item)
            return
        e = self._mitt_entry(item)
        if e is None:
            return
        if section == "history":
            how = mitt.action_of(e, e.pos)
        elif section == "continue":
            how = mitt.CONTINUE
        else:
            how = ""
        self._mitt_play(e, how)

    def _mitt_play(self, e: mitt.Entry, how: str = "") -> None:
        """*e* through the window's own path; *how*: CONTINUE where it got
        to, AGAIN from the start (neither asks), "" as the lists play it."""
        if e.kind == mitt.SERIES:
            self._mitt_open_series(e)
            return
        if e.kind == mitt.CHANNEL:
            self._mitt_play_channel(e)
            return
        if e.kind == mitt.RADIO:
            self._mitt_radio(e.row)
            return
        if e.kind == mitt.WEB:
            self._mitt_web(e.row)
            return
        if e.kind == mitt.LOCAL:
            self._mitt_play_local(e, how)
            return
        if not self._mitt_is_active(e):
            self._mitt_elsewhere(e, partial(self._mitt_title_here, e.row, e.kind, how, e.pos))
            return
        self._mitt_title_here(e.row, e.kind, how, e.pos)

    def _mitt_elsewhere(self, e: mitt.Entry, then) -> None:
        """*then* once *e*'s source is in use (switched to, the page kept)."""
        from ..core.search_all import Source
        self._sa_on_source(Source(e.source, self._mitt_names().get(e.source, "")), then)

    @from_pane
    def _mitt_play_channel(self, e: mitt.Entry) -> None:
        if self._mitt_is_active(e):
            self.play_live_channel(e.row)
            return
        store = getattr(self, "playlist_store", None)
        pl = store.get(e.source) if store is not None else None
        if pl is None:
            self._show_toast(tr("sa_source_gone",
                                source=self._mitt_names().get(e.source, "?")), 4000)
            return
        self._play_channel_elsewhere(pl, e.row)

    @from_pane
    def _mitt_radio(self, row: dict) -> None:
        self._radio_play(row)

    @from_pane
    def _mitt_web(self, row: dict) -> None:
        self._web_activate(row)

    def _mitt_title_url(self, row: dict, kind: str) -> str:
        if kind == mitt.EPISODE:
            if row.get("id") is not None:
                url = self.client.episode_url(row.get("id"), row.get("container_extension"))
                if url:
                    return url
            return str(row.get("_url") or "")
        sid = row.get("stream_id")
        if sid is not None:
            url = self.client.vod_url(sid, row.get("container_extension"))
            if url:
                return url
        return str(row.get("_url") or "")

    @from_pane
    def _mitt_title_here(self, row: dict, kind: str, how: str = "", pos: float = 0.0) -> None:
        """A film or an episode of the source in use in the docked player."""
        if self._links_later(partial(self._mitt_title_url, row, kind),
                             partial(self._mitt_title_here, row, kind, how, pos)):
            return
        key = self._item_key(row)
        if how == mitt.CONTINUE:
            at = self._ms_resume_at(key, "episode" if kind == mitt.EPISODE else "movie", row,
                                    self.resume.saved_position(
                                        key, "episode" if kind == mitt.EPISODE else "movie"))
            self._sp_start_once = (key, at if at > 0 else pos)
        elif how == mitt.AGAIN:
            self._sp_start_once = (key, 0.0)
        try:
            if kind == mitt.EPISODE:
                self._play_continue_episode(row)
                return
            url = self._mitt_title_url(row, kind)
            if not url:
                self._set_status(tr("sa_no_address", name=str(row.get("name") or "")),
                                 error=True)
                return
            self._current_key = key
            # A downloaded film plays its file, as this film (mw_downloads).
            start = getattr(self, "_dl_start", self._start_playback)
            start(url, row.get("name") or row.get("title") or "dopeIPTV",
                  row.get("stream_icon") or row.get("cover"), key, "movie", item=row)
        finally:
            self._sp_start_once = None

    @from_pane
    def _mitt_play_local(self, e: mitt.Entry, how: str = "") -> None:
        path = str(e.row.get("_url") or e.row.get("_path") or e.ident)
        if not path or not os.path.isfile(path):
            idx = self._choice_dialog(tr("nav_local"), tr("local_missing"),
                                      [(tr("local_missing_remove"), "normal"),
                                       (tr("common_cancel"), "primary")])
            if idx == 0:
                self._mitt_forget_history(e)
            return
        title = e.title or os.path.splitext(os.path.basename(path))[0]
        item = {**{k: v for k, v in e.row.items() if not str(k).startswith("_resume")},
                "name": title, "_path": path, "_key": path, "_kind": "local"}
        q = getattr(self, "_track_queue", []) or []
        if not (q and getattr(self, "_queue_explicit", False)):
            self._track_queue = [item]
            self._queue_explicit = False
        if how == mitt.CONTINUE:
            self._start_at_once(path, "local")
        elif how == mitt.AGAIN:
            self._sp_start_once = (path, 0.0)
        try:
            self._start_playback(path, title, e.row.get("stream_icon"), path, "local",
                                 record=True, item=item)
        finally:
            self._sp_start_once = None

    def _mitt_open_series(self, e: mitt.Entry, focus: Any = None) -> None:
        row = {k: v for k, v in e.row.items()
               if k not in ("_new", "_qid", "_kind", "_section", "_group")}
        if self._mitt_is_active(e):
            self._mitt_series_here(row, focus)
        else:
            self._mitt_elsewhere(e, partial(self._mitt_series_here, row, focus))

    def _mitt_series_here(self, row: dict, focus: Any = None) -> None:
        """The series' page over Mine; its Back comes here (mw_places)."""
        self._open_series_page(row, focus=focus)
        if self._series_page_showing():
            self._check_nav("mitt")

    # -- the hearts --------------------------------------------------------------------

    def _mitt_fav_store(self, e: mitt.Entry):
        """(store, id key, id) a live tile's favorite is kept in."""
        if e.kind == mitt.RADIO:
            return self.radio_favs, "stationuuid", e.row.get("stationuuid")
        if e.kind == mitt.WEB:
            return self.web_favs, "url", e.row.get("url")
        if self._mitt_is_active(e):
            return self.favs, "stream_id", e.row.get("stream_id")
        return (FavoriteStore(self._own(self.settings), f"favorites_{e.source}"), "stream_id",
                e.row.get("stream_id"))

    def _mitt_heart(self, row: Any) -> None:
        """The heart: out of the favorites - and pressed again, back where it
        was, in every folder at its place."""
        e = self._mitt_entry(row)
        page = self._mitt_page
        if e is None or page is None:
            return
        off = self._mitt_off if self._mitt_off is not None else {}
        self._mitt_off = off
        store, id_key, ident = self._mitt_fav_store(e)
        if ident is None:
            return
        if e.key in off:
            kept = off.pop(e.key)
            for group, items, order in kept:
                for it in items:
                    store.add(group, it)
                store.reorder(group, order)
            log.info("mine: a favorite put back")
        else:
            kept = []
            for group, items in store.groups.items():
                mine = [x for x in items if x.get(id_key) == ident]
                if mine:
                    kept.append((group, mine, [x.get(id_key) for x in items]))
            if not kept:
                return
            store.remove(ident)
            off[e.key] = kept
            log.info("mine: a favorite taken out")
        try:
            self.list_model.refresh_all()
        except Exception:
            pass
        page.tiles.off = set(off)
        page.tiles.viewport().update()
        page.tiles.focus_key(e.key, take_focus=page.tiles.hasFocus())

    # -- the queue ---------------------------------------------------------------------

    def _mitt_order(self) -> list[str]:
        data = self._mitt_data
        return [e.row.get("_qid") for e in (data.queue if data is not None else [])
                if e.row.get("_qid")]

    def _mitt_set_order(self, order: list[str], focus: str = "") -> None:
        data = self._mitt_data
        if data is None:
            return
        self._own(self.settings).setValue(mitt.ORDER_KEY, mitt.dump_order(order))
        by = {e.row.get("_qid"): e for e in data.queue}
        data.queue = [by[i] for i in order if i in by]
        self._mitt_render()
        page = self._mitt_page
        if page is not None and focus:
            page.queue.focus_key(focus, take_focus=page.queue.hasFocus() or True)

    def _mitt_queue_nudge(self, card: Any, delta: int) -> None:
        """Up or down one place among the rows shown."""
        e = self._mitt_entry(card)
        page = self._mitt_page
        if e is None or page is None:
            return
        shown = [c.data.row.get("_qid") for c in page.queue.items()]
        qid = e.row.get("_qid")
        if qid not in shown:
            return
        i = shown.index(qid)
        j = i + (1 if delta > 0 else -1)
        if not 0 <= j < len(shown):
            return
        order = self._mitt_order()
        target = order.index(shown[j])
        if delta > 0:
            order = mitt.moved_to(order, [qid], target + 1)
        else:
            order = mitt.moved_to(order, [qid], target)
        self._mitt_set_order(order, e.key)

    def _mitt_queue_move(self, card: Any, index: int) -> None:
        """A row dragged before the row shown at *index* (past the last: the end)."""
        e = self._mitt_entry(card)
        page = self._mitt_page
        if e is None or page is None:
            return
        shown = [c.data.row.get("_qid") for c in page.queue.items()]
        order = self._mitt_order()
        if 0 <= index < len(shown):
            at = order.index(shown[index])
        else:
            at = (order.index(shown[-1]) + 1) if shown else len(order)
        self._mitt_set_order(mitt.moved_to(order, [e.row.get("_qid")], at), e.key)

    def _mitt_queue_remove(self, card: Any) -> None:
        e = self._mitt_entry(card)
        if e is None:
            return
        kind = "vod" if e.kind == mitt.FILM else "series"
        self._remove_watchlist(e.row, kind, False)
        data = self._mitt_data
        if data is not None:
            data.queue = [x for x in data.queue if x.key != e.key]
            self._mitt_render()

    def _mitt_watched(self, item: Any, kind: str) -> None:
        """A title marked watched (watched to the end, or by hand): off the
        queue, if it is on it."""
        try:
            if not isinstance(item, dict) or not self.is_on_watchlist(item, kind):
                return
            self._remove_watchlist(item, kind, False)
            name = str(item.get("name") or item.get("title") or "")
            log.info("mine: %s watched, off the queue", for_log(name))
            self._show_toast(tr("mitt_auto_removed", title=name), 4000)
            data = self._mitt_data
            if data is not None:
                qid = mitt.queue_ident(item, mitt.FILM if kind == "vod" else mitt.SERIES)
                data.queue = [x for x in data.queue if x.row.get("_qid") != qid]
                if self._mitt_showing():
                    self._mitt_render()
        except Exception:
            log.debug("mine: watched", exc_info=True)

    # -- History's rows ----------------------------------------------------------------

    def _mitt_forget_history(self, e: mitt.Entry, render: bool = True) -> None:
        key, kind = e.row.get("_key"), e.row.get("_kind")
        if key is None or not kind:
            return
        if self._mitt_is_active(e) and (not e.source or e.source == self._mitt_active()):
            self.history.remove(key, kind)
        else:
            HistoryStore(self._own(self.settings), f"history_{e.source}").remove(key, kind)
        data = self._mitt_data
        if data is not None:
            data.history = [x for x in data.history if x.key != e.key]
            if render:
                self._mitt_render()

    def _mitt_forget_history_cards(self, cards: list) -> None:
        """Several of History's rows taken out at once (Delete, or Remove
        on one of the rows picked); drawn again once."""
        entries = [e for e in (self._mitt_entry(c) for c in cards) if e is not None]
        for e in entries:
            self._mitt_forget_history(e, render=False)
        if entries and self._mitt_data is not None:
            self._mitt_render()

    def _mitt_history_remove(self, item: Any, e: mitt.Entry) -> None:
        """Remove in a History row's menu: every row picked when this one
        is among several picked, else this one."""
        page = self._mitt_page
        picked = page.timeline.selected_cards() if page is not None else []
        keys = {x.key for x in (self._mitt_entry(c) for c in picked) if x is not None}
        if len(picked) > 1 and (any(c is item for c in picked) or e.key in keys):
            self._mitt_forget_history_cards(picked)
        else:
            self._mitt_forget_history(e)

    # -- the menus ---------------------------------------------------------------------

    def _mitt_menu(self, section: str, item: Any, pos: QPoint) -> None:
        if section == "folder":
            self._mitt_folder_item_menu(item, pos)
            return
        e = self._mitt_entry(item)
        if e is None:
            return
        m = QMenu(self)
        if not self._mitt_is_active(e):
            m.addAction(tr("sa_open_series") if e.kind == mitt.SERIES else tr("btn_play"),
                        partial(self._mitt_activate, section, item))
            if section == "history":
                m.addAction(tr("local_missing_remove"), partial(self._mitt_history_remove, item, e))
            m.addSeparator()
            note = m.addAction(tr("sa_from_source",
                                  source=self._mitt_names().get(e.source, "?")))
            note.setEnabled(False)
        elif section == "new":
            snap = {k: v for k, v in e.row.items() if k != "_new"}
            m.addAction(tr("common_open"), partial(self._mitt_open_series, e))
            m.addSeparator()
            m.addAction(tr("common_dismiss"),
                        partial(self._follow_mark_seen, snap.get("series_id")))
            m.addAction(tr("ctx_unfollow_series"), partial(self._unfollow_series, snap))
        elif e.kind == mitt.CHANNEL:
            self._mitt_channel_menu(m, e, section)
        elif e.kind in (mitt.RADIO, mitt.WEB):
            self._stream_row_actions(m, e.row)
        elif e.kind == mitt.LOCAL:
            self._mitt_local_menu(m, e, section)
        else:
            self._mitt_title_menu(m, e, section)
        if section == "history" and self._mitt_is_active(e) and e.kind != mitt.LOCAL:
            m.addSeparator()
            m.addAction(tr("local_missing_remove"), partial(self._mitt_history_remove, item, e))
        if section == "queue":
            m.addSeparator()
            m.addAction(tr("mitt_move_top"), partial(self._mitt_queue_move, item, 0))
            m.addAction(tr("mitt_move_up_menu"), partial(self._mitt_queue_nudge, item, -1))
            m.addAction(tr("mitt_move_down_menu"), partial(self._mitt_queue_nudge, item, 1))
        m.exec(pos)
        m.deleteLater()
        self._mitt_refresh_soon()

    def _mitt_external(self, e: mitt.Entry, player: str) -> None:
        """Open in mpv or VLC, as the list's Open externally does."""
        if e.kind == mitt.CHANNEL:
            fmt = self.settings.value("stream_format", "ts")
            sid = e.row.get("stream_id")
            if self._links_later(lambda: self.client.live_url(sid, fmt),
                                 partial(self._mitt_external, e, player)):
                return
            url, kind = self._live_stream_address(sid, fmt), "live"
        elif e.kind == mitt.LOCAL:
            url, kind = str(e.row.get("_url") or e.ident), "local"
        else:
            if self._links_later(partial(self._mitt_title_url, e.row, e.kind),
                                 partial(self._mitt_external, e, player)):
                return
            url = self._mitt_title_url(e.row, e.kind)
            kind = "episode" if e.kind == mitt.EPISODE else "movie"
        if not url or not self._confirm_external_while_playing():
            return
        if kind not in ("local",) and self._vpn_guard_blocks(url):
            return
        launch_player(player, url, e.title or "dopeIPTV", self)
        saved = self.series_ctx
        self.series_ctx = e.row.get("_series_ctx") if kind == "episode" else saved
        try:
            self.history.add(url, e.title, e.row.get("stream_icon"), self._item_key(e.row), kind,
                             extra=self._history_extra(kind, e.row, e.title))
        finally:
            self.series_ctx = saved

    def _mitt_copy(self, e: mitt.Entry) -> None:
        if e.kind == mitt.CHANNEL:
            fmt = self.settings.value("stream_format", "ts")
            sid = e.row.get("stream_id")
            if self._links_later(lambda: self.client.live_url(sid, fmt),
                                 partial(self._mitt_copy, e), key="copy"):
                return
            url = self._live_stream_address(sid, fmt)
        elif e.kind == mitt.LOCAL:
            url = str(e.row.get("_url") or e.ident)
        else:
            if self._links_later(partial(self._mitt_title_url, e.row, e.kind),
                                 partial(self._mitt_copy, e), key="copy"):
                return
            url = self._mitt_title_url(e.row, e.kind)
        if url:
            QApplication.clipboard().setText(url)

    def _mitt_open_menu(self, m: QMenu, e: mitt.Entry, play_text: str, section: str) -> None:
        m.addAction(play_text, partial(self._mitt_activate, section, Card(data=e)))
        ext = m.addMenu(tr("ctx_open_externally"))
        ext.addAction("mpv", partial(self._mitt_external, e, "mpv"))
        ext.addAction("VLC", partial(self._mitt_external, e, "vlc"))

    def _mitt_channel_menu(self, m: QMenu, e: mitt.Entry, section: str) -> None:
        """A channel's menu, as the list's (Favorites, History) has it."""
        row = e.row
        self._mitt_open_menu(m, e, tr("common_watch"), section)
        m.addAction(tr("ctx_cast_to_chromecast"), partial(self._open_cast_dialog, row))
        self._box_channel_menu(m, row)
        mv = m.addMenu(tr("mv_add"))
        mvw = getattr(self, "_multiview_win", None)
        for n in range(self._multiview_cell_count()):
            occupant = ""
            if mvw is not None and n < len(mvw.cells) and mvw.cells[n].title:
                occupant = f"  —  {mvw.cells[n].title}"
            mv.addAction(tr("mv_cell", n=n + 1) + occupant,
                         partial(self._add_channel_to_multiview, row, n))
        m.addAction(tr("btn_epg_guide"), self._open_epg_guide)
        m.addAction(tr("btn_refresh_epg"), self._refresh_epg_now)
        if self._timeshift_days(row):
            m.addSeparator()
            self._build_timeshift_menu(m.addMenu(tr("tooltip_timeshift")), row)
        m.addSeparator()
        self._build_record_menu(m.addMenu(tr("rec_record")), row)
        m.addAction(tr("upcoming_remind"), partial(self._remind_upcoming, row))
        self._health_channel_menu(m, row)
        self._tsa_channel_menu(m, row)
        self._chanprefs_menu(m, row)
        m.addSeparator()
        sid = row.get("stream_id")
        snap = {k: row.get(k) for k in ("stream_id", "name", "stream_icon", "num",
                                        "epg_channel_id", "category_id", "tv_archive",
                                        "tv_archive_duration") if row.get(k) is not None}
        if self.favs.is_favorite(sid):
            m.addAction(tr("ctx_remove_from_favorites"), partial(self._mitt_unfav, sid))
        else:
            m.addAction(tr("ctx_add_to_favorites"),
                        partial(self._fav_add_many, "chan", [snap], FAV_DEFAULT_GROUP))
        self._add_fav_folder_menu(m, "chan", [snap], self.favs)
        m.addSeparator()
        m.addAction(tr("ctx_copy_stream_url"), partial(self._mitt_copy, e))

    def _mitt_unfav(self, sid: Any) -> None:
        """Out of the favorites, every folder (the list's Remove outside
        Favorites)."""
        self.favs.remove(sid)
        try:
            self.list_model.refresh_all()
        except Exception:
            pass
        self._mitt_folders_changed()

    def _mitt_local_menu(self, m: QMenu, e: mitt.Entry, section: str) -> None:
        self._mitt_open_menu(m, e, tr("card_continue") if e.pos > 0 else tr("btn_play"),
                             section)
        if e.pos > 0:
            m.addAction(tr("ctx_continue_remove"), partial(self._mitt_forget_local, e))
        if section == "history":
            m.addSeparator()
            m.addAction(tr("local_missing_remove"), partial(self._mitt_history_remove, None, e))
        m.addSeparator()
        m.addAction(tr("ctx_copy_stream_url"), partial(self._mitt_copy, e))

    def _mitt_forget_local(self, e: mitt.Entry) -> None:
        self.resume.clear("local", e.ident)
        self._mitt_refresh_soon()

    def _mitt_fav_row(self, e: mitt.Entry) -> dict:
        """A film's or a series' row as a favorite's snapshot needs it (a
        History row carries its id only as _key)."""
        row = dict(e.row)
        if e.kind == mitt.FILM and row.get("stream_id") is None:
            tail = str(row.get("_url") or "").rsplit(".", 1)
            ext = tail[1] if len(tail) == 2 and 0 < len(tail[1]) <= 4 else None
            row["stream_id"] = row.get("_key")
            row["container_extension"] = row.get("container_extension") or ext
        return {k: v for k, v in row.items()
                if k not in ("_qid", "_new", "_section", "_group", "_kind")}

    def _mitt_title_menu(self, m: QMenu, e: mitt.Entry, section: str) -> None:
        """A film's, an episode's or a series' menu, as the lists have it."""
        row = self._mitt_fav_row(e)
        if e.kind == mitt.SERIES:
            m.addAction(tr("sa_open_series"), partial(self._mitt_open_series, e))
        else:
            self._mitt_open_menu(m, e, tr("card_continue") if e.pos > 0 else tr("btn_play"),
                                 section)
            if e.kind == mitt.EPISODE:
                m.addAction(tr("ctx_cast_to_chromecast"), partial(self._mitt_cast_episode, e))
            else:
                m.addAction(tr("ctx_cast_to_chromecast"), partial(self._open_cast_dialog, row))
            if e.pos > 0 or row.get("_resume_pos") is not None:
                m.addAction(tr("ctx_continue_remove"), partial(self._mitt_forget_resume, e))
        ctx = row.get("_series_ctx") if isinstance(row.get("_series_ctx"), dict) else None
        if e.kind == mitt.EPISODE and ctx and ctx.get("series_id") is not None:
            m.addAction(tr("sa_open_series"),
                        partial(self._mitt_open_series,
                                mitt.Entry(mitt.SERIES, str(ctx["series_id"]), "", ctx,
                                           e.source), self._item_key(e.row)))
            m.addSeparator()
            self._add_follow_action(m, ctx)
            return
        section_key = "movie" if e.kind == mitt.FILM else "series"
        store = self.movie_favs if e.kind == mitt.FILM else self.series_favs
        ident = row.get(store.id_key)
        if ident is not None:
            m.addSeparator()
            on = store.is_favorite(ident)
            m.addAction(tr("ctx_remove_from_favorites" if on else "ctx_add_to_favorites"),
                        partial(self._toggle_media_fav, row, section_key, not on))
            self._add_fav_folder_menu(m, section_key, [row], store)
            if e.kind == mitt.SERIES:
                self._add_follow_action(m, row)
        if self.tmdb:
            m.addSeparator()
            m.addAction(tr("ctx_match_tmdb"),
                        partial(self._open_tmdb_match_dialog, row,
                                "vod" if e.kind == mitt.FILM else "series"))
        kind = "vod" if e.kind == mitt.FILM else "series"
        m.addSeparator()
        trakt_ok = bool(self._watch_accounts()) and (
            self.tmdb is not None or isinstance(row.get("_tmdb_id"), int))
        if e.kind == mitt.FILM:
            mark, unmark, watched = (self._mark_movie_watched, self._unmark_movie_watched,
                                     self.is_movie_watched(row))
        else:
            mark, unmark, watched = (self._mark_series_watched, self._unmark_series_watched,
                                     self.show_watched_count(row) > 0)
        if watched:
            m.addAction(tr("ctx_unmark_watched"), partial(unmark, row, False))
            if trakt_ok:
                m.addAction(self._account_action("ctx_unmark_watched"), partial(unmark, row, True))
        else:
            m.addAction(tr("ctx_mark_watched"), partial(mark, row, False))
            if trakt_ok and e.kind == mitt.FILM:
                m.addAction(self._account_action("ctx_mark_watched"), partial(mark, row, True))
        trakt_wl = bool(self._watch_accounts()) and self.tmdb is not None
        if self.is_on_watchlist(row, kind):
            m.addAction(tr("ctx_watchlist_remove"), partial(self._remove_watchlist, row, kind,
                                                            False))
            if trakt_wl:
                m.addAction(self._account_action("ctx_watchlist_remove"),
                            partial(self._remove_watchlist, row, kind, True))
        else:
            m.addAction(tr("ctx_watchlist_add"), partial(self._add_watchlist, row, kind, False))
            if trakt_wl:
                m.addAction(self._account_action("ctx_watchlist_add"),
                            partial(self._add_watchlist, row, kind, True))
        if e.kind == mitt.FILM:
            m.addSeparator()
            m.addAction(tr("ctx_copy_stream_url"), partial(self._mitt_copy, e))

    def _mitt_cast_episode(self, e: mitt.Entry) -> None:
        saved = self.series_ctx
        self.series_ctx = e.row.get("_series_ctx") or saved
        try:
            self._open_cast_dialog(e.row)
        finally:
            self.series_ctx = saved

    def _mitt_forget_resume(self, e: mitt.Entry) -> None:
        self._remove_continue({**e.row, "_kind": "episode" if e.kind == mitt.EPISODE else "vod"})
        self._mitt_refresh_soon()

    # -- the folders -------------------------------------------------------------------
    #
    # Every source's favourites, one folder of a name whatever source and kind
    # hold it (core/mitt.fav_folders), read on the page's worker. What a move,
    # a reorder or a title out of its folder does acts on each title's own
    # source's store; a folder renamed, removed, locked or coloured is so in
    # every source that holds it - all of them or none (_mitt_change).

    def _mitt_fav_stores(self):
        """(section, store, id key, kind) of the window's own favourites:
        the source in use's, the stations', the web streams'."""
        for section, attr, id_key, kind in _FAV_SECTIONS:
            store = getattr(self, attr, None)
            if store is not None:
                yield section, store, id_key, kind

    def _mitt_folders(self) -> _Folders | None:
        data = self._mitt_data
        return data.folders if data is not None else None

    def _mitt_folders_stale(self) -> None:
        """The window's own favourites changed since the folders were read
        (the sidebar's menus, a list's heart): read again - once."""
        folders = self._mitt_folders()
        if folders is None:
            return
        now = self._mitt_fav_stamp()
        if now != folders.stamp and now != self._mitt_fstamp_asked:
            self._mitt_folders_build()

    def _mitt_folders_changed(self) -> None:
        """A folder or a favourite changed from here: the folders read again
        (and the Overview once it shows)."""
        self._mitt_folders_dirty = True
        self._mitt_folders_build()

    def _mitt_lock_union(self) -> set[str]:
        """Every folder locked in any store the folders hold, as kept now (a
        lock set a moment ago counts at once): a folder of a name is locked
        when any source keeps it so."""
        own = self._own(self.settings)
        out: set[str] = set()
        for _src, _sec, key in self._mitt_store_keys():
            out.update(x for x in _json_of(own.value(f"{key}_locked", ""), list)
                       if isinstance(x, str))
        return out

    def _mitt_lock_real(self, name: str, locks: set[str] | None = None) -> bool:
        """Whether folder *name* is locked in any source (whether the lock
        is shut or not)."""
        locks = locks if locks is not None else self._mitt_lock_union()
        folders = self._mitt_folders()
        f = folders.named.get(name) if folders is not None else None
        return name in locks or bool(f is not None and f.locked)

    def _mitt_locked(self, name: str, locks: set[str] | None = None) -> bool:
        """Whether folder *name* is locked and the lock is shut."""
        if getattr(self.parental, "session_unlocked", False):
            return False
        return self._mitt_lock_real(name, locks)

    def _mitt_folder_names(self) -> list[str]:
        """The folders' names across every source - for a kids profile none
        that is locked."""
        folders = self._mitt_folders()
        if folders is None:
            return []
        if getattr(self, "_profile_kids", lambda: False)():
            locks = self._mitt_lock_union()
            return [n for n in folders.named if not self._mitt_lock_real(n, locks)]
        return list(folders.named)

    def _mitt_folders_fill(self) -> None:
        """The folders' tiles, and the one picked's titles under them - of
        every source, each saying which (a kids profile: the source in
        use's, and never a locked folder)."""
        page = self._mitt_page
        folders = self._mitt_folders()
        if page is None or folders is None:
            return
        self._mitt_folders_stale()
        kids = bool(getattr(self, "_profile_kids", lambda: False)())
        unlocked = bool(getattr(self.parental, "session_unlocked", False))
        locks = self._mitt_lock_union()
        chip = page.chip()
        terms = mitt.terms_of(page.query())
        names = [n for n in folders.named if not (kids and self._mitt_lock_real(n, locks))]
        if self._mitt_folder != LOOSE and self._mitt_folder not in names \
                and not self._mitt_folders_pending():
            self._mitt_folder = LOOSE
        tiles = [Card(key=LOOSE, title=tr("nav_favorites"),
                      line=self._mitt_counts(folders.loose), initials="♥")]
        for n in names:
            shut = not unlocked and self._mitt_lock_real(n, locks)
            title = f"{n}  {tr('cat_locked_suffix')}" if shut else n
            tiles.append(Card(key=f"f:{n}", title=title, line=self._mitt_counts(folders.named[n]),
                              data=n))
        tiles.append(Card(key=FolderTileView.NEW, title=tr("mitt_new_folder")))
        loose = self._mitt_folder == LOOSE
        page.set_folders(tiles, LOOSE if loose else f"f:{self._mitt_folder}")
        folder = folders.loose if loose else folders.named.get(self._mitt_folder)
        hidden = not loose and self._mitt_lock_real(self._mitt_folder, locks) \
            and (kids or not unlocked)
        cards = []
        if folder is not None and not hidden:
            names_of = self._mitt_names()
            many = len(names_of) > 1
            for e in folder.entries:
                if mitt.matches(e, chip, terms):
                    cards.append(self._mitt_folder_card(e, many, names_of))
        title = tr("nav_favorites") if loose else self._mitt_folder
        empty = "" if cards else (tr("mitt_nothing") if (terms or chip != mitt.ALL)
                                  else tr("mitt_folder_empty"))
        page.set_folder_items(title, self._mitt_counts(folder), cards, empty, True)
        self._mitt_mark()

    def _mitt_counts(self, folder: mitt.Folder | None) -> str:
        words = {"chan": ("mitt_n_channel", "mitt_n_channels"),
                 "movie": ("mitt_n_film", "mitt_n_films"),
                 "series": ("mitt_n_show", "mitt_n_shows"),
                 "radio": ("mitt_n_station", "mitt_n_stations"),
                 "web": ("mitt_n_web_one", "mitt_n_web")}
        parts = []
        for section, _kind, _id in mitt.FAV_SECTIONS:
            n = folder.counts.get(section, 0) if folder is not None else 0
            if n:
                one, many = words[section]
                parts.append(tr(one if n == 1 else many, n=n))
        return " · ".join(parts)

    def _mitt_folder_card(self, e: mitt.Entry, many: bool = False,
                          names: dict[str, str] | None = None) -> Card:
        """A folder's title: its picture, its name and - with two sources or
        more - the source it is of, after its kind."""
        row = e.row
        logo = e.kind in (mitt.CHANNEL, mitt.RADIO, mitt.WEB)
        image = str(row.get("stream_icon") or row.get("favicon") or "") if logo else \
            self._mitt_art(row, "vod" if e.kind == mitt.FILM else "series")
        source = ""
        if many and e.source:
            source = (names if names is not None else self._mitt_names()).get(e.source, "?")
        line = self._mitt_join(tr(_KIND_WORD[e.kind]), source)
        return Card(key=e.key, title=e.title, line=line, image=image or "",
                    rank=FolderItemsView.LOGO if logo else 0, data=e)

    def _mitt_swatch(self, card: Card) -> tuple[QColor, QColor] | None:
        name = card.data if isinstance(card.data, str) else None
        folders = self._mitt_folders()
        f = folders.named.get(name) if (name and folders is not None) else None
        colours = f.colour if f is not None else {}
        bg, fg = colours.get("bgcolor"), colours.get("color")
        if bg:
            return QColor(bg), QColor(fg or "#ffffff")
        if fg:
            return QColor(fg), QColor("#ffffff")
        return None

    def _mitt_folder_picked(self, card: Card) -> None:
        name = card.data if isinstance(card.data, str) else None
        if name and self._mitt_locked(name) and not self._request_unlock():
            return
        self._mitt_folder = name or LOOSE
        self._mitt_folders_fill()

    def _mitt_folder_play(self, card: Any) -> None:
        """A folder's title played as Mine plays it - another source's
        channel from that source without switching, its film or series once
        it is switched to (_mitt_play)."""
        e = self._mitt_entry(card)
        if e is None:
            return
        if e.kind in (mitt.CHANNEL, mitt.FILM, mitt.SERIES):
            e = mitt.Entry(e.kind, e.ident, e.title, self._mitt_plain(e.row), e.source)
        self._mitt_play(e)

    def _mitt_folder_new(self, pos: QPoint) -> None:
        """New folder: for which kind of favorite (of the source in use),
        then its name."""
        m = QMenu(self)
        labels = {"chan": "fav_channels", "movie": "fav_movies", "series": "fav_series",
                  "radio": "fav_radio", "web": "fav_web"}
        for section, store, _k, _kind in self._mitt_fav_stores():
            m.addAction(tr(labels[section]), partial(self._mitt_folder_create, store))
        m.exec(pos)
        m.deleteLater()

    def _mitt_folder_create(self, store) -> None:
        name = self._ask_folder_name()
        if name:
            store.ensure_group(name)
            self._mitt_folder = name
            self._mitt_after_folders()

    def _mitt_after_folders(self) -> None:
        if self.mode == "fav":
            self._load_categories()
        else:
            try:
                self.list_model.refresh_all()
            except Exception:
                pass
        self._mitt_folders_changed()

    def _mitt_change(self, steps: list[tuple[Any, Callable[[Any], None]]]) -> bool:
        """*steps* - (a store, what is done to it) - in order: all of them or
        none. One that fails puts every store it touched back as it was
        (its folders, locks and colours, as kept) and says so. True when
        all were made."""
        kept: dict[int, tuple[Any, tuple]] = {}
        try:
            for store, step in steps:
                if id(store) not in kept:
                    kept[id(store)] = (store, _state_of(store))
                step(store)
        except Exception:
            log.exception("mine: a folder was not changed - every source put back as it was")
            for store, state in reversed(list(kept.values())):
                _put_back(store, state)
            self._show_toast(tr("mitt_folder_not_changed"), 5000)
            self._mitt_after_folders()
            return False
        return True

    def _mitt_folder_menu(self, card: Card, pos: QPoint) -> None:
        name = card.data if isinstance(card.data, str) else None
        if not name:
            return
        m = QMenu(self)
        m.addAction(tr("ctx_rename_folder"), partial(self._mitt_folder_rename, name))
        m.addAction(tr("ctx_remove_folder", group=name), partial(self._mitt_folder_remove, name))
        folders = self._mitt_folders()
        f = folders.named.get(name) if folders is not None else None
        if self._mitt_lock_real(name):
            m.addAction(tr("ctx_unlock_group"), partial(self._mitt_folder_lock, name, False))
        elif f is not None and any(sec == "chan" for _src, sec in f.places):
            # Folders of channels lock (as the lists have it): in every
            # source that holds this one.
            m.addAction(tr("ctx_lock_group"), partial(self._mitt_folder_lock, name, True))
        palette = ((tr("color_default"), ""), (tr("accent_blue"), "#4C8DFF"),
                   (tr("accent_green"), "#2FBF71"), (tr("accent_orange"), "#FF9F43"),
                   (tr("accent_red"), "#FF5C5C"), (tr("accent_purple"), "#8E6BFF"),
                   (tr("accent_teal"), "#2AC3C3"), (tr("accent_pink"), "#FF5C8A"))
        cm = m.addMenu(tr("ctx_set_bg_color"))
        for label, hexv in palette:
            cm.addAction(label, partial(self._mitt_folder_colour, name, hexv))
        m.exec(pos)
        m.deleteLater()

    def _mitt_folder_rename(self, name: str) -> None:
        """Renamed in every source that holds it - its titles, its lock and
        its colour with it."""
        new, ok = QInputDialog.getText(self, tr("ctx_rename_folder"), tr("prompt_folder_name"),
                                       text=name)
        new = (new or "").strip()
        if not ok or not new or new in (name, FAV_DEFAULT_GROUP):
            return
        every = _Stores(self).every()
        steps: list = [(st, partial(_renamed, name, new)) for _src, _sec, st in every
                       if name in st.groups]
        # A lock kept by a source's channels for a folder they do not hold
        # (its films do) goes with the name too.
        steps += [(st, partial(_move_lock, name, new)) for _src, _sec, st in every
                  if name in st.locked_groups()]
        if not self._mitt_change(steps):
            return
        if self._mitt_folder == name:
            self._mitt_folder = new
        self._mitt_after_folders()

    def _mitt_folder_remove(self, name: str) -> None:
        """Removed from every source that holds it (its titles stay
        favourites elsewhere); a locked one asks for the PIN."""
        if self._mitt_lock_real(name) and not self._request_unlock():
            return
        every = _Stores(self).every()
        steps: list = [(st, partial(_removed, name)) for _src, _sec, st in every
                       if name in st.groups]
        steps += [(st, partial(_lock_off, name)) for _src, _sec, st in every
                  if name not in st.groups and name in st.locked_groups()]
        if not self._mitt_change(steps):
            return
        if self._mitt_folder == name:
            self._mitt_folder = LOOSE
        self._mitt_after_folders()

    def _mitt_folder_lock(self, name: str, locked: bool) -> None:
        """Locked - or opened - in every source that holds it, the PIN
        asked as the lists ask it (mw_context._set_fav_lock)."""
        if locked and not self._ensure_pin_configured():
            return
        if not locked and not self._request_unlock():
            return
        stores = _Stores(self)
        every = stores.every()
        steps: list = []
        if locked:
            owners: list = []
            for src, sec, st in every:
                owner = stores.owner(src, sec) if name in st.groups else None
                if owner is not None and all(o is not owner for o in owners):
                    owners.append(owner)
            steps = [(o, partial(_lock_on, name)) for o in owners]
        else:
            steps = [(st, partial(_lock_off, name)) for _src, _sec, st in every
                     if name in st.locked_groups()]
        if not self._mitt_change(steps):
            return
        if locked:
            self.parental.lock_session()
        self._mitt_after_folders()

    def _mitt_folder_colour(self, name: str, colour: str) -> None:
        every = _Stores(self).every()
        steps = [(st, partial(_coloured, name, colour)) for _src, _sec, st in every
                 if name in st.groups]
        if self._mitt_change(steps):
            self._mitt_after_folders()

    def _mitt_folder_target(self, folder: Any) -> str:
        if isinstance(folder, Card):
            folder = folder.data if isinstance(folder.data, str) else LOOSE
        return FAV_DEFAULT_GROUP if folder in (None, LOOSE) else str(folder)

    def _mitt_folder_here(self) -> str:
        return FAV_DEFAULT_GROUP if self._mitt_folder == LOOSE else self._mitt_folder

    def _mitt_drop_shown(self, keys: set[str]) -> None:
        """Titles moved out of the folder shown: gone from it at once (the
        folders read again bring the rest)."""
        folders = self._mitt_folders()
        folder = None if folders is None else (
            folders.loose if self._mitt_folder == LOOSE else folders.named.get(self._mitt_folder))
        if folder is not None:
            gone = [x for x in folder.entries if x.key in keys]
            folder.entries = [x for x in folder.entries if x.key not in keys]
            for x in gone:
                sec = str(x.row.get("_section"))
                folder.counts[sec] = max(0, folder.counts.get(sec, 0) - 1)
        self._mitt_folders_fill()

    def _mitt_folder_drop(self, cards: list, folder: Any) -> None:
        """Titles dropped on a folder's tile: moved there out of the folder
        shown (the favorites outside folders: out of no other) - each in its
        own source's store. A folder locked or coloured in one source is so
        in the one a title comes to it from."""
        target = self._mitt_folder_target(folder)
        here = self._mitt_folder_here()
        if target == here:
            return
        if target != FAV_DEFAULT_GROUP and self._mitt_locked(target) \
                and not self._request_unlock():
            return
        entries = [e for e in (self._mitt_entry(c) for c in cards) if e is not None]
        if not entries:
            return
        stores = _Stores(self)
        by: dict[tuple[str, str], list[dict]] = {}
        for e in entries:
            by.setdefault((e.source, str(e.row.get("_section"))), []).append(
                self._mitt_plain(e.row))
        named = target != FAV_DEFAULT_GROUP
        locked = named and self._mitt_lock_real(target)
        folders = self._mitt_folders()
        f = folders.named.get(target) if (named and folders is not None) else None
        colour = dict(f.colour) if f is not None else {}
        steps: list = []
        for (src, sec), items in by.items():
            st = stores.get(src, sec)
            if st is None:
                continue
            steps.append((st, partial(_moved_into, items, target, here)))
            owner = stores.owner(src, sec) if locked else None
            if owner is not None:
                steps.append((owner, partial(_lock_on, target)))
            if colour:
                steps.append((st, partial(_colour_on, target, colour)))
        if not self._mitt_change(steps):
            return
        self._mitt_after_folders()
        self._mitt_drop_shown({e.key for e in entries})

    @staticmethod
    def _mitt_plain(row: dict) -> dict:
        return {k: v for k, v in row.items() if k not in ("_section", "_group")
                and not (k in ("_kind", "_key") and row.get("_section") in ("radio", "web"))}

    def _mitt_folder_reorder(self, cards: list, index: int) -> None:
        """Titles dragged to another place in the folder shown - each kept
        in its own store's order there (a title of another source or kind
        keeps the place of its own among them)."""
        page = self._mitt_page
        if page is None:
            return
        shown = list(page.folder_items.items())
        keys = [c.key for c in shown]
        moving = [c.key for c in cards if c.key in keys]
        if not moving:
            return
        order = mitt.moved_to(keys, moving, index)
        by = {c.key: c.data for c in shown if isinstance(c.data, mitt.Entry)}
        group = self._mitt_folder_here()
        touched = {(by[k].source, str(by[k].row.get("_section"))) for k in moving if k in by}
        stores = _Stores(self)
        steps: list = []
        for src, sec in touched:
            id_key = _ID_KEY.get(sec, "stream_id")
            ids = [by[k].row.get(id_key) for k in order if k in by
                   and by[k].source == src and str(by[k].row.get("_section")) == sec]
            st = stores.get(src, sec)
            if st is not None and ids:
                steps.append((st, partial(_reordered, group, ids)))
        if not self._mitt_change(steps):
            return
        folders = self._mitt_folders()
        folder = None if folders is None else (
            folders.loose if self._mitt_folder == LOOSE else folders.named.get(self._mitt_folder))
        if folder is not None:
            folder.entries = mitt.reordered_within(folder.entries, order)
        self._mitt_after_folders()
        self._mitt_folders_fill()
        page.folder_items.focus_key(moving[0], take_focus=False)

    def _mitt_folder_item_menu(self, card: Any, pos: QPoint) -> None:
        e = self._mitt_entry(card)
        if e is None:
            return
        page = self._mitt_page
        picked = page.folder_items.selected_cards() if page is not None else []
        batch = picked if card in picked and len(picked) > 1 else [card]
        m = QMenu(self)
        elsewhere = not self._mitt_is_active(e)
        if elsewhere:
            # Another source's title: played or opened from there, out of
            # its own source's favourites - none of the source in use's
            # actions apply to it.
            m.addAction(tr("sa_open_series") if e.kind == mitt.SERIES else tr("btn_play"),
                        partial(self._mitt_folder_play, card))
            m.addAction(tr("ctx_remove_from_favorites"), partial(self._mitt_folder_unfav, [card]))
        elif e.kind == mitt.CHANNEL:
            self._mitt_channel_menu(m, e, "folder")
        elif e.kind in (mitt.RADIO, mitt.WEB):
            self._stream_row_actions(m, e.row)
        else:
            self._mitt_title_menu(m, e, "folder")
        m.addSeparator()
        many = f" ({len(batch)})" if len(batch) > 1 else ""
        sub = m.addMenu(tr("ctx_move_to") + many)
        here = self._mitt_folder_here()
        if here != FAV_DEFAULT_GROUP:
            sub.addAction(tr("nav_favorites"), partial(self._mitt_folder_drop, batch, LOOSE))
        for n in self._mitt_folder_names():
            if n != here:
                sub.addAction(n, partial(self._mitt_folder_drop, batch, n))
        sub.addSeparator()
        sub.addAction(tr("ctx_new_folder"), partial(self._mitt_folder_move_new, batch))
        if here != FAV_DEFAULT_GROUP:
            m.addAction(tr("mitt_out_of_folder") + many, partial(self._mitt_folder_out, batch))
        if elsewhere:
            m.addSeparator()
            note = m.addAction(tr("sa_from_source", source=self._mitt_names().get(e.source, "?")))
            note.setEnabled(False)
        m.exec(pos)
        m.deleteLater()
        self._mitt_folders_fill()

    def _mitt_folder_move_new(self, cards: list) -> None:
        name = self._ask_folder_name()
        if name:
            self._mitt_folder_drop(cards, name)

    def _mitt_folder_out(self, cards: list) -> None:
        """Out of the folder shown (still a favorite elsewhere, if it is) -
        each out of its own source's store."""
        group = self._mitt_folder
        if group == LOOSE:
            return
        entries = [e for e in (self._mitt_entry(c) for c in cards) if e is not None]
        stores = _Stores(self)
        steps: list = []
        for e in entries:
            sec = str(e.row.get("_section"))
            st = stores.get(e.source, sec)
            if st is not None:
                steps.append((st, partial(_out_of, e.row.get(_ID_KEY.get(sec, "stream_id")),
                                          group)))
        if not self._mitt_change(steps):
            return
        self._mitt_after_folders()
        self._mitt_drop_shown({e.key for e in entries})

    def _mitt_folder_unfav(self, cards: list) -> None:
        """Out of the favorites of its own source, every folder there."""
        entries = [e for e in (self._mitt_entry(c) for c in cards) if e is not None]
        stores = _Stores(self)
        steps: list = []
        for e in entries:
            sec = str(e.row.get("_section"))
            st = stores.get(e.source, sec)
            if st is not None:
                steps.append((st, partial(_out_of, e.row.get(_ID_KEY.get(sec, "stream_id")),
                                          None)))
        if not self._mitt_change(steps):
            return
        self._mitt_after_folders()
        self._mitt_drop_shown({e.key for e in entries})


class _Stores:
    """The favourites' stores one change acts on, each made once: the
    source in use's, the stations' and the web streams' the window's own,
    another source's read from the settings now (this profile's) - none for
    a kids profile, which sees the source in use alone."""

    def __init__(self, window: Any) -> None:
        self._w = window
        self._own = {(src, sec): st for src, sec, st in window._mitt_own_stores()}
        self._others = set(window._mitt_other_ids())
        self._made: dict[tuple[str, str], FavoriteStore] = {}

    def get(self, source: str, section: str) -> Any:
        if section not in _FAV_KEY:
            source = ""                     # the stations' and web streams': no source's
        st = self._own.get((source, section))
        if st is not None:
            return st
        if section not in _FAV_KEY or source not in self._others:
            return None
        key = (source, section)
        if key not in self._made:
            self._made[key] = FavoriteStore(self._w._own(self._w.settings),
                                            _fav_key(section, source), _ID_KEY[section])
        return self._made[key]

    def owner(self, source: str, section: str) -> Any:
        """The store a source keeps its folders' locks in: its channels'
        (the lists lock channel folders); the stations' and web streams'
        their own."""
        return self.get(source, "chan" if section in _FAV_KEY else section)

    def every(self) -> list[tuple[str, str, Any]]:
        out = []
        for src, sec, _key in self._w._mitt_store_keys():
            st = self.get(src, sec)
            if st is not None:
                out.append((src, sec, st))
        return out


#: What of a store is kept beside its folders: its folders, locks, colours.
_STORE_SUFFIXES = ("", "_locked", "_colors")


def _state_of(store: Any) -> tuple:
    """A store as it is now, to be put back (_put_back)."""
    s = store.settings
    values = []
    for suffix in _STORE_SUFFIXES:
        key = store.key + suffix
        values.append((key, bool(s.contains(key)), s.value(key)))
    return {g: list(v) for g, v in store.groups.items()}, values


def _put_back(store: Any, state: tuple) -> None:
    groups, values = state
    store.groups = groups
    for key, had, value in values:
        try:
            if had:
                store.settings.setValue(key, value)
            else:
                store.settings.remove(key)
        except Exception:
            log.warning("mine: a favourites value was not put back")
    store.version += 1


def _renamed(old: str, new: str, store: Any) -> None:
    store.rename_group(old, new)


def _removed(name: str, store: Any) -> None:
    store.remove_group(name)


def _coloured(name: str, colour: str, store: Any) -> None:
    store.set_group_color(name, bgcolor=colour)


def _lock_on(name: str, store: Any) -> None:
    if name not in store.locked_groups():
        store.set_group_locked(name, True)


def _lock_off(name: str, store: Any) -> None:
    if name in store.locked_groups():
        store.set_group_locked(name, False)


def _move_lock(old: str, new: str, store: Any) -> None:
    if old in store.locked_groups():
        store.set_group_locked(old, False)
        store.set_group_locked(new, True)


def _colour_on(name: str, colour: dict, store: Any) -> None:
    if not store.group_color(name):
        store.set_group_color(name, **colour)


def _moved_into(items: list, target: str, source: str, store: Any) -> None:
    store.move(items, target, source)


def _reordered(group: str, ids: list, store: Any) -> None:
    store.reorder(group, ids)


def _out_of(ident: Any, group: str | None, store: Any) -> None:
    if ident is not None:
        store.remove(ident, group)
