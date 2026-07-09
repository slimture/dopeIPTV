"""macOS platform support: OpenGL, libmpv discovery, wakelock, player paths."""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import subprocess
import sys


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


def set_cursor_hidden(hidden: bool) -> None:
    """Hide/show the mouse cursor on macOS via an application override cursor.

    Per-widget ``setCursor(BlankCursor)`` does not reliably hide the pointer
    over a QOpenGLWidget on macOS (the native view keeps its own cursor rect),
    so the fullscreen/mini-player idle-hide has no effect there. An override
    cursor is honoured by Cocoa. Kept balanced via the module flag so the
    push/pop stack never drifts. macOS only - Linux uses the widget cursor."""
    global _cursor_hidden
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QCursor
    from PyQt6.QtWidgets import QApplication
    if hidden and not _cursor_hidden:
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.BlankCursor))
        _cursor_hidden = True
    elif not hidden and _cursor_hidden:
        QApplication.restoreOverrideCursor()
        _cursor_hidden = False


def extra_mpv_opts() -> dict:
    """Extra mpv options for macOS (hardware decoding)."""
    return {"hwdec": "videotoolbox-copy"}


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


def libmpv_install_hint() -> str:
    """Installation hint shown when libmpv is missing on macOS."""
    return "\n  Install with: brew install mpv && pip install python-mpv"


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
