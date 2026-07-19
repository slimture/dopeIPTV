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
w.favs.add("Default", {"stream_id": 5, "name": "Fav Chan",
                       "stream_icon": None})
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
assert "Fav Chan" in joined                  # favorites-now shelf
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

# Switching any classic mode also leaves Home.
w._show_home_page(); app.processEvents()
assert w._home_showing()
w.switch_mode("live"); app.processEvents()
assert not w._home_showing()

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
