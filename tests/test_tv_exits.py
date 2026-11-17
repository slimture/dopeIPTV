"""TV mode is one state with one way out (ui/mw_tv.py exit_tv_mode), and
leaving it puts the window back as it was.

The bugs these hold down, seen on Linux:
- Back never left TV mode: on the page it went back to the picture, over the
  picture back to the page, round and round. Now Back goes on to Home, asks
  there, and leaves on the next press.
- The picture's own way out of full screen (its button, a double click)
  showed the window's lists with TV mode still on behind them, and the next
  channel changed from the list brought TV mode's picture back. Now that
  ends TV mode.
- Its page was drawn at the window's old size while the window manager made
  the window full screen, and visibly folded into place. Now a curtain
  stands in for it until the window is full screen.
- Leaving left the docked player beside the lists out of place: the window
  came back by a title bar off (tests/test_fullscreen_restore.py), and after
  a resize in TV mode not at all. Now the window's own state and geometry,
  the panes, the sidebar, the docked player's height and a pop-out are put
  back, whatever way TV mode is left.
"""
from __future__ import annotations

import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP = None


def _app():
    from PyQt6.QtWidgets import QApplication
    global _APP
    _APP = QApplication.instance() or QApplication([])   # kept alive
    return _APP


# -- Back leads out ------------------------------------------------------------------------

class _BackWin:
    """What TvPage.back asks of the window."""

    def __init__(self, playing=True):
        self.playing = playing
        self.done: list[str] = []

    def _tv_has_picture(self):
        return self.playing

    def _tv_now_title(self):
        return "Chan 1"

    def _tv_picture(self):
        self.done.append("picture")

    def _tv_back_at_home(self):
        self.done.append("ask to leave")


def _page(win):
    from dopeiptv.ui import tv_mode
    from dopeiptv.ui.tv_widgets import TvTile
    _app()
    page = tv_mode.TvPage(win)

    class One(tv_mode.TvScreen):
        key = "home"

        def __init__(self, page):
            super().__init__(page)
            self.tile = TvTile("A", parent=self)

        def focusables(self):
            return [self.tile]

    scr = One(page)
    page.screens["home"] = scr
    page.stack.addWidget(scr)
    page.show_screen("home", focus=False)
    return page, scr


def test_back_on_home_asks_to_leave_never_round_to_the_picture():
    win = _BackWin()
    page, scr = _page(win)
    page.set_focus(scr.tile)
    page.back()
    assert win.done == ["ask to leave"]             # not the picture: the loop is gone
    assert page.focus_widget() is page.tab("home")


def test_back_after_a_look_from_the_picture_returns_to_it():
    win = _BackWin()
    page, scr = _page(win)
    page.picture_back = True                        # the guide's key over the picture
    page.set_focus(scr.tile)
    page.back()
    assert win.done == ["picture"]
    win.done.clear()
    page.picture_back = False
    page.refresh_now()
    page.set_focus(page.now)                        # Now playing: its row is the picture's
    page.back()
    assert win.done == ["picture"]


def test_back_twice_on_home_leaves_and_only_then():
    from dopeiptv.i18n import tr
    from dopeiptv.ui import mw_tv

    class Win(mw_tv._TvMixin):
        def __init__(self):
            self._tv_on = True
            self.left = 0
            self._tv_page, _scr = _page(_BackWin())

        def exit_tv_mode(self):
            self.left += 1

    w = Win()
    w._tv_back_at_home()
    assert w.left == 0 and w._tv_page.flash_lbl.text() == tr("tv_leave_again")
    w._tv_back_at_home()
    assert w.left == 1
    # Another key in between takes the question back.
    w = Win()
    w._tv_back_at_home()
    w._tv_leave_disarm()
    w._tv_back_at_home()
    assert w.left == 0
    # Too late: asked again.
    w = Win()
    w._tv_back_at_home()
    w._tv_leave_until = 1.0                        # long gone
    w._tv_back_at_home()
    assert w.left == 0


# -- the whole window --------------------------------------------------------------------

_CHILD = r"""
import faulthandler, os, sys, tempfile, time
faulthandler.dump_traceback_later(400, exit=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QEvent, QObject, QPoint, QSettings, Qt, QTimer
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])
errors = []


def hook(t, v, tb):
    errors.append(f"{t.__name__}: {v}")
    sys.__excepthook__(t, v, tb)


sys.excepthook = hook
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
s.setValue("tv_inputs", "false")
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
import dopeiptv.ui.main_window as mw

mw.launch_player = lambda *a, **k: None


class Client(DemoClient):
    def live_categories(self):
        return [{"category_id": "1", "category_name": "News"}]

    def live_streams(self, category_id=None):
        return [{"stream_id": i + 1, "num": i + 1, "name": f"Chan {i + 1}", "stream_icon": "",
                 "category_id": "1", "epg_channel_id": ""} for i in range(6)]

    def live_url(self, sid, fmt="ts"):
        return f"http://tv.invalid/{sid}.ts"


w = mw.MainWindow(Client(), s, PlaylistStore(s))
if w.player is None:
    print("TV_SKIP: no embedded player here", flush=True)
    os._exit(0)
played = []


def fake_play(url, *a, **k):
    played.append(url)
    w.player.current_url = url
    return True


w.player.play = fake_play
w.wake.acquire = w.wake.release = lambda *a, **k: None
CH = Client().live_streams(None)
w.resize(1100, 700)
w.show()


def spin(secs=0.1):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def until(cond, secs=4.0):
    end = time.monotonic() + secs
    while not cond() and time.monotonic() < end:
        spin(0.02)
    return cond()


def key(k, mods=Qt.KeyboardModifier.NoModifier, text=""):
    target = QApplication.focusWidget() or w
    ov = QKeyEvent(QEvent.Type.ShortcutOverride, k, mods, text)
    QApplication.sendEvent(target, ov)
    if not ov.isAccepted():
        QApplication.sendEvent(target, QKeyEvent(QEvent.Type.KeyPress, k, mods, text))
    else:
        QApplication.sendEvent(target, QKeyEvent(QEvent.Type.KeyPress, k, mods, text))
    spin(0.03)


def snap():
    p = w.player
    pos = p.mapTo(w, QPoint(0, 0))
    ov = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(w, ov)
    return {"tv": w._tv_on, "fs": w.isFullScreen(), "max": w.isMaximized(),
            "player_fs": w._player_fs, "geo": w.geometry().getRect(),
            "split": w._root.sizes(), "player": (pos.x(), pos.y(), p.width(), p.height()),
            "video_h": getattr(p, "_docked_video_height", None),
            "handle": w._player_resize._preferred, "side": w._side.isVisible(),
            "collapsed": bool(getattr(w, "_sidebar_collapsed", False)),
            "menubar": w.menuBar().isVisible(), "page": w._center_stack.currentIndex(),
            "keys taken": ov.isAccepted(), "checked": w._menu_actions["tv_mode"].isChecked(),
            "popped out": w._popout_win is not None}


def same(tag, base, extra=True):
    now = snap()
    diff = {k: (base[k], now[k]) for k in base if base[k] != now[k]}
    assert not diff and extra, (tag, diff)


def settle():
    until(lambda: w._tv_restoring is None and w._settle_job is None, 3)
    spin(0.15)


spin(0.6)

# -- the page is drawn complete from its first frame ----------------------------------
# A window manager makes the window full screen a moment after it is asked
# (X11, Wayland): showFullScreen() here does it 150 ms later, as there.
real_fs = w.showFullScreen
w.showFullScreen = lambda: QTimer.singleShot(150, real_fs)
paints = []


class Paints(QObject):
    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Paint and obj is w._tv_page:
            m = w._tv_page._outer.contentsMargins()
            paints.append((obj.size().width(), obj.size().height(), m.left(), m.top()))
        return False


base = snap()
w.enter_tv_mode()
page = w._tv_page
spy = Paints()
page.installEventFilter(spy)
assert w._tv_on and w._center_stack.currentWidget() is not page   # the curtain, not the page
assert until(lambda: w._center_stack.currentWidget() is page and paints)
spin(0.3)
full = w.size()
want_margins = (int(full.width() * page.SAFE), int(full.height() * page.SAFE))
assert paints and all((p[0], p[1]) == (full.width(), full.height()) for p in paints), paints
assert all(p[2:] == want_margins for p in paints), (paints, want_margins)
assert QApplication.focusWidget() is not None and page.isAncestorOf(QApplication.focusWidget())
w.showFullScreen = real_fs
assert w._menu_actions["tv_mode"].isChecked()                     # said in the window's terms
w._menu_actions["tv_mode"].trigger()                              # the View menu: off
settle()
same("View menu", base)

# -- Ctrl+Shift+T on the page: no error, and it leaves --------------------------------
w.enter_tv_mode()
spin(0.2)
page.set_focus(page.tab("home"))
ctrl_shift = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
target = QApplication.focusWidget()
QApplication.sendEvent(target, QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_T, ctrl_shift))
assert not errors, errors                                         # it raised TypeError
w._shortcut_run("tv_mode")
settle()
same("Ctrl+Shift+T", base)

# -- every way out, then the window's own ways to change channel ------------------------
w.play_live_channel(CH[0])
spin(0.4)
base = snap()
assert base["player"][3] > 0 and not base["tv"]


def watch_in_tv():
    w.enter_tv_mode()
    spin(0.2)
    assert w._tv_watching                                          # what plays, full screen
    key(Qt.Key.Key_Escape)                                         # its info bar
    key(Qt.Key.Key_Escape)                                         # the page
    assert until(lambda: not w._tv_watching and w._center_stack.currentWidget() is w._tv_page)
    w._tv_page.show_screen("channels")
    until(lambda: w._tv_page.screen("channels").chans.count() == len(CH))
    ch = w._tv_page.screen("channels")
    w._tv_page.set_focus(ch.chans)
    ch.chans.set_row(1)
    key(Qt.Key.Key_Return)
    assert until(lambda: w._tv_watching and w._player_fs)
    key(Qt.Key.Key_PageUp)                                         # zapped in TV mode
    assert until(lambda: played[-1].endswith("/3.ts")), played


def back_out():
    for n in range(10):
        if not w._tv_on:
            return n
        key(Qt.Key.Key_Escape)
    raise AssertionError("Back never leaves TV mode")


def pad_back_out():
    for n in range(10):
        if not w._tv_on:
            return n
        w._tv_input("back")
        spin(0.03)
    raise AssertionError("the pad's Back never leaves TV mode")


def options_leave():
    key(Qt.Key.Key_Blue)
    w._tv_page.set_focus(w._tv_page.screen("settings").tiles["exit"])
    key(Qt.Key.Key_Return)


def window_manager_lets_go():
    w.showNormal()                       # as a window manager's own key does


def resized_then_options():
    w.resize(900, 600)                   # the window resized while in TV mode
    spin(0.3)
    options_leave()


WAYS = (("Back", back_out), ("a pad's Back", pad_back_out),
        ("Ctrl+Shift+T", lambda: w._shortcut_run("tv_mode")),
        ("the View menu", lambda: w._menu_actions["tv_mode"].trigger()),
        ("Options > Leave", options_leave),
        ("tv_command off", lambda: w.tv_command("tv", "off")),
        ("tv_command toggle", lambda: w.tv_command("tv", "toggle")),
        ("the phone remote", lambda: w._phone_do("tv", {"mode": "off"})),
        ("the remote API", lambda: w._remote_do("tv", {"mode": "toggle"})),
        ("the picture's Exit fullscreen", lambda: w.player.fs_exit_btn.click()),
        ("a double click on the picture", lambda: w.player.double_clicked.emit()),
        ("the window manager", window_manager_lets_go),
        ("a resize, then Options > Leave", resized_then_options))
for name, way in WAYS:
    watch_in_tv()
    way()
    assert until(lambda: not w._tv_on, 2), name
    settle()
    same(name, base, w.player.current_url is not None)           # still playing, docked
    # The window's own ways to change channel: TV mode stays off.
    w.listw.setCurrentIndex(w.list_model.index(2))
    w.play()
    w.tune_from_guide(CH[3])
    w._remote_media("next")
    w._phone_do("_play", {"row": dict(CH[1])})
    w._phone_do("next", {})
    w.tv_command("tv_key", "ch_up")
    spin(0.3)
    same(name + ", then channels changed", base)
    w.play_live_channel(CH[0])
    spin(0.2)

# -- from Mine, maximized, from the video's full screen, popped out ------------------------
w._show_place("mitt")
spin(0.2)
w.enter_tv_mode()
spin(0.2)
w._tv_browse()                              # the page, the stream behind it
assert w._center_stack.currentWidget() is w._tv_page
w.switch_mode("live")                       # TV mode holds the window meanwhile...
assert w._center_stack.currentWidget() is w._tv_page
w._show_place("films")
assert w._center_stack.currentWidget() is w._tv_page
w.exit_tv_mode()                            # ...and the window is back on the place last asked
settle()
same("from Mine, then Movies asked for", base)
assert w._place == "films" and w.mode == "vod" and w.nav_btns["vod"].isChecked()
w._show_place("tv")
spin(0.3)
same("TV again", base)

w.showMaximized()
spin(0.3)
maxed = snap()
watch_in_tv()
w.exit_tv_mode()
settle()
same("maximized", maxed)
w.showNormal()
spin(0.3)
w.setGeometry(*base["geo"])
spin(0.3)
base = snap()                    # the panes as un-maximizing left them

w._fs_toggled_at = 0
w._toggle_player_fullscreen()
spin(0.3)
assert w._player_fs
w.enter_tv_mode()
spin(0.2)
assert w._tv_watching
w.exit_tv_mode()
settle()
same("from the video's full screen", base)

w._toggle_popout()
spin(0.3)
if w._popout_win is not None:
    popped = snap()
    w.enter_tv_mode()
    spin(0.2)
    assert w._popout_win is None                      # docked in TV mode
    w.exit_tv_mode()
    settle()
    same("popped out", popped)
    w._exit_popout()
    spin(0.3)

assert not errors, errors
print("TV_EXITS_OK", flush=True)
os._exit(0)
"""


def test_every_way_out_restores_the_window_and_tv_mode_stays_off():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=_REPO_ROOT)
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=420)
    text = out.stdout + out.stderr
    if "TV_SKIP" in text:
        return
    assert "TV_EXITS_OK" in text, text[-4000:]
