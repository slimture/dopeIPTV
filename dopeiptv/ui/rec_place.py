"""Recordings in the shared form of every place, pure presentation (the
window's side is ui/mw_place_rec.py), on ``list_place.ListPlace``: the
header (the folder's or list's name, how many recordings and what they
take on the disk, the filter, ⋯ for what concerns them all - scheduled
recordings, series recordings, a new folder, the folder itself -, Grid /
List), "Recording now and next" - what records, with its red mark and how
long it has run, and what is to come, soonest first - a calm word where
there is nothing, then the recordings as 16:9 cards or rows: a frame of
each (else its channel's logo, fitted), its channel, day, length and size,
how far it was watched, Watched.

The cards are dressed only as they paint (``PlaceCardView``), their frames
asked for only for those; nothing here plays, records or stores.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QToolButton

from ..i18n import tr
from .list_place import ListPlace, PlaceCardView
from .media_cards import CardRow

__all__ = ["RecCardView", "RecPlace"]


class RecCardView(PlaceCardView):
    """The recordings' rows or cards (``PlaceCardView``)."""

    OBJECT_NAME = "RecPlaceView"


class RecPlace(ListPlace):
    """The place: the header with the filter, ⋯ and Grid / List; what
    records now and next; a calm word; the recordings."""

    NAME = "Rec"
    VIEW = RecCardView
    GRID = "wide"

    def __init__(self, loader=None, parent=None) -> None:
        super().__init__(tr("nav_recordings"), loader, parent)
        self.retranslate()
        self.restyle()

    def _make_tools(self):
        self.more = self.tool_button("RecMore")
        self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        return (self.more,)

    def _make_rows(self, loader) -> None:
        # Logos, fitted: the small picture leaves the words their room.
        self.now = CardRow(tr("rec_place_now"), loader, picture="poster")
        self.add_row(self.now)

    def retranslate(self) -> None:
        self.setAccessibleName(tr("nav_recordings"))
        super().retranslate()
        self.now.set_title(tr("rec_place_now"))
        self.more.setToolTip(tr("sp_more"))
        self.more.setAccessibleName(tr("sp_more"))
