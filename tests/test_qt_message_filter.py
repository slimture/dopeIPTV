"""Qt's harmless platform chatter stays out of the log; real warnings do not."""

from __future__ import annotations

import pytest

from dopeiptv import app as app_module


@pytest.fixture
def passed_on(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(app_module, "_original_msg_handler",
                        lambda _type, _context, message: seen.append(message))
    return seen


@pytest.mark.parametrize("message", [
    "virtual void QtWaylandClient::QWaylandTextInputv3::disableSurface"
    "(wl_surface*) Trying to disable 0x555f53c06a80 but 0x0 is focused",
    "Got leave event for surface 0x0 but window is already unset",
])
def test_wayland_noise_is_dropped(passed_on, message):
    app_module._qt_message_filter(None, None, message)
    assert passed_on == []


def test_any_other_warning_is_passed_on(passed_on):
    app_module._qt_message_filter(None, None, "QOpenGLWidget: Failed to create context")
    assert passed_on == ["QOpenGLWidget: Failed to create context"]


def test_qts_last_words_go_into_the_crash_file_before_it_aborts(passed_on, monkeypatch):
    """A fatal error at start - no display to connect to - left only "Fatal
    Python error: Aborted" in the crash file the next start sends; the
    reason Qt gave, and the warning before it, went to no terminal."""
    import io
    from types import SimpleNamespace

    from PyQt6.QtCore import QtMsgType
    crash = io.StringIO()
    monkeypatch.setattr(app_module, "_crash_target", crash)
    monkeypatch.setattr(app_module, "_QT_LAST_WORDS", [])
    xcb = SimpleNamespace(category="qt.qpa.xcb")
    plain = SimpleNamespace(category="default")
    app_module._qt_message_filter(QtMsgType.QtWarningMsg, xcb, "could not connect to display :0")
    assert crash.getvalue() == ""                        # a warning alone writes nothing
    app_module._qt_message_filter(
        QtMsgType.QtFatalMsg, plain,
        "This application failed to start because no Qt platform plugin could be initialized.")
    assert crash.getvalue() == (
        "Qt: qt.qpa.xcb: could not connect to display :0\n"
        "Qt: This application failed to start because no Qt platform plugin could be "
        "initialized.\n")
    assert passed_on[-1].startswith("This application failed")   # still handed on


def test_qts_last_words_are_masked_and_a_terminal_gets_none(passed_on, monkeypatch):
    import io
    import sys
    from types import SimpleNamespace

    from PyQt6.QtCore import QtMsgType
    crash = io.StringIO()
    monkeypatch.setattr(app_module, "_crash_target", crash)
    monkeypatch.setattr(app_module, "_QT_LAST_WORDS", [])
    app_module._qt_message_filter(QtMsgType.QtFatalMsg, SimpleNamespace(category=""),
                                  "cannot open http://tv.example/get.php?username=k&password=secret9")
    assert "secret9" not in crash.getvalue() and crash.getvalue().startswith("Qt: ")
    monkeypatch.setattr(app_module, "_crash_target", sys.stderr)
    app_module._keep_qt_words(QtMsgType.QtFatalMsg, None, "to a terminal: Qt prints it itself")
