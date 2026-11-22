"""The lists in the window (MainWindow mixin): a mode switched and what a
source has (``switch_mode``, ``_refresh_mode_availability``,
``_apply_mode_visibility``); the category column, its category kept per
mode (``_load_categories``, ``_category_changed``); the middle list
(``_load_items``; the combined views under their headings,
``_show_grouped``, the recordings' and the favourites'), as a grid or
rows (``_apply_list_layout``); what a row is called and how it is tinted
(``channel_display_name``, ``item_tint``); the catalogue as the lists show
it - hidden, locked, a kids profile's (``_visible_catalog``,
``_channel_hidden``); the filter (``_apply_filter``) and the status row's
count (``_count_text``); a row found and selected (``_try_select_key``,
``_reveal_item_in_list``).
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QAbstractItemView, QListWidgetItem

from ..core.log import log
from ..core.stores import catalogue_cats, kept_place, lock_hides
from ..core.workers import run_async
from ..i18n import tr


# Sentinel for "no pending category to reselect" - distinct from None, which
# is a real category id (the "All" row).
_UNSET = object()

#: The source's catalogue not read for the parental lock's held-back rows
#: (_lock_learn): asked again no sooner than this.
LOCK_RETRY_S = 60.0


# The status row's "123 channels", per kind of list.
_COUNT_KEYS = {
    "live": "count_channels", "vod": "count_movies",
    "series": "count_series", "episode": "count_episodes",
    "fav": "count_favorites", "history": "count_history",
    "rec": "rec_n_recordings", "watchlist": "count_watchlist",
    "watched": "count_watched", "local": "count_files",
    "radio": "count_stations",
}


class _ListsMixin:

    # -- modes and categories ------------------------------------------------------

    def switch_mode(self, mode: str) -> None:
        # Local files and Recordings are never a kids profile's, by any way
        # in (a key, the palette, a menu): mw_profiles.KIDS_NEVER.
        if mode in ("local", "rec") and getattr(self, "_profile_kids", lambda: False)():
            mode = "live"
        # Any mode switch shows the three-column window (in TV mode: where
        # leaving it lands - TV mode holds the window).
        if getattr(self, "_center_stack", None) is not None and not self._tv_land_on(0):
            self._center_stack.setCurrentIndex(0)
        # ... and from the Guide page to the list - unless the guide itself
        # tunes a channel: tune_from_guide's jump in the list runs under it.
        if not getattr(self, "_guide_hold", False):
            self._close_guide_page()
        self._close_series_page()       # a series' page gives way to the list
        getattr(self, "_close_mitt_page", lambda: None)()    # ...and Mine's page
        # ...and the Search page, unless the window moves on by itself (a
        # source switched to from it has no list of the mode shown).
        if not getattr(self, "_search_hold", False):
            self._close_search_page()
        # Watched is History's second filter: History is the entry lit.
        self._check_nav("guide" if self._guide_page_showing()
                        else "search" if self._search_page_showing()
                        else self._nav_key_for_mode(mode))
        self.mode = mode
        self.series_ctx = None
        self.back_btn.hide()
        self.clear_history_btn.setVisible(mode == "history")
        if mode in ("history", "watched"):
            self._history_filter = "watched" if mode == "watched" else "all"
        self._sync_history_chips()
        self._refresh_nav_status()
        if hasattr(self, "local_view_btn"):
            self.local_view_btn.setVisible(mode == "local")
            self.local_rescan_btn.setVisible(mode == "local")
            self._sync_local_view_btn()
        # Ctrl/Shift-click gathers rows wherever an action can take several
        # at once; in Favorites they can also be dragged to a folder.
        self.listw.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
            if mode in ("history", "rec", "live", "vod", "series", "fav")
            else QAbstractItemView.SelectionMode.SingleSelection)
        self.listw.set_fav_drag(mode == "fav")
        self.search.clear()
        self._sync_search_hint()
        self._load_categories()
        # Series is a place of the unified look where the list was, and so
        # are Local files (mw_place_local; what it shows watched on disk),
        # Radio (mw_place_radio) and Recordings (mw_place_rec).
        self._place_series_sync()
        self._place_local_sync()
        self._place_radio_sync()
        self._place_rec_sync()
        # The TV place's header over the live list only (mw_place_tv).
        sync = getattr(self, "_place_tv_sync", None)
        if sync is not None:
            sync()
        self._update_sync_btn()
        # Its place is the one shown now (mw_places) - under a page that
        # stays over it, the place it goes back to.
        self._mode_place(mode)
        # Opening Watched is a good moment to pull the latest Trakt
        # history (respects the 1 h TTL, so it's a no-op if just synced).
        if mode == "watched":
            self._maybe_sync_watched()

    def _refresh_mode_availability(self) -> None:
        """Hide a Browse mode (TV/Movies/Series) the active provider has no
        content for, so a live-only (or VOD-only) provider doesn't show empty
        sections. TV/Movies/Series are the Xtream API's three fixed types, so we
        probe each type's category list in the background; an empty list hides
        that mode. Fail-open: a probe that errors (or an all-empty result) leaves
        every mode shown, so a transient hiccup or an uncategorised provider
        never blanks a working section. Purely nav visibility - no effect on
        playback or the mpv/VLC player."""
        if not self.client:
            return
        self._avail_gen += 1
        gen = self._avail_gen
        self._mode_avail = {}
        # Start from all-visible so a mode hidden for the previous provider
        # doesn't linger while the new provider's probes are in flight (an
        # entry the person took out of the sidebar stays out: mw_place_look).
        self._nav_avail = {}
        self._apply_nav_visibility()
        fns = {"live": getattr(self.client, "live_categories", None),
               "vod": getattr(self.client, "vod_categories", None),
               "series": getattr(self.client, "series_categories", None)}
        for key, fn in fns.items():
            if fn is None:
                continue   # client doesn't offer this type -> leave it shown
            def done(cats, key=key, gen=gen):
                if gen != self._avail_gen:
                    return   # a newer provider/refresh superseded this probe
                self._mode_avail[key] = bool(cats)
                self._apply_mode_visibility()
            run_async(self.pool, fn, done, lambda _e: None)

    def _apply_mode_visibility(self) -> None:
        modes = ("live", "vod", "series")
        known = {m: self._mode_avail.get(m) for m in modes}
        # Fail open until we know at least one mode has content, so an all-empty
        # result (e.g. an auth glitch) never hides the whole Browse group.
        if not any(v is True for v in known.values()):
            self._nav_avail = {}
            self._apply_nav_visibility()
            return
        # Hide only on a definite empty (False); unknown (None) stays. The
        # guide is TV's: a provider without channels has nothing in it.
        # (Its shortcut and menu entries still open it.)
        self._nav_avail = {m: known[m] is not False for m in modes}
        self._nav_avail["guide"] = known["live"] is not False
        self._apply_nav_visibility()
        # If the mode you're on just got hidden, move to the first visible one
        # (under the Search page or the Guide, which stay: the window moved,
        # not you - and the place shown stays theirs).
        if self.mode in modes and known[self.mode] is False:
            for m in modes:
                if known[m] is not False:
                    held = getattr(self, "_guide_hold", False)
                    self._search_hold = True
                    self._guide_hold = True
                    try:
                        self.switch_mode(m)
                    finally:
                        self._search_hold = False
                        self._guide_hold = held
                    break

    def _remembered_cat_row(self, default_row: int = 0) -> int:
        """The row holding the sub-category last visited in this section, or
        *default_row* when there is none (first visit) or it is gone (the
        provider dropped the category, a favourites group was renamed, a
        recordings folder was deleted). Ids come back from the provider as
        int or str interchangeably, so compare as strings as a fallback."""
        want = self._last_cat.get(self.mode, _UNSET)
        if want is _UNSET:
            return default_row
        for i in range(self.cat_list.count()):
            d = self.cat_list.item(i).data(Qt.ItemDataRole.UserRole)
            if d == want or (d is not None and want is not None
                             and not isinstance(d, tuple)
                             and str(d) == str(want)):
                return i
        return default_row

    def _select_remembered_cat(self, default_row: int = 0) -> None:
        self.cat_list.setCurrentRow(self._remembered_cat_row(default_row))

    def _load_categories(self) -> None:
        self._load_gen += 1
        gen = self._load_gen
        # The Search page on screen follows the lists: the lock shut (Lock
        # now), a category hidden or locked - found again at once.
        self._search_rules_check()
        # Reset the category search: it only applies to the provider sections
        # that actually have categories (live/movies/series). Collapse the box
        # back to just its 🔍 toggle on every section switch.
        if hasattr(self, "cat_search"):
            cat_mode = self.mode in ("live", "vod", "series")
            # Category search where categories exist; a plain list filter in the
            # folder/list sections (Favorites, Watch Later, Watched, Recordings,
            # History, Local files) so the same 🔍 works everywhere, adapted
            # to each.
            show = cat_mode or self.mode in (
                "fav", "watchlist", "watched", "rec", "history", "local")
            self._cat_search_supported = show
            self._sync_cat_search_hint()
            self.cat_search.blockSignals(True)
            self.cat_search.clear()
            self.cat_search.blockSignals(False)
            self.cat_search.hide()
            self.cat_search_btn.blockSignals(True)
            self.cat_search_btn.setChecked(False)
            self.cat_search_btn.blockSignals(False)
            # Not on the collapsed icon rail (see _apply_sidebar_chrome).
            self.cat_search_btn.setVisible(
                show and not getattr(self, "_sidebar_collapsed", False))
        # A Local files search still walking a folder: not what is wanted now.
        self._local_cancel_walk()
        self.cat_list.clear()
        self.list_model.set_items([], self.mode)
        if self.mode == "local":
            self._load_local_categories()
            return
        if self.mode == "radio":
            self._load_radio_categories()
            return
        if self.mode == "rec":
            self.cat_list.blockSignals(True)
            for label, data in [(tr("rec_all_recordings"), None),
                                (tr("rec_active_scheduled"), "__jobs__"),
                                (tr("rec_upcoming"), "__upcoming__")]:
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, data)
                self.cat_list.addItem(item)
            for rel in self.rec.folders():
                item = QListWidgetItem(rel)
                item.setData(Qt.ItemDataRole.UserRole, rel)
                self.cat_list.addItem(item)
            self.cat_list.blockSignals(False)
            self._select_remembered_cat()
            return
        if self.mode == "history":
            self.cat_list.blockSignals(True)
            for label, data in [(tr("cat_all"), None),
                                (tr("fav_channels"), "live"),
                                (tr("nav_movies"), "movie"),
                                (tr("nav_series"), "series"),
                                (tr("nav_local"), "local"),
                                (tr("nav_radio"), "radio")]:
                it = QListWidgetItem(label)
                it.setData(Qt.ItemDataRole.UserRole, data)
                self.cat_list.addItem(it)
            self.cat_list.blockSignals(False)
            self._select_remembered_cat()
            return
        if self.mode == "fav":
            # Split column: a Channels section (with its user-defined
            # groups + parental lock nested underneath), then flat
            # Movies and Series sections. The row data is a
            # (section, group) tuple - group is only meaningful for
            # channels.
            self.cat_list.blockSignals(True)
            all_row = QListWidgetItem(tr("cat_all"))
            all_row.setData(Qt.ItemDataRole.UserRole, ("all", None))
            self.cat_list.addItem(all_row)

            def add_fav_section(section: str, label: str, store) -> None:
                # A section header row (shows everything in the section) with
                # the user's folders nested underneath. Locks are a
                # channels-only feature; folders in movies/series just carry a
                # name.
                head = QListWidgetItem(label)
                head.setData(Qt.ItemDataRole.UserRole, (section, None))
                self.cat_list.addItem(head)
                for g in store.custom_groups():
                    locked = (section == "chan" and store.is_locked(g)
                              and not self.parental.session_unlocked)
                    if locked and kids:
                        continue        # a kids profile: not there at all
                    text = (f"    {g}  " + tr("cat_locked_suffix")
                            if locked else f"    {g}")
                    it = QListWidgetItem(text)
                    it.setData(Qt.ItemDataRole.UserRole, (section, g))
                    self.cat_list.addItem(it)

            kids = self._profile_kids()
            add_fav_section("chan", tr("fav_channels"), self.favs)
            add_fav_section("movie", tr("fav_movies"), self.movie_favs)
            add_fav_section("series", tr("fav_series"), self.series_favs)
            # Stations and web streams only once there are some.
            if self.radio_favs.items():
                add_fav_section("radio", tr("fav_radio"), self.radio_favs)
            if self.web_favs.items():
                add_fav_section("web", tr("fav_web"), self.web_favs)
            if self.trakt.is_connected():
                trakt_row = QListWidgetItem(tr("watched_trakt"))
                trakt_row.setData(Qt.ItemDataRole.UserRole, ("trakt", None))
                self.cat_list.addItem(trakt_row)
            self.cat_list.blockSignals(False)
            self._select_remembered_cat()
            return
        if self.mode == "watchlist":
            # Watch Later has two sub-categories - Movies and Series -
            # mirroring the two Trakt watchlist endpoints. 'All' up
            # top shows both stacked, movies first.
            self.cat_list.blockSignals(True)
            for label, data in [
                    (tr("cat_all"), None),
                    (tr("nav_movies"), "movies"),
                    (tr("nav_series"), "series")]:
                it = QListWidgetItem(label)
                it.setData(Qt.ItemDataRole.UserRole, data)
                self.cat_list.addItem(it)
            self.cat_list.blockSignals(False)
            self._select_remembered_cat()
            return
        if self.mode == "watched":
            # Split into Local and Trakt. The Trakt row (and the
            # combined 'All') only appear when connected - a user with
            # no Trakt account just sees their local list.
            self.cat_list.blockSignals(True)
            # Trakt, Simkl or both (mw_trakt): one row for what they say.
            accounts = self._accounts_name()
            connected = bool(accounts)
            rows = []
            if connected:
                rows.append((tr("cat_all"), None))
            rows.append((tr("watched_local"), "local"))
            if connected:
                rows.append((tr("watched_trakt") if accounts == "Trakt"
                             else accounts, "trakt"))
            for label, data in rows:
                it = QListWidgetItem(label)
                it.setData(Qt.ItemDataRole.UserRole, data)
                self.cat_list.addItem(it)
            self.cat_list.blockSignals(False)
            self._select_remembered_cat()
            return
        self._show_busy(tr("status_loading_categories"))
        fn = {"live": self.client.live_categories,
              "vod": self.client.vod_categories,
              "series": self.client.series_categories}[self.mode]
        request_mode = self.mode

        def done(cats):
            if gen != self._load_gen or self.mode != request_mode:
                return
            self._hide_busy()
            # Only rows: a source that answers with something else (a
            # refused login's object) must not take the list down with it.
            self._raw_categories = [c for c in cats or [] if isinstance(c, dict)]
            if hasattr(self, "_kids_note_cats"):
                self._kids_note_cats(request_mode, self._raw_categories)
            if request_mode == "live":
                self._ms_live_note(self._raw_categories)
            self._search_index_cache.pop(request_mode, None)  # rebuild on search
            self.cat_list.blockSignals(True)
            self.cat_list.clear()
            all_item = QListWidgetItem(tr("cat_all"))
            all_item.setData(Qt.ItemDataRole.UserRole, None)
            self.cat_list.addItem(all_item)
            # A synthetic "Continue watching" category, shown when there are
            # partly-watched titles of this section's kind (movies under
            # Movies, episodes under Series).
            cont_kind = {"vod": "vod", "series": "episode"}.get(self.mode)
            if cont_kind and self._continue_items(cont_kind):
                cw = QListWidgetItem("▶  " + tr("cat_continue"))
                cw.setData(Qt.ItemDataRole.UserRole, "__continue__")
                self.cat_list.addItem(cw)
            # "Recently added" for TV, Movies and Series - the latest the
            # provider has published, newest first.
            if self.mode in ("vod", "series", "live"):
                rc = QListWidgetItem("🆕  " + tr("cat_recent"))
                rc.setData(Qt.ItemDataRole.UserRole, "__recent__")
                self.cat_list.addItem(rc)
            # The series followed (mw_follow), with their news first.
            if self.mode == "series" and self.followed.ids():
                fw = QListWidgetItem("🔔  " + tr("cat_following"))
                fw.setData(Qt.ItemDataRole.UserRole, "__followed__")
                self.cat_list.addItem(fw)
            # A kids profile sees only what the parental lock leaves open,
            # and is never asked for the PIN of the rest (mw_profiles).
            kids = self._profile_kids()
            from ..core.profiles import adult_category
            cat_ok = getattr(self, "_kids_cat_ok", None)
            for c in self._raw_categories:
                cid = c.get("category_id")
                if self.overrides.is_hidden(self.mode, cid):
                    continue
                name = self.overrides.display_name(
                    self.mode, cid, c.get("category_name", "?"))
                # Nothing for adults - and with "Only what a grown-up
                # allows", only what one chose (mw_profiles).
                if kids and (adult_category(name, c.get("category_name"))
                             or (callable(cat_ok)
                                 and not cat_ok(self.mode, cid, name, c.get("category_name")))):
                    continue
                if (self.overrides.is_locked(self.mode, cid)
                        and not self.parental.session_unlocked):
                    if kids:
                        continue
                    name += "  " + tr("cat_locked_suffix")
                ovr = self.overrides.get(self.mode, cid)
                icon = ovr.get("icon", "")
                if icon:
                    name = f"{icon}  {name}"
                it = QListWidgetItem(name)
                it.setData(Qt.ItemDataRole.UserRole, cid)
                color = ovr.get("color", "")
                if color:
                    it.setForeground(QColor(color))
                bgcolor = ovr.get("bgcolor", "")
                if bgcolor:
                    it.setBackground(QColor(bgcolor))
                self.cat_list.addItem(it)
            self.cat_list.blockSignals(False)
            # Normally land on the first real category, but if a reload was
            # asked to keep the current one (e.g. after Manage categories),
            # reselect that category so the list doesn't jump to the top.
            keep = self._pending_cat_select
            self._pending_cat_select = _UNSET
            # Land on the first *real* category, skipping the synthetic ones
            # ("All" is row 0, then Recently added / Continue watching): those
            # aren't where a user expects to arrive after switching a mode, and
            # landing on "Recently added" for TV looked like the app forgot the
            # previous category.
            row = 0
            for i in range(self.cat_list.count()):
                d = self.cat_list.item(i).data(Qt.ItemDataRole.UserRole)
                if d is None or (isinstance(d, str) and d.startswith("__")):
                    continue
                row = i
                break
            # ...unless this section has been visited before, in which case
            # come back to the category that was open. The explicit overrides
            # below (a jump to what's playing, a reload asked to keep the
            # current category, a series drill-in) still win over it.
            row = self._remembered_cat_row(row)
            # A pending "jump to now playing" wants every item visible, so land
            # on the "All" row (0) rather than the first category - unless we
            # know the target's category (e.g. tuning from the EPG guide), in
            # which case land there so the sidebar reflects where the channel
            # lives (it still contains the channel, so the jump selects it).
            if getattr(self, "_pending_jump_key", None) is not None:
                row = 0
                cat = getattr(self, "_pending_jump_cat", None)
                if cat is not None:
                    for i in range(self.cat_list.count()):
                        d = self.cat_list.item(i).data(
                            Qt.ItemDataRole.UserRole)
                        # str() both sides: int vs str category ids.
                        if d is not None and str(d) == str(cat):
                            row = i
                            break
                    self._pending_jump_cat = None
            if keep is not _UNSET:
                for i in range(self.cat_list.count()):
                    if self.cat_list.item(i).data(
                            Qt.ItemDataRole.UserRole) == keep:
                        row = i
                        break
            self.cat_list.setCurrentRow(row)

        def fail(msg):
            if gen != self._load_gen:
                return
            self._error(msg)

        run_async(self.pool, fn, done, fail)

    def _category_changed(self, cur, _prev=None) -> None:
        if not cur:
            return
        # When: a double click right after it is the one that picked it
        # (mw_guide's _guide_category_double_clicked).
        self._cat_changed_at = time.monotonic()
        cat = cur.data(Qt.ItemDataRole.UserRole)
        if self.mode == "local" and cat == "__add__":
            # The "+ Add folder" row is a button, not a category: open the
            # picker (it rebuilds the list and selects the new folder).
            self._local_add_folder()
            return
        locked = False
        if cat is not None:
            if self.mode == "fav":
                # cat is a (section, group) tuple; only a named channel
                # group can be parental-locked.
                section, group = cat
                locked = (section == "chan" and group is not None
                          and self.favs.is_locked(group))
            elif self.mode in ("live", "vod", "series"):
                locked = self.overrides.is_locked(self.mode, cat)
        if locked and not self.parental.session_unlocked:
            if not self._request_unlock():
                self.cat_list.blockSignals(True)
                self.cat_list.setCurrentRow(0)
                self.cat_list.blockSignals(False)
                # What row 0 holds: in Favorites that is "All", and None
                # there loaded the channels only, under an "All" highlight.
                first = self.cat_list.item(0)
                self._load_items(first.data(Qt.ItemDataRole.UserRole)
                                 if first else None)
                return
            self._load_categories()
            return
        if self.series_ctx:
            log.info("category changed while inside a series - leaving the "
                     "episode list (cat=%r)", cat)
        if self._series_page_showing() and self.cat_list.hasFocus():
            # Arrowed through the sidebar's categories: their list, in the
            # series page's place (a click does the same, _sp_category_clicked).
            self._sp_category_clicked()
        if self._search_page_showing() and self.cat_list.hasFocus():
            self._search_category_clicked()
        self.series_ctx = None
        self.back_btn.hide()
        self._update_sync_btn()
        # Remember where we are in this section, so coming back lands here
        # (see _remembered_cat_row).
        self._last_cat[self.mode] = cat
        self._load_items(cat)
        # In "solo" mode keep only the now-active category visible in the list.
        self._apply_cat_solo()

    def _is_combined_view(self, category_id) -> bool:
        """The combined views that stack several kinds together: grouped under
        headers in list mode, or a flat poster wall in grid mode. They must be
        rebuilt (not just re-filtered) when the grid setting changes."""
        if self.mode == "fav":
            return category_id in (("all", None), ("trakt", None))
        if self.mode == "watchlist":
            return category_id is None
        if self.mode == "watched":
            return True
        if self.mode == "history":
            return category_id is None
        if self.mode == "rec":
            return category_id not in ("__jobs__", "__upcoming__")
        return False

    def _load_items(self, category_id) -> None:
        # Apply the right layout up front and deterministically: grouped
        # overviews are always a headed list, every other view honours the
        # user's grid/list choice. Doing it here (not via a sticky flag) fixes
        # the grid setting being "forgotten" when hopping between categories.
        self._current_cat = category_id
        self._sync_sort_box()            # show THIS category's sort order
        self._apply_list_layout(False)   # honour the user's grid/list choice
        if self.mode == "radio":
            self._load_radio_items(category_id)
            return
        if self.mode == "local":
            # Coming back from another section lands where the user left
            # off - the folder they were browsing, or the series they had
            # open. Only a genuinely new category selection starts at the
            # root of it.
            if self._local_restore_place():
                return
            self._local_ctx = None
            self._local_series = None
            self._local_nav_stack = []
            self.back_btn.hide()
            self._load_local_items(category_id)
            self._local_remember_place()
            return
        if self.mode == "rec":
            if category_id == "__jobs__":
                self.all_items = [self._job_item(j)
                                  for j in reversed(self.rec.jobs)]
                self._apply_filter()
                return
            if category_id == "__upcoming__":
                self.all_items = [
                    self._job_item(j) for j in reversed(self.rec.jobs)
                    if j["status"] == "scheduled"]
                self._apply_filter()
                return
            self._load_recordings_grouped(
                self.rec.files(category_id), show_pending=category_id is None)
            return
        if self.mode == "fav":
            # category_id is a (section, group) tuple (or None as a
            # fallback, meaning all channels).
            section, group = category_id if category_id else ("chan", None)
            self._fav_section = section
            # A folder's colour cascades to all its favourites: resolve it once
            # here (all items in a selected folder share it) for item_tint.
            fstore = {"chan": self.favs, "movie": self.movie_favs,
                      "series": self.series_favs, "radio": self.radio_favs,
                      "web": self.web_favs}.get(section)
            gc = fstore.group_color(group) if (fstore and group) else {}
            self._fav_view_tint = (gc.get("color", "") or "",
                                   gc.get("bgcolor", "") or "")
            if section == "all":
                self._load_favorites_all()
                return
            if section == "movie":
                self.all_items = self.movie_favs.items(group)
            elif section == "radio":
                self.all_items = self._radio_fav_rows(group)
            elif section == "web":
                self.all_items = self._web_fav_rows(group)
            elif section == "series":
                self.all_items = self.series_favs.items(group)
            elif section == "trakt":
                # Favourites pulled from the Trakt 'dopeIPTV Favorites'
                # list - fetched over the network, so show an empty list
                # now and fill it in asynchronously.
                self.all_items = []
                self._apply_filter()
                self._load_trakt_favorites()
                return
            else:
                exclude = (() if self.parental.session_unlocked
                           else self.favs.locked_groups())
                self.all_items = self.favs.items(group, exclude_groups=exclude)
            self._apply_filter()
            return
        if self.mode == "history":
            self._history_subcat = category_id  # None / live / movie / series
            items = self._lock_filter(self.history.items())
            if category_id is not None:
                kinds = self._HISTORY_KINDS.get(category_id, set())
                self.all_items = [it for it in items
                                  if it.get("_kind") in kinds]
                self._apply_filter()
                return
            # 'All' - grouped by kind. Keep each row's original _kind for
            # playback/scrobble; only tag _ekind so the delegate paints the
            # right artwork (logo vs poster) per row.
            def sect(cat):
                ks = self._HISTORY_KINDS[cat]
                return self._sorted(self._search_filter(
                    [it for it in items if it.get("_kind") in ks]))
            grouped: list[dict] = []
            for hk, ek, rows in (
                    ("fav_channels", "fav", sect("live")),
                    ("fav_movies", "vod", sect("movie")),
                    ("fav_series", "series", sect("series")),
                    ("nav_local", "vod", sect("local")),
                    ("nav_radio", "radio", sect("radio"))):
                if rows:
                    grouped.append({"_header": tr(hk)})
                    grouped += [{**r, "_ekind": ek} for r in rows]
            self._render_rows(grouped, "history")
            return
        if self.mode == "watchlist":
            self._watchlist_subcat = category_id
            movies = [{**m, "_kind": "vod"} for m in self.watchlist.movies]
            shows = [{**s, "_kind": "series"} for s in self.watchlist.shows]
            if category_id == "movies":
                self.all_items = movies
                self._apply_filter()
                return
            if category_id == "series":
                self.all_items = shows
                self._apply_filter()
                return
            # 'All' - Movies and Series stacked under headers.
            self._show_grouped(
                [("fav_movies", "vod", "vod", self._search_filter(movies)),
                 ("fav_series", "series", "series", self._search_filter(shows))],
                "watchlist")
            return
        if self.mode == "watched":
            self._watched_subcat = category_id
            local = self.watched.local_watched_items()
            if category_id == "local":
                items = local
            elif category_id == "trakt":
                items = self._trakt_watched_items()
            else:
                items = self._merge_watched(
                    local, self._trakt_watched_items())
            movies = self._search_filter(
                [it for it in items
                 if it.get("_kind") not in ("series", "episode")])
            series = self._search_filter(
                [it for it in items
                 if it.get("_kind") in ("series", "episode")])
            self._show_grouped(
                [("fav_movies", "vod", "vod", movies),
                 ("fav_series", "series", "series", series)],
                "watched")
            return
        if category_id == "__continue__" and self.mode in ("vod", "series"):
            # Synthetic category: partly-watched titles of this section's kind
            # (movies under Movies, episodes under Series), from the resume
            # store (no network fetch).
            kind = "vod" if self.mode == "vod" else "episode"
            self.all_items = self._continue_items(kind)
            self._apply_filter()
            return
        if category_id == "__followed__" and self.mode == "series":
            # Synthetic category: the series followed, the ones with new
            # episodes first - from the follow store's snapshots (no network
            # fetch); what is hidden, locked or not for this profile left out.
            store = self.followed
            keep = self._catalog_filter("series")
            rows = [store.snapshot(sid) for sid in store.ids()]
            rows = [r for r in rows if r.get("series_id") is not None and keep(r)]
            rows.sort(key=lambda r: not store.new_ids(r.get("series_id")))
            self.all_items = rows
            self._apply_filter()
            return
        if category_id == "__recent__" and self.mode in ("vod", "series",
                                                          "live"):
            # Synthetic category: every title sorted by the provider's
            # publish/update time, newest first (capped so a huge library
            # doesn't lag the list).
            rmode = self.mode
            rgen = self._load_gen
            skey = "last_modified" if rmode == "series" else "added"
            rfetch = {"vod": self.client.vod_streams,
                      "series": self.client.series_list,
                      "live": self.client.live_streams}[rmode]
            # Short cache: re-opening "Recently added" refetched the entire
            # library every time (slow on a big provider). Serve a recent
            # result straight away instead.
            cache = getattr(self, "_recent_cache", {}).get(rmode)
            if cache and time.time() - cache[0] < 300:
                self.all_items = cache[1]
                self._apply_filter()
                return
            self._show_busy(tr("status_loading_recent"))

            def recent_done(items):
                if rgen != self._load_gen or self.mode != rmode:
                    return
                self._hide_busy()
                items = items or []
                excluded = self.overrides.excluded_ids(
                    rmode, include_locked=not self.parental.session_unlocked)
                if excluded:
                    items = [it for it in items
                             if str(it.get("category_id")) not in excluded]

                def _added(it):
                    try:
                        return int(it.get(skey) or 0)
                    except (TypeError, ValueError):
                        return 0
                self.all_items = sorted(
                    items, key=_added, reverse=True)[:200]
                if not hasattr(self, "_recent_cache"):
                    self._recent_cache = {}
                self._recent_cache[rmode] = (time.time(), self.all_items)
                self._apply_filter()

            def recent_fail(msg):
                if rgen != self._load_gen:
                    return
                self._hide_busy()
                self._error(msg)

            run_async(self.pool, lambda: rfetch(None),
                      recent_done, recent_fail)
            return
        self._show_busy(self._loading_message())
        fn = {"live": self.client.live_streams,
              "vod": self.client.vod_streams,
              "series": self.client.series_list}[self.mode]
        mode = self.mode
        gen = self._load_gen

        def done(items):
            if gen != self._load_gen or self.mode != mode:
                return
            self._hide_busy()
            items = items or []
            if category_id is None:
                excluded = self.overrides.excluded_ids(
                    mode,
                    include_locked=not self.parental.session_unlocked)
                if excluded:
                    items = [it for it in items
                             if str(it.get("category_id")) not in excluded]
            self.all_items = items
            self._apply_filter()
            if self.mode == "live":
                self._ensure_xmltv_loaded()

        def fail(msg):
            if gen != self._load_gen:
                return
            self._error(msg)

        run_async(self.pool, lambda: fn(category_id), done, fail)

    # -- grouped, headed "All / combined" views ------------------------------

    def _search_filter(self, items: list) -> list:
        """Filter items by the current search text on their display name."""
        text = self.search.text().lower().strip()
        if not text:
            return items
        name = self._display_name_fn()
        return [it for it in items if text in name(it).lower()]

    def _grouped(self, sections: list) -> list:
        """Build a headed list from ordered (header_key, ekind, kind, items)
        sections, tagging each row with _ekind (delegate art/badges) and _kind
        (playback routing). Empty sections are skipped. Shared by the combined
        Favorites / Watch Later / Watched / History views."""
        out: list[dict] = []
        for header_key, ekind, kind, items in sections:
            if items:
                out.append({"_header": tr(header_key)})
                out += [{**it, "_ekind": ekind, "_kind": kind}
                        for it in self._sorted(items)]
        return out

    def _grid_on(self) -> bool:
        return self.settings.value("view_grid", "false") == "true"

    def _render_rows(self, rows: list, model_kind: str,
                     empty_msg: str | None = None) -> None:
        """Populate the model from a headed row list.

        In list mode the section headers are kept as full-width rows. In grid
        mode they are dropped: Qt's icon grid gives every cell a uniform size,
        so a header can't cleanly span a row without either wrecking the poster
        column alignment or leaving a tall, ragged gap. Grid mode is therefore
        a clean, uniform poster wall (sections stay grouped in order, just
        without the labels); the section labels live in list mode."""
        n = sum(1 for r in rows if not r.get("_header"))
        # (Recordings keep theirs: the place shows them over its grid too.)
        if self._grid_on() and model_kind != "rec":
            rows = [r for r in rows if not r.get("_header")]
        self.all_items = rows
        self.list_model.set_items(rows, model_kind)
        self._set_list_status(self._count_text(model_kind, n) if n
                              else (empty_msg or self._count_text(model_kind, 0)))
        if getattr(self, "_pending_jump_key", None) is not None:
            # A jump to what plays (Recordings is built here, not through
            # _apply_filter): its row picked once it is in.
            if self._try_select_key(self._pending_jump_key):
                self._pending_jump_key = None
                return
        # On the very first populated list, select the first playable row (not a
        # section header) so the detail pane shows something straight away -
        # selecting only, never auto-playing.
        if n and not getattr(self, "_did_initial_select", False):
            for row in range(self.list_model.rowCount()):
                item = self.list_model.item_at(row)
                if item and not item.get("_header"):
                    self._did_initial_select = True
                    self.listw.setCurrentIndex(self.list_model.index(row))
                    break

    def _show_grouped(self, sections: list, model_kind: str,
                      empty_msg: str | None = None) -> None:
        """Render a combined view from ordered sections - grouped under
        headers in list mode, a flat poster wall in grid mode."""
        self._render_rows(self._grouped(sections), model_kind, empty_msg)

    def _load_recordings_grouped(self, files: list,
                                 show_pending: bool = False) -> None:
        """Recordings in the order picked (core/rec_view.arrange): the
        newest first by default, under their days - today, yesterday,
        earlier this week, last week, then by month -, else the oldest
        first, by name, under their channels, the longest or the largest
        first; Unwatched only leaves out what this profile watched. Rows
        keep their recording nature (no _ekind/_kind retag) so they still
        play via the rec path."""
        import datetime
        from ..core import rec_view
        marks = self._rec_watched()
        groups = rec_view.arrange(
            self._search_filter(files), rec_view.order_of(self.settings),
            datetime.datetime.now(), self._mitt_week_start(), length=self._rec_length,
            watched=marks.has, only_unwatched=rec_view.unwatched_only(self.settings))
        grouped: list[dict] = []
        # Surface the pending recordings (recording now + scheduled) at the very
        # top of the default "All recordings" view, soonest first, so upcoming
        # recordings are visible and manageable (right-click → edit / cancel)
        # the moment the Recordings section opens - not hidden behind a
        # sub-category. Only on the All view; a specific folder shows its files.
        if show_pending:
            pending = sorted(
                (j for j in self.rec.jobs
                 if j["status"] in ("recording", "scheduled")),
                key=lambda j: j.get("start") or 0)
            if pending:
                grouped.append({"_header": tr("rec_upcoming"), "_pending": True})
                grouped += [self._job_item(j) for j in pending]
        for key, rows in groups:
            if key is not None:
                grouped.append({"_header": self._rec_group_title(key)})
            grouped += rows
        self._render_rows(grouped, "rec")

    def _load_favorites_all(self) -> None:
        """Show every favorite at once - channels, movies and series - grouped
        under section headers, so opening Favorites shows all three kinds
        immediately instead of just Channels."""
        exclude = (() if self.parental.session_unlocked
                   else self.favs.locked_groups())
        chans = self._search_filter(
            self.favs.items(None, exclude_groups=exclude))
        movies = self._search_filter(self.movie_favs.items())
        series = self._search_filter(self.series_favs.items())
        radio = self._search_filter(self._radio_fav_rows())
        web = self._search_filter(self._web_fav_rows())
        self._show_grouped(
            [("fav_channels", "fav", "live", chans),
             ("fav_movies", "vod", "vod", movies),
             ("fav_series", "series", "series", series),
             ("fav_radio", "radio", "radio", radio),
             ("fav_web", "web", "web", web)],
            "fav", tr("fav_empty_all"))

    def _apply_list_layout(self, _force_list: bool = False) -> None:
        """Set the middle pane layout from the user's grid/list choice: a plain
        top-to-bottom list, or a uniform, justified poster grid. Combined views
        (favorites, watched, history, ...) use the very same grid - they just
        drop their section headers there (see _render_rows)."""
        from PyQt6.QtWidgets import QListView
        grid = self._grid_on()
        self.delegate.set_grid(grid)
        if not grid:
            self.listw.setViewMode(QListView.ViewMode.ListMode)
            self.listw.setFlow(QListView.Flow.TopToBottom)
            self.listw.setWrapping(False)
            self.listw.set_grid_cell(None)
            self.listw.setGridSize(QSize())
        else:
            self.listw.setViewMode(QListView.ViewMode.IconMode)
            self.listw.setFlow(QListView.Flow.LeftToRight)
            self.listw.setWrapping(True)
            self.listw.setResizeMode(QListView.ResizeMode.Adjust)
            self.listw.set_grid_cell(self.delegate.grid_size())
        # The TV place's header follows a grid or rows (mw_place_tv).
        sync = getattr(self, "_place_tv_sync", None)
        if sync is not None:
            sync()

    # -- list and filtering --------------------------------------------------------

    def _count_text(self, kind: str, n: int) -> str:
        """"123 channels" for the status row, for each kind of list."""
        if kind == "rec" and getattr(self, "mode", "") == "local":
            kind = "local"          # Local files' rows ride the recordings' kind
        key = _COUNT_KEYS.get(kind)
        return tr(key, n=n) if key else str(n)

    # History left-category -> the stored _kind values it covers, for the
    # grouped view and the per-category delete.
    _HISTORY_KINDS = {
        "live": {"live"},
        "movie": {"movie", "vod"},
        "series": {"series", "episode"},
        "local": {"local"},
        "radio": {"radio"},
    }

    def _display_name_fn(self):
        """channel_display_name for the rows of the current list, as one
        function: the plain name when nothing here is renamed (most lists),
        so a search goes through 80,000 rows without a lookup for each."""
        mode = "episode" if self.series_ctx else self.mode
        if (mode in ("live", "vod", "series", "fav")
                and self.channel_ov.anything_renamed(
                    "live" if mode == "fav" else mode)):
            return self.channel_display_name
        return lambda it: it.get("name") or it.get("title") or "?"

    def channel_display_name(self, it) -> str:
        base = it.get("name") or it.get("title") or "?"
        mode = "episode" if self.series_ctx else self.mode
        if mode in ("live", "vod", "series", "fav"):
            key = self._item_key(it)
            if key is not None:
                ov_mode = "live" if mode == "fav" else mode
                return self.channel_ov.display_name(ov_mode, key, base)
        return base

    def item_tint(self, it, kind: str):
        """(text, background) hex colours for a list item. A colour set on a
        category is NOT inherited by its items - that only tints the category
        row itself. Items are tinted only by their own per-item colour, and in
        a favourites folder by the folder's colour."""
        if not it:
            return "", ""
        if self.mode == "fav":
            return getattr(self, "_fav_view_tint", ("", ""))
        mode = {"live": "live", "vod": "vod", "series": "series"}.get(kind)
        if mode is None:
            return "", ""
        key = self._item_key(it)
        if key is not None:
            iov = self.channel_ov.get(mode, key)
            return iov.get("color", "") or "", iov.get("bgcolor", "") or ""
        return "", ""

    def _try_select_key(self, key) -> bool:
        """Select + scroll to the row with this item key, if it's in the
        current list. The playing-item jump and the back-from-episodes landing
        (which targets the SERIES row, not what's playing) share this."""
        if key is None:
            return False
        for row in range(self.list_model.rowCount()):
            item = self.list_model.item_at(row)
            # str() both sides: providers mix int and str ids, and a key that
            # passed through JSON storage (resume/history) may come back as a
            # string while the freshly fetched list carries ints - an == on
            # raw values then never matches and the row is never selected.
            if (item and not item.get("_header")
                    and self._item_key(item) is not None
                    and str(self._item_key(item)) == str(key)):
                idx = self.list_model.index(row)
                self.listw.setCurrentIndex(idx)
                self.listw.scrollTo(
                    idx, QAbstractItemView.ScrollHint.PositionAtCenter)
                return True
        return False

    def _reveal_item_in_list(self, it, target: str) -> None:
        """Navigate the *target* mode's list to *it*'s own category and select
        it - used when playing from Home so the classic list behind the player
        reflects (and highlights) the item, in its category, whether or not we
        were already in that mode. Works for any mode (live channels, movies)."""
        self._pending_jump_key = self._item_key(it)
        self._pending_jump_cat = it.get("category_id")
        # Safety net only - long enough that a slow provider fetch of the
        # category's list still lands inside the window (2.5 s wasn't: the key
        # got cleared mid-load and the row was never selected).
        QTimer.singleShot(8000, self._clear_pending_jump)
        if self.mode != target:
            self.switch_mode(target)   # done() honours the pending jump
            return
        if not self.cat_list.count():
            return
        row, cat = 0, self._pending_jump_cat
        if cat is not None:
            for i in range(self.cat_list.count()):
                d = self.cat_list.item(i).data(Qt.ItemDataRole.UserRole)
                # str() both sides: providers mix int and str category ids.
                if d is not None and str(d) == str(cat):
                    row = i
                    break
        self._pending_jump_cat = None
        if self.cat_list.currentRow() == row:
            self._apply_filter()   # same category: just (re)select the row
        else:
            self.cat_list.setCurrentRow(row)

    def _reveal_channel_in_list(self, it) -> None:
        """Reveal a live channel in the TV list (see _reveal_item_in_list)."""
        self._reveal_item_in_list(it, "live")

    def _visible_catalog(self, items, mode: str) -> list:
        """*items* from a provider-wide list (category None) with what the
        user hid or locked taken out, exactly as the category views do. Home,
        the guide and the searches read the whole catalogue directly, and
        showed a locked category's films to anyone who opened them there."""
        keep = self._catalog_filter(mode)
        return [it for it in (items or []) if keep(it)]

    def _catalog_filter(self, mode: str):
        """_visible_catalog's test for one row. Made here on the GUI thread,
        it can run on a worker: going through tens of thousands of films
        on the GUI thread held up the window, and the player with it."""
        excluded = self.overrides.excluded_ids(
            mode, include_locked=not self.parental.session_unlocked)
        hidden = self._channel_hidden
        # A kids profile: nothing for adults either (mw_profiles).
        kids = getattr(self, "_kids_keep", lambda _m: None)(mode)
        if kids is not None:
            return lambda it: (str(it.get("category_id")) not in excluded
                               and not hidden(it, mode) and kids(it))
        return lambda it: (str(it.get("category_id")) not in excluded
                           and not hidden(it, mode))

    # -- the parental lock on what the stores kept ---------------------------------

    def _lock_shut(self) -> dict[str, frozenset[str]]:
        """Per mode, the source in use's categories that are locked while the
        lock is shut (a kids profile's always); none while it is open."""
        kids = getattr(self, "_profile_kids", lambda: False)()
        if self.parental.session_unlocked and not kids:
            return {}
        out = {}
        for mode in ("live", "vod", "series"):
            ids = self.overrides.locked_ids(mode)
            if ids:
                out[mode] = ids
        return out

    def _lock_filter(self, rows) -> list:
        """*rows* the stores kept of the source in use - History's, Continue
        watching's - as the lists may show them: none of a category locked
        while the lock is shut. A row that does not say its category (History
        kept none for a channel or a film before) is held back until the
        source's catalogue said it - read meanwhile on the pool, and shown
        then (_lock_learn). With nothing locked, or the lock open, all."""
        rows = list(rows or [])
        shut = self._lock_shut()
        if not shut:
            return rows
        found = self._lock_known()
        out, wanted = [], {}
        for r in rows:
            hides = lock_hides(r, shut, found)
            if hides is None:
                place = kept_place(r)
                if place is not None:
                    wanted.setdefault(place[0], set()).add(place[2])
                self._lock_held = True
            elif not hides:
                out.append(r)
        if wanted:
            self._lock_learn(wanted)
        return out

    def _lock_known(self) -> dict[str, dict[str, str]]:
        """Per mode, what the source in use's catalogue said of the rows
        _lock_filter held back: their id -> category ("" for none)."""
        got = self.__dict__.get("_lock_cats")
        if got is None or got[0] is not self.client:
            got = (self.client, {})
            self._lock_cats = got
        return got[1]

    def _lock_learn(self, wanted: dict[str, set[str]]) -> None:
        """The categories of *wanted* (per mode, ids) read from the source's
        catalogue on the pool - its lists as the client holds them, a
        portal's only as loaded before - then whatever was held back for
        want of them shown (_lock_cats_in)."""
        import threading
        if threading.current_thread() is not threading.main_thread():
            return
        client = self.client
        failed_at = self.__dict__.get("_lock_failed")
        if failed_at is not None and failed_at[0] is client \
                and time.monotonic() - failed_at[1] < LOCK_RETRY_S:
            return                      # held back meanwhile; asked again later
        asking = self.__dict__.get("_lock_asking")
        if asking is None or asking[0] is not client:
            asking = (client, set())
            self._lock_asking = asking
        todo = {m: {i for i in ids if (m, i) not in asking[1]} for m, ids in wanted.items()}
        todo = {m: ids for m, ids in todo.items() if ids}
        if not todo:
            return
        asking[1].update((m, i) for m, ids in todo.items() for i in ids)

        def work():
            return client, {m: catalogue_cats(client, m, ids) for m, ids in todo.items()}

        def failed(_msg=None) -> None:
            if self.__dict__.get("_lock_asking", (None,))[0] is client:
                asking[1].difference_update((m, i) for m, ids in todo.items() for i in ids)
            self._lock_failed = (client, time.monotonic())

        run_async(self.pool, work, self._lock_cats_in, failed)

    def _lock_cats_in(self, got) -> None:
        client, found = got
        if client is not self.client:
            return
        known = self._lock_known()
        for mode, cats in found.items():
            known.setdefault(mode, {}).update(cats)
        if not self.__dict__.pop("_lock_held", False):
            return
        held = getattr(self, "_held_rows_in", None)
        if callable(held):
            held("lock")

    def _channel_hidden(self, it, kind: str) -> bool:
        # A row carrying its own kind is judged by THAT, not by the view's.
        # In the mixed views (Favorites "All", Watched, History) the
        # view-derived kind is "fav" for every row, so every one of them was
        # looked up in the LIVE hidden list: a hidden movie stayed visible
        # there, and - the real damage - a favourite movie vanished when an
        # unrelated channel happened to share its id, because provider ids
        # are only unique within a type.
        row = it.get("_kind") or it.get("_ekind")
        kind = {"live": "live", "fav": "live", "movie": "vod", "vod": "vod",
                "series": "series"}.get(row, kind)
        if kind not in ("live", "vod", "series", "fav"):
            return False
        key = self._item_key(it)
        if key is None:
            return False
        ov_mode = "live" if kind == "fav" else kind
        return self.channel_ov.is_hidden(ov_mode, key)

    def _content_kind(self) -> str:
        """The kind of content the middle list is currently showing.
        Same as self.mode except inside the Favorites view, where the
        selected section decides whether the rows are channels ('fav',
        painted like live), movies ('vod') or series ('series')."""
        if self.series_ctx:
            return "episode"
        if self.mode == "fav":
            return {"movie": "vod", "series": "series", "radio": "radio",
                    "web": "web"}.get(self._fav_section, "fav")
        return self.mode

    def _on_list_populated(self) -> None:
        """Retire the loading strip/overlay once the model actually has rows.

        Wired to list_model.modelReset so it fires for every set_items path,
        including the ones that skip _apply_filter (startup load while the
        window is inactive, mw_search). Tolerates firing during early
        __init__, before the busy machinery exists."""
        if self.list_model.rowCount() <= 0:
            return
        if hasattr(self, "_hide_busy") and hasattr(self, "notices"):
            self._hide_busy()

    def _apply_filter(self) -> None:
        # Radio's Search row asks the whole directory instead (mw_radio).
        if self.mode == "radio" and self._radio_search_hook():
            return
        # In a folder of Local files it looks through the subfolders too.
        if self.mode == "local" and self._local_search_hook():
            return
        text = self.search.text().lower().strip()
        kind = self._content_kind()
        if self.channel_ov.anything_hidden():
            items = [it for it in self.all_items
                     if not self._channel_hidden(it, kind)]
        else:
            # Nothing hidden (most lists): asking each of 80,000 films
            # took a third of a second on the GUI thread.
            items = list(self.all_items)
        kids = getattr(self, "_kids_keep", lambda _m: None)(kind)
        if kids is not None:
            # A kids profile: nothing the provider marks for adults, nor a
            # category named for them - "All" and "Recently added" too.
            items = [it for it in items if kids(it)]
        if text:
            name = self._display_name_fn()
            filtered = [it for it in items if text in name(it).lower()]
        else:
            filtered = items
        filtered = self._sorted(filtered)
        self.list_model.set_items(filtered, kind)
        if getattr(self, "_pending_jump_key", None) is not None:
            # Match the armed key itself (for every playing-item jump it equals
            # the playing key, so this is the same select - but the back-from-
            # episodes landing arms the SERIES row, which isn't what's playing).
            if self._try_select_key(self._pending_jump_key):
                self._pending_jump_key = None
        # The list is populated now, so clear any loading strip/overlay - a
        # gen-discarded async load (e.g. rapid Home<->TV toggling) could leave
        # the "Loading channels…" overlay stuck on until the next selection.
        self._hide_busy()
        if self.mode == "fav" and not self.series_ctx and not self.all_items:
            self._set_list_status(tr({"movie": "fav_empty_movies",
                                      "series": "fav_empty_series"}.get(
                                          self._fav_section, "fav_empty_channels")))
        elif kind == "history" and not self.all_items:
            self._set_list_status(tr("history_empty"))
        else:
            self._set_list_status(self._count_text(kind, len(filtered)))
        # The TV place's header and Recently watched (mw_place_tv).
        shown = getattr(self, "_place_tv_list_shown", None)
        if shown is not None:
            shown()
        # The Guide page over TV follows the list's category (mw_guide).
        follow = getattr(self, "_guide_follow_list", None)
        if follow is not None:
            follow()
