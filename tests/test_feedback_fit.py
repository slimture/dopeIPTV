"""The feedback window on a small screen: never taller than the room the
screen has for it, the rest scrolling, Cancel and Send always in sight - and
no taller than it needs when there is little in it."""
from __future__ import annotations

import tempfile

import pytest

_APP: list = []


def _qapp():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
    app = QApplication.instance() or QApplication([])
    _APP[:] = [app]                       # kept: a QApplication let go takes Qt down
    return app


def _settle(app) -> None:
    for _ in range(3):
        app.processEvents()


def _send_in_sight(fb) -> bool:
    from PyQt6.QtCore import QPoint
    top = fb.send_btn.mapTo(fb, QPoint(0, 0)).y()
    return fb.send_btn.isVisible() and 0 <= top and top + fb.send_btn.height() <= fb.height()


def test_a_full_feedback_window_fits_the_screen_and_scrolls():
    app = _qapp()
    from dopeiptv.ui import dialogs
    tickets = [{"c": f"c{n}", "t": 1763400000 + n, "w": f"Report {n}",
                "r": [{"code": "thanks", "t": 1763400000 + n}]} for n in range(8)]
    fb = dialogs.FeedbackDialog(None, lambda opts: "line\n" * 60, answers=tickets)
    fb.show()
    _settle(app)
    fb.topic_chips["bug"].setChecked(True)
    fb._toggle_preview()
    _settle(app)
    room = fb._room()
    assert room is not None
    assert fb.height() <= int(room.height() * fb.SCREEN_SHARE)
    assert fb._scroll.verticalScrollBar().maximum() > 0, "what does not fit scrolls"
    assert _send_in_sight(fb)
    # It can be made shorter still; it never insists on all of its content.
    assert fb.minimumHeight() <= fb.SHORTEST
    fb.resize(fb.width(), fb.SHORTEST)
    _settle(app)
    assert _send_in_sight(fb)
    fb.close()


def test_a_short_feedback_window_is_as_tall_as_it_needs():
    app = _qapp()
    from dopeiptv.ui import dialogs
    fb = dialogs.FeedbackDialog(None, lambda opts: "")
    fb.show()
    _settle(app)
    room = fb._room()
    assert room is not None
    if fb.height() < int(room.height() * fb.SCREEN_SHARE):
        assert fb._scroll.verticalScrollBar().maximum() == 0, "no scrolling while it fits"
    assert _send_in_sight(fb)
    fb.close()
