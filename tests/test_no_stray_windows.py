"""No widget is shown as a window of its own on the way into Home.

A parentless widget made visible before it reaches a layout is, for that
moment, a top-level window. On GNOME (Wayland) Qt asks the compositor for
an activation token for it and drops it unused, and the pointer spins as
if a program were starting - in 1.4.2 Home's donate heart did that every
time Home opened.

Subprocess pattern (see test_home): a real MainWindow with its offscreen
player.
"""
import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QEvent, QObject, QSettings, Qt
from PyQt6.QtWidgets import QApplication, QWidget

from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
settings = QSettings("dopeiptv-test", "stray-windows")
settings.clear()
settings.setValue("home_enabled", "true")
settings.setValue("donate_watch_min", 5000)     # Home's donate card is due
stray = []
main = []

class Watch(QObject):
    def eventFilter(self, obj, ev):
        if (ev.type() == QEvent.Type.Show and isinstance(obj, QWidget)
                and obj.isWindow() and obj not in main
                and obj.windowType() not in (Qt.WindowType.Popup,
                                             Qt.WindowType.ToolTip,
                                             Qt.WindowType.Dialog)):
            stray.append(type(obj).__name__)
        return False

watch = Watch()
app.installEventFilter(watch)
w = MainWindow(DemoClient(), settings)
main.append(w)
w.resize(1200, 760)
w.show()
app.processEvents()

def step(fn):
    fn()
    for _ in range(20):
        app.processEvents()

step(w._show_home_page)
step(w._leave_home)
step(w._show_home_page)
step(w._apply_home_settings)
step(w.hide_donate_heart)
step(w.show_donate_heart)
step(w._leave_home)
step(w._show_home_page)
# Home switched off in Settings, while it shows and again after.
settings.setValue("home_enabled", "false")
step(w._apply_home_settings)
step(w._apply_home_settings)
assert not w._home_showing()
settings.setValue("home_enabled", "true")
step(w._apply_home_settings)
step(w._show_home_page)
assert not stray, stray
print("NO_STRAY_OK")
"""


def test_home_shows_no_stray_windows():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD], capture_output=True, text=True,
        env=env, cwd=_REPO_ROOT, timeout=180)
    assert "NO_STRAY_OK" in proc.stdout, (
        f"a widget was shown as a window of its own\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-2000:]!r}")
