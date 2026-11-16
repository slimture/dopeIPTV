"""The player's picture drawn on a thread of its own.

Each video surface is a native window of its own (a QWindow in a
QWidget.createWindowContainer) that a render thread draws mpv's frames into
and swaps itself: mpv's update callback wakes that thread - nothing goes
through the window's thread - and the waits for a frame's display time and
for vsync are that thread's alone. Nothing the window does can slow the
picture, and the picture slows nothing in the window: the window has no GL
widget left, so a scroll repaints only what it moved again.

What the window shows over the picture (the loading indicator, the DVR row
and timeline, stats, badges, the fullscreen controls, multiview's labels)
stays the Qt widgets it always was, where it always was - its clicks,
hover, tooltips and cursor are Qt's own, because the native window takes
no input (WindowTransparentForInput: the window under it gets it). A native
window covers what Qt paints beneath it, so what those widgets show is
drawn into the picture as well, by mpv itself (`overlay-add`): each one is
drawn again only when it changes. While mpv has no picture to show (before
the first frame, sound alone, stopped) the native window is not shown and
everything is plain Qt.

The window's own thread (media/video_surface.py's _MpvGLWidget, exactly as
it always was) is used when this is switched off (core/video_mode.py) or
could not start; then the reason is logged and kept for Nerd > Diagnostics
and the error report.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
import weakref
from typing import Any, Callable

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor, QGuiApplication, QImage, QOffscreenSurface, QOpenGLContext, QPainter, QRegion,
    QSurface, QSurfaceFormat, QWindow,
)
from PyQt6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from ..core import video_mode
from ..core.log import log
from .players import _libmpv, _register_error_callback
from .video_surface import _MpvGLWidget, build_core

#: How long the window's thread waits for the render thread's GL context and
#: mpv render context at the first show, before it gives up on them.
START_TIMEOUT_S = 8.0
#: A picture mpv has but the render thread could not show for this long is
#: a failure of this way of drawing (the window's thread takes over).
STUCK_S = 4.0
#: Where mpv's update callback is not used (Windows), how often the render
#: thread asks mpv for a new frame while none is waiting.
POLL_S = 0.008
#: At most this often an overlay is drawn again (it changes far less often).
OVERLAY_MS = 25
#: An overlay this big (logical pixels) is drawn again at most this often.
BIG_OVERLAY_PX = 400_000
BIG_OVERLAY_MS = 100
#: mpv's overlay ids; mpv draws them in this order (lowest underneath).
OVERLAY_IDS = 64
#: Platforms where Qt cannot hand a window's GL to another thread.
_NO_THREAD_PLATFORMS = frozenset({"offscreen", "minimal", "vnc", "linuxfb"})
_GL_COLOR_BUFFER_BIT = 0x00004000
#: A window moved to a screen with another pixel ratio: its size in pixels
#: changed too (Qt 6.6 names the event; older ones send a screen change).
_PLATFORM_SURFACE = int(QEvent.Type.PlatformSurface)
_DPR_EVENTS = frozenset(int(t) for t in (
    getattr(QEvent.Type, "DevicePixelRatioChange", None),
    getattr(QEvent.Type, "ScreenChangeInternal", None)) if t is not None)


def _surface_format() -> QSurfaceFormat:
    fmt = QSurfaceFormat.defaultFormat()
    if sys.platform == "darwin":
        fmt.setVersion(4, 1)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(0)
    fmt.setStencilBufferSize(0)
    return fmt


def can_start() -> str:
    """Why this system cannot draw the picture on a thread of its own ("":
    it can try)."""
    if _libmpv is None:
        return "libmpv not loaded"
    name = QGuiApplication.platformName()
    if name in _NO_THREAD_PLATFORMS:
        return f"the {name} platform"
    try:
        if not QOpenGLContext.supportsThreadedOpenGL():
            return f"{name}: no OpenGL on other threads"
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    if video_mode.failed():
        return video_mode.failed()
    return ""


def make_video_surface(parent: QWidget | None = None, settings: Any | None = None) -> QWidget:
    """The player's video surface: drawn on a thread of its own unless that
    is switched off or cannot be done here, else the window's own
    (_MpvGLWidget, as it always was). Logged either way."""
    want, why = video_mode.wanted(settings)
    if want:
        why = can_start()
        if not why:
            return ThreadedVideoSurface(parent)
        log.warning("video: drawn on the window's thread (fallback: %s)", why)
        video_mode.note(video_mode.WINDOW, f"fallback: {why}")
    else:
        log.info("video: drawn on the window's thread (%s)", why)
        video_mode.note(video_mode.WINDOW, why)
    return _MpvGLWidget(parent)


def swap_for_fallback(old: QWidget, wire: Callable[[QWidget], None]) -> QWidget:
    """Put a window-thread surface (_MpvGLWidget) where *old* (a threaded
    surface that could not start) was: in its layout slot, with its
    children (labels over the picture) and its settings, wired by *wire*.
    Returns the new surface; *old* goes."""
    parent = old.parentWidget()
    new = _MpvGLWidget(parent)
    new.variant_picker = bool(getattr(old, "variant_picker", False))
    pref = getattr(old, "hwdec_pref", None)
    if pref is not None:
        new.hwdec_pref = pref
    new.EXTRA_OPTS = dict(getattr(old, "EXTRA_OPTS", {}) or {})
    new.setMinimumSize(old.minimumSize())
    new.setMaximumSize(old.maximumSize())
    new.setSizePolicy(old.sizePolicy())
    new.setMouseTracking(old.hasMouseTracking())
    box = getattr(old, "_box", None)
    for child in list(old.children()):
        if isinstance(child, QWidget) and child is not box and not child.isWindow():
            visible = child.isVisibleTo(old)
            child.setParent(new)
            child.setVisible(visible)
    lay = parent.layout() if parent is not None else None
    if lay is not None:
        lay.replaceWidget(old, new)
    else:
        new.setGeometry(old.geometry())
    wire(new)
    new.setVisible(old.isVisibleTo(parent) if parent is not None else True)
    old.hide()
    old.deleteLater()
    return new


def _alive(obj: Any) -> bool:
    """A Qt object whose C++ side still exists (a widget over the picture
    can be deleted at any time - a notice, a dialog, the pop-out's button)."""
    try:
        return obj is not None and not sip.isdeleted(obj)
    except TypeError:
        return False


# -- the native window ------------------------------------------------------------


class _VideoWindow(QWindow):
    """The native window the render thread draws into. It takes no input
    and no focus: the widgets under it - the surface, the overlays - get
    the mouse and the keys, exactly as before."""

    def __init__(self, changed: Callable[[], None],
                 going: Callable[["_VideoWindow"], None] | None = None) -> None:
        super().__init__()
        self._changed = changed
        self._going = going
        self.shown_once = False
        # Its native surface is about to go or went: never drawn into again
        # until it is made anew (a recreated window says SurfaceCreated).
        self.gone = False
        self.setSurfaceType(QSurface.SurfaceType.OpenGLSurface)
        self.setFormat(_surface_format())
        self.setFlags(self.flags() | Qt.WindowType.WindowTransparentForInput
                      | Qt.WindowType.WindowDoesNotAcceptFocus)
        # Another screen: another pixel ratio, so another size in pixels.
        self.screenChanged.connect(self._screen_changed)

    def _screen_changed(self, _screen) -> None:
        self._tell()

    def _tell(self) -> None:
        # Called from Qt's events: whatever the owner does must not raise
        # out of them (it may be on its way out itself).
        try:
            self._changed()
        except Exception as e:
            log.debug("video window: %s", e)

    def exposeEvent(self, e) -> None:
        if self.isExposed():
            self.shown_once = True
        self._tell()

    def resizeEvent(self, e) -> None:
        self._tell()

    def event(self, e) -> bool:
        t = int(e.type())
        if t in _DPR_EVENTS:
            self._tell()
        elif t == _PLATFORM_SURFACE:
            kind = e.surfaceEventType()
            if kind == e.SurfaceEventType.SurfaceAboutToBeDestroyed:
                # The native window goes (a window recreated: frameless, on
                # top, a reparent; or deleted - QWindowContainer destroys it
                # explicitly first): the render thread lets go of it first.
                self.gone = True
                if self._going is not None:
                    try:
                        self._going(self)
                    except Exception as ex:
                        log.debug("video window going: %s", ex)
            elif kind == e.SurfaceEventType.SurfaceCreated:
                self.gone = False
        return super().event(e)


class _Target:
    """Where the render thread draws: a window, its size in pixels, whether
    it is on screen."""
    __slots__ = ("window", "w", "h", "exposed")

    def __init__(self, window: QWindow | None, w: int, h: int, exposed: bool) -> None:
        self.window, self.w, self.h, self.exposed = window, w, h, exposed


_WAYLAND = [False]        # set when the first surface is made (the platform)


def _on_screen(win: QWindow) -> bool:
    """Whether to draw into *win*. Its own isExposed() is not enough: on
    Wayland a child window is exposed only while its parent is, and a
    compositor sends no frame callbacks to a surface covered entirely - the
    window under a fullscreen picture - so Qt took the parent, and with it
    the picture, for gone. Once shown, the picture goes on while its window
    is visible and the top-level window is neither hidden nor minimized."""
    if win.isExposed():
        return True
    # Elsewhere a window not exposed is one that cannot be seen (minimized,
    # occluded - macOS says so): nothing is drawn, and a vsync'd swap into
    # it could wait for as long as it stays hidden.
    if not _WAYLAND[0]:
        return False
    if not (getattr(win, "shown_once", False) and win.isVisible()):
        return False
    top = win.parent()
    while top is not None and top.parent() is not None:
        top = top.parent()
    return top is None or (top.isVisible()
                           and not top.windowStates() & Qt.WindowState.WindowMinimized)


def _usable(win: Any) -> bool:
    return _alive(win) and not getattr(win, "gone", False)


def _target_of(win: QWindow | None, shown: bool) -> _Target:
    if win is None or not _usable(win):
        return _Target(None, 0, 0, False)
    dpr = win.devicePixelRatio() or 1.0
    w, h = max(1, round(win.width() * dpr)), max(1, round(win.height() * dpr))
    return _Target(win, w, h, bool(shown and win.width() > 0 and win.height() > 0
                                   and _on_screen(win)))


# -- the render thread ------------------------------------------------------------


class _Renderer(QThread):
    """Owns the GL context and mpv's render context; renders each frame mpv
    has into the target window and swaps. Only *wake*, *set_target*,
    *set_blank* and *stop* are called from other threads."""

    def __init__(self, core, offscreen: QOffscreenSurface, fmt: QSurfaceFormat, name: str,
                 told: "_Told") -> None:
        super().__init__()
        self.setObjectName(name)
        self._core = core
        self._off = offscreen
        self._fmt = fmt
        self._told = told
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._target = _Target(None, 0, 0, False)
        self._blank = False
        self._redraw = True
        self._quit = False
        self._release: QWindow | None = None     # a window to let go of
        # makeCurrent again before the next frame: the GL context takes a
        # resize, a move or another screen there (on macOS Qt updates the
        # NSOpenGLContext's drawable in makeCurrent - so there it is made
        # current every frame, as Qt's own threaded render loop does).
        self._remake = True
        self._every_frame = sys.platform == "darwin"
        self._released = threading.Event()
        # Windows: mpv's update callback from its own thread crashed there
        # (see _MpvGLWidget._create_render_context), so this thread asks
        # mpv itself, at the display's pace.
        self._poll = (sys.platform == "win32"
                      or os.environ.get("DOPEIPTV_VIDEO_POLL") == "1")
        self.ready = threading.Event()
        self.error = ""
        self.frames = 0          # frames shown
        self.pending_since = 0.0  # a frame mpv has, not shown yet (monotonic)
        self.render_ms = 0.0     # the last render + swap
        self._glClear: Any = None
        self._glClearColor: Any = None
        self._proc_fn: Any = None
        self._ctx: QOpenGLContext | None = None
        self.rctx: Any = None

    # -- other threads ------------------------------------------------------------
    def wake(self) -> None:
        self._wake.set()

    def set_target(self, target: _Target) -> None:
        with self._lock:
            self._target = target
            self._redraw = True
            self._remake = True      # size, screen or window changed
        self._wake.set()

    def set_blank(self, blank: bool) -> None:
        with self._lock:
            self._blank = blank
            self._redraw = True
        self._wake.set()

    def redraw(self) -> None:
        with self._lock:
            self._redraw = True
        self._wake.set()

    def stop(self) -> None:
        self._quit = True
        self._wake.set()

    def release(self, window: QWindow, timeout: float = 1.0) -> bool:
        """The window's thread: *window*'s native surface is about to go.
        The render thread stops drawing into it and lets it go (its context
        moves to the offscreen surface) before this returns."""
        if not self.isRunning():
            return True
        with self._lock:
            if self._target.window is window:
                self._target = _Target(None, 0, 0, False)
            self._released.clear()
            self._release = window
        self._wake.set()
        ok = self._released.wait(timeout)
        if not ok:
            log.warning("video thread: did not let go of a window in %.1f s", timeout)
        return ok

    def _drop_target(self, window: Any) -> None:
        with self._lock:
            if self._target.window is window:
                self._target = _Target(None, 0, 0, False)

    def finish(self, *_args) -> None:
        """Stop and wait: before what it draws into or with goes."""
        self.stop()
        if self.isRunning() and not self.wait(5000):
            log.error("video thread: %s did not stop", self.objectName())

    # -- this thread --------------------------------------------------------------
    def _get_proc_address(self, _ctx, name: bytes) -> int:
        ctx = self._ctx
        if ctx is None:
            return 0
        try:
            addr = ctx.getProcAddress(name)
            v = int(addr) if addr else 0
        except Exception:
            v = 0
        if not v and sys.platform == "darwin":
            from ..core.platform_macos import gl_get_proc_address_fallback
            return gl_get_proc_address_fallback(name)
        return v

    def _fn(self, name: bytes, restype, *argtypes):
        addr = self._get_proc_address(None, name)
        if not addr:
            return None
        return ctypes.CFUNCTYPE(restype, *argtypes)(addr)

    def run(self) -> None:
        ctx = QOpenGLContext()
        ctx.setFormat(self._fmt)
        if not ctx.create():
            self.error = "the OpenGL context could not be made"
            self.ready.set()
            return
        if not ctx.makeCurrent(self._off):
            self.error = "the OpenGL context could not be made current"
            self.ready.set()
            return
        self._ctx = ctx
        try:
            self._proc_fn = _libmpv.MpvGlGetProcAddressFn(self._get_proc_address)
            rctx = _libmpv.MpvRenderContext(
                self._core, "opengl", opengl_init_params={"get_proc_address": self._proc_fn})
        except Exception as e:
            self.error = f"mpv render context: {type(e).__name__}: {e}"
            ctx.doneCurrent()
            self._ctx = None
            self.ready.set()
            return
        self._glClearColor = self._fn(b"glClearColor", None, ctypes.c_float, ctypes.c_float,
                                      ctypes.c_float, ctypes.c_float)
        self._glClear = self._fn(b"glClear", None, ctypes.c_uint)
        if not self._poll:
            rctx.update_cb = self._wake.set
        self.rctx = rctx
        # The context stays current on this thread from here on - on the
        # offscreen surface when there is no window to draw into: mpv may
        # do GL work in any render API call, update() included.
        self.ready.set()
        try:
            self._loop(ctx, rctx)
        except Exception as e:
            log.error("video thread: stopped by %s: %s", type(e).__name__, e)
            self._told.failed.emit(f"{type(e).__name__}: {e}")
        finally:
            # Not update_cb = None first: python-mpv drops the callback it
            # holds before it hands mpv the new one, so mpv's thread could
            # still call the freed one - a crash on closing multiview. free()
            # ends the callbacks itself; rctx keeps ours alive until then.
            try:
                ctx.makeCurrent(self._off)
                rctx.free()
            except Exception as e:
                log.info("video thread: render context free: %s", e)
            self.rctx = None
            try:
                ctx.doneCurrent()
            except Exception:
                pass
            self._ctx = None

    def _clear_black(self) -> None:
        if self._glClearColor is not None and self._glClear is not None:
            self._glClearColor(0.0, 0.0, 0.0, 1.0)
            self._glClear(_GL_COLOR_BUFFER_BIT)

    def _loop(self, ctx: QOpenGLContext, rctx) -> None:
        current = None              # the window made current last
        failed_makes = 0
        while not self._quit:
            if self._poll:
                self._wake.wait(POLL_S)
            else:
                self._wake.wait(0.5)
            self._wake.clear()
            if self._quit:
                break
            with self._lock:
                target, blank, redraw = self._target, self._blank, self._redraw
                remake, self._remake = self._remake or self._every_frame, False
                self._redraw = False
                release, self._release = self._release, None
            if release is not None:
                if current is release:
                    ctx.makeCurrent(self._off)
                    current = None
                self._released.set()
                if target.window is release:
                    continue
            if current is None and QOpenGLContext.currentContext() is not ctx:
                ctx.makeCurrent(self._off)
            try:
                frame = bool(rctx.update())
            except Exception:
                frame = True
            if frame and not self.pending_since:
                self.pending_since = time.monotonic()
            if target.window is None or not target.exposed:
                if current is not None:
                    ctx.makeCurrent(self._off)
                    current = None
                if frame:
                    # Nothing on screen to draw into: the frame is taken all
                    # the same, so mpv's clock never waits on us.
                    rctx.render(skip_rendering=True)
                    rctx.report_swap()
                    self.pending_since = 0.0
                continue
            if not (frame or redraw):
                continue
            if not _usable(target.window):
                # Its window went (or is going) before the window's thread
                # said so: nothing to draw into, an ordinary state.
                self._drop_target(target.window)
                if current is not None:
                    ctx.makeCurrent(self._off)
                    current = None
                if frame:
                    rctx.render(skip_rendering=True)
                    rctx.report_swap()
                    self.pending_since = 0.0
                continue
            t0 = time.monotonic()
            try:
                if (remake or current is not target.window
                        or QOpenGLContext.currentContext() is not ctx):
                    if not ctx.makeCurrent(target.window):
                        failed_makes += 1
                        if failed_makes in (1, 50):
                            log.warning("video thread: the window could not be drawn into (%d)",
                                        failed_makes)
                        ctx.makeCurrent(self._off)
                        current = None
                        continue
                    current = target.window
                failed_makes = 0
                if blank:
                    self._clear_black()
                else:
                    rctx.render(flip_y=True, opengl_fbo={
                        "fbo": int(ctx.defaultFramebufferObject()), "w": target.w, "h": target.h})
                ctx.swapBuffers(target.window)
            except RuntimeError as e:
                # sip: the window's wrapper was deleted under us. Not a
                # failure of the render thread - the window is gone.
                log.info("video thread: its window went (%s)", e)
                self._drop_target(target.window)
                ctx.makeCurrent(self._off)
                current = None
                continue
            if not blank:
                rctx.report_swap()
            self.render_ms = (time.monotonic() - t0) * 1000.0
            self.pending_since = 0.0
            self.frames += 1
            if self.frames == 1:
                self._told.shown.emit()
            if self.frames % 1500 == 0:
                log.debug("video thread: %d frames, last %.1f ms, %dx%d",
                          self.frames, self.render_ms, target.w, target.h)


class _Told(QObject):
    """What the render thread tells the window's thread (queued)."""
    shown = pyqtSignal()
    failed = pyqtSignal(str)
    vo = pyqtSignal(bool)


# -- what sits over the picture ---------------------------------------------------


class _Overlays(QObject):
    """The widgets over the picture, drawn into it by mpv (overlay-add),
    each one again only when it changes. Which widgets: the surface's own
    children, and its siblings stacked above it that overlap it."""

    _WATCH = frozenset({QEvent.Type.Paint, QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.Move,
                        QEvent.Type.Resize, QEvent.Type.ZOrderChange, QEvent.Type.ParentChange,
                        QEvent.Type.ChildAdded})

    def __init__(self, surface: "ThreadedVideoSurface") -> None:
        super().__init__(surface)
        self._surface = surface
        self._over: QWidget = surface        # the surface they are placed over
        self._watched: weakref.WeakSet = weakref.WeakSet()
        self._shown: list[tuple[int, QWidget, tuple]] = []  # (id, widget, (x, y, w, h))
        self._imgs: dict[int, list[QImage]] = {}
        self._dirty: set[int] = set()        # ids of widgets painted again
        self._full = True
        self._rendering = False              # our own render() paints them too
        self._stopped = False                # uninstalled: for good
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(OVERLAY_MS)
        self._timer.timeout.connect(self._flush)
        # New overlays, moved siblings: looked for again now and then.
        self._scan = QTimer(self)
        self._scan.setInterval(300)
        self._scan.timeout.connect(self.relook)
        self._scan.start()

    def place_over(self, w: QWidget) -> None:
        """The widget the overlays are placed over (the pop-out's mirror
        while popped out)."""
        self._over = w
        self.relook()

    def relook(self) -> None:
        if self._stopped:
            return
        self._full = True
        if not self._timer.isActive():
            self._timer.start()

    def _over_alive(self) -> QWidget | None:
        over = self._over
        if not _alive(over):
            # The pop-out's mirror went before the surface was told: back
            # over the surface itself.
            over = self._over = self._surface
        return over if _alive(over) else None

    def _candidates(self) -> list[QWidget]:
        over = self._over_alive()
        if over is None:
            return []
        box = getattr(over, "_box", None)
        out = [c for c in over.children()
               if isinstance(c, QWidget) and c is not box and not c.isWindow()]
        parent = over.parentWidget()
        if parent is not None:
            sibs = [c for c in parent.children() if isinstance(c, QWidget) and not c.isWindow()]
            try:
                i = sibs.index(over)
            except ValueError:
                i = len(sibs)
            out += sibs[i + 1:]
        return out

    def _watch(self, w: QWidget) -> None:
        if w in self._watched:
            return              # its children come in through ChildAdded
        for c in [w, *w.findChildren(QWidget)]:
            if c in self._watched or not _alive(c):
                continue
            self._watched.add(c)
            c.installEventFilter(self)
            # A widget deleted is taken off the picture at once - nothing
            # here keeps hold of it (or asks it anything) after that.
            c.destroyed.connect(self._gone)

    def _gone(self, obj: Any = None) -> None:
        # *obj* is the wrapper sip finds for the address now - not always
        # the one held here (a Python-made widget's is already marked
        # deleted): both are let go.
        for w in list(self._watched):
            if w is obj or not _alive(w):
                self._watched.discard(w)
        self._prune(obj)
        self.relook()

    def _prune(self, obj: Any = None) -> bool:
        """Take the deleted widgets (and *obj*, going) out of what is shown,
        their mpv overlays with them. Whether there were any."""
        dead = [(oid, w) for oid, w, _g in self._shown if w is obj or not _alive(w)]
        if not dead:
            return False
        core = self._surface.mpv if _alive(self._surface) else None
        gone = {oid for oid, _w in dead}
        for oid in gone:
            if core is not None:
                self._remove(core, oid)
            else:
                self._imgs.pop(oid, None)
        self._shown = [e for e in self._shown if e[0] not in gone]
        self._dirty -= gone
        return True

    def eventFilter(self, obj, event) -> bool:
        # An event filter must never raise: every widget here can be deleted
        # at any moment. Anything odd - look at them all again.
        # (getattr: a filter can be called on its way out, its state gone.)
        if getattr(self, "_rendering", True) or getattr(self, "_stopped", True):
            return False
        try:
            t = event.type()
            if t not in self._WATCH:
                return False
            if t == QEvent.Type.ChildAdded:
                child = event.child()
                if isinstance(child, QWidget) and _alive(child):
                    self._watch(child)
                self._full = True
            elif t == QEvent.Type.Paint:
                self._prune()
                for oid, w, _g in self._shown:
                    if w is obj or (isinstance(obj, QWidget) and w.isAncestorOf(obj)):
                        self._dirty.add(oid)
                        break
                else:
                    self._full = True
            else:
                self._full = True
            if not self._timer.isActive():
                self._timer.start()
        except Exception as e:
            self._full = True
            log.debug("video overlays: event: %s", e)
        return False

    def _geometry(self, w: QWidget) -> tuple[int, int, int, int] | None:
        over = self._over_alive()
        if over is None or not _alive(w):
            return None
        if not w.isVisible() or w.width() <= 0 or w.height() <= 0:
            return None
        if w.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen):
            return None
        tl = over.mapFromGlobal(w.mapToGlobal(QPoint(0, 0)))
        r = QRect(tl, w.size())
        if not r.intersects(over.rect()):
            return None
        return r.x(), r.y(), r.width(), r.height()

    def _flush(self) -> None:
        if self._stopped:
            return
        if self._over is not self._surface and not _alive(self._over):
            self._over_alive()
            self._full = True
        self._prune()
        core = self._surface.mpv if _alive(self._surface) else None
        if core is None:
            return
        try:
            if self._full:
                self._full = False
                want: list[tuple[QWidget, tuple]] = []
                for w in self._candidates():
                    if not _alive(w):
                        continue
                    self._watch(w)
                    g = self._geometry(w)
                    if g is not None:
                        want.append((w, g))
                want = want[:OVERLAY_IDS]
                old = {oid: (w, g) for oid, w, g in self._shown}
                shown = []
                for oid, (w, g) in enumerate(want):
                    prev = old.get(oid)
                    if prev is None or prev[0] is not w or prev[1] != g or oid in self._dirty:
                        self._add(core, oid, w, g)
                    shown.append((oid, w, g))
                for oid in sorted(set(old) - set(range(len(want)))):
                    self._remove(core, oid)
                self._shown = shown
            else:
                for oid, w, g in self._shown:
                    if oid in self._dirty:
                        if self._geometry(w) != g:
                            self._full = True
                            break
                        self._add(core, oid, w, g)
            # A big one changing all the time (the loading indicator over a
            # fullscreen picture) is drawn again less often.
            big = any(g[2] * g[3] > BIG_OVERLAY_PX for oid, _w, g in self._shown
                      if oid in self._dirty)
            self._timer.setInterval(BIG_OVERLAY_MS if big else OVERLAY_MS)
            self._dirty.clear()
        except Exception as e:
            # A widget deleted half-way: shown again from scratch.
            self._full = True
            log.debug("video overlays: %s", e)
        if self._full and not self._stopped:
            self._timer.start()

    def _add(self, core, oid: int, w: QWidget, g: tuple) -> None:
        over = self._over_alive()
        if over is None or not _alive(w):
            return
        dpr = over.devicePixelRatioF() or 1.0
        pw, ph = max(1, round(g[2] * dpr)), max(1, round(g[3] * dpr))
        img = QImage(pw, ph, QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        self._rendering = True
        try:
            w.render(p, QPoint(0, 0), QRegion(),
                     QWidget.RenderFlag.DrawChildren | QWidget.RenderFlag.DrawWindowBackground)
        finally:
            self._rendering = False
            p.end()
        keep = self._imgs.setdefault(oid, [])
        keep.append(img)
        del keep[:-2]            # the one mpv copies now, and the one before
        addr = int(img.constBits())
        core.command("overlay-add", oid, round(g[0] * dpr), round(g[1] * dpr), f"&{addr}", 0,
                     "bgra", pw, ph, img.bytesPerLine())

    def _remove(self, core, oid: int) -> None:
        try:
            core.command("overlay-remove", oid)
        except Exception:
            pass
        self._imgs.pop(oid, None)

    def clear(self) -> None:
        core = self._surface.mpv if _alive(self._surface) else None
        if core is not None:
            for oid, _w, _g in self._shown:
                self._remove(core, oid)
        self._shown = []
        self._imgs.clear()
        self._dirty.clear()
        self._full = True

    def stop(self) -> None:
        self._scan.stop()
        self._timer.stop()

    def uninstall(self) -> None:
        """For good (the surface falls back or goes): no filter left on any
        widget, nothing held."""
        self._stopped = True
        self.stop()
        self.clear()
        for w in list(self._watched):
            if _alive(w):
                try:
                    w.removeEventFilter(self)
                    w.destroyed.disconnect(self._gone)
                except (RuntimeError, TypeError):
                    pass
        self._watched.clear()


# -- the surface --------------------------------------------------------------------


class _Ending:
    """What a surface leaves running - its render thread, its mpv core -
    ended if the surface is destroyed without shutdown() (its destroyed
    signal: nothing is asked of the surface itself then, it is gone). The
    thread first: mpv's render context goes before its core."""

    def __init__(self) -> None:
        self.renderer: _Renderer | None = None
        self.core: Any = None

    def end(self, *_args) -> None:
        r, self.renderer = self.renderer, None
        if r is not None:
            r.finish()
        m, self.core = self.core, None
        if m is not None:
            try:
                m.terminate()
            except Exception:
                pass


_LIVE: "weakref.WeakSet[ThreadedVideoSurface]" = weakref.WeakSet()
_QUIT_HOOKED = [False]


def _stop_all() -> None:
    """At quit: every surface's render thread ends (a QThread destroyed while
    it runs aborts the process)."""
    for s in list(_LIVE):
        try:
            s.shutdown()
        except Exception as e:
            log.debug("video thread: shutdown at quit: %s", e)


def live_threads() -> int:
    """Render threads running now (tests, diagnostics)."""
    return sum(1 for s in list(_LIVE)
               if s._renderer is not None and s._renderer.isRunning())


class ThreadedVideoSurface(QWidget):
    """The video surface whose picture a thread of its own draws: the same
    signals and attributes the player and multiview use on _MpvGLWidget."""

    threaded = True
    frame_ready = pyqtSignal()           # never per frame here (parity only)
    playback_error = pyqtSignal(str)
    eof_event = pyqtSignal()
    video_mouse_press = pyqtSignal(object)
    video_mouse_move = pyqtSignal(object)
    video_mouse_release = pyqtSignal(object)
    video_dbl_click = pyqtSignal()
    video_key_press = pyqtSignal(object)
    video_key_release = pyqtSignal(object)
    #: This surface could not draw; the owner puts a window-thread surface
    #: in its place (swap_for_fallback). The reason as text.
    fallback = pyqtSignal(str)

    EXTRA_OPTS: dict = {}
    variant_picker = False

    _SEEK_KEYS = (Qt.Key.Key_Left, Qt.Key.Key_Right)
    _PLAYER_KEYS = (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down)
    _count = 0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        ThreadedVideoSurface._count += 1
        self._name = f"dopeiptv-video-{ThreadedVideoSurface._count}"
        _WAYLAND[0] = QGuiApplication.platformName().startswith("wayland")
        _LIVE.add(self)
        app = QGuiApplication.instance()
        if app is not None and not _QUIT_HOOKED[0]:
            _QUIT_HOOKED[0] = True
            app.aboutToQuit.connect(_stop_all)
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), QColor(0, 0, 0))
        self.setPalette(pal)
        self.mpv = None
        self._render_suspended = False   # parity with _MpvGLWidget
        self._blank = False
        self._vo = False                 # mpv has a picture to show
        self._failed = ""
        self._renderer: _Renderer | None = None
        self._offscreen: QOffscreenSurface | None = None
        self._told = _Told()
        self._told.shown.connect(self._first_frame)
        self._told.failed.connect(self._render_failed)
        self._told.vo.connect(self._vo_changed)
        # Where the picture goes: this surface's own window, or the pop-out's.
        self._win = _VideoWindow(self._window_changed, self._window_going)
        self._box = QWidget.createWindowContainer(self._win, self)
        self._box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._box.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._box.hide()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._box)
        self._mirror: ThreadedMirror | None = None
        self._overlays = _Overlays(self)
        self._starting = False
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(1000)
        self._watchdog.timeout.connect(self._check_stuck)
        self._started_at = 0.0
        self._ending = _Ending()
        self.destroyed.connect(self._ending.end)

    # -- what the player reads ----------------------------------------------------
    @property
    def _ctx(self):
        r = self._renderer
        return r.rctx if r is not None else None

    @property
    def _paint_state(self) -> str:
        r = self._renderer
        return "render" if r is not None and r.frames > 0 else ""

    @property
    def _paint_n(self) -> int:
        r = self._renderer
        return r.frames if r is not None else 0

    frames_shown = _paint_n

    # -- start --------------------------------------------------------------------
    def showEvent(self, e) -> None:
        super().showEvent(e)
        if self.mpv is None and not self._failed and not self._starting:
            self._starting = True
            # After the show, not inside it: a surface that cannot start is
            # replaced by its owner, which must not happen in its own event.
            QTimer.singleShot(0, self._start)
        self._window_changed()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._window_changed()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._window_changed()

    def ensure_started(self) -> None:
        """Start now if shown and not started (what play() needs before it
        loads a file)."""
        if self.mpv is None and not self._failed and self.isVisible():
            self._start()

    def _start(self) -> None:
        self._starting = False
        if self.mpv is not None or self._failed:
            return
        why = can_start()
        if why:
            self._give_up(why)
            return
        t0 = time.monotonic()
        try:
            self.mpv = build_core(self)
        except Exception as e:
            self.mpv = None
            self._give_up(f"mpv: {type(e).__name__}: {e}")
            return
        self._ending.core = self.mpv
        fmt = _surface_format()
        off = QOffscreenSurface(self.screen())
        off.setFormat(fmt)
        off.create()
        if not off.isValid():
            self._kill_core()
            self._give_up("no offscreen GL surface")
            return
        self._offscreen = off
        r = _Renderer(self.mpv, off, fmt, self._name, self._told)
        self._renderer = r
        self._ending.renderer = r
        r.start()
        if not r.ready.wait(START_TIMEOUT_S) or r.error:
            reason = r.error or "the render thread did not start"
            self._stop_renderer()
            self._kill_core()
            self._give_up(reason)
            return
        _register_error_callback(self.mpv, self.playback_error, self.eof_event)
        told = self._told

        def on_vo(_name, value) -> None:            # mpv's event thread
            try:
                told.vo.emit(bool(value))
            except RuntimeError:
                pass
        try:
            self.mpv.observe_property("vo-configured", on_vo)
        except Exception as e:
            log.warning("video thread: vo-configured not observed (%s)", e)
            self._vo = True
        self._started_at = time.monotonic()
        self._watchdog.start()
        video_mode.note(video_mode.THREAD)
        log.info("video: drawn on a thread of its own (%s, %s, ready in %.0f ms)",
                 self._name, QGuiApplication.platformName(), (time.monotonic() - t0) * 1000)
        self._sync_box()
        self._window_changed()

    def _give_up(self, reason: str) -> None:
        self._failed = reason
        self._overlays.uninstall()
        video_mode.note_failed(reason)
        log.warning("video: drawn on the window's thread (fallback: %s)", reason)
        self.fallback.emit(reason)

    def _kill_core(self) -> None:
        self._ending.core = None
        m, self.mpv = self.mpv, None
        if m is not None:
            try:
                m.terminate()
            except Exception:
                pass

    def _stop_renderer(self) -> None:
        self._ending.renderer = None
        r, self._renderer = self._renderer, None
        if r is not None:
            r.finish()

    # -- the picture: where it goes, whether it shows ---------------------------------
    def _live_mirror(self) -> "ThreadedMirror | None":
        """The pop-out's mirror - or none if it was deleted before
        drop_mirror() (its window going away with it)."""
        m = self._mirror
        if m is not None and not _alive(m):
            self._mirror = m = None
            self._overlays.place_over(self)
        return m

    def _window_changed(self) -> None:
        r = self._renderer
        if r is None or not _alive(self):
            return
        try:
            m = self._live_mirror()
            if m is not None:
                t = _target_of(m._win, m.isVisible() and m._box.isVisible())
            else:
                t = _target_of(self._win, self.isVisible() and self._box.isVisible())
        except RuntimeError:            # a window on its way out
            t = _Target(None, 0, 0, False)
        r.set_target(t)

    def _window_going(self, win: QWindow) -> None:
        if not _alive(self):
            # This surface is being destroyed (its window goes with it):
            # its thread and core end here, before the window does.
            self._ending.end()
            return
        r = self._renderer
        if r is not None:
            r.release(win)
        QTimer.singleShot(0, self._window_changed)

    def _vo_changed(self, on: bool) -> None:
        self._vo = on
        self._sync_box()

    def _sync_box(self) -> None:
        """The native window shows only while mpv has a picture: before the
        first frame, for sound alone and when stopped, what is under it (the
        loading indicator, black) is plain Qt."""
        show = self._vo and not self._blank and self.mpv is not None
        m = self._live_mirror()
        if m is not None:
            m._box.setVisible(show)
            self._box.hide()
        else:
            self._box.setVisible(show)
        self._overlays.relook()
        self._window_changed()

    def _first_frame(self) -> None:
        log.info("video thread: first frame shown (%s)", self._name)

    def _check_stuck(self) -> None:
        r = self._renderer
        if r is None or self._failed:
            return
        since = r.pending_since
        if since and r.frames == 0 and time.monotonic() - since > STUCK_S:
            self._render_failed("no frame could be shown")

    def _render_failed(self, reason: str) -> None:
        if self._failed:
            return
        log.error("video thread: %s", reason)
        self._watchdog.stop()
        self._overlays.stop()
        self._stop_renderer()
        self._kill_core()
        self._give_up(reason)

    def set_blank(self, blank: bool) -> None:
        self._blank = blank
        r = self._renderer
        if r is not None:
            r.set_blank(blank)
        self._sync_box()

    def update(self, *a) -> None:
        super().update(*a)
        r = self._renderer
        if r is not None and not a:
            r.redraw()

    # -- the pop-out ----------------------------------------------------------------
    def make_mirror(self, parent: QWidget) -> "ThreadedMirror":
        """The pop-out's surface: its own native window, which this
        surface's render thread draws into instead of its own while it is
        there. Nothing GL is moved between windows."""
        m = ThreadedMirror(self, parent)
        self._mirror = m
        self._overlays.place_over(m)
        self._sync_box()
        return m

    def drop_mirror(self) -> None:
        m, self._mirror = self._mirror, None
        self._overlays.place_over(self)
        self._sync_box()
        if m is not None:
            m._owner = None
            # Let go of its window now, not when it is deleted: the thread
            # may still be drawing the frame it began there.
            r = self._renderer
            if r is not None and _alive(m):
                r.release(m._win)
            if _alive(m):
                m.hide()
                m.deleteLater()

    # -- input (the widget itself gets it: the native window takes none) -------------
    def keyPressEvent(self, event) -> None:
        if (event.key() in self._PLAYER_KEYS
                and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            self.video_key_press.emit(event)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if (event.key() in self._SEEK_KEYS
                and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            self.video_key_release.emit(event)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def mousePressEvent(self, event) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.video_mouse_press.emit(event)

    def mouseMoveEvent(self, event) -> None:
        self.video_mouse_move.emit(event)

    def mouseReleaseEvent(self, event) -> None:
        self.video_mouse_release.emit(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.video_dbl_click.emit()

    # -- end ------------------------------------------------------------------------
    def shutdown(self) -> None:
        self._watchdog.stop()
        self._overlays.uninstall()
        self._stop_renderer()
        self._kill_core()
        off, self._offscreen = self._offscreen, None
        if off is not None:
            off.destroy()


class ThreadedMirror(QWidget):
    """The pop-out's video surface while the threaded surface's picture is
    shown there: a native window of its own, drawn by the owner's render
    thread. The mouse and the cursor are this widget's, as the old mirrors'."""

    video_mouse_press = pyqtSignal(object)
    video_mouse_move = pyqtSignal(object)
    video_mouse_release = pyqtSignal(object)
    video_dbl_click = pyqtSignal()

    def __init__(self, owner: ThreadedVideoSurface, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._owner: ThreadedVideoSurface | None = owner
        self.mpv = None
        self._ctx = None
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), QColor(0, 0, 0))
        self.setPalette(pal)
        self.setMouseTracking(True)
        self._win = _VideoWindow(self._changed, self._going)
        self._box = QWidget.createWindowContainer(self._win, self)
        self._box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._box.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._box.hide()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._box)

    def _changed(self) -> None:
        o = self._owner
        if o is not None:
            o._window_changed()

    def _going(self, win: QWindow) -> None:
        o = self._owner
        if o is not None:
            o._window_going(win)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._changed()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._changed()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._changed()

    def mousePressEvent(self, e) -> None:
        self.video_mouse_press.emit(e)

    def mouseMoveEvent(self, e) -> None:
        self.video_mouse_move.emit(e)

    def mouseReleaseEvent(self, e) -> None:
        self.video_mouse_release.emit(e)

    def mouseDoubleClickEvent(self, _e) -> None:
        self.video_dbl_click.emit()


# -- the packaging check -------------------------------------------------------------

#: --video-check's exit codes: drawn, could not try here, tried and failed.
CHECK_OK, CHECK_SKIPPED, CHECK_FAILED = 0, 3, 1
#: What it plays: a moving test picture from FFmpeg's lavfi, else a still
#: picture made here (a libmpv whose FFmpeg has no lavfi device).
CHECK_SOURCE = "av://lavfi:testsrc=size=320x240:rate=25"


def video_check(seconds: float = 20.0) -> int:
    """`--video-check`: can this machine draw the picture on a thread of its
    own? A small window plays a generated test picture until the render
    thread has shown frames. CHECK_SKIPPED when it cannot try (no OpenGL on
    other threads, no window on screen), CHECK_FAILED when it could and drew
    nothing - the app would fall back to the window's thread there."""
    import tempfile

    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    why = can_start()
    if why:
        log.info("video-check: skipped (%s)", why)
        return CHECK_SKIPPED
    host = QWidget()
    host.setWindowTitle("dopeIPTV video check")
    host.resize(320, 240)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    surface = ThreadedVideoSurface(host)
    lay.addWidget(surface)
    gave_up: list[str] = []
    errors: list[str] = []
    surface.fallback.connect(gave_up.append)
    surface.playback_error.connect(errors.append)
    host.show()
    end = time.monotonic() + seconds

    def spin(until: Callable[[], bool]) -> bool:
        while time.monotonic() < end:
            app.processEvents()
            if until():
                return True
            time.sleep(0.02)
        return False

    still = ""
    try:
        if not spin(lambda: surface.mpv is not None or bool(gave_up)) or gave_up:
            reason = gave_up[0] if gave_up else "did not start"
            log.info("video-check: skipped (%s)", reason)
            return CHECK_SKIPPED
        surface.mpv.play(CHECK_SOURCE)
        spin(lambda: surface.frames_shown >= 3 or bool(errors) or bool(gave_up))
        if errors and not surface.frames_shown and not gave_up:
            # No lavfi in this FFmpeg: a still picture made here instead.
            img = QImage(320, 240, QImage.Format.Format_RGB32)
            img.fill(QColor(40, 120, 200))
            fd, still = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            img.save(still)
            errors.clear()
            surface.mpv["image-display-duration"] = "inf"
            surface.mpv.play(still)
            spin(lambda: surface.frames_shown >= 1 or bool(errors) or bool(gave_up))
        shown = surface.frames_shown
        if shown >= 1 and not gave_up:
            log.info("video-check: OK (%d frames drawn on the render thread, %s)",
                     shown, QGuiApplication.platformName())
            return CHECK_OK
        if not surface._win.isExposed():
            log.info("video-check: skipped (the window was never on screen)")
            return CHECK_SKIPPED
        log.error("video-check: FAILED (%s)", gave_up[0] if gave_up else
                  (f"playback error: {errors[0]}" if errors else f"{shown} frames in {seconds:.0f} s"))
        return CHECK_FAILED
    finally:
        surface.shutdown()
        host.close()
        app.processEvents()
        if still:
            try:
                os.unlink(still)
            except OSError:
                pass
