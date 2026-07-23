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

# The GL video widget must stay CHILD-FREE: a widget on top of a
# QOpenGLWidget forces Qt/macOS onto the render-to-texture composition
# path, which goes stale (frozen picture) when the player is reparented
# into the pop-out window - "stats for nerds" was the reproducible trigger.
# The stats and black-cover overlays are the player's children, drawn over
# the video as siblings.
from PyQt6.QtWidgets import QWidget as _QW
child_widgets = [c for c in vid.children() if isinstance(c, _QW)]
assert child_widgets == [], f"video widget must have no child widgets: {child_widgets}"
assert player._stats_overlay.parent() is player, "stats overlay is the player's child"
assert player._blackout.parent() is player, "black cover is the player's child"

# macOS mirror pop-out wiring: start_mirror builds a MIRROR surface bound to
# the docked video (renders its render context, owns no mpv/ctx of its own),
# covers the docked video with a placeholder, and routes frame updates;
# stop_mirror tears it all down. The docked mpv/render context are never
# touched - the whole point is that the real GL surface is not reparented.
host = _QW()
mirror = player.start_mirror(host)
assert mirror._mirror_of is player.video, "mirror renders the docked video ctx"
assert mirror.mpv is None and mirror._ctx is None, "mirror owns no mpv/ctx"
assert player._mirror is mirror
assert player._dock_ph is not None and not player._dock_ph.isHidden(), \
    "docked video is covered while mirrored"
assert player.video.mpv is sentinel_mpv, "docked mpv untouched by start_mirror"
# The floating overlays follow the mirror into the pop-out window and anchor
# to it; the docked GL surface itself never moves.
assert player._ov_surface is mirror, "overlays anchor to the mirror"
assert player.seek_overlay.parent() is host, "seek bar moved to the pop-out"
assert player.ts_timeline.parent() is host, "timeshift timeline moved"
assert player._stats_overlay.parent() is host, "stats moved"
assert player.video.parent() is player, "the GL surface is NOT reparented"
# Auto-hide: the mirror path counts as a pop-out context, so the control bar
# fades on idle and returns on a mirror hover (the guards used to key off
# _popout_mode, which the mirror path never sets - the bar stayed pinned).
assert player._in_popout() is True, "mirror is a pop-out context"
player._fs_ui = False               # left True by the fullscreen checks above
player.set_popout_autohide(True)
player._hide_popout_bar()
assert player.bar.isHidden(), "auto-hide must fade the bar while mirrored"
player.reveal_pop_overlays()
assert not player.bar.isHidden(), "a mirror hover brings the bar back"
player.stop_mirror()
assert player._mirror is None
assert player._in_popout() is False, "docked again: not a pop-out context"
assert player._ov_surface is player.video, "overlays anchor back to the video"
assert player.seek_overlay.parent() is player, "seek bar back on the player"
assert player.ts_timeline.parent() is player and \
    player._stats_overlay.parent() is player, "overlays back on the player"
assert player._dock_ph.isHidden(), "placeholder lifts on dock-back"
assert player.video.mpv is sentinel_mpv, "docked mpv still untouched"

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
