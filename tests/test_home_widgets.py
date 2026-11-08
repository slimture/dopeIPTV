"""Home's building blocks: the featured strip, the cards' composed
pictures, the shelves' arrows, and how titles are split for display."""
from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from dopeiptv.ui.home_widgets import (  # noqa: E402
    HERO_H, POSTER_H, POSTER_W, RANK_W, _Card, _Hero, _Shelf, rating_text,
    split_year,
)
from dopeiptv.ui.mw_home import HomePage  # noqa: E402

app = QApplication.instance() or QApplication([])


def test_a_year_in_the_name_moves_under_the_title():
    assert split_year("Copper Sky (2022)") == ("Copper Sky", "2022")
    assert split_year("EN - Dune: Part Two [2024]") == ("EN - Dune: Part Two",
                                                        "2024")
    assert split_year("Blade Runner 2049") == ("Blade Runner 2049", "")
    assert split_year("") == ("", "")


def test_ratings_read_as_out_of_ten():
    assert rating_text({"rating": "7.4"}) == "★ 7.4"
    assert rating_text({"rating_5based": 4}) == "★ 8.0"
    assert rating_text({"rating": "0"}) == ""
    assert rating_text({"rating": "n/a"}) == ""
    assert rating_text({}) == ""


def _poster(color: str = "#336699") -> QPixmap:
    pm = QPixmap(200, 300)
    pm.fill(QColor(color))
    return pm


def test_a_ranked_card_makes_room_for_its_number():
    plain = _Card(POSTER_W, POSTER_H, "A")
    ranked = _Card(POSTER_W, POSTER_H, "B", rank=3)
    assert plain.width() == POSTER_W
    assert ranked.width() == POSTER_W + RANK_W
    ranked.set_pixmap(_poster())
    assert ranked.img.pixmap().width() == POSTER_W + RANK_W


def test_every_card_style_composes_an_opaque_picture():
    now = time.time()
    cards = [_Card(POSTER_W, POSTER_H, "p", badge="NEW"),
             _Card(296, 166, "w", "12 min left", progress=40, style="wide"),
             _Card(264, 148, "t", style="tile", live=(now - 600, now + 600))]
    for c in cards:
        c.set_pixmap(_poster(), contain=c._style == "tile")
        img = c.img.pixmap().toImage()
        assert not img.isNull()
        # Corners are painted in the page background, never left clear.
        assert img.pixelColor(0, 0).alpha() == 255


def test_the_strip_is_just_the_bar_with_nothing_to_feature():
    bar = QWidget()
    bar.setFixedHeight(40)
    hero = _Hero(bar, None, lambda it, kind: None)
    assert hero.height() < HERO_H
    assert hero._title.isHidden()
    hero.set_items([({"name": "Copper Sky (2022)", "rating": "7"}, "vod",
                     "#1 · Popular"),
                    ({"name": "Signal"}, "series", "#1 · Series")])
    assert hero.height() == HERO_H
    assert hero._title.text() == "Copper Sky"
    assert "2022" in hero._meta.text() and "★ 7.0" in hero._meta.text()
    assert hero._timer.isActive()
    played = []
    hero.play.connect(lambda it, kind: played.append((it["name"], kind)))
    hero._play_btn.click()
    hero.show_item(1)
    hero._play_btn.click()
    assert played == [("Copper Sky (2022)", "vod"), ("Signal", "series")]
    hero.set_items([])
    assert hero.height() < HERO_H and not hero._timer.isActive()


def test_featured_is_the_chart_first_else_what_was_added_last():
    pv = [{"stream_id": i, "name": f"P{i}"} for i in range(7)]
    ps = [{"series_id": 1, "name": "S1"}]
    got = HomePage._featured([], [], pv, ps)
    assert [it["name"] for it, _k, _t in got] == ["P0", "P1", "P2", "P3",
                                                   "P4"]
    got = HomePage._featured([], [], pv[:3], ps)
    assert [(it["name"], k) for it, k, _t in got] == [
        ("P0", "vod"), ("P1", "vod"), ("P2", "vod"), ("S1", "series")]
    got = HomePage._featured([{"stream_id": 9, "name": "New"}], [], [], [])
    assert [(it["name"], k) for it, k, _t in got] == [("New", "vod")]
    assert HomePage._featured([], [], [], []) == []


def test_a_shelf_shows_its_arrows_only_when_it_scrolls():
    shelf = _Shelf("Row", more=lambda: None)
    shelf.resize(900, 320)
    shelf.add(_Card(POSTER_W, POSTER_H, "only one"))
    shelf.finish(POSTER_H)
    shelf.show()
    app.processEvents()
    assert shelf._prev.isHidden() and shelf._next.isHidden()
    for n in range(14):
        shelf.add(_Card(POSTER_W, POSTER_H, f"c{n}"))
    for _ in range(3):              # the row's new size, then the range
        app.processEvents()
    assert not shelf._next.isHidden()
    assert shelf._next.isEnabled() and not shelf._prev.isEnabled()
    shelf.hide()


def test_minutes_left_on_continue_watching():
    assert HomePage._minutes_left({"_resume_pos": 1200,
                                   "_resume_dur": 6000}) == 80
    assert HomePage._minutes_left({"_resume_pos": 5990,
                                   "_resume_dur": 6000}) == 1
    assert HomePage._minutes_left({}) == 0
