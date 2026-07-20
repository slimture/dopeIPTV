"""Home section regressions: the page builds its shelves from the window's
stores, clicking cards leaves Home and acts in the classic view, and the
settings master-toggle hides the nav entry.

Subprocess pattern (see test_multiview): needs a real MainWindow whose
player owns an offscreen QOpenGLWidget.
"""
import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.mw_home import HomePage

app = QApplication.instance() or QApplication([])
settings = QSettings("dopeiptv-test", "home-child")
settings.clear()
w = MainWindow(DemoClient(), settings)
now = time.time()

# Seed the stores the shelves read from.
w.history.add("http://x/live/u/p/1.ts", "Chan One", None, 1, "live")
# Same movie as the resume seed below, under its history identity: Continue
# watching owns partly-watched titles, so Recently viewed must NOT repeat it.
w.history.add("http://x/movie/7.mp4", "Halfway Movie HISTROW", None,
              7, "movie")
w.favs.add("Default", {"stream_id": 5, "name": "Fav Chan",
                       "stream_icon": None})
w.movie_favs.add("Default", {"stream_id": 9, "name": "Fav Movie"})
w.series_favs.add("Default", {"series_id": 12, "name": "Fav Series"})
w.resume.record("vod", 7, pos=600, dur=3600,
                item={"stream_id": 7, "name": "Halfway Movie"})

# Home nav button exists and opens the page. (isHidden, not isVisible:
# the window itself is never shown in this offscreen run.)
assert "home" in w.nav_btns and not w.nav_btns["home"].isHidden()
w._show_home_page()
app.processEvents()
assert w._home_showing()
page = w._home_page
assert isinstance(page, HomePage)

# The synchronous shelves populated from the seeded stores.
texts = []
def walk(widget):
    from PyQt6.QtWidgets import QLabel
    for lbl in widget.findChildren(QLabel):
        texts.append(lbl.text())
walk(page)
joined = " | ".join(texts)
assert "Halfway Movie" in joined, joined     # resume shelf
# ...but its history twin is filtered out of Recently viewed (shelf sync).
assert "Halfway Movie HISTROW" not in joined, joined
assert "Fav Chan" in joined                  # favorites-now shelf (channel)
assert "Fav Movie" in joined                 # favorites shelf (movie fav)
assert "Fav Series" in joined                # favorites shelf (series fav)
assert "Chan One" in joined                  # recently viewed shelf

# Clicking a movie card leaves Home and plays it as a MOVIE (not via the
# mode-sensitive play_item - a movie must not be built as a live URL).
plays = []
def fake_sp(url, title, icon, key, kind, *a, **k):
    plays.append((title, kind))
w._start_playback = fake_sp
w.client.vod_url = lambda sid, ext=None: f"http://x/movie/{sid}.{ext or 'mp4'}"
page._play_media({"stream_id": 7, "name": "Halfway Movie",
                  "container_extension": "mp4"})
app.processEvents()
assert not w._home_showing()
assert plays == [("Halfway Movie", "movie")], plays
# ...and the detail panel under the player shows THIS movie, not whatever the
# classic list had selected before (the stale-channel-info bug). (The exact
# classic mode it lands in depends on which sections the demo provider has
# content for, so that isn't asserted here.)
assert w._detail_name == "Halfway Movie", w._detail_name

# Switching any classic mode also leaves Home.
w._show_home_page(); app.processEvents()
assert w._home_showing()
w.switch_mode("live"); app.processEvents()
assert not w._home_showing()

# A continue-watching EPISODE card must play the episode AND drill the middle
# column into its series' episode list: the drill rides the async category
# load (a plain switch_mode landed on "all series" when that load reset the
# list), and the pending-jump key selects the episode row once the list fills.
played = []
w.play_item = lambda it, *a, **k: played.append(it.get("id"))
ctx = {"series_id": 12, "name": "Fav Series"}
page._play_media({"_kind": "episode", "id": 42, "name": "S1 * E3 - Ep",
                  "container_extension": "mp4", "_series_ctx": ctx})
assert played == [42], played                 # played, before navigation
assert w.mode == "series"
assert w._pending_series_drill == ctx          # drill armed for the cat load
assert w._pending_jump_key == 42, w._pending_jump_key
app.processEvents()

# A Recently-viewed EPISODE row (carrying the stored series snapshot) plays
# as an EPISODE - so the resume prompt finds its saved position - and drills
# into its series. The context-less fallback used to degrade it to a "movie":
# restarted from zero, duplicated in History and posterless.
w.switch_mode("live"); app.processEvents()
hist_plays = []
w._start_playback = (lambda url, title, icon, key, kind, *a, **kw:
                     hist_plays.append((url, kind)))
w._pending_series_drill = None
page._play_history({"_kind": "episode", "_key": 77,
                    "_url": "http://x/series/77.mp4",
                    "name": "Show · S1 * E1 - Pilot",
                    "_series_ctx": {"series_id": 12, "name": "Fav Series"}})
assert hist_plays == [("http://x/series/77.mp4", "episode")], hist_plays
assert w.mode == "series"
assert w._pending_series_drill == {"series_id": 12, "name": "Fav Series"}
assert w._pending_jump_key == 77, w._pending_jump_key
app.processEvents()
# ...while an OLD entry without the snapshot still replays (as a movie).
w.switch_mode("live"); app.processEvents()
hist_plays.clear()
page._play_history({"_kind": "episode", "_key": 78,
                    "_url": "http://x/series/78.mp4", "name": "S01 E02"})
assert hist_plays == [("http://x/series/78.mp4", "movie")], hist_plays

# The Watch Later shelf shows playable movies/shows from the watchlist store
# (rows without a provider id are filtered out - no dead cards).
w.watchlist.movies = [{"stream_id": 9, "name": "WL Movie"},
                      {"name": "Trakt-only Movie"}]
w.watchlist.shows = [{"series_id": 12, "name": "WL Show"}]
w._show_home_page(); app.processEvents()
texts.clear()
walk(w._home_page)
joined2 = " | ".join(texts)
assert "WL Movie" in joined2
assert "WL Show" in joined2
assert "Trakt-only Movie" not in joined2

# Master toggle off: nav hidden, showing refused, page left if open.
w.settings.setValue("home_enabled", "false")
w._apply_home_settings()
assert w.nav_btns["home"].isHidden()
w._show_home_page()
assert not w._home_showing()

print("HOME_OK")
"""


def test_home_page():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD], capture_output=True, text=True,
        env=env, cwd=_REPO_ROOT, timeout=180)
    assert "HOME_OK" in proc.stdout, (
        f"home checks failed\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-2000:]!r}")


def test_is_movie_filters_live_channels():
    """The Movies shelf must exclude live channels a provider mixes into the
    VOD list, and keep real movie rows (no stream_type, or 'movie')."""
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from dopeiptv.ui.mw_home import HomePage
    is_movie = HomePage._is_movie
    assert is_movie({"stream_id": 1, "stream_type": "movie"})
    assert is_movie({"stream_id": 2})                      # blank type kept
    assert not is_movie({"stream_id": 3, "stream_type": "live"})
    assert not is_movie({"stream_id": 4, "stream_type": "radio"})
    assert not is_movie({"stream_type": "movie"})          # no id, not a movie
