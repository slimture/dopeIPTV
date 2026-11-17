"""A place of the new look kept to the window's list (``ListMirror``): the
list stays the source of truth - what it holds, its menus, its selection,
the detail panel that follows it - and the place draws it, card for card.

* A reset of the list's model drawn again a moment later (one rebuild for
  a burst): the same rows keep their cards, the scroll and the choice; a
  new list starts at the top. A heading row has no card (``card_of``
  returns None); the rest keep the list's order.
* Rows drawn again (art found, marks): the cards redressed as they paint.
* The card picked - and every card gathered with Ctrl / Shift - picked in
  the list a moment later (its menus act on the list's selection, the
  detail panel follows its current row); a row the window picks in the
  list (the pane following what plays, Back to a row) picked on its card.
* The place's filter and the list's are one: typed in either, both say it.

Used by the Recordings and the Radio places (ui/mw_place_rec.py,
ui/mw_place_radio.py); nothing here plays or stores.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QItemSelection, QItemSelectionModel, QTimer

from .list_place import ListPlace, PlaceCard

#: The list follows the card the keyboard rests on after this long.
SELECT_SYNC_MS = 250


class ListMirror:
    """*place*'s cards kept to *window*'s list. *card_of(row)* a list row's
    card (None for a heading); *active()* whether the place stands where
    the list was; *context()* what the cards are of (the same again keeps
    the scroll); *after()* told once the cards changed (the header)."""

    def __init__(self, window: Any, place: ListPlace,
                 card_of: Callable[[dict], PlaceCard | None],
                 active: Callable[[], bool], context: Callable[[], tuple],
                 after: Callable[[], None]) -> None:
        self.w = window
        self.place = place
        self.card_of = card_of
        self.active = active
        self.context = context
        self.after = after
        #: The model row of each card, or None: one card per row, in order.
        self.rows: list[int] | None = None
        self.ctx: tuple | None = None
        self.pending = False
        self.syncing = False
        view = place.view
        self._sel = QTimer(place)
        self._sel.setSingleShot(True)
        self._sel.setInterval(SELECT_SYNC_MS)
        self._sel.timeout.connect(self.mirror)
        self._dress = QTimer(place)
        self._dress.setSingleShot(True)
        self._dress.setInterval(60)
        self._dress.timeout.connect(self._redress)
        view.current_item_changed.connect(self._picked)
        view.selectionModel().selectionChanged.connect(self._picked)
        model = window.list_model
        model.modelReset.connect(self.soon)
        model.rowsInserted.connect(self.soon)
        model.rowsRemoved.connect(self.soon)
        model.dataChanged.connect(self._changed)
        window.listw.selectionModel().currentChanged.connect(self._list_moved)
        place.filter.setText(window.search.text())
        place.filter.textChanged.connect(self._typed)
        window.search.textChanged.connect(self._search_changed)

    def _alive(self) -> bool:
        return not sip.isdeleted(self.place) and self.active()

    # -- the cards ---------------------------------------------------------------------

    def soon(self, *_a) -> None:
        """The list changed: the cards follow a moment later, once."""
        if self.pending:
            return
        self.pending = True
        QTimer.singleShot(0, self.rebuild)

    def rebuild(self) -> None:
        self.pending = False
        if not self._alive():
            return
        place, model = self.place, self.w.list_model
        rows = model.items()
        cards: list[PlaceCard] = []
        row_of: list[int] = []
        for i, it in enumerate(rows):
            card = self.card_of(it) if isinstance(it, dict) else None
            if card is None:
                continue
            cards.append(card)
            row_of.append(i)
        self.rows = None if len(row_of) == len(rows) else row_of
        view = place.view
        ctx = self.context()
        same = ctx == self.ctx
        bar = place.area.verticalScrollBar() if place.area is not None else None
        scroll = bar.value() if bar is not None else 0
        old = view.items()
        self.syncing = True
        try:
            if len(old) == len(cards) and all(a.key == b.key for a, b in zip(old, cards,
                                                                               strict=True)):
                # The same rows (the list drawn again): the cards as they
                # are, the scroll and the choice kept.
                for a, b in zip(old, cards, strict=True):
                    a.data, a.title = b.data, b.title
                view.redress()
            else:
                view.set_items(cards)
        finally:
            self.syncing = False
        self.ctx = ctx
        if bar is not None:
            if same:
                QTimer.singleShot(0, partial(self._scroll_to, scroll))
            else:
                place.scroll_to_top()
        self.follow(reveal=not same)
        self.after()

    def _scroll_to(self, value: int) -> None:
        place = self.place
        if not sip.isdeleted(place) and place.area is not None:
            place.area.verticalScrollBar().setValue(value)

    def _changed(self, *_a) -> None:
        if self._alive():
            self._dress.start()

    def _redress(self) -> None:
        if self._alive():
            self.place.view.redress()

    def card_row(self, row: int) -> int:
        """The card of model row *row*, -1 for none (a heading)."""
        if self.rows is None:
            return row
        try:
            return self.rows.index(row)
        except ValueError:
            return -1

    def model_row(self, card_row: int) -> int:
        if self.rows is None:
            return card_row
        return self.rows[card_row] if 0 <= card_row < len(self.rows) else -1

    # -- the choice, both ways ------------------------------------------------------------

    def _picked(self, *_a) -> None:
        if not self.syncing:
            self._sel.start()

    def settle(self) -> None:
        """The card picked picked in the list now (before a menu or a play)."""
        self._sel.stop()
        self.mirror()

    def mirror(self) -> None:
        """The card picked - and every card gathered - picked in the list,
        whose menus and detail panel act on them."""
        if not self._alive():
            return
        view, model, listw = self.place.view, self.w.list_model, self.w.listw
        sel = QItemSelection()
        for ix in view.selectionModel().selectedIndexes():
            r = self.model_row(ix.row())
            if 0 <= r < model.rowCount():
                sel.select(model.index(r), model.index(r))
        cur = view.currentIndex()
        row = self.model_row(cur.row()) if cur.isValid() else -1
        sm = listw.selectionModel()
        self.syncing = True
        try:
            sm.select(sel, QItemSelectionModel.SelectionFlag.ClearAndSelect)
            if 0 <= row < model.rowCount() and row != listw.currentIndex().row():
                sm.setCurrentIndex(model.index(row), QItemSelectionModel.SelectionFlag.NoUpdate)
        finally:
            self.syncing = False

    def _list_moved(self, cur, _prev=None) -> None:
        if not self.syncing and self._alive():
            self.follow(cur=cur)

    def follow(self, reveal: bool = True, cur=None) -> None:
        """The list's current row's card picked (and on screen)."""
        listw = self.w.listw
        cur = listw.currentIndex() if cur is None else cur
        if not cur.isValid():
            return
        i = self.card_row(cur.row())
        view = self.place.view
        if not 0 <= i < view.model_.rowCount():
            return
        self.syncing = True
        try:
            idx = view.model_.index(i, 0)
            if view.currentIndex() != idx:
                view.setCurrentIndex(idx)
        finally:
            self.syncing = False
        if reveal:
            QTimer.singleShot(0, partial(self.reveal, i))

    def reveal(self, i: int) -> None:
        if not self._alive():
            return
        place = self.place
        view = place.view
        if not 0 <= i < view.model_.rowCount():
            return
        view.doItemsLayout()
        lay = place.body.layout()
        if lay is not None:
            lay.activate()
        place.reveal(view, view.visualRect(view.model_.index(i, 0)))

    # -- one filter ------------------------------------------------------------------------

    def _typed(self, text: str) -> None:
        if self.w.search.text() != text:
            self.w.search.setText(text)

    def _search_changed(self, text: str) -> None:
        if not sip.isdeleted(self.place) and self.place.filter.text() != text:
            self.put_filter(text)

    def put_filter(self, text: str) -> None:
        f = self.place.filter
        f.blockSignals(True)
        try:
            f.setText(text)
        finally:
            f.blockSignals(False)
