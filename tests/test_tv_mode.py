"""TV mode on a real window (offscreen, in a child process as the other
window tests): every element of every screen is reached with the arrows
and none holds the focus for good, Back leaves a screen, OK plays,
channel up/down zaps, the keys come through the window's one filter, a
pad's or LIRC's actions do the same, and leaving puts the window back.
"""
from __future__ import annotations

import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os, sys, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QEvent, QSettings, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
s.setValue("tv_inputs", "false")
from dopeiptv.core import commands, shortcut_keys
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import app_menu
import dopeiptv.ui.main_window as mw
from dopeiptv.ui.tv_widgets import TvGrid, TvList, TvTile

mw.launch_player = lambda *a, **k: None


class Client(DemoClient):
    def live_categories(self):
        return [{"category_id": "1", "category_name": "News"},
                {"category_id": "2", "category_name": "Sport"}]

    def live_streams(self, category_id=None):
        rows = [{"stream_id": i + 1, "num": i + 1, "name": f"Chan {i + 1}", "stream_icon": "",
                 "category_id": "1" if i < 5 else "2", "epg_channel_id": ""} for i in range(9)]
        return [r for r in rows if category_id in (None, r["category_id"])]

    def live_url(self, sid, fmt="ts"):
        return f"http://tv.invalid/{sid}.ts"

    def vod_categories(self):
        return [{"category_id": "10", "category_name": "Drama"}]

    def vod_streams(self, category_id=None):
        return [{"stream_id": 100 + i, "name": f"Film {i}", "category_id": "10"} for i in range(11)]

    def vod_url(self, sid, ext=None):
        return f"http://tv.invalid/movie/{sid}.mp4"

    def series_categories(self):
        return [{"category_id": "20", "category_name": "Crime"}]

    def series_list(self, category_id=None):
        return [{"series_id": 300 + i, "name": f"Show {i}", "category_id": "20"} for i in range(4)]

    def series_info(self, sid):
        return {"episodes": {"1": [{"id": 9000 + j, "episode_num": j + 1, "title": f"Ep {j + 1}",
                                    "container_extension": "mp4"} for j in range(3)],
                             "2": [{"id": 9100 + j, "episode_num": j + 1, "title": f"Ep {j + 1}",
                                    "container_extension": "mp4"} for j in range(2)]}}

    def episode_url(self, eid, ext=None):
        return f"http://tv.invalid/ep/{eid}.{ext or 'mp4'}"


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
w.history.add("http://tv.invalid/3.ts", "Chan 3", None, 3, "live", extra={"stream_id": 3, "num": 3})
w.favs.add("all", {"stream_id": 2, "name": "Chan 2", "num": 2})
w.resume.record("vod", 104, pos=600, dur=3600, item={"stream_id": 104, "name": "Film 4"})
w.show()


def spin(secs=0.2):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def until(cond, secs=4.0):
    end = time.monotonic() + secs
    while not cond() and time.monotonic() < end:
        spin(0.02)
    return cond()


def key(k, text=""):
    target = QApplication.focusWidget() or w
    ov = QKeyEvent(QEvent.Type.ShortcutOverride, k, Qt.KeyboardModifier.NoModifier, text)
    QApplication.sendEvent(target, ov)
    assert ov.isAccepted(), k                     # TV mode's, not a shortcut's
    QApplication.sendEvent(target, QKeyEvent(QEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier, text))
    spin(0.03)


# -- the menu entry, the key, the command line --------------------------------------
assert "tv_mode" in dict((g, n) for g, _w, n in app_menu.GROUPS)["view"]
assert shortcut_keys.DEFAULTS["tv_mode"] == "Ctrl+Shift+T"
assert "tv_mode" in w._shortcuts
assert commands.parse_command(["--tv"]) == {"tv": True}
assert commands.parse_command(["--tv", "--channel", "X"]) == {"channel": "X", "tv": True}
assert commands.parse_command(["--channel", "X"]) == {"channel": "X"}

spin(0.5)
assert w.handle_command({"tv": True})["ok"]
spin(0.3)
page = w._tv_page
assert w._tv_on and w.isFullScreen() and not w.menuBar().isVisible()
assert w._center_stack.currentWidget() is page and page.current == "home"
assert until(lambda: any(sh.title.text() == "Live now" and not sh.isHidden() for sh in page.screens["home"].shelves))


# -- every element reached, none a trap --------------------------------------------
DIRS = ("up", "down", "left", "right")


def prep(n, d):
    # A list or the grid at the edge the arrow leaves it by.
    if isinstance(n, TvList) and n.count():
        n.set_row(0 if d != "down" else n.count() - 1)
    elif isinstance(n, TvGrid) and n.count():
        if d == "down":
            n.set_row(n.count() - 1)
        elif d == "right":
            n.set_row(min(n.columns(), n.count()) - 1)
        else:
            n.set_row(0)
    g = getattr(page.screens.get("guide"), "grid", None)
    if g is not None and n is g.view and g._rows:
        g._select_block(0 if d == "up" else len(g._rows) - 1, 0)


def walk(screen):
    page.show_screen(screen)
    spin(0.4)
    start = page.tab(screen)
    want = [x for x in page.focusables()]
    seen, todo = {id(start): start}, [start]
    while todo:
        n = todo.pop()
        for d in DIRS:
            if n in page.tabs and d == "down" and n is not start:
                continue                       # another screen's way in
            prep(n, d)
            page.set_focus(n)
            page.move(d)
            spin(0.01)
            m = page.focus_widget()
            if page.current != screen:
                page.show_screen(screen)
                spin(0.05)
                continue
            if m is not None and id(m) not in seen:
                seen[id(m)] = m
                todo.append(m)
    missed = [type(x).__name__ + ":" + getattr(x, "title", "") for x in want if id(x) not in seen]
    assert not missed, (screen, missed)
    # From each one, Up enough times reaches the tabs (lists step their rows).
    for n in want:
        if n in page.tabs:
            continue
        page.set_focus(n)
        for _ in range(120):
            if page.focus_widget() in page.tabs:
                break
            page.move("up")
        assert page.focus_widget() is page.tab(screen), (screen, type(n).__name__)
    return want


for name in ("home", "channels", "vod", "series", "settings", "guide", "search"):
    if name in ("channels", "vod", "series", "guide"):
        page.show_screen(name)
        until(lambda: len(page.screen(name).focusables()) > 1, 4.0)
    got = walk(name)
    print("walked", name, len(got))

# Every row of a list, and every cell of the grid, with the arrows alone.
ch = page.screen("channels")
page.show_screen("channels")
page.set_focus(ch.chans)
ch.chans.set_row(0)
for _ in range(ch.chans.count() - 1):
    page.move("down")
assert ch.chans.row() == ch.chans.count() - 1
vod = page.screen("vod")
page.show_screen("vod")
page.set_focus(vod.grid)
vod.grid.set_row(0)
cells = {0}
for _ in range(30):
    page.move("right") if vod.grid.row() % vod.grid.columns() < vod.grid.columns() - 1 else page.move("down")
    cells.add(vod.grid.row())
    if vod.grid.row() == vod.grid.count() - 1:
        break
assert vod.grid.count() - 1 in cells, cells

# The search: typed on the keyboard on screen and on a real one; results reachable.
page.show_screen("search")
srch = page.screen("search")
page.set_focus(srch.keyboard.keys[2])          # "c"
key(Qt.Key.Key_Return)
key(Qt.Key.Key_H, "h")
assert srch.query == "ch", srch.query
key(Qt.Key.Key_Backspace)
assert srch.query == "c"
key(Qt.Key.Key_H, "h")
assert until(lambda: srch.shelves and srch.shelves[0].tiles())
walk("search")

# -- Back leaves screens --------------------------------------------------------------
for name in ("channels", "guide", "vod", "series", "search", "settings"):
    page.show_screen(name)
    spin(0.2)
    for _ in range(4):
        if page.current == "home":
            break
        key(Qt.Key.Key_Escape)
    assert page.current == "home", name
page.show_screen("series")
until(lambda: page.screen("series").grid.count() > 0)
ser = page.screen("series")
page.set_focus(ser.grid)
key(Qt.Key.Key_Return)                          # a series opens its episodes
assert until(lambda: ser.stack.currentWidget() is ser.ep_box and ser.episodes.count() == 3)
# Its seasons in a row over them (as on its page in the window): Right and
# Left in the episodes go to the season after and before.
assert len(ser.season_tiles) == 2 and ser.season_area.isVisibleTo(ser)
assert ser.season_tiles[0].current and ser.season_at == 0
key(Qt.Key.Key_Right)
assert ser.season_at == 1 and ser.episodes.count() == 2 and ser.season_tiles[1].current
key(Qt.Key.Key_Left)
assert ser.season_at == 0 and ser.episodes.count() == 3
key(Qt.Key.Key_Escape)                          # back to the posters
assert ser.stack.currentWidget() is ser.grid and page.current == "series"

# -- OK plays, channel up/down zaps ------------------------------------------------------
page.show_screen("channels")
page.set_focus(ch.cats)
ch.cats.set_row([c["_cat"] for c in ch.cats.items()].index("1"))     # News
key(Qt.Key.Key_Return)                          # OK on a category: its channels
assert until(lambda: ch.chans.count() == 5 and page.focus_widget() is ch.chans)
ch.chans.set_row(1)
key(Qt.Key.Key_Return)
assert until(lambda: w._tv_watching and w._player_fs), (w._tv_watching, w._player_fs)
first = played[-1]
assert first.endswith(f"/{ch.chans.items()[1]['stream_id']}.ts"), played
assert w._tv_info is not None and w._tv_info.isVisible()
key(Qt.Key.Key_PageUp)                          # channel up: the next in that list
assert until(lambda: played[-1] != first)
assert played[-1].endswith(f"/{ch.chans.items()[2]['stream_id']}.ts"), played
key(Qt.Key.Key_ChannelDown)
assert until(lambda: played[-1] == first), played
key(Qt.Key.Key_Up)                              # Up zaps too, over the picture
assert until(lambda: played[-1].endswith(f"/{ch.chans.items()[2]['stream_id']}.ts"))
info = w.tv_info()
assert info["name"] == ch.chans.items()[2]["name"] and info["num"] == ch.chans.items()[2]["num"]
assert until(lambda: w._tv_info.isVisible())
key(Qt.Key.Key_Return)                          # OK hides the info bar...
assert not w._tv_info.isVisible()
key(Qt.Key.Key_Q, "q")                          # ...and any key brings it back
assert w._tv_info.isVisible()
# The guide over the picture keeps it; Back returns to it. Back over the
# picture: the info bar first, then the screen it came from with the stream
# going on behind it (Now playing above the tabs); Stop stops it.
key(Qt.Key.Key_Green)
assert until(lambda: page.current == "guide" and not w._tv_watching)
assert w._tv_has_picture()
key(Qt.Key.Key_Escape)
assert until(lambda: w._tv_watching)
assert w._tv_info.isVisible()
key(Qt.Key.Key_Escape)                          # the info bar goes first
assert w._tv_watching and not w._tv_info.isVisible()
key(Qt.Key.Key_Escape)
assert until(lambda: not w._tv_watching)
assert w._tv_has_picture() and w.player.current_url == played[-1]
assert page.current == "guide" and w._center_stack.currentWidget() is page
assert not page.now.isHidden()
assert page.hint.text() == f"{tr('tv_hint_now')} · {tr('tv_hint_leave')}", page.hint.text()
# Back goes on towards Home - never round to the picture again - and on
# Home asks before it leaves TV mode; any other key takes the question back.
key(Qt.Key.Key_Escape)
assert page.current == "home" and not w._tv_watching and w._tv_has_picture()
key(Qt.Key.Key_Escape)
assert w._tv_on and page.flash_lbl.isVisible() and page.flash_lbl.text() == tr("tv_leave_again")
key(Qt.Key.Key_Right)
assert w._tv_on and not page.flash_lbl.isVisible()
key(Qt.Key.Key_P)                               # Play: the picture again
assert until(lambda: w._tv_watching)
key(Qt.Key.Key_X)                               # Stop stops
assert until(lambda: not w._tv_watching and not w._tv_has_picture())
assert w._center_stack.currentWidget() is page and page.now.isHidden()

# -- a pad's and LIRC's actions: the same way in ------------------------------------------
page.show_screen("channels")
page.set_focus(ch.chans)
ch.chans.set_row(0)
w._tv_input("down")
assert ch.chans.row() == 1
n = len(played)
w._tv_input("ok")
assert until(lambda: len(played) > n and w._tv_watching)
w._tv_input("back")                             # the info bar
w._tv_input("back")                             # the page, the stream behind it
assert until(lambda: not w._tv_watching) and w._tv_has_picture()
w._tv_input("stop")
assert until(lambda: not w._tv_has_picture())

# The phone, the remote API and Home Assistant: the same way in (tv_command).
page.show_screen("channels")
page.set_focus(ch.chans)
ch.chans.set_row(0)
assert w.tv_command("tv_key", "down") == {"ok": True, "tv_mode": True}
assert ch.chans.row() == 1
assert w._phone_do("tv_key", {"action": "down"})["ok"] and ch.chans.row() == 2
assert w._remote_do("tv_key", {"action": "up"})["ok"] and ch.chans.row() == 1
assert w._phone_state()["tv_mode"] is True

# A film from Movies plays; the red key makes a channel a favorite.
page.show_screen("vod")
page.set_focus(vod.grid)
vod.grid.set_row(3)
w._tv_input("ok")
assert until(lambda: played[-1] == "http://tv.invalid/movie/103.mp4"), played
w._tv_input("stop")
assert until(lambda: not w._tv_watching)
page.show_screen("channels")
page.set_focus(ch.chans)
ch.chans.set_row(0)
sid = ch.chans.current_item()["stream_id"]
was = w.favs.is_favorite(sid)
key(Qt.Key.Key_Red)
assert w.favs.is_favorite(sid) is not was
# A channel's number, then OK: it plays.
key(Qt.Key.Key_7, "7")
key(Qt.Key.Key_Return)
assert until(lambda: played[-1] == "http://tv.invalid/7.ts" and w._tv_watching), played
key(Qt.Key.Key_Escape)
key(Qt.Key.Key_Escape)
assert until(lambda: not w._tv_watching) and w._tv_has_picture()
# Options' Record, with the page in front of the picture: what plays.
page.show_screen("settings")
rec_tile = page.screen("settings").tiles["record"]
assert rec_tile.isEnabled() and rec_tile.title == tr("rec_record") and rec_tile.sub == "Chan 7", \
    (rec_tile.isEnabled(), rec_tile.title, rec_tile.sub)
assert rec_tile in page.focusables()
key(Qt.Key.Key_X)
assert until(lambda: not w._tv_has_picture())
page.screen("settings").refresh()
assert not rec_tile.isEnabled() and rec_tile.sub == tr("tv_rec_live_only")

# -- the app's questions in TV mode: its own window, answered with the remote --------------
from PyQt6.QtCore import QTimer
from dopeiptv.ui.tv_widgets import TvAsk
seen = []


def answer():
    d = QApplication.activeModalWidget()
    seen.append(type(d).__name__)
    w._tv_input("down")                         # a pad's, LIRC's or the phone's arrow
    # A keyboard's OK goes to the question, not to TV mode's filter.
    QApplication.sendEvent(d.focus_at(), QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                                                   Qt.KeyboardModifier.NoModifier))


QTimer.singleShot(50, answer)
QTimer.singleShot(5000, lambda: [x.reject() for x in QApplication.topLevelWidgets()
                                 if isinstance(x, TvAsk)])
picked = w._choice_dialog("Question", "Words", [("One", "primary"), ("Two", "normal")])
assert seen == ["TvAsk"] and picked == 1, (seen, picked)
assert w._tv_on and w._center_stack.currentWidget() is page

# -- leaving puts the window back ----------------------------------------------------------
key(Qt.Key.Key_Blue)
assert page.current == "settings"
leave = page.screen("settings").tiles["exit"]
page.set_focus(leave)
key(Qt.Key.Key_Return)
assert until(lambda: not w._tv_on)
assert not w.isFullScreen() and w.menuBar().isVisible()
assert w._center_stack.currentIndex() == 0
# Its clock is TV mode's alone: the page stays made, its timer does not run.
assert not page._clock_timer.isActive()
# Off, the keys are the app's own again.
ov = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier)
QApplication.sendEvent(w, ov)
assert not ov.isAccepted()
# Off, TV mode's keys from the phone leave playback alone.
n = len(played)
for a in ("ok", "up", "left", "red", "digit_3", "back", "menu"):
    assert w.tv_command("tv_key", a) == {"ok": False, "tv_mode": False}, a
assert len(played) == n and not w._tv_on and w._phone_state()["tv_mode"] is False
# On and off again from the phone.
assert w.tv_command("tv", "toggle")["tv_mode"] and w._tv_on and w.isFullScreen()
assert until(lambda: page._clock_timer.isActive())     # shown again: its clock again
assert not w.tv_command("tv", "off")["tv_mode"] and not w._tv_on and w.menuBar().isVisible()
assert until(lambda: not page._clock_timer.isActive())
print("TV_OK", flush=True)
os._exit(0)
"""


def test_tv_mode_on_a_real_window():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=_REPO_ROOT)
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=240)
    text = out.stdout + out.stderr
    if "TV_SKIP" in text:
        return
    assert "TV_OK" in text, text[-4000:]
