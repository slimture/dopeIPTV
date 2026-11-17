"""The cards every place shares (ui/media_cards.py): the pills' size and
colours, the grid's measures (as many columns as fit, flush at both ends,
never one Qt wraps away), a channel's, a list row's and a sideways card's
parts mirrored right to left, what a screen reader hears - and in real
views: Enter, a click and the menu key said as signals, the one playing
marked, a page that scrolls as one with its grid as tall as its cards, and
pictures (and the guide) asked only for what is on screen."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    from PyQt6.QtWidgets import QApplication
    a = QApplication.instance() or QApplication([])
    from dopeiptv.ui import theme as th
    th.apply_theme(theme="graphite")
    return a


def _spin(app, n=8):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


class FakeLoader:
    """What the views ask of core/workers.LogoLoader."""

    def __init__(self):
        self.cache = {}
        self.asked = []

    def is_dead(self, url):
        return False

    def get(self, url, cb):
        from PyQt6.QtGui import QColor, QPixmap
        self.asked.append(url)
        pm = QPixmap(60, 90)
        pm.fill(QColor("#336699"))
        self.cache[url] = pm
        cb(pm)


# -- the pills -------------------------------------------------------------------------

def test_pill_is_sized_from_the_glyphs_ink_with_room(app):
    from PyQt6.QtGui import QFontMetrics
    from dopeiptv.ui import media_cards as mc
    fm = QFontMetrics(mc.pill_font())
    size = mc.pill_size("Watched")
    assert size.height() == fm.ascent() + fm.descent() + 2 * mc.PILL_PAD_Y
    assert size.width() == fm.horizontalAdvance("Watched") + 2 * mc.PILL_PAD_X
    assert mc.pill_height() == size.height()


@pytest.mark.parametrize("theme", ["graphite", "light", "latte", "solarized_light", "nord",
                                   "contrast", "sand"])
def test_pill_ink_reads_on_its_fill_in_every_theme(app, theme):
    from dopeiptv.ui import media_cards as mc
    from dopeiptv.ui import theme as th
    th.apply_theme(theme=theme)
    try:
        for kind in mc.PILL_KINDS:
            fill, ink = mc.pill_colours(kind)
            need = 3.0 if kind in ("new", "playing") else 4.5   # bold, as on_accent
            assert th.contrast(fill.name(), ink.name()) >= need, (theme, kind)
    finally:
        th.apply_theme(theme="graphite")


def test_pills_given_as_kinds_or_pairs_and_playing_added(app):
    from dopeiptv.ui import media_cards as mc
    got = mc.pills_of(["new", ("seen", "Sedd"), "bogus", "new"])
    assert got == [("new", ""), ("seen", "Sedd")]
    card = mc.Card(key="a", title="A", pills=("seen",))
    assert mc.card_pills(card, playing=True)[0] == ("playing", "")
    assert mc.card_pills(card, playing=False) == [("seen", "")]


def test_screen_reader_hears_title_line_pills_and_progress(app):
    from dopeiptv.ui import media_cards as mc
    card = mc.Card(key="a", title="Northern Lights", line="2025", pills=("new",),
                   progress=0.48, rank=2)
    words = mc.card_a11y(card, playing=True)
    for part in ("Northern Lights", "2025", mc.pill_text("new"), mc.pill_text("playing"),
                 "48"):
        assert part in words
    nn = mc.NowNext("Kvällsnytt", time.time() - 60, time.time() + 600, "Debatten",
                    time.time() + 600)
    words = mc.channel_a11y(mc.ChannelRow(key="1", name="Kanal Ett", number="1"), nn)
    assert "Kanal Ett" in words and "Kvällsnytt" in words and "Debatten" in words


# -- the grid's measures ---------------------------------------------------------------

@pytest.mark.parametrize("layout", ["poster", "wide"])
def test_grid_fills_the_width_and_never_wraps_a_column_away(app, layout):
    from PyQt6.QtCore import QRect
    from dopeiptv.ui import media_cards as mc
    for avail in range(120, 2400, 7):
        m = mc.grid_metrics(avail, layout)
        # Qt wraps once the next cell reaches the width: always short of it.
        assert m.cols * m.slot_w < avail
        assert m.cols == 1 or m.card_w >= mc.MIN_CARD[layout] - mc.GRID_GAP
        cards = [mc.card_in_slot(QRect(c * m.slot_w, 0, m.slot_w, m.cell_h), m, c)
                 for c in range(m.cols)]
        for c, card in enumerate(cards):
            slot = QRect(c * m.slot_w, 0, m.slot_w, m.cell_h)
            assert slot.left() <= card.left() and card.right() <= slot.right()
        assert cards[0].left() == 0 or m.cols == 1
        if m.cols > 1:
            assert m.cols * m.slot_w - (cards[-1].right() + 1) <= 1
            gaps = {cards[i + 1].left() - cards[i].right() - 1 for i in range(m.cols - 1)}
            assert max(gaps) - min(gaps) <= 1 and abs(min(gaps) - m.gap) <= 1


def test_grid_cell_height_is_the_picture_and_its_text():
    from dopeiptv.ui import media_cards as mc
    m = mc.grid_metrics(800, "poster", title_h=17, line_h=14)
    assert m.picture_h == round((m.card_w - 2 * mc.CARD_PAD) * 1.5)
    assert m.cell_h == (mc.CARD_PAD + m.picture_h + mc.TEXT_TOP + 17 + mc.TEXT_GAP + 14
                        + mc.CARD_PAD + m.gap)
    assert m.height(9) == m.lines(9) * m.cell_h
    w = mc.grid_metrics(800, "wide")
    assert w.picture_h == round((w.card_w - 2 * mc.CARD_PAD) * 9 / 16)


def test_column_counted_from_the_left_in_either_direction():
    from dopeiptv.ui import media_cards as mc
    m = mc.grid_metrics(800, "poster")
    assert [mc.left_column(c * m.slot_w, m, 800, False) for c in range(m.cols)] == \
        list(range(m.cols))
    # Qt lays a right-to-left grid out from the right: the first item's slot
    # is the rightmost, column cols-1 counted from the left.
    assert mc.left_column(800 - m.slot_w, m, 800, True) == m.cols - 1
    assert mc.left_column(800 - m.cols * m.slot_w, m, 800, True) == 0


# -- rows mirrored ---------------------------------------------------------------------

def test_channel_row_parts_in_order_and_mirrored():
    from PyQt6.QtCore import QRect
    from dopeiptv.ui import media_cards as mc
    r = QRect(0, 0, 900, 56)
    ltr = mc.channel_parts(r, "compact", False)
    assert ltr["num"].left() < ltr["logo"].left() < ltr["name"].left() < ltr["now"].left() \
        < ltr["next"].left()
    assert ltr["logo"].width() == mc.CHANNEL_DENSITY["compact"][1]
    rtl = mc.channel_parts(r, "compact", True)
    for k in ("num", "logo", "name", "now", "next"):
        assert rtl[k].width() == ltr[k].width()
        assert rtl[k].left() == r.right() - ltr[k].right()
    airy = mc.channel_parts(r, "airy", False)
    assert not airy["next"].isValid()                 # next is under the bar
    assert airy["logo"].width() == mc.CHANNEL_DENSITY["airy"][1]
    narrow = mc.channel_parts(QRect(0, 0, 500, 56), "compact", False)
    assert not narrow["next"].isValid()


def test_list_and_strip_parts_mirrored():
    from PyQt6.QtCore import QRect
    from dopeiptv.ui import media_cards as mc
    r = QRect(10, 0, 600, mc.LIST_ROW_H)
    ltr, rtl = mc.list_parts(r, "poster", 80, False), mc.list_parts(r, "poster", 80, True)
    assert ltr["picture"].left() < ltr["text"].left() < ltr["pills"].left()
    assert rtl["picture"].left() > rtl["text"].left() > rtl["pills"].left()
    slot = QRect(0, 0, mc.STRIP_CARD_W + mc.STRIP_GAP, mc.strip_height("wide"))
    a, b = mc.strip_parts(slot, "wide", True, False), mc.strip_parts(slot, "wide", True, True)
    assert a["card"].left() == 0 and b["card"].left() == mc.STRIP_GAP
    assert a["rank"].left() < a["picture"].left() < a["text"].left()
    assert b["rank"].left() > b["picture"].left() > b["text"].left()
    for k in ("rank", "picture", "text"):
        assert b[k].width() == a[k].width()
        assert b["card"].contains(b[k].center())


# -- pictures --------------------------------------------------------------------------

def test_pictures_scaled_once_per_size_at_the_screens_ratio(app):
    from PyQt6.QtGui import QPixmap
    from dopeiptv.ui import media_cards as mc
    pics = mc.Pictures()
    pm = QPixmap(300, 450)
    a = pics.get(pm, 100, 150, 2.0)
    assert a.width() == 200 and a.devicePixelRatio() == 2.0
    assert pics.get(pm, 100, 150, 2.0) is a
    logo = pics.get(QPixmap(200, 100), 40, 40, 1.0, fit=True)
    assert logo.width() == 40 and logo.height() == 20
    assert len(pics) == 2


# -- the views ---------------------------------------------------------------------------

def _cards(n, image=True):
    from dopeiptv.ui import media_cards as mc
    return [mc.Card(key=f"k{i}", title=f"Title {i}", line="2026",
                    image=f"img{i}" if image else "") for i in range(n)]


def test_grid_lays_out_the_columns_it_measured(app):
    from dopeiptv.ui import media_cards as mc
    for fit in (False, True):
        page = mc.PlacePage("Movies", scroll_whole=fit)
        view = mc.CardView(None, "poster")
        page.set_content(view)
        view.set_items(_cards(40, image=False))
        page.resize(840, 700)
        page.show()
        _spin(app)
        m = view.metrics
        assert m.cols >= 3
        tops = [view.visualRect(view.model_.index(i, 0)).top() for i in range(m.cols + 1)]
        assert len(set(tops[:m.cols])) == 1          # a whole row on one line
        assert tops[m.cols] > tops[0]                 # the next on the next line
        if fit:
            assert view.height() == m.height(40) + 2   # as tall as its cards
        page.close()


def test_enter_click_and_menu_key_said_as_signals(app):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from dopeiptv.ui import media_cards as mc
    view = mc.CardView(None, "list")
    view.set_items(_cards(5, image=False))
    view.resize(500, 400)
    view.show()
    _spin(app)
    got, menus = [], []
    view.activated.connect(got.append)
    view.menu.connect(lambda it, pos: menus.append(it))
    view.setFocus()
    view.setCurrentIndex(view.model_.index(2, 0))
    QTest.keyClick(view, Qt.Key.Key_Return)
    assert got and got[-1].key == "k2"
    QTest.keyClick(view, Qt.Key.Key_Menu)
    assert menus and menus[-1].key == "k2"
    r = view.visualRect(view.model_.index(3, 0))
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=r.center())
    assert got[-1].key == "k3"                       # a card plays on one click
    view.close()
    ch = mc.ChannelView(None)
    ch.set_items([mc.ChannelRow(key=f"c{i}", name=f"Ch {i}", number=str(i + 1))
                  for i in range(5)])
    ch.resize(600, 400)
    ch.show()
    _spin(app)
    picked = []
    ch.activated.connect(picked.append)
    r = ch.visualRect(ch.model_.index(1, 0))
    QTest.mouseClick(ch.viewport(), Qt.MouseButton.LeftButton, pos=r.center())
    assert picked == []                              # a channel: one click picks it
    QTest.mouseDClick(ch.viewport(), Qt.MouseButton.LeftButton, pos=r.center())
    assert picked and picked[-1].key == "c1"
    ch.click_plays = lambda: True                    # "Auto-play preview on selection"
    QTest.mouseClick(ch.viewport(), Qt.MouseButton.LeftButton,
                     pos=ch.visualRect(ch.model_.index(2, 0)).center())
    assert picked[-1].key == "c2"
    ch.close()


def test_playing_marked_and_layout_switched(app):
    from dopeiptv.ui import media_cards as mc
    view = mc.CardView(None, "poster")
    view.set_items(_cards(6, image=False))
    view.set_playing("k1")
    assert view.playing_key == "k1"
    idx = view.model_.index(1, 0)
    from PyQt6.QtCore import Qt
    assert mc.pill_text("playing") in view.model_.data(idx, Qt.ItemDataRole.AccessibleTextRole)
    view.set_layout("list")
    assert view.layout_kind == "list" and not view.isWrapping()
    view.set_layout("wide")
    assert view.layout_kind == "wide" and view.isWrapping()
    assert view.focus_key("k4", take_focus=False)
    assert view.current_item().key == "k4"
    assert not view.focus_key("nope")


def test_pictures_asked_only_for_what_the_page_shows(app):
    from dopeiptv.ui import media_cards as mc
    loader = FakeLoader()
    page = mc.PlacePage("Movies")
    view = mc.CardView(loader, "poster")
    page.set_content(view)
    view.set_items(_cards(600))
    page.resize(900, 700)
    page.show()
    _spin(app, 12)
    first = set(loader.asked)
    assert "img0" in first
    assert len(first) < 60                          # not 600
    page.area.verticalScrollBar().setValue(page.area.verticalScrollBar().maximum())
    _spin(app, 12)
    assert "img599" in loader.asked
    assert "img300" not in loader.asked             # never on screen
    page.close()


def test_channel_guide_asked_only_for_rows_that_paint(app):
    from dopeiptv.ui import media_cards as mc
    calls = []

    def guide(row):
        calls.append(row.key)
        return mc.NowNext("Show", time.time() - 60, time.time() + 600)

    view = mc.ChannelView(None, "airy")
    view.guide = guide
    view.set_items([mc.ChannelRow(key=f"c{i}", name=f"Ch {i}") for i in range(5000)])
    view.resize(700, 500)
    view.show()
    _spin(app)
    view.grab()
    assert 0 < len(set(calls)) < 40
    n = len(calls)
    view.grab()
    assert len(calls) == n                          # kept for the minute
    view.close()


def test_card_row_hidden_while_empty_and_arrows_for_more(app):
    from dopeiptv.ui import media_cards as mc
    row = mc.CardRow("Continue", None, picture="poster")
    row.resize(500, 120)
    row.set_cards([])
    assert not row.isVisible() and row.isHidden()
    row.set_cards(_cards(8, image=False))
    row.show()
    _spin(app)
    assert row.isVisible()
    assert row._on.isVisible() and not row._back.isVisible()
    row.strip.step(1)
    _spin(app)
    assert row._back.isVisible()


def test_header_sort_and_switch(app):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from dopeiptv.ui import media_cards as mc
    h = mc.PageHeader("Movies")
    h.show()
    h.set_count("2 140 movies")
    assert h.count.text() == "2 140 movies"
    assert h.sort_btn.isHidden() and h.switch.isHidden()
    h.set_sort([("new", "Newest"), ("az", "A-Z")], "new")
    h.set_views([("poster", "Grid"), ("list", "List")], "poster")
    assert h.sort_key() == "new" and h.view_key() == "poster"
    sorts, views = [], []
    h.sort_changed.connect(sorts.append)
    h.view_changed.connect(views.append)
    h._sort_group.actions()[1].trigger()
    assert sorts == ["az"] and h.sort_key() == "az" and "A-Z" in h.sort_btn.text()
    h.switch.buttons[1].click()
    assert views == ["list"]
    h.switch.setFocus()
    QTest.keyClick(h.switch, Qt.Key.Key_Left)
    assert views[-1] == "poster" and h.view_key() == "poster"
    h.close()


def test_header_controls_in_one_line_and_title_cut_with_an_ellipsis(app):
    """A filter beside the sort and the switch: one height, one foot (it
    stood taller and higher on macOS); a long title cut with "…" and whole
    in its tooltip; a narrow header's sort its mark alone."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QLineEdit, QSizePolicy
    from dopeiptv.ui import media_cards as mc
    title = "Premiere Cinema Collection 2026 and much more"
    h = mc.PageHeader(title)
    h.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    h.title.setMinimumWidth(0)
    filt = QLineEdit(objectName="Search")
    filt.setMaximumWidth(240)
    h.add_widget(filt)
    h.set_count("6443 movies")
    h.set_sort([("global", "Global default"), ("az", "A-Z")], "global")
    h.set_views([("poster", "Grid"), ("list", "List")], "poster")
    h.resize(640, 80)
    h.show()
    _spin(app)
    parts = (filt, h.sort_btn, h.switch)
    assert {w.height() for w in parts} == {mc.PageHeader.CONTROL_H}
    feet = {w.mapTo(h, w.rect().bottomLeft()).y() for w in parts}
    assert len(feet) == 1, feet
    shown = h.title.text()
    assert shown != title and shown.endswith("…"), shown
    assert h.title.toolTip() == title
    assert h.sort_btn.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    h.resize(400, 80)
    _spin(app)
    assert h.sort_btn.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    h.resize(1600, 80)
    _spin(app)
    assert h.title.text() == title and h.title.toolTip() == ""
    h.close()


def test_rtl_grid_starts_at_the_right(app):
    from PyQt6.QtCore import Qt
    from dopeiptv.ui import media_cards as mc
    view = mc.CardView(None, "poster")
    view.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    view.set_items(_cards(10, image=False))
    view.resize(800, 600)
    view.show()
    _spin(app)
    a = view.visualRect(view.model_.index(0, 0))
    b = view.visualRect(view.model_.index(1, 0))
    assert a.left() > b.left()
    m = view.metrics
    col = mc.left_column(a.x(), m, view.viewport().width(), True)
    assert col == m.cols - 1
    view.grab()                                     # paints without error
    view.close()
