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

The render thread's GL context is a strategy of its own: Qt's
QOpenGLContext, made and current on that thread (`_QtGL`, Linux and
Windows), or on macOS an NSOpenGLContext of the app's own
(media/video_macos.py's `MacGL`) - there Qt's context cannot be made
current on another thread (AppKit's main-thread rule), so that one is put
on its view by the window's thread (`attach`) and only drawn with and
flushed on the render thread. The renderer and the surface are the same
for both.

On macOS the native window is not a Qt child window either but a view of
the app's own (video_macos.PictureView) kept over a plain placeholder box
(`_Placement`): Qt's cocoa plugin drops every mouse move over a child
window, transparent for input or not, so the widgets got no hover there,
and a widget made native later (a menu makes its siblings native) put its
own view over the picture for good. The app's own view is never the
mouse's, follows its box into whichever native view holds it, and stays
on top there.

The window's own thread (media/video_surface.py's _MpvGLWidget, exactly as
it always was) is used when this is switched off (core/video_mode.py) or
could not start; then the reason is logged and kept for Nerd > Diagnostics
and the error report.
"""

from __future__ import annotations

import contextlib
import ctypes
import functools
import os
import sys
import threading
import time
import weakref
from typing import Any, Callable

from PyQt6 import sip
from PyQt6.QtCore import (
    QCoreApplication, QEvent, QObject, QPoint, QRect, Qt, QThread, QTimer, pyqtSignal,
)
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
#: macOS: a change to the context's view the window's thread could not make
#: (the render thread was in a frame) is tried again this soon, at most
#: this many times in a row before the surface gives up.
ATTACH_RETRY_MS = 4
ATTACH_TRIES = 500
#: Platforms where Qt cannot hand a window's GL to another thread.
_NO_THREAD_PLATFORMS = frozenset({"offscreen", "minimal", "vnc", "linuxfb"})
#: The render thread's GL context: Qt's own, or macOS's NSOpenGLContext.
QT_GL, NSGL = "qt", "nsgl"
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


def gl_kind(platform: str) -> str:
    """Which GL context the render thread draws with on *platform*
    (sys.platform): macOS's own NSOpenGLContext there, Qt's elsewhere."""
    return NSGL if platform == "darwin" else QT_GL


def _kind() -> str:
    return gl_kind(sys.platform)


def can_start() -> str:
    """Why this system cannot draw the picture on a thread of its own ("":
    it can try)."""
    if _libmpv is None:
        return "libmpv not loaded"
    name = QGuiApplication.platformName()
    if name in _NO_THREAD_PLATFORMS:
        return f"the {name} platform"
    if _kind() == NSGL:
        # Qt says cocoa has no OpenGL on other threads - true of its own
        # context, not of the one media/video_macos.py makes for it.
        from . import video_macos
        why = video_macos.unavailable()
        if why:
            return f"{name}: {why}"
    else:
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
            video_mode.chosen(video_mode.THREAD)
            return ThreadedVideoSurface(parent)
        log.warning("video: drawn on the window's thread (fallback: %s)", why)
        video_mode.note(video_mode.WINDOW, f"fallback: {why}")
    else:
        log.info("video: drawn on the window's thread (%s)", why)
        video_mode.note(video_mode.WINDOW, why)
    return _MpvGLWidget(parent)


def native_menu(menu: QWidget, platform: str | None = None) -> None:
    """macOS: *menu* made a native window before it shows (the menus over
    the video: ui/widgets.popup_menu_over_video, the player's options). A
    widget made native makes every other child of its parent native with it
    - for a menu of the main window, its central widget, whose native view
    then lay over the picture's own for good: the picture black after a
    right-click. So while the picture may be drawn on a thread of its own,
    the menu goes native alone; with the window's thread, as it always did.
    (*platform*: sys.platform; tests.)"""
    if (platform or sys.platform) != "darwin":
        return
    alone = video_mode.wanted(None)[0] and not video_mode.failed()
    attr = Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings
    was = QCoreApplication.testAttribute(attr)
    if alone and not was:
        QCoreApplication.setAttribute(attr, True)
    try:
        menu.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        menu.winId()
    finally:
        if alone and not was:
            QCoreApplication.setAttribute(attr, False)


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


def window_change() -> Any:
    """media/video_macos's `window_change`: around a change of a window
    that holds a picture which AppKit may draw at once. Nothing where no
    macOS context is (every other system)."""
    from . import video_macos
    return video_macos.window_change()


class WindowChanges:
    """A top-level window that holds a picture, before its Qt class in the
    bases: full screen on or off, maximized, minimized, normal, its state,
    flags, size, brought forward - asked from Python inside `window_change`.

    On macOS AppKit draws the picture's view itself during such a change
    (the full screen transition flushes Core Animation there and then) and
    waits for the context's CGL lock, holding Python's lock as it was asked;
    a render thread in a frame holds that CGL lock and waits for Python's -
    the app hung for good entering and leaving TV mode. Inside
    `window_change` no render thread is in a frame. Everywhere else it is
    nothing."""

    def showFullScreen(self) -> None:
        with window_change():
            super().showFullScreen()  # type: ignore[misc]

    def showMaximized(self) -> None:
        with window_change():
            super().showMaximized()  # type: ignore[misc]

    def showMinimized(self) -> None:
        with window_change():
            super().showMinimized()  # type: ignore[misc]

    def showNormal(self) -> None:
        with window_change():
            super().showNormal()  # type: ignore[misc]

    def setWindowState(self, state: Any) -> None:
        with window_change():
            super().setWindowState(state)  # type: ignore[misc]

    def setWindowFlags(self, flags: Any) -> None:
        with window_change():
            super().setWindowFlags(flags)  # type: ignore[misc]

    def setGeometry(self, *args: Any) -> None:
        with window_change():
            super().setGeometry(*args)  # type: ignore[misc]

    def resize(self, *args: Any) -> None:
        with window_change():
            super().resize(*args)  # type: ignore[misc]

    def raise_(self) -> None:
        with window_change():
            super().raise_()  # type: ignore[misc]

    def activateWindow(self) -> None:
        with window_change():
            super().activateWindow()  # type: ignore[misc]


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
    if getattr(type(win), "native_picture", False):  # macOS's own view
        return not win.gone
    return _alive(win) and not getattr(win, "gone", False)


def _target_of(win: QWindow | None, shown: bool) -> _Target:
    if win is None or not _usable(win):
        return _Target(None, 0, 0, False)
    dpr = win.devicePixelRatio() or 1.0
    w, h = max(1, round(win.width() * dpr)), max(1, round(win.height() * dpr))
    return _Target(win, w, h, bool(shown and win.width() > 0 and win.height() > 0
                                   and _on_screen(win)))


def _picture_window(owner: QWidget, changed: Callable[[], None], going: Callable[[Any], None],
                    failed: Callable[[str], None]) -> tuple[Any, QWidget, "_Placement | None"]:
    """(what the picture is drawn into, the box holding its place in
    *owner*, the placement keeping it there or None). Linux and Windows: a
    native child window in a window container. macOS: a view of the app's
    own that no mouse event is for, kept over a plain box (_Placement)."""
    if _kind() == NSGL:
        from . import video_macos
        view = video_macos.PictureView()
        box = QWidget(owner)
        place: _Placement | None = _Placement(box, view, changed, going, failed)
        win: Any = view
    else:
        win = _VideoWindow(changed, going)
        box = QWidget.createWindowContainer(win, owner)
        place = None
    box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    box.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    box.hide()
    return win, box, place


def _owner_target(owner: QWidget) -> _Target:
    """Where *owner* (the surface, or the pop-out's mirror) has the picture
    drawn now: its native window (Linux, Windows) or, on macOS, its own
    view as its placement has it."""
    shown = owner.isVisible() and owner._box.isVisible()
    p = getattr(owner, "_place", None)
    if p is None:
        return _target_of(owner._win, shown)
    p.sync(tell=False)
    return p.target(shown)


# -- macOS: the picture's own view kept in place ----------------------------------

_E = QEvent.Type
_MOVED = frozenset(int(t) for t in (_E.Move, _E.Resize))
_ANCESTOR_EVENTS = _MOVED | frozenset(int(t) for t in (
    _E.Show, _E.Hide, _E.ParentChange, _E.WinIdChange))
_REWATCH = frozenset(int(t) for t in (_E.ParentChange, _E.WinIdChange))
_EXPOSE = int(_E.Expose)
_TOP_EVENTS = _DPR_EVENTS | frozenset(int(t) for t in (
    _E.Expose, _E.WindowStateChange, _E.Show, _E.Hide, _E.PlatformSurface))
#: Looked at again this often while shown, whatever was said.
PLACE_CHECK_MS = 1000


class _Placement(QObject):
    """macOS: the picture's own view (media/video_macos.PictureView) kept
    over *box*, the surface's placeholder: in the NSView of the box's
    nearest native ancestor - the window's, or a widget made native since
    (a menu made native makes its siblings native, and a native view put
    over the picture left it black for good) -, at the box's place there, on
    top, hidden while the box is. Followed through the events of the box
    and of its ancestors below the window (moved, resized, shown, hidden,
    reparented, made native) and of the top-level window (exposed - macOS
    says covered or uncovered there -, minimized, another screen, its
    native window going), and looked at again once a second while shown.
    Each change ends in *changed* (the surface's _window_changed); *going*
    is told before the window's native view goes, *failed* of an error."""

    def __init__(self, box: QWidget, view: Any, changed: Callable[[], None],
                 going: Callable[[Any], None], failed: Callable[[str], None]) -> None:
        super().__init__(box)
        self._box = box
        self.view = view
        self._changed = changed
        self._going = going
        self._failed = failed
        self._widgets: list[QWidget] = []
        self._top: QWindow | None = None
        self._top_exposed: bool | None = None
        self._stale = True              # the watched widgets to be looked up again
        self._closed = False
        self.moved = False              # put on another view or raised: attach again
        self._soon = QTimer(self)
        self._soon.setSingleShot(True)
        self._soon.setInterval(0)
        self._soon.timeout.connect(self._sync_and_tell)
        self._tick = QTimer(self)
        self._tick.setInterval(PLACE_CHECK_MS)
        self._tick.timeout.connect(self._sync_and_tell)

    # -- what it watches ----------------------------------------------------------
    def _rewatch(self) -> None:
        self._stale = False
        for w in self._widgets:
            if _alive(w):
                w.removeEventFilter(self)
        self._widgets = []
        box = self._box
        top_w = box.window()
        w: QWidget | None = box
        while w is not None and w is not top_w:
            w.installEventFilter(self)
            self._widgets.append(w)
            w = w.parentWidget()
        top = top_w.windowHandle() if top_w is not None else None
        if top is not self._top:
            if self._top is not None and _alive(self._top):
                self._top.removeEventFilter(self)
            self._top = top
            if top is not None:
                top.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        # Never raises (every widget here can go at any moment), never
        # takes an event.
        if getattr(self, "_closed", True):
            return False
        try:
            t = int(event.type())
            if obj is self._top:
                if t not in _TOP_EVENTS:
                    return False
                if t == _PLATFORM_SURFACE:
                    kind = event.surfaceEventType()
                    if kind == event.SurfaceEventType.SurfaceAboutToBeDestroyed:
                        self._host_going()
                        return False
                elif t == _EXPOSE:
                    # A window's repaint can say it too: only covered or
                    # uncovered counts.
                    now = bool(obj.isExposed())
                    if now == self._top_exposed:
                        return False
                    self._top_exposed = now
                self._soon.start()
            elif t in _ANCESTOR_EVENTS:
                if t in _REWATCH:
                    self._stale = True
                if t in _MOVED:
                    self.sync(tell=False)       # follows a resize step by step
                self._soon.start()
        except Exception as e:
            log.debug("video: the picture's place: %s", e)
        return False

    def _host_going(self) -> None:
        """The window's native view goes (a window made again): the context
        and the render thread let go of the picture's view, which leaves it
        - placed again once the window is back."""
        try:
            self._going(self.view)
        except Exception as e:
            log.debug("video: letting go of the picture's view: %s", e)
        self.view.unhost()
        self._stale = True
        log.info("video: the picture's view left its window (that window's view goes)")
        self._soon.start()

    # -- where it goes --------------------------------------------------------------
    def _sync_and_tell(self) -> None:
        self.sync(tell=True)

    def sync(self, tell: bool = True) -> None:
        if self._closed or not _alive(self._box):
            return
        if self._stale or self._top is None:
            self._rewatch()
        box = self._box
        try:
            visible = box.isVisible() and box.width() > 0 and box.height() > 0
            host = box.nativeParentWidget() if visible else None
            ptr = int(host.winId()) if host is not None else 0
            pos = box.mapTo(host, QPoint(0, 0)) if host is not None else QPoint()
            self.view.scale = box.devicePixelRatioF() or 1.0
            changes = self.view.place(ptr, (pos.x(), pos.y(), box.width(), box.height()),
                                      visible)
        except Exception as e:
            self._closed = True
            self._failed(f"the picture's view: {type(e).__name__}: {e}")
            return
        if changes:
            self._say(changes, host, ptr)
        if self.view.shown:
            if not self._tick.isActive():
                self._tick.start()
        elif self._tick.isActive():
            self._tick.stop()
        if tell:
            self._changed()

    def _say(self, changes: list[str], host: QWidget | None, ptr: int) -> None:
        if "host" in changes or "raised" in changes:
            self.moved = True
        if "host" in changes and host is not None:
            what = "the window" if host is self._box.window() else (
                f"{type(host).__name__} {host.objectName()!r}, a widget made native")
            log.info("video: the picture's view on NSView %#x (%s)", ptr, what)
        if "raised" in changes:
            log.info("video: the picture's view raised over a native view put on top of it")
        for word in ("shown", "hidden"):
            if word in changes:
                log.info("video: the picture's view %s", word)

    def target(self, shown: bool) -> _Target:
        v = self.view
        if v.gone or self._closed:
            return _Target(None, 0, 0, False)
        box, top = self._box, self._top
        on = bool(shown and v.shown and top is not None and top.isExposed()
                  and not top.windowStates() & Qt.WindowState.WindowMinimized)
        v.on_screen = on
        dpr = v.scale or 1.0
        return _Target(v, max(1, round(box.width() * dpr)), max(1, round(box.height() * dpr)), on)

    def covered(self) -> str:
        """Why the picture is not on screen, in the log's words."""
        top = self._top
        if not self.view.shown:
            return "its view is hidden"
        if top is None or not top.isExposed():
            return "its window is covered, hidden or on no screen"
        return "its window is minimized"

    def close(self) -> None:
        """For good: nothing watched, the view out of its window."""
        if self._closed and self.view.gone:
            return
        self._closed = True
        self._soon.stop()
        self._tick.stop()
        for w in [*self._widgets, self._top]:
            if w is not None and _alive(w):
                try:
                    w.removeEventFilter(self)
                except RuntimeError:
                    pass
        self._widgets, self._top = [], None
        self.view.remove()


# -- the render thread's GL context -------------------------------------------------

_NO_LOCK = contextlib.nullcontext()


class _QtGL:
    """Qt's own QOpenGLContext, made and current on the render thread -
    current on the offscreen surface while there is no window to draw into
    (Linux, Windows). The strategy the renderer draws through; macOS's is
    media/video_macos.py's MacGL, with the same calls.

    The render thread: create, get_proc_address, idle (current with no
    window), keep (current somewhere), section (a GL call outside a frame:
    nothing to hold here), begin (current on a window), fbo, present
    (swap), end, free, abandon. The window's thread: attach (the target as
    it is), detach, close - nothing to do for Qt's."""

    render_args: dict = {}

    def __init__(self, offscreen: QOffscreenSurface | None, fmt: QSurfaceFormat | None) -> None:
        self._off = offscreen
        self._fmt = fmt
        self._ctx: QOpenGLContext | None = None
        self.info = ""

    def create(self) -> str:
        ctx = QOpenGLContext()
        ctx.setFormat(self._fmt)
        if not ctx.create():
            return "the OpenGL context could not be made"
        if not ctx.makeCurrent(self._off):
            return "the OpenGL context could not be made current"
        self._ctx = ctx
        return ""

    def get_proc_address(self, name: bytes) -> int:
        ctx = self._ctx
        if ctx is None:
            return 0
        try:
            addr = ctx.getProcAddress(name)
            return int(addr) if addr else 0
        except Exception:
            return 0

    def idle(self) -> None:
        self._ctx.makeCurrent(self._off)

    def keep(self) -> None:
        if QOpenGLContext.currentContext() is not self._ctx:
            self._ctx.makeCurrent(self._off)

    def section(self):
        return _NO_LOCK

    def begin(self, window: QWindow, again: bool) -> bool:
        ctx = self._ctx
        if again or QOpenGLContext.currentContext() is not ctx:
            return bool(ctx.makeCurrent(window))
        return True

    def fbo(self) -> int:
        return int(self._ctx.defaultFramebufferObject())

    def present(self, window: QWindow) -> None:
        self._ctx.swapBuffers(window)

    def end(self, ok) -> None:
        pass

    def free(self, rctx) -> None:
        ctx = self._ctx
        try:
            ctx.makeCurrent(self._off)
            rctx.free()
        except Exception as e:
            log.info("video thread: render context free: %s", e)
        try:
            ctx.doneCurrent()
        except Exception:
            pass
        self._ctx = None

    def abandon(self) -> None:
        self._ctx.doneCurrent()
        self._ctx = None

    def attach(self, target: "_Target") -> "_Target":
        return target

    def forget(self) -> None:
        pass

    def detach(self, window) -> None:
        pass

    def close(self) -> None:
        pass


# -- the render thread ------------------------------------------------------------


class _Renderer(QThread):
    """Owns mpv's render context and draws through its GL context (*gl*:
    Qt's, else macOS's); renders each frame mpv has into the target window
    and presents it. Only *wake*, *set_target*, *set_blank*, *redraw*,
    *release* and *stop* are called from other threads."""

    def __init__(self, core, offscreen: QOffscreenSurface | None, fmt: QSurfaceFormat | None,
                 name: str, told: "_Told", gl: Any = None) -> None:
        super().__init__()
        self.setObjectName(name)
        self._core = core
        self._gl = gl if gl is not None else _QtGL(offscreen, fmt)
        self._told = told
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._target = _Target(None, 0, 0, False)
        self._blank = False
        self._redraw = True
        self._quit = False
        self._release: QWindow | None = None     # a window to let go of
        # makeCurrent again before the next frame: the GL context takes a
        # resize, a move or another screen there.
        self._remake = True
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
        The render thread stops drawing into it and lets it go (Qt's context
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
        try:
            return self._gl.get_proc_address(name)
        except Exception:
            return 0

    def _fn(self, name: bytes, restype, *argtypes):
        addr = self._get_proc_address(None, name)
        if not addr:
            return None
        return ctypes.CFUNCTYPE(restype, *argtypes)(addr)

    def run(self) -> None:
        gl = self._gl
        error = gl.create()
        if error:
            self.error = error
            self.ready.set()
            return
        try:
            self._proc_fn = _libmpv.MpvGlGetProcAddressFn(self._get_proc_address)
            with gl.section():
                rctx = _libmpv.MpvRenderContext(
                    self._core, "opengl", opengl_init_params={"get_proc_address": self._proc_fn})
        except Exception as e:
            self.error = f"mpv render context: {type(e).__name__}: {e}"
            gl.abandon()
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
            self._loop(gl, rctx)
        except Exception as e:
            log.error("video thread: stopped by %s: %s", type(e).__name__, e)
            self._told.failed.emit(f"{type(e).__name__}: {e}")
        finally:
            # Not update_cb = None first: python-mpv drops the callback it
            # holds before it hands mpv the new one, so mpv's thread could
            # still call the freed one - a crash on closing multiview. free()
            # ends the callbacks itself; rctx keeps ours alive until then.
            gl.free(rctx)
            self.rctx = None

    def _clear_black(self) -> None:
        if self._glClearColor is not None and self._glClear is not None:
            self._glClearColor(0.0, 0.0, 0.0, 1.0)
            self._glClear(_GL_COLOR_BUFFER_BIT)

    def _skip(self, gl, rctx) -> None:
        # Nothing on screen to draw into: the frame is taken all the same,
        # so mpv's clock never waits on us.
        with gl.section():
            rctx.render(skip_rendering=True)
        rctx.report_swap()
        self.pending_since = 0.0

    def _loop(self, gl, rctx) -> None:
        current = None              # the window drawn into last
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
                remake, self._remake = self._remake, False
                self._redraw = False
                release, self._release = self._release, None
            if release is not None:
                if current is release:
                    gl.idle()
                    current = None
                self._released.set()
                if target.window is release:
                    continue
            if current is None:
                gl.keep()
            with gl.section():
                try:
                    frame = bool(rctx.update())
                except Exception:
                    frame = True
            if frame and not self.pending_since:
                self.pending_since = time.monotonic()
            if target.window is None or not target.exposed:
                if current is not None:
                    gl.idle()
                    current = None
                if frame:
                    self._skip(gl, rctx)
                continue
            if not (frame or redraw):
                continue
            if not _usable(target.window):
                # Its window went (or is going) before the window's thread
                # said so: nothing to draw into, an ordinary state.
                self._drop_target(target.window)
                if current is not None:
                    gl.idle()
                    current = None
                if frame:
                    self._skip(gl, rctx)
                continue
            t0 = time.monotonic()
            try:
                ok = gl.begin(target.window, remake or current is not target.window)
                if ok is None:
                    # macOS: the window's thread is putting the context on
                    # this window's view - drawn once it is there.
                    continue
                try:
                    if ok:
                        current = target.window
                        failed_makes = 0
                        if blank:
                            self._clear_black()
                        else:
                            rctx.render(flip_y=True, opengl_fbo={
                                "fbo": gl.fbo(), "w": target.w, "h": target.h},
                                **gl.render_args)
                        gl.present(target.window)
                finally:
                    gl.end(ok)
                if not ok:
                    failed_makes += 1
                    if failed_makes in (1, 50):
                        log.warning("video thread: the window could not be drawn into (%d)",
                                    failed_makes)
                    gl.idle()
                    current = None
                    continue
            except RuntimeError as e:
                # sip: the window's wrapper was deleted under us. Not a
                # failure of the render thread - the window is gone.
                log.info("video thread: its window went (%s)", e)
                self._drop_target(target.window)
                gl.idle()
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
                        QEvent.Type.ChildAdded, QEvent.Type.LayoutRequest})

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

    def _settle(self, w: QWidget) -> None:
        """*w* as its next paint will show it: the layout requests waiting
        for it and its children (a text that changed width, a button shown
        or hidden, a new size) carried out now, before it is drawn - so mpv
        is never handed a picture of it laid out the old way (the DVR row
        first squeezed, then its own size)."""
        if not w.isVisible():
            return
        self._rendering = True       # what settling moves is drawn just after
        try:
            for c in [w, *w.findChildren(QWidget)]:
                QCoreApplication.sendPostedEvents(c, QEvent.Type.LayoutRequest.value)
            lay = w.layout()
            if lay is not None:
                lay.activate()
        finally:
            self._rendering = False

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
                    self._settle(w)
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
                        self._settle(w)
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
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        self._rendering = True
        try:
            # Only what the widget paints itself, as over the old surface:
            # DrawWindowBackground fills its rectangle with the palette's
            # window colour first - a square box around the round centre
            # button. A widget that fills its background (autoFillBackground,
            # a style sheet's) still does.
            w.render(p, QPoint(0, 0), QRegion(), QWidget.RenderFlag.DrawChildren)
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
    """What a surface leaves running - its render thread, its GL context,
    its mpv core - ended if the surface is destroyed without shutdown() (its
    destroyed signal: nothing is asked of the surface itself then, it is
    gone). The thread first (mpv's render context goes with it), then the
    context (macOS: off its view, on this thread), then the core."""

    def __init__(self) -> None:
        self.renderer: _Renderer | None = None
        self.gl: Any = None
        self.view: Any = None            # macOS: the picture's own view
        self.core: Any = None

    def end(self, *_args) -> None:
        r, self.renderer = self.renderer, None
        if r is not None:
            r.finish()
        g, self.gl = self.gl, None
        if g is not None:
            try:
                g.close()
            except Exception as e:
                log.warning("video thread: the GL context did not close: %s", e)
        v, self.view = self.view, None
        if v is not None:
            try:
                v.remove()
            except Exception as e:
                log.warning("video: the picture's view did not go: %s", e)
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
        self._gl: Any = None                 # the render thread's GL context
        self._offscreen: QOffscreenSurface | None = None
        self._attach_timer: QTimer | None = None
        self._attach_tries = 0
        self._fail_why = ""
        self._told = _Told()
        self._told.shown.connect(self._first_frame)
        self._told.failed.connect(self._render_failed)
        self._told.vo.connect(self._vo_changed)
        # Where the picture goes: this surface's own window, or the pop-out's.
        self._ending = _Ending()
        self._win, self._box, self._place = _picture_window(
            self, self._window_changed, self._window_going, self._fail_soon)
        if self._place is not None:
            self._ending.view = self._win
            # The box goes before the surface says so: what draws into its
            # view ends first (the window-thread surface's container does
            # the same for its window).
            self._box.destroyed.connect(self._ending.end)
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
        self._drawn: bool | None = None      # the picture drawn, as last said
        self._sent: tuple | None = None      # macOS: the target last handed over
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
        mac = _kind() == NSGL
        try:
            # macOS: mpv's render does not wait for the frame's display time
            # there (media/video_macos.py), so mpv hands each frame over at
            # that time instead of 50 ms early - DOPEIPTV_GUI_RENDER=tuned's
            # timing.
            self.mpv = build_core(self, tuned=True) if mac else build_core(self)
        except Exception as e:
            self.mpv = None
            self._give_up(f"mpv: {type(e).__name__}: {e}")
            return
        self._ending.core = self.mpv
        gl, why = self._make_gl(mac)
        if gl is None:
            self._kill_core()
            self._give_up(why)
            return
        self._gl = gl
        self._ending.gl = gl
        r = _Renderer(self.mpv, self._offscreen, None, self._name, self._told, gl=gl)
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
        if mac:
            log.info("video thread: NSOpenGLContext, %s (%s)", gl.info or "renderer unknown",
                     gl.format)
        self._sync_box()
        self._window_changed()

    def _make_gl(self, mac: bool) -> tuple[Any, str]:
        """The render thread's GL context: (the strategy, "") or (None,
        why not). macOS's NSOpenGLContext is made here, on the main thread;
        Qt's is made on the render thread, with the offscreen surface made
        here for it."""
        if mac:
            from . import video_macos
            gl = video_macos.MacGL()
            try:
                why = gl.prepare()
            except Exception as e:
                why = f"NSOpenGLContext: {type(e).__name__}: {e}"
            return (None, why) if why else (gl, "")
        fmt = _surface_format()
        off = QOffscreenSurface(self.screen())
        off.setFormat(fmt)
        off.create()
        if not off.isValid():
            return None, "no offscreen GL surface"
        self._offscreen = off
        return _QtGL(off, fmt), ""

    @property
    def gl_info(self) -> str:
        """What the render thread draws with, for the log ("" for Qt's)."""
        gl = self._gl
        return f"NSOpenGLContext, {gl.info}" if gl is not None and gl.info else ""

    def _give_up(self, reason: str) -> None:
        self._failed = reason
        self._overlays.uninstall()
        if self._place is not None:
            # macOS: the view of its own leaves the window at once - never a
            # last frame left over the surface that takes its place.
            self._place.close()
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
        # Once its thread has ended: macOS's context off its view, here.
        self._ending.gl = None
        gl, self._gl = self._gl, None
        if gl is not None:
            try:
                gl.close()
            except Exception as e:
                log.warning("video thread: the GL context did not close: %s", e)

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
        owner: QWidget = self
        try:
            m = self._live_mirror()
            owner = m if m is not None else self
            t = _owner_target(owner)
        except RuntimeError:            # a window on its way out
            t = _Target(None, 0, 0, False)
        gl = self._gl
        p = getattr(owner, "_place", None)
        if p is not None and p.moved and gl is not None:
            # On another view, or raised: AppKit may have let go of the
            # context's drawable - put on the view again.
            p.moved = False
            gl.forget()
        if gl is not None:
            t = self._attach(gl, t)
            if t is None:
                return
        self._say_drawn(t, p)
        if p is not None:
            # macOS looks again on its own (events, once a second): the same
            # target with the same drawable is no new frame to draw.
            key = (id(t.window), t.w, t.h, t.exposed, getattr(gl, "changes", 0))
            if key == self._sent:
                return
            self._sent = key
        r.set_target(t)

    def _say_drawn(self, t: _Target, place: "_Placement | None") -> None:
        """Whether the picture is drawn, in the log once per change (macOS:
        why not - its view hidden, its window covered or minimized)."""
        drawn = t.window is not None and t.exposed
        if drawn == self._drawn:
            return
        first, self._drawn = self._drawn is None, drawn
        if drawn:
            log.info("video: the picture is drawn (%s)", self._name)
        elif not first:
            why = place.covered() if place is not None else "its window is hidden or covered"
            log.info("video: the picture is not drawn now - %s (%s)", why, self._name)

    def _attach(self, gl: Any, t: _Target) -> _Target | None:
        """The GL context on the target's window, on this thread (macOS:
        its NSView; Qt's: nothing to do). None: not now - the render thread
        draws on where it was, and this is tried again in a moment."""
        try:
            got = gl.attach(t)
        except Exception as e:
            self._fail_soon(f"the picture's window: {type(e).__name__}: {e}")
            return None
        if got is not None:
            self._attach_tries = 0
            return got
        self._attach_tries += 1
        if self._attach_tries > ATTACH_TRIES:
            self._fail_soon("the picture's window stayed busy")
            return None
        if self._attach_timer is None:
            self._attach_timer = QTimer(self)
            self._attach_timer.setSingleShot(True)
            self._attach_timer.setInterval(ATTACH_RETRY_MS)
            self._attach_timer.timeout.connect(self._window_changed)
        if not self._attach_timer.isActive():
            self._attach_timer.start()
        return None

    def _fail_soon(self, reason: str) -> None:
        # Not inside the event that found it: the owner replaces this
        # surface on its fallback signal.
        if not self._fail_why:
            self._fail_why = reason
            QTimer.singleShot(0, self._fail_now)

    def _fail_now(self) -> None:
        if self._fail_why:
            self._render_failed(self._fail_why)

    def _let_go(self, win: QWindow) -> None:
        """*win*'s native window is going: the render thread lets go of it,
        then (macOS) the context is taken off its view, on this thread."""
        self._sent = None                # handed over again when it is back
        r = self._renderer
        if r is not None:
            r.release(win)
        gl = self._gl
        if gl is not None:
            try:
                gl.detach(win)
            except Exception as e:
                log.warning("video thread: the GL context did not let go of its window: %s", e)

    def _window_going(self, win: QWindow) -> None:
        if not _alive(self):
            # This surface is being destroyed (its window goes with it):
            # its thread and core end here, before the window does.
            self._ending.end()
            return
        self._let_go(win)
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
        if self._attach_timer is not None:
            self._attach_timer.stop()
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
        there. Nothing GL is moved between windows (on macOS the one
        context is put on its view instead, on this thread)."""
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
            if _alive(m):
                self._let_go(m._win)
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
        if self._attach_timer is not None:
            self._attach_timer.stop()
        self._overlays.uninstall()
        self._stop_renderer()
        if self._place is not None:
            self._place.close()           # after the context let go of it
        self._kill_core()
        off, self._offscreen = self._offscreen, None
        if off is not None:
            off.destroy()


def _mirror_gone(owner_ref: Any, view: Any, *_args) -> None:
    """macOS: a mirror's box went - its owner's render thread and context
    let go of its view (if its owner is still there), then the view goes."""
    o = owner_ref()
    try:
        if o is not None and _alive(o):
            o._let_go(view)
    except Exception as e:
        log.debug("video: the pop-out's view: %s", e)
    view.remove()


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
        self._win, self._box, self._place = _picture_window(
            self, self._changed, self._going, self._failed)
        if self._place is not None:
            # Its view goes with it - its owner's thread let go of it first.
            self._box.destroyed.connect(functools.partial(_mirror_gone, weakref.ref(owner),
                                                          self._win))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._box)

    def _changed(self) -> None:
        o = self._owner
        if o is not None:
            o._window_changed()

    def _going(self, win: Any) -> None:
        o = self._owner
        if o is not None:
            o._window_going(win)

    def _failed(self, reason: str) -> None:
        o = self._owner
        if o is not None:
            o._fail_soon(reason)

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
    thread has shown frames - on macOS with the app's own NSOpenGLContext
    (media/video_macos.py), the way the app draws there. CHECK_SKIPPED when
    it cannot try (no OpenGL on other threads or no PyObjC, no pixel format,
    no window on screen), CHECK_FAILED when it could and drew nothing - the
    app would fall back to the window's thread there."""
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
            where = QGuiApplication.platformName()
            if surface.gl_info:
                where = f"{where}, {surface.gl_info}"
            log.info("video-check: OK (%d frames drawn on the render thread, %s)", shown, where)
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
