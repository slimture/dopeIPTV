"""Radio in the shared form of every place, pure presentation (the window's
side is ui/mw_place_radio.py), on ``list_place.ListPlace``: a way back to
the countries, languages or genres over the header while one of them is
open ("‹ Back to Countries" - the mouse's back button, Backspace), the
header (the list's name - Most voted, a country -, how many stations or
countries, the filter - the whole directory's search under Search -, Grid
/ List), "My stations" - the favourite stations as small cards, what plays
on the one playing -, a calm word where there is nothing, then the list:
stations as rows or tiles with their logos fitted (the country, what it
plays and how as a muted line; a favourite's star), or the countries,
languages and genres with how many stations each (a country's flag).

The cards are dressed only as they paint (``PlaceCardView``), their logos
asked for only for those; nothing here plays or asks radio-browser.info.
"""

from __future__ import annotations

from pathlib import Path

from ..i18n import tr
from .list_place import ListPlace, PlaceCardView
from .media_cards import CardRow

__all__ = ["RadioCardView", "RadioPlace", "flag_path", "station_line"]

_FLAGS = Path(__file__).resolve().parents[1] / "assets" / "flags"


def flag_path(code: str) -> str:
    """A country's bundled flag by its two letters, "" when there is none."""
    code = str(code or "").strip().lower()
    if len(code) != 2 or not code.isalpha():
        return ""
    p = _FLAGS / f"{code}.svg"
    return str(p) if p.is_file() else ""


def station_line(sub: str, now_on: str = "") -> str:
    """A station's line: what it says is on (while it plays), else where it
    is from and what it plays."""
    return f"♪ {now_on}" if now_on else str(sub or "")


class RadioCardView(PlaceCardView):
    """The stations' rows or tiles, or the countries' rows: every picture
    a logo or a flag, fitted inside its box."""

    OBJECT_NAME = "RadioPlaceView"

    def __init__(self, loader=None, layout: str = "list", parent=None) -> None:
        super().__init__(loader, layout, parent)
        self.fit_always = True


class RadioPlace(ListPlace):
    """The place: Back while a country, language or genre is open; the
    header with the filter and Grid / List; My stations; a calm word; the
    list."""

    NAME = "Radio"
    VIEW = RadioCardView
    GRID = "wide"

    def __init__(self, loader=None, parent=None) -> None:
        super().__init__(tr("nav_radio"), loader, parent)
        self.retranslate()
        self.restyle()

    def _make_rows(self, loader) -> None:
        self.favs = CardRow(tr("radio_cat_favorites"), loader, picture="poster")
        self.add_row(self.favs)

    def retranslate(self) -> None:
        self.setAccessibleName(tr("nav_radio"))
        super().retranslate()
        self.favs.set_title(tr("radio_cat_favorites"))
