"""Leaving full screen - the video's, or TV mode's - puts the window back
where it was (ui/main_window.py _restore_normal_geometry).

On X11 the geometry set right after showNormal() raced the window manager
taking the window out of full screen: with mutter the window crept down and
shrank by its title bar each time. On Wayland (weston) it shrank by Qt's own
decorations even when left alone. Now it is set once the window manager has
let go - and only where it is needed; elsewhere at once, as before.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, QRect, QSize  # noqa: E402

_APP = None


def _app():
    from PyQt6.QtWidgets import QApplication
    global _APP
    _APP = QApplication.instance() or QApplication([])   # kept alive
    return _APP


class _Screen:
    def geometry(self):
        return QRect(0, 0, 1920, 1080)


class _Win(QObject):
    """The pieces of MainWindow the geometry put back uses."""

    def __init__(self):
        from dopeiptv.ui.main_window import MainWindow
        _app()
        super().__init__()
        self.size_now = QSize(1920, 1080)
        self.geo_now = QRect(0, 0, 1920, 1080)
        self.set_to: list[QRect] = []
        self.done = 0
        for name in ("_restore_normal_geometry", "_after_fullscreen_left", "_settle_tick",
                     "_settle_cancel"):
            setattr(self, name, getattr(MainWindow, name).__get__(self))

    def screen(self):
        return _Screen()

    def size(self):
        return self.size_now

    def geometry(self):
        return self.geo_now

    def isFullScreen(self):  # noqa: N802 - Qt's name
        return False

    def isMaximized(self):  # noqa: N802 - Qt's name
        return False

    def setGeometry(self, geo):  # noqa: N802 - Qt's name
        self.set_to.append(QRect(geo))
        self.geo_now = QRect(geo)

    def then(self):
        self.done += 1


def _platform(monkeypatch, name):
    from dopeiptv.ui import main_window as mw
    monkeypatch.setattr(mw.QGuiApplication, "platformName", staticmethod(lambda: name))


def test_on_x11_and_wayland_the_geometry_waits_for_the_window_manager(monkeypatch):
    for platform in ("xcb", "wayland"):
        _platform(monkeypatch, platform)
        w = _Win()
        want = QRect(100, 117, 1280, 800)
        w._restore_normal_geometry(want, then=w.then)
        assert w.set_to == [] and w.done == 0           # never at once: it raced the WM
        w._settle_tick()
        assert w.set_to == []                           # still full screen
        # The window manager lets go - with the title bar counted in (mutter).
        w.size_now, w.geo_now = QSize(1280, 763), QRect(100, 154, 1280, 763)
        w._settle_tick()
        assert w.set_to == []                           # once is not settled yet
        w._settle_tick()
        assert w.set_to == [want] and w.done == 1, platform
        w._settle_tick()
        assert w.set_to == [want] and w.done == 1       # one job, done once
        w._settle_cancel()


def test_a_window_manager_that_puts_it_back_itself_is_left_alone(monkeypatch):
    _platform(monkeypatch, "xcb")
    w = _Win()
    want = QRect(100, 117, 1280, 800)
    w._restore_normal_geometry(want, then=w.then)
    w.size_now, w.geo_now = QSize(1280, 800), QRect(want)
    w._settle_tick()
    w._settle_tick()
    assert w.set_to == [] and w.done == 1
    w._settle_cancel()


def test_a_newer_restore_keeps_what_an_earlier_one_was_to_run(monkeypatch):
    """TV mode left, then the video's full screen in and out at once: its
    geometry wins, and TV mode's put-back still runs - lost, the layout was
    never saved again and the sidebar never folded by itself again."""
    _platform(monkeypatch, "xcb")
    w = _Win()
    w._restore_normal_geometry(QRect(100, 117, 1280, 800), then=w.then)
    newer = QRect(50, 60, 1000, 700)
    w._restore_normal_geometry(newer)
    w.size_now, w.geo_now = QSize(1000, 663), QRect(50, 97, 1000, 663)
    w._settle_tick()
    w._settle_tick()
    assert w.set_to == [newer] and w.done == 1
    w._settle_cancel()


def test_elsewhere_the_geometry_is_set_at_once_as_before(monkeypatch):
    for platform in ("windows", "cocoa", "offscreen"):
        _platform(monkeypatch, platform)
        w = _Win()
        want = QRect(10, 20, 800, 600)
        w._restore_normal_geometry(want)
        assert w.set_to == [want], platform
        w._settle_cancel()
