"""The column under the video while an episode plays: its series' poster and
the episode's name - never the row the list had picked before, a TV channel
as often as not, whose logo stayed there and whose button would have started
that channel (seen on a nightly after 1.6.8). In the real window, offscreen,
in a child process as the other window tests."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow

w = MainWindow(DemoClient(), s, PlaylistStore(s))
channel = {"name": "Kanal Ett", "stream_id": 7, "stream_icon": ""}
w._current_key = w._item_key(channel)
w._show_detail(channel)
assert w._detail_name == "Kanal Ett", w._detail_name
assert w.d_logo.width() == w.POSTER_SIZE_LIVE[0]

w.series_ctx = None
w._detail_follow_episode({"id": 5, "name": "Pilot"}, "ep-5")
assert w._detail_name == "Kanal Ett", "no series: the column as it was"

w.series_ctx = {"name": "Example Show", "cover": ""}
w._detail_follow_episode({"id": 5, "name": "Pilot"}, "ep-5")
assert w._current_key == "ep-5", w._current_key
assert w._detail_name == "Pilot", w._detail_name
assert w.d_logo.width() == w.POSTER_SIZE_MEDIA[0], "a series' poster, not a channel's logo"
assert w.d_logo.text() == "E", w.d_logo.text()
print("ok")
"""


def test_an_episode_shows_its_series_under_the_video():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               PYTHONPATH=_REPO_ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""))
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.strip().endswith("ok"), out.stdout + out.stderr


def test_every_episode_start_goes_through_it():
    """_start_playback is the one way in for every episode - the series'
    page, Continue, the next one, TV mode, the phone, watch together."""
    src = (Path(_REPO_ROOT) / "dopeiptv" / "ui" / "main_window.py").read_text(encoding="utf-8")
    body = src.split("def _start_playback(", 1)[1].split("\n    def ", 1)[0]
    assert "self._play_tell(title, key, kind, item, restart)" in body
    tell = src.split("def _play_tell(", 1)[1].split("\n    def ", 1)[0]
    assert 'if kind == "episode":\n            try:\n                self._detail_follow_episode(item, key)' in tell


_CHILD_PAGE = r"""
import os, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw_mod
from dopeiptv.ui.main_window import MainWindow

mw_mod.launch_player = lambda *a, **k: None
w = MainWindow(DemoClient(), s, PlaylistStore(s))
w.cover.resolver = None
w._choice_dialog = lambda *a, **k: 0

def settle(cond, secs=10):
    end = time.time() + secs
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert cond(), "timed out"

def idle(secs):
    end = time.time() + secs
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)

def season(n, count):
    return [{"id": str(n * 1000 + e), "episode_num": e, "title": f"Ep {e}",
             "name": f"Show 30 S{n}E{e}", "container_extension": "mkv",
             "info": {"plot": "A plot."}} for e in range(1, count + 1)]

info = {"info": {"name": "Show 30", "plot": "The series.", "genre": "Comedy"},
        "episodes": {"1": season(1, 3), "2": season(2, 2)}}
asked = []
w.client.series_info = lambda sid: asked.append(sid) or info
w.client.episode_url = lambda i, ext=None: f"http://x/series/u/p/{i}.mkv"
w.resize(1440, 900)
w.show()
settle(lambda: w.list_model.rowCount() > 0)
idle(0.5)

channel = {"name": "Kanal Ett", "stream_id": 7, "stream_icon": ""}
w._current_key = w._item_key(channel)
w._show_detail(channel)
assert w.d_logo.width() == w.POSTER_SIZE_LIVE[0]

# -- a series opened (a click in the Series place, a row, Now playing): its
#    poster at once, not the channel's logo left from before ------------------
series = {"series_id": 130, "name": "Show 30", "cover": ""}
w._open_series_page(series)
assert w.d_logo.width() == w.POSTER_SIZE_MEDIA[0], "the channel's logo stayed"
assert w.d_logo.text() == "S", w.d_logo.text()
assert w._detail_name == "Show 30", w._detail_name
settle(lambda: bool(w._sp_seasons))
page = w._series_page
idle(0.5)
assert asked == [130], f"series_info asked more than once: {asked}"
assert w.media_plot.text() == "The series.", w.media_plot.text()
cur = page.view.current_item()
assert cur is not None and w._current_key == cur["id"], (w._current_key, cur)

# -- an episode clicked once (nothing plays): the column shows it, the
#    poster's button plays that very episode --------------------------------
r = page.view.visualRect(page.model.index(2, 0))
QTest.mouseClick(page.view.viewport(), Qt.MouseButton.LeftButton, pos=r.center())
idle(0.5)
ep = page.view.current_item()
assert ep["id"] == "1003" and w._current_key == "1003", (ep, w._current_key)
assert w._detail_name == ep["name"], (w._detail_name, ep["name"])
assert w.d_logo.width() == w.POSTER_SIZE_MEDIA[0]
assert w._overlay_state() == "play"
assert getattr(w, "_last_playback", None) is None or w._last_playback.get("key") != "1003"
w._play_overlay_clicked()
app.processEvents()
assert w._last_playback["kind"] == "episode" and str(w._last_playback["key"]) == "1003", \
    w._last_playback
w.player.current_url = w.player.current_url or "http://x/series/u/p/1003.mkv"
assert w._overlay_state() != "play", w._overlay_state()

# Another episode picked while that one plays: its own button reads play.
r = page.view.visualRect(page.model.index(0, 0))
QTest.mouseClick(page.view.viewport(), Qt.MouseButton.LeftButton, pos=r.center())
idle(0.5)
assert w._current_key == "1001" and w._overlay_state() == "play", w._overlay_state()

# -- a channel picked, then Now playing: the series' poster again ----------
w._leave_series_page()
w._current_key = w._item_key(channel)
w._show_detail(channel)
assert w.d_logo.width() == w.POSTER_SIZE_LIVE[0]
w._jump_to_now_playing()
settle(lambda: w._series_page_showing())
idle(0.5)
assert w.d_logo.width() == w.POSTER_SIZE_MEDIA[0], "Now playing left the channel's logo"
assert w._current_key == "1003", w._current_key
assert w._overlay_state() != "play", w._overlay_state()
print("ok")
os._exit(0)
"""


def test_a_series_page_drives_the_column_under_the_video():
    """A series opened, an episode clicked once, Now playing: the column under
    the video shows the series' poster and the episode picked, and its button
    plays that episode - as a click on a channel or a film shows its own."""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               PYTHONPATH=_REPO_ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""))
    out = subprocess.run([sys.executable, "-c", _CHILD_PAGE], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stdout[-3000:] + out.stderr[-4000:]
    assert out.stdout.strip().endswith("ok"), out.stdout[-3000:] + out.stderr[-4000:]
