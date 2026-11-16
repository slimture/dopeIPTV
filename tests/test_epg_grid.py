"""EPG guide grid regressions.

Covers: channels without EPG data are clickable/playable; arrow-key cell
navigation, description panel and progress fills; the rebuild when the
guide finishes loading after the guide opened; no timer running while the
guide is off screen; and the board opening on "now" in a right-to-left
language. The guide (EpgGrid) is tested here in its window (EpgGridDialog);
tests/test_guide_page.py has it as the window's Guide page.

Runs in a subprocess like the multiview test: the dialog needs a real
MainWindow (whose embedded player owns an offscreen QOpenGLWidget), and
those abort at Qt's interpreter-exit teardown when several accumulate in
one process - it also destabilises later Qt tests in the suite. We assert
on the child's success marker and ignore its exit status.
"""
import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QPointF, QSettings
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (QApplication, QDialog, QGraphicsPixmapItem,
                             QGraphicsSimpleTextItem)

from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.epg_grid import EpgGridDialog
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.theme import P

app = QApplication.instance() or QApplication([])
settings = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"),
                     QSettings.Format.IniFormat)
w = MainWindow(DemoClient(), settings)
now = time.time()

# ---- 1. A channel without EPG is clickable and playable -------------------
chans = [
    {"name": "With EPG", "stream_id": 1, "num": 1, "epg_channel_id": "x.se"},
    {"name": "No EPG", "stream_id": 2, "num": 2},
]
d = EpgGridDialog(w, chans)
g = d.grid
d.resize(1200, 600)
d.show()
app.processEvents()
# In its window: Close, no Pop out, the name in the title bar.
assert g.close_btn.isVisible() and not g.popout_btn.isVisible()
assert not g.title_lbl.isVisible()

# The guide opens scrolled to "now" (wall-clock dependent), which shifts
# the pinned channel column in scene coords. Real clicks go through
# mapToScene so the app is fine - but this test clicks raw scene points,
# so pin the view at origin first to make them deterministic.
g.view.horizontalScrollBar().setValue(0)
g.view.verticalScrollBar().setValue(0)
app.processEvents()

g._select_at(QPointF(50, g.HEADER_H + g.ROW_H + 10))     # name cell, row 1
assert g._selected and g._selected["channel"]["name"] == "No EPG"
assert g._selected["prog"] is None
assert g.play_btn.isEnabled()
g._select_at(QPointF(g.CH_COL_W + 300, g.HEADER_H + g.ROW_H + 10))
assert g._selected["channel"]["name"] == "No EPG"

tuned = []
w.tune_from_guide = lambda ch: tuned.append(ch["name"])
g._play_selected()
assert tuned == ["No EPG"], tuned
# Its window closes on a play, as the guide's window always did.
assert d.result() == QDialog.DialogCode.Accepted and not d.isVisible()
d.deleteLater(); app.processEvents()

# ---- 2. Navigation, description panel, progress, day jumps ----------------
def fake_programmes(ch, a, b):
    if ch.get("stream_id") != 1:
        return []
    # Kept well inside the board's own window. It opens at the current half
    # hour minus thirty minutes, so a programme placed an hour and a half back
    # sits on that edge - and whether it lands inside depends on the seconds
    # on the clock when the test runs. It passed here and failed on CI at
    # 07:30 for no other reason.
    return [
        {"title": "Earlier", "description": "",
         "start_timestamp": now - 1500, "stop_timestamp": now - 900},
        {"title": "On Air", "description": "A described programme.",
         "start_timestamp": now - 900, "stop_timestamp": now + 900},
        {"title": "Later", "description": "",
         "start_timestamp": now + 900, "stop_timestamp": now + 3600},
    ]

w.xmltv.programmes_in = fake_programmes
d = EpgGridDialog(w, chans)
g = d.grid
d.resize(1200, 600)
d.show()
app.processEvents()

assert len(g._rows) == 2
assert len(g._rows[0][1]) == 3          # three programme blocks
assert len(g._rows[1][1]) == 1          # the no-EPG filler

g._nav(1, 0)                            # first press -> the on-air block
assert g._selected["prog"]["title"] == "On Air"
assert g.desc.isVisible() and "described" in g.desc.text()
g._nav(1, 0)                            # right -> next programme
assert g._selected["prog"]["title"] == "Later"
assert not g.desc.isVisible()
g._nav(0, 1)                            # down -> the no-EPG row's filler
assert g._selected["channel"]["name"] == "No EPG"
assert g._selected["prog"] is None
g._nav(0, -1)                           # up -> back to a programme
assert g._selected["channel"]["name"] == "With EPG"

# Exactly one card is marked on-air ("now" state), and the live tick
# (which rolls the highlight forward) must not raise.
now_cards = [rb for _c, blocks in g._rows for rb in blocks
             if rb.get("_state") == "now"]
assert len(now_cards) == 1, len(now_cards)
g._tick()
# The theme's own inks on the cards and the channel names: white ones were
# all but invisible on a light theme's pale board. The names in its quieter
# ink, the titles (what is read) in its text colour.
assert now_cards[0]["label"].brush().color() == QColor(P["text"])
names = [it for in_group, it in g._row_items[0]
         if in_group and isinstance(it, QGraphicsSimpleTextItem)]
assert names and names[0].brush().color() == QColor(P["text2"])

g._scroll_hours(24); g._scroll_hours(-24); g._scroll_tonight()
d.deleteLater(); app.processEvents()

# ---- 3. Rebuild when the EPG finishes loading after open ------------------
# The window's own startup guide load runs async and, when it concludes as
# FAILED (no network on CI), sets xmltv._failed - which _epg_ready() treats
# as "concluded", so the guide would skip the loading-band state we're
# testing. Wait for that load to conclude, then pin both flags under stubs.
deadline = time.time() + 20
while time.time() < deadline and not (w.xmltv.is_loaded()
                                      or getattr(w.xmltv, "_failed", False)):
    app.processEvents(); time.sleep(0.02)
state = {"loaded": False}
w.xmltv.is_loaded = lambda: state["loaded"]
w.xmltv._failed = False
w.xmltv.programmes_in = lambda ch, a, b: ([] if not state["loaded"] else [
    {"title": "P", "description": "",
     "start_timestamp": now - 600, "stop_timestamp": now + 600}])

one = [{"name": "One", "stream_id": 1, "num": 1, "epg_channel_id": "one.se"}]
d = EpgGridDialog(w, one)
g = d.grid
app.processEvents()
# Nothing ticks before the guide is on screen ...
assert not g._epg_poll.isActive() and not g._refresh.isActive()
d.show()
app.processEvents()
assert g._epg_poll.isActive()                        # polling while unloaded
assert g._refresh.isActive()
assert g._rows[0][1][0]["data"]["prog"] is None      # loading band
state["loaded"] = True
g._maybe_reload_epg()
assert not g._epg_poll.isActive()
assert g._rows[0][1][0]["data"]["prog"]["title"] == "P"
d.deleteLater(); app.processEvents()

# ... nor once it is off screen; what came in meanwhile is drawn as it
# comes back, and a refresh asked for is still waited for.
state["loaded"] = False
d = EpgGridDialog(w, one)
g = d.grid
d.show()
app.processEvents()
assert g._epg_poll.isActive() and g._refresh.isActive()
w._refresh_epg_now = lambda: None
w._guide_loading = object()
g._refresh_guide()
assert g._guide_poll.isActive()
d.hide()
app.processEvents()
assert not any(t.isActive() for t in (g._refresh, g._epg_poll,
                                      g._guide_poll, g._preload,
                                      g._preload_more))
state["loaded"] = True
d.show()
app.processEvents()
assert g._refresh.isActive() and not g._epg_poll.isActive()
assert g._guide_poll.isActive()
assert g._rows[0][1][0]["data"]["prog"]["title"] == "P"
w._guide_loading = None
g._after_refresh()
assert not g._guide_poll.isActive() and g.refresh_btn.isEnabled()
d.deleteLater(); app.processEvents()

# ---- 4. "All" with thousands of channels: every one has a row -------------
# The guide used to stop at 300 channels, so "All" with a big provider showed
# only its first country. Rows are now drawn as they scroll into view, and the
# ones far away are taken down again.
w.xmltv.programmes_in = lambda ch, a, b: []
many = [{"name": f"Filler {i}", "stream_id": 1000 + i, "num": i}
        for i in range(3000)]
many.append({"name": "V Sport Premium SE", "stream_id": 42, "num": 9999})
d = EpgGridDialog(w, many)
g = d.grid
d.resize(1200, 600)
d.show()
app.processEvents()
last = len(many) - 1
assert len(g._rows) == len(many)
assert 0 < len(g._row_items) <= g.KEEP_ROWS
assert not g._rows[last][1]                    # far down: not drawn yet
# Scroll sideways first, then to the very bottom: the last row is drawn, and
# its channel cell sits in the pinned column, not where the column was.
g.view.horizontalScrollBar().setValue(600)
vbar = g.view.verticalScrollBar()
vbar.setValue(vbar.maximum())
app.processEvents()
ch, blocks = g._rows[last]
assert ch["name"] == "V Sport Premium SE" and blocks
cell = next(it for in_group, it in g._row_items[last]
            if in_group and isinstance(it.data(0), dict))
col_left = g.view.mapToScene(0, 0).x()
assert col_left > 100, col_left
assert abs(cell.sceneBoundingRect().x() - col_left) < 2, (
    cell.sceneBoundingRect().x(), col_left)
# Walking the whole line-up keeps only a few hundred rows in the scene.
for v in range(0, vbar.maximum() + 1, g.ROW_H * 25):
    vbar.setValue(v)
    assert len(g._row_items) <= g.KEEP_ROWS, len(g._row_items)
assert not g._rows[0][1]                       # the top was taken down
# Keyboard navigation reaches a row that was taken down.
g._focus = (0, 0)
g._nav(0, 1)
assert g._selected["channel"]["name"] == "Filler 1"
# Filtering still searches the whole list.
g.filter.setText("v sport")
app.processEvents()
assert [c.get("name") for c, _b in g._rows] == ["V Sport Premium SE"]
assert g._rows[0][1]
d.deleteLater(); app.processEvents()

# ---- 5. A logo arriving after the board scrolled lands in its row ---------
# Logos load async. The channel column is pinned by translating its group by
# the scroll offset, so a logo added later must be positioned in GROUP
# coordinates - positioning it in scene coordinates parked it hundreds of px
# left of the visible column, which is why logos only appeared the second
# time the guide was opened (cached -> callback ran during the build).
pending_logos = []
class _StubLogos:
    def get(self, url, cb):
        pending_logos.append(cb)
w.logos = _StubLogos()
w.xmltv.programmes_in = lambda ch, a, b: []
d = EpgGridDialog(w, [{"name": "Logo", "stream_id": 7, "num": 1,
                       "stream_icon": "http://example.invalid/logo.png"}])
g = d.grid
d.resize(900, 400)
d.show()
app.processEvents()
g.view.horizontalScrollBar().setValue(600)     # pin the column away from x=0
app.processEvents()
assert pending_logos, "no logo was requested"
pm = QPixmap(38, 24); pm.fill(QColor("#ff0000"))
pending_logos[0](pm)
logo_items = [i for i in g.scene.items() if isinstance(i, QGraphicsPixmapItem)]
assert len(logo_items) == 1, logo_items
lx = logo_items[0].sceneBoundingRect().x()
col_left = g.view.mapToScene(0, 0).x()
assert col_left > 100, col_left                # the view really did scroll
assert col_left <= lx <= col_left + g.CH_COL_W, (lx, col_left)
d.deleteLater(); app.processEvents()

# ---- 6. Only the card is clickable - the title is paint on top of it ------
w.logos = None
w.xmltv.programmes_in = lambda ch, a, b: [
    {"title": "Clickable Title", "description": "",
     "start_timestamp": now - 1800, "stop_timestamp": now + 3600}]
d = EpgGridDialog(w, [{"name": "One", "stream_id": 1, "num": 1}])
g = d.grid
d.resize(1200, 600)
d.show()
g.view.horizontalScrollBar().setValue(0)
g.view.verticalScrollBar().setValue(0)
app.processEvents()
rb = g._rows[0][1][0]
label_rect = rb["label"].sceneBoundingRect()
assert rb["label"].data(0) is None             # carries no click payload
item, data = g._hit(QPointF(label_rect.x() + 4, label_rect.center().y()))
assert item is rb["item"], item                # the card, not the text item
assert data["prog"]["title"] == "Clickable Title"
d.deleteLater(); app.processEvents()

# ---- 7. A right-to-left language opens the board on "now" too ---------------
# Qt inverts a view's horizontal scrolling under a right-to-left layout, and
# the guide's scroll positions are left-to-right scene coordinates: Arabic,
# Persian, Hebrew and Urdu opened it hours away from now, on an empty board.
from PyQt6.QtCore import Qt
app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
d = EpgGridDialog(w, [{"name": "One", "stream_id": 1, "num": 1}])
g = d.grid
d.resize(1200, 600)
d.show()
app.processEvents()
vp = g.view.viewport().rect()
left = g.view.mapToScene(vp.topLeft()).x()
right = g.view.mapToScene(vp.topRight()).x()
now_x = g._x(time.time())
assert left <= now_x <= right, (left, now_x, right)
# The day jumps run along the board's time axis there too: back on the left.
assert (g.day_back_btn.mapTo(g, g.day_back_btn.rect().topLeft()).x()
        < g.day_fwd_btn.mapTo(g, g.day_fwd_btn.rect().topLeft()).x())
app.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
d.deleteLater(); app.processEvents()

# ---- 8. The calm look: headings, empty rows, now line, column -------------
from PyQt6.QtWidgets import QGraphicsRectItem
from dopeiptv.i18n import tr
w.xmltv.is_loaded = lambda: True
w.xmltv._failed = False
def calm_progs(ch, a, b):
    if ch.get("stream_id") not in (1, 5):
        return []
    return [
        # From the board's start (a), so it is never clipped to a sliver
        # whatever the minute: at least 20 minutes wide, room for its title.
        {"title": "Aired", "description": "",
         "start_timestamp": a, "stop_timestamp": now - 600},
        {"title": "On Air", "description": "",
         "start_timestamp": now - 600, "stop_timestamp": now + 1800},
        {"title": "Nyheter", "description": "",            # five minutes
         "start_timestamp": now + 1800, "stop_timestamp": now + 2100},
        {"title": "Later", "description": "",
         "start_timestamp": now + 2100, "stop_timestamp": now + 9000},
    ]
w.xmltv.programmes_in = calm_progs
calm = [
    {"name": "-= Sweden =-", "stream_id": 100, "num": 1},
    {"name": "SVT1 HD SE", "stream_id": 1, "num": 2},
    {"name": "N1 Info & Updates SE", "stream_id": 2, "num": 3},
    {"name": "#### SPORT ####", "stream_id": 101, "num": 4},
    {"name": "--- With a guide ---", "stream_id": 5, "num": 5},
]
d = EpgGridDialog(w, calm)
g = d.grid
d.resize(1200, 600)
d.show()
app.processEvents()
g.view.horizontalScrollBar().setValue(0)
g.view.verticalScrollBar().setValue(0)
app.processEvents()
assert g.ROW_H == 44 and g.CH_COL_W == g.COL_W == 160
# A heading row: nothing in it to pick, no card - unless it has a guide.
assert g._rows[0][1] == [] and g._rows[3][1] == []
assert len(g._rows[4][1]) == 4
y0 = g.HEADER_H + 10
assert g._hit(QPointF(40, y0)) == (None, None)
assert g._hit(QPointF(g.CH_COL_W + 200, y0)) == (None, None)
g._selected = None
g._select_at(QPointF(40, y0))
assert g._selected is None
assert not any(isinstance(it.data(0), dict) for _in, it in g._row_items[0])
words = [it.text() for _in, it in g._row_items[0]
         if isinstance(it, QGraphicsSimpleTextItem)]
assert words == ["Sweden"], words
# Arrows step over a heading, and start below one at the top.
g._focus = None
g._nav(0, 0)
assert g._focus[0] == 1
g._nav(0, 1)
assert g._focus[0] == 2
g._nav(0, 1)
assert g._focus[0] == 4, g._focus            # over "#### SPORT ####"
g._nav(0, -1)
assert g._focus[0] == 2
assert g._step_row(1, -1) is None            # only a heading above
# A row without a guide: an empty field, still playable, its text in the
# tooltip - not on the row (another row has a guide: no line over the board).
field = g._rows[2][1][0]
assert field["data"]["prog"] is None and field["label"] is None
assert field["item"].toolTip() == tr("epg_no_guide_available")
assert not g._note.isVisible()
# Flat cards, no border; the on-air one tinted, with a strip up to the "now"
# line in the accent; the aired one dimmed.
blocks = {rb["data"]["prog"]["title"]: rb for rb in g._rows[1][1]}
on_air, aired = blocks["On Air"], blocks["Aired"]
assert on_air["item"].pen().style() == Qt.PenStyle.NoPen
assert on_air["item"].brush().color() == g._tone("block_now")
assert aired["item"].brush().color() == g._tone("block_past")
assert aired["label"].brush().color() == QColor(P["muted"])
strip = on_air["strip"]
assert strip is not None and strip.parentItem() is on_air["item"]
assert strip.brush().color() == QColor(P["accent"])
now_x = g._x(time.time())
assert abs(strip.rect().right() - now_x) < 2, (strip.rect().right(), now_x)
assert abs(g._now_line.pos().x() - now_x) < 2 and not g._now_line.isVisible()   # off by default
w.settings.setValue("guide_now_line", "true")
g._place_now_line(time.time())
assert g._now_line.isVisible()
assert abs(g._now_head.pos().x() - now_x) < 2
assert g._now_head.parentItem() is g._header_group
# The strip is paint: a click on it is the card's.
r = strip.sceneBoundingRect()
assert g._hit(r.center())[0] is on_air["item"]
# A wide card says when it starts; a five-minute one shows no "Ny…".
assert on_air["time"] is not None
assert on_air["time"].text() == time.strftime(
    "%H:%M", time.localtime(on_air["data"]["prog"]["start_timestamp"]))
assert blocks["Nyheter"]["label"] is None
assert "Nyheter" in blocks["Nyheter"]["item"].toolTip()
# The on-air card rolls on with the clock: aired, it loses its strip.
real_time = time.time
time.time = lambda: now + 1900
try:
    g._mark_now()
    assert on_air["_state"] == "past" and on_air["strip"] is None
    assert strip.scene() is None
    assert blocks["Nyheter"]["strip"] is not None
finally:
    time.time = real_time
g._mark_now()
# The channel column's edge is dragged (and remembered), narrower than names
# need is the logos alone, a double-click puts it back.
assert g._col_edge_at(g.CH_COL_W + 2) and not g._col_edge_at(g.CH_COL_W + 20)
g._col_drag_start(g.CH_COL_W)
g._col_drag_move(260)
g._col_drag_end(260)
app.processEvents()
assert g.CH_COL_W == 260 and int(settings.value(g.COL_KEY)) == 260
g._col_drag_start(g.CH_COL_W)
g._col_drag_end(g.CH_COL_W + 1)               # a click, no drag: unchanged
assert g.CH_COL_W == 260
g._col_drag_start(g.CH_COL_W)
g._col_drag_end(80)
assert g.CH_COL_W == g.COL_LOGOS_W and int(settings.value(g.COL_KEY)) == g.COL_LOGOS_W
g._col_reset()
assert g.CH_COL_W == 160
d.deleteLater(); app.processEvents()
# A new guide opens with the width kept.
settings.setValue("guide_channel_col", 220)
d = EpgGridDialog(w, calm)
g = d.grid
d.resize(1200, 600)
d.show()
app.processEvents()
assert g.CH_COL_W == 220
# A narrow board: the logos alone, the name in the cell's tooltip, a short
# form of it where there is no logo; the edge is not offered there.
d.resize(700, 600)
for _ in range(5):
    app.processEvents()
time.sleep(0.2)
for _ in range(5):
    app.processEvents()
g._refit()
assert g.view.viewport().width() < g.NARROW_VW
assert g.CH_COL_W == g.COL_LOGOS_W
cell = next(it for in_group, it in g._row_items[2]
            if in_group and isinstance(it, QGraphicsRectItem)
            and isinstance(it.data(0), dict))
assert cell.toolTip() == "N1 Info & Updates SE"
texts = [it.text() for in_group, it in g._row_items[2]
         if in_group and isinstance(it, QGraphicsSimpleTextItem)]
assert texts == ["N1"], texts
assert not g._col_edge_at(g.CH_COL_W)
settings.remove("guide_channel_col")
d.deleteLater(); app.processEvents()
# No row with a guide at all: said once, over the board, not on each row.
w.xmltv.programmes_in = lambda ch, a, b: []
d = EpgGridDialog(w, calm)
g = d.grid
d.resize(1200, 600)
d.show()
app.processEvents()
assert g._note.isVisible() and g._note.text() == tr("epg_no_guide_any")
assert all(rb["label"] is None for _c, bl in g._rows for rb in bl)
d.deleteLater(); app.processEvents()

print("EPG_GRID_OK")
"""


def test_epg_grid_interactions():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    # One retry: the child needs an offscreen OpenGL context (the MainWindow
    # embeds a QOpenGLWidget), and creating one can fail spuriously when the
    # machine is under heavy load - the child then dies before printing its
    # marker without any assertion having failed. A genuine regression fails
    # both attempts identically, so the retry can't mask one.
    for _attempt in (1, 2):
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD], capture_output=True, text=True,
            env=env, cwd=_REPO_ROOT, timeout=180)
        if "EPG_GRID_OK" in proc.stdout:
            return
    assert "EPG_GRID_OK" in proc.stdout, (
        f"EPG grid checks failed\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-2000:]!r}")


def test_recording_from_the_guide_takes_the_detail_panels_path():
    # The guide added a job of its own, so an on-air programme on the channel
    # being watched opened a second connection instead of recording the
    # stream already playing.
    from types import SimpleNamespace
    from unittest import mock

    from dopeiptv.ui.epg_grid import EpgGrid

    win = SimpleNamespace(_record_programme=mock.Mock())
    ch = {"stream_id": 5, "name": "One"}
    p = {"title": "News", "start_timestamp": 100.0, "stop_timestamp": 200.0}
    info = mock.Mock()
    EpgGrid._record(SimpleNamespace(window=win, info=info), ch, p)
    win._record_programme.assert_called_once_with(
        ch, {"title": "News"}, 100.0, 200.0)


def test_the_now_line_shows_only_when_turned_on():
    """Off by default (behind the cards it looked broken); turned on in
    Settings > Guide, over the cards and under the titles."""
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication

    from dopeiptv.ui.epg_grid import EpgGrid

    QApplication.instance() or QApplication([])
    s = QSettings("dopeIPTV-test", "now-line")
    s.clear()

    class _W:
        settings = s

    grid = EpgGrid.__new__(EpgGrid)
    grid.window = _W()
    assert not grid._now_shown()
    s.setValue(EpgGrid.NOW_LINE_KEY, "true")
    assert grid._now_shown()
    s.clear()


def test_a_time_cut_by_the_channel_column_is_hidden():
    """A half hour's label starting under the pinned channel column showed
    only its last digit ("0" of 21:00) beside it."""
    from PyQt6.QtWidgets import QApplication, QGraphicsSimpleTextItem

    from dopeiptv.ui.epg_grid import EpgGrid

    app = QApplication.instance() or QApplication([])   # kept: the items need it
    grid = EpgGrid.__new__(EpgGrid)
    cut, clear = QGraphicsSimpleTextItem("21:00"), QGraphicsSimpleTextItem("21:30")
    cut.setPos(150, 0)
    clear.setPos(210, 0)
    grid._time_labels = [cut, clear]
    grid._hide_cut_times(160)
    assert not cut.isVisible() and clear.isVisible()
    grid._hide_cut_times(100)                  # scrolled clear of the column
    assert cut.isVisible()
    del app
