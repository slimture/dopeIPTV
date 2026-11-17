"""The rows over the places' lists (Recently watched, Continue, My stations,
what records now) play on one click only with autoplay on, as the lists do;
a row that only opens a page (Series' Popular right now) opens on one."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[1] / "dopeiptv" / "ui"


@pytest.mark.parametrize("module, row", [
    ("mw_place_tv.py", "top.recent"),
    ("mw_place_films.py", "cont"),
    ("mw_place_series.py", "place.cont"),
    ("mw_place_local.py", "place.cont"),
    ("mw_place_radio.py", "place.favs"),
    ("mw_place_rec.py", "place.now"),
])
def test_a_row_that_plays_follows_autoplay(module, row):
    src = (UI / module).read_text(encoding="utf-8")
    assert re.search(re.escape(row) + r"\.strip\.click_plays = self\._autoplay_preview", src), module


def test_one_click_follows_the_setting():
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication

    from dopeiptv.ui.media_cards import Card, CardRow

    app = QApplication.instance() or QApplication([])
    row = CardRow("Continue")
    row.strip.set_items([Card(key="a", title="A"), Card(key="b", title="B")])
    played = []
    row.activated.connect(lambda c: played.append(c.key))
    on = [False]
    row.strip.click_plays = lambda: on[0]
    idx = row.strip.model_.index(0, 0)
    row.strip.clicked.emit(idx)
    assert played == []                     # autoplay off: a click only picks
    on[0] = True
    row.strip.clicked.emit(idx)
    assert played == ["a"]                  # on: one click plays
    app.processEvents()
