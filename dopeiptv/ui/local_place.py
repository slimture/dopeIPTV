"""Local files in the shared form of every place, pure presentation (the
window's side is ui/mw_place_local.py), on ``list_place.ListPlace``: a way
back to the folder above over the header ("‹ Back to Films" - and the
mouse's back button, and Backspace), the header (the folder's name, or
"Local files" at the top; how many folders and files, or the library's
series and films; a filter; Rescan; in the library Grid / List; the
Folders / Library switch), the "Continue" row of the files in progress, a
calm word where there is nothing (with "Add folder…" while there is no
folder at all), then the content: a folder's folders and files as rows,
the library as posters or rows.

Fast with big folders and big libraries: one view of uniform rows or cells
with a painted delegate, what a card shows beyond its name - its picture,
its line, its pills, how far it got - worked out only as the card is
painted or about to be (``LocalCardView.dress``), the pictures through the
loaders for those cards alone; rows put in and taken out where they are,
without a reset (``insert_items``, ``remove_rows``), so a folder read in
slices grows at its end and one changed on disk changes in place.

A picture that is not the shape of its box - a folder's glyph, an album's
square cover, a still in a poster's box - is fitted inside it on the
card's surface rather than cut (``fits_inside``).
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import pyqtSignal

from ..i18n import tr
from .list_place import ListPlace, PlaceCard, PlaceCardView, fits_inside, layout_icon
from .media_cards import CardRow

__all__ = ["LocalCard", "LocalCardView", "LocalPlace", "fits_inside", "layout_icon"]


@dataclass(slots=True)
class LocalCard(PlaceCard):
    """A row of Local files on a card; *gen* the dressing it was dressed
    for (``LocalCardView``: -1, not yet)."""


class LocalCardView(PlaceCardView):
    """The folder's rows or the library's cards (``PlaceCardView``)."""

    OBJECT_NAME = "LocalPlaceView"


class LocalPlace(ListPlace):
    """The place: Back over the header; the header with the filter,
    Rescan, Grid / List (the library) and Folders / Library; Continue; a
    calm word; the cards. The window fills each part and listens to their
    signals."""

    add_requested = pyqtSignal()

    NAME = "Local"
    VIEW = LocalCardView
    GRID = "poster"

    def __init__(self, loader=None, parent=None) -> None:
        super().__init__(tr("nav_local"), loader, parent)
        self.add_btn = self.note_btn
        self.add_btn.setText(tr("local_add_folder"))
        self.note_action.connect(self.add_requested)
        self.retranslate()
        self.restyle()

    def _make_tools(self):
        self.rescan = self.tool_button("LocalRescan")
        return (self.rescan,)

    def _make_rows(self, loader) -> None:
        self.cont = CardRow(tr("card_continue"), loader, picture="wide")
        self.add_row(self.cont)

    def retranslate(self) -> None:
        self.setAccessibleName(tr("nav_local"))
        super().retranslate()
        self.cont.set_title(tr("card_continue"))
        self.add_btn.setText(tr("local_add_folder"))
        self.rescan.setAccessibleName(tr("local_rescan"))

    def set_note(self, text: str, add: bool = False) -> None:
        """A calm word where the cards are while there are none - with
        "Add folder…" beside it (*add*) while there is no folder at all."""
        super().set_note(text, action=add)
