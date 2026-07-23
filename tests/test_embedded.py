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

# Fullscreen idle-hide must never blank the cursor (or pull the controls)
# under an open menu - the subtitle/audio pickers and the right-click menu
# overlap the video, and hiding mid-choice stranded the user without a
# pointer until they clicked.
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QMenu

player._fs_ui = True
menu = QMenu()
menu.addAction("Subtitles")
menu.popup(QPoint(10, 10))
app.processEvents()
assert QApplication.activePopupWidget() is menu
player._hide_fs_ui()
assert player.cursor().shape() != Qt.CursorShape.BlankCursor
assert player._overlay_timer.isActive()   # re-armed, not given up
menu.close()
app.processEvents()
player._hide_fs_ui()
assert player.cursor().shape() == Qt.CursorShape.BlankCursor

# Render-context lifecycle: reparenting the player (docking in/out of the
# pop-out window) recreates the GL context, which the video widget handles by
# freeing and rebuilding ONLY the mpv render context. Freeing it must NEVER
# touch the mpv instance - that is what keeps the stream and audio alive across
# a pop-out toggle (the "audio but frozen video" bug).
class _FakeCtx:
    freed = False

    def free(self):
        _FakeCtx.freed = True

vid = player.video
sentinel_mpv = object()
vid.mpv = sentinel_mpv
vid._ctx = _FakeCtx()
vid._free_render_context()
assert vid._ctx is None, "render context must be cleared"
assert _FakeCtx.freed is True, "old render context must be freed"
assert vid.mpv is sentinel_mpv, "mpv instance must be untouched (audio lives)"

# The post-reparent settle (geometry re-lock + framebuffer nudge) must be
# pure widget work: it must never touch the mpv instance or the render
# context - freeing/recreating the render context outside the paint cycle
# is what wedged libmpv into a black-for-the-session state.
vid._ctx = _FakeCtx()
_FakeCtx.freed = False
player._settle_after_reparent()
assert vid.mpv is sentinel_mpv, "settle must not touch mpv"
assert vid._ctx is not None and _FakeCtx.freed is False, \
    "settle must not free/recreate the render context"

# The fullscreen-transition cover (macOS animated fullscreen) is pure
# chrome: it shows, tracks resizes, uncovers via its timers or _end_fs_cover,
# and never touches mpv or the render context. force=True bypasses the
# darwin gate so the wiring is exercised on any platform.
player.begin_fs_transition_cover(force=True)
assert not player._fs_cover.isHidden(), "cover must be up"
assert player._fs_cover_fail.isActive(), "failsafe must be armed"
player.resize(player.width() + 4, player.height())   # animation resize
assert not player._fs_cover.isHidden(), "cover survives the resize stream"
player._end_fs_cover()
assert player._fs_cover.isHidden(), "cover must lift"
assert not player._fs_cover_fail.isActive(), "timers stopped with the cover"
assert vid.mpv is sentinel_mpv and vid._ctx is not None \
    and _FakeCtx.freed is False, "cover must never touch mpv/render context"

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
