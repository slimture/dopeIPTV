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
    def __init__(self, window, url, title, codecs=None,
                 audio_index=0, start=0.0, tracks=None,
                 probe=True, source=None, live=False):
        cast["url"], cast["title"] = url, title
        cast["codecs"], cast["audio_index"] = codecs, audio_index
        cast["start"], cast["source"] = start, source
        cast["live"] = live

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

# The converter reads the format the player uses: some channels are simply
# not served as HLS, which is a 4XX to everything that asks for one.
assert open_cast("live", chan) == "http://p/live/u/pw/9851.m3u8"
assert cast["source"] == "http://p/live/u/pw/9851.ts", cast
# And it is handed over as a channel. Nothing in the stream says so - mpv
# answers with the seekable window and ffprobe measures a catch-up .ts to the
# second - and a length is what makes the receiver draw a name and a progress
# bar over the picture and leave them there.
assert cast["live"] is True, cast

# A favourite FILM is not a channel. The row's own kind decides; letting the
# Favorites section decide handed a movie a /live/ address built from its own
# id, and the panel answers that with a 4XX to everything that asks.
fav_movie = {"name": "Film", "_kind": "movie", "stream_id": 61155,
             "container_extension": "mkv"}
assert open_cast("fav", fav_movie) == "http://p/movie/u/pw/61155.mkv"
assert cast["live"] is False, cast
assert open_cast("fav", fav_movie, "movie") == "http://p/movie/u/pw/61155.mkv"

# Casting a film you are part way into asks about THAT position: the stored
# point is only written when playback switches or stops, so a film 22 minutes
# in had nothing saved yet and was offered from the beginning.
asked = []
w._ask_resume = lambda pos: asked.append(pos) or pos
w._playing_key = 5     # what _item_key returns for stream_id 5


class PartWay:
    current_url = "http://p/movie/u/pw/5.mp4"

    def playback_position(self):
        return 1320.0                      # 22 minutes

    def playback_duration(self):
        return 6000.0


w.player = PartWay()
film = {"name": "Film", "_kind": "movie", "stream_id": 5,
        "container_extension": "mp4"}
cast.clear()
w.mode = "history"
w._open_cast_dialog(film)
assert asked == [1320.0], asked
assert cast["start"] == 1320.0, cast
w.player = None
w._playing_key = None

# ...and in the player's own options menu, next to the audio and subtitle
# tracks - the natural place to look while watching. The player is handed a
# label and an action; it never learns what a Chromecast is.
from PyQt6.QtWidgets import QMenu
if w.player is not None:
    om = QMenu()
    w.player.populate_options_menu(om)
    assert any("Chromecast" in a.text() for a in om.actions()), \
        [a.text() for a in om.actions()]

# The player's own right-click and the menu bar cast what is playing, so a
# channel you started here can be moved to the TV without finding its row
# again. Neither offers it when nothing is playing.
assert w.can_cast_playing() is False
cast.clear()
w.cast_playing()
assert cast == {}, cast


class FakePlayer:
    current_url = "http://p/live/u/pw/9851.ts"
    visible = True

    def isVisible(self):
        return self.visible

    def hide(self):
        self.visible = False


w.player = FakePlayer()
w._playing_item = chan
w.mode = "live"
assert w.can_cast_playing() is True
w.cast_playing()
assert cast.get("url") == "http://p/live/u/pw/9851.m3u8", cast

# Not every way into the player leaves the row behind - a play from Home, a
# resumed title. The address on screen is enough, and the entry disappearing
# for those would be the wrong half of the feature.
w._playing_item = None
w._last_playback = None
cast.clear()
assert w.can_cast_playing() is True
w.cast_playing()
assert cast.get("url") == "http://p/live/u/pw/9851.ts", cast

# The cast strip lives in the right-hand column, and that column is
# draggable - so its width is not ours to assume. Pull it in and a plain row
# does not stop at its contents' minimum: it goes on until the buttons are
# drawn on top of one another. This is the real strip, not a rebuild of it,
# because what has to hold is the window's own tree.
w.show_cast_strip("Alva TV", "SVT1 HD")
# Casting stops local playback, so the pane it leaves behind is black with a
# toolbar under it that controls nothing. It goes with the stream.
assert w.player is None or w.player.isVisible() is False
from PyQt6.QtCore import QRect
for width in (640, 420, 300, 220, 160):
    height = w.cast_bar.heightForWidth(width)
    w.cast_bar.resize(width, height)
    # Straight at the layout: the strip is inside an unshown window here, so
    # the resize event that would normally re-run it is still in the post.
    w.cast_bar.layout().setGeometry(QRect(0, 0, width, height))
    shown = [c for c in w.cast_bar.findChildren(mw.QWidget)
             if not c.isHidden() and c.parent() is w.cast_bar]
    for i, a in enumerate(shown):
        assert w.cast_bar.rect().contains(a.geometry()), \
            (width, a.objectName() or type(a).__name__, a.geometry())
        for b in shown[i + 1:]:
            assert not a.geometry().intersects(b.geometry()), \
                (width, a.geometry(), b.geometry())

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
