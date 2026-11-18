"""The TV place's pieces (ui/tv_place.py) and its rows in the channel list
(ui/channel_list.ChannelDelegate): what is on now and next from a guide in
memory only (never a load), the rows' heights per density, a row's parts
compact and airy - "Then ..." in a column of its own or under the bar -
the one playing marked, a provider's heading "channel" as a heading,
mirrored right to left; the delegate painting live rows the place's way
(and every other list as before), asking the guide only for the rows that
paint, 20,000 channels laid out as uniform rows; the header's switch, sort
and count; Recently watched hidden while empty, and kept as it is when the
same channels come again."""
from __future__ import annotations

import os
import time
from array import array
from types import SimpleNamespace

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


NOW = 1_800_000_000.0


class Guide:
    """What the rows ask of providers/epg.XmltvGuide - and nothing that
    loads: a lookup that would load or download fails the test."""

    delay_minutes = 0

    def __init__(self, loaded=True):
        self.loaded = loaded
        self.asked = []
        # One channel, "ch1": 20:00-21:00 Kvällsnytt, 21:00-21:30 Debatten.
        self.sched = {"ch1": (array("q", [int(NOW - 3600), int(NOW - 1800), int(NOW + 1800)]),
                              array("q", [int(NOW - 1800), int(NOW + 1800), int(NOW + 3600)]),
                              ("Earlier", "Kvällsnytt", "Debatten"), ("", "", ""))}

    def is_loaded(self):
        return self.loaded

    def schedule(self, item):
        self.asked.append(item.get("stream_id"))
        if not self.loaded:
            return None
        return self.sched.get(item.get("epg_channel_id") or "")

    def ensure_loaded(self, *a, **k):          # pragma: no cover - must not be called
        raise AssertionError("a row's paint loaded the guide")

    _download = load_cached = ensure_loaded


# -- what is on ---------------------------------------------------------------------------

def test_now_and_next_from_the_guide_in_memory():
    from dopeiptv.ui.tv_place import guide_now_next
    g = Guide()
    nn = guide_now_next(g, {"stream_id": 1, "epg_channel_id": "ch1"}, now=NOW)
    assert nn.title == "Kvällsnytt" and nn.next_title == "Debatten"
    assert nn.start == NOW - 1800 and nn.stop == NOW + 1800 and nn.next_start == NOW + 1800
    assert nn.fraction(NOW) == pytest.approx(0.5)
    # Between programmes: only what comes next.
    gap = Guide()
    gap.sched["ch1"] = (array("q", [int(NOW + 600)]), array("q", [int(NOW + 1200)]),
                        ("Later",), ("",))
    nn = guide_now_next(gap, {"epg_channel_id": "ch1"}, now=NOW)
    assert nn.title == "" and nn.next_title == "Later" and nn.fraction(NOW) is None


def test_no_guide_in_memory_no_now_and_nothing_loaded():
    from dopeiptv.ui.tv_place import guide_now_next
    assert guide_now_next(None, {"stream_id": 1}) is None
    assert guide_now_next(Guide(loaded=False), {"epg_channel_id": "ch1"}, now=NOW) is None
    assert guide_now_next(Guide(), {"epg_channel_id": "nope"}, now=NOW) is None
    # After the guide's last programme: nothing.
    assert guide_now_next(Guide(), {"epg_channel_id": "ch1"}, now=NOW + 7200) is None


def test_the_guides_delay_is_its_own_now(monkeypatch):
    from dopeiptv.ui import tv_place
    monkeypatch.setattr(tv_place.time, "time", lambda: NOW + 1800 + 60)
    g = Guide()
    assert tv_place.guide_now_next(g, {"epg_channel_id": "ch1"}).title == "Debatten"
    g.delay_minutes = 5                         # Settings > Guide: 5 minutes behind
    assert tv_place.guide_now_next(g, {"epg_channel_id": "ch1"}).title == "Kvällsnytt"


# -- a row ---------------------------------------------------------------------------------

def test_row_heights_per_density(app):
    from dopeiptv.ui import tv_place
    assert tv_place.tv_row_height("compact") == 56
    assert tv_place.tv_row_height("airy") == 84
    assert tv_place.tv_row_height("bogus") == 56
    assert tv_place.tv_row_height("airy", info_line=True) == 84 + tv_place.info_line_h()


class Recorder:
    """A painter that notes every text it draws and where."""

    def __init__(self, w, h):
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QPainter, QPixmap
        self.pix = QPixmap(w, h)
        self.pix.fill(Qt.GlobalColor.black)
        texts = self.texts = []

        class P(QPainter):
            def drawText(self, *a):
                from PyQt6.QtCore import QRect, QRectF
                if len(a) == 3 and isinstance(a[0], (QRect, QRectF)):
                    texts.append((QRect(a[0].toRect() if isinstance(a[0], QRectF) else a[0]),
                                  str(a[2])))
                elif len(a) == 2:
                    texts.append((None, str(a[1])))
                super().drawText(*a)
        self.painter = P(self.pix)

    def end(self):
        self.painter.end()

    def at(self, needle):
        return next(r for r, t in self.texts if needle in t)


def _row(**kw):
    from dopeiptv.ui.media_cards import NowNext
    from dopeiptv.ui.tv_place import TvRow
    nn = NowNext("Kvällsnytt", NOW - 1800, NOW + 1800, "Debatten", NOW + 1800)
    base = {"name": "Kanal Ett", "number": "1", "now": nn, "now_at": NOW}
    base.update(kw)
    return TvRow(**base)


def test_compact_row_has_the_next_programme_in_a_column_of_its_own(app):
    from PyQt6.QtCore import QRect
    from dopeiptv.ui.tv_place import paint_tv_row
    rec = Recorder(900, 56)
    try:
        parts = paint_tv_row(rec.painter, QRect(0, 0, 900, 56), 0, 3, _row(), "compact", False)
    finally:
        rec.end()
    assert parts["next"].isValid() and parts["next"].left() > parts["now"].right()
    title = rec.at("Kvällsnytt")
    then = rec.at("Debatten")
    assert then.left() >= parts["next"].left()
    assert abs(then.center().y() - title.center().y()) <= 12      # the same line
    assert parts["num"].right() < parts["logo"].left() < parts["name"].left()
    assert rec.at("Kanal Ett").left() >= parts["name"].left()


def test_airy_row_has_the_next_programme_under_the_bar_and_a_bigger_logo(app):
    from PyQt6.QtCore import QRect
    from dopeiptv.ui.tv_place import paint_tv_row
    rec = Recorder(900, 84)
    try:
        parts = paint_tv_row(rec.painter, QRect(0, 0, 900, 84), 0, 3, _row(), "airy", False)
    finally:
        rec.end()
    assert not parts["next"].isValid()
    title, then = rec.at("Kvällsnytt"), rec.at("Debatten")
    # Under the title (and its bar), from the start of the column.
    assert then.top() > title.bottom() and then.left() == parts["now"].left()
    assert parts["logo"].height() == 52


def test_a_narrow_compact_row_drops_the_next_column(app):
    from PyQt6.QtCore import QRect
    from dopeiptv.ui.tv_place import paint_tv_row
    rec = Recorder(500, 56)
    try:
        parts = paint_tv_row(rec.painter, QRect(0, 0, 500, 56), 0, 3, _row(), "compact", False)
    finally:
        rec.end()
    assert not parts["next"].isValid()
    assert not any("Debatten" in t for _r, t in rec.texts)


def test_a_narrow_pane_stacks_the_name_over_what_is_on(app):
    from PyQt6.QtCore import QRect
    from dopeiptv.ui.tv_place import NARROW_FROM, paint_tv_row, tv_row_height
    for density, rtl in (("compact", False), ("airy", False), ("compact", True)):
        h = tv_row_height(density)
        w = NARROW_FROM - 100
        rec = Recorder(w, h)
        try:
            parts = paint_tv_row(rec.painter, QRect(0, 0, w, h), 1, 3, _row(playing=True),
                                 density, rtl)
        finally:
            rec.end()
        name, title = rec.at("Kanal Ett"), rec.at("Kvällsnytt")
        assert title.top() > name.bottom(), density
        assert parts["now"].width() == parts["name"].width() > w // 2
        assert 0 <= name.top() and title.bottom() < h
        assert not any(t == "Playing" for _r, t in rec.texts)     # the wash and bar say it


def test_the_playing_row_is_marked(app):
    from PyQt6.QtCore import QRect
    from PyQt6.QtGui import QColor
    from dopeiptv.ui.theme import P
    from dopeiptv.ui.tv_place import paint_tv_row
    rec = Recorder(900, 56)
    try:
        paint_tv_row(rec.painter, QRect(0, 0, 900, 56), 1, 3, _row(playing=True), "compact",
                     False)
    finally:
        rec.end()
    assert any(t == "Playing" for _r, t in rec.texts)
    img = rec.pix.toImage()
    bar = img.pixelColor(1, 28)
    assert bar == QColor(P["accent"]), bar.name()          # the accent's bar at the start
    rec = Recorder(900, 56)
    try:
        paint_tv_row(rec.painter, QRect(0, 0, 900, 56), 1, 3, _row(), "compact", False)
    finally:
        rec.end()
    assert not any(t == "Playing" for _r, t in rec.texts)
    assert rec.pix.toImage().pixelColor(1, 28) != QColor(P["accent"])


def test_right_to_left_is_mirrored(app):
    from PyQt6.QtCore import QRect
    from dopeiptv.ui.tv_place import paint_tv_row
    rec = Recorder(900, 56)
    try:
        ltr = paint_tv_row(rec.painter, QRect(0, 0, 900, 56), 1, 3, _row(playing=True),
                           "compact", False)
        rtl = paint_tv_row(rec.painter, QRect(0, 0, 900, 56), 1, 3, _row(playing=True),
                           "compact", True)
    finally:
        rec.end()
    for key in ("num", "logo", "name", "now", "next"):
        assert rtl[key].left() == 900 - 1 - ltr[key].right(), key
    assert rtl["num"].left() > rtl["logo"].left() > rtl["name"].left() > rtl["now"].left()


def test_marks_beside_the_name(app):
    from PyQt6.QtCore import QRect
    from dopeiptv.ui.tv_place import ARCHIVE, STAR, paint_tv_row
    rec = Recorder(900, 56)
    try:
        parts = paint_tv_row(rec.painter, QRect(0, 0, 900, 56), 1, 3,
                             _row(favourite=True, archive=True, health="#5FCB8A", tuner=True),
                             "compact", False)
    finally:
        rec.end()
    name = parts["name_text"]
    star, arch = rec.at(STAR), rec.at(ARCHIVE)
    assert name.left() > parts["name"].left()            # the dot and the ring before it
    assert star.left() >= name.right() and arch.left() > star.left()
    assert star.right() <= parts["name"].right() + 1


def test_a_name_is_never_clipped_to_its_measure(app):
    """Issue #35: on a scaled screen the drawn name came out a pixel or two
    wider than its measure and its last letter was cut off ("BBC Eart") -
    it is drawn without clipping, with a little room before it is cut."""
    from PyQt6.QtCore import QRect, Qt
    from PyQt6.QtGui import QPainter, QPixmap
    from dopeiptv.ui.tv_place import NAME_SLACK, paint_tv_row
    drawn = []

    class P(QPainter):
        def drawText(self, *a):
            if len(a) == 3:
                drawn.append((str(a[2]), int(a[1])))
            super().drawText(*a)
    for width in (376, 900):
        pix = QPixmap(width, 56)
        painter = P(pix)
        try:
            name = "BBC Earth (Geography and Wildlife)"
            parts = paint_tv_row(painter, QRect(0, 0, width, 56), 1, 3,
                                 _row(name=name, favourite=True, archive=True),
                                 "compact", False)
        finally:
            painter.end()
        text, flags = next((t, f) for t, f in drawn if t.startswith("BBC"))
        assert text == name or text.endswith("…"), text
        assert flags & int(Qt.TextFlag.TextDontClip)
        assert parts["name_text"].right() + NAME_SLACK < parts["name"].right() + 1
        drawn.clear()


def test_a_heading_channel_is_painted_as_a_heading(app):
    from PyQt6.QtCore import QRect
    from dopeiptv.ui.tv_place import heading_of, paint_tv_row
    assert heading_of("-= Sweden =-") == "Sweden"
    assert heading_of("SE| SVT1") is None
    rec = Recorder(900, 56)
    try:
        parts = paint_tv_row(rec.painter, QRect(0, 0, 900, 56), 1, 3,
                             _row(name="-= Sweden =-", heading="Sweden"), "compact", False)
    finally:
        rec.end()
    assert set(parts) == {"heading"}
    assert [t for _r, t in rec.texts] == ["SWEDEN"]


def test_the_technical_line_sits_under_what_is_on(app):
    from PyQt6.QtCore import QRect
    from dopeiptv.ui.tv_place import paint_tv_row, tv_row_height
    for density in ("compact", "airy"):
        h = tv_row_height(density, info_line=True)
        rec = Recorder(900, h)
        try:
            paint_tv_row(rec.painter, QRect(0, 0, 900, h), 1, 3,
                         _row(info="1920×1080 · 50 fps"), density, False)
        finally:
            rec.end()
        info, title = rec.at("50 fps"), rec.at("Kvällsnytt")
        assert info.top() > title.bottom() and info.bottom() < h, density


# -- the delegate ----------------------------------------------------------------------------

def _host(guide=None, playing=None):
    from PyQt6.QtWidgets import QWidget
    host = QWidget()
    host.settings = SimpleNamespace(value=lambda k, d=None: d)
    host._playing_key = playing
    host._playing_group = "live" if playing is not None else None
    host.item_tint = lambda *a: ("", "")
    host.channel_display_name = lambda it: it.get("name") or "?"
    host.cover = SimpleNamespace(cover_url=lambda *a: "", should_fetch=lambda *a: False)
    host.logos = SimpleNamespace(cache={}, get=lambda *a: None)
    host.watched_source = lambda *a: None
    host.is_item_on_watchlist = lambda *a: False
    host.is_favorite_item = lambda it, kind: it.get("stream_id") == 2
    host._timeshift_days = lambda it: 0
    host._item_key = lambda it: it.get("stream_id")
    host.xmltv = guide or Guide()
    return host


def test_live_rows_are_the_places_and_every_other_list_as_before(app):
    from PyQt6.QtCore import QModelIndex
    from PyQt6.QtWidgets import QStyleOptionViewItem
    from dopeiptv.ui.channel_list import ChannelDelegate, ChannelListModel
    host = _host()
    d = ChannelDelegate(host, "medium")
    m = ChannelListModel()
    m.set_items([{"stream_id": 1, "name": "A"}], "live")
    opt = QStyleOptionViewItem()
    assert d.sizeHint(opt, m.index(0)).height() == 56
    d.set_tv_density("airy")
    assert d.sizeHint(opt, m.index(0)).height() == 84
    d.set_tv_density("nonsense")
    assert d.tv_density == "compact"
    m.set_items([{"stream_id": 1, "name": "A"}], "vod")
    assert d.sizeHint(opt, m.index(0)).height() == d.row_h == 66
    m.set_items([{"stream_id": 1, "name": "A"}], "fav")
    assert d.sizeHint(opt, m.index(0)).height() == d.row_h
    m.set_items([{"stream_id": 1, "name": "A"}], "live")
    d.set_grid(True)
    assert d.sizeHint(opt, m.index(0)) == d.grid_size()   # a grid keeps its tiles
    assert QModelIndex() is not None
    host.close()


def test_the_guide_is_asked_only_for_the_rows_that_paint(app):
    from dopeiptv.ui.channel_list import ChannelDelegate, ChannelListModel, ChannelListView
    g = Guide()
    host = _host(g, playing=3)
    v = ChannelListView()
    m = ChannelListModel()
    v.setModel(m)
    d = ChannelDelegate(host, "medium")
    v.setItemDelegate(d)
    v.resize(700, 600)
    rows = [{"stream_id": i, "num": i, "name": f"Channel {i}",
             "epg_channel_id": "ch1" if i % 2 else ""} for i in range(1, 20_001)]
    t0 = time.perf_counter()
    m.set_items(rows, "live")
    v.show()
    v.doItemsLayout()
    _spin(app)
    took = time.perf_counter() - t0
    assert v.uniformItemSizes()
    assert v.visualRect(m.index(1)).height() == 56
    painted = set(g.asked)
    assert painted and len(painted) <= 600 // 56 + 4, len(painted)
    assert max(painted) < 30
    asked = len(g.asked)
    v.viewport().repaint()
    _spin(app)
    assert len(g.asked) == asked                  # kept for the minute
    d.forget_guide()
    v.viewport().repaint()
    _spin(app)
    assert len(g.asked) > asked                   # a new guide: asked again
    v.scrollTo(m.index(15_000))
    _spin(app)
    assert any(14_900 < sid < 15_100 for sid in g.asked)
    assert len(set(g.asked)) < 60
    assert took < 5.0
    v.close()
    host.close()


def test_the_delegate_paints_the_places_row_with_the_windows_facts(app):
    from PyQt6.QtCore import QRect
    from PyQt6.QtWidgets import QStyleOptionViewItem
    from dopeiptv.ui.channel_list import ChannelDelegate, ChannelListModel
    from dopeiptv.ui import tv_place
    host = _host(playing=2)
    host.channel_display_name = lambda it: "Renamed " + it["name"]
    d = ChannelDelegate(host, "medium")
    m = ChannelListModel()
    m.set_items([{"stream_id": 2, "num": 7, "name": "Two", "epg_channel_id": "ch1"}], "live")
    seen = []
    real = tv_place.paint_tv_row

    def spy(p, rect, row, count, r, density, rtl, **kw):
        seen.append(r)
        return real(p, rect, row, count, r, density, rtl, **kw)
    import dopeiptv.ui.channel_list as cl
    old = cl.paint_tv_row
    cl.paint_tv_row = spy
    try:
        rec = Recorder(800, 56)
        opt = QStyleOptionViewItem()
        opt.rect = QRect(0, 0, 800, 56)
        try:
            d.paint(rec.painter, opt, m.index(0))
        finally:
            rec.end()
    finally:
        cl.paint_tv_row = old
    r = seen[0]
    assert r.name == "Renamed Two" and r.number == "7"
    assert r.playing and r.favourite and not r.archive
    assert r.now is not None and "Earlier" in (r.now.title, r.now.next_title)
    assert r.now_at == pytest.approx(time.time(), abs=5)
    host.close()


# -- the header and Recently watched ---------------------------------------------------------

class FakeLoader:
    def __init__(self):
        self.cache = {}

    def is_dead(self, url):
        return False

    def get(self, url, cb):
        from PyQt6.QtGui import QColor, QPixmap
        pm = QPixmap(64, 64)
        pm.fill(QColor("#336699"))
        self.cache[url] = pm
        cb(pm)


def test_the_header_its_switch_and_recently_watched(app):
    from PyQt6.QtWidgets import QWidget, QVBoxLayout
    from dopeiptv.ui.media_cards import Card
    from dopeiptv.ui.tv_place import TvPlaceTop
    host = QWidget()
    lay = QVBoxLayout(host)
    top = TvPlaceTop(FakeLoader())
    lay.addWidget(top)
    lay.addStretch(1)
    host.resize(900, 400)
    host.show()
    _spin(app)
    picked = []
    top.density_changed.connect(picked.append)
    top.set_title("Sweden")
    top.set_count("48 channels · by number")
    top.set_sort([("global", "Global default"), ("alpha_asc", "A→Z")], "alpha_asc")
    top.set_density("airy")
    assert top.header.title.text() == "Sweden" and top.header.title.isVisible()
    assert top.header.accessibleName() == "Sweden"
    assert top.header.count.text() == "48 channels · by number"
    assert top.header.sort_key() == "alpha_asc"
    assert top.header.view_key() == "airy" and top.header.switch.isVisible()
    top.header.switch.buttons[0].click()
    assert picked == ["compact"]
    top.set_density("compact", shown=False)       # a grid of tiles: no density
    assert not top.header.switch.isVisible()
    top.set_density("compact", shown=True)
    assert top.header.switch.isVisible()
    # Empty: hidden. Cards: shown; the same channels again keep the strip.
    top.set_recent([])
    assert not top.recent.isVisible()
    cards = [Card(key="1", title="Kanal Ett", line="Kvällsnytt", image="http://l/1"),
             Card(key="4", title="Sport HD", line="Hockey", image="http://l/4")]
    top.set_recent(cards)
    _spin(app)
    assert top.recent.isVisible() and top.recent.keys() == ["1", "4"]
    model = top.recent.strip.model_
    resets = []
    model.modelReset.connect(lambda: resets.append(1))
    top.set_recent([Card(key="1", title="Kanal Ett", line="Debatten"),
                    Card(key="4", title="Sport HD", line="Hockey")])
    assert not resets and top.recent.cards()[0].line == "Debatten"
    top.set_recent(list(reversed(cards)))
    assert resets and top.recent.keys() == ["4", "1"]
    top.set_playing("1")
    assert top.recent.strip.playing_key == "1"
    # A narrow pane: the title cut, the sort its mark alone, the switch away.
    top.set_title("A very long category name that will not fit in a narrow pane at all")
    host.resize(300, 400)
    _spin(app)
    from PyQt6.QtCore import Qt
    assert top.header.sort_btn.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    assert not top.header.switch.isVisible()
    assert top.header.title.text().endswith("…")
    assert top.minimumSizeHint().width() <= 300
    host.resize(900, 400)
    _spin(app)
    assert top.header.switch.isVisible()
    host.close()
