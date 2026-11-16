"""The middle pane follows what plays, in the real window: what starts from
outside it (the phone, the remote API, the media keys, channel up and
down) and is not what it shows takes it there - a channel to TV and its
category, an episode to its series' page with the episode marked, a film
to Movies and its category, a local file to its folder, a station to
Radio; a web stream leaves it. Never while the person types in a field,
with a dialog open, in TV mode, for a play from the pane itself, or for
the same title started again."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import copy, json, os, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import QApplication, QDialog

_tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _tmp)

from dopeiptv.core import series_view as sv
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw_mod
from dopeiptv.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
films_dir = os.path.join(_tmp, "Films")
os.makedirs(films_dir)
film_file = os.path.join(films_dir, "Holiday 2024.mkv")
open(film_file, "wb").write(b"\0" * 64)
settings = QSettings(os.path.join(_tmp, "s.ini"), QSettings.Format.IniFormat)
settings.setValue("home_enabled", "false")
settings.setValue("local_dirs", json.dumps([films_dir]))
mw_mod.launch_player = lambda *a, **k: None                    # never a real mpv window
w = MainWindow(DemoClient(), settings)
w.cover.resolver = None
w._choice_dialog = lambda *a, **k: 0
w._autoplay_preview = lambda: False

def settle(cond, secs=12, what=""):
    end = time.time() + secs
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert cond(), "timed out: " + what

def idle(secs):
    end = time.time() + secs
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)

c = w.client
c.live_url = lambda sid, fmt="ts": f"http://127.0.0.1:9/live/{sid}.ts"
c.vod_categories = lambda: [{"category_id": "10", "category_name": "Drama"},
                            {"category_id": "11", "category_name": "Comedy"}]
films = [{"stream_id": 300 + i, "name": f"Film {i:02d}", "category_id": "11" if i % 2 else "10",
          "container_extension": "mkv"} for i in range(30)]
c.vod_streams = lambda cat=None: [f for f in films if cat in (None, f["category_id"])]
c.vod_url = lambda sid, ext=None: f"http://127.0.0.1:9/movie/{sid}.mkv"
c.series_categories = lambda: [{"category_id": "6", "category_name": "Crime"}]
shows = [{"series_id": 100 + i, "name": f"Show {i:02d}", "category_id": "6", "cover": ""} for i in range(8)]
c.series_list = lambda cat=None: [s for s in shows if cat in (None, s["category_id"])]
def season(n, count):
    return [{"id": str(n * 1000 + e), "episode_num": e, "title": f"Ep {e}", "container_extension": "mkv",
             "info": {"duration_secs": 2700}} for e in range(1, count + 1)]
info = {"info": {"name": "Show 03"}, "episodes": {"1": season(1, 3), "2": season(2, 4)}}
c.series_info = lambda sid: info
c.episode_url = lambda i, ext=None: f"http://127.0.0.1:9/series/{i}.{ext or 'mkv'}"

w.resize(1440, 900)
w.show()
settle(lambda: w.list_model.rowCount() > 0, what="first list")
idle(1.0)

def selected_key():
    it = w.list_model.item_at(w.listw.currentIndex().row())
    return None if it is None else str(w._item_key(it))

def cat_data():
    item = w.cat_list.currentItem()
    return None if item is None else item.data(Qt.ItemDataRole.UserRole)

# 1. The series lists on screen, a channel started from outside: TV, its
#    category, the channel selected - as the list does when you zap.
w.switch_mode("series")
settle(lambda: w.mode == "series" and w.list_model.rowCount() > 0, what="series lists")
ch = c.live_streams()[2]
w.play_live_channel(ch)
settle(lambda: w.mode == "live" and selected_key() == "3", what="channel followed from Series")
assert cat_data() == DemoClient.CATEGORY_ID, cat_data()

# 2. An episode started from outside (the phone's path) while the list shows
#    channels: its series' page, the episode marked, its season shown.
series = shows[3]
seasons = sv.seasons(w._episode_rows(copy.deepcopy(info), series))
ep = seasons[1].episodes[1]
w._play_series_episode(series, seasons, ep)
settle(lambda: w._series_page_showing() and str((w._sp_ctx or {}).get("series_id")) == "103"
       and w._sp_seasons, what="episode's series page")
settle(lambda: w._sp_playing()[0] == "2002" and w._series_page.current == 1, what="episode marked")

# 3. A channel from outside while that page shows: the page leaves as its
#    Back would, the list goes to the channel.
w.play_live_channel(c.live_streams()[0])
settle(lambda: not w._series_page_showing() and w.mode == "live" and selected_key() == "1",
       what="channel followed from the series page")
assert w._sp_timer is None or not w._sp_timer.isActive()

# 4. A film: Movies, its category, the film selected.
w._pb_play_movie(films[5])
settle(lambda: w.mode == "vod" and selected_key() == "305", what="film followed")
assert str(cat_data()) == "11", cat_data()

# 5. A local file: Local files, its folder, the file selected.
w._pb_play_local(film_file, [film_file])
settle(lambda: w.mode == "local" and selected_key() == film_file, what="local file followed")

# 6. A station: Radio, the station selected among the favourite stations.
st = {"stationuuid": "u-p3", "name": "P3", "url": "http://127.0.0.1:9/p3", "stream_icon": ""}
w.radio_favs.add("Favorites" if not w.radio_favs.custom_groups() else w.radio_favs.custom_groups()[0], st)
w._radio_play(st)
settle(lambda: w.mode == "radio", what="station followed")
settle(lambda: selected_key() == "radio:u-p3", what="station selected")

# 7. A web stream: the pane stays.
w.switch_mode("vod")
settle(lambda: w.mode == "vod" and w.list_model.rowCount() > 0, what="movies again")
w._pane_follow_soon("web", "https://example.invalid/watch", False)
idle(0.5)
assert w.mode == "vod" and w._pane_follow_job is None

# 8. Never while the person types in a field of the window - but a search
#    box that only has the focus, empty, is no one typing.
w.switch_mode("series")
settle(lambda: w.mode == "series" and w.list_model.rowCount() > 0, what="series lists again")
real_active = w.isActiveWindow
w.isActiveWindow = lambda: True
real_focus = QApplication.focusWidget
QApplication.focusWidget = staticmethod(lambda: w.search)
w.search.setText("Show 0")
w.play_live_channel(c.live_streams()[1])
idle(1.0)
assert w.mode == "series", "followed while typing"
w.search.clear()
w._start_playback(c.live_url(4), "Art of Motion", None, 4, "live", item=c.live_streams()[3])
settle(lambda: w.mode == "live" and selected_key() == "4", what="an empty focused search box is no typing")
QApplication.focusWidget = real_focus
w.isActiveWindow = real_active
w.switch_mode("series")
settle(lambda: w.mode == "series" and w.list_model.rowCount() > 0, what="series lists once more")

# 9. Never with a dialog open, nor in TV mode.
dlg = QDialog(w)
dlg.setModal(True)
dlg.show()
app.processEvents()
w.play_live_channel(c.live_streams()[3])
idle(1.0)
assert w.mode == "series", "followed under a dialog"
dlg.close()
app.processEvents()
w._tv_on = True
w.play_live_channel(c.live_streams()[4])
idle(1.0)
assert w.mode == "series", "followed in TV mode"
w._tv_on = False

# 10. Never for a play from the pane itself (the Search page's), nor for the
#     same title started again (the app's reconnect).
with w._from_pane():
    w.play_live_channel(c.live_streams()[0])
idle(1.0)
assert w.mode == "series", "followed a play from the pane"
w._start_playback(c.live_url(1), "Big Buck Bunny", None, 1, "live", item=c.live_streams()[0])
idle(1.0)
assert w.mode == "series", "followed the same channel again"
# ...but channel up on the computer (the player's button, the media keys)
# does: through the source's channels, the list shows none - and there.
w._media_active = lambda: True               # the stream plays (nothing to play here)
w._zap(1)
settle(lambda: w.mode == "live" and selected_key() == "2", what="channel up followed")
print("PANE_FOLLOW_OK", flush=True)
os._exit(0)
"""


def test_the_middle_pane_follows_what_plays_in_the_window(tmp_path):
    pytest.importorskip("PyQt6")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=str(tmp_path),
               XDG_DATA_HOME=str(tmp_path / "data"), XDG_CACHE_HOME=str(tmp_path / "cache"))
    proc = subprocess.run([sys.executable, "-c", _CHILD], capture_output=True, text=True,
                          env=env, cwd=_REPO_ROOT, timeout=240)
    assert "PANE_FOLLOW_OK" in proc.stdout, (
        f"stdout={proc.stdout[-3000:]!r}\nstderr={proc.stderr[-4000:]!r}")
