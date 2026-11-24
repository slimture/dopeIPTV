"""Downloaded in the shared form of every place, pure presentation (the
window's side is ui/mw_downloads.py), on ``list_place.ListPlace``: Back
while a series' episodes are open ("‹ Back to Downloaded" - the mouse's
back button, Backspace), the header (the place's name or the series', how
many titles and what they take on the disk, the filter, the sort, All /
Movies / Series, Grid / List), "Downloading" - what downloads now and what
waits, each with its progress and why it waits or failed -, a calm word
where there is nothing (how to download), then the films and the series
with their downloaded episodes as posters or rows: the poster saved beside
each file, the year, length and size, how far it was watched, Downloaded.

The cards are dressed only as they paint (``PlaceCardView``); nothing here
plays, downloads or deletes.
"""

from __future__ import annotations

from ..i18n import tr
from .list_place import ListPlace, PlaceCardView
from .media_cards import CardRow

__all__ = ["DownloadsCardView", "DownloadsPlace", "SECTIONS"]

#: The header's switch: everything, the films, the series.
SECTIONS = ("all", "movie", "series")
_SECTION_WORDS = {"all": "cat_all", "movie": "nav_movies", "series": "nav_series"}


class DownloadsCardView(PlaceCardView):
    """The downloaded films, series and episodes as posters or rows."""

    OBJECT_NAME = "DownloadsPlaceView"


class DownloadsPlace(ListPlace):
    """The place: Back while a series is open; the header with the filter,
    the sort, the sections and Grid / List; Downloading; a calm word; the
    titles."""

    NAME = "Downloads"
    VIEW = DownloadsCardView
    GRID = "poster"

    def __init__(self, loader=None, parent=None) -> None:
        super().__init__(tr("nav_downloads"), loader, parent)
        self.retranslate()
        self.restyle()

    def _make_rows(self, loader) -> None:
        self.running = CardRow(tr("dl_place_running"), loader, picture="poster")
        self.add_row(self.running)

    def set_sections(self, current: str) -> None:
        """All / Movies / Series in the header's switch."""
        opts = [(k, tr(_SECTION_WORDS[k])) for k in SECTIONS]
        sw = self.header.switch
        if sw.keys() != list(SECTIONS) or [b.text() for b in sw.buttons] != [w for _k, w in opts]:
            self.header.set_views(opts, current if current in SECTIONS else "all")
        else:
            self.header.set_view_key(current if current in SECTIONS else "all")
        sw.setAccessibleName(tr("nav_downloads"))

    def retranslate(self) -> None:
        self.setAccessibleName(tr("nav_downloads"))
        super().retranslate()
        self.running.set_title(tr("dl_place_running"))
