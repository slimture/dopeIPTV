"""MainWindow mixin: the Local files place (ui/local_place.py draws it).

The roots stay in the left column as they were (the registered folders,
the shares the system mounted, "+ Add folder…"); the middle pane shows,
where the list was, the shared form of every place: a way back to the
folder above, the folder's name ("Local files" at the top) with how many
folders and files it holds, the filter, Rescan (F5 - its tooltip names the
key in force), the library's Grid / List (remembered, ``local_library_view``)
and the Folders / Library switch (remembered as ever, ``local_view``);
"Continue" - the files in progress under the folder shown, from the resume
points the app keeps, never a disk read for it; then the content: a
folder's folders and files as rows (with a file's size and, once played,
its length; a music folder with its cover as the app finds it), the
library as posters or rows (series, films, folders and music shelves, as
the library sorts them, with the covers it finds).

The list under it is the source of truth and keeps doing all it did
(ui/mw_local.py): a folder read in slices on the search's own threads
(the cards put in as its rows come, never a reset under the user), the
search through the subfolders, the library's walk and its cache, the art
found on the pool, the queue a file plays on into (its folder's), the
right-click menus, the selection the detail pane follows. The place shows
what the list holds, card for card: a reset drawn again (the same rows:
the cards as they are, the scroll and the choice kept), rows put in or
taken out where they are, the rows drawn again redressed.

A double click or Enter plays a file (through its folder's queue) or
opens a folder, a library's series opens its page (ui/mw_series_page.py);
Back, the mouse's back button and Backspace go up one folder. Continue
plays from where it got to without the resume question; the place stays.

The place answers mw_places' hooks: ``_place_local_show`` (the sidebar's
entry, Back from a page over it) and ``_place_local_refresh`` (a profile,
a source, a sync). What changes on disk shows by itself (ui/mw_local_watch.py).
"""

from __future__ import annotations

import math
import os
from functools import partial

from PyQt6 import sip
from PyQt6.QtCore import QItemSelection, QItemSelectionModel, QPoint, Qt, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QMenu

from ..core import film_view as fv
from ..core import local_listing as local_order
from ..core.log import log
from ..i18n import tr
from .list_place import line_of
from .local_place import LocalCard, LocalPlace
from .series_cards import minutes_text
from .theme import P

#: The library's grid or list ("poster" / "list"), remembered.
VIEW_KEY = "local_library_view"
VIEWS = ("poster", "list")
#: The most files on Continue.
CONTINUE_MAX = 12
#: The detail pane follows the card the keyboard rests on after this long.
SELECT_SYNC_MS = 250
_JUNK_ICONS = frozenset({"", "n/a", "na", "null", "none", "0", "-", "false"})


def size_text(n: int) -> str:
    """A file's size for its line: "734 MB", "1.4 GB"."""
    from ..core.recording import format_size
    return format_size(n) if n > 0 else ""


#: The sort menu's words for each order (core/local_listing.ORDERS).
ORDER_WORDS = {local_order.NAME: "sort_az", local_order.NAME_DESC: "sort_za",
               local_order.MODIFIED: "local_sort_modified", local_order.OLDEST: "rec_sort_oldest",
               local_order.LARGEST: "rec_sort_largest", local_order.TYPE: "local_sort_type"}

class _PlaceLocalMixin:
    """MainWindow mixin: the Local files place."""

    _lp_page: LocalPlace | None = None
    #: The model row of each card, or None: one card per row, in order.
    _lp_rows: list[int] | None = None
    _lp_pending = False
    _lp_syncing = False
    #: What the cards are of (the view, root, folder, series, search), to
    #: keep the scroll when it is the same again.
    _lp_ctx: tuple | None = None
    _lp_sel_timer: QTimer | None = None
    _lp_dress_timer: QTimer | None = None
    _lp_cont_sig: tuple | None = None
    _local_folder_gone = False

    # -- where it is ------------------------------------------------------------------

    @property
    def _local_rescan_place_btn(self):
        """The place's Rescan, whose tooltip names the key in force
        (mw_shortcuts' _KEY_TIPS); None before the place is made."""
        place = self._lp_page
        return place.rescan if place is not None and not sip.isdeleted(place) else None

    def _place_local_active(self) -> bool:
        """The place is where the list is (it may be under another page)."""
        place, stack = self._lp_page, getattr(self, "_list_stack", None)
        return (place is not None and stack is not None and not sip.isdeleted(place)
                and stack.currentWidget() is place)

    def _place_local_showing(self) -> bool:
        """...and the middle pane shows it (not a series' page, the Guide,
        Search over it)."""
        return (self._place_local_active()
                and self._mid_stack.currentWidget() is self._list_page)

    def _place_local_show(self) -> bool:
        """The Local files place in the middle pane: mw_places' hook for
        the sidebar's entry (``_show_place``) and for Back from a page over
        it (``_return_to_place``) - the place as it was left. True."""
        self._close_guide_page()
        self._close_series_page()
        self._close_search_page()
        if getattr(self, "_focus_mode", False):
            self._set_focus_mode(False)
        if self.mode != "local":
            self.switch_mode("local")
        else:
            self._place_local_sync()
        entered = getattr(self, "_place_entered", None)
        if callable(entered):
            entered("local")
        may = getattr(self, "_place_series_may_focus", None)
        if self._place_local_showing() and (may is None or may()):
            self._lp_page.view.setFocus(Qt.FocusReason.OtherFocusReason)
        return True

    def _place_local_sync(self) -> None:
        """switch_mode: the place where the list is while Local files is the
        mode, the list itself otherwise; what is watched on disk with it."""
        stack = getattr(self, "_list_stack", None)
        if stack is None:
            return
        if self.mode == "local":
            place = self._ensure_local_place()
            if stack.currentWidget() is not place:
                stack.setCurrentWidget(place)
            self._lp_rebuild()
        elif self._lp_page is not None and stack.currentWidget() is self._lp_page:
            stack.setCurrentWidget(self._list_body)
        watch = getattr(self, "_lw_sync", None)
        if callable(watch):
            watch()

    def _ensure_local_place(self) -> LocalPlace:
        place = self._lp_page
        if place is not None and not sip.isdeleted(place):
            return place
        place = LocalPlace(self.logos)
        v = place.view
        v.dress_fn = self._lp_dress
        v.activated.connect(self._lp_activate)
        v.menu.connect(self._lp_menu)
        v.current_item_changed.connect(self._lp_current)
        v.selectionModel().selectionChanged.connect(self._lp_selected)
        place.back_requested.connect(self._lp_back)
        place.add_requested.connect(self._local_add_folder)
        place.rescan.clicked.connect(self._local_rescan)
        place.header.view_changed.connect(self._lp_view_picked)
        place.header.sort_changed.connect(self._lp_sort_picked)
        place.layout_changed.connect(self._lp_layout_picked)
        place.cont.strip.click_plays = self._autoplay_preview   # as the lists
        place.cont.activated.connect(self._lp_continue)
        place.cont.menu.connect(self._lp_continue_menu)
        # Continue hidden from its heading (mw_place_look); the header
        # compact while Just the list.
        place.cont.set_hide_key("local_continue")
        place.cont.hide_requested.connect(self._place_row_hide)
        self._place_look_adopt(place)
        place.filter.setText(self.search.text())
        place.filter.textChanged.connect(self._lp_filter)
        self.search.textChanged.connect(self._lp_search_changed)
        model = self.list_model
        model.modelReset.connect(self._lp_soon)
        model.rowsInserted.connect(self._lp_inserted)
        model.rowsRemoved.connect(self._lp_removed)
        model.dataChanged.connect(self._lp_data_changed)
        self.listw.selectionModel().currentChanged.connect(self._lp_list_moved)
        self._mid_stack.currentChanged.connect(self._lp_stack_changed)
        t = QTimer(self)
        t.setSingleShot(True)
        t.setInterval(SELECT_SYNC_MS)
        t.timeout.connect(self._lp_mirror)
        self._lp_sel_timer = t
        d = QTimer(self)
        d.setSingleShot(True)
        d.setInterval(60)
        d.timeout.connect(self._lp_redress)
        self._lp_dress_timer = d
        self._list_stack.addWidget(place)
        self._lp_page = place
        self._lp_icons()
        return place

    def _lp_icons(self) -> None:
        place = self._lp_page
        if place is None:
            return
        try:
            place.rescan.setIcon(QIcon(self._action_pixmap("refresh", 15, P["text2"])))
        except Exception:
            pass
        btn = getattr(self, "local_rescan_btn", None)
        tip = btn.toolTip() if btn is not None else tr("local_rescan_tip")
        place.rescan.setToolTip(tip)

    # -- the cards: what the list holds -------------------------------------------------

    def _lp_soon(self, *_a) -> None:
        """The list's model was reset: the cards follow a moment later (one
        rebuild for a burst)."""
        if self._lp_page is None or self._lp_pending:
            return
        self._lp_pending = True
        QTimer.singleShot(0, self._lp_rebuild)

    def _lp_context(self) -> tuple:
        return (self._local_view(), getattr(self, "_current_cat", None),
                getattr(self, "_local_ctx", None), getattr(self, "_local_series", None),
                self._local_search_text())

    def _lp_library_top(self) -> bool:
        return (self._local_view() == "series" and not getattr(self, "_local_ctx", None)
                and not getattr(self, "_local_series", None))

    def _lp_layout(self) -> tuple[str, str]:
        """(layout, a row's picture): the library as posters or rows, a
        folder (and a search's matches) as rows with a still's picture."""
        if self._lp_library_top():
            v = str(self.settings.value(VIEW_KEY, "poster") or "poster")
            return (v if v in VIEWS else "poster"), "poster"
        return "list", "wide"

    @staticmethod
    def _lp_card(it: dict) -> LocalCard:
        title = it.get("name") or os.path.basename(str(it.get("_path") or "").rstrip(os.sep))
        return LocalCard(key=str(it.get("_key") or it.get("_path") or ""), title=str(title or ""),
                         data=it)

    def _lp_rebuild(self) -> None:
        self._lp_pending = False
        place = self._lp_page
        if place is None or sip.isdeleted(place) or not self._place_local_active():
            return
        model = self.list_model
        rows = model.items()
        cards: list[LocalCard] = []
        row_of: list[int] = []
        heads = False
        for i, it in enumerate(rows):
            if not isinstance(it, dict):
                continue
            if it.get("_header"):
                heads = True
                continue
            cards.append(self._lp_card(it))
            row_of.append(i)
        self._lp_rows = row_of if heads or len(row_of) != len(rows) else None
        view = place.view
        ctx = self._lp_context()
        same = ctx == self._lp_ctx
        bar = place.area.verticalScrollBar() if place.area is not None else None
        scroll = bar.value() if bar is not None else 0
        old = view.items()
        self._lp_syncing = True
        try:
            place.set_layout(*self._lp_layout())
            if len(old) == len(cards) and all(a.key == b.key for a, b in zip(old, cards,
                                                                               strict=True)):
                # The same rows (Rescan with nothing new, the library walked
                # again): the cards as they are, the scroll and choice kept.
                for a, b in zip(old, cards, strict=True):
                    a.data, a.title = b.data, b.title
                view.redress()
            else:
                view.set_items(cards)
        finally:
            self._lp_syncing = False
        self._lp_ctx = ctx
        if bar is not None:
            if same:
                QTimer.singleShot(0, partial(self._lp_scroll_to, scroll))
            else:
                place.scroll_to_top()
        self._lp_follow_list(reveal=not same)
        self._lp_head()
        self._lp_rows_refresh()
        self._lp_mark()
        watch = getattr(self, "_lw_sync", None)
        if callable(watch):
            watch()

    def _lp_scroll_to(self, value: int) -> None:
        place = self._lp_page
        if place is not None and not sip.isdeleted(place) and place.area is not None:
            place.area.verticalScrollBar().setValue(value)

    def _lp_card_of(self, row: int) -> int:
        """The card of model row *row*, -1 for none (a heading)."""
        rows = self._lp_rows
        if rows is None:
            return row
        try:
            return rows.index(row)
        except ValueError:
            return -1

    def _lp_row_of(self, card_row: int) -> int:
        rows = self._lp_rows
        if rows is None:
            return card_row
        return rows[card_row] if 0 <= card_row < len(rows) else -1

    def _lp_inserted(self, _parent, first: int, last: int) -> None:
        """Rows put in where they are (a folder's next slice, a search's
        matches, a file added on disk): their cards likewise - the scroll and
        the choice untouched."""
        place = self._lp_page
        if place is None or self._lp_pending or not self._place_local_active():
            return                      # a rebuild on its way draws them
        model, view = self.list_model, place.view
        n = last - first + 1
        if self._lp_rows is None and view.model_.rowCount() + n == model.rowCount():
            new = [model.item_at(r) for r in range(first, last + 1)]
            if all(isinstance(it, dict) and not it.get("_header") for it in new):
                self._lp_syncing = True
                try:
                    view.insert_items(first, [self._lp_card(it) for it in new])
                finally:
                    self._lp_syncing = False
                self._lp_head()
                self._lp_mark()
                return
        self._lp_soon()

    def _lp_removed(self, _parent, first: int, last: int) -> None:
        place = self._lp_page
        if place is None or self._lp_pending or not self._place_local_active():
            return
        model, view = self.list_model, place.view
        n = last - first + 1
        if self._lp_rows is None and view.model_.rowCount() - n == model.rowCount():
            self._lp_syncing = True
            try:
                view.remove_rows(range(first, last + 1))
            finally:
                self._lp_syncing = False
            self._lp_head()
            return
        self._lp_soon()

    def _lp_data_changed(self, *_a) -> None:
        """The list's rows drawn again (art found, tags read): the cards
        too, a moment later, once."""
        if self._lp_dress_timer is not None and self._place_local_active():
            self._lp_dress_timer.start()

    def _lp_redress(self) -> None:
        place = self._lp_page
        if place is None or sip.isdeleted(place) or not self._place_local_active():
            return
        place.view.redress()
        self._lp_rows_refresh()

    # -- what a card says ----------------------------------------------------------------

    def _lp_image(self, it: dict) -> str:
        icon = str(it.get("stream_icon") or "").strip()
        if icon.lower() in _JUNK_ICONS:
            icon = ""
        if not icon and it.get("_kind") in ("localdir", "localalbum", "localcollection"):
            icon = self._folder_tile()
        return icon

    def _lp_dress(self, card: LocalCard) -> None:
        """A card's picture, line, pills and how far it got - worked out as
        it comes on screen."""
        it = card.data if isinstance(card.data, dict) else {}
        card.title = str(it.get("name") or card.title)
        card.image = self._lp_image(it)
        card.line = self._lp_line(it)
        pills: list = []
        card.progress = None
        if it.get("_kind") == "local":
            pos, dur = self._lp_resume(card.key)
            if pos > 60 and dur > pos:
                card.progress = pos / dur
            if fv.is_new(it):
                pills.append("new")
        card.pills = tuple(pills)

    def _lp_resume(self, key: str) -> tuple[float, float]:
        try:
            pos, dur, _at = self.resume.progress(key, "local")
            return float(pos or 0), float(dur or 0)
        except Exception:
            return 0.0, 0.0

    def _lp_line(self, it: dict) -> str:
        """The muted line under a card's name: where a search's match is,
        what a folder holds, a file's artist and album, length and size."""
        kind = it.get("_kind")
        parts: list[str] = [str(it.get("_sub") or "")]
        if kind == "localalbum":
            parts.append(str(it.get("_desc") or ""))
        elif kind == "localcollection":
            try:
                parts.append(tr("count_files", n=int(it.get("_desc") or 0)))
            except (TypeError, ValueError):
                parts.append(str(it.get("_desc") or ""))
        elif kind == "localseries":
            eps = (getattr(self, "_local_series_index", None) or {}).get(
                it.get("_series_title") or "")
            if eps:
                parts.append(tr("count_episodes", n=len(eps)))
        elif kind == "local":
            music = [str(it.get(k) or "") for k in ("_artist", "_album")]
            parts += music
            _pos, dur = self._lp_resume(str(it.get("_key") or ""))
            if dur > 0:
                parts.append(minutes_text(max(1, math.ceil(dur / 60))))
            try:
                parts.append(size_text(int(it.get("_size") or 0)))
            except (TypeError, ValueError):
                pass
        return line_of(parts)

    # -- the header ----------------------------------------------------------------------

    def _lp_root(self) -> str:
        """The folder of Local files the sidebar has picked; "" for none -
        no folder added yet, or another place's category still the window's
        (a TV category "1" was shown as the folder, and no "Add folder")."""
        cat = getattr(self, "_current_cat", None)
        if not isinstance(cat, str) or not cat or cat == "__add__":
            return ""
        cur = self.cat_list.currentItem() if hasattr(self, "cat_list") else None
        if cur is None or cur.data(Qt.ItemDataRole.UserRole) != cat:
            return ""
        return cat

    def _lp_root_label(self) -> str:
        cur = self.cat_list.currentItem() if hasattr(self, "cat_list") else None
        root = self._lp_root()
        if cur is not None and cur.data(Qt.ItemDataRole.UserRole) == root:
            return cur.text().strip()
        return os.path.basename(root.rstrip(os.sep)) or root

    def _lp_where(self) -> tuple[str, str | None]:
        """(the title, the way back's words or None at the top)."""
        top = tr("nav_local")
        series = getattr(self, "_local_series", None)
        if series:
            return str(series), tr("fp_back_to", name=top)
        ctx = getattr(self, "_local_ctx", None)
        root = self._lp_root()
        if ctx and not (root and os.path.normpath(ctx) == os.path.normpath(root)):
            name = os.path.basename(str(ctx).rstrip(os.sep)) or str(ctx)
            parent = os.path.dirname(str(ctx).rstrip(os.sep))
            up = top if (not root or os.path.normpath(parent) == os.path.normpath(root)
                         or not os.path.normpath(parent).startswith(os.path.normpath(root))) \
                else (os.path.basename(parent) or top)
            return name, tr("fp_back_to", name=up)
        return top, None

    def _lp_deep(self) -> bool:
        return bool(getattr(self, "_local_ctx", None) or getattr(self, "_local_series", None))

    def _lp_count(self) -> str:
        """The count line: the root's name at the top, then what it holds -
        a search says what it found."""
        if self._local_search_text() and self._local_walk_mode():
            return getattr(self, "_local_search_said", "") or tr("local_searching")
        place = self._lp_page
        cards = place.view.items() if place is not None else []
        kinds: dict[str, int] = {}
        home = 0
        for c in cards:
            it = c.data if isinstance(c.data, dict) else {}
            k = str(it.get("_kind") or "")
            kinds[k] = kinds.get(k, 0) + 1
            home += 1 if it.get("_home") else 0
        parts: list[str] = []
        if not self._lp_deep() and self._lp_root():
            parts.append(self._lp_root_label())
        if self._lp_library_top():
            for n, key in ((kinds.get("localseries", 0), "count_series"),
                           (kinds.get("local", 0), "count_movies")):
                if n:
                    parts.append(tr(key, n=n))
            folders = kinds.get("localalbum", 0) + kinds.get("localcollection", 0) \
                + kinds.get("localdir", 0)
            if folders:
                parts.append(tr("local_n_folders", n=folders))
        else:
            dirs = kinds.get("localdir", 0) + kinds.get("localalbum", 0) \
                + kinds.get("localcollection", 0)
            if dirs:
                parts.append(tr("local_n_folders", n=dirs))
            if kinds.get("local", 0):
                parts.append(tr("count_files", n=kinds["local"]))
        return " · ".join(p for p in parts if p)

    def _lp_sort_picked(self, order: str) -> None:
        self._local_sort_picked(order)
        self._lp_head()

    def _lp_head(self) -> None:
        place = self._lp_page
        if place is None or sip.isdeleted(place):
            return
        title, back = self._lp_where()
        h = place.header
        h.set_title(title)
        h.set_count(self._lp_count())
        views = [("folders", tr("local_place_folders")), ("series", tr("local_place_library"))]
        if views != [(k, b.text()) for k, b in zip(h.switch.keys(), h.switch.buttons,
                                                    strict=False)]:
            h.set_views(views, self._local_view())
        else:
            h.set_view_key(self._local_view())
        h.switch.setAccessibleName(tr("nav_local"))
        place.set_back(back)
        lib = self._lp_library_top()
        # The order every folder is in, its button naming it (in the library
        # the last changed first is the newest added); a search keeps its own.
        opts = [] if self._local_search_text() else [
            (k, tr(ORDER_WORDS[k] if not (lib and k == local_order.MODIFIED) else "option_sort_recent"))
            for k in local_order.ORDERS]
        order = self._local_order()
        if opts != h._sort_opts:
            h.set_sort(opts, order)
        else:
            h.set_sort_key(order)
        place.show_layout_switch(lib, self._lp_layout()[0] if lib else "")
        if place.filter.text() != self.search.text():
            self._lp_put_filter(self.search.text())
        hint = self.search.placeholderText() or tr("list_filter_placeholder")
        if place.filter.placeholderText() != hint:
            place.filter.setPlaceholderText(hint)
        self._lp_note()

    def _lp_note(self) -> None:
        place = self._lp_page
        if place is None:
            return
        if place.view.model_.rowCount():
            place.set_note("")
            return
        no_roots = not self._lp_root() and not any(
            isinstance(self.cat_list.item(i).data(Qt.ItemDataRole.UserRole), str)
            and self.cat_list.item(i).data(Qt.ItemDataRole.UserRole) != "__add__"
            for i in range(self.cat_list.count()))
        if no_roots:
            place.set_note(tr("local_add_hint"), add=True)
            return
        if getattr(self, "_local_listing", None) is not None \
                or getattr(self, "_local_walk", None) is not None:
            place.set_note("")          # still reading: the activity line says so
            return
        if getattr(self, "_local_folder_gone", False) and not self._local_search_text():
            place.set_note(tr("local_gone"))
            return
        readout = getattr(self, "_list_readout", None)
        text = readout[1] if readout and readout[0] == "local" else ""
        if self._local_search_text() and self._local_walk_mode():
            text = getattr(self, "_local_search_said", "") or text
        if getattr(self, "_local_scan_active", False) and not text:
            text = tr("local_scanning")
        place.set_note(text or tr("local_empty"))

    def _lp_view_picked(self, key: str) -> None:
        """Folders or Library in the header: the switch it always was."""
        if key in ("folders", "series") and key != self._local_view():
            self._local_toggle_view()
        self._lp_head()

    def _lp_layout_picked(self, key: str) -> None:
        if key not in VIEWS:
            return
        self.settings.setValue(VIEW_KEY, key)
        place = self._lp_page
        if place is None:
            return
        cur = place.view.current_item()
        place.set_layout(*self._lp_layout())
        if cur is not None:
            QTimer.singleShot(0, lambda k=cur.key: self._lp_page is not None
                              and self._lp_page.view.focus_key(k, take_focus=False))

    def _lp_filter(self, text: str) -> None:
        """Typed in the place's filter: the list's, which filters (and in a
        folder looks through its subfolders) as it always did."""
        if self.search.text() != text:
            self.search.setText(text)

    def _lp_search_changed(self, text: str) -> None:
        place = self._lp_page
        if place is None or sip.isdeleted(place) or place.filter.text() == text:
            return
        self._lp_put_filter(text)

    def _lp_put_filter(self, text: str) -> None:
        f = self._lp_page.filter
        f.blockSignals(True)
        try:
            f.setText(text)
        finally:
            f.blockSignals(False)

    # -- Continue ------------------------------------------------------------------------

    def _lp_continue_cards(self) -> list[LocalCard]:
        """The files in progress under the folder shown, newest first: from
        the resume points the app keeps, named from the list or History -
        nothing read from the disk."""
        base = getattr(self, "_local_ctx", None) or self._lp_root()
        if not base:
            return []
        head = os.path.normpath(base).rstrip(os.sep) + os.sep
        try:
            points = list(self.resume.positions("local"))
        except Exception:
            return []
        listed = {r.get("_key"): r for r in (getattr(self, "all_items", None) or [])
                  if isinstance(r, dict) and r.get("_kind") == "local"}
        hist: dict = {}
        out: list[LocalCard] = []
        for key, pos, dur, _ts in sorted(points, key=lambda t: t[3], reverse=True):
            path = str(key)
            if not os.path.normpath(path).startswith(head) or pos <= 60 or dur <= pos:
                continue
            row = listed.get(path)
            if row is None:
                if not hist:
                    hist = {h.get("_key"): h for h in self.history.items()
                            if isinstance(h, dict) and h.get("_kind") == "local"} or {None: {}}
                row = hist.get(path) or {}
            from .mw_local import clean_title
            stem = os.path.splitext(os.path.basename(path))[0]
            title = str(row.get("name") or clean_title(stem)[0] or stem)
            icon = self._lp_image(row) if row else ""
            data = {"_path": path, "_key": path, "_kind": "local", "name": title,
                    "stream_icon": icon, "_resume_pos": pos, "_resume_dur": dur}
            if not self._kids_only([data], "local"):
                continue
            left = max(1, math.ceil((dur - pos) / 60))
            out.append(LocalCard(key=path, title=title, line=tr("home_min_left", n=left),
                                 image=icon, progress=pos / dur, data=data))
            if len(out) >= CONTINUE_MAX:
                break
        return out

    def _lp_rows_refresh(self) -> None:
        """Continue: under a folder or the library, not over a search - nor
        hidden from its heading, nor with Just the list (mw_place_look)."""
        place = self._lp_page
        if place is None or sip.isdeleted(place):
            return
        shown = getattr(self, "_place_row_shown", None)
        hidden = callable(shown) and not shown("local_continue")
        try:
            cards = [] if hidden or self._local_search_text() else self._lp_continue_cards()
        except Exception:
            log.exception("local files: Continue was not shown")
            cards = []
        sig = tuple((c.key, c.title, c.image, round(c.progress or 0, 3)) for c in cards)
        if sig == self._lp_cont_sig and place.cont.isHidden() == (not cards):
            return
        self._lp_cont_sig = sig
        bar = place.area.verticalScrollBar() if place.area is not None else None
        v = bar.value() if bar is not None else 0
        y0 = place.view.y()
        place.cont.set_cards(cards)
        if bar is not None and v > 0:
            lay = place.body.layout()
            if lay is not None:
                lay.activate()
            dy = place.view.y() - y0
            if dy:
                bar.setValue(v + dy)

    def _lp_continue(self, card) -> None:
        """A file of Continue: from where it got to, without the resume
        question - through its folder's queue when the folder is the one
        shown; the place stays."""
        it = getattr(card, "data", None)
        if not isinstance(it, dict):
            return
        path = str(it.get("_path") or "")
        if not path:
            return
        if not os.path.isfile(path):
            idx = self._choice_dialog(tr("nav_local"), tr("local_missing"),
                                      [(tr("ctx_continue_remove"), "normal"),
                                       (tr("common_cancel"), "primary")])
            if idx == 0:
                self._lp_forget(path)
            return
        listed = next((r for r in (self.all_items or []) if isinstance(r, dict)
                       and r.get("_key") == path and r.get("_kind") == "local"), None)
        if listed is not None:
            self._lp_select_key(path)
        self._start_at_once(path, "local")
        try:
            with self._from_pane():
                self.play_item(listed or it)
        finally:
            self._sp_start_once = None

    def _lp_continue_menu(self, card, pos: QPoint) -> None:
        it = getattr(card, "data", None)
        if not isinstance(it, dict):
            return
        path = str(it.get("_path") or "")
        m = QMenu(self)
        m.addAction(tr("card_continue"), partial(self._lp_continue, card))
        m.addAction(tr("resume_restart"), partial(self._lp_play_from_start, it))
        m.addAction(tr("local_show_folder"), partial(self._local_reveal, path))
        m.addSeparator()
        m.addAction(tr("ctx_continue_remove"), partial(self._lp_forget, path))
        m.exec(pos)

    def _lp_play_from_start(self, it: dict) -> None:
        path = str(it.get("_path") or "")
        if not path or not os.path.isfile(path):
            return
        self._sp_start_once = (path, 0.0)
        try:
            with self._from_pane():
                self.play_item(it)
        finally:
            self._sp_start_once = None

    def _lp_forget(self, path: str) -> None:
        self.resume.clear("local", path)
        self._lp_cont_sig = None
        self._lp_rows_refresh()
        if self._lp_page is not None:
            self._lp_page.view.redress()

    # -- what the cards do ---------------------------------------------------------------

    def _lp_activate(self, card) -> None:
        """A double click, or Enter: a file plays (through its folder's
        queue), a folder opens, a library's series opens its page."""
        it = getattr(card, "data", None)
        if not isinstance(it, dict):
            return
        if self._lp_sel_timer is not None:
            self._lp_sel_timer.stop()
        self._lp_mirror()
        with self._from_pane():
            self.play_item(it)

    def _lp_menu(self, card, pos: QPoint) -> None:
        """A card's menu: the list's own for its row - for every card
        selected with it (the queue takes them all)."""
        it = getattr(card, "data", None)
        if not isinstance(it, dict):
            return
        if self._lp_sel_timer is not None:
            self._lp_sel_timer.stop()
        self._lp_mirror()
        with self._from_pane():
            self._local_context_menu(None, it, at=pos)
        self._lp_rows_refresh()

    def _lp_back(self) -> None:
        """Back, the mouse's back button, Backspace: one folder up."""
        if self._lp_deep() and self.mode == "local":
            self._local_up()

    def _lp_current(self, _card) -> None:
        if not self._lp_syncing and self._lp_sel_timer is not None:
            self._lp_sel_timer.start()

    def _lp_selected(self, *_a) -> None:
        if not self._lp_syncing and self._lp_sel_timer is not None:
            self._lp_sel_timer.start()

    def _lp_mirror(self) -> None:
        """The card picked - and every card gathered - picked in the list,
        whose menu, queue and detail panel act on them."""
        place = self._lp_page
        if place is None or sip.isdeleted(place) or not self._place_local_active():
            return
        view, model = place.view, self.list_model
        sel = QItemSelection()
        for ix in view.selectionModel().selectedIndexes():
            r = self._lp_row_of(ix.row())
            if 0 <= r < model.rowCount():
                sel.select(model.index(r), model.index(r))
        cur = view.currentIndex()
        row = self._lp_row_of(cur.row()) if cur.isValid() else -1
        sm = self.listw.selectionModel()
        self._lp_syncing = True
        try:
            sm.select(sel, QItemSelectionModel.SelectionFlag.ClearAndSelect)
            if 0 <= row < model.rowCount() and row != self.listw.currentIndex().row():
                # The detail panel follows the list's current row.
                sm.setCurrentIndex(model.index(row), QItemSelectionModel.SelectionFlag.NoUpdate)
        finally:
            self._lp_syncing = False

    def _lp_list_moved(self, cur, _prev=None) -> None:
        """The list's row picked by the window (back out of a folder, a file
        revealed, the pane following what plays): its card picked and on
        screen."""
        if self._lp_syncing or not self._place_local_active():
            return
        self._lp_follow_list(cur=cur)

    def _lp_follow_list(self, reveal: bool = True, cur=None) -> None:
        place = self._lp_page
        if place is None:
            return
        cur = self.listw.currentIndex() if cur is None else cur
        if not cur.isValid():
            return
        i = self._lp_card_of(cur.row())
        view = place.view
        if not 0 <= i < view.model_.rowCount():
            return
        self._lp_syncing = True
        try:
            idx = view.model_.index(i, 0)
            if view.currentIndex() != idx:
                view.setCurrentIndex(idx)
        finally:
            self._lp_syncing = False
        if reveal:
            QTimer.singleShot(0, partial(self._lp_reveal, i))

    def _lp_reveal(self, i: int) -> None:
        place = self._lp_page
        if place is None or sip.isdeleted(place) or not self._place_local_active():
            return
        view = place.view
        if not 0 <= i < view.model_.rowCount():
            return
        view.doItemsLayout()
        lay = place.body.layout()
        if lay is not None:
            lay.activate()
        place.reveal(view, view.visualRect(view.model_.index(i, 0)))

    def _lp_select_key(self, key: str) -> None:
        model = self.list_model
        for r in range(model.rowCount()):
            it = model.item_at(r)
            if isinstance(it, dict) and it.get("_key") == key:
                self.listw.setCurrentIndex(model.index(r))
                return

    # -- what plays -----------------------------------------------------------------------

    def _lp_mark(self) -> None:
        place = self._lp_page
        if place is None or sip.isdeleted(place):
            return
        key = ""
        if getattr(self, "_playing_group", None) == "local" \
                and getattr(self, "_playing_key", None) is not None:
            key = str(self._playing_key)
        place.view.set_playing(key)
        place.cont.set_playing(key)

    def _place_local_playing(self) -> None:
        """_start_playback and Stop: what plays marked; Continue as the
        resume points are now."""
        if self._lp_page is None:
            return
        self._lp_mark()
        if self._place_local_active():
            QTimer.singleShot(0, self._lp_rows_refresh)

    def _lp_stack_changed(self, _i: int) -> None:
        """The place on screen again (Back from a series' page): Continue
        and the marks as they are now - the cards, scroll and choice as
        they were."""
        if not self._place_local_showing():
            return
        self._lp_rows_refresh()
        self._lp_mark()
        self._lp_page.view.viewport().update()

    # -- the window around it ---------------------------------------------------------

    def _place_local_restyle(self) -> None:
        """A theme or a language: drawn again in it."""
        place = self._lp_page
        if place is None or sip.isdeleted(place):
            return
        place.restyle()
        place.retranslate()
        self._lp_icons()
        self._lp_cont_sig = None
        if self._place_local_active():
            self._lp_head()
            self._lp_rows_refresh()
            place.view.redress()

    def _place_local_refresh(self) -> None:
        """mw_places: what the place shows changed (a profile, a source, a
        sync) - Continue and the cards' marks again."""
        place = self._lp_page
        if place is None or sip.isdeleted(place) or not self._place_local_active():
            return
        self._lp_cont_sig = None
        self._lp_rows_refresh()
        place.view.redress()

    def _lp_state(self) -> dict:
        """What the place shows, for a test and the log."""
        place = self._lp_page
        view = place.view if place is not None else None
        return {"shown": self._place_local_showing(),
                "cards": view.model_.rowCount() if view is not None else 0,
                "layout": view.layout_kind if view is not None else "",
                "title": place.header.title_text() if place is not None else "",
                "continue": [c.key for c in place.cont.cards()] if place is not None else []}
