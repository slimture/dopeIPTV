"""No widget is shown as a window of its own on the way through the
places (Mine, TV, Movies, Series, Guide, Search, Local files, Radio,
Recordings) and the donate heart's rhythm.

A parentless widget made visible before it reaches a layout is, for that
moment, a top-level window. On GNOME (Wayland) Qt asks the compositor for
an activation token for it and drops it unused, and the pointer spins as
if a program were starting - in 1.4.2 Home's donate heart did that every
time Home opened (Home is gone since 1.6.2; its places are what opens now).

Subprocess pattern: a real MainWindow with its offscreen player.
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

import faulthandler
faulthandler.enable()
app = QApplication.instance() or QApplication([])
# As the app does (app.py): cycles collected on the GUI thread only. Left
# automatic, a pool thread could free Qt objects and crash the run.
from dopeiptv.core.gcguard import GcGuard
GcGuard(app)
settings = QSettings("dopeiptv-test", "stray-windows")
settings.clear()
# A month's asking on its first day: the sidebar's heart.
import time
settings.setValue("donate_first_seen", time.time() - 60 * 86400)
settings.setValue("donate_period", time.time() - 60)
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

for place in ("mitt", "tv", "films", "series", "guide", "search", "local",
              "radio", "rec", "mitt"):
    step(lambda p=place: w._show_place(p))
    assert w._place == place, (place, w._place)
step(w._donate_heart_refresh)
step(w.hide_donate_heart)
settings.remove("donate_until")
step(w._donate_heart_refresh)
step(lambda: w._place_tab_picked("watchlist"))
step(lambda: w._place_tab_picked("history"))
step(lambda: w._show_place("tv"))
assert not stray, stray
print("NO_STRAY_OK", flush=True)
"""


def test_the_places_show_no_stray_windows():
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


_MARKS = r"""
import os, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QEvent, QObject, QSettings, Qt
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QWidget

from dopeiptv.core.stores import PlaylistStore
from dopeiptv.ui.source_page import Origin, SourcePage

app = QApplication.instance() or QApplication([])
stray = []
mine = []


class Watch(QObject):
    def eventFilter(self, obj, ev):
        if (ev.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow()
                and obj not in mine and obj.windowType() not in (Qt.WindowType.Popup, Qt.WindowType.ToolTip,
                                             Qt.WindowType.Dialog)):
            stray.append(type(obj).__name__)
        return False


watch = Watch()
app.installEventFilter(watch)
host = QWidget()
mine.append(host)
row = QHBoxLayout(host)
host.show()
app.processEvents()
# A mark given its text before its row ("This profile's", "In use"): shown
# with the row, never on its own; an empty one stays hidden in it.
own, empty = Origin(), Origin()
own.set("This profile's", True)
empty.set("", False)
row.addWidget(own)
row.addWidget(empty)
for _ in range(5):
    app.processEvents()
assert own.isVisible() and not empty.isVisible(), (own.isVisible(), empty.isVisible())
own.set("", True)
assert not own.isVisible()
own.set("Shared", False)
assert own.isVisible()
# A source's own page: its tabs and marks, Nerd on and off.
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
store = PlaylistStore(s)
pl = store.add({"name": "Box", "kind": "enigma2", "server": "http://192.0.2.10"})
for nerd_on in (True, False):
    page = SourcePage(pl, store=store, nerd_on=nerd_on, settings=s)
    mine.append(page)
    page.resize(800, 600)
    page.show()
    for _ in range(5):
        app.processEvents()
    assert page.tabs["net"].isVisible() == nerd_on
    assert page.tabs["conn"].isVisible()
    page.close()
assert not stray, stray
print("MARKS_OK", flush=True)
os._exit(0)
"""


def test_marks_and_a_source_pages_tabs_never_show_on_their_own():
    """On GNOME (Wayland) a widget shown as a window of its own for a moment
    asks for an activation token it never uses, and the pointer spins until
    it times out: Settings' "This profile's" and "In use" marks did, and a
    source's page's tabs - some 25 s after Settings opened."""
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run(
        [sys.executable, "-c", _MARKS], capture_output=True, text=True,
        env=env, cwd=_REPO_ROOT, timeout=120)
    assert "MARKS_OK" in proc.stdout, (
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-2000:]!r}")
