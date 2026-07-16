"""Reparent-path regression test for the detached ("pop out") player.

The pop-out feature moves the *one* embedded player widget into its own
top-level window and back. If that reparenting is wrong, the shared player
is orphaned or destroyed and playback breaks - so guard the object graph:
after popping out the player belongs to the pop-out window (with a
placeholder left in the detail pane), and after popping in it is back in the
detail pane with its docked mode restored. Closing the pop-out window must
bounce the player home rather than tear it down.

Runs headless (offscreen). The GL render context can't build there, so this
checks the widget graph and mode flags, not on-screen rendering.
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


def _make_window(qapp):
    try:
        from PyQt6.QtCore import QSettings
        from dopeiptv.providers.client import OfflineClient
        from dopeiptv.ui.main_window import MainWindow
    except Exception:
        pytest.skip("Qt/UI not available")
    settings = QSettings("dopeiptv-test", "popout")
    settings.clear()
    w = MainWindow(OfflineClient(), settings)
    if w.player is None:
        pytest.skip("embedded player unavailable (no libmpv) - nothing to detach")
    return w


def test_popout_reparents_player_and_back(qapp):
    w = _make_window(qapp)
    det = w._det
    player = w.player
    assert player.parent() is det

    w._toggle_popout()
    qapp.processEvents()
    assert w._popout_win is not None
    assert player.parent() is w._popout_win
    assert player._popout_mode is True
    assert w._popout_placeholder is not None

    w._toggle_popout()
    qapp.processEvents()
    assert w._popout_win is None
    assert player.parent() is det
    assert player._popout_mode is False
    assert w._popout_placeholder is None


def test_popout_fullscreen_toggle_does_not_raise(qapp):
    w = _make_window(qapp)
    w._toggle_popout()
    qapp.processEvents()
    # The offscreen plugin ignores real fullscreen state; we only assert the
    # toggles run cleanly (the debounce is bypassed by exercising directly).
    w._popout_fs_toggled_at = 0.0
    w._toggle_popout_fullscreen()
    qapp.processEvents()
    w._popout_fs_toggled_at = 0.0
    w._toggle_popout_fullscreen()
    qapp.processEvents()
    w._exit_popout()
    qapp.processEvents()
    assert w._popout_win is None


def test_closing_popout_window_brings_player_home(qapp):
    w = _make_window(qapp)
    det = w._det
    player = w.player
    w._toggle_popout()
    qapp.processEvents()
    win = w._popout_win
    win.close()               # window X: must bounce, not destroy the player
    qapp.processEvents()
    assert w._popout_win is None
    assert player.parent() is det
