"""Stats for nerds stay up in fullscreen, in the pop-out and back again.

The report: "stats for nerds still doesn't work in fullscreen". Fullscreen
on macOS is the pop-out filling the screen, and the pop-out (on Linux too)
moves the overlays into its own window - and Qt hides a widget given a new
parent. Nothing showed the stats again, so they went the moment fullscreen
began while their timer ticked on, and came back neither on leaving it. And
the I key did nothing there whenever the main window's search box had been
clicked into before: the window's remembered focus counted as typing.

Run in a child process with the real window (offscreen), mpv faked: each
step checks the box is shown, lies inside the picture that is on screen,
and nothing in its window covers it.
"""
from __future__ import annotations

import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QPoint, QRect, QSettings
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow


class FakeMpv:
    track_list = [{"type": "video", "selected": True, "codec": "h264",
                   "demux-w": 1920, "demux-h": 1080}]

    def __getattr__(self, name):
        return 1.0


w = MainWindow(DemoClient(), s, PlaylistStore(s))
p = w.player
if p is None:
    print("SKIP_NO_PLAYER", flush=True)
    os._exit(0)
w.resize(1280, 800)
w.show()
p.show()
p.video.mpv = FakeMpv()
ov = p._stats_overlay


def spin():
    for _ in range(20):
        app.processEvents()


def global_rect(widget):
    return QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())


def up(where, surface):
    spin()
    assert ov.isVisible(), f"{where}: the stats are not shown"
    assert p._stats_timer.isActive(), where
    pic = global_rect(surface)
    assert surface.isVisible() and pic.contains(global_rect(ov)), (
        f"{where}: the box {global_rect(ov)} is outside the picture {pic}")
    win = ov.window()
    r = ov.rect()
    for pt in (r.center(), r.topLeft() + QPoint(2, 2), r.bottomRight() - QPoint(2, 2),
               QPoint(r.right() - 2, r.top() + 2), QPoint(r.left() + 2, r.bottom() - 2)):
        top = win.childAt(ov.mapTo(win, pt))
        assert top is ov or (top is not None and ov.isAncestorOf(top)), (
            f"{where}: {type(top).__name__} {top.objectName() if top else ''} covers the stats")


def fullscreen():
    w._fs_toggled_at = 0.0
    w._toggle_player_fullscreen()


def popout_fullscreen():
    w._popout_fs_toggled_at = 0.0
    w._toggle_popout_fullscreen()


spin()
w._shortcut_stats()                     # the I key
up("docked", p.video)

# Fullscreen the way the window goes there on this platform (Linux: the
# window itself; macOS: the pop-out filling the screen), and back.
fullscreen()
up("fullscreen", p._ov_surface)
fullscreen()
up("after fullscreen", p.video)

# macOS's fullscreen, run here as it runs there: the mirror pop-out, then its
# window to fullscreen.
w._fs_via_popout = True
w._popout_macos()
popout_fullscreen()
assert p._ov_surface is w._popout_mirror and w._popout_win.isFullScreen()
up("macOS fullscreen", w._popout_mirror)
# The I key there, with the search box the main window's last focus: off, on.
w.search.setFocus()
spin()
w._shortcut_stats()
spin()
assert not ov.isVisible(), "I did not reach the stats in fullscreen"
w._shortcut_stats()
up("macOS fullscreen, I twice", w._popout_mirror)
w._exit_player_fullscreen()
up("after macOS fullscreen", p.video)

# The pop-out, its own fullscreen and back, then docked again.
w._toggle_popout()
up("pop-out", w._popout_mirror)
popout_fullscreen()
up("pop-out fullscreen", w._popout_mirror)
popout_fullscreen()
up("pop-out after fullscreen", w._popout_mirror)
w._toggle_popout()
up("docked again", p.video)

# Stats turned off stay off through all of it.
w._shortcut_stats()
spin()
assert not ov.isVisible()
fullscreen()
spin()
assert not ov.isVisible(), "stats that were off came back in fullscreen"
fullscreen()
w._toggle_popout()
spin()
assert not ov.isVisible(), "stats that were off came back in the pop-out"
w._toggle_popout()
spin()
assert not ov.isVisible() and not p._stats_timer.isActive()
print("STATS_FS_OK", flush=True)
os._exit(0)
"""


def test_stats_stay_up_in_fullscreen_and_the_popout():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    if "SKIP_NO_PLAYER" in out.stdout:
        import pytest
        pytest.skip("embedded player unavailable (no libmpv)")
    # os._exit after the marker: offscreen GL teardown at interpreter exit
    # is no product fault (see test_popout).
    assert "STATS_FS_OK" in out.stdout, out.stdout[-2000:] + out.stderr[-3000:]
