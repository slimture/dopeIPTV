"""The macOS piece of the picture's own thread (media/video_macos.py): the
NSOpenGLContext the render thread draws with there. Checked with stand-ins
for AppKit, PyObjC and the OpenGL framework - which call is made on which
thread, under which lock and in which order, and what a failure becomes.
Runs anywhere; the real drawing only on a Mac (the release build's
--video-check on the macOS runners)."""
from __future__ import annotations

import os
import sys
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace as NS

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from dopeiptv.media import video_macos as vm
from dopeiptv.media.video_thread import _Target

app = QApplication.instance() or QApplication([])


class _Log(list):
    def add(self, *what):
        where = "main" if threading.current_thread() is threading.main_thread() else "render"
        self.append((where, *what))

    def names(self, where=None):
        return [e[1] for e in self if where is None or e[0] == where]


class _Lib:
    """The OpenGL framework: CGL's lock, the current context, parameters."""

    def __init__(self, log):
        self.log = log
        self.current = 0

        def glGetString(which):
            return {vm.GL_RENDERER: b"Fake GPU", vm.GL_VERSION: b"4.1 Fake"}.get(which)
        self.glGetString = glGetString

    def CGLLockContext(self, cgl):
        assert cgl == 0xC61
        self.log.add("lock")

    def CGLUnlockContext(self, cgl):
        self.log.add("unlock")

    def CGLGetCurrentContext(self):
        return self.current

    def CGLSetParameter(self, cgl, param, ref):
        self.log.add("param", param, ref._obj.value)


class _Ctx:
    def __init__(self, log, lib, refuse=False):
        self.log, self.lib, self.refuse = log, lib, refuse
        self._view = None
        self.flush_error = None

    def makeCurrentContext(self):
        self.log.add("current")
        self.lib.current = 0xC61

    def flushBuffer(self):
        self.log.add("flush")
        if self.flush_error:
            raise self.flush_error

    def update(self):
        self.log.add("update")

    def setView_(self, view):
        self.log.add("setView", view.ptr)
        if not self.refuse:
            self._view = view

    def view(self):
        return self._view

    def clearDrawable(self):
        self.log.add("clearDrawable")
        self._view = None


class _AppKit:
    def __init__(self, log, lib, hardware=True, formats=True, context=True, refuse=False):
        self.asked = []
        appkit = self

        class _PF:
            def initWithAttributes_(self, attrs):
                appkit.asked.append(list(attrs))
                if not formats or (vm.PFA_ACCELERATED in attrs and not hardware):
                    return None
                return NS(attrs=list(attrs))

        class _C:
            def initWithFormat_shareContext_(self, pf, share):
                assert share is None
                appkit.ctx = _Ctx(log, lib, refuse) if context else None
                return appkit.ctx

        self.NSOpenGLPixelFormat = NS(alloc=_PF)
        self.NSOpenGLContext = NS(alloc=_C, clearCurrentContext=lambda: log.add("clearCurrent"))


class _View:
    def __init__(self, log, ptr, w=640, h=360, scale=2.0, win=0x1001, screen=0x2001, x=0, y=0):
        self.log, self.ptr = log, ptr
        self.w, self.h, self.x, self.y = w, h, x, y
        self.win = NS(ptr=win, backingScaleFactor=lambda: scale,
                      screen=lambda: NS(ptr=screen))

    def bounds(self):
        return NS(size=NS(width=self.w, height=self.h))

    def frame(self):
        return NS(origin=NS(x=self.x, y=self.y))

    def window(self):
        return self.win

    def setWantsBestResolutionOpenGLSurface_(self, on):
        self.log.add("bestResolution", on)


class _Objc:
    def __init__(self, log, views):
        self.log, self.views = log, views

    def objc_object(self, c_void_p=None):
        return self.views[int(c_void_p)]

    def pyobjc_id(self, obj):
        return obj.ptr

    @contextmanager
    def autorelease_pool(self):
        self.log.add("pool+")
        try:
            yield
        finally:
            self.log.add("pool-")


class _QWindow:
    def __init__(self, ptr):
        self.ptr = ptr

    def winId(self):
        return self.ptr

    def devicePixelRatio(self):
        return 2.0


def _make(**kw):
    log = _Log()
    lib = _Lib(log)
    appkit = _AppKit(log, lib, **kw)
    views = {0xA: _View(log, 0xA), 0xB: _View(log, 0xB)}
    gl = vm.MacGL(appkit=appkit, objc_mod=_Objc(log, views), lib=lib)
    return gl, log, appkit, views


def _on_render_thread(fn, *args):
    out = []
    t = threading.Thread(target=lambda: out.append(fn(*args)))
    t.start()
    t.join(5)
    return out[0] if out else None


def _ready(**kw):
    """A context made (main thread) and current on a render thread."""
    gl, log, appkit, views = _make(**kw)
    assert gl.prepare() == ""
    assert _on_render_thread(gl.create) == ""
    log.clear()
    return gl, log, appkit, views


# -- the pure parts --------------------------------------------------------------------

def test_the_pixel_format_asks_for_what_mpv_draws_with():
    attrs = vm.pixel_format_attrs(vm.PROFILE_4_1_CORE, True)
    assert attrs[:2] == [vm.PFA_OPENGL_PROFILE, vm.PROFILE_4_1_CORE]
    assert vm.PFA_DOUBLE_BUFFER in attrs and vm.PFA_ACCELERATED in attrs
    assert attrs[-1] == 0 and attrs.count(0) == 1
    assert vm.PFA_ACCELERATED not in vm.pixel_format_attrs(vm.PROFILE_4_1_CORE, False)
    # 4.1 core on a hardware renderer first, as the app asks Qt for.
    assert vm.FORMATS[0] == (vm.PROFILE_4_1_CORE, True)


def test_the_backing_size_is_the_views_points_times_its_scale():
    assert vm.backing_size(640, 360, 2.0) == (1280, 720)        # Retina
    assert vm.backing_size(640, 360, 1.0) == (640, 360)
    assert vm.backing_size(100.4, 50.6, 1.0) == (100, 51)
    assert vm.backing_size(333, 200, 1.5) == (500, 300)
    assert vm.backing_size(0, 0, 2.0) == (1, 1)                 # never 0
    assert vm.backing_size(640, 360, 0) == (640, 360)           # no scale known
    assert vm.backing_size(640, 360, float("nan")) == (640, 360)


def test_what_a_change_needs():
    p = vm.Place(1, 2, 3, 0.0, 0.0, 1280, 720)
    assert vm.change_for(None, p, False) == vm.SET
    assert vm.change_for(p._replace(view=9), p, False) == vm.SET
    assert vm.change_for(p, p, False) == ""
    assert vm.change_for(p, p, True) == vm.UPDATE                # shown again
    for moved in (p._replace(w=1920), p._replace(window=7), p._replace(screen=8),
                  p._replace(x=4.0), p._replace(h=1080)):
        assert vm.change_for(p, moved, False) == vm.UPDATE


def test_off_macos_it_says_so():
    if sys.platform != "darwin":
        assert vm._look() == "not macOS"


# -- made on the main thread -------------------------------------------------------------

def test_prepare_takes_the_best_pixel_format_there_is():
    gl, log, appkit, views = _make()
    assert gl.prepare() == ""
    assert len(appkit.asked) == 1 and vm.PFA_ACCELERATED in appkit.asked[0]
    assert gl.format == "OpenGL 4.1 core, hardware renderer"
    # A Mac (or a runner) without a hardware renderer: the next one.
    gl, log, appkit, views = _make(hardware=False)
    assert gl.prepare() == ""
    assert len(appkit.asked) == 2 and vm.PFA_ACCELERATED not in appkit.asked[1]
    assert gl.format == "OpenGL 4.1 core"


def test_prepare_says_why_not():
    gl, *_ = _make(formats=False)
    assert gl.prepare() == "no OpenGL pixel format (4.1 or 3.2 core)"
    gl, *_ = _make(context=False)
    assert gl.prepare() == "the NSOpenGLContext could not be made"


def test_made_current_on_the_render_thread_with_no_vsync_wait():
    gl, log, appkit, views = _make()
    gl.prepare()
    assert _on_render_thread(gl.create) == ""
    assert ("render", "current") in log
    assert ("render", "param", vm.CGL_CP_SWAP_INTERVAL, 0) in log
    assert gl.info == "Fake GPU, OpenGL 4.1 Fake"


# -- on its view: the main thread, under the lock ------------------------------------------

def test_the_main_thread_puts_the_context_on_the_view_under_its_lock():
    gl, log, appkit, views = _ready()
    win = _QWindow(0xA)
    out = gl.attach(_Target(win, 640, 360, True))
    assert (out.window, out.w, out.h, out.exposed) == (win, 1280, 720, True)
    assert log == [("main", "lock"), ("main", "pool+"), ("main", "bestResolution", True),
                   ("main", "setView", 0xA), ("main", "update"), ("main", "pool-"),
                   ("main", "unlock")]


def test_only_a_change_reaches_appkit():
    gl, log, appkit, views = _ready()
    win = _QWindow(0xA)
    gl.attach(_Target(win, 640, 360, True))
    log.clear()
    gl.attach(_Target(win, 640, 360, True))
    assert log == []                                     # nothing changed
    views[0xA].w = 800                                   # resized
    assert gl.attach(_Target(win, 800, 360, True)).w == 1600
    assert log.names() == ["lock", "pool+", "update", "pool-", "unlock"]
    log.clear()
    views[0xA].x = 20                                    # moved in its window
    gl.attach(_Target(win, 800, 360, True))
    assert "update" in log.names()
    log.clear()
    gl.attach(_Target(win, 800, 360, False))             # hidden: nothing asked
    assert log == []
    gl.attach(_Target(win, 800, 360, True))              # shown again
    assert "update" in log.names()
    log.clear()
    gl.attach(_Target(_QWindow(0xB), 640, 360, True))    # the pop-out's window
    assert ("main", "setView", 0xB) in log
    log.clear()
    gl.attach(_Target(_QWindow(0xB), 640, 360, True))    # another QWindow, same view
    assert ("main", "setView", 0xB) in log


def test_a_view_that_does_not_take_the_context_is_a_reason():
    gl, log, appkit, views = _ready(refuse=True)
    with pytest.raises(vm.MacGLError, match="did not take"):
        gl.attach(_Target(_QWindow(0xA), 640, 360, True))
    assert log.names()[-1] == "unlock"                   # nothing left locked
    assert gl._gate.acquire(blocking=False)


def test_nothing_of_appkit_is_asked_off_the_main_thread():
    gl, log, appkit, views = _ready()
    target = _Target(_QWindow(0xA), 640, 360, True)
    for call in (lambda: gl.attach(target), gl.prepare,
                 lambda: (setattr(gl, "_window", target.window), gl.detach(target.window))):
        errors: list = []

        def run(call=call, errors=errors):
            try:
                call()
            except vm.MacGLError as e:
                errors.append(e)
        t = threading.Thread(target=run)
        t.start()
        t.join(5)
        assert errors and "off the main thread" in str(errors[0])
    assert log == []


def test_the_window_thread_never_waits_for_a_frame():
    gl, log, appkit, views = _ready()
    a, b = _QWindow(0xA), _QWindow(0xB)
    gl.attach(_Target(a, 640, 360, True))
    in_frame, done = threading.Event(), threading.Event()

    def frame():
        assert gl.begin(a, False) is True
        in_frame.set()
        done.wait(5)
        gl.present(a)
        gl.end(True)
    t = threading.Thread(target=frame)
    t.start()
    assert in_frame.wait(5)
    t0 = time.monotonic()
    assert gl.attach(_Target(b, 640, 360, True)) is None  # later - not waited for
    assert time.monotonic() - t0 < 0.5
    done.set()
    t.join(5)
    assert gl.attach(_Target(b, 640, 360, True)).window is b


# -- drawn on the render thread ------------------------------------------------------------

def test_a_frame_is_locked_current_drawn_and_flushed():
    gl, log, appkit, views = _ready()
    win = _QWindow(0xA)
    gl.attach(_Target(win, 640, 360, True))
    log.clear()

    def frame():
        ok = gl.begin(win, False)
        try:
            log.add("render")
            gl.present(win)
        finally:
            gl.end(ok)
        return ok
    assert _on_render_thread(frame) is True
    assert log == [("render", "lock"), ("render", "pool+"), ("render", "current"),
                   ("render", "render"), ("render", "flush"), ("render", "pool-"),
                   ("render", "unlock")]
    assert gl.fbo() == 0
    assert gl.render_args == {"block_for_target_time": False}


def test_a_window_the_context_is_not_on_is_no_failure():
    gl, log, appkit, views = _ready()
    gl.attach(_Target(_QWindow(0xA), 640, 360, True))
    log.clear()
    assert _on_render_thread(gl.begin, _QWindow(0xB), True) is None
    assert log == [] and gl._gate.acquire(blocking=False)


def test_a_drawing_error_is_a_reason_not_a_window_gone():
    gl, log, appkit, views = _ready()
    win = _QWindow(0xA)
    gl.attach(_Target(win, 640, 360, True))
    appkit.ctx.flush_error = RuntimeError("objc: NSInternalInconsistencyException")
    gl.begin(win, False)
    with pytest.raises(vm.MacGLError) as err:
        gl.present(win)
    gl.end(True)
    # The render loop takes a RuntimeError for its window going away.
    assert not isinstance(err.value, RuntimeError)
    assert gl._gate.acquire(blocking=False)


# -- the end ---------------------------------------------------------------------------------

def test_a_view_that_goes_is_let_go_of_on_the_main_thread():
    gl, log, appkit, views = _ready()
    win = _QWindow(0xA)
    gl.attach(_Target(win, 640, 360, True))
    log.clear()
    gl.detach(_QWindow(0xB))                     # not its view: nothing
    assert log == []
    gl.detach(win)
    assert log.names() == ["lock", "pool+", "clearDrawable", "pool-", "unlock"]
    assert all(where == "main" for where, *_ in log)
    log.clear()
    gl.attach(_Target(win, 640, 360, True))      # a new view there: put on it again
    assert ("main", "setView", 0xA) in log


def test_teardown_frees_with_the_context_current_then_lets_go_on_the_main_thread():
    gl, log, appkit, views = _ready()
    gl.attach(_Target(_QWindow(0xA), 640, 360, True))
    log.clear()
    rctx = NS(free=lambda: log.add("free"))
    _on_render_thread(gl.free, rctx)
    assert log.names("render") == ["lock", "pool+", "current", "pool-", "free", "unlock",
                                   "clearCurrent"]
    gl.close()
    assert log.names("main") == ["lock", "pool+", "clearDrawable", "pool-", "unlock"]
    assert gl._ctx is None


def test_a_context_still_in_use_is_never_freed():
    gl, log, appkit, views = _ready()        # the render thread made it current...
    gl.close()                               # ...and never ended
    assert gl._ctx is not None and "clearDrawable" not in log.names()
    assert gl in vm._KEPT
    vm._KEPT.remove(gl)


# -- the renderer drawing through it -------------------------------------------------------

class _Rctx:
    """mpv's render context: a frame to show while *want* lasts."""

    def __init__(self, log):
        self.log, self.want, self.renders = log, 0, []

    def update(self):
        self.log.add("rctx.update")
        if self.want > 0:
            self.want -= 1
            return True
        return False

    def render(self, **kw):
        self.log.add("rctx.render")
        self.renders.append(kw)

    def report_swap(self):
        pass

    def free(self):
        self.log.add("rctx.free")


def _held(log, name):
    """Every *name* on the render thread came with the CGL lock held; how
    many there were."""
    state, seen = None, 0
    for where, what, *_ in log:
        if where != "render":
            continue
        if what in ("lock", "unlock"):
            state = what
        elif what == name:
            seen += 1
            assert state == "lock", f"{name} without the lock"
    return seen


def test_the_render_loop_draws_through_the_mac_context(monkeypatch):
    from PyQt6.QtGui import QWindow

    from dopeiptv.media import video_thread as vt
    gl, log, appkit, views = _make()
    assert gl.prepare() == ""
    rctx = _Rctx(log)

    class _Libmpv:
        MpvGlGetProcAddressFn = staticmethod(lambda fn: fn)

        @staticmethod
        def MpvRenderContext(core, api, **kw):
            log.add("rctx.made")
            return rctx
    monkeypatch.setattr(vt, "_libmpv", _Libmpv)
    r = vt._Renderer(object(), None, None, "mac-test", vt._Told(), gl=gl)
    r.start()
    assert r.ready.wait(5) and not r.error
    win = QWindow()
    views[int(win.winId())] = _View(log, int(win.winId()))
    deadline = time.monotonic() + 5
    target = None
    while target is None and time.monotonic() < deadline:
        target = gl.attach(vt._Target(win, 640, 360, True))
    rctx.want = 3
    r.set_target(target)
    while r.frames < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    r.finish()
    assert r.frames == 3 and not r.isRunning()
    gl.close()
    # Made current on the render thread before mpv's render context.
    names = log.names("render")
    assert names.index("current") < names.index("rctx.made")
    assert _held(log, "rctx.made") == 1 and _held(log, "rctx.update") >= 3
    # Each frame into the default framebuffer at the view's backing size,
    # not waiting for the display time; locked, current, flushed.
    drawn = [kw for kw in rctx.renders if "opengl_fbo" in kw]
    assert len(drawn) == 3
    for kw in drawn:
        assert kw["opengl_fbo"] == {"fbo": 0, "w": 1280, "h": 720}
        assert kw["flip_y"] is True and kw["block_for_target_time"] is False
    assert _held(log, "rctx.render") == 3 and _held(log, "flush") == 3
    # The context put on the view on the main thread; the end in order:
    # mpv's render context freed with the context current, nothing current
    # on that thread after, then the context off its view on the main one.
    assert ("main", "setView", int(win.winId())) in log
    assert all(where == "main" for where, what, *_ in log
               if what in ("setView", "clearDrawable"))
    tail = [e[1] for e in log if e[1] in ("rctx.free", "clearCurrent", "clearDrawable")]
    assert tail == ["rctx.free", "clearCurrent", "clearDrawable"]
    assert _held(log, "rctx.free") == 1


# -- the picture's own view ------------------------------------------------------------

class NSView:
    """AppKit's NSView as the picture's view uses it: a superview, its
    subviews in order (the last on top), a frame, hidden."""
    _next = [0x5000]

    def __init__(self, ptr=None):
        if ptr is None:
            ptr = NSView._next[0] = NSView._next[0] + 0x10
        self.ptr, self.sup, self.subs = ptr, None, []
        self.frame_, self.hidden, self.layer = None, False, False

    @classmethod
    def alloc(cls):
        return cls()

    def initWithFrame_(self, frame):
        self.frame_ = frame
        return self

    def setWantsLayer_(self, on):
        self.layer = on

    def setHidden_(self, hidden):
        self.hidden = hidden

    def isHidden(self):
        return self.hidden

    def superview(self):
        return self.sup

    def removeFromSuperview(self):
        if self.sup is not None:
            self.sup.subs.remove(self)
            self.sup = None

    def setFrame_(self, frame):
        self.frame_ = frame

    def subviews(self):
        return list(self.subs)

    def addSubview_positioned_relativeTo_(self, view, where, other):
        assert where == vm.NS_WINDOW_ABOVE and other is None
        if view.sup is not None:
            view.sup.subs.remove(view)
        self.subs.append(view)
        view.sup = self

    # What MacGL asks of the view it goes on.
    def window(self):
        return NS(ptr=0x1001, backingScaleFactor=lambda: 2.0, screen=lambda: NS(ptr=0x2001))

    def bounds(self):
        (_x, _y), (w, h) = self.frame_
        return NS(size=NS(width=w, height=h))

    def frame(self):
        (x, y), _size = self.frame_
        return NS(origin=NS(x=x, y=y))

    def setWantsBestResolutionOpenGLSurface_(self, on):
        pass


class ViewKit:
    """AppKit and PyObjC for the picture's view: NSView, the host views by
    their pointer."""

    def __init__(self):
        self.NSView = NSView
        self.hosts = {}
        kit = self

        class _O:
            @staticmethod
            def objc_object(c_void_p=None):
                return kit.hosts.setdefault(int(c_void_p), NSView(int(c_void_p)))

            @staticmethod
            def pyobjc_id(obj):
                return obj.ptr
        self.objc = _O


def test_the_mouse_is_never_the_picture_views():
    kit = ViewKit()
    cls = vm.view_class(kit)
    assert issubclass(cls, NSView) and vm.view_class(kit) is cls       # made once
    v = cls.alloc().initWithFrame_(((0, 0), (1, 1)))
    assert v.hitTest_((10, 10)) is None                                 # clicks go under it
    assert v.acceptsFirstMouse_(None) is False and v.isOpaque() is True


def test_the_picture_view_is_made_when_shown_and_kept_on_top():
    kit = ViewKit()
    pv = vm.PictureView(appkit=kit, objc_mod=kit.objc)
    assert pv.place(0x10, (0, 0, 640, 360), False) == [] and pv.nsview is None
    assert pv.place(0x10, (5, 6, 640, 360), True) == ["made", "host", "frame", "shown"]
    v, host = pv.nsview, kit.hosts[0x10]
    assert v.sup is host and host.subs[-1] is v and v.layer and not v.hidden
    assert v.frame_ == ((5, 6), (640, 360)) and pv.shown and pv.winId() == v.ptr
    assert pv.place(0x10, (5, 6, 640, 360), True) == []                 # nothing changed
    host.addSubview_positioned_relativeTo_(NSView(), vm.NS_WINDOW_ABOVE, None)
    assert pv.place(0x10, (5, 6, 640, 360), True) == ["raised"]          # back on top
    assert host.subs[-1] is v
    # Into the view of a widget made native since.
    assert pv.place(0x20, (1, 2, 640, 360), True) == ["host", "frame"]
    assert v.sup is kit.hosts[0x20] and v not in host.subs
    assert pv.place(0x20, (1, 2, 640, 360), False) == ["hidden"] and v.hidden and not pv.shown
    pv.unhost()                                                          # its window's view goes
    assert v.sup is None and pv.nsview is v
    assert pv.place(0x20, (1, 2, 640, 360), True) == ["host", "frame", "shown"]
    pv.remove()
    assert pv.gone and v.sup is None and pv.nsview is None
    assert pv.place(0x20, (1, 2, 640, 360), True) == []


def test_the_context_goes_on_the_picture_view_itself():
    gl, log, appkit, views = _ready()
    kit = ViewKit()
    pv = vm.PictureView(appkit=kit, objc_mod=kit.objc)
    gl._objc = kit.objc
    assert gl.attach(_Target(pv, 1, 1, True)).exposed is False          # not made yet
    pv.place(0x10, (0, 0, 640, 360), True)
    out = gl.attach(_Target(pv, 1280, 720, True))
    assert (out.window, out.w, out.h, out.exposed) == (pv, 1280, 720, True)
    assert appkit.ctx.view() is pv.nsview
    log.clear()
    gl.forget()                                                          # raised: on it again
    gl.attach(_Target(pv, 1280, 720, True))
    assert "setView" in log.names()
