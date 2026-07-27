"""Casting must work from every section, not just the channel list.

A live channel has to be handed over as HLS - the receiver cannot decode a raw
MPEG transport stream at all, whatever it is labelled - and a live row can turn
up in Channels, Favorites, History and Home alike. History used to cast its
stored .ts address verbatim, so casting from there could never produce a
picture while the very same channel cast fine from the channel list.

Runs in a subprocess: the window embeds a QOpenGLWidget whose offscreen
teardown can abort at interpreter exit, so we assert on the child's success
marker and ignore its exit status (same pattern as the other window tests).
"""
import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from dopeiptv.providers.client import DemoClient
import dopeiptv.ui.main_window as mw

app = QApplication.instance() or QApplication([])
settings = QSettings("dopeiptv-test", "cast-sources")
settings.clear()
w = mw.MainWindow(DemoClient(), settings)

cast = {}


class FakeDialog:
    def __init__(self, window, url, title, codecs=None, audio_index=0):
        cast["url"], cast["title"] = url, title
        cast["codecs"], cast["audio_index"] = codecs, audio_index

    def exec(self):
        return 0


class FakeClient(DemoClient):
    def live_url(self, stream_id, fmt="ts"):
        return "http://p/live/u/pw/%s.%s" % (stream_id, fmt)

    def vod_url(self, stream_id, ext=None):
        return "http://p/movie/u/pw/%s.%s" % (stream_id, ext or "mp4")


mw.CastDialog = FakeDialog
mw.ChromecastManager.available = staticmethod(lambda: True)
w.client = FakeClient()


def open_cast(mode, item, fav_section="chan"):
    cast.clear()
    w.mode = mode
    w._fav_section = fav_section
    w._open_cast_dialog(item)
    return cast.get("url")


chan = {"name": "SVT1", "stream_id": 9851}
hist_chan = dict(chan, _kind="live", _url="http://p/live/u/pw/9851.ts")

# The channel list - this always worked, and must keep working.
assert open_cast("live", chan) == "http://p/live/u/pw/9851.m3u8"

# History: the row carries the .ts address it was played from. Cast it as
# HLS anyway - this is the bug.
assert open_cast("history", hist_chan) == "http://p/live/u/pw/9851.m3u8"

# Favorites (the channel section), and a channel row inside the grouped
# "all favorites" view.
assert open_cast("fav", chan) == "http://p/live/u/pw/9851.m3u8"
assert open_cast("fav", hist_chan, "movie") == "http://p/live/u/pw/9851.m3u8"

# A movie in History has no provider id - its stored address is all there is,
# and it is the right one.
movie = {"name": "Film", "_kind": "movie", "_url": "http://p/movie/x.mp4"}
assert open_cast("history", movie) == "http://p/movie/x.mp4"

# A movie in the Movies list is built from its provider id as before.
assert open_cast("vod", {"name": "Film", "stream_id": 5,
                         "container_extension": "mp4"}) == \
    "http://p/movie/u/pw/5.mp4"

# Nothing to cast: no dialog at all rather than one pointing nowhere.
assert open_cast("history", {"name": "Ghost"}) is None

print("CAST_SOURCES_OK")
"""


def test_every_section_casts_a_channel_as_hls():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD], capture_output=True, text=True,
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
        cwd=_REPO_ROOT, timeout=180)
    assert "CAST_SOURCES_OK" in proc.stdout, (
        f"cast source checks failed\nstdout={proc.stdout!r}\n"
        f"stderr={proc.stderr[-2000:]!r}")
