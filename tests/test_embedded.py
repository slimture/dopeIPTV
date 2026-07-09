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
    player = EmbeddedPlayer()
    # The event-filter guard flags must exist immediately after __init__.
    assert player._fs_ui is False
    assert player._pip_mode is False
    # Force pending events (font/style changes) through the filter; this is
    # what triggered the original AttributeError.
    qapp.processEvents()
