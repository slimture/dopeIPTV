"""Extracted from main_window.py (mixin); MainWindow inherits this.

Verbatim move - self.* access and behaviour unchanged.
"""

from __future__ import annotations

from .dialogs import ContentManagerDialog
from ..core.stores import FAV_DEFAULT_GROUP
from ..i18n import tr
from .tmdb_match import TmdbMatchDialog
from .widgets import confirm
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QInputDialog, QLineEdit, QMenu, QMessageBox


class _ContextMenuMixin:
    def _context_menu(self, pos) -> None:
        idx = self.listw.indexAt(pos)
        if not idx.isValid():
            return
        it = self.list_model.item_at(idx.row())
        if not it:
            return
        # Select (highlight) the right-clicked row so it's clear which
        # item the menu acts on. The _rmb_selecting guard stops the
        # live-channel autoplay preview from firing, so right-clicking a
        # channel highlights it without starting playback or interrupting
        # whatever is already playing.
        self._rmb_selecting = True
        self.listw.setCurrentIndex(idx)
        self._rmb_selecting = False
        if self.mode == "rec":
            self._rec_context_menu(pos, it)
            return
        m = QMenu(self)
        m.addAction(tr("ctx_play_in_mpv"), lambda: self.play_item(it, "mpv"))
        # Continue-watching rows carry a resume point; offer to forget it.
        if it.get("_resume_pos") is not None:
            m.addAction(tr("ctx_continue_remove"),
                        lambda it=it: self._remove_continue(it))
        ext = m.addMenu(tr("ctx_open_externally"))
        ext.addAction("mpv",
                      lambda: self.play_item(it, "mpv", external=True))
        ext.addAction("VLC",
                      lambda: self.play_item(it, "vlc", external=True))
        if not (self.mode == "series" and not self.series_ctx):
            m.addAction(tr("ctx_cast_to_chromecast"),
                        lambda: self._open_cast_dialog(it))
        content_kind = self._content_kind()
        # The grouped "All favorites" view shares mode 'fav' for every row;
        # honour the row's own tag so movies/series get their own menu there.
        # Without this a movie row fell into the CHANNEL branch: "Remove from
        # favorites" removed from the channel store (a no-op for the movie),
        # and it was offered channel-only actions (multiview/timeshift/record).
        if content_kind == "fav":
            rk = it.get("_kind") or it.get("_ekind")
            if rk in ("vod", "movie"):
                content_kind = "vod"
            elif rk == "series":
                content_kind = "series"
        # History / Watch Later / Watched rows are snapshots of channels,
        # movies or series - map them to their real kind so they get the same
        # add/remove-favorites (and, for channels, multiview/timeshift)
        # actions as their home views.
        elif content_kind in ("history", "watchlist", "watched"):
            rk = it.get("_kind") or it.get("_ekind")
            if rk in ("vod", "movie"):
                content_kind = "vod"
                # History movie rows carry the provider id only as _key and
                # the container extension only inside the stored URL -
                # synthesize the fields a favorite snapshot (and its later
                # playback) needs.
                if it.get("stream_id") is None and it.get("_key") is not None:
                    tail = (it.get("_url") or "").rsplit(".", 1)
                    ext = (tail[1] if len(tail) == 2
                           and 0 < len(tail[1]) <= 4 else None)
                    it = {**it, "stream_id": it.get("_key"),
                          "container_extension":
                              it.get("container_extension") or ext}
            elif rk == "series" and it.get("series_id") is not None:
                content_kind = "series"
            elif rk == "live":
                content_kind = "live"
        if (content_kind in ("live", "fav")
                and it.get("stream_id") is not None):
            # Pick which grid window to send the stream to; each shows the
            # channel it currently holds so you know what you'd replace. The
            # count follows Settings → Multiview (2/4/6/9).
            mv = m.addMenu(tr("mv_add"))
            mvw = getattr(self, "_multiview_win", None)
            if mvw is not None:
                mv_count = len(mvw.cells)
            else:
                try:
                    mv_count = int(self.settings.value("mv_cells", 4))
                except (TypeError, ValueError):
                    mv_count = 4
                if mv_count not in (2, 4, 6, 9):
                    mv_count = 4
            for n in range(mv_count):
                occupant = ""
                if (mvw is not None and n < len(mvw.cells)
                        and mvw.cells[n].title):
                    occupant = f"  —  {mvw.cells[n].title}"
                mv.addAction(
                    tr("mv_cell", n=n + 1) + occupant,
                    lambda it=it, n=n: self._add_channel_to_multiview(it, n))
            m.addAction(tr("btn_epg_guide"), self._open_epg_guide)
        if (content_kind in ("live", "fav")
                and it.get("stream_id") is not None):
            if self._timeshift_days(it):
                m.addSeparator()
                self._build_timeshift_menu(
                    m.addMenu(tr("tooltip_timeshift")), it)
            elif self._ts_provider_flagged(it):
                # The provider flags this channel with catch-up but we've
                # learned (or wrongly learned) it as broken, so its ◀◀ marker
                # is hidden. Offer a per-channel reset to bring it back.
                m.addSeparator()
                m.addAction(tr("ts_reset_channel"),
                            lambda it=it: self._reset_channel_timeshift(it))
            m.addSeparator()
            self._build_record_menu(m.addMenu(tr("rec_record")), it)
            # For a not-yet-live (or any) channel: a reminder for its upcoming
            # programme, alongside the record options above - the same set the
            # "stream hasn't started yet" prompt offers.
            m.addAction(tr("upcoming_remind"),
                        lambda it=it: self._remind_upcoming(it))
        if (content_kind in ("live", "fav")
                and it.get("stream_id") is not None):
            # Every category works the same: a one-click "Add to favorites"
            # (into the default bucket), an "Add to folder" submenu for any
            # user-made folders, and Remove when it's already a favorite.
            m.addSeparator()
            if (content_kind == "fav"
                    or self.favs.is_favorite(it.get("stream_id"))):
                m.addAction(tr("ctx_remove_from_favorites"),
                            lambda: self._remove_fav(it))
            else:
                m.addAction(tr("ctx_add_to_favorites"),
                            lambda: self._add_fav(FAV_DEFAULT_GROUP, it))
            self._add_fav_folder_menu(
                m, lambda g: self._add_fav(g, it),
                lambda: self._add_fav(None, it), self.favs)
        elif content_kind == "vod" and it.get("stream_id") is not None:
            m.addSeparator()
            if self.movie_favs.is_favorite(it.get("stream_id")):
                m.addAction(tr("ctx_remove_from_favorites"),
                            lambda: self._toggle_media_fav(it, "movie", False))
            else:
                m.addAction(tr("ctx_add_to_favorites"),
                            lambda: self._toggle_media_fav(it, "movie", True))
            self._add_fav_folder_menu(
                m,
                lambda g: self._toggle_media_fav(it, "movie", True, group=g),
                lambda: self._new_media_fav_folder(it, "movie"),
                self.movie_favs)
        elif content_kind == "series" and it.get("series_id") is not None:
            m.addSeparator()
            if self.series_favs.is_favorite(it.get("series_id")):
                m.addAction(tr("ctx_remove_from_favorites"),
                            lambda: self._toggle_media_fav(it, "series", False))
            else:
                m.addAction(tr("ctx_add_to_favorites"),
                            lambda: self._toggle_media_fav(it, "series", True))
            self._add_fav_folder_menu(
                m,
                lambda g: self._toggle_media_fav(it, "series", True, group=g),
                lambda: self._new_media_fav_folder(it, "series"),
                self.series_favs)
        if (self.mode in ("vod", "series") and not self.series_ctx
                and self.tmdb):
            m.addSeparator()
            m.addAction(tr("ctx_match_tmdb"),
                        lambda: self._open_tmdb_match_dialog(
                            it, "vod" if self.mode == "vod" else "series"))
        # Mark-as-watched and Watch-Later live behind the same TMDB
        # match. Both surface a 'local only' variant (no Trakt push)
        # and, when Trakt is connected, a 'and on Trakt' variant that
        # also POSTs the change so any other device sees it.
        # For rows in the Watch Later view, _kind on the snapshot
        # tells us whether the entry is a movie or a series so the
        # same code path works there.
        eff_mode = self.mode
        if self.mode in ("watchlist", "watched"):
            eff_mode = it.get("_kind") or "vod"
        elif self.mode == "history":
            # History stores 'movie'/'episode'/'live'; only movies get
            # the mark-as-watched actions here (episodes lack the
            # season/episode data those need).
            eff_mode = "vod" if it.get("_kind") == "movie" else None
        # Mark-as-watched does NOT need TMDB: the local flag keys on the
        # provider stream_id when no TMDB account is configured. Only
        # the '+ Trakt' variants need TMDB (Trakt's API is tmdb-keyed),
        # so those are gated on trakt-connected AND a TMDB resolver.
        if ((eff_mode == "vod")
                or (eff_mode == "series" and not self.series_ctx)
                or self.series_ctx):
            m.addSeparator()
            # The '+Trakt' push needs a tmdb id (Trakt's API is tmdb-keyed):
            # available either from a TMDB resolver or from an id the row
            # already carries (Watch Later / Watched snapshots). Without a
            # TMDB key AND without a carried id there's no way to reach Trakt.
            trakt_ok = self.trakt.is_connected() and (
                self.tmdb is not None
                or isinstance(it.get("_tmdb_id"), int))
            if self.series_ctx:
                mark = self._mark_episode_watched
                unmark = self._unmark_episode_watched
                watched = self.is_episode_watched(it)
            elif eff_mode == "vod":
                mark = self._mark_movie_watched
                unmark = self._unmark_movie_watched
                watched = self.is_movie_watched(it)
            else:
                # Series list item: local-only 'seen the whole show'.
                # Trakt has no whole-show watched primitive, only
                # per-episode, so we skip the +Trakt variant here.
                mark = self._mark_series_watched
                unmark = self._unmark_series_watched
                watched = self.show_watched_count(it) > 0
            if mark is not None:
                if watched:
                    m.addAction(tr("ctx_unmark_watched"),
                                lambda it=it: unmark(it, False))
                    if trakt_ok:
                        m.addAction(tr("ctx_unmark_watched_trakt"),
                                    lambda it=it: unmark(it, True))
                else:
                    m.addAction(tr("ctx_mark_watched"),
                                lambda it=it: mark(it, False))
                    if trakt_ok:
                        m.addAction(tr("ctx_mark_watched_trakt"),
                                    lambda it=it: mark(it, True))
        # Watch Later toggle only for movies and shows (not episodes).
        # Also works from within the Watch Later view - the row's
        # _kind maps it back to vod/series so the same store call
        # runs. That's what makes 'remove from Watch Later' reachable.
        # Watch Later also works without TMDB - the list stores a
        # snapshot keyed on the provider stream_id. TMDB only gates the
        # '+ Trakt' variants (Trakt watchlist is tmdb-keyed).
        wl_kind = None
        if not self.series_ctx:
            if self.mode in ("vod", "series"):
                wl_kind = self.mode
            elif self.mode == "watchlist":
                wl_kind = it.get("_kind")
            elif self.mode == "history":
                wl_kind = "vod" if it.get("_kind") == "movie" else None
        if wl_kind in ("vod", "series"):
            on_list = self.is_on_watchlist(it, wl_kind)
            trakt_ok = self.trakt.is_connected() and self.tmdb is not None
            if on_list:
                m.addAction(
                    tr("ctx_watchlist_remove"),
                    lambda it=it, k=wl_kind: self._remove_watchlist(it, k, False))
                if trakt_ok:
                    m.addAction(
                        tr("ctx_watchlist_remove_trakt"),
                        lambda it=it, k=wl_kind: self._remove_watchlist(
                            it, k, True))
            else:
                m.addAction(
                    tr("ctx_watchlist_add"),
                    lambda it=it, k=wl_kind: self._add_watchlist(it, k, False))
                if trakt_ok:
                    m.addAction(
                        tr("ctx_watchlist_add_trakt"),
                        lambda it=it, k=wl_kind: self._add_watchlist(
                            it, k, True))
        if (self.mode in ("live", "vod", "series")
                and not self.series_ctx):
            ov_mode = self.mode
            key = self._item_key(it)
            m.addSeparator()
            m.addAction(
                tr("ctx_rename_channel") if ov_mode == "live"
                else tr("ctx_rename"),
                lambda: self._rename_channel(ov_mode, key, it))
            m.addAction(
                tr("ctx_hide_channel") if ov_mode == "live" else tr("ctx_hide"),
                lambda: self._hide_channel(ov_mode, key))
            _palette = ((tr("color_default"), ""),
                        (tr("accent_blue"), "#4C8DFF"),
                        (tr("accent_green"), "#2FBF71"),
                        (tr("accent_orange"), "#FF9F43"),
                        (tr("accent_red"), "#FF5C5C"),
                        (tr("accent_purple"), "#8E6BFF"),
                        (tr("accent_teal"), "#2AC3C3"),
                        (tr("accent_pink"), "#FF5C8A"))
            color_menu = m.addMenu(tr("ctx_set_color"))
            for label, hexv in _palette:
                color_menu.addAction(
                    label,
                    lambda c=hexv: self._set_item_color(ov_mode, key, color=c))
            bg_menu = m.addMenu(tr("ctx_set_bg_color"))
            for label, hexv in _palette:
                bg_menu.addAction(
                    label,
                    lambda c=hexv: self._set_item_color(ov_mode, key, bgcolor=c))
            if self.channel_ov.get(ov_mode, key):
                m.addAction(
                    tr("ctx_reset_channel"),
                    lambda: self._reset_channel(ov_mode, key))
            if self.channel_ov.has_overrides(ov_mode):
                m.addAction(
                    tr("ctx_restore_defaults"),
                    lambda: self._restore_default_channels(ov_mode))
        if self.mode == "history":
            m.addSeparator()
            m.addAction(tr("ctx_remove_from_history"),
                        lambda: self._remove_history_selected(it))
        if (not (self.mode == "series" and not self.series_ctx)
                and self.mode != "history"):
            url, _ = self._stream_for(it)
            if url:
                m.addSeparator()
                m.addAction(
                    tr("ctx_copy_stream_url"),
                    lambda: QApplication.clipboard().setText(url))
        m.exec(self.listw.viewport().mapToGlobal(pos))

    # -- channel customizations ----------------------------------------------------

    def _rename_channel(self, mode: str, key, it) -> None:
        if key is None:
            return
        current = self.channel_ov.display_name(
            mode, key, it.get("name") or it.get("title") or "")
        name, ok = QInputDialog.getText(
            self, tr("ctx_rename_channel"), tr("cm_new_name"), text=current)
        if ok:
            self.channel_ov.update(mode, key, name=name.strip())
            self._apply_filter()

    def _hide_channel(self, mode: str, key) -> None:
        if key is None:
            return
        self.channel_ov.update(mode, key, hidden=True)
        self._apply_filter()

    def _reset_channel(self, mode: str, key) -> None:
        self.channel_ov.update(mode, key, name="", hidden=False,
                               color="", bgcolor="")
        self._apply_filter()

    def _set_item_color(self, mode: str, key, **fields) -> None:
        if key is None:
            return
        self.channel_ov.update(mode, key, **fields)
        self.list_model.refresh_all()
        self.listw.viewport().update()

    def _open_tmdb_match_dialog(self, it: dict, kind: str) -> None:
        """Open the manual TMDB-match dialog for a movie/series and refresh
        the detail panel + list icon once the user picks (or clears) an
        override. kind is 'vod' for movies or 'series' for TV shows."""
        title = (it.get("name") or it.get("title") or "").strip()
        if not title or not self.tmdb:
            return

        def on_pick(_details) -> None:
            # Nudge the list model so the delegate re-queries the poster,
            # and rebuild the detail panel if this item is currently open.
            self.list_model.refresh_all()
            current = self.list_model.item_at(
                self.listw.currentIndex().row())
            if current and current is it:
                self._show_detail(it)

        TmdbMatchDialog(self, title, kind, on_pick).exec()

    def _restore_default_channels(self, mode: str) -> None:
        if confirm(self, tr("ctx_restore_defaults").rstrip("."),
                   tr("msg_restore_defaults_body")):
            self.channel_ov.reset_mode(mode)
            self._apply_filter()

    # -- favorites -----------------------------------------------------------------

    def _add_fav_folder_menu(self, menu, add_to_group, new_folder,
                             store) -> None:
        """Shared 'Add to folder ▸ [folders…] / New folder…' submenu for all
        three categories. *add_to_group* files the item into a named folder;
        *new_folder* prompts for a new one."""
        folder = menu.addMenu(tr("ctx_add_to_folder"))
        for g in store.custom_groups():
            folder.addAction(g, lambda g=g: add_to_group(g))
        if store.custom_groups():
            folder.addSeparator()
        folder.addAction(tr("ctx_new_folder"), lambda: new_folder())

    def _new_media_fav_folder(self, item, section: str) -> None:
        name, ok = QInputDialog.getText(
            self, tr("ctx_new_folder"), tr("prompt_folder_name"))
        name = (name or "").strip()
        if ok and name and name != FAV_DEFAULT_GROUP:
            self._toggle_media_fav(item, section, True, group=name)

    def _add_fav(self, group, item) -> None:
        if group is None:
            group, ok = QInputDialog.getText(
                self, tr("ctx_new_folder"), tr("prompt_folder_name"))
            group = (group or "").strip()
            if not ok or not group:
                return
        self.favs.add(group, item)
        if self.mode == "fav":
            self._load_categories()

    def _remove_fav(self, item) -> None:
        if self.mode == "fav":
            cur = self.cat_list.currentItem()
            data = cur.data(Qt.ItemDataRole.UserRole) if cur else None
            # fav category data is a (section, group) tuple; only the
            # channel section carries a group to scope the removal to.
            group = data[1] if isinstance(data, tuple) else None
            self.favs.remove(item.get("stream_id"), group)
            self._load_categories()
        else:
            self.favs.remove(item.get("stream_id"))
            self.list_model.refresh_all()

    def _toggle_media_fav(self, item, section: str, add: bool,
                          group: str | None = None) -> None:
        """Add/remove a movie ('movie') or series ('series') favorite. *add*
        with no *group* files it into the default bucket; pass a folder name
        to file it there instead. Remove drops it from every folder. Mirrors
        to Trakt when connected and refreshes the affected view."""
        store = self.movie_favs if section == "movie" else self.series_favs
        ident = item.get(store.id_key)
        kind = "vod" if section == "movie" else "series"
        tid = self._tmdb_id_for_item(item, kind)
        if add:
            # Stamp the resolved tmdb id onto the stored snapshot so a
            # later Trakt push/remove doesn't need to re-resolve it.
            snap = dict(item)
            if isinstance(tid, int):
                snap["_tmdb_id"] = tid
            store.add(group or FAV_DEFAULT_GROUP, snap)
        else:
            store.remove(ident)
        # Mirror to the Trakt 'dopeIPTV Favorites' list when connected.
        if self.trakt.is_connected():
            if isinstance(tid, int):
                self._trakt_push(
                    (lambda: self.trakt.add_favorite(tid, kind)) if add
                    else (lambda: self.trakt.remove_favorite(tid, kind)))
            elif add and self.tmdb is not None:
                self._resolve_tmdb_id_async(
                    item, kind,
                    lambda rid, k=kind: self._trakt_push(
                        lambda: self.trakt.add_favorite(rid, k))
                    if isinstance(rid, int) else None)
        if (self.mode == "fav"
                and self._fav_section in (section, "all")
                and not add):
            # Reload so the removed row disappears - also in the grouped
            # "All" view, which shows movie/series rows too.
            cur = self.cat_list.currentItem()
            self._load_items(cur.data(Qt.ItemDataRole.UserRole) if cur
                             else (section, None))
        else:
            self.list_model.refresh_all()

    # -- parental control ----------------------------------------------------------

    def _request_unlock(self) -> bool:
        if self.parental.session_unlocked:
            return True
        if not self.parental.has_pin():
            return True
        pin, ok = QInputDialog.getText(
            self, tr("parental_control"), tr("parental_enter_pin"),
            QLineEdit.EchoMode.Password)
        if not ok:
            return False
        if self.parental.verify(pin.strip()):
            self.parental.session_unlocked = True
            return True
        QMessageBox.warning(self, tr("msg_parental_title"),
                            tr("msg_wrong_pin"))
        return False

    def _ensure_pin_configured(self) -> bool:
        if self.parental.has_pin():
            return True
        pin, ok = QInputDialog.getText(
            self, tr("parental_control"), tr("pin_choose_prompt"),
            QLineEdit.EchoMode.Password)
        pin = (pin or "").strip()
        if ok and pin:
            self.parental.set_pin(pin)
            return True
        return False

    # -- category context menu -----------------------------------------------------

    def _cat_menu(self, pos) -> None:
        it = self.cat_list.itemAt(pos)
        data = it.data(Qt.ItemDataRole.UserRole) if it else None
        m = QMenu(self)

        if self.mode == "fav":
            if not data:
                return
            section, group = data
            store = {"chan": self.favs, "movie": self.movie_favs,
                     "series": self.series_favs}.get(section)
            if store is None:      # the 'All' / Trakt rows manage nothing
                return
            if group and group != FAV_DEFAULT_GROUP:
                # A folder row: rename / remove (+ parental lock for channels).
                m.addAction(
                    tr("ctx_rename_folder"),
                    lambda: self._rename_fav_folder(store, group))
                m.addAction(
                    tr("ctx_remove_folder", group=group),
                    lambda: (store.remove_group(group),
                             self._load_categories()))
                if section == "chan":
                    if store.is_locked(group):
                        m.addAction(tr("ctx_unlock_group"),
                                    lambda: self._set_fav_lock(group, False))
                    else:
                        m.addAction(tr("ctx_lock_group"),
                                    lambda: self._set_fav_lock(group, True))
                _pal = ((tr("color_default"), ""),
                        (tr("accent_blue"), "#4C8DFF"),
                        (tr("accent_green"), "#2FBF71"),
                        (tr("accent_orange"), "#FF9F43"),
                        (tr("accent_red"), "#FF5C5C"),
                        (tr("accent_purple"), "#8E6BFF"),
                        (tr("accent_teal"), "#2AC3C3"),
                        (tr("accent_pink"), "#FF5C8A"))
                cmenu = m.addMenu(tr("ctx_set_color"))
                for label, hexv in _pal:
                    cmenu.addAction(
                        label,
                        lambda c=hexv, st=store, gr=group:
                        self._set_folder_color(st, gr, color=c))
                bmenu = m.addMenu(tr("ctx_set_bg_color"))
                for label, hexv in _pal:
                    bmenu.addAction(
                        label,
                        lambda c=hexv, st=store, gr=group:
                        self._set_folder_color(st, gr, bgcolor=c))
            else:
                # A section header row: make a new folder under it.
                m.addAction(tr("ctx_new_folder"),
                            lambda: self._new_fav_folder(store))
            m.exec(self.cat_list.mapToGlobal(pos))
            return

        if self.mode not in ("live", "vod", "series"):
            return
        if data is not None:
            cid = data
            m.addAction(tr("ctx_rename_category"),
                        lambda: self._rename_category(cid))
            m.addAction(tr("ctx_set_icon"),
                        lambda: self._set_category_icon(cid))
            _palette = ((tr("color_default"), ""),
                        (tr("accent_blue"), "#4C8DFF"),
                        (tr("accent_green"), "#2FBF71"),
                        (tr("accent_orange"), "#FF9F43"),
                        (tr("accent_red"), "#FF5C5C"),
                        (tr("accent_purple"), "#8E6BFF"),
                        (tr("accent_teal"), "#2AC3C3"),
                        (tr("accent_pink"), "#FF5C8A"))
            color_menu = m.addMenu(tr("ctx_set_color"))
            for label, hex_val in _palette:
                color_menu.addAction(
                    label,
                    lambda c=hex_val: self._set_category_color(cid, color=c))
            bg_menu = m.addMenu(tr("ctx_set_bg_color"))
            for label, hex_val in _palette:
                bg_menu.addAction(
                    label,
                    lambda c=hex_val: self._set_category_color(cid, bgcolor=c))
            cov = self.overrides.get(self.mode, cid)
            if cov.get("color") or cov.get("bgcolor"):
                m.addAction(
                    tr("ctx_reset_color"),
                    lambda: self._set_category_color(cid, color="", bgcolor=""))
            m.addSeparator()
            m.addAction(tr("ctx_hide_category"),
                        lambda: self._set_category_flag(cid, hidden=True))
            if self.overrides.is_locked(self.mode, cid):
                m.addAction(
                    tr("ctx_unlock_category"),
                    lambda: self._set_category_flag(cid, locked=False))
            else:
                m.addAction(tr("ctx_lock_category"),
                            lambda: self._lock_category(cid))
            m.addSeparator()
        if self.mode == "live":
            m.addAction(tr("btn_epg_guide"), self._open_epg_guide)
            m.addSeparator()
        m.addAction(tr("ctx_manage_categories"), self._open_content_manager)
        m.addSeparator()
        m.addAction(tr("menu_refresh_playlist"), self.refresh_playlist)
        m.exec(self.cat_list.mapToGlobal(pos))

    def _set_folder_color(self, store, group: str, **fields) -> None:
        store.set_group_color(group, **fields)
        # Reload the list so the folder's items repaint with the new colour.
        if self.mode == "fav":
            self._load_items(self.cat_list.currentItem().data(
                Qt.ItemDataRole.UserRole)
                if self.cat_list.currentItem() else None)

    def _new_fav_folder(self, store) -> None:
        name, ok = QInputDialog.getText(
            self, tr("ctx_new_folder"), tr("prompt_folder_name"))
        name = (name or "").strip()
        if ok and name and name != FAV_DEFAULT_GROUP:
            store.ensure_group(name)
            self._load_categories()

    def _rename_fav_folder(self, store, group: str) -> None:
        name, ok = QInputDialog.getText(
            self, tr("ctx_rename_folder"), tr("prompt_folder_name"),
            text=group)
        name = (name or "").strip()
        if ok and name and name != group and name != FAV_DEFAULT_GROUP:
            store.rename_group(group, name)
            self._load_categories()

    def _set_fav_lock(self, group: str, locked: bool) -> None:
        if locked and not self._ensure_pin_configured():
            return
        self.favs.set_group_locked(group, locked)
        if locked:
            self.parental.lock_session()
        self._load_categories()

    def _rename_category(self, cid) -> None:
        current = self.overrides.display_name(
            self.mode, cid,
            next((c.get("category_name", "") for c in self._raw_categories
                  if c.get("category_id") == cid), ""))
        name, ok = QInputDialog.getText(
            self, tr("cm_rename_title"), tr("cm_new_name"), text=current)
        if ok:
            self.overrides.update(self.mode, cid, name=name.strip())
            self._load_categories()

    def _set_category_icon(self, cid) -> None:
        current = self.overrides.get(self.mode, cid).get("icon", "")
        icon, ok = QInputDialog.getText(
            self, tr("set_cat_icon_title"), tr("set_cat_icon_prompt"),
            text=current)
        if ok:
            self.overrides.update(self.mode, cid, icon=icon.strip())
            self._load_categories()

    def _set_category_flag(self, cid, **fields) -> None:
        self.overrides.update(self.mode, cid, **fields)
        self._load_categories()

    def _set_category_color(self, cid, **fields) -> None:
        """Colour a category row without reloading the list, so the selection
        and scroll position stay put (a full reload jumps back to the top)."""
        from PyQt6.QtGui import QColor
        self.overrides.update(self.mode, cid, **fields)
        ovr = self.overrides.get(self.mode, cid)
        col, bg = ovr.get("color", ""), ovr.get("bgcolor", "")
        for i in range(self.cat_list.count()):
            item = self.cat_list.item(i)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == cid:
                item.setData(Qt.ItemDataRole.ForegroundRole,
                             QColor(col) if col else None)
                item.setData(Qt.ItemDataRole.BackgroundRole,
                             QColor(bg) if bg else None)
                break
        self.cat_list.viewport().update()

    def _lock_category(self, cid) -> None:
        if not self._ensure_pin_configured():
            return
        self.parental.lock_session()
        self._set_category_flag(cid, locked=True)

    def _open_content_manager(self) -> None:
        if self.mode not in ("live", "vod", "series"):
            return
        cur = self.cat_list.currentItem()
        self._pending_cat_select = (cur.data(Qt.ItemDataRole.UserRole)
                                    if cur else self._pending_cat_select)
        ContentManagerDialog(
            self, self.mode, self._raw_categories, self.overrides).exec()
        self._load_categories()
