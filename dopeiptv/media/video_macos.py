"""macOS: the player's picture drawn on a thread of its own with an
NSOpenGLContext of the app's own, the way Apple documents multithreaded
OpenGL (what a CVDisplayLink-driven NSOpenGLView does).

Qt's own context cannot be used on the render thread there: its
makeCurrent attaches the context to the window's view
(-[NSOpenGLContext setView:]), which AppKit allows on the main thread only
- on any other thread AppKit stops the process (SIGTRAP). So on macOS
media/video_thread.py's render thread draws with this one instead (`MacGL`,
the strategy its renderer talks to there):

- the main thread makes it (`prepare`: a pixel format of its own - OpenGL
  4.1 core, double-buffered, a hardware renderer where there is one), puts
  it on the native window's NSView (`attach`: setView, Retina's backing
  pixels asked for) and brings it up to date then and whenever the view's
  size, place, window, screen or backing scale changed or it is shown
  again (update), lets go of a view that is going (`detach`) and of the
  context (`close`: clearDrawable);
- the render thread makes it current, draws mpv's frame into its default
  framebuffer at the view's size in backing pixels and flushes it (`begin`,
  `present`, `end`), and frees mpv's render context with it current
  (`free`).

Both sides hold the context's CGL lock while they use it, so an update
never lands in the middle of a frame. The window's thread never waits for
the render thread: it only tries the context's gate, and a change it could
not make now (the render thread is in a frame) is made a moment later
(`attach` returns None) while the picture goes on as it was. Every flush,
update, setView and clearDrawable of every context is made one at a time
(`_FLUSH`): NSOpenGLContext is not re-entrant even across contexts, which is
why Qt's own context does the same. So the flush must not wait for vsync
(swap interval 0) - one cell of multiview would hold every other one up -
and mpv's render does not wait for the frame's display time while the lock
is held either: the core is built with mpv's timing offset 0 instead
(video_surface.build_core's *tuned*), so mpv hands each frame over at its
time and it is flushed then. A window on screen is composited whole, so
there is no tearing.

The view it draws into is the app's own as well (`PictureView`), not a Qt
child window: Qt's cocoa plugin takes a child window under the pointer for
the one a mouse move is for and drops the move (no hover reached the
widgets over the picture), and a view of the app's own can follow its
placeholder into whichever native view holds it (media/video_thread's
_Placement). Its hitTest: says nil and it has no tracking area: the mouse
is Qt's, as with no native window at all.

Anything that fails - no PyObjC, no OpenGL framework, no pixel format or
context, a view that would not take the context, an error while drawing -
is a reason (`unavailable`, `MacGLError`, `prepare`'s answer), and the
surface falls back to the window's thread with it. AppKit is imported only
when a context is made, and only on macOS; the pure parts (the pixel
format's attributes, the backing size, what a change needs) and the whole
order of calls are tested anywhere with stand-ins.
"""

from __future__ import annotations

import contextlib
import ctypes
import math
import sys
import threading
import time
from typing import Any, NamedTuple

from ..core.log import log

OPENGL_FRAMEWORK = "/System/Library/Frameworks/OpenGL.framework/OpenGL"

# NSOpenGL.h: pixel format attributes and profiles.
PFA_DOUBLE_BUFFER = 5
PFA_COLOR_SIZE = 8
PFA_ACCELERATED = 73
PFA_ALLOW_OFFLINE_RENDERERS = 96
PFA_OPENGL_PROFILE = 99
PROFILE_3_2_CORE = 0x3200
PROFILE_4_1_CORE = 0x4100
#: CGLTypes.h: kCGLCPSwapInterval.
CGL_CP_SWAP_INTERVAL = 222
GL_RENDERER, GL_VERSION = 0x1F01, 0x1F02

#: The pixel formats tried, best first: OpenGL 4.1 core (what the app asks
#: Qt for on macOS) on a hardware renderer, then on any, then 3.2 core.
FORMATS: tuple[tuple[int, bool], ...] = (
    (PROFILE_4_1_CORE, True), (PROFILE_4_1_CORE, False), (PROFILE_3_2_CORE, False))

#: How long the window's thread tries a lock before it leaves a change for
#: a moment later.
TRY_S = 0.002
#: How long the render thread waits for its context's gate (the window's
#: thread holds it only for one call into AppKit).
WAIT_S = 2.0
#: How long the window's thread waits to take the context off a view that
#: is going, or off its view as it goes itself.
LET_GO_S = 1.0
#: The context's updates in the log at most this often (a resize is many).
UPDATE_SAY_S = 2.0
#: NSWindowOrderingMode's NSWindowAbove: a subview put over the others.
NS_WINDOW_ABOVE = 1

SET, UPDATE = "set", "update"

#: Every flush, update, setView and clearDrawable of every context, one at
#: a time (NSOpenGLContext is not re-entrant, even across contexts).
_FLUSH = threading.Lock()
#: Contexts a render thread that never ended may still use: never freed.
_KEPT: list[Any] = []


class MacGLError(Exception):
    """The macOS context could not do what was asked: the surface falls
    back to the window's thread with this as the reason. Not a
    RuntimeError - the render loop takes those for a window that went."""


class Place(NamedTuple):
    """Where the context draws: the view, its window and screen (pointers),
    its frame's origin in its superview, its size in backing pixels."""
    view: int
    window: int
    screen: int
    x: float
    y: float
    w: int
    h: int


def pixel_format_attrs(profile: int, accelerated: bool) -> list[int]:
    """NSOpenGLPixelFormat's attributes, 0-terminated: the profile,
    double-buffered, 24-bit colour, no depth or stencil (mpv draws a flat
    picture), GPUs that drive no display allowed (as Qt's own context does:
    the automatic graphics switching of a Mac with two)."""
    attrs = [PFA_OPENGL_PROFILE, profile, PFA_DOUBLE_BUFFER, PFA_COLOR_SIZE, 24,
             PFA_ALLOW_OFFLINE_RENDERERS]
    if accelerated:
        attrs.append(PFA_ACCELERATED)
    attrs.append(0)
    return attrs


def describe(profile: int, accelerated: bool) -> str:
    v = "4.1" if profile == PROFILE_4_1_CORE else "3.2"
    return f"OpenGL {v} core" + (", hardware renderer" if accelerated else "")


def backing_size(w: float, h: float, scale: float) -> tuple[int, int]:
    """A view's size in backing pixels - what its GL drawable has with
    wantsBestResolutionOpenGLSurface (twice its points on a Retina screen).
    Never 0: a view not laid out yet is still drawn 1 x 1."""
    try:
        s = float(scale)
    except (TypeError, ValueError):
        s = 1.0
    if not math.isfinite(s) or s <= 0:
        s = 1.0
    return max(1, round(float(w) * s)), max(1, round(float(h) * s))


def change_for(attached: Place | None, now: Place, shown_again: bool) -> str:
    """What the context needs on the main thread before it draws into
    *now*: SET for another view (or none yet), UPDATE when the view's size,
    place, window, screen or scale changed or it is shown again, "" when
    nothing did."""
    if attached is None or attached.view != now.view:
        return SET
    if shown_again or attached != now:
        return UPDATE
    return ""


# -- what this Mac has ---------------------------------------------------------------

_WHY: list[str] = []
_GL: list[Any] = []


def opengl() -> tuple[Any, str]:
    """The OpenGL framework through ctypes (its CGL calls and the GL
    functions mpv asks for), loaded once; (None, why) when it cannot be."""
    if _GL:
        return _GL[0], ""
    try:
        lib = ctypes.CDLL(OPENGL_FRAMEWORK)
        for name in ("CGLLockContext", "CGLUnlockContext"):
            fn = getattr(lib, name)
            fn.argtypes = [ctypes.c_void_p]
            fn.restype = ctypes.c_int
        lib.CGLGetCurrentContext.argtypes = []
        lib.CGLGetCurrentContext.restype = ctypes.c_void_p
        lib.CGLSetParameter.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                        ctypes.POINTER(ctypes.c_int)]
        lib.CGLSetParameter.restype = ctypes.c_int
    except (OSError, AttributeError) as e:
        return None, f"the OpenGL framework: {e}"
    _GL.append(lib)
    return lib, ""


def _look() -> str:
    if sys.platform != "darwin":
        return "not macOS"
    try:
        import AppKit
        import objc
        AppKit.NSOpenGLContext, AppKit.NSOpenGLPixelFormat, objc.objc_object  # noqa: B018
    except Exception as e:
        return f"PyObjC's AppKit: {type(e).__name__}: {e}"
    return opengl()[1]


def unavailable() -> str:
    """Why this Mac cannot draw the picture on a thread of its own ("": it
    can try): PyObjC's AppKit and the OpenGL framework, looked for once."""
    if not _WHY:
        _WHY.append(_look())
    return _WHY[0]


def _main_only(what: str) -> None:
    # AppKit allows setView, update and clearDrawable (and a view's
    # geometry) on the main thread only, and stops the process elsewhere:
    # refused here instead, as a reason to fall back.
    if threading.current_thread() is not threading.main_thread():
        raise MacGLError(f"{what} asked off the main thread")


# -- the context ---------------------------------------------------------------------


class _Held:
    """A GL call outside a frame (mpv's update, its render context made or
    skipped): the context's gate and CGL lock held around it."""
    __slots__ = ("_gl",)

    def __init__(self, gl: "MacGL") -> None:
        self._gl = gl

    def __enter__(self) -> None:
        gl = self._gl
        if not gl._gate.acquire(timeout=WAIT_S):
            raise MacGLError("the OpenGL context stayed busy")
        try:
            gl._lock()
        except BaseException:
            gl._gate.release()
            raise

    def __exit__(self, *_exc: Any) -> None:
        gl = self._gl
        try:
            gl._unlock()
        finally:
            gl._gate.release()


class MacGL:
    """The NSOpenGLContext the render thread draws with on macOS.

    The window's thread: prepare, attach, detach, close. The render thread:
    create, section, begin, fbo, present, end, free, abandon (idle and keep
    are nothing here: the context stays current on that thread, and the
    window's thread decides which view it draws into). *appkit*, *objc_mod*
    and *lib* (the OpenGL framework) stand in for the real ones in tests."""

    #: mpv's render does not wait for the frame's display time while the
    #: CGL lock is held (see the module's docstring).
    render_args: dict[str, Any] = {"block_for_target_time": False}

    def __init__(self, appkit: Any = None, objc_mod: Any = None, lib: Any = None) -> None:
        self._appkit = appkit
        self._objc = objc_mod
        self._lib = lib
        self._ctx: Any = None
        self._cgl: int | None = None
        self._gate = threading.Lock()
        self._place: Place | None = None
        self._window: Any = None       # the QWindow whose view the context is on
        self._seen = False             # that view was on screen at the last attach
        self._running = False          # the render thread holds it (create .. free)
        self._open: Any = None         # the frame's autorelease pool
        self._said_update = 0.0
        self._updates = 0
        self.changes = 0               # setViews and updates made (a new drawable)
        self.info = ""                 # the renderer, for the log
        self.format = ""

    # -- the window's thread ----------------------------------------------------------
    def prepare(self) -> str:
        """The pixel format and the context ("" when made, else why not)."""
        _main_only("the NSOpenGLContext")
        if self._appkit is None or self._objc is None:
            try:
                import AppKit
                import objc
            except Exception as e:
                return f"PyObjC's AppKit: {type(e).__name__}: {e}"
            self._appkit, self._objc = AppKit, objc
        if self._lib is None:
            lib, why = opengl()
            if lib is None:
                return why
            self._lib = lib
        ak = self._appkit
        with self._pool():
            pf, chosen = None, FORMATS[0]
            for chosen in FORMATS:
                pf = ak.NSOpenGLPixelFormat.alloc().initWithAttributes_(
                    pixel_format_attrs(*chosen))
                if pf is not None:
                    break
            if pf is None:
                return "no OpenGL pixel format (4.1 or 3.2 core)"
            ctx = ak.NSOpenGLContext.alloc().initWithFormat_shareContext_(pf, None)
        if ctx is None:
            return "the NSOpenGLContext could not be made"
        self._ctx = ctx
        self.format = describe(*chosen)
        return ""

    def attach(self, target: Any) -> Any:
        """The context on *target*'s view as it is now: put there (setView)
        or brought up to date (update) when that changed. Returns the target
        the render thread draws into - its size in backing pixels - or None
        while the render thread is in a frame: tried again a moment later,
        the picture going on meanwhile as it was."""
        win = target.window
        if win is None or not target.exposed:
            self._seen = False         # shown again: an update first
            return target
        _main_only("the picture's view")
        ctx = self._ctx
        if ctx is None:
            raise MacGLError("no OpenGL context")
        view, place = self._place_of(target)
        if view is None:               # the picture's own view not made yet
            return type(target)(win, target.w, target.h, False)
        out = type(target)(win, place.w, place.h, True)
        change = (change_for(self._place, place, not self._seen)
                  if win is self._window else SET)
        why = "" if change != SET else (
            "first" if self._place is None else "another view"
            if self._place.view != place.view else "again")
        if not change and self._id(ctx.view()) != place.view:
            # AppKit let go of it (the view left its window a moment): on
            # it again.
            change, why = SET, "lost"
        if not change:
            return out
        if not self._gate.acquire(timeout=TRY_S):
            return None
        try:
            self._lock()
            try:
                if not _FLUSH.acquire(timeout=TRY_S):
                    return None
                try:
                    with self._pool():
                        if change == SET:
                            view.setWantsBestResolutionOpenGLSurface_(True)
                            ctx.setView_(view)
                            if self._id(ctx.view()) != place.view:
                                raise MacGLError("the view did not take the OpenGL context")
                        # After setView too, as SDL's context does: the
                        # drawable as the view is now.
                        ctx.update()
                finally:
                    _FLUSH.release()
            finally:
                self._unlock()
            was, self._place, self._window, self._seen = self._place, place, win, True
            self.changes += 1
        finally:
            self._gate.release()
        self._say(change, why, was, place)
        return out

    def forget(self) -> None:
        """Put on the view again at the next attach (its view was moved or
        raised: AppKit may have let go of the drawable)."""
        self._place = None

    def _say(self, change: str, why: str, was: Place | None, now: Place) -> None:
        """Each attach in the log, an update at most every UPDATE_SAY_S (a
        window being resized updates on every step)."""
        size = f"{now.w}x{now.h} px"
        if change == SET:
            log.info("video thread: the context on view %#x (%s; window %#x, %s)",
                     now.view, why, now.window, size)
            self._said_update = 0.0
            return
        what = ("shown again" if was is None or was == now else
                "another window" if was.window != now.window else
                "another screen" if was.screen != now.screen else
                "resized" if (was.w, was.h) != (now.w, now.h) else "moved")
        self._updates += 1
        t = time.monotonic()
        if t - self._said_update >= UPDATE_SAY_S:
            log.info("video thread: the context updated (%s, %s; %d since)", what, size,
                     self._updates)
            self._said_update, self._updates = t, 0

    def detach(self, window: Any) -> None:
        """*window*'s view is going: the context lets go of it first (the
        render thread has let go of the window already - the renderer's
        release)."""
        if window is None or window is not self._window:
            return
        _main_only("letting go of the picture's view")
        self._clear("a view that is going")

    def close(self) -> None:
        """Once the render thread has ended: the context off its view and
        let go of here, never on the render thread."""
        ctx = self._ctx
        if ctx is None:
            return
        if self._running or threading.current_thread() is not threading.main_thread():
            # The render thread did not end (the renderer's finish waited
            # for it): nothing it may still use is taken from under it.
            log.warning("video thread: the OpenGL context is still in use - kept")
            _KEPT.append(self)
            return
        if self._clear("the context going"):
            self._ctx = None
        else:
            _KEPT.append(self)

    def _clear(self, what: str) -> bool:
        ctx = self._ctx
        if ctx is None:
            return True
        if not self._gate.acquire(timeout=LET_GO_S):
            log.warning("video thread: the OpenGL context stayed busy (%s)", what)
            return False
        try:
            self._lock()
            try:
                if not _FLUSH.acquire(timeout=LET_GO_S):
                    log.warning("video thread: another OpenGL context stayed busy (%s)", what)
                    return False
                try:
                    with self._pool():
                        ctx.clearDrawable()
                finally:
                    _FLUSH.release()
            finally:
                self._unlock()
            was = self._place
            self._place, self._window, self._seen = None, None, False
        finally:
            self._gate.release()
        if was is not None:
            log.info("video thread: the context let go of view %#x (%s)", was.view, what)
        return True

    def _place_of(self, target: Any) -> tuple[Any, Place]:
        win = target.window
        view = getattr(win, "nsview", None)
        if view is None and hasattr(win, "nsview"):
            return None, Place(0, 0, 0, 0.0, 0.0, target.w, target.h)
        ptr = self._id(view) if view is not None else int(win.winId())
        if view is None:
            view = self._proxy(ptr)
        nswin = view.window()
        bounds = view.bounds().size
        frame = view.frame().origin
        if nswin is not None:
            scale = nswin.backingScaleFactor()
            screen = nswin.screen()
        else:
            scale, screen = target.window.devicePixelRatio(), None
        if bounds.width > 0 and bounds.height > 0:
            w, h = backing_size(bounds.width, bounds.height, scale)
        else:
            w, h = target.w, target.h
        return view, Place(ptr, self._id(nswin), self._id(screen),
                           float(frame.x), float(frame.y), w, h)

    # -- the render thread ------------------------------------------------------------
    def create(self) -> str:
        """Current on this thread from here on ("" or why not)."""
        ctx = self._ctx
        if ctx is None:
            return "no OpenGL context"
        try:
            with self._pool():
                ctx.makeCurrentContext()
            cgl = self._lib.CGLGetCurrentContext()
            if not cgl:
                return "the OpenGL context could not be made current"
            self._cgl = cgl
            self._running = True
            interval = ctypes.c_int(0)
            self._lib.CGLSetParameter(cgl, CGL_CP_SWAP_INTERVAL, ctypes.byref(interval))
        except Exception as e:
            return f"NSOpenGLContext: {type(e).__name__}: {e}"
        self.info = self._renderer_name()
        return ""

    def get_proc_address(self, name: bytes) -> int:
        try:
            return ctypes.cast(getattr(self._lib, name.decode("ascii")),
                               ctypes.c_void_p).value or 0
        except (AttributeError, OSError, UnicodeDecodeError, ctypes.ArgumentError, TypeError):
            return 0

    def idle(self) -> None:
        pass

    def keep(self) -> None:
        pass

    def section(self) -> _Held:
        return _Held(self)

    def begin(self, window: Any, again: bool) -> bool | None:
        """A frame into *window*: the gate and the CGL lock taken, an
        autorelease pool, the context current. None when the context is not
        on that window's view (the window's thread is putting it there): no
        frame now, and no failure."""
        if self._ctx is None or window is not self._window:
            return None
        if not self._gate.acquire(timeout=WAIT_S):
            return None
        if window is not self._window or self._ctx is None:
            self._gate.release()
            return None
        locked = False
        try:
            self._lock()
            locked = True
            pool = self._pool()
            pool.__enter__()
            self._open = pool
            self._ctx.makeCurrentContext()
        except Exception as e:
            self._end_frame(locked)
            raise MacGLError(f"makeCurrentContext: {type(e).__name__}: {e}") from e
        return True

    def fbo(self) -> int:
        return 0

    def present(self, _window: Any) -> None:
        try:
            with _FLUSH:
                self._ctx.flushBuffer()
        except Exception as e:
            raise MacGLError(f"flushBuffer: {type(e).__name__}: {e}") from e

    def end(self, ok: bool | None) -> None:
        if ok:
            self._end_frame(True)

    def free(self, rctx: Any) -> None:
        """At the render thread's end: mpv's render context freed with this
        context current, then nothing current on this thread. Its view is
        let go of on the window's thread (close). Freed even if the gate
        stayed busy: libmpv's core must not go before its render context."""
        held = self._gate.acquire(timeout=WAIT_S)
        try:
            if held:
                self._lock()
            try:
                with self._pool():
                    self._ctx.makeCurrentContext()
                rctx.free()
            except Exception as e:
                log.info("video thread: render context free: %s", e)
            finally:
                if held:
                    self._unlock()
        finally:
            if held:
                self._gate.release()
        self.abandon()

    def abandon(self) -> None:
        try:
            self._appkit.NSOpenGLContext.clearCurrentContext()
        except Exception:
            pass
        self._running = False

    # -- the pieces -------------------------------------------------------------------
    def _lock(self) -> None:
        if self._cgl:
            self._lib.CGLLockContext(self._cgl)

    def _unlock(self) -> None:
        if self._cgl:
            self._lib.CGLUnlockContext(self._cgl)

    def _end_frame(self, locked: bool) -> None:
        pool, self._open = self._open, None
        try:
            if pool is not None:
                pool.__exit__(None, None, None)
        finally:
            try:
                if locked:
                    self._unlock()
            finally:
                self._gate.release()

    def _pool(self) -> Any:
        make = getattr(self._objc, "autorelease_pool", None)
        return make() if make is not None else contextlib.nullcontext()

    def _proxy(self, ptr: int) -> Any:
        objc = self._objc
        try:
            return objc.objc_object(c_void_p=ptr)
        except TypeError:
            return objc.objc_object(c_void_p=ctypes.c_void_p(ptr))

    def _id(self, obj: Any) -> int:
        if obj is None:
            return 0
        try:
            return int(self._objc.pyobjc_id(obj))
        except Exception:
            return id(obj)

    def _renderer_name(self) -> str:
        try:
            gs = self._lib.glGetString
            gs.restype = ctypes.c_char_p
            gs.argtypes = [ctypes.c_uint]
            name = (gs(GL_RENDERER) or b"").decode("utf-8", "replace")
            version = (gs(GL_VERSION) or b"").decode("utf-8", "replace")
        except Exception:
            return ""
        return f"{name}, OpenGL {version}" if name else ""


# -- the picture's own view ------------------------------------------------------------

_VIEW_CLASS: dict[int, Any] = {}


def view_class(appkit: Any) -> Any:
    """The NSView class of the picture's own view (made once per process -
    an Objective-C class name is the process's): it is never the mouse's.
    hitTest: says nil, so clicks, the wheel and drags go to the view under
    it - Qt's - and it has no tracking area, so Qt's own sees every mouse
    move and enter/leave over the picture. A child QWindow could not do
    that: Qt's cocoa plugin takes the child window under the pointer for
    the one the move is for (QCocoaWindow::childWindowAt), transparent for
    input or not, and drops it."""
    cls = _VIEW_CLASS.get(id(appkit))
    if cls is None:
        class DopeIPTVPictureView(appkit.NSView):  # type: ignore[misc, name-defined]
            def hitTest_(self, _point):
                return None

            def acceptsFirstMouse_(self, _event):
                return False

            def isOpaque(self):
                return True
        cls = _VIEW_CLASS[id(appkit)] = DopeIPTVPictureView
    return cls


class PictureView:
    """macOS: the native view the picture is drawn into, the app's own -
    made the first time it is shown, put into the NSView of its nearest
    native ancestor at the place its placeholder widget has there
    (media/video_thread._Placement says where), on top of that view's other
    subviews, hidden while the placeholder is. The window's thread only.

    What the render thread and MacGL ask of a window (`winId`,
    `devicePixelRatio`, `isExposed`, `gone`) it answers; `nsview` is the
    view itself."""

    native_picture = True

    def __init__(self, appkit: Any = None, objc_mod: Any = None) -> None:
        self._appkit = appkit
        self._objc = objc_mod
        self.nsview: Any = None
        self.gone = False
        self.shown = False             # on a host view and not hidden
        self.on_screen = False         # and its window on screen (the last target)
        self.scale = 1.0
        self._host = 0
        self._host_view: Any = None
        self._frame: tuple[int, int, int, int] | None = None

    def winId(self) -> int:
        return self._id(self.nsview) if self.nsview is not None else 0

    def devicePixelRatio(self) -> float:
        return self.scale

    def isExposed(self) -> bool:
        return self.on_screen

    def place(self, host: int, rect: tuple[int, int, int, int], visible: bool) -> list[str]:
        """Over *rect* (points, in the host view's flipped coordinates) of
        the NSView *host* (a pointer; 0: none), shown or hidden. What
        changed: "made", "host", "raised", "frame", "shown", "hidden"."""
        _main_only("the picture's view")
        changes: list[str] = []
        if self.gone:
            return changes
        if not host:
            visible = False
        if self.nsview is None:
            if not visible:
                return changes         # made the first time it shows
            self._make()
            changes.append("made")
        v = self.nsview
        with self._pool():
            if host and host != self._host:
                hv = self._proxy(host)
                if v.superview() is not None:
                    v.removeFromSuperview()
                hv.addSubview_positioned_relativeTo_(v, NS_WINDOW_ABOVE, None)
                self._host, self._host_view, self._frame = host, hv, None
                changes.append("host")
            elif host and self._host_view is not None:
                subs = self._host_view.subviews()
                n = len(subs) if subs is not None else 0
                if n and self._id(subs[n - 1]) != self._id(v):
                    # A view put over it since (a widget made native): the
                    # picture would be under it, black.
                    self._host_view.addSubview_positioned_relativeTo_(v, NS_WINDOW_ABOVE, None)
                    changes.append("raised")
            if host and rect != self._frame:
                x, y, w, h = rect
                v.setFrame_(((x, y), (w, h)))
                self._frame = rect
                changes.append("frame")
            hide = not visible
            if bool(v.isHidden()) != hide:
                v.setHidden_(hide)
                changes.append("hidden" if hide else "shown")
        self.shown = bool(visible and self._host)
        return changes

    def unhost(self) -> None:
        """Out of its host view (that view is going); placed again later."""
        v = self.nsview
        if v is not None and threading.current_thread() is threading.main_thread():
            with self._pool():
                if v.superview() is not None:
                    v.removeFromSuperview()
        self._host, self._host_view, self._frame = 0, None, None
        self.shown = self.on_screen = False

    def remove(self) -> None:
        """For good (its surface goes or falls back): out of its window."""
        if self.gone:
            return
        self.unhost()
        self.gone = True
        if threading.current_thread() is threading.main_thread():
            self.nsview = None

    def _make(self) -> None:
        if self._appkit is None or self._objc is None:
            import AppKit
            import objc
            self._appkit, self._objc = AppKit, objc
        cls = view_class(self._appkit)
        v = cls.alloc().initWithFrame_(((0, 0), (1, 1)))
        if v is None:
            raise MacGLError("the picture's view could not be made")
        v.setWantsLayer_(True)
        v.setHidden_(True)
        self.nsview = v
        log.info("video: the picture's own view %#x - the mouse goes through it to the widgets "
                 "(hitTest: nil, no tracking area)", self._id(v))

    def _pool(self) -> Any:
        make = getattr(self._objc, "autorelease_pool", None)
        return make() if make is not None else contextlib.nullcontext()

    def _proxy(self, ptr: int) -> Any:
        if self._objc is None:
            import objc as _objc
            self._objc = _objc
        bridge = self._objc
        try:
            return bridge.objc_object(c_void_p=ptr)
        except TypeError:
            return bridge.objc_object(c_void_p=ctypes.c_void_p(ptr))

    def _id(self, obj: Any) -> int:
        if obj is None:
            return 0
        try:
            return int(self._objc.pyobjc_id(obj))
        except Exception:
            return id(obj)
