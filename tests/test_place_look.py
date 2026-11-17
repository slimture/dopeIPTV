"""What the places show (core/places.py, ui/mw_place_look.py), without a
window: the sidebar's entries taken out and where the app opens then, the
rows over the lists and Just the list, each profile's own keys (Popular
right now keeping its own on Posters & info); the places' block in a column
or folded into even rows of tiles that wrap and mirror, a folded entry's
bar under its icon, the fold control's chevron, "Hide this row" on a row's
heading, and the compact header (the title on the count's line where they
fit, as it was once compact is off). The window itself is
tests/test_place_look_window.py."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSize, Qt  # noqa: E402
from PyQt6.QtGui import QColor  # noqa: E402
from PyQt6.QtWidgets import QApplication, QSizePolicy, QWidget  # noqa: E402

from dopeiptv.core import places, profiles, sync  # noqa: E402


class S(dict):
    """A QSettings stand-in."""

    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    from dopeiptv.ui import theme
    theme.apply_theme(theme="graphite")
    return a


# -- the sidebar's entries ---------------------------------------------------------

def test_only_the_lower_places_and_pages_can_be_taken_out():
    assert places.HIDEABLE == ("guide", "search", "local", "radio", "rec")
    assert not {"mitt", "tv", "films", "series"} & set(places.HIDEABLE)
    s = S()
    assert places.hidden(s) == frozenset() and places.shown(s) == places.PLACES
    assert places.set_hidden(s, "radio", True) and places.set_hidden(s, "guide", True)
    assert not places.set_hidden(s, "radio", True)          # already out
    assert not places.set_hidden(s, "tv", True) and not places.set_hidden(s, "mitt", True)
    assert s[places.HIDDEN_KEY] == "guide,radio"             # the sidebar's order
    assert places.shown(s) == ("mitt", "tv", "films", "series", "search", "local", "rec")
    assert places.lower_shown(s)
    for p in ("local", "rec"):
        places.set_hidden(s, p, True)
    assert not places.lower_shown(s)                          # the hairline goes
    assert places.set_hidden(s, "radio", False) and places.lower_shown(s)
    # Anything else in the key - a typo, Mine - is never hidden.
    assert places.hidden(S({places.HIDDEN_KEY: " search , mitt,bogus,tv "})) == {"search"}
    assert places.hidden(S({places.HIDDEN_KEY: ["rec", "films"]})) == {"rec"}
    assert places.hidden_text(["rec", "guide", "tv"]) == "guide,rec"


def test_the_app_never_opens_on_a_place_taken_out():
    s = S({places.LAST_KEY: "radio", places.LAST_LIST_KEY: "radio"})
    assert places.start_places(s) == ("radio", "")
    places.set_hidden(s, "radio", True)
    assert places.start_places(s) == ("tv", "")              # TV in its stead
    # A page taken out: the list place under it.
    s = S({places.LAST_KEY: "guide", places.LAST_LIST_KEY: "films"})
    assert places.start_places(s) == ("films", "guide")
    places.set_hidden(s, "guide", True)
    assert places.start_places(s) == ("films", "")
    # A page over a list place taken out: TV under it.
    s = S({places.LAST_KEY: "search", places.LAST_LIST_KEY: "local"})
    places.set_hidden(s, "local", True)
    assert places.start_places(s) == ("tv", "search")
    # Mine is never taken out; "always Mine" stays.
    s = S({places.START_KEY: "mitt", places.HIDDEN_KEY: "guide,search,local,radio,rec"})
    assert places.start_places(s) == ("mitt", "")


def test_folded_is_off_until_switched_on():
    assert not places.folded(S())
    assert places.folded(S({places.FOLDED_KEY: "true"}))
    assert places.folded(S({places.FOLDED_KEY: True}))
    assert not places.folded(S({places.FOLDED_KEY: "false"}))


# -- the rows and Just the list -------------------------------------------------------

def test_the_rows_their_keys_and_popular_right_now_in_its_own():
    names = set(places.ROWS)
    assert {"tv_recent", "films_continue", "series_continue", "local_continue",
            "films_popular", "series_popular", "rec_now", "radio_favs"} == names
    assert places.ROWS["films_popular"].key == places.POPULAR_KEYS["vod"]
    assert places.ROWS["series_popular"].key == places.POPULAR_KEYS["series"]
    assert not places.ROWS["films_popular"].own
    s = S()
    assert all(places.row_on(s, n) and places.row_shown(s, n) for n in names)
    assert not places.row_on(s, "bogus") and not places.row_shown(s, "bogus")
    assert places.set_row(s, "tv_recent", False)
    assert s["place_row_tv_recent"] == "false" and not places.row_shown(s, "tv_recent")
    assert not places.set_row(s, "tv_recent", False)          # nothing to change
    # Popular right now: the very key Posters & info keeps - no second one.
    assert places.set_row(s, "films_popular", False)
    assert s[places.POPULAR_KEYS["vod"]] == "false" and not places.popular_on(s, "vod")
    assert not any(k.startswith("place_row_films_popular") for k in s)
    assert places.rows_of("films") == (places.ROWS["films_popular"], places.ROWS["films_continue"])


def test_just_the_list_hides_the_plain_rows_and_keeps_their_keys():
    s = S({"place_row_series_continue": "false"})
    before = dict(s)
    s[places.JUST_LIST_KEY] = "true"
    assert places.just_list(s)
    for n in ("tv_recent", "films_continue", "series_continue", "local_continue",
              "films_popular", "series_popular"):
        assert not places.row_shown(s, n), n
    # What records now and the favourite stations are not over those lists.
    assert places.row_shown(s, "rec_now") and places.row_shown(s, "radio_favs")
    assert places.row_on(s, "tv_recent") and places.popular_on(s, "vod")
    del s[places.JUST_LIST_KEY]
    assert s == before
    assert places.row_shown(s, "tv_recent") and not places.row_shown(s, "series_continue")


def test_what_a_place_opens_as_while_just_the_list_is_on():
    s = S()
    assert places.view_of(s, "films", "poster") == "poster"
    assert places.view_of(s, "tv", "airy") == "airy"
    s[places.JUST_LIST_KEY] = "true"
    assert places.view_of(s, "films", "poster") == "list"
    assert places.view_of(s, "series", "poster") == "list"
    assert places.view_of(s, "tv", "airy") == "compact"
    assert places.view_of(s, "local", "poster") == "poster"   # not Local's library
    assert s == {places.JUST_LIST_KEY: "true"}                 # nothing stored over


def test_each_profile_keeps_its_own_and_they_sync():
    own = places.ROW_KEYS | {places.HIDDEN_KEY, places.FOLDED_KEY, places.JUST_LIST_KEY}
    assert own <= profiles.OWN_KEYS, own - profiles.OWN_KEYS
    for key in places.POPULAR_KEYS.values():
        assert not profiles.is_own(key)                        # the household's, as ever
    for key in own:
        assert sync.syncable(key) and sync.syncable(f"profiles/kid/{key}"), key
    assert places.LOOK_KEYS == own | set(places.POPULAR_KEYS.values())


# -- the places' block --------------------------------------------------------------

def _block(app, width: int = 196, rtl: bool = False):
    from dopeiptv.ui.nav_widgets import NavButton, PlacesFold, PlacesLayout, SideRule
    box = QWidget()
    if rtl:
        box.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    lay = PlacesLayout(box, spacing=2)
    btns = []
    for key in ("mitt", "live", "vod", "series", "guide", "search"):
        b = NavButton(key, key)
        b.setFixedHeight(32)
        lay.addWidget(b)
        btns.append(b)
    rule = SideRule()
    lay.addWidget(rule)
    for key in ("local", "radio", "rec"):
        b = NavButton(key, key)
        b.setFixedHeight(32)
        lay.addWidget(b)
        btns.append(b)
    fold = PlacesFold()
    lay.addWidget(fold)
    box.resize(width, 600)
    return box, lay, btns, rule, fold


def test_in_full_a_column_folded_even_rows_that_wrap(app):
    box, lay, btns, rule, fold = _block(app)
    box.show()
    lay.setGeometry(box.rect())
    ys = [b.geometry().y() for b in btns]
    assert ys == sorted(ys) and len(set(ys)) == 9
    assert all(b.geometry().width() == 196 for b in btns)
    assert btns[5].geometry().bottom() < rule.geometry().y() < btns[6].geometry().y()
    assert fold.geometry().width() == fold.W                  # centred under them
    assert abs(fold.geometry().center().x() - 98) <= 1
    assert not lay.hasHeightForWidth()
    # Folded: nine entries and the chevron in two rows of five.
    rule.hide()
    assert lay.set_folded(True) and not lay.set_folded(True)
    assert lay.hasHeightForWidth()
    assert lay.grid(196, 10) == (5, 2, 36)
    lay.setGeometry(box.rect())
    rows = sorted({b.geometry().y() for b in btns} | {fold.geometry().y()})
    assert len(rows) == 2
    assert [b.geometry().x() for b in btns[:5]] == [0, 40, 80, 120, 160]
    assert fold.geometry().x() == 160 and fold.geometry().y() == rows[1]
    assert fold.geometry().size() == btns[0].geometry().size()  # a tile like the rest
    assert lay.heightForWidth(196) == 2 * btns[0].geometry().height() + lay.TILE_GAP
    # Narrow: more rows; wide: one, never wider than a tile may be.
    assert lay.grid(120, 10)[1] == 4
    assert lay.grid(600, 10) == (10, 1, 48)
    # An entry taken out leaves no hole: the row closes up.
    btns[4].hide()
    lay.setGeometry(box.rect())
    assert btns[5].geometry().x() == 160 and lay.grid(196, 9)[:2] == (5, 2)
    # In full again: the column as before.
    btns[4].show()
    rule.show()
    lay.set_folded(False)
    lay.setGeometry(box.rect())
    assert [b.geometry().y() for b in btns] == ys
    box.deleteLater()


def test_folded_tiles_mirror_right_to_left(app):
    box, lay, btns, rule, fold = _block(app, rtl=True)
    rule.hide()
    lay.set_folded(True)
    lay.setGeometry(box.rect())
    assert btns[0].geometry().right() == 195                  # Mine at the right
    assert btns[1].geometry().right() < btns[0].geometry().left()
    box.deleteLater()


def _near(c: QColor, hex_: str, tol: int = 40) -> bool:
    w = QColor(hex_)
    return abs(c.red() - w.red()) + abs(c.green() - w.green()) + abs(c.blue() - w.blue()) <= tol


def test_a_folded_entry_is_lit_by_a_bar_under_its_icon(app):
    from dopeiptv.ui.nav_widgets import NavButton
    from dopeiptv.ui.theme import P
    b = NavButton("live", "")
    b.setStyleSheet(f"QPushButton{{background:{P['side']};border:none;}}"
                    f"QPushButton:checked{{background:{P['nav_tint']};}}")
    b.setIconSize(QSize(22, 22))
    b.setProperty("rail", True)
    b.setProperty("tile", True)
    b.resize(40, 34)
    b.setChecked(True)
    img = b.grab().toImage()
    bottom = [img.pixelColor(x, 34 - 3) for x in range(14, 26)]
    assert any(_near(c, P["nav_ink"]) for c in bottom)
    assert not any(_near(img.pixelColor(1, y), P["nav_ink"]) for y in range(8, 26))
    b.setChecked(False)
    img = b.grab().toImage()
    assert not any(_near(img.pixelColor(x, 31), P["nav_ink"], 20) for x in range(14, 26))


def test_the_fold_control_points_up_in_full_and_down_folded(app):
    from dopeiptv.ui.nav_widgets import PlacesFold
    f = PlacesFold()
    f.resize(28, 16)
    a, tip, c = f.chevron_points()
    assert tip.y() < a.y() and tip.y() < c.y()
    f.set_folded(True)
    a, tip, c = f.chevron_points()
    assert tip.y() > a.y() and f.is_folded()
    sp = f.sizePolicy()
    assert sp.horizontalPolicy() != QSizePolicy.Policy.Fixed   # grows to a tile


# -- "Hide this row" -------------------------------------------------------------------

def test_a_named_row_offers_hide_this_row_on_its_heading(app):
    from dopeiptv.i18n import tr
    from dopeiptv.ui.media_cards import CardRow
    row = CardRow("Continue")
    assert row.heading_menu() is None                          # no name: nothing
    asked = []
    row.hide_requested.connect(asked.append)
    row.set_hide_key("films_continue")
    m = row.heading_menu()
    assert [a.text() for a in m.actions()] == [tr("row_hide")]
    m.actions()[0].trigger()
    assert asked == ["films_continue"]
    assert row.heading.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
    from dopeiptv.ui.tv_place import TvPlaceTop
    top = TvPlaceTop()
    got = []
    top.hide_requested.connect(got.append)
    top.recent.heading_menu().actions()[0].trigger()
    assert got == ["tv_recent"]


# -- the compact header ------------------------------------------------------------------

def test_the_header_is_one_line_at_one_height_compact_or_not(app):
    from dopeiptv.ui.media_cards import PageHeader, PlacePage
    page = PlacePage("Sweden")
    h = page.header
    h.set_count("14 channels")
    h.set_views([("compact", "Compact"), ("airy", "Airy")])
    page.resize(800, 600)
    page.show()
    app.processEvents()
    top = page._lay.contentsMargins().top()
    # The name small at its start; how many is the status line's - kept,
    # and a screen reader's description.
    assert h.height() == PageHeader.CONTROL_H and h.is_inline()
    assert h.title.isVisible() and h.title.text() == "Sweden"
    assert f"font-size:{PageHeader.TITLE_PX}px" in h.title.styleSheet()
    assert h.count.text() == "14 channels" and not h.count.isVisible()
    assert h.accessibleName() == "Sweden" and h.accessibleDescription() == "14 channels"
    assert h.line_text() == "Sweden"
    # Just the list: less room round it; the line the same.
    page.set_compact(True)
    for _ in range(3):
        app.processEvents()
    assert h.is_compact() and page.is_compact() and page._lay.contentsMargins().top() < top
    assert h.height() == PageHeader.CONTROL_H
    page.set_compact(False)
    for _ in range(3):
        app.processEvents()
    assert page._lay.contentsMargins().top() == top
    page.resize(330, 600)
    for _ in range(3):
        app.processEvents()
    assert h.width() <= 330
    page.deleteLater()


def test_with_the_name_shown_it_is_cut_first_never_over_the_count(app):
    from dopeiptv.ui.media_cards import PageHeader
    name = "A very long category name indeed, longer than any pane"
    h = PageHeader(name)
    h.show_title = h.show_count = True
    h.set_count("1 204 movies")
    h.resize(300, 60)
    h.show()
    for _ in range(3):
        app.processEvents()
    assert h.title.text() != name and h.title.text().endswith("…")
    assert h.count.text() == "1 204 movies"
    assert h.title.toolTip() == name + " · 1 204 movies"
    assert h.title.geometry().right() < h.count.geometry().left()      # never over it
    h.resize(1400, 60)
    for _ in range(3):
        app.processEvents()
    assert h.title.text() == name and h.title.toolTip() == ""   # room again: whole
    assert h.title.geometry().right() < h.count.geometry().left()
    h.resize(90, 60)
    for _ in range(3):
        app.processEvents()
    assert h.count.text().endswith("…") and h.title.width() <= PageHeader.TITLE_MIN
    assert h.title.geometry().right() < h.count.geometry().left()
    h.deleteLater()
