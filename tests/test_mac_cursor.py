"""The pointer hidden over the video on macOS (core/platform_macos.py,
media/embedded._MacInputFilter): the override cursor alone is a cursor rect
on Qt's own view, and over the picture's own native view (the video drawn on
its own thread) AppKit put the arrow back - so AppKit is asked as well,
hidden until the mouse moves. A move Qt sends while the pointer rests where
it was hidden does not bring it back; a real one, or a click, does."""
from __future__ import annotations

import sys
import types

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QCursor, QMouseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from dopeiptv.core import platform_macos  # noqa: E402

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def appkit(monkeypatch):
    calls: list[bool] = []

    class NSCursor:
        @staticmethod
        def setHiddenUntilMouseMoves_(flag):
            calls.append(flag)

    monkeypatch.setitem(sys.modules, "AppKit", types.SimpleNamespace(NSCursor=NSCursor))
    monkeypatch.setattr(platform_macos, "_cursor_hidden", False)
    monkeypatch.setattr(platform_macos, "_hidden_at", None)
    yield calls
    platform_macos.set_cursor_hidden(False)


def test_hidden_through_appkit_as_well_and_shown_again(appkit):
    platform_macos.set_cursor_hidden(True)
    assert appkit == [True]
    assert QApplication.overrideCursor().shape() == Qt.CursorShape.BlankCursor
    platform_macos.set_cursor_hidden(True)            # balanced: once
    assert appkit == [True]
    platform_macos.set_cursor_hidden(False)
    assert appkit == [True, False]
    assert QApplication.overrideCursor() is None


def test_a_move_where_the_pointer_rests_is_no_move(appkit):
    assert platform_macos.cursor_moved(1, 2)          # nothing hidden: every move counts
    platform_macos.set_cursor_hidden(True)
    at = QCursor.pos()
    assert not platform_macos.cursor_moved(at.x(), at.y())
    assert platform_macos.cursor_moved(at.x() + 3, at.y())


def test_no_pyobjc_leaves_the_override_cursor(monkeypatch):
    monkeypatch.setitem(sys.modules, "AppKit", None)  # import fails
    monkeypatch.setattr(platform_macos, "_cursor_hidden", False)
    platform_macos.set_cursor_hidden(True)
    assert QApplication.overrideCursor().shape() == Qt.CursorShape.BlankCursor
    platform_macos.set_cursor_hidden(False)
    assert QApplication.overrideCursor() is None


def test_the_filter_brings_it_back_only_for_a_real_move_or_a_click(appkit, monkeypatch):
    from dopeiptv.media import embedded

    f = embedded._MacInputFilter.__new__(embedded._MacInputFilter)
    monkeypatch.setattr(embedded._MacInputFilter, "_IS_MAC", True)
    monkeypatch.setattr(embedded._MacInputFilter, "_over_video", lambda self: False)
    f._cursor_timer = types.SimpleNamespace(start=lambda: None, stop=lambda: None)

    def mouse(kind, pos):
        return QMouseEvent(kind, QPointF(0, 0), QPointF(pos), Qt.MouseButton.NoButton,
                           Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)

    platform_macos.set_cursor_hidden(True)
    at = QCursor.pos()
    f.eventFilter(None, mouse(QEvent.Type.MouseMove, at))
    assert platform_macos._cursor_hidden and appkit == [True]
    f.eventFilter(None, mouse(QEvent.Type.MouseMove, at + QPoint(4, 0)))
    assert not platform_macos._cursor_hidden and appkit == [True, False]
    platform_macos.set_cursor_hidden(True)
    f.eventFilter(None, mouse(QEvent.Type.MouseButtonPress, QCursor.pos()))
    assert not platform_macos._cursor_hidden
