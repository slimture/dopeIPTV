"""A menu over the video takes its own clicks (1.6.1).

The player's right-click menu - and the pop-out's, a multiview cell's and
the guide's - opens on a right-button press. Qt follows every right press
with a context-menu event once the press is handled, since Qt 6.9 to the
widget the pointer was over last. While the menu ran its own loop inside the
press (exec()), that event came only after a click on one of its items, to
the widget under that item: where the menu hung over a list, its row's own
menu opened, as if the click had gone through the menu. Wayland shows it:
when the menu goes, the compositor puts the pointer on the window under it.

The menus are now shown (popup()) and the press ends at once."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QPoint, QPointF, QSettings, Qt, QTimer
from PyQt6.QtGui import QEnterEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QListWidget, QMenu

_tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _tmp)

from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw_mod
from dopeiptv.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
settings = QSettings(os.path.join(_tmp, "s.ini"), QSettings.Format.IniFormat)
settings.setValue("home_enabled", "false")
mw_mod.launch_player = lambda *a, **k: None
w = MainWindow(DemoClient(), settings)
w.cover.resolver = None
w.resize(1200, 800)
w.show()
QTest.qWaitForWindowExposed(w)

def settle(cond, secs=10, what=""):
    end = time.time() + secs
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert cond(), "timed out: " + what

L, R, NO = Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier
win = w.windowHandle()
p = w.player
p.current_url = "http://127.0.0.1:9/live/1.ts"        # something plays: the menu is offered
p.show()
p.video.show()
settle(lambda: p.video.isVisible() and p.video.height() > 100, what="the docked video")

# A list under the menu with a row menu of its own (the guide's entries, the
# now card, a list's rows have one).
probe = QListWidget(w)
probe.addItems([f"Episode {i}" for i in range(1, 30)])
probe.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
row_menus = []
probe.customContextMenuRequested.connect(lambda pos: row_menus.append(pos))

seen = {}

def in_the_menu():
    m = app.activePopupWidget()
    seen["menu"] = m
    if not isinstance(m, QMenu):
        return
    m.triggered.connect(lambda a: seen.setdefault("triggered", a.text()))
    item = next(a for a in m.actions() if a.text() == tr("opt_stats_for_nerds"))
    at = m.mapToGlobal(m.actionGeometry(item).center())
    seen["under_video"] = p.video.rect().contains(p.video.mapFromGlobal(at))
    # The list where the item hangs, under the menu.
    tl = w.mapFromGlobal(at)
    probe.setGeometry(tl.x() - 60, tl.y() - 40, 240, 200)
    probe.show()
    probe.raise_()
    # The right button comes up (still over the video), then a click on the item.
    QTest.mouseRelease(win, R, NO, press_at)
    mw = m.windowHandle()
    local = m.mapFromGlobal(at)
    QTest.mouseMove(mw, local)
    QTest.mousePress(mw, L, NO, local)
    QTest.mouseRelease(mw, L, NO, local)
    # The menu is gone: the pointer is over the window there - what a
    # compositor says (an enter) once the popup's surface is unmapped.
    QTest.qWait(50)
    QApplication.sendEvent(win, QEnterEvent(QPointF(tl), QPointF(tl), QPointF(at)))
    seen["clicked"] = True

press_at = w.mapFromGlobal(p.video.mapToGlobal(QPoint(30, int(p.video.height() * 0.6))))
QTest.mouseMove(win, press_at)
app.processEvents()
QTimer.singleShot(150, in_the_menu)
QTest.mousePress(win, R, NO, press_at)
settle(lambda: seen.get("clicked") or ("menu" in seen and not isinstance(seen["menu"], QMenu)),
       what="the menu clicked")
QTest.qWait(150)
assert isinstance(seen.get("menu"), QMenu), seen
assert seen["under_video"] is False, "the item must hang outside the video"
assert seen.get("triggered") == tr("opt_stats_for_nerds"), seen
assert row_menus == [], f"the click went through the menu to the list: {row_menus}"
assert app.activePopupWidget() is None
assert p._stats_overlay.isVisible()                    # the item did its work
p._show_stats()
print("DOCKED_OK", flush=True)

# The guide's menu (a right press on a row) is shown, not run inside the
# press: the press is over when the menu is up.
from dopeiptv.ui.epg_grid import EpgGrid
w._open_epg_guide(True)
settle(lambda: any(g.isVisible() and g._rows for g in w.findChildren(EpgGrid)), what="the guide")
grid = next(g for g in w.findChildren(EpgGrid) if g.isVisible())
vp = grid.view.viewport()
pos = QPoint(vp.width() - 20, 40)
returned = []
QTimer.singleShot(1500, lambda: (returned.append("menu still running"),
                                 app.activePopupWidget() and app.activePopupWidget().close()))
QTest.mousePress(vp, R, NO, pos)
returned.append("press over")
up = app.activePopupWidget()
assert returned[0] == "press over", returned
assert isinstance(up, QMenu) and up.isVisible(), up
up.close()
QTest.mouseRelease(vp, R, NO, pos)
print("GUIDE_OK", flush=True)
os._exit(0)
"""


def test_a_click_on_the_video_menu_stays_in_the_menu(tmp_path):
    pytest.importorskip("PyQt6")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=str(tmp_path),
               XDG_DATA_HOME=str(tmp_path / "data"), XDG_CACHE_HOME=str(tmp_path / "cache"))
    proc = subprocess.run([sys.executable, "-c", _CHILD], capture_output=True, text=True,
                          env=env, cwd=_REPO_ROOT, timeout=240)
    out = proc.stdout
    assert "DOCKED_OK" in out and "GUIDE_OK" in out, (
        f"stdout={out[-3000:]!r}\nstderr={proc.stderr[-4000:]!r}")


def test_the_menu_is_shown_and_the_call_returns_at_once():
    """Every menu over the video goes through this one helper: it shows the
    menu and returns while it is up - never runs it (exec) in the caller."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QPoint, QTimer
    from PyQt6.QtWidgets import QApplication, QMenu, QWidget

    from dopeiptv.ui.widgets import popup_menu_over_video

    app = QApplication.instance() or QApplication([])
    host = QWidget()
    host.resize(300, 200)
    host.show()
    menu = QMenu(host)
    menu.addAction("one")
    order = []

    def later():
        order.append("menu still running" if menu.isVisible() else "menu gone")
        menu.close()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(later)
    timer.start(300)
    try:
        popup_menu_over_video(menu, host.mapToGlobal(QPoint(10, 10)))
        order.append("returned")
        assert order == ["returned"] and menu.isVisible()
        assert app.activePopupWidget() is menu
    finally:
        timer.stop()
        menu.close()
        host.close()
