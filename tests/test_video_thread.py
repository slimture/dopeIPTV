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


def test_forced_tries_where_qt_says_no_and_keeps_the_other_checks(monkeypatch):
    """DOPEIPTV_VIDEO_THREAD=force (to test macOS, where Qt says no): Qt's
    "no OpenGL on other threads" is passed over - nothing else is."""
    monkeypatch.setattr(video_thread.QGuiApplication, "platformName", staticmethod(lambda: "cocoa"))
    monkeypatch.setattr(video_thread.QOpenGLContext, "supportsThreadedOpenGL",
                        staticmethod(lambda: False))
    monkeypatch.setattr(video_thread, "_libmpv", object())
    assert video_thread.can_start() == "cocoa: no OpenGL on other threads"
    monkeypatch.setenv(video_mode.ENV, "force")
    assert video_mode.forced() and video_mode.wanted(_Settings()) == (True, "")
    assert video_thread.can_start() == ""
    video_mode.note(video_mode.THREAD)
    assert video_mode.line() == "Video: own thread (forced)"
    video_mode.note_failed("no frame could be shown")
    assert video_thread.can_start() == "no frame could be shown"
    monkeypatch.setattr(video_thread, "_libmpv", None)
    assert video_thread.can_start() == "libmpv not loaded"


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


# -- widgets over the picture come and go (1.6.1: deleted buttons) --------------------

class _Hook:
    """sys.excepthook while a test runs: what an event filter raised."""

    def __init__(self, monkeypatch):
        import sys
        self.seen = []
        monkeypatch.setattr(sys, "excepthook", lambda *a: self.seen.append(a[1]))


def _deferred():
    from PyQt6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    app.processEvents()


def _paint_all(host):
    host.repaint()
    for w in host.findChildren(QWidget):
        w.repaint()
    app.processEvents()


def test_buttons_deleted_over_the_picture_never_raise(monkeypatch):
    from PyQt6 import sip
    from PyQt6.QtWidgets import QPushButton
    hook = _Hook(monkeypatch)
    host, surface, layer, stats, bar, badge, under = _setup()
    for round_ in range(20):
        made = []
        for i in range(3):
            b = QPushButton(f"notice {round_}.{i}", host if i % 2 else surface)
            b.setGeometry(20 + 30 * i, 60, 60, 24)
            b.show()
            made.append(b)
        layer.relook()                   # what the 300 ms scan does
        layer._flush()
        assert any(w is made[0] for _oid, w, _g in layer._shown)
        # Gone two ways: deleted at once, and later through the event loop.
        sip.delete(made[0])
        made[1].deleteLater()
        _deferred()
        _paint_all(host)
        layer._flush()
        made[2].deleteLater()
        _deferred()
        _paint_all(host)
        layer.relook()
        layer._flush()
    assert hook.seen == []
    assert all(video_thread._alive(w) for _oid, w, _g in layer._shown)
    assert all(video_thread._alive(w) for w in list(layer._watched))
    layer.stop()


def test_a_deleted_overlay_leaves_the_picture_at_once():
    from PyQt6 import sip
    from PyQt6.QtWidgets import QPushButton
    host, surface, layer, stats, bar, badge, under = _setup()
    b = QPushButton("Retry", host)
    b.setGeometry(40, 80, 80, 24)
    b.show()
    layer._flush()
    oid = next(o for o, w, _g in layer._shown if w is b)
    surface.mpv.calls.clear()
    sip.delete(b)
    assert ("overlay-remove", oid) in surface.mpv.calls
    assert all(w is not b for _o, w, _g in layer._shown)
    layer.stop()


def test_ids_left_over_after_a_middle_one_goes_are_taken_off():
    from PyQt6 import sip
    from PyQt6.QtWidgets import QPushButton
    host, surface, layer, stats, bar, badge, under = _setup()
    b = QPushButton("middle", host)
    b.setGeometry(40, 80, 80, 24)
    b.show()
    c = QPushButton("last", host)
    c.setGeometry(140, 80, 80, 24)
    c.show()
    layer._flush()
    top = max(o for o, _w, _g in layer._shown)
    sip.delete(b)                    # off the picture now, a hole in the ids
    surface.mpv.calls.clear()
    layer._full = True
    layer._flush()
    shown = {o for o, _w, _g in layer._shown}
    removed = {cl[1] for cl in surface.mpv.calls if cl[0] == "overlay-remove"}
    assert top not in shown and top in removed
    assert shown == set(range(len(shown)))
    layer.stop()


def test_uninstalled_the_layer_watches_nothing(monkeypatch):
    from PyQt6.QtWidgets import QPushButton
    hook = _Hook(monkeypatch)
    host, surface, layer, stats, bar, badge, under = _setup()
    layer._flush()
    layer.uninstall()
    assert not layer._shown and not list(layer._watched)
    b = QPushButton("after", host)
    b.show()
    _paint_all(host)
    b.deleteLater()
    _deferred()
    layer._flush()
    layer.relook()
    assert hook.seen == [] and not layer._shown


def test_the_pop_out_mirror_deleted_first_falls_back_to_the_surface(monkeypatch):
    hook = _Hook(monkeypatch)
    host, surface, layer, stats, bar, badge, under = _setup()
    pop = QWidget()
    mirror = _Surface(pop)
    pop.show()
    layer.place_over(mirror)
    layer._flush()
    pop.deleteLater()
    _deferred()
    layer._flush()
    assert layer._over is surface and hook.seen == []
    layer.stop()


def test_a_window_on_its_way_out_is_never_drawn_into():
    from PyQt6 import sip
    win = video_thread._VideoWindow(lambda: None)
    win.resize(100, 100)
    assert video_thread._target_of(win, True).window is win
    win.gone = True
    assert video_thread._target_of(win, True).window is None
    win.gone = False
    sip.delete(win)
    assert video_thread._target_of(win, True).window is None


def test_a_surface_destroyed_without_shutdown_ends_what_it_runs():
    class _Thread:
        finished = False

        def finish(self):
            self.finished = True

    class _Core:
        ended = False

        def terminate(self):
            self.ended = True

    host = QWidget()
    s = video_thread.ThreadedVideoSurface(host)
    t, c = _Thread(), _Core()
    s._ending.renderer, s._ending.core = t, c
    host.deleteLater()
    _deferred()
    assert t.finished and c.ended


# -- the mouse on the picture: as on the window-thread surface ------------------------

def _mouse_log(surface_type):
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtTest import QTest
    host = QWidget()
    host.resize(320, 200)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    s = surface_type(host)
    lay.addWidget(s)
    host.show()
    app.processEvents()
    seen = []
    s.video_mouse_press.connect(lambda e: seen.append(("press", e.button().name)))
    s.video_mouse_release.connect(lambda e: seen.append(("release", e.button().name)))
    s.video_dbl_click.connect(lambda: seen.append(("double",)))
    win = host.windowHandle()
    at = QPoint(60, 50)              # off the centre, on the picture
    QTest.mouseClick(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at)
    seen.append("|")
    QTest.mouseDClick(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at)
    seen.append("|")
    QTest.mouseClick(win, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, at)
    if hasattr(s, "shutdown"):
        s.shutdown()
    host.close()
    return seen


def test_a_click_double_click_and_right_click_reach_the_player_as_before():
    old = _mouse_log(video_surface._MpvGLWidget)
    new = _mouse_log(video_thread.ThreadedVideoSurface)
    assert new == old
    assert ("press", "LeftButton") in old and ("double",) in old
    assert ("press", "RightButton") in old


# -- what mpv is handed: what the widget paints, nothing more --------------------------

def _image_of(layer, w):
    """The picture of *w* the layer hands mpv (its last one)."""
    layer._full = True
    layer._flush()
    oid = next(o for o, x, _g in layer._shown if x is w)
    return layer._imgs[oid][-1]


def test_a_round_button_is_handed_over_round_with_soft_edges():
    from PyQt6.QtWidgets import QPushButton
    host, surface, layer, stats, bar, badge, under = _setup()
    b = QPushButton(host)
    b.setObjectName("CenterPlay")
    b.setFixedSize(72, 72)
    b.setStyleSheet("#CenterPlay { background: rgba(16,16,20,140); border:none;"
                    " border-radius:36px; }")
    b.move(120, 50)
    b.show()
    img = _image_of(layer, b)
    w, h = img.width(), img.height()
    corners = [img.pixelColor(x, y).alpha() for x, y in
               ((1, 1), (w - 2, 1), (1, h - 2), (w - 2, h - 2))]
    assert corners == [0, 0, 0, 0]              # no square box around it
    assert img.pixelColor(w // 2, h // 2).alpha() > 100
    edge = {img.pixelColor(x, h // 2).alpha() for x in range(0, 6)}
    assert any(0 < a < 140 for a in edge)        # anti-aliased, not cut
    layer.stop()


def test_a_widget_that_fills_its_background_still_does():
    host, surface, layer, stats, bar, badge, under = _setup()
    panel = QWidget(host)
    panel.setAutoFillBackground(True)
    pal = panel.palette()
    from PyQt6.QtGui import QColor
    pal.setColor(panel.backgroundRole(), QColor(10, 20, 30))
    panel.setPalette(pal)
    panel.setGeometry(150, 100, 60, 30)
    panel.show()
    img = _image_of(layer, panel)
    assert img.pixelColor(1, 1).alpha() == 255
    # A label without a background of its own: only its text.
    img = _image_of(layer, badge)
    assert img.pixelColor(1, 1).alpha() == 0
    layer.stop()


def test_a_row_is_handed_over_laid_out_as_it_will_be_painted():
    # The DVR row: a time label beside a slider. Its text grows just before
    # the picture is taken - the layout that follows must come first, or mpv
    # shows the row laid out the old way until the next picture.
    from PyQt6.QtWidgets import QHBoxLayout, QSlider
    host, surface, layer, stats, bar, badge, under = _setup()
    row = QWidget(host)
    lay = QHBoxLayout(row)
    lay.setContentsMargins(4, 2, 4, 2)
    label = QLabel("0:07", row)
    slider = QSlider(video_thread.Qt.Orientation.Horizontal, row)
    lay.addWidget(label)
    lay.addWidget(slider, 1)
    row.setGeometry(10, 120, 300, 28)
    row.show()
    app.processEvents()
    layer._flush()
    label.setText("DVR −1:23:45 / 2:00:00")          # a layout request waits
    seen = []
    orig = layer._add

    def add(core, oid, w, g):
        if w is row:
            seen.append(slider.x())
        orig(core, oid, w, g)
    layer._add = add
    layer._dirty.update(o for o, w, _g in layer._shown if w is row)
    layer._flush()
    app.processEvents()
    assert seen and seen[-1] == slider.x()            # never the old layout
    layer.stop()


def test_the_rows_time_keeps_its_width_while_shown():
    from dopeiptv.media.rewind_bar import steady_width
    from PyQt6.QtWidgets import QHBoxLayout
    bar_ = QWidget()
    QHBoxLayout(bar_).addWidget(label := QLabel("DVR 0:07"))
    bar_.show()
    widths = []
    for text in ("DVR 0:07", "DVR 0:11", "DVR 0:19", "DVR 1:00"):
        label.setText(text)
        steady_width(label, bar_)
        widths.append(label.minimumWidth())
    assert len(set(widths)) == 1                      # digits ticking: no change
    label.setText("DVR −0:21 / 1:00")
    steady_width(label, bar_)
    assert label.minimumWidth() > widths[0]           # a longer text: wider
    bar_.hide()
    label.setText("DVR 0:30")
    steady_width(label, bar_)
    assert label.minimumWidth() == 0                  # hidden: afresh
