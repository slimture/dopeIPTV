"""The Guide as a page in the middle pane: the Guide entry shows the guide
where the lists are, lit like the other places, and never touches what
plays; Esc, another entry or a category leave it; Pop out puts the same
guide in a window (the entry brings that forward then); its timers run only
while it is on screen; a guide kept from before is shown again as it was.

Runs in a subprocess like the other MainWindow tests (an offscreen
QOpenGLWidget per window, which Qt's exit teardown does not like in
numbers); the child prints a marker when every check passed.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytest.importorskip("PyQt6.QtWidgets")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os, sys, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QEvent, QSettings, Qt
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from dopeiptv import i18n
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw
from dopeiptv.ui import theme
from dopeiptv.ui.epg_grid import EpgGridDialog

i18n.set_language("sv")


class Client(DemoClient):
    pass


def pump(n=10):
    for _ in range(n):
        app.processEvents()
        w.pool.waitForDone(5)


s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
s.setValue("language", "sv")
theme.apply_theme(s)
app.setStyleSheet(theme.build_style())
w = mw.MainWindow(Client(), s, PlaylistStore(s))
now = time.time()
w.xmltv.is_loaded = lambda: True
w.xmltv.programmes_in = lambda ch, a, b: [
    {"title": f"Nu på {ch.get('name')}", "description": "",
     "start_timestamp": now - 600, "stop_timestamp": now + 1200}]
w.resize(1280, 800)
w.show()
pump(30)
w.switch_mode("live")
pump(20)
assert w.all_items, "the demo line-up did not load"

# Something plays - and nothing the guide does may touch it.
URL = "http://example.invalid/live/3.ts"
calls = []
w.player.stop = lambda *a, **k: calls.append("stop")
w.player.play = lambda *a, **k: calls.append("play")
w.player.current_url = URL
playing = w.all_items[2]
w._playing_group = "live"
w._playing_key = w._item_key(playing)
w._playing_item = playing
w.player.show()
pump()


def untouched():
    assert w.player.current_url == URL, w.player.current_url
    assert calls == [], calls
    assert w.player.isVisible()
    assert w._playing_key == w._item_key(playing)


guide = w.nav_btns["guide"]
live = w.nav_btns["live"]
loads = []
real_load = w._load_categories
def counted_load(*a, **k):
    loads.append(1)
    return real_load(*a, **k)
w._load_categories = counted_load

# -- the Guide entry shows the page, lit, the player as it was -----------------
guide.click()
pump()
page = w._guide_page
grid = w._guide_grid
assert w._mid_stack.currentWidget() is page and page.isVisible()
assert not w._list_page.isVisible()
assert grid is not None and grid.isVisible() and grid.parentWidget() is page
assert guide.isChecked() and not live.isChecked()
assert w.mode == "live"
assert len(grid.channels) == len(w.all_items)           # the TV list's scope
assert grid._playing_row == 2 and grid.playing_btn.isVisible()
assert grid.popout_btn.isVisible() and not grid.close_btn.isVisible()
assert grid._refresh.isActive()
# The page sits where the list does: the sidebar and the player stay.
assert w._side.isVisible() and w._det.isVisible()
assert page.width() == w._mid.width()
untouched()

# -- Esc goes back where you were; nothing ticks off screen -------------------
w._on_escape()
pump()
assert w._mid_stack.currentWidget() is w._list_page and w._list_page.isVisible()
assert not page.isVisible()
assert live.isChecked() and not guide.isChecked()
assert not any(t.isActive() for t in (grid._refresh, grid._epg_poll,
                                      grid._guide_poll, grid._preload,
                                      grid._preload_more))
untouched()

# -- back again: the same guide, nothing drawn anew --------------------------
gen = grid._build_gen
w._shortcut_epg_guide()
pump()
assert w._guide_grid is grid and grid._build_gen == gen
assert grid._refresh.isActive() and guide.isChecked()
# The page's own keys: P is the guide's (jump to playing), not the window's
# shortcut that pops the player out.
grid.view.setFocus()
ev = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_P, Qt.KeyboardModifier.NoModifier, "p")
app.sendEvent(grid.view, ev)
assert ev.isAccepted()
# The TV entry - the list under the page - takes the page away, no reload.
n = len(loads)
live.click()
pump()
assert not w._guide_page_showing() and live.isChecked() and not guide.isChecked()
assert len(loads) == n and w.mode == "live"
untouched()

# -- another place leaves it ---------------------------------------------------
guide.click()
pump()
assert w._guide_page_showing()
w.nav_btns["rec"].click()
pump()
assert w.mode == "rec" and not w._guide_page_showing()
assert w.nav_btns["rec"].isChecked() and not guide.isChecked()
assert not grid._refresh.isActive()
untouched()

# -- from Recordings: the whole line-up, fetched; a category click leaves it ----
guide.click()
pump(20)
assert w._guide_page_showing() and guide.isChecked() and w.mode == "rec"
grid = w._guide_grid
assert grid is not None and grid.scope_key[0] == "all"
assert len(grid.channels) == len(Client().live_streams())
item = w.cat_list.item(0)
w.cat_list.itemClicked.emit(item)
pump()
assert not w._guide_page_showing() and w.nav_btns["rec"].isChecked()
untouched()

# -- tuning from the page: it stays, the list follows underneath --------------
guide.click()
pump(20)
grid = w._guide_grid
tuned = []
def fake_play(it):
    tuned.append(it.get("name"))
    w._playing_key = w._item_key(it)
    w._playing_item = it
w.play_live_channel = fake_play
grid._selected = {"channel": grid._rows[0][0], "prog": None}
grid._play_selected()
pump(20)
assert tuned == [grid._rows[0][0]["name"]]
assert w.mode == "live"                         # the list jumped to TV ...
assert w._guide_page_showing() and guide.isChecked() and not live.isChecked()
assert grid._playing_row == 0                   # ... and the guide marks it
playing = grid._rows[0][0]
calls.clear()

# -- Pop out: the same guide in its window, the page back where you were -------
w._on_escape()
pump()
guide.click()
pump()
grid = w._guide_grid
grid.view.verticalScrollBar().setValue(0)
grid.popout_btn.click()
pump()
win = w._guide_window
assert isinstance(win, EpgGridDialog) and win.isVisible()
assert win.grid is grid and grid.isVisible() and grid.close_btn.isVisible()
assert not grid.popout_btn.isVisible()
assert not w._guide_page_showing() and live.isChecked() and not guide.isChecked()
assert grid._refresh.isActive()                 # on screen in its window
assert not win.isModal()
# While it is out, the entry and the key bring it forward - no second one.
guide.click()
pump()
w._shortcut_epg_guide()
pump()
assert w._guide_window is win and not w._guide_page_showing()
assert live.isChecked() and not guide.isChecked()
# Closed, it goes back to the page, kept for the next time.
win.close()
pump()
assert w._guide_window is None and w._guide_grid is grid
assert grid.parentWidget() is page and not grid.close_btn.isVisible()
assert not grid._refresh.isActive()
guide.click()
pump()
assert w._guide_grid is grid and grid.isVisible() and grid._refresh.isActive()
untouched()

# -- the pane folded away (focus mode) hides it, and its timers stop -----------
w._set_focus_mode(True)
pump()
assert not grid.isVisible() and not grid._refresh.isActive()
w._set_focus_mode(False)
pump()
assert grid.isVisible() and grid._refresh.isActive()
# Opened while the pane is folded away, the page comes back with it.
w._set_focus_mode(True)
w._shortcut_epg_guide()
pump()
assert grid.isVisible() and not w._focus_mode

# -- a stale guide is drawn afresh; a new theme redraws the one on screen ------
grid._stop = time.time() + 3600
assert grid.stale()
w._on_escape()
w._shortcut_epg_guide()
pump()
assert w._guide_grid is not grid and not w._guide_grid.stale()
grid = w._guide_grid
w._guide_restyle()
pump()
assert w._guide_grid is not grid and w._guide_page_showing()
assert w._guide_grid.isVisible()
grid = w._guide_grid

# -- in video fullscreen the guide comes over it in its window ---------------
w._on_escape()
pump()
w._player_fs = True
w._shortcut_epg_guide()
pump()
assert w._guide_window is not None and not w._guide_page_showing()
w._guide_window.close()
pump()
w._player_fs = False

# -- another source: its guide is not this one's -------------------------------
w._shortcut_epg_guide()
pump()
assert w._guide_page_showing()
w._guide_source_changed()
pump()
assert not w._guide_page_showing() and w._guide_grid is None
assert live.isChecked() and not guide.isChecked()
w._shortcut_epg_guide()
pump()
w._guide_grid.popout_btn.click()
pump()
assert w._guide_window is not None
w._guide_source_changed()
pump()
assert w._guide_window is None and w._guide_grid is None
untouched()

# -- from Mine: the page, and Esc goes back to Mine ---------------------------
w._show_place("mitt")
pump()
assert w._place == "mitt" and w.nav_btns["mitt"].isChecked()
w._shortcut_epg_guide()
pump(20)
assert w._guide_page_showing() and guide.isChecked() and w._list_place == "mitt"
from_mine = w._guide_grid
w._shortcut_epg_guide()                  # its key again: the same guide
pump()
assert w._guide_grid is from_mine and w._guide_page_showing()
w._on_escape()
pump()
assert w._place == "mitt" and w.nav_btns["mitt"].isChecked()
assert not w._guide_page_showing() and w.mode in ("fav", "watchlist", "history", "watched")
# Another place over the page takes the page away too.
w._shortcut_epg_guide()
pump()
w._show_place("films")
pump()
assert not w._guide_page_showing() and w.nav_btns["vod"].isChecked()
assert not guide.isChecked()
untouched()
print("GUIDE_PAGE_OK", flush=True)
os._exit(0)
"""


def _child_env(tmp_path) -> dict:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
                "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                "XDG_CACHE_HOME"):
        env[var] = str(tmp_path / var.lower())
    return env


def test_the_guide_is_a_page_in_the_middle_pane(tmp_path):
    # One retry, as test_epg_grid: an offscreen GL context can fail to come
    # up under load and kill the child before any check ran; a real failure
    # fails both times.
    for _attempt in (1, 2):
        out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT,
                             env=_child_env(tmp_path), capture_output=True,
                             text=True, timeout=180)
        if "GUIDE_PAGE_OK" in out.stdout:
            return
        if "AssertionError" in out.stderr:
            break
    raise AssertionError(out.stdout + out.stderr[-4000:])

