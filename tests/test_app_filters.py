"""The two filters every event of the app goes through, kept cheap: each
is a call into Python for each of them - some forty thousand while the
Settings window is built, and every frame's events while a video plays.

- The player's (seek keys; on macOS the cursor over the video) lets an
  event that is no key - or, on macOS, mouse - one through without asking
  its type; a key still reaches the seek keys.
- The dopeiptv:// link filter is there only on macOS, the one system that
  hands a running app a link as an event.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QObject, Qt, QUrl  # noqa: E402
from PyQt6.QtGui import QKeyEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])


class _NoType(QEvent):
    """An event that must not be asked its type."""

    def type(self):                                      # noqa: A003
        raise AssertionError("asked")


def test_the_players_app_filter_skips_what_is_no_key(monkeypatch):
    from dopeiptv.media import embedded
    host = QObject()
    filt = embedded._MacInputFilter(host)
    try:
        seen = []
        monkeypatch.setattr(filt, "_handle_seek_key",
                            lambda ev, pressed: seen.append((ev.key(), pressed)) or True)
        assert filt.eventFilter(host, _NoType(QEvent.Type.User)) is False
        press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left,
                          Qt.KeyboardModifier.NoModifier)
        release = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Left,
                            Qt.KeyboardModifier.NoModifier)
        override = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Left,
                             Qt.KeyboardModifier.NoModifier)
        assert filt.eventFilter(host, press) is True
        assert filt.eventFilter(host, release) is True
        assert filt.eventFilter(host, override) is False
        assert seen == [(Qt.Key.Key_Left, True), (Qt.Key.Key_Left, False)]
        monkeypatch.setattr(filt, "_handle_seek_key", lambda ev, pressed: False)
        assert filt.eventFilter(host, press) is False
    finally:
        app.removeEventFilter(filt)


def test_link_events_are_watched_on_macos_only(monkeypatch):
    from dopeiptv import app as app_mod

    class Window(QObject):
        def __init__(self) -> None:
            super().__init__()
            self.commands = []

        def handle_command(self, cmd) -> None:
            self.commands.append(cmd)

    monkeypatch.setattr(app_mod.sys, "platform", "linux")
    w = Window()
    app_mod._watch_link_events(app, w)
    assert getattr(w, "_link_filter", None) is None
    # macOS: its file-open events (PyQt makes none from Python: a stand-in
    # for the class, which the filter tells them by).
    class FileOpen:
        def __init__(self, link: str) -> None:
            self._url = QUrl(link)

        def url(self) -> QUrl:
            return self._url

        def file(self) -> str:
            return self._url.toLocalFile()

    import PyQt6.QtGui
    monkeypatch.setattr(PyQt6.QtGui, "QFileOpenEvent", FileOpen)
    monkeypatch.setattr(app_mod.sys, "platform", "darwin")
    w = Window()
    app_mod._watch_link_events(app, w)
    monkeypatch.undo()
    try:
        f = w._link_filter
        assert f.eventFilter(w, _NoType(QEvent.Type.User)) is False
        assert f.eventFilter(w, FileOpen("dopeiptv://channel?name=SVT1")) is True
        # A link: asked about before it tunes (core/commands, "link").
        assert w.commands == [{"channel": "SVT1", "link": True}]
        assert f.eventFilter(w, FileOpen("file:///tmp/x.m3u")) is False
    finally:
        app.removeEventFilter(w._link_filter)
