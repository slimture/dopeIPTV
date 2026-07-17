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

# Clicking cell 0 focuses it and unfocuses the rest.
w._focus_cell(w.cells[0])
app.processEvents()
assert w.cells[0]._focused and not w.cells[1]._focused

# The channel-item helper builds the live URL (stream_format default 'ts').
h._add_channel_to_multiview({"stream_id": 5, "name": "Five"})
app.processEvents()
assert any(c.url and c.url.endswith("5.ts") for c in w.cells)

# Targeting a specific cell (0..3) sends the stream there and focuses it.
h.add_to_multiview("http://x/live/7.ts", "Seven", cell=2)
app.processEvents()
assert w.cells[2].url == "http://x/live/7.ts"
assert w._focused is w.cells[2]
assert w.cells[2].number == 3   # cell index 2 is position "3"

# Cells are numbered 1..4 in reading order.
assert [c.number for c in w.cells] == [1, 2, 3, 4]

# Hover reveal + fade must not raise.
w._reveal_numbers()
app.processEvents()
w._hide_numbers()
app.processEvents()

# Filling the 4th cell, then a 5th add replaces the focused cell (no crash).
h.add_to_multiview("http://x/live/9.ts", "Nine")
app.processEvents()
h.add_to_multiview("http://x/live/10.ts", "Ten")
app.processEvents()
assert all(c.url is not None for c in w.cells)

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
