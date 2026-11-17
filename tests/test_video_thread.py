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


def test_before_the_first_video_it_says_the_own_thread_is_coming():
    """The own thread starts with the first video: until then Diagnostics
    said "Window thread" in amber, and the person thought it was broken."""
    from dopeiptv.ui.nerd_settings import video_path_chip, video_path_text
    from dopeiptv.i18n import tr
    video_mode.chosen(video_mode.THREAD)
    assert video_mode.pending()
    assert video_mode.line() == "Video: own thread (not started yet)"
    assert video_path_text() == tr("video_path_pending")
    chip, why = video_path_chip()
    assert tr("video_path_thread") in chip.text() and why is not None
    video_mode.note(video_mode.THREAD)
    assert not video_mode.pending() and video_path_text() == tr("video_path_thread")
    video_mode.note_failed("no frame could be shown")
    assert not video_mode.pending()
    assert video_mode.line().startswith("Video: window thread (fallback: ")


def test_making_the_threaded_surface_notes_the_choice(monkeypatch):
    monkeypatch.setattr(video_thread, "can_start", lambda: "")
    made = []
    monkeypatch.setattr(video_thread, "ThreadedVideoSurface", lambda parent=None: made.append(1) or QWidget())
    video_thread.make_video_surface(QWidget(), _Settings())
    assert made and video_mode.pending()


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


def test_macos_draws_with_its_own_context_and_elsewhere_with_qts():
    assert video_thread.gl_kind("darwin") == video_thread.NSGL
    assert video_thread.gl_kind("linux") == video_thread.QT_GL
    assert video_thread.gl_kind("win32") == video_thread.QT_GL


def test_on_macos_can_start_asks_its_own_context_not_qt(monkeypatch):
    """Qt's "no OpenGL on other threads" on cocoa is about its own context:
    on macOS what counts is whether the app's own can be made (PyObjC's
    AppKit, the OpenGL framework)."""
    from dopeiptv.media import video_macos
    monkeypatch.setattr(video_thread, "_kind", lambda: video_thread.NSGL)
    monkeypatch.setattr(video_thread.QGuiApplication, "platformName", staticmethod(lambda: "cocoa"))
    monkeypatch.setattr(video_thread.QOpenGLContext, "supportsThreadedOpenGL",
                        staticmethod(lambda: False))
    monkeypatch.setattr(video_thread, "_libmpv", object())
    monkeypatch.setattr(video_macos, "unavailable", lambda: "")
    assert video_thread.can_start() == ""
    monkeypatch.setattr(video_macos, "unavailable",
                        lambda: "PyObjC's AppKit: ImportError: no AppKit")
    assert video_thread.can_start() == "cocoa: PyObjC's AppKit: ImportError: no AppKit"
    monkeypatch.setattr(video_macos, "unavailable", lambda: "")
    video_mode.note_failed("no frame could be shown")
    assert video_thread.can_start() == "no frame could be shown"
    # The platforms nothing can draw on another thread on are still refused.
    monkeypatch.setattr(video_thread.QGuiApplication, "platformName",
                        staticmethod(lambda: "offscreen"))
    assert video_thread.can_start() == "the offscreen platform"


def test_a_mac_context_that_cannot_be_made_is_a_reason(monkeypatch):
    from dopeiptv.media import video_macos

    class _NoContext:
        def prepare(self):
            return "no OpenGL pixel format (4.1 or 3.2 core)"

    class _Broken:
        def prepare(self):
            raise OSError("boom")
    host = QWidget()
    s = video_thread.ThreadedVideoSurface(host)
    monkeypatch.setattr(video_macos, "MacGL", _NoContext)
    assert s._make_gl(True) == (None, "no OpenGL pixel format (4.1 or 3.2 core)")
    monkeypatch.setattr(video_macos, "MacGL", _Broken)
    assert s._make_gl(True) == (None, "NSOpenGLContext: OSError: boom")
    s.shutdown()


class _FakeRenderer:
    def __init__(self, log):
        self.log, self.frames, self.pending_since = log, 0, 0.0

    def set_target(self, t):
        self.log.append(("set_target", t))

    def release(self, win):
        self.log.append(("release", win))

    def finish(self):
        self.log.append(("finish",))


class _FakeGL:
    def __init__(self, log, answer):
        self.log, self.answer, self.info = log, answer, ""

    def attach(self, t):
        self.log.append(("attach",))
        if isinstance(self.answer, Exception):
            raise self.answer
        return t if self.answer == "now" else None

    def detach(self, win):
        self.log.append(("detach", win))

    def forget(self):
        self.log.append(("forget",))

    def close(self):
        self.log.append(("close",))


_HOSTS: list = []


def _surface_with(answer):
    log = []
    host = QWidget()
    _HOSTS.append(host)                  # its surface lives as long as it does
    s = video_thread.ThreadedVideoSurface(host)
    s._renderer = _FakeRenderer(log)
    s._gl = _FakeGL(log, answer)
    return s, log


def test_a_busy_context_is_put_on_its_view_a_moment_later():
    s, log = _surface_with("later")
    s._window_changed()
    assert log == [("attach",)]                    # the old target stays meanwhile
    assert s._attach_timer.isActive()
    assert s._attach_timer.interval() == video_thread.ATTACH_RETRY_MS
    s._gl.answer = "now"
    s._window_changed()
    assert [e[0] for e in log] == ["attach", "attach", "set_target"]
    assert s._attach_tries == 0
    s.shutdown()


def test_a_context_that_stays_busy_or_fails_falls_back(monkeypatch):
    for answer, reason in (
            (video_thread.ATTACH_TRIES + 1, "the picture's window stayed busy"),
            (RuntimeError("setView"), "the picture's window: RuntimeError: setView")):
        s, log = _surface_with("later" if isinstance(answer, int) else answer)
        gave_up = []
        s.fallback.connect(gave_up.append)
        for _ in range(answer if isinstance(answer, int) else 1):
            s._window_changed()
        assert not gave_up                         # never inside the event itself
        app.processEvents()
        assert gave_up == [reason] and s._failed == reason
        assert ("finish",) in log and ("close",) in log
        assert log.index(("finish",)) < log.index(("close",))
        assert s._attach_timer is None or not s._attach_timer.isActive()
        video_mode.reset()


def test_a_window_that_goes_is_let_go_by_the_thread_then_the_context():
    s, log = _surface_with("now")
    win = s._win
    s._window_going(win)
    assert log[:2] == [("release", win), ("detach", win)]
    s.shutdown()
    assert log[-2:] == [("finish",), ("close",)]


class _QtCtx:
    """Qt's QOpenGLContext as the render thread sees it."""
    current = None
    log: list = []
    fail: set = set()

    def setFormat(self, fmt):
        pass

    def create(self):
        return True

    def makeCurrent(self, surface):
        ok = surface not in _QtCtx.fail
        _QtCtx.log.append(("makeCurrent", surface, ok))
        _QtCtx.current = self if ok else None
        return ok

    def doneCurrent(self):
        _QtCtx.log.append(("doneCurrent",))
        _QtCtx.current = None

    def swapBuffers(self, win):
        _QtCtx.log.append(("swap", win))

    def defaultFramebufferObject(self):
        return 7

    def getProcAddress(self, name):
        return 0

    @staticmethod
    def currentContext():
        return _QtCtx.current


def test_qts_context_is_driven_on_linux_and_windows_as_it_always_was(monkeypatch):
    """The render loop with Qt's context, call for call what it made before
    the context became a strategy: current on the offscreen surface, on a
    window for its frames (again after a change), swapped; off it when the
    window is not shown or could not be made current; freed on the
    offscreen surface."""
    from PyQt6.QtGui import QWindow
    a, b = QWindow(), QWindow()
    _QtCtx.log, _QtCtx.fail, _QtCtx.current = [], set(), None
    log = _QtCtx.log
    box = []

    def target(win, shown=True):
        return video_thread._Target(win, 640, 360, shown)
    script = [
        (lambda r: r.set_target(target(a)), True),
        (None, True), (None, True),
        (lambda r: r.set_target(target(a, False)), True),
        (None, True),
        (lambda r: r.set_target(target(b)), True),
        (None, True),
        (lambda r: (_QtCtx.fail.add(a), r.set_target(target(a))), True),
        (None, True),
    ]

    class _Rctx:
        update_cb = None

        def __init__(self):
            self.i = 0

        def update(self):
            r = box[0]
            if self.i >= len(script):
                r.stop()
                return False
            act, frame = script[self.i]
            self.i += 1
            log.append(("update",))
            if act:
                act(r)
            r._wake.set()
            return frame

        def render(self, **kw):
            log.append(("render", "skip" if kw.get("skip_rendering") else kw["opengl_fbo"]["fbo"],
                        kw.get("block_for_target_time")))

        def report_swap(self):
            pass

        def free(self):
            log.append(("free",))

    class _Libmpv:
        MpvGlGetProcAddressFn = staticmethod(lambda fn: fn)
        MpvRenderContext = staticmethod(lambda *a, **kw: _Rctx())
    monkeypatch.setattr(video_thread, "QOpenGLContext", _QtCtx)
    monkeypatch.setattr(video_thread, "_libmpv", _Libmpv)
    off = object()
    r = video_thread._Renderer(object(), off, None, "qt-test", video_thread._Told())
    box.append(r)
    r.run()                         # this thread is the render thread here
    up, mc, sw = ("update",), "makeCurrent", "swap"
    assert log == [
        (mc, off, True),
        up, ("render", "skip", None),                          # no window yet
        up, (mc, a, True), ("render", 7, None), (sw, a),       # current on it
        up, ("render", 7, None), (sw, a),                      # stays current
        up, ("render", 7, None), (sw, a),
        up, (mc, off, True), ("render", "skip", None),         # not shown
        up, ("render", "skip", None),
        up, (mc, b, True), ("render", 7, None), (sw, b),       # another window
        up, ("render", 7, None), (sw, b),
        up, (mc, a, False), (mc, off, True),                   # could not be
        (mc, off, True), ("free",), ("doneCurrent",),          # freed off-window
    ]
    assert r.frames == 5 and r.error == ""


# -- the default: on, but not in a release on a Mac ---------------------------------

def test_a_test_build_is_a_nightly_or_a_checkout():
    from dopeiptv.core import updates
    assert video_mode.is_test_build("1.6.2.191", frozen=True, checkout=False)
    assert not video_mode.is_test_build("1.6.2", frozen=True, checkout=False)
    assert video_mode.is_test_build("1.6.2", frozen=False, checkout=True)      # from source
    assert not video_mode.is_test_build("1.6.2", frozen=False, checkout=False)  # pip's release
    for v in ("1.6.2", "1.6.2.191", "v1.4.0.12", "2.0"):
        assert video_mode.is_test_build(v, True, False) == updates.is_nightly(v)


def test_on_everywhere_but_in_a_release_on_macos():
    assert video_mode.default_on("linux", False) and video_mode.default_on("win32", False)
    assert video_mode.default_on("linux", True) and video_mode.default_on("darwin", True)
    assert not video_mode.default_on("darwin", False)


def test_a_mac_release_draws_on_the_windows_thread_unless_asked(monkeypatch):
    monkeypatch.setattr(video_mode, "_here", lambda: ("darwin", False))
    assert video_mode.wanted(_Settings()) == (False, video_mode.MAC_DEFAULT)
    assert video_mode.wanted(None) == (False, video_mode.MAC_DEFAULT)       # a multiview cell
    assert not video_mode.default_on_here()
    # A choice made in Settings counts, either way.
    assert video_mode.wanted(_Settings(video_own_thread="true")) == (True, "")
    assert video_mode.wanted(None) == (True, "")
    assert video_mode.wanted(_Settings(video_own_thread="false")) == (False, "switched off in Settings")
    # The environment wins over both.
    monkeypatch.setenv(video_mode.ENV, "1")
    assert video_mode.wanted(_Settings()) == (True, "")
    monkeypatch.setenv(video_mode.ENV, "0")
    assert video_mode.wanted(_Settings(video_own_thread="true"))[0] is False
    monkeypatch.delenv(video_mode.ENV)
    # A nightly on a Mac, and Linux and Windows: on.
    for here in (("darwin", True), ("linux", False), ("win32", False)):
        monkeypatch.setattr(video_mode, "_here", lambda here=here: here)
        assert video_mode.wanted(_Settings()) == (True, "")
        assert video_mode.wanted(_Settings(video_own_thread="false"))[0] is False


def test_a_macs_default_is_said_neutrally():
    from dopeiptv.i18n import tr
    from dopeiptv.ui.nerd_settings import video_path_chip, video_path_text
    from dopeiptv.ui.theme import P
    video_mode.note(video_mode.WINDOW, video_mode.MAC_DEFAULT)
    assert video_path_text() == tr("video_path_mac_default")
    assert video_mode.line() == "Video: window thread (the default on macOS)"
    chip, why = video_path_chip()
    assert P["muted"] in chip.text() and P["warn"] not in chip.text()
    assert why is not None and why.text() == tr("video_path_mac_default")
    video_mode.note_failed("no frame could be shown")                    # a fallback: amber
    chip, _why = video_path_chip()
    assert P["warn"] in chip.text()


# -- the right-click: a menu made native, never its siblings with it ----------------

def test_a_menu_made_native_on_macos_leaves_its_siblings_alone(monkeypatch):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QMenu

    from dopeiptv.media.video_thread import native_menu
    native = Qt.WidgetAttribute.WA_NativeWindow

    def window():
        top = QWidget()
        central = QWidget(top)
        top.show()
        return top, central
    video_mode.wanted(_Settings())                     # the own thread: wanted
    top, central = window()
    m = QMenu(top)
    native_menu(m, platform="darwin")
    assert m.testAttribute(native) and not central.testAttribute(native)
    # With the window's thread, as it always was: its siblings native too.
    video_mode.wanted(_Settings(video_own_thread="false"))
    top2, central2 = window()
    m2 = QMenu(top2)
    native_menu(m2, platform="darwin")
    assert m2.testAttribute(native) and central2.testAttribute(native)
    # Elsewhere nothing is made native.
    m3 = QMenu(top)
    native_menu(m3, platform="linux")
    assert not m3.testAttribute(native)
    for w in (top, top2):
        w.close()


# -- macOS: the picture's own view kept over its box ---------------------------------

def _mac_surface(monkeypatch):
    """A surface as macOS makes it - its picture in a view of its own, kept
    by its placement - with stand-ins for AppKit's views."""
    from dopeiptv.media import video_macos
    from tests.test_video_macos import ViewKit
    kit = ViewKit()
    monkeypatch.setattr(video_thread, "_kind", lambda: video_thread.NSGL)
    real = video_macos.PictureView
    monkeypatch.setattr(video_macos, "PictureView",
                        lambda: real(appkit=kit, objc_mod=kit.objc))
    # Its render thread and core are the real player's (not started here).
    monkeypatch.setattr(video_thread.ThreadedVideoSurface, "_start", lambda self: None)
    top = QWidget()
    top.resize(500, 320)
    central = QWidget(top)
    central.setGeometry(10, 20, 480, 290)
    lay = QVBoxLayout(central)
    lay.setContentsMargins(0, 0, 0, 0)
    s = video_thread.ThreadedVideoSurface(central)
    lay.addWidget(s)
    top.show()
    app.processEvents()
    log = []
    s._renderer = _FakeRenderer(log)
    _HOSTS.append(top)
    return s, top, central, kit, log


def test_on_macos_the_picture_view_follows_its_box(monkeypatch):
    s, top, central, kit, log = _mac_surface(monkeypatch)
    pv = s._win
    assert isinstance(s._place, video_thread._Placement)
    s._window_changed()
    assert pv.nsview is None                         # nothing native before a picture
    s._box.show()
    s._window_changed()
    v, host = pv.nsview, kit.hosts[int(top.winId())]
    assert v.sup is host and host.subs[-1] is v and not v.hidden
    assert v.frame_ == ((10, 20), (480, 290))        # in the window's view, at its place
    t = log[-1][1]
    assert t.window is pv and t.exposed and (t.w, t.h) == (480, 290)
    # Moved with an ancestor, at once.
    central.move(30, 40)
    assert v.frame_ == ((30, 40), (480, 290))
    # An ancestor made native (a menu's siblings, before the fix): the view
    # goes into that widget's own view, at its place there, on top.
    s._gl = _FakeGL(log, "now")
    central.winId()
    app.processEvents()
    assert v.sup is kit.hosts[int(central.winId())] and v.frame_ == ((0, 0), (480, 290))
    assert ("forget",) in log and not s._place.moved     # the context put on it again
    s._gl = None
    # Hidden with its box; the picture not drawn.
    s._box.hide()
    app.processEvents()
    assert v.hidden and not log[-1][1].exposed
    s.shutdown()
    assert pv.gone and v.sup is None


def test_on_macos_the_same_target_is_handed_over_once(monkeypatch):
    s, top, central, kit, log = _mac_surface(monkeypatch)
    s._box.show()
    s._window_changed()
    n = len(log)
    for _ in range(3):                               # the 1 s look, a repaint's expose
        s._window_changed()
        s._place._sync_and_tell()
    assert len(log) == n                             # no redraw asked for nothing
    central.resize(400, 250)
    app.processEvents()
    assert len(log) > n and (log[-1][1].w, log[-1][1].h) == (400, 250)
    s._let_go(s._win)                                # let go: handed over again after
    s._window_changed()
    assert log[-1][0] == "set_target"
    s.shutdown()


def test_on_macos_a_view_given_up_leaves_at_once(monkeypatch):
    s, top, central, kit, log = _mac_surface(monkeypatch)
    s._box.show()
    s._window_changed()
    v = s._win.nsview
    gave_up = []
    s.fallback.connect(gave_up.append)
    s._render_failed("no frame could be shown")
    assert gave_up == ["no frame could be shown"]
    assert s._win.gone and v.sup is None             # never a last frame over the new one


def test_on_macos_the_windows_view_going_lets_go_first(monkeypatch):
    from PyQt6.QtGui import QPlatformSurfaceEvent
    s, top, central, kit, log = _mac_surface(monkeypatch)
    s._box.show()
    s._window_changed()
    pv = s._win
    going = QPlatformSurfaceEvent(QPlatformSurfaceEvent.SurfaceEventType.SurfaceAboutToBeDestroyed)
    s._place.eventFilter(s._place._top, going)
    assert ("release", pv) in log and pv.nsview.sup is None and not pv.shown
    app.processEvents()
    s._window_changed()                              # back once its window is
    assert pv.nsview.sup is kit.hosts[int(top.winId())]
    s.shutdown()


def test_on_macos_the_mouse_reaches_the_player_as_before(monkeypatch):
    from dopeiptv.media import video_macos
    from tests.test_video_macos import ViewKit
    kit = ViewKit()
    monkeypatch.setattr(video_thread, "_kind", lambda: video_thread.NSGL)
    real = video_macos.PictureView
    monkeypatch.setattr(video_macos, "PictureView",
                        lambda: real(appkit=kit, objc_mod=kit.objc))
    assert _mouse_log(video_thread.ThreadedVideoSurface) == _mouse_log(video_surface._MpvGLWidget)


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
