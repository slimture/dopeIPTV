"""The Series place in the middle pane, pure presentation (the window's side
is ui/mw_place_series.py): the shared form of ui/media_cards.py - the
header (the category's name, "860 series · Recently updated", a filter,
the sort and the grid / list switch), the "Popular right now" row with
its ranks, the "Continue" row with each series' next episode and how far
it got, then the category's series as poster cards or rows of a list.

Fast with tens of thousands of series: one view of uniform cells and a
painted delegate (no widget per series); what a card shows beyond its
title - its picture, its line, its pills - is filled in only as the card
is painted or about to be (``SeriesGrid.fill``), so a list of 20,000
costs what the screen shows, and the pictures come through the loaders
for those cards alone.

The view keeps a selection of several cards (Ctrl and Shift with a
click or the arrows) for what the menu does to all of them; a plain
click opens the series.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QAbstractItemView, QApplication, QLabel, QLineEdit, QSizePolicy

from ..i18n import tr
from .media_cards import Card, CardDelegate, CardRow, CardView, PlacePage
from .theme import P

__all__ = ["SeriesCard", "SeriesGrid", "SeriesPlace"]


@dataclass(slots=True)
class SeriesCard(Card):
    """A card of the place: *gen* is the art generation its picture and
    line were filled for (``SeriesGrid.art_gen``; -1: not yet)."""
    gen: int = -1


class _Delegate(CardDelegate):
    """The shared card, filled in just before it is painted."""

    def paint(self, p, option, index) -> None:
        v = self._view
        card = v.model_.item_at(index.row())
        if card is not None:
            v.fill_card(card)
        super().paint(p, option, index)


class SeriesGrid(CardView):
    """The category's series: a ``CardView`` whose cards are filled in as
    they paint (*fill(card, full)*: the window's; *full* when the card's
    picture and line are to be worked out again - the first time, or once
    ``refresh_art`` says posters were found since). Several cards can be
    selected (Ctrl / Shift); one click alone opens."""

    OBJECT_NAME = "SeriesPlaceGrid"

    def __init__(self, loader=None, layout: str = "poster", parent=None) -> None:
        self.fill: Callable[[Card, bool], None] | None = None
        self.art_gen = 0
        super().__init__(loader, layout, parent)
        self.setItemDelegate(_Delegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

    def fill_card(self, card: Card) -> None:
        fn = self.fill
        if fn is None:
            return
        full = getattr(card, "gen", self.art_gen) != self.art_gen
        if full and isinstance(card, SeriesCard):
            card.gen = self.art_gen
        try:
            fn(card, full)
        except Exception:
            pass                        # a card painted plain rather than none

    def wanted_urls(self, item) -> list[str]:
        if isinstance(item, Card):
            self.fill_card(item)
        return super().wanted_urls(item)

    def refresh_art(self) -> None:
        """Posters were found since (TMDB answered): the cards on screen
        work theirs out again as they paint; the pictures asked anew."""
        self.art_gen += 1
        self._ahead = None
        self._ask_ahead()
        self.viewport().update()

    def selected_items(self) -> list:
        rows = sorted(ix.row() for ix in self.selectionModel().selectedIndexes())
        return [c for c in (self.item_at(r) for r in rows) if c is not None]

    def _clicked(self, idx) -> None:
        mods = QApplication.keyboardModifiers()
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
                   | Qt.KeyboardModifier.MetaModifier):
            return                      # gathering cards, not opening one
        super()._clicked(idx)


class _Filter(QLineEdit):
    """The header's filter: room for its words when there is room (it
    gives way to the title on a narrow pane)."""

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setWidth(max(hint.width(), SeriesPlace.FILTER_W - 20))
        return hint


class SeriesPlace(PlacePage):
    """The place: header (with the filter), Popular right now, Continue,
    the grid. The window fills each part and listens to their signals."""

    MARGIN_TOP = 6
    #: The filter's width in the header, at most.
    FILTER_W = 240

    def __init__(self, loader=None, layout: str = "poster", parent=None) -> None:
        super().__init__(tr("nav_series"), scroll_whole=True, parent=parent)
        self.setAccessibleName(tr("nav_series"))
        self.filter = _Filter(objectName="Search")
        self.filter.setClearButtonEnabled(True)
        self.filter.setMaximumWidth(self.FILTER_W)
        self.filter.setMinimumWidth(90)
        self.filter.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.header.add_widget(self.filter)
        self.popular = CardRow(tr("card_popular"), loader, picture="poster")
        self.cont = CardRow(tr("card_continue"), loader, picture="poster")
        self.add_row(self.popular)
        self.add_row(self.cont)
        self.note = QLabel("")
        self.note.setObjectName("SeriesPlaceNote")
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        self.note.setWordWrap(True)
        self.add_row(self.note)
        self.note.hide()
        self.grid = SeriesGrid(loader, layout)
        self.set_content(self.grid)
        self.filter.returnPressed.connect(lambda: self.grid.setFocus(
            Qt.FocusReason.TabFocusReason))
        self.retranslate()
        self.restyle()

    def restyle(self) -> None:
        super().restyle()
        if hasattr(self, "note"):
            self.note.setStyleSheet(f"color:{P['muted']}; font-size:13px;"
                                    " background:transparent; padding:12px 0;")

    def retranslate(self) -> None:
        self.filter.setPlaceholderText(tr("list_filter_placeholder"))
        self.filter.setAccessibleName(tr("list_filter_placeholder"))
        self.popular.set_title(tr("card_popular"))
        self.cont.set_title(tr("card_continue"))

    def set_note(self, text: str) -> None:
        """A calm word where the cards are while there are none."""
        self.note.setText(text)
        self.note.setVisible(bool(text))
