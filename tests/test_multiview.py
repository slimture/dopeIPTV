"""Object-graph regression for the multiview grid.

Guards the grid/fill/focus logic: adding a channel lands it in the first free
cell and gives it audio focus; the per-channel helper builds the live URL; and
closing tears the window down. Runs the assertions in a subprocess because
four offscreen QOpenGLWidgets abort at Qt's interpreter-exit teardown (the app
sidesteps this via os._exit) - not a product fault, so we assert on the
child's success marker and ignore its exit status. Headless: the GL contexts
can't build, so this checks the widget graph, not on-screen video.
"""
import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QMainWindow
from dopeiptv.ui.mw_multiview import _MultiviewMixin

app = QApplication.instance() or QApplication([])


class Client:
    def live_url(self, sid, fmt):
        return "http://x/live/%s.%s" % (sid, fmt)

    def timeshift_urls(self, sid, start_dt, dur):
        base = "http://x/ts/%s/%s" % (sid, int(start_dt.timestamp()))
        return [base + ".ts", base + ".m3u8"]


class Host(QMainWindow, _MultiviewMixin):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("dopeiptv-test", "mv")
        self.settings.clear()
        self.client = Client()
        self._multiview_win = None


h = Host()
# Skip the one-time "multiview needs N connections" info dialog (it's modal
# and would block this headless run).
h.settings.setValue("mv_info_seen", "true")
assert h._multiview_win is None

# The sidebar button is a toggle for an EMPTY grid: open, press again ->
# closed. (With streams running it must NOT toggle - checked further down.)
h._show_multiview()
app.processEvents()
assert h._multiview_win is not None and h._multiview_win.isVisible()
h._show_multiview()
app.processEvents()
assert h._multiview_win is None

# First add opens the window and fills cell 0, which takes focus.
h.add_to_multiview("http://x/live/1.ts", "One")
app.processEvents()
w = h._multiview_win
assert w is not None and len(w.cells) == 4
assert w.cells[0].url == "http://x/live/1.ts"
assert w._focused is w.cells[0]

# Second add -> next free cell, focus follows.
h.add_to_multiview("http://x/live/2.ts", "Two")
app.processEvents()
assert w.cells[1].url == "http://x/live/2.ts"
assert w._focused is w.cells[1]

# Focusing a cell unmutes it and mutes the rest (one audio at a time).
w._focus_cell(w.cells[0])
app.processEvents()
assert w.cells[0]._focused and not w.cells[1]._focused
assert not w.cells[0].is_muted() and w.cells[1].is_muted()

# Single-click only focuses now (no accidental mute while grabbing to drag);
# mute is a right-click action driven through set_muted.
w.cells[0].set_muted(True)
app.processEvents()
assert w.cells[0].is_muted()
w.cells[0].set_muted(False)
app.processEvents()
assert not w.cells[0].is_muted()

# Swapping two cells exchanges their streams (right-click "Move / swap with").
w._focus_cell(w.cells[0])
u0, u1 = w.cells[0].url, w.cells[1].url
w._swap_cells(w.cells[0], w.cells[1])
app.processEvents()
assert w.cells[0].url == u1 and w.cells[1].url == u0

# The window is title-bar-less (frameless) by default.
from PyQt6.QtCore import Qt as _Qt
assert bool(w.windowFlags() & _Qt.WindowType.FramelessWindowHint)

# The channel-item helper builds the live URL (stream_format default 'ts').
h._add_channel_to_multiview({"stream_id": 5, "name": "Five"})
app.processEvents()
assert any(c.url and c.url.endswith("5.ts") for c in w.cells)
# A plain channel is not timeshift-capable (no tv_archive).
five = next(c for c in w.cells if c.url and c.url.endswith("5.ts"))
assert not five._ts_capable

# A catch-up channel (tv_archive + depth) gets the archive timeline: seeking
# back re-requests the provider archive via client.timeshift_urls, and the
# error-walk steps through candidate URL schemes before falling back to live.
h._add_channel_to_multiview(
    {"stream_id": 8, "name": "Arch", "tv_archive": 1,
     "tv_archive_duration": 2}, cell=3)
app.processEvents()
ts = w.cells[3]
assert ts._ts_capable and ts._ts_days == 2
assert ts._live_url.endswith("8.ts")
ts._go_timeshift(45)
assert ts._ts_seg_start is not None
assert ts._ts_candidates and ts._ts_candidates[0].startswith("http://x/ts/8/")
i0 = ts._ts_cand_idx
ts._on_error("boom")          # walk to the next candidate scheme
assert ts._ts_cand_idx == i0 + 1
ts._go_live()
assert ts._ts_seg_start is None and ts._ts_candidates == []

# Targeting a specific cell (0..3) sends the stream there and focuses it.
h.add_to_multiview("http://x/live/7.ts", "Seven", cell=2)
app.processEvents()
assert w.cells[2].url == "http://x/live/7.ts"
assert w._focused is w.cells[2]
assert w.cells[2].number == 3   # cell index 2 is position "3"

# Cells are numbered 1..4 in reading order.
assert [c.number for c in w.cells] == [1, 2, 3, 4]

# Overlay reveal + fade, and the cell context menu builders, must not raise.
w._reveal_overlays()
app.processEvents()
w._hide_overlays()
app.processEvents()

# Filling the 4th cell, then a 5th add replaces the focused cell (no crash).
h.add_to_multiview("http://x/live/9.ts", "Nine")
app.processEvents()
h.add_to_multiview("http://x/live/10.ts", "Ten")
app.processEvents()
assert all(c.url is not None for c in w.cells)

# Sending the DOCKED player's film/episode to a cell hands the playhead over,
# so it continues where you were instead of restarting from zero. Live has no
# duration and belongs at the live edge, so it hands over nothing.
h.settings.setValue("mv_stop_docked", "false")
seen = []
_orig_add = w.add_stream
w.add_stream = lambda *a, **k: (seen.append(k), _orig_add(*a, **k))[1]

class _Film:
    current_url = "http://x/movie/1.mkv"
    def playback_duration(self): return 5400.0
    def playback_position(self): return 1234.5

h.player = _Film()
h._playing_item = {"name": "A Film"}
h._send_docked_to_multiview()
app.processEvents()
assert seen and abs(seen[-1]["start"] - 1234.5) < 0.01, seen

class _Live:
    current_url = "http://x/live/99.ts"
    def playback_duration(self): return 0.0
    def playback_position(self): return 812.0

h.player = _Live()
h._playing_item = {"name": "A Channel"}
h._send_docked_to_multiview()
app.processEvents()
assert seen[-1]["start"] == 0.0, seen
w.add_stream = _orig_add

# A live cell never reports a resume position (its mpv has no duration).
assert w.cells[0].resume_pos() == 0.0

# Frameless: the cells' own edges resize the grid, so it can be sized without
# turning the title bar on.
from PyQt6.QtCore import QPoint, QRect
E = _Qt.Edge
w.setGeometry(QRect(120, 120, 900, 600))
app.processEvents()
g = w.frameGeometry()
assert not w.resize_edges_at(g.center())            # middle of a cell: no grab
assert w.resize_edges_at(
    QPoint(g.left() + 1, g.top() + 1)) == (E.LeftEdge | E.TopEdge)
assert w.resize_edges_at(
    QPoint(g.right() - 1, g.bottom() - 1)) == (E.RightEdge | E.BottomEdge)
assert w.resize_edges_at(QPoint(g.center().x(), g.bottom() - 1)) == E.BottomEdge

# A press on an edge resizes instead of moving audio focus to that cell.
w._focus_cell(w.cells[0])
cell = w.cells[len(w.cells) - 1]        # bottom-right cell
class _Pos:                              # the handlers call .toPoint() on both
    def __init__(self, p): self._p = p
    def toPoint(self): return self._p

class _Ev:
    def __init__(self, gp): self._g = gp
    def button(self): return _Qt.MouseButton.LeftButton
    def buttons(self): return _Qt.MouseButton.LeftButton
    def globalPosition(self): return _Pos(self._g)
    def position(self): return _Pos(QPoint(0, 0))
cell._on_press(_Ev(QPoint(g.right() - 1, g.bottom() - 1)))
assert w._focused is w.cells[0], "an edge grab must not steal audio focus"
assert w.resizing is not None, "expected the manual resize fallback"

# Dragging that corner grows the grid; the opposite corner stays put. The
# press landed 1 px inside the corner, so the drag target is measured from
# there - the delta is what moves the edge.
geo = QRect(w.geometry())
w.drag_resize(QPoint(g.right() - 1 + 140, g.bottom() - 1 + 90))
app.processEvents()
ng = w.geometry()
assert ng.topLeft() == geo.topLeft(), (ng, geo)
assert (ng.width(), ng.height()) == (geo.width() + 140, geo.height() + 90), ng
w.drag_resize(QPoint(g.left(), g.top()))            # cannot go below the floor
app.processEvents()
assert w.width() >= w.MIN_W and w.height() >= w.MIN_H
cell._on_release(_Ev(QPoint(0, 0)))
assert w.resizing is None

# With streams running the button only re-raises - never closes the grid.
h._show_multiview()
app.processEvents()
assert h._multiview_win is w

h._close_multiview()
app.processEvents()
assert h._multiview_win is None

print("MULTIVIEW_OK")
"""


def test_multiview_grid_and_focus():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD], capture_output=True, text=True,
        env=env, cwd=_REPO_ROOT, timeout=180)
    assert "MULTIVIEW_OK" in proc.stdout, (
        f"multiview checks failed\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-2000:]!r}")
