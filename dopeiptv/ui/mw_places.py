"""The window's places (core/places.py): the sidebar is the app's structure -
Mine, TV, Movies, Series, Guide, Search, then Local files, Radio,
Recordings - and there is no Home.

``_show_place(name)`` is the one way to a place: a nav entry, a back
button, Esc from the Guide or Search, a profile switch and the start all
go through it. A place's own page answers through its hook
(``_mitt_show``, ``_place_tv_show``, ``_place_films_show``,
``_place_series_show``: True once it showed); without one the place shows
its list under the shared header (``media_cards.PageHeader``) - Mine as
tabs Favorites / Watch later / History over today's lists.

``_place`` is the place on screen, ``_list_place`` the list place under a
page; ``_place_entered(name)`` notes a place as current (its entry lit,
kept for the next start - each profile its own). The app opens where the
person left off, or always on Mine (``start_place`` on Settings > General).
"""
from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from ..core import places
from ..core.log import log
from ..i18n import tr

#: The words of each place, as its entry and header say them.
PLACE_WORDS = {"mitt": "nav_mine", "tv": "nav_tv", "films": "nav_movies",
               "series": "nav_series", "guide": "nav_guide", "search": "nav_search",
               "local": "nav_local", "radio": "nav_radio", "rec": "nav_recordings"}
#: Mine's tabs while it shows today's lists.
MITT_TAB_WORDS = {"fav": "nav_favorites", "watchlist": "nav_watchlist",
                  "history": "nav_history"}


def place_title(place: str) -> str:
    return tr(PLACE_WORDS.get(place, "nav_tv"))


class _PlacesMixin:
    """MainWindow mixin: the places and the way to each."""

    _place = places.DEFAULT_PLACE
    _list_place = places.DEFAULT_PLACE
    #: Mine's list last shown (its tab; History's Watched filter included).
    _mitt_mode = "fav"
    _place_count_text = ""
    _place_header = None
    _start_page = ""
    _start_base = places.DEFAULT_PLACE

    # -- the start ------------------------------------------------------------------

    def _init_places(self) -> None:
        """Before the window is built: Home's settings carried over (once),
        and the list mode of the place the app opens on - so the first
        list fetched is that place's, not TV's and then another."""
        try:
            from ..core import profiles
            places.retire_home(profiles.raw(self.settings), profiles.active_id())
        except Exception as e:
            log.warning("places: Home's settings: %s", e)
        base, page = places.start_places(self.settings)
        self._list_place = base
        self._place = base
        self._start_base = base
        self._start_page = page
        if base == "mitt":
            self.mode = self._mitt_tab_mode()
        else:
            self.mode = places.MODE_OF.get(base, "live")

    def _start_loader(self):
        """What loads the start's first list: TV's categories as ever, or
        another place's whole mode set up, as its entry would."""
        if self.mode == "live":
            return self._load_categories
        return lambda: self.switch_mode(self.mode)

    def _open_start_place(self) -> None:
        """Once the first list is asked for: the place's own page over it
        when it has one, then the Guide or Search when the person left off
        there."""
        if sip.isdeleted(self):
            return
        base = self._start_base
        if self._place != base:
            return                      # gone somewhere else already
        hook = places.SHOW_HOOKS.get(base)
        if hook and callable(getattr(self, hook, None)):
            self._show_place(base)
        else:
            self._place_entered(base)
        page, self._start_page = self._start_page, ""
        if page:
            QTimer.singleShot(0, lambda p=page: self._show_place(p))

    def _profile_place(self) -> None:
        """Another profile in use: its start place - where that person left
        off, or Mine - with its list read anew for the profile."""
        base, page = places.start_places(self.settings)
        mode = self._mitt_tab_mode() if base == "mitt" else places.MODE_OF.get(base, "live")
        if self.mode == mode or (base == "mitt" and self.mode in places.MITT_MODES):
            self._pages_give_way()
            self._load_categories()
        else:
            self.switch_mode(mode)
        hook = places.SHOW_HOOKS.get(base)
        if hook and callable(getattr(self, hook, None)):
            self._show_place(base)
        else:
            self._place_entered(base)
        if page:
            self._show_place(page)

    # -- going to a place --------------------------------------------------------------

    def _show_place(self, name: str, reload: bool = False) -> bool:
        """Go to place *name*: its own page when it has one, else its list
        under the shared header. Back on the place shown (a page over it -
        the Guide, Search, a series - gives way); *reload* reads its list
        again even then. True once it shows."""
        name = places.valid(name) or places.DEFAULT_PLACE
        if name == "guide":
            self._open_epg_guide()
            return True
        if name == "search":
            self._open_search_page()
            return True
        hook = places.SHOW_HOOKS.get(name)
        fn = getattr(self, hook, None) if hook else None
        if callable(fn):
            self._pages_give_way()
            try:
                shown = bool(fn())
            except Exception:
                log.exception("places: %s's page", name)
                shown = False
            if shown:
                self._place_entered(name)
                return True
        return self._show_place_list(name, reload)

    def _show_place_list(self, name: str, reload: bool = False) -> bool:
        """A place as its list, under the shared header."""
        mode = self._mitt_tab_mode() if name == "mitt" else places.MODE_OF.get(name, "live")
        same = (self.mode in places.MITT_MODES) if name == "mitt" else self.mode == mode
        if same and not reload:
            self._pages_give_way()
            self._place_entered(name)
            lw = getattr(self, "listw", None)
            if lw is not None:
                lw.setFocus()
            return True
        self.switch_mode(self.mode if (same and name == "mitt") else mode)
        return True

    def _pages_give_way(self) -> None:
        """The pages over a place's list - the Guide, Search, a series', a
        place's own - give way to the middle pane's list (the central
        stack's too, unless TV mode holds the window); a place's own page
        is shown again by its hook."""
        self._close_guide_page()
        self._close_series_page()
        self._close_search_page()
        # A place's own pages close through their own way out, so what they
        # forget on leaving (Mine's search while viewing privately) is forgotten.
        for close in ("_close_mitt_page",):
            fn = getattr(self, close, None)
            if callable(fn):
                fn()
        mid = getattr(self, "_mid_stack", None)
        if mid is not None and mid.currentIndex() != 0:
            mid.setCurrentIndex(0)
        stack = getattr(self, "_center_stack", None)
        if stack is not None and stack.currentIndex() != 0 and not self._tv_land_on(0):
            stack.setCurrentIndex(0)

    def _return_to_place(self) -> None:
        """Back from the Guide, Search or a series' page: the place under it,
        as it was left."""
        place = self._list_place if self._list_place in places.LIST_PLACES \
            else places.place_of_mode(getattr(self, "mode", "live"))
        hook = places.SHOW_HOOKS.get(place)
        fn = getattr(self, hook, None) if hook else None
        if callable(fn):
            try:
                if fn():
                    self._place_entered(place)
                    return
            except Exception:
                log.exception("places: %s's page", place)
        mid = getattr(self, "_mid_stack", None)
        if mid is not None and mid.currentIndex() != 0:
            mid.setCurrentIndex(0)
        self._place_entered(place)
        lw = getattr(self, "listw", None)
        if lw is not None:
            lw.setFocus()

    def _place_entered(self, name: str) -> None:
        """*name* is the place shown now: its entry lit, its header said,
        kept for the next start. A place's own page calls it as it shows."""
        name = places.valid(name)
        if not name:
            return
        self._place = name
        if name not in places.PAGE_PLACES:
            self._list_place = name
        if hasattr(self, "nav_btns"):
            self._check_nav(places.NAV_OF[name])
        try:
            places.note(self.settings, name, self._list_place)
        except Exception as e:
            log.debug("places: not kept: %s", e)
        self._sync_place_header()

    def _mode_place(self, mode: str) -> None:
        """A list mode switched to (switch_mode): its place is the list
        place now - and the place shown, unless a page stays over it."""
        if mode in places.MITT_MODES:
            self._mitt_mode = mode
        place = places.place_of_mode(mode)
        self._list_place = place
        over = (self._guide_page_showing() or self._search_page_showing())
        if over:
            self._sync_place_header()
        else:
            self._place_entered(place)

    def _mitt_tab_mode(self) -> str:
        mode = self._mitt_mode if self._mitt_mode in places.MITT_MODES else "fav"
        if mode in ("history", "watched"):
            mode = "watched" if getattr(self, "_history_filter", "all") == "watched" \
                else "history"
        return mode

    def _replay_history_row(self, it: dict) -> None:
        """A History row played again (Mine's history): from its stored
        address and kind, wherever the window is - a station as Radio plays
        it, an episode under its series (resume, marks, scrobbles; no
        queue), then its series' page with the episode marked (Back: the
        place under it)."""
        url = it.get("_url")
        if not url:
            return
        kind = it.get("_kind") or "live"
        if kind == "radio":
            self._radio_play(it)
            return
        ctx = it.get("_series_ctx") or {}
        if kind == "episode" and ctx.get("series_id") is None:
            # Recorded before the series' snapshot existed: a film then.
            kind = "movie"
        try:
            self._current_key = it.get("_key")
            self._show_detail(it)       # the panel follows this row
        except Exception:
            pass
        saved = self.series_ctx
        if kind == "episode":
            self.series_ctx = ctx
            self._ep_queue_override, self._ep_index_override = [], -1
        try:
            self._start_playback(url, it.get("name") or "dopeIPTV",
                                 it.get("stream_icon"), it.get("_key"), kind, item=it)
        finally:
            self.series_ctx = saved
            if kind == "episode":
                self._ep_queue_override, self._ep_index_override = None, -1
        if kind == "episode":
            self._open_series_page(ctx, focus=it.get("_key"))

    def _refresh_places(self, reason: str = "") -> None:
        """What a place shows changed (a profile, a source, a followed
        series, a sync): each place's own page asked to show it again - and
        for another profile or a sync what the places show (mw_place_look:
        the sidebar's entries, the fold, Just the list) as that one says."""
        if reason in ("profile", "sync"):
            look = getattr(self, "_place_look_apply", None)
            if callable(look):
                if reason == "profile":
                    self._jl_views = None
                try:
                    look()
                except Exception:
                    log.exception("places: what the places show (%s)", reason)
        for place, hook in places.REFRESH_HOOKS.items():
            fn = getattr(self, hook, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    log.exception("places: refreshing %s (%s)", place, reason)

    # -- the shared header over today's lists ------------------------------------------

    def _build_place_header(self, layout) -> None:
        """The header over the middle pane's list: the place's name, its
        count, Mine's tabs."""
        from .media_cards import PageHeader
        holder = QWidget(objectName="PlaceHeaderHolder")
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(0)
        header = PageHeader("")
        header.setObjectName("PlaceHeader")
        header.view_changed.connect(self._place_tab_picked)
        lay.addWidget(header)
        layout.insertWidget(0, holder)
        self._place_header = header
        self._place_header_holder = holder
        self._sync_place_header()

    def _sync_place_header(self) -> None:
        header = self._place_header
        if header is None or sip.isdeleted(header):
            return
        place = self._list_place if self._list_place in places.LIST_PLACES else "tv"
        header.set_title(place_title(place))
        header.set_count(self._place_count_text)
        if place == "mitt":
            mode = getattr(self, "mode", "fav")
            tab = "history" if mode in ("history", "watched") else (
                mode if mode in places.MITT_TABS else "fav")
            opts = [(k, tr(MITT_TAB_WORDS[k])) for k in places.MITT_TABS]
            if header.switch.keys() != list(places.MITT_TABS) or [
                    b.text() for b in header.switch.buttons] != [w for _k, w in opts]:
                header.set_views(opts, tab)
            else:
                header.set_view_key(tab)
            header.switch.setAccessibleName(tr("nav_mine"))
        elif header.switch.keys():
            header.set_views([])

    def _place_tab_picked(self, key: str) -> None:
        """One of Mine's tabs: its list (History back on the filter last
        chosen)."""
        if key not in places.MITT_TABS:
            return
        if key == "history":
            mode = "watched" if getattr(self, "_history_filter", "all") == "watched" \
                else "history"
        else:
            mode = key
        if mode != getattr(self, "mode", ""):
            self.switch_mode(mode)

    def _place_count(self, text: str) -> None:
        """The list's count, under the header's name."""
        self._place_count_text = text or ""
        header = self._place_header
        if header is not None and not sip.isdeleted(header):
            header.set_count(self._place_count_text)

    def _places_restyle(self) -> None:
        header = self._place_header
        if header is not None and not sip.isdeleted(header):
            header.restyle()
