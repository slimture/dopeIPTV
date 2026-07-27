"""Object-graph regression test for the detached ("pop out") player.

On Linux the pop-out MIRRORS the stream into the new window (raster mirror by
default - see _RasterMirror): the one real player widget is NEVER reparented,
its control bar moves into the pop-out, and docking back must restore
everything. If that graph is wrong the shared player is orphaned or destroyed
and playback breaks, so guard it:

- popping out creates the pop-out window with a mirror child; the player (and
  its mpv/GL surface) stays docked in the detail pane;
- the control bar moves into the pop-out and comes back on dock-in;
- window toggles (fullscreen, on-top, frameless, auto-hide) neither raise nor
  leak state;
- closing the pop-out window docks back rather than destroying anything.

The assertions run in a subprocess. GL/mpv teardown on the offscreen platform
can abort at Qt's interpreter-exit (the app itself sidesteps this via
os._exit); that teardown abort is not a product fault, so we assert on the
child's success marker and ignore its exit status. Runs headless: the GL
render context can't build there, so this checks the widget graph and mode
flags, not on-screen rendering.
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
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QPushButton, QVBoxLayout, QWidget)
from dopeiptv.media.embedded import EmbeddedPlayer
from dopeiptv.ui.mw_popout import _PopoutMixin

app = QApplication.instance() or QApplication([])


class Host(QMainWindow, _PopoutMixin):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("dopeiptv-test", "popout-sub")
        self.settings.clear()
        self._pip_win = None
        self._popout_win = None
        self._popout_placeholder = None
        self._player_fs = False
        self._det = QWidget()
        lay = QVBoxLayout(self._det)
        lay.setContentsMargins(0, 0, 0, 0)
        self.player = EmbeddedPlayer()
        lay.addWidget(self.player, 1)
        self.setCentralWidget(self._det)

    def _exit_pip(self):
        pass

    def _exit_player_fullscreen(self):
        pass


def buttons(w):
    return [c for c in w._det.children() if isinstance(c, QPushButton)]


h = Host()
if h.player is None:
    print("SKIP_NO_PLAYER")
    raise SystemExit(0)
det, player = h._det, h.player
assert player.parent() is det

# Pop out: a mirror child appears in the pop-out window; the player - and with
# it the mpv instance and GL surface - is NEVER reparented out of the pane.
h._toggle_popout()
app.processEvents()
assert h._popout_win is not None
assert player.parent() is det, "the real player must stay docked"
assert getattr(h, "_popout_mirror", None) is not None
assert h._popout_mirror.parent() is h._popout_win
assert player.bar.parent() is not player, "control bar moves to the pop-out"
assert buttons(h) == [], "no placeholder button in the pane (player stays)"

# Pop-out fullscreen toggles must not raise.
h._popout_fs_toggled_at = 0.0
h._toggle_popout_fullscreen()
app.processEvents()
h._popout_fs_toggled_at = 0.0
h._toggle_popout_fullscreen()
app.processEvents()

# Always-on-top toggle (the right-click menu action that replaced PiP's) must
# flip the window flag and persist the choice.
from PyQt6.QtCore import Qt
h._set_popout_on_top(True)
app.processEvents()
assert bool(h._popout_win.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
assert h.settings.value("popout_on_top") == "true"
h._set_popout_on_top(False)
app.processEvents()
assert not bool(h._popout_win.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

# Frameless (no title bar) is the default; the toggle can restore it.
assert bool(h._popout_win.windowFlags() & Qt.WindowType.FramelessWindowHint)
h._set_popout_frameless(False)
app.processEvents()
assert not bool(h._popout_win.windowFlags() & Qt.WindowType.FramelessWindowHint)
assert h.settings.value("popout_frameless") == "false"
h._set_popout_frameless(True)
app.processEvents()
assert bool(h._popout_win.windowFlags() & Qt.WindowType.FramelessWindowHint)

# Auto-hide-controls toggle persists and drives the player flag (default on).
assert h.player._popout_autohide is True
h._set_popout_autohide(False)
assert h.settings.value("popout_autohide") == "false"
assert h.player._popout_autohide is False
h._set_popout_autohide(True)
assert h.player._popout_autohide is True

# A frameless window has no grips, so the video's own edges resize it.
from PyQt6.QtCore import QPoint, QRect
E = Qt.Edge
win = h._popout_win
win.setGeometry(QRect(100, 100, 800, 500))
app.processEvents()
g = win.frameGeometry()
assert not h._popout_resize_edges(g.center())          # middle: plain click
assert h._popout_resize_edges(
    QPoint(g.left() + 1, g.top() + 1)) == (E.LeftEdge | E.TopEdge)
assert h._popout_resize_edges(
    QPoint(g.right() - 1, g.bottom() - 1)) == (E.RightEdge | E.BottomEdge)
assert h._popout_resize_edges(
    QPoint(g.right() - 1, g.center().y())) == E.RightEdge
assert h._popout_resize_edges(
    QPoint(g.center().x(), g.top() + 1)) == E.TopEdge

# With the title bar on, the system frame owns resizing again.
h._set_popout_frameless(False)
app.processEvents()
assert not h._popout_resize_edges(QPoint(g.left() + 1, g.top() + 1))
h._set_popout_frameless(True)
app.processEvents()

# Dragging the bottom-right corner grows the window and leaves the opposite
# corner where it was. Headless there is no window manager to hand the drag
# to, so this exercises the fallback that also runs on macOS.
win.setGeometry(QRect(100, 100, 800, 500))
app.processEvents()
g = QRect(win.geometry())
h._start_popout_resize(E.RightEdge | E.BottomEdge,
                       QPoint(g.right(), g.bottom()))
assert h._popout_resize is not None, "expected the manual resize fallback"
h._drag_popout_resize(QPoint(g.right() + 120, g.bottom() + 60))
app.processEvents()
ng = win.geometry()
assert ng.topLeft() == g.topLeft(), (ng, g)
assert (ng.width(), ng.height()) == (g.width() + 120, g.height() + 60), ng
# It cannot be squeezed below the minimum size.
h._drag_popout_resize(QPoint(g.left(), g.top()))
app.processEvents()
assert win.width() >= h.POPOUT_MIN_W and win.height() >= h.POPOUT_MIN_H
h._popout_resize = None

# Escape while not fullscreen must not dock or raise; it only leaves
# fullscreen, so the window stays detached here.
h._popout_escape()
app.processEvents()
assert h._popout_win is not None

# Dock back in: mirror torn down, bar home, nothing left behind.
h._toggle_popout()
app.processEvents()
assert h._popout_win is None
assert h._popout_mirror is None
assert player.parent() is det
assert player.bar.parent() is player, "control bar back on the player"
assert player._mirror is None
assert buttons(h) == []

# Closing the window docks back instead of destroying anything.
h._toggle_popout()
app.processEvents()
win = h._popout_win
win.close()
app.processEvents()
assert h._popout_win is None
assert player.parent() is det
assert player.bar.parent() is player

print("POPOUT_OK")
"""


def test_popout_window_paths():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD], capture_output=True, text=True,
        env=env, cwd=_REPO_ROOT, timeout=180)
    if "SKIP_NO_PLAYER" in proc.stdout:
        pytest.skip("embedded player unavailable (no libmpv)")
    # Exit status is ignored on purpose: offscreen GL/mpv teardown can abort
    # at interpreter exit after the checks have already run and printed.
    assert "POPOUT_OK" in proc.stdout, (
        f"pop-out checks failed\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-2000:]!r}")


_WIN32_CHILD = r"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget
from dopeiptv.media.embedded import EmbeddedPlayer
from dopeiptv.ui.mw_popout import _PopoutMixin, _use_mirror_popout

# Fake the platform only AFTER every import. Setting it first makes the
# stdlib take its Windows branches on a Linux host - shutil reaches for
# _winapi and the whole child dies before reaching the assertions.
sys.platform = "win32"

app = QApplication.instance() or QApplication([])
assert _use_mirror_popout() is False, "win32 must take the reparent path"


class Host(QMainWindow, _PopoutMixin):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("dopeiptv-test", "popout-win32")
        self.settings.clear()
        self._popout_win = None
        self._popout_placeholder = None
        self._player_fs = False
        self._det = QWidget()
        QVBoxLayout(self._det).setContentsMargins(0, 0, 0, 0)
        self.player = EmbeddedPlayer()
        self._det.layout().addWidget(self.player, 1)
        self.setCentralWidget(self._det)

    def _exit_player_fullscreen(self):
        pass


h = Host()
det, player = h._det, h.player
h._toggle_popout(); app.processEvents()
assert h._popout_win is not None
assert player.parent() is h._popout_win, "the player itself moves to the window"
assert getattr(h, "_popout_mirror", None) is None, "no mirror is built on win32"
assert player._popout_mode is True
h._toggle_popout(); app.processEvents()
assert h._popout_win is None
assert player.parent() is det, "and comes back on dock-in"
assert player._popout_mode is False
print("WIN32_POPOUT_OK")
"""


def test_windows_pops_out_by_reparenting():
    """Windows moves the real player into the pop-out window.

    It was put on the macOS mirror path in 1.2.0 and shipped as "experimental,
    not sufficiently tested" - it wasn't: the GL mirror drew upside down and
    then black, the raster mirror managed one frame and froze. Both work around
    defects Windows does not have. Platform is faked, so this runs anywhere.
    """
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    proc = subprocess.run(
        [sys.executable, "-c", _WIN32_CHILD], capture_output=True, text=True,
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
        cwd=_REPO_ROOT, timeout=180)
    assert "WIN32_POPOUT_OK" in proc.stdout, (
        f"win32 pop-out checks failed\nstdout={proc.stdout!r}\n"
        f"stderr={proc.stderr[-1500:]!r}")
