"""Construction smoke test for the embedded player.

Regression guard: eventFilter() reads self._fs_ui / self._pip_mode, and
events can be delivered to the filtered widgets (font/style changes on the
control bar) while __init__ is still running. If those flags are not set
up front, building the player raises AttributeError and the whole window
fails to open.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception:
        pytest.skip("PyQt6 not available")
    app = QApplication.instance() or QApplication([])
    yield app


def test_embedded_player_constructs(qapp):
    try:
        from dopeiptv.media.embedded import EmbeddedPlayer
    except Exception:
        pytest.skip("Qt/OpenGL not available")
    # One player per process: each EmbeddedPlayer owns a QOpenGLWidget, and
    # tearing several down under the offscreen platform can segfault the whole
    # run on CI - so the centre-button checks piggyback on this single instance
    # rather than building a second player.
    player = EmbeddedPlayer()
    # The event-filter guard flags must exist immediately after __init__.
    assert player._fs_ui is False
    assert player._popout_mode is False
    # Force pending events (font/style changes) through the filter; this is
    # what triggered the original AttributeError.
    qapp.processEvents()

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
