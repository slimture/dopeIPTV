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
