"""Favorites in bulk: act on every selected row at once, move rows between
folders, and the drag-and-drop that does the same with the mouse - drop rows
on a folder in the sidebar to move them there, or between rows to reorder.

MainWindow mixin; the single-row favorite actions stay in mw_context.
"""

from __future__ import annotations

from PyQt6.QtCore import QItemSelectionModel, Qt, QTimer
from PyQt6.QtWidgets import QInputDialog

from ..core.stores import FAV_DEFAULT_GROUP
from ..i18n import tr

# The context-menu kind of a row -> the favorites section that stores it.
_SECTION = {"live": "chan", "fav": "chan", "vod": "movie", "series": "series"}
_SECTION_LABEL = {"chan": "fav_channels", "movie": "fav_movies",
                  "series": "fav_series"}


class _FavoritesMixin:
    """MainWindow mixin: multi-row favorite actions, moves and drag-and-drop."""

    _fav_drag_items: list = []

    def _fav_store(self, section: str | None):
        return {"chan": self.favs, "movie": self.movie_favs,
                "series": self.series_favs}.get(section)

    def _row_content_kind(self, it: dict) -> tuple[str, dict]:
        """The kind a row's actions follow, and the row they act on.

        Mixed views (Favorites "All", History, Watch Later, Watched) share one
        mode for every row, so the row's own tag decides there. A History
        movie row is rebuilt with the fields a favorite snapshot needs."""
        content_kind = self._content_kind()
        # Without this a movie row in "All favorites" fell into the CHANNEL
        # branch: "Remove from favorites" removed from the channel store (a
        # no-op for the movie), and it was offered channel-only actions.
        if content_kind == "fav":
            rk = it.get("_kind") or it.get("_ekind")
            if rk in ("vod", "movie"):
                content_kind = "vod"
            elif rk == "series":
                content_kind = "series"
        elif content_kind in ("history", "watchlist", "watched"):
            rk = it.get("_kind") or it.get("_ekind")
            if rk in ("vod", "movie"):
                content_kind = "vod"
                # History movie rows carry the provider id only as _key and
                # the container extension only inside the stored URL.
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
        return content_kind, it

    def _row_section(self, it) -> str | None:
        """The favorites section ('chan', 'movie', 'series') a row is filed
        in, or None for rows that cannot be a favorite (headers, episodes)."""
        if not it or it.get("_header") or self.series_ctx:
            return None
        kind, it = self._row_content_kind(it)
        section = _SECTION.get(kind)
        store = self._fav_store(section)
        if store is None or it.get(store.id_key) is None:
            return None
        return section

    def _fav_batch(self, row: int, it: dict, kind: str) -> list[dict]:
        """What a context-menu action applies to: every selected row of the
        clicked row's kind when that row is part of a multi-selection,
        otherwise the clicked row alone."""
        rows = sorted(ix.row()
                      for ix in self.listw.selectionModel().selectedRows())
        section = _SECTION.get(kind)
        if len(rows) < 2 or row not in rows or section is None:
            return [it]
        id_key = self._fav_store(section).id_key
        batch = []
        for r in rows:
            if r == row:
                batch.append(it)
                continue
            other = self.list_model.item_at(r)
            if not other or other.get("_header"):
                continue
            k, other = self._row_content_kind(other)
            if _SECTION.get(k) == section and other.get(id_key) is not None:
                batch.append(other)
        return batch

    def _fav_view_group(self, section: str) -> str | None:
        """The folder on screen when it belongs to *section*, else None."""
        cur = self.cat_list.currentItem() if self.mode == "fav" else None
        data = cur.data(Qt.ItemDataRole.UserRole) if cur else None
        if isinstance(data, tuple) and data[0] == section:
            return data[1]
        return None

    def _fav_source_group(self, section: str) -> str:
        """The folder a move takes rows out of: the one on screen, or the
        default bucket when the view merges folders."""
        return self._fav_view_group(section) or FAV_DEFAULT_GROUP

    def _ask_folder_name(self) -> str | None:
        name, ok = QInputDialog.getText(
            self, tr("ctx_new_folder"), tr("prompt_folder_name"))
        name = (name or "").strip()
        if not ok or not name or name == FAV_DEFAULT_GROUP:
            return None
        return name

    # -- bulk actions -------------------------------------------------------

    def _fav_add_many(self, section: str, items: list[dict],
                      group: str | None) -> None:
        """File *items* into *group* (None asks for a new folder name)."""
        if group is None:
            group = self._ask_folder_name()
            if group is None:
                return
        if section == "chan":
            for it in items:
                self.favs.add(group, it)
        else:
            for it in items:
                self._toggle_media_fav(it, section, True, group=group)
        if self.mode == "fav":
            self._load_categories()
        else:
            self.list_model.refresh_all()

    def _fav_remove_many(self, section: str, items: list[dict]) -> None:
        if section != "chan":
            for it in items:
                self._toggle_media_fav(it, section, False)
            return
        # In Favorites a channel leaves only the folder on screen; elsewhere
        # it stops being a favorite altogether.
        group = self._fav_view_group("chan") if self.mode == "fav" else None
        for it in items:
            self.favs.remove(it.get("stream_id"), group)
        if self.mode == "fav":
            self._load_categories()
        else:
            self.list_model.refresh_all()

    def _fav_move(self, section: str, items: list[dict],
                  target: str | None) -> None:
        """Move *items* out of the folder on screen into *target* (None asks
        for a new folder name)."""
        if target is None:
            target = self._ask_folder_name()
            if target is None:
                return
        store = self._fav_store(section)
        if store is None or not items:
            return
        store.move(items, target, self._fav_source_group(section))
        self._load_categories()

    def _add_fav_move_menu(self, menu, section: str, items: list[dict],
                           suffix: str = "") -> None:
        """'Move to ▸' in the Favorites view: the other folders of the
        section, the section itself (out of any folder) and a new folder."""
        if self.mode != "fav" or self._fav_section not in (section, "all"):
            return
        store = self._fav_store(section)
        here = self._fav_source_group(section)
        sub = menu.addMenu(tr("ctx_move_to") + suffix)
        if here != FAV_DEFAULT_GROUP:
            sub.addAction(tr(_SECTION_LABEL[section]),
                          lambda: self._fav_move(section, items,
                                                 FAV_DEFAULT_GROUP))
        targets = [g for g in store.custom_groups() if g != here]
        for g in targets:
            sub.addAction(g, lambda g=g: self._fav_move(section, items, g))
        if targets or here != FAV_DEFAULT_GROUP:
            sub.addSeparator()
        sub.addAction(tr("ctx_new_folder"),
                      lambda: self._fav_move(section, items, None))

    # -- drag and drop ------------------------------------------------------

    def _fav_drag_start(self, rows: list[int]) -> str | None:
        """Called by the list when a drag begins. Remembers what is dragged
        and returns the text for the drag badge, or None to refuse."""
        self._fav_drag_items = []
        if (self.mode != "fav" or self.series_ctx
                or self._fav_section not in ("chan", "movie", "series",
                                             "all")):
            return None
        picked = []
        for r in rows:
            it = self.list_model.item_at(r)
            section = self._row_section(it)
            if section:
                picked.append((section, self._row_content_kind(it)[1]))
        if not picked:
            return None
        # The press that began the drag also selected a row, which arms the
        # live preview; dragging a channel must not switch what is playing.
        self._preview_timer.stop()
        self._fav_drag_items = picked
        first = self.channel_display_name(picked[0][1])
        return first if len(picked) == 1 else f"{first}  +{len(picked) - 1}"

    def _fav_drag_finish(self) -> None:
        self._fav_drag_items = []

    def _fav_drop_allowed(self, data) -> bool:
        """May the rows being dragged be dropped on this sidebar row?"""
        if (not self._fav_drag_items or self.mode != "fav"
                or not isinstance(data, tuple)):
            return False
        section, group = data
        if self._fav_store(section) is None:
            return False
        if not any(s == section for s, _ in self._fav_drag_items):
            return False
        # Dropping rows where they already are would do nothing.
        return (group or FAV_DEFAULT_GROUP) != self._fav_source_group(section)

    def _fav_drop_on_folder(self, data) -> None:
        if not self._fav_drop_allowed(data):
            return
        section, group = data
        items = [it for s, it in self._fav_drag_items if s == section]
        self._fav_store(section).move(
            items, group or FAV_DEFAULT_GROUP, self._fav_source_group(section))
        self._fav_drag_items = []
        # Not from inside the sidebar's own drop handler: the reload clears
        # the very list that is still delivering the drop.
        QTimer.singleShot(0, self._load_categories)

    def _fav_reorder_target(self):
        """(store, folder) the middle list can be reordered in, or None.

        Only a list that IS one folder in its stored order qualifies: a sort
        other than the default shows a different order, and the section row
        merges every folder unless there are none."""
        if self.mode != "fav" or self.series_ctx:
            return None
        section = self._fav_section
        store = self._fav_store(section)
        if store is None or self._current_sort_order() != "default":
            return None
        group = self._fav_view_group(section)
        if group is None:
            if store.custom_groups():
                return None
            group = FAV_DEFAULT_GROUP
        return store, group

    def _fav_can_reorder(self) -> bool:
        if not self._fav_drag_items or self._fav_reorder_target() is None:
            return False
        return any(s == self._fav_section for s, _ in self._fav_drag_items)

    def _fav_reorder_drop(self, row: int) -> None:
        """Move the dragged rows to sit before list row *row*."""
        target = self._fav_reorder_target()
        if target is None:
            return
        store, group = target
        id_key = store.id_key
        moving = [it.get(id_key) for s, it in self._fav_drag_items
                  if s == self._fav_section]
        self._fav_drag_items = []
        if not moving:
            return
        skip = set(moving)
        shown = [self.list_model.item_at(r)
                 for r in range(self.list_model.rowCount())]
        head = [x.get(id_key) for x in shown[:row]
                if x and x.get(id_key) not in skip]
        tail = [x.get(id_key) for x in shown[row:]
                if x and x.get(id_key) not in skip]
        store.reorder(group, head + moving + tail)
        QTimer.singleShot(0, lambda: self._fav_reload_selecting(moving))

    def _fav_reload_selecting(self, idents: list) -> None:
        """Reload the favorites list and select the rows that just moved -
        without starting the live preview, as a reorder is no channel change."""
        cur = self.cat_list.currentItem()
        self._load_items(cur.data(Qt.ItemDataRole.UserRole) if cur else None)
        store = self._fav_store(self._fav_section)
        if store is None:
            return
        wanted = {str(i) for i in idents}
        sm = self.listw.selectionModel()
        first = None
        self._rmb_selecting = True
        try:
            sm.clearSelection()
            for r in range(self.list_model.rowCount()):
                it = self.list_model.item_at(r)
                if it and str(it.get(store.id_key)) in wanted:
                    idx = self.list_model.index(r)
                    sm.select(idx, QItemSelectionModel.SelectionFlag.Select
                              | QItemSelectionModel.SelectionFlag.Rows)
                    if first is None:
                        first = idx
            if first is not None:
                sm.setCurrentIndex(
                    first, QItemSelectionModel.SelectionFlag.NoUpdate)
        finally:
            self._rmb_selecting = False
