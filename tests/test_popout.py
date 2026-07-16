"""Reparent-path regression test for the detached ("pop out") player.

The pop-out feature moves the *one* embedded player widget into its own
top-level window and back. If that reparenting is wrong the shared player is
orphaned or destroyed and playback breaks, so guard the object graph:

- popping out reparents the player into the pop-out window and leaves a
  placeholder in the detail pane;
- popping in reparents it back and detaches the placeholder *synchronously*
  (a leftover placeholder child crashed PiP entry, which scans the detail
  pane's children - "wrapped C/C++ object ... has been deleted");
- closing the pop-out window bounces the player home rather than destroying
  it.

The assertions run in a subprocess. Reparenting a QOpenGLWidget between
top-level windows on the offscreen platform leaves GL/mpv state that aborts
at Qt's interpreter-exit teardown (the app itself only sidesteps this via
os._exit). That teardown abort is not a product fault, so we assert on the
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

# Reparent out: player moves to the pop-out window, placeholder into the pane.
h._toggle_popout()
app.processEvents()
assert h._popout_win is not None
assert player.parent() is h._popout_win
assert player._popout_mode is True
assert h._popout_placeholder is not None
assert len(buttons(h)) == 1

# Pop-out fullscreen toggles must not raise.
h._popout_fs_toggled_at = 0.0
h._toggle_popout_fullscreen()
app.processEvents()
h._popout_fs_toggled_at = 0.0
h._toggle_popout_fullscreen()
app.processEvents()

# Reparent back in: player home, placeholder fully detached (the crash guard).
h._toggle_popout()
app.processEvents()
assert h._popout_win is None
assert player.parent() is det
assert player._popout_mode is False
assert h._popout_placeholder is None
assert buttons(h) == []

# Closing the window bounces the player home instead of destroying it.
h._toggle_popout()
app.processEvents()
win = h._popout_win
win.close()
app.processEvents()
assert h._popout_win is None
assert player.parent() is det

print("POPOUT_OK")
"""


def test_popout_reparent_paths():
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
    # Exit status is ignored on purpose: an offscreen QOpenGLWidget reparent
    # aborts at Qt teardown after the checks have already run and printed.
    assert "POPOUT_OK" in proc.stdout, (
        f"pop-out reparent checks failed\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-2000:]!r}")
