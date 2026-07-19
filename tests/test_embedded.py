"""Construction smoke test for the embedded player.

Regression guard: eventFilter() reads self._fs_ui / self._popout_mode, and
events can be delivered to the filtered widgets (font/style changes on the
control bar) while __init__ is still running. If those flags are not set
up front, building the player raises AttributeError and the whole window
fails to open.

Subprocess pattern (see test_multiview / test_home): each EmbeddedPlayer owns
a QOpenGLWidget, and tearing it down under the offscreen platform can segfault
the interpreter at shutdown. Running it in a child and asserting on a printed
marker (not the return code) keeps a teardown crash from failing the run - the
assertions have already passed and printed OK by the time any crash happens.
"""
import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication

from dopeiptv.media.embedded import EmbeddedPlayer

app = QApplication.instance() or QApplication([])

player = EmbeddedPlayer()
# The event-filter guard flags must exist immediately after __init__.
assert player._fs_ui is False
assert player._popout_mode is False
# Force pending events (font/style changes) through the filter; this is
# what triggered the original AttributeError.
app.processEvents()

# Centre play/pause button: hidden with no stream, shown while paused, and a
# double-click cancels the pending single-click pause. (isHidden, not
# isVisible: the player widget itself is never shown in this offscreen run.)
player._reveal_center()
assert player.center_btn.isHidden()
player.current_url = "http://x/stream.ts"
player._paused = True
player._reveal_center()
assert not player.center_btn.isHidden()
player._click_timer.start()
player._on_video_dbl_click()
assert not player._click_timer.isActive()
assert player._ignore_next_release is True

print("EMBEDDED_OK")
"""


def test_embedded_player_constructs():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD], capture_output=True, text=True,
        env=env, cwd=_REPO_ROOT, timeout=180)
    # Assert on the marker, not the return code: an offscreen-GL teardown
    # segfault after the checks passed must not fail the test.
    assert "EMBEDDED_OK" in proc.stdout, (
        f"embedded checks failed\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-2000:]!r}")
