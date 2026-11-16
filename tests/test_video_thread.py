"""The picture drawn on a thread of its own (media/video_thread.py) and the
switch and the fallback around it (core/video_mode.py). No GL here: what
the window's thread does - which surface is made and why, what sits over
the picture and what mpv is handed for it, the window-thread surface put in
place of one that cannot draw - and how that stands, as the user and the
error report see it. The drawing itself is checked with the real player
(not in CI: it needs a GL stack)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from dopeiptv.core import video_mode
from dopeiptv.media import video_surface, video_thread

app = QApplication.instance() or QApplication([])


class _Settings:
    def __init__(self, **values):
        self.values = values

    def value(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv(video_mode.ENV, raising=False)
    video_mode.reset()
    yield
    video_mode.reset()


# -- the switch -----------------------------------------------------------------

def test_on_by_default():
    assert video_mode.wanted(_Settings()) == (True, "")


def test_the_setting_switches_it_off_and_a_cell_follows():
    want, why = video_mode.wanted(_Settings(video_own_thread="false"))
    assert not want and why == "switched off in Settings"
    # A multiview cell has no settings of its own: what was read last.
    assert video_mode.wanted(None) == (False, "switched off in Settings")


def test_the_environment_wins_either_way(monkeypatch):
    monkeypatch.setenv(video_mode.ENV, "0")
    assert video_mode.wanted(_Settings())[0] is False
    monkeypatch.setenv(video_mode.ENV, "1")
    assert video_mode.wanted(_Settings(video_own_thread="false")) == (True, "")


def test_the_report_line_says_how_and_why():
    assert video_mode.line() == "Video: not started yet"
    video_mode.note(video_mode.THREAD)
    assert video_mode.line() == "Video: own thread"
    video_mode.note_failed("no OpenGL on other threads")
    assert video_mode.line() == "Video: window thread (fallback: no OpenGL on other threads)"
    assert video_mode.failed() == "no OpenGL on other threads"


def test_the_report_carries_the_line():
    from dopeiptv.core import report
    video_mode.note(video_mode.THREAD)
    assert "Video: own thread" in report.system_lines()


def test_nerd_diagnostics_says_it_in_words():
    from dopeiptv.ui.nerd_settings import diagnostics, video_path_text
    from dopeiptv.i18n import tr
    video_mode.note(video_mode.THREAD)
    assert video_path_text() == tr("video_path_thread")
    video_mode.note(video_mode.WINDOW, "switched off in Settings")
    assert video_path_text() == tr("video_path_off")
    video_mode.note_failed("mpv render context: boom")
    assert video_path_text() == tr("video_path_fallback", reason="mpv render context: boom")
    assert "Video: window thread (fallback: mpv render context: boom)" in diagnostics(_Settings())


# -- which surface ------------------------------------------------------------------

def test_switched_off_makes_the_window_thread_surface(monkeypatch):
    monkeypatch.setenv(video_mode.ENV, "0")
    host = QWidget()
    s = video_thread.make_video_surface(host)
    assert type(s) is video_surface._MpvGLWidget
    assert video_mode.mode() == video_mode.WINDOW


def test_a_platform_without_threaded_gl_falls_back_and_says_why():
    # CI runs offscreen: no GL on other threads - the old surface, logged.
    host = QWidget()
    s = video_thread.make_video_surface(host, _Settings())
    assert type(s) is video_surface._MpvGLWidget
    assert video_mode.line().startswith("Video: window thread (fallback: ")


def test_can_start_says_why_not(monkeypatch):
    monkeypatch.setattr(video_thread, "_libmpv", None)
    assert video_thread.can_start() == "libmpv not loaded"


def test_after_a_failure_every_surface_uses_the_window_thread(monkeypatch):
    monkeypatch.setattr(video_thread.QGuiApplication, "platformName", staticmethod(lambda: "xcb"))
    monkeypatch.setattr(video_thread.QOpenGLContext, "supportsThreadedOpenGL",
                        staticmethod(lambda: True))
    monkeypatch.setattr(video_thread, "_libmpv", object())
    assert video_thread.can_start() == ""
    video_mode.note_failed("no frame could be shown")
    assert video_thread.can_start() == "no frame could be shown"


# -- the fallback swap ------------------------------------------------------------------

def test_the_window_thread_surface_takes_the_place_of_one_that_could_not_draw():
    host = QWidget()
    lay = QVBoxLayout(host)
    above = QLabel("above")
    lay.addWidget(above)
    old = video_thread.ThreadedVideoSurface(host)
    old.variant_picker = True
    old.hwdec_pref = "auto-safe"
    label = QLabel("cell title", old)          # multiview's labels are children
    lay.addWidget(old, 1)
    below = QLabel("below")
    lay.addWidget(below)
    host.show()
    wired = []
    new = video_thread.swap_for_fallback(old, wired.append)
    assert type(new) is video_surface._MpvGLWidget
    assert wired == [new]
    assert lay.indexOf(new) == 1 and lay.indexOf(old) == -1
    assert label.parent() is new
    assert new.variant_picker is True and new.hwdec_pref == "auto-safe"
    old.shutdown()


# -- what sits over the picture --------------------------------------------------------

class _Core:
    def __init__(self):
        self.calls = []

    def command(self, *args):
        self.calls.append(args)


class _Surface(QWidget):
    """The bits of a surface the overlay layer reads: its mpv core, its
    native box (left out of the overlays) and its geometry."""

    def __init__(self, parent):
        super().__init__(parent)
        self.mpv = _Core()
        self._box = QWidget(self)
        self._box.setGeometry(0, 0, 320, 180)


def _setup():
    host = QWidget()
    host.resize(400, 400)
    surface = _Surface(host)
    surface.setGeometry(0, 0, 320, 180)
    under = QLabel("under the picture, not an overlay", host)   # before: below it
    under.setGeometry(0, 0, 50, 20)
    under.lower()
    stats = QLabel("stats", host)
    stats.setGeometry(10, 10, 100, 40)
    stats.setStyleSheet("background: rgba(0,0,0,150); color: white;")
    bar = QLabel("control bar, below the picture", host)
    bar.setGeometry(0, 200, 320, 30)
    badge = QLabel("LIVE", surface)                              # a child of the surface
    badge.setGeometry(280, 5, 30, 16)
    host.show()
    layer = video_thread._Overlays(surface)
    return host, surface, layer, stats, bar, badge, under


def _adds(core):
    return [c for c in core.calls if c[0] == "overlay-add"]


def test_overlays_over_the_picture_go_to_mpv_in_their_order():
    host, surface, layer, stats, bar, badge, under = _setup()
    layer._flush()
    adds = _adds(surface.mpv)
    assert [a[1] for a in adds] == [0, 1]
    # The surface's own child first, then the sibling above it; position and
    # size in pixels, premultiplied BGRA from memory.
    assert adds[0][2:4] == (280, 5) and adds[0][7:9] == (30, 16)
    assert adds[1][2:4] == (10, 10) and adds[1][7:9] == (100, 40)
    assert all(a[4].startswith("&") and a[6] == "bgra" for a in adds)
    layer.stop()


def test_a_hidden_overlay_is_taken_off_and_a_repaint_redraws_only_it():
    host, surface, layer, stats, bar, badge, under = _setup()
    layer._flush()
    surface.mpv.calls.clear()
    layer._dirty.add(1)
    layer._flush()
    assert [a[1] for a in _adds(surface.mpv)] == [1]
    surface.mpv.calls.clear()
    stats.hide()
    layer._full = True
    layer._flush()
    assert ("overlay-remove", 1) in surface.mpv.calls
    layer.stop()


def test_its_own_drawing_does_not_ask_for_another():
    host, surface, layer, stats, bar, badge, under = _setup()
    layer._flush()
    layer._dirty.clear()
    layer._full = False
    layer._timer.stop()
    layer._add(surface.mpv, 1, stats, (10, 10, 100, 40))
    assert not layer._dirty and not layer._full
    layer.stop()


def test_the_overlays_follow_into_the_pop_out():
    host, surface, layer, stats, bar, badge, under = _setup()
    pop = QWidget()
    pop.resize(500, 300)
    mirror = _Surface(pop)
    mirror.setGeometry(0, 0, 480, 270)
    moved = QLabel("DVR row", pop)
    moved.setGeometry(20, 230, 300, 30)
    pop.show()
    layer.place_over(mirror)
    surface.mpv.calls.clear()
    layer._flush()
    adds = _adds(surface.mpv)
    assert len(adds) == 1 and adds[0][2:4] == (20, 230)
    layer.stop()


def test_a_renderer_that_is_not_running_lets_go_at_once():
    from PyQt6.QtGui import QWindow
    r = video_thread._Renderer(None, None, None, "t", video_thread._Told())
    assert r.release(QWindow()) is True
