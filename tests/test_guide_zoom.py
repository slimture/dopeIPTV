"""The guide's zoom: the time scale fits the room the board has (Fit, the
default) in steady steps; − / +, Ctrl + wheel and Ctrl + = / - / 0 pick a
span that stays until Fit; the page and the window each remember their own
(written only when it changes); a new scale keeps the time at the left edge
(or "now" in view), puts every programme, the on-air card and the header
where their times are, keeps the selection, and lays out only the rows on
screen - with 10,000 channels too. A few pixels more or less keep the scale
(nothing drawn anew); the rows beyond the view follow a few at a time.

Runs in a subprocess like tests/test_epg_grid.py: the guide needs a real
MainWindow, whose embedded player owns an offscreen QOpenGLWidget; the
child prints a marker when every check passed.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytest.importorskip("PyQt6.QtWidgets")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_fit_span_grows_with_the_width_in_steps():
    from dopeiptv.ui.epg_grid import EpgGrid

    widths = list(range(100, 3200, 7))
    spans = [EpgGrid.auto_span(w) for w in widths]
    assert spans == sorted(spans)
    assert set(spans) == set(EpgGrid.ZOOM_SPANS)
    for w, s in zip(widths, spans, strict=True):
        # As many hours as keep a half hour wide enough, and not one step more.
        if s > EpgGrid.ZOOM_SPANS[0]:
            assert w / (s * 2) >= EpgGrid.MIN_HALF_HOUR_W, (w, s)
        bigger = [x for x in EpgGrid.ZOOM_SPANS if x > s]
        if bigger:
            assert w / (bigger[0] * 2) < EpgGrid.MIN_HALF_HOUR_W, (w, s)
    # A few pixels either way do not change the span.
    assert EpgGrid.auto_span(700) == EpgGrid.auto_span(704)
    assert EpgGrid.auto_span(50) == 1.0 and EpgGrid.auto_span(10_000) == 6.0


_CHILD = r"""
import os, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtGui import QKeyEvent, QWheelEvent
from PyQt6.QtWidgets import QApplication, QGraphicsSimpleTextItem

from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.epg_grid import EpgGrid, EpgGridDialog
from dopeiptv.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
settings = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"),
                     QSettings.Format.IniFormat)
w = MainWindow(DemoClient(), settings)
w.xmltv.is_loaded = lambda: True
now = time.time()
SPANS = EpgGrid.ZOOM_SPANS
LONG = "A programme title far too long for any half-hour card at all"

writes = []
_set = settings.setValue
def counted_set(key, value):
    if str(key).startswith("guide_zoom"):
        writes.append((key, value))
    _set(key, value)
settings.setValue = counted_set


def progs(ch, a, b):
    # Half-hour programmes on the half hours, a five-minute one in each
    # hour, and a long title now and then.
    out, t, i = [], (a // 1800) * 1800, 0
    while t < b:
        d = 300 if i % 3 == 2 else 1800
        title = LONG if i % 4 == 1 else f"P{i} {ch.get('name')}"
        out.append({"title": title, "description": "",
                    "start_timestamp": t, "stop_timestamp": t + d})
        t += d
        i += 1
    return out


w.xmltv.programmes_in = progs


def settle(g):
    for _ in range(5):
        app.processEvents()
    deadline = time.time() + 5
    while g._fit_timer.isActive() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()


def area(g):
    return g.view.viewport().width() - g.CH_COL_W


def shown(g):
    left = g.left_time()
    return left, left + area(g) / g._ppm * 60


def check_layout(g):
    ppm = g._ppm
    # The board shows its span - give or take the few pixels a resize too
    # small to draw it anew leaves (REFIT_TOLERANCE).
    assert abs(area(g) / ppm / 60 - g.span_hours()) <= (
        g.span_hours() * g.REFIT_TOLERANCE * 1.1 + 0.01), (
        area(g), ppm, g.span_hours())
    # Every programme drawn where its time is, the on-air card under "now" -
    # now as the check runs: the test takes minutes, and a programme ending
    # meanwhile (a five-minute one, or the half hour) made the card marked
    # at the start the wrong one (CI failed so, once, at 19:30).
    x = lambda ts: g.CH_COL_W + (ts - g._start) / 60 * ppm
    g._mark_now()
    t_now = time.time()
    seen = on_air = 0
    for _ch, blocks in g._rows:
        for rb in blocks:
            p = rb["data"]["prog"]
            if p is None:
                continue
            x1 = max(g.CH_COL_W, x(p["start_timestamp"]))
            x2 = min(g.CH_COL_W + g._grid_w, x(p["stop_timestamp"]))
            r = rb["item"].path().boundingRect()
            assert abs(r.x() - (x1 + 1)) < 0.5, (r.x(), x1)
            assert abs(r.width() - (x2 - x1 - 3)) < 0.5, (r.width(), x2 - x1)
            if rb["_state"] == "now":
                on_air += 1
                assert p["start_timestamp"] <= t_now < p["stop_timestamp"] + 1, (
                    p["start_timestamp"], t_now, p["stop_timestamp"])
            seen += 1
    assert seen and on_air, (seen, on_air)
    # The header: a label at every half hour, or the hours alone once a
    # half hour is too narrow for its label - each at its time.
    marks = g._half_hours()
    labels = sorted((it.pos().x(), it.text()) for it in g._header_group.childItems()
                    if isinstance(it, QGraphicsSimpleTextItem))
    from datetime import datetime
    want = {round(x(t) + 4): datetime.fromtimestamp(t).strftime("%H:%M") for t in marks}
    for lx, text in labels:
        assert want.get(round(lx)) == text, (lx, text)
    if len(labels) < len(marks):             # the hours alone: narrow ones
        assert 30 * ppm < 60 and len(labels) <= (len(marks) + 1) // 2, (
            ppm, len(labels), len(marks))
    else:                                    # every half hour: room for each
        assert 30 * ppm >= 40, ppm
    return len(labels) < len(marks)


def key(target, k, mods=Qt.KeyboardModifier.ControlModifier):
    over = QKeyEvent(QEvent.Type.ShortcutOverride, k, mods, "")
    app.sendEvent(target, over)
    app.sendEvent(target, QKeyEvent(QEvent.Type.KeyPress, k, mods, ""))
    return over.isAccepted()


def wheel(g, dy, mods=Qt.KeyboardModifier.ControlModifier):
    vp = g.view.viewport()
    pos = QPointF(vp.width() / 2, vp.height() / 2)
    ev = QWheelEvent(pos, QPointF(vp.mapToGlobal(pos.toPoint())), QPoint(0, 0),
                     QPoint(0, dy), Qt.MouseButton.NoButton, mods,
                     Qt.ScrollPhase.NoScrollPhase, False)
    app.sendEvent(vp, ev)


chans = [{"name": f"Ch {i}", "stream_id": i + 1, "num": i + 1} for i in range(40)]

# ---- 1. Fit by default: the span the room gives, stepped -----------------
d = EpgGridDialog(w, chans)
g = d.grid
d.resize(1200, 640)
d.show()
settle(g)
assert g.fit_btn.isChecked() and g.fit_btn.isCheckable()
assert g.span_hours() == EpgGrid.auto_span(area(g))
assert g._fitted_vw == g.view.viewport().width()
check_layout(g)
# Opened on now, and it stays in view.
lo, hi = shown(g)
assert lo <= now <= hi, (lo, now, hi)
# A scheduled recording marks its programme; a finished one does not.
real_jobs = list(w.rec.jobs)
sid = chans[0]["stream_id"]
p0 = next(rb["data"]["prog"] for rb in g._rows[0][1] if rb["data"]["prog"])
w.rec.jobs = real_jobs + [
    {"status": "scheduled", "url": f"http://h/live/u/p/{sid}.ts",
     "start": p0["start_timestamp"], "stop": p0["stop_timestamp"]},
    {"status": "recorded", "url": f"http://h/live/u/p/{sid}.ts",
     "start": p0["stop_timestamp"], "stop": p0["stop_timestamp"] + 7200}]
g._drop_row(0)
g._ensure_row(0)
marked = [rb["data"]["prog"] for rb in g._rows[0][1] if rb["text"].startswith("⏺ ")]
assert [m["start_timestamp"] for m in marked] == [p0["start_timestamp"]], marked
assert g._has_rec_job(chans[0], p0) and not g._has_rec_job(chans[1], p0)
w.rec.jobs = real_jobs
g._drop_row(0)
g._ensure_row(0)

# ---- 2. A resize: followed once it settles, the left edge's time kept ------
g.scroll_to_time(now + 3 * 3600)
app.processEvents()
left = g.left_time()
wide_span = g.span_hours()
d.resize(760, 640)
for _ in range(3):
    app.processEvents()
# Debounced: the old scale until the size settles ...
assert g._fit_timer.isActive() and g._fitted_vw != g.view.viewport().width()
settle(g)
# ... then the hours that fit the narrower board, from the same time.
assert g._fitted_vw == g.view.viewport().width()
assert g.span_hours() == EpgGrid.auto_span(area(g)) < wide_span
assert abs(g.left_time() - left) < 60, (g.left_time() - left)
check_layout(g)
d.resize(1500, 640)
settle(g)
assert g.span_hours() == EpgGrid.auto_span(area(g)) > wide_span
assert abs(g.left_time() - left) < 60, (g.left_time() - left)
# "Now" in view stays in view - also from near the right edge, where the
# left edge's time alone would have pushed it out.
g._scroll_to_now()
app.processEvents()
d.resize(760, 640)
settle(g)
lo, hi = shown(g)
assert lo <= now <= hi, (lo, now, hi)
# A few pixels more or less (a scroll bar, the player's column settling):
# the scale stays and nothing is drawn anew - every row on screen was, for
# each pixel, which made the guide on the page lag.
ppm0, span0, vw0 = g._ppm, g.span_hours(), g.view.viewport().width()
item = next(it for _in, it in next(iter(g._row_items.values())))
for dw in (3, -2, -4):
    d.resize(d.width() + dw, 640)
    settle(g)
    assert g._fitted_vw == g.view.viewport().width() != vw0
    if EpgGrid.auto_span(area(g)) == span0:
        assert g._ppm == ppm0 and g.span_hours() == span0
        assert item.scene() is g.scene, "the board was drawn anew"
        check_layout(g)
# More than that: the scale follows (the rest of this test resizes so).
d.resize(760, 640)
settle(g)
# (A board reaching a day back - catch-up channels - so "now" can sit far
# right in a view of six hours.)
real_ts = w._timeshift_days
w._timeshift_days = lambda ch: 1
d2 = EpgGridDialog(w, [dict(c, tv_archive=1) for c in chans[:5]])
g2 = d2.grid
d2.resize(1000, 600)
d2.show()
settle(g2)
for _ in range(8):
    g2.zoom_out()
settle(g2)
lo, hi = shown(g2)
g2.scroll_to_time(now - (hi - lo) * 0.9)
app.processEvents()
lo, hi = shown(g2)
assert lo <= now <= hi and now - lo > (hi - lo) / 2, (lo, now, hi)
for _ in range(8):
    g2.zoom_in()
settle(g2)
lo, hi = shown(g2)
assert g2.span_hours() == SPANS[0] and lo <= now <= hi, (lo, now, hi)
g2.zoom_fit()
w._timeshift_days = real_ts
d2.deleteLater()
app.processEvents()
# Hidden mid-resize: the timer stops with the others, and the scale is
# worked out as it shows again - before its first paint.
d.resize(1200, 640)
app.processEvents()
assert g._fit_timer.isActive()
d.hide()
app.processEvents()
assert not g._fit_timer.isActive()
d.show()
app.processEvents()
assert g._fitted_vw == g.view.viewport().width()
assert g.span_hours() == EpgGrid.auto_span(area(g))
settle(g)

# ---- 3. − / + pick a span and leave Fit; Fit goes back -------------------
writes.clear()
auto = g.span_hours()
i = SPANS.index(auto)
g.zoom_out_btn.click()
settle(g)
assert g.span_hours() == SPANS[i + 1] and not g.fit_btn.isChecked()
assert settings.value("guide_zoom_window") == f"{SPANS[i + 1]:g}"
assert settings.value("guide_zoom_page") is None        # the page's own
check_layout(g)
g.zoom_in_btn.click()
g.zoom_in_btn.click()
settle(g)
assert g.span_hours() == SPANS[i - 1] and not g.fit_btn.isChecked()
check_layout(g)
# Fixed: a resize keeps the span (the scale follows the width).
d.resize(900, 640)
settle(g)
assert g.span_hours() == SPANS[i - 1]
check_layout(g)
for _ in range(10):
    g.zoom_in_btn.click()
settle(g)
assert g.span_hours() == SPANS[0] and not g.zoom_in_btn.isEnabled()
assert g.zoom_out_btn.isEnabled()
check_layout(g)
for _ in range(10):
    g.zoom_out_btn.click()
settle(g)
assert g.span_hours() == SPANS[-1] and not g.zoom_out_btn.isEnabled()
check_layout(g)
d.resize(700, 640)
settle(g)
assert check_layout(g)                       # the hours alone in the header
d.resize(900, 640)
settle(g)
# Narrow cards: the title cut with "…", whole in the tooltip with its time.
cut = [rb for _c, bl in g._rows for rb in bl
       if rb.get("label") is not None and rb["data"]["prog"]
       and rb["data"]["prog"]["title"] == LONG]
assert cut
for rb in cut:
    assert rb["label"].text().endswith("…"), rb["label"].text()
    assert rb["label"].boundingRect().width() <= rb["x2"] - rb["x1"] - 14 + 0.5
p = cut[0]["data"]["prog"]
tip = cut[0]["item"].toolTip()
assert LONG in tip and time.strftime("%H:%M", time.localtime(p["start_timestamp"])) in tip
assert "–" in tip
# A card too narrow for two letters shows no title at all.
assert not any(rb["label"].text().rstrip("…") == "" for _c, bl in g._rows
               for rb in bl if rb.get("label") is not None)
writes.clear()
g.fit_btn.click()
settle(g)
assert g.fit_btn.isChecked() and g.span_hours() == EpgGrid.auto_span(area(g))
assert settings.value("guide_zoom_window") == "auto"
g.fit_btn.click()                            # already Fit: stays lit
settle(g)
assert g.fit_btn.isChecked()
assert writes == [("guide_zoom_window", "auto")], writes   # only when changed

# ---- 4. Ctrl + wheel and Ctrl + = / - / 0 ------------------------------------
g.view.setFocus()
auto = g.span_hours()
i = SPANS.index(auto)
wheel(g, 120)
settle(g)
assert g.span_hours() == SPANS[i - 1] and not g.fit_btn.isChecked()
wheel(g, -60)                                # half a notch: nothing yet
assert g.span_hours() == SPANS[i - 1]
wheel(g, -60)
settle(g)
assert g.span_hours() == SPANS[i]
wheel(g, 60)                                 # turned back: starts afresh
wheel(g, -120)
settle(g)
assert g.span_hours() == SPANS[i + 1]
wheel(g, 120)
settle(g)
assert g.span_hours() == SPANS[i]
# The plain wheel scrolls, as it always did.
vbar = g.view.verticalScrollBar()
v0 = vbar.value()
wheel(g, -120, Qt.KeyboardModifier.NoModifier)
app.processEvents()
assert vbar.value() > v0 and g.span_hours() == SPANS[i]
assert key(g.view, Qt.Key.Key_Minus)         # the guide's, not a shortcut
settle(g)
assert g.span_hours() == SPANS[i + 1]
key(g.view, Qt.Key.Key_Equal)
key(g.view, Qt.Key.Key_Equal)
settle(g)
assert g.span_hours() == SPANS[i - 1]
key(g.view, Qt.Key.Key_Minus, Qt.KeyboardModifier.NoModifier)   # no Ctrl
assert g.span_hours() == SPANS[i - 1]
key(g.view, Qt.Key.Key_0)
settle(g)
assert g.fit_btn.isChecked() and g.span_hours() == auto
# From the filter field too.
key(g.filter, Qt.Key.Key_Minus)
settle(g)
assert g.span_hours() == SPANS[i + 1] and g.filter.text() == ""
key(g.filter, Qt.Key.Key_0)
settle(g)
assert g.fit_btn.isChecked()

# ---- 5. The selection stays on its programme --------------------------------
g.view.verticalScrollBar().setValue(0)
app.processEvents()
g._focus = None
g._nav(1, 0)
sel = g._selected["prog"]
g.zoom_out()
settle(g)
assert g._selected["prog"]["start_timestamp"] == sel["start_timestamp"]
assert g._selected["prog"]["title"] == sel["title"]
row, col = g._focus
assert g._rows[row][1][col]["data"] is g._selected
assert g._sel_outline.rect() == g._rows[row][1][col]["item"].sceneBoundingRect()
g._nav(1, 0)                                 # navigation goes on from it
assert g._selected["prog"]["start_timestamp"] >= sel["stop_timestamp"]
g.zoom_fit()
settle(g)
d.deleteLater()
app.processEvents()

# ---- 6. 10,000 channels: a new scale lays out the rows on screen only ------
many = [{"name": f"Channel {i}", "stream_id": 10_000 + i, "num": i}
        for i in range(10_000)]
d = EpgGridDialog(w, many)
g = d.grid
d.resize(1200, 700)
d.show()
settle(g)
vbar = g.view.verticalScrollBar()
vbar.setValue(vbar.maximum() // 2)
app.processEvents()
time.sleep(0.15)
app.processEvents()
rows, gen = g._rows, g._build_gen
worst = 0.0
for step in [g.zoom_out] * 7 + [g.zoom_in] * 7 + [g.zoom_fit]:
    t0 = time.perf_counter()
    step()
    worst = max(worst, time.perf_counter() - t0)
    top, bottom = g._visible_rows()
    assert g._rows is rows and len(rows) == 10_000 and g._build_gen == gen
    assert all(top - 1 <= r < bottom + 1 for r in g._row_items), (
        top, bottom, sorted(g._row_items))
    assert g._rows[top][1] and g._rows[bottom - 1][1]
assert worst < 1.5, f"a zoom step took {worst:.2f}s with 10,000 channels"
check_layout(g)
# The rows beyond the view follow - a few at a time, nearest first, so the
# window (and the video beside the guide) never waits for two dozen rows.
top, bottom = g._visible_rows()
want = set(range(max(0, top - g.PRELOAD_ROWS),
                 min(len(g._rows), bottom + g.PRELOAD_ROWS)))
deadline = time.time() + 5
while not want <= set(g._row_items) and time.time() < deadline:
    app.processEvents()
    time.sleep(0.01)
assert want <= set(g._row_items), sorted(want - set(g._row_items))
g._preload.stop()
for r in want - set(range(top - 1, bottom + 1)):
    g._drop_row(r)
before = set(g._row_items)
g._preload_rows()
drawn = set(g._row_items) - before
assert 0 < len(drawn) <= g.PRELOAD_CHUNK, drawn
assert max(abs(r - (top + bottom) / 2) for r in drawn) <= min(
    abs(r - (top + bottom) / 2) for r in want - set(g._row_items))
assert g._preload_more.isActive()
d.hide()
app.processEvents()
assert not g._preload_more.isActive()
d.deleteLater()
app.processEvents()

# ---- 7. On the page: its own zoom, the window's apart; the sidebar --------
settings.remove("guide_zoom_window")
settings.remove("guide_zoom_page")
w.resize(1280, 800)
w.show()
for _ in range(30):
    app.processEvents()
w.switch_mode("live")
for _ in range(20):
    app.processEvents()
w._shortcut_epg_guide()
grid = w._guide_grid
settle(grid)
assert grid.isVisible() and not grid._in_window and grid.fit_btn.isChecked()
page_auto = grid.span_hours()
assert page_auto == EpgGrid.auto_span(area(grid))
check_layout(grid)
grid.zoom_out_btn.click()
settle(grid)
page_span = grid.span_hours()
assert page_span > page_auto
assert settings.value("guide_zoom_page") == f"{page_span:g}"
assert settings.value("guide_zoom_window") is None
# Popped out: the window's zoom (Fit, never set), worked out for its room.
grid.popout_btn.click()
win = w._guide_window
settle(grid)
assert grid._in_window and grid.fit_btn.isChecked()
assert grid.span_hours() == EpgGrid.auto_span(area(grid))
check_layout(grid)
grid.zoom_in_btn.click()
settle(grid)
win_span = grid.span_hours()
assert settings.value("guide_zoom_window") == f"{win_span:g}"
assert settings.value("guide_zoom_page") == f"{page_span:g}"
# Back on the page: the page's span again.
win.close()
for _ in range(5):
    app.processEvents()
w._shortcut_epg_guide()
settle(grid)
assert w._guide_grid is grid and not grid._in_window
assert grid.span_hours() == page_span and not grid.fit_btn.isChecked()
check_layout(grid)
# A guide drawn afresh reads it back.
w._on_escape()
w._drop_guide_grid()
w._shortcut_epg_guide()
grid = w._guide_grid
settle(grid)
assert grid.span_hours() == page_span and not grid.fit_btn.isChecked()
grid.fit_btn.click()
settle(grid)
assert settings.value("guide_zoom_page") == "auto"
# The sidebar folded to its rail: more room, the scale follows, from the
# same time at the left edge.
grid.scroll_to_time(now + 2 * 3600)
app.processEvents()
left, vw0 = grid.left_time(), grid.view.viewport().width()
w._on_side_toggle(False)
settle(grid)
assert grid.view.viewport().width() > vw0
assert grid._fitted_vw == grid.view.viewport().width()
assert grid.span_hours() == EpgGrid.auto_span(area(grid))
assert abs(grid.left_time() - left) < 60, grid.left_time() - left
check_layout(grid)
w._on_side_toggle(True)
settle(grid)
assert grid.view.viewport().width() == vw0
assert abs(grid.left_time() - left) < 60, grid.left_time() - left
# A pane with no room for the zoom buttons: they give way, the day jumps
# keep theirs; back with the room.
def right_of(b):
    return b.mapTo(grid, b.rect().topRight()).x()
def left_of(b):
    return b.mapTo(grid, b.rect().topLeft()).x()
assert not grid._zoom_box.isHidden()
assert right_of(grid.day_fwd_btn) < left_of(grid.zoom_out_btn)
assert right_of(grid.zoom_in_btn) < left_of(grid.refresh_btn)
w.resize(1000, 700)
settle(grid)
assert grid._zoom_box.isHidden()
assert right_of(grid.day_fwd_btn) < left_of(grid.refresh_btn)
assert grid.now_btn.width() >= grid.now_btn.sizeHint().width()
w.resize(1280, 800)
settle(grid)
assert not grid._zoom_box.isHidden()
assert abs(grid.left_time() - left) < 60, grid.left_time() - left
# A new theme draws a new guide where the old one was.
w._guide_restyle()
grid = w._guide_grid
settle(grid)
assert abs(grid.left_time() - left) < 60, grid.left_time() - left
print("GUIDE_ZOOM_OK", flush=True)
os._exit(0)
"""


def _child_env(tmp_path) -> dict:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                "XDG_CACHE_HOME"):
        env[var] = str(tmp_path / var.lower())
    return env


def test_guide_zoom(tmp_path):
    # One retry, as test_epg_grid: an offscreen GL context can fail to come
    # up under load and kill the child before any check ran; a real failure
    # fails both times.
    for _attempt in (1, 2):
        out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT,
                             env=_child_env(tmp_path), capture_output=True,
                             text=True, timeout=240)
        if "GUIDE_ZOOM_OK" in out.stdout:
            return
        if "AssertionError" in out.stderr:
            break
    raise AssertionError(out.stdout + out.stderr[-4000:])
