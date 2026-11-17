"""macOS platform support: OpenGL, libmpv discovery, wakelock, player paths."""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import subprocess


def find_libmpv() -> None:
    """Help ctypes find libmpv.dylib on macOS (Homebrew installs)."""
    if ctypes.util.find_library("mpv"):
        return
    for prefix in ("/opt/homebrew/lib", "/usr/local/lib"):
        dylib = os.path.join(prefix, "libmpv.dylib")
        if os.path.isfile(dylib):
            os.environ.setdefault(
                "DYLD_LIBRARY_PATH",
                prefix + ":" + os.environ.get("DYLD_LIBRARY_PATH", ""))
            break


def setup_opengl() -> None:
    """Request OpenGL 4.1 Core Profile (required for mpv render API on macOS)."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QSurfaceFormat
    from PyQt6.QtWidgets import QApplication

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    fmt = QSurfaceFormat()
    fmt.setVersion(4, 1)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(0)
    fmt.setStencilBufferSize(0)
    QSurfaceFormat.setDefaultFormat(fmt)


def apply_widget_surface_format(widget) -> None:
    """Set the OpenGL 4.1 Core Profile format on a specific widget."""
    from PyQt6.QtGui import QSurfaceFormat

    fmt = QSurfaceFormat()
    fmt.setVersion(4, 1)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    widget.setFormat(fmt)


_opengl_dll = None
try:
    _opengl_dll = ctypes.cdll.LoadLibrary(
        ctypes.util.find_library("OpenGL"))
except Exception:
    pass


def gl_get_proc_address_fallback(name: bytes) -> int:
    """Resolve an OpenGL function via the macOS OpenGL framework.

    Qt's getProcAddress can fail for core GL functions on macOS;
    this loads them directly from the framework as a fallback.
    """
    if _opengl_dll is None:
        return 0
    try:
        return ctypes.cast(
            getattr(_opengl_dll, name.decode("utf-8")),
            ctypes.c_void_p).value or 0
    except (AttributeError, OSError):
        return 0


_cursor_hidden = False
_hidden_at: tuple[int, int] | None = None


def set_cursor_hidden(hidden: bool) -> None:
    """Hide/show the mouse cursor over the video on macOS.

    Per-widget ``setCursor(BlankCursor)`` does not reliably hide the pointer
    over a QOpenGLWidget on macOS (the native view keeps its own cursor
    rect), so an application override cursor is set. That is still a cursor
    rect on Qt's own view, though: over the picture's own native view (the
    video drawn on its own thread, media/video_macos.PictureView) AppKit put
    the arrow back and the pointer never went. So AppKit is asked as well,
    the way a video player on the Mac does it: hidden until the mouse moves
    (``NSCursor setHiddenUntilMouseMoves:``), whichever view is under it.
    Kept balanced via the module flag so the push/pop stack never drifts.
    macOS only - Linux uses the widget cursor."""
    global _cursor_hidden, _hidden_at
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QCursor
    from PyQt6.QtWidgets import QApplication
    if hidden and not _cursor_hidden:
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.BlankCursor))
        _cursor_hidden = True
        pos = QCursor.pos()
        _hidden_at = (pos.x(), pos.y())
        _appkit_hidden(True)
    elif not hidden and _cursor_hidden:
        QApplication.restoreOverrideCursor()
        _cursor_hidden = False
        _hidden_at = None
        _appkit_hidden(False)


def cursor_moved(x: int, y: int) -> bool:
    """Whether a mouse move at (*x*, *y*) is the pointer really moving: a
    move Qt sends while the pointer rests where it was hidden (a widget
    shown or hidden under it) must not bring it back."""
    return not _cursor_hidden or _hidden_at != (x, y)


def _appkit_hidden(hidden: bool) -> None:
    try:
        import AppKit
        AppKit.NSCursor.setHiddenUntilMouseMoves_(bool(hidden))
    except Exception as e:  # no PyObjC: the override cursor alone
        from .log import log
        log.debug("macOS: the pointer not hidden through AppKit: %s", e)


#: The sound output mpv tries first on macOS, CoreAudio after it (a libmpv
#: without AVFoundation's output takes the next one). mpv's CoreAudio output
#: listens to the system's device changes and is handed one more after it
#: has gone - an AirPlay device found nearby crashed the app in libmpv
#: (hotplug_cb on an output already freed); AVFoundation's listens to none.
MAC_AO = "avfoundation,coreaudio"


def extra_mpv_opts() -> dict:
    """Extra mpv options for macOS: hardware decoding, and the sound output
    that does not crash on a device change (MAC_AO). A Nerd mpv option of
    the person's own still wins (applied after these)."""
    return {"hwdec": "videotoolbox-copy", "ao": MAC_AO}


def extra_player_candidates(player: str) -> list[str]:
    """Additional executable paths for mpv/VLC on macOS."""
    if player == "mpv":
        return ["/opt/homebrew/bin/mpv", "/usr/local/bin/mpv"]
    return ["/Applications/VLC.app/Contents/MacOS/VLC"]


def fix_app_name(name: str) -> None:
    """Set the macOS application-menu name so it reads 'dopeIPTV' instead of
    'python' when the app is run from source. Needs pyobjc; if it isn't
    installed this is a no-op and the menu keeps the process name (a proper
    .app bundle with CFBundleName in Info.plist fixes it for good - see the
    packaging docs). Must run before QApplication is created."""
    try:
        import objc  # noqa: F401
        bundle = objc.lookUpClass("NSBundle").mainBundle()
        info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        if info is not None:
            info["CFBundleName"] = name
            info["CFBundleDisplayName"] = name
        try:
            objc.lookUpClass("NSProcessInfo").processInfo().setProcessName_(
                name)
        except Exception:
            pass
    except Exception:
        pass


def libmpv_install_hint(say=None) -> str:
    """Installation hint shown when libmpv is missing on macOS; *say* turns
    a string key into text (English for the log, translated for Settings)."""
    from ..i18n import english
    return "\n  " + (say or english)("play_hint_macos")


class WakeLockMacOS:
    """Keeps the screen awake via a caffeinate child process."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None

    @property
    def held(self) -> bool:
        return self._proc is not None

    def acquire(self, reason: str = "Playing video") -> None:
        if self.held:
            return
        # -d prevents display sleep, -i prevents idle system sleep; tying it to
        # our own PID (-w) makes the OS release the assertion if we crash. Use
        # the absolute path so it resolves even under a Finder-launched app's
        # minimal PATH.
        exe = "/usr/bin/caffeinate"
        if not os.path.exists(exe):
            exe = "caffeinate"
        try:
            self._proc = subprocess.Popen(
                [exe, "-d", "-i", "-w", str(os.getpid())],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def release(self) -> None:
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
