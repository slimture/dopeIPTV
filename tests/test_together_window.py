"""Watch together in the real window: the app menu's and the player's entry,
the strip under the video while in a room, private viewing taking the
window out of it, and the start of what the room plays without the resume
question.

Runs in a subprocess: the window embeds a QOpenGLWidget whose offscreen
teardown can abort at interpreter exit, so we assert on the child's success
marker and ignore its exit status (same pattern as the other window tests).
"""
import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
from dopeiptv.core import privacy
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow
from tests.test_together import FakeRelay, Hub

app = QApplication.instance() or QApplication([])
settings = QSettings(os.path.join(tmp, "settings.ini"), QSettings.Format.IniFormat)
w = MainWindow(DemoClient(), settings)
w.show()
app.processEvents()

label = tr("together_title") + "…"
assert any(a.text() == label for a in w.findChildren(type(w.menuBar().actions()[0]))), "menu entry"
if w.player is not None:
    assert any(lbl() == label for lbl, _act in w.player.extra_options), w.player.extra_options

hub = Hub()
w._together_relay_for = lambda room, me: FakeRelay(hub, me)


def spin(cond, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


assert w.together_start("Anna") is None
assert spin(lambda: w._together_link is not None)
assert spin(lambda: hub.msgs)                     # the hello went out
strip = w._together_strip
assert strip is not None and strip.isVisible(), strip
assert strip.code.text() == w.together_view()["code"]
host = w._info_scroll.parentWidget().layout()
assert host.indexOf(strip) == host.indexOf(w._info_scroll) - 1   # right above the details

w.open_together()
dlg = w._together_dialog
assert dlg is not None and dlg.isVisible() and dlg.pages.currentIndex() == 1
dlg.close()
app.processEvents()

# What the room plays starts where the room is - no resume question.
w._together_start_at = 42.0
assert w._together_take_start() == 42.0 and w._together_take_start() is None

# Private viewing takes the window out of the room.
w.set_private_viewing(True)
app.processEvents()
assert not w.together_view()["in"] and not strip.isVisible()
assert w.together_start("Anna") == tr("together_private")
w.set_private_viewing(False)
privacy.set_private(False)
assert spin(lambda: hub.left)
w._together_close()
print("TOGETHER_WINDOW_OK")
"""


def test_watch_together_in_the_window():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for _attempt in (1, 2):
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD], capture_output=True, text=True,
            env=env, cwd=_REPO_ROOT, timeout=180)
        if "TOGETHER_WINDOW_OK" in proc.stdout:
            return
    assert "TOGETHER_WINDOW_OK" in proc.stdout, (
        f"watch together window checks failed\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-3000:]!r}")
