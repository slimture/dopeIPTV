"""External playback: launch an external mpv/VLC process, plus libmpv
detection used by the embedded player."""

from __future__ import annotations

import os
import subprocess
import sys

from ..core.log import log, redact_url
from ..core.xdg import system_env

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QMessageBox

from ..i18n import english, tr

from ..core.player_exec import find_player_executable

_libmpv_error: str | None = None


def _prepare_bundled_libmpv() -> None:
    """Point python-mpv at the libmpv we ship inside the frozen bundle.

    python-mpv finds libmpv with ctypes.util.find_library('mpv'), which only
    searches the *system* library path - never our PyInstaller bundle. So on a
    machine with no system mpv installed (exactly the machine bundling libmpv
    is meant to serve) it raised "Cannot find libmpv in the usual places" and
    the embedded player was silently disabled. It only ever worked where the
    user happened to have mpv installed system-wide - Arch, Homebrew, the CI
    runner - which is why "it works on my machine" but nowhere else. When
    frozen, monkeypatch find_library so 'mpv' resolves to the shipped file.
    A plain source run isn't frozen, so this is a no-op there and the normal
    system lookup applies."""
    if not getattr(sys, "frozen", False):
        return
    import ctypes.util
    if sys.platform == "darwin":
        soname = "libmpv.2.dylib"
    elif sys.platform == "win32":
        soname = "mpv-2.dll"
    else:
        soname = "libmpv.so.2"
    candidates = [getattr(sys, "_MEIPASS", None),
                  os.path.dirname(sys.executable)]
    bundled = next((os.path.join(d, soname) for d in candidates
                    if d and os.path.exists(os.path.join(d, soname))), None)
    if not bundled:
        return
    _orig = ctypes.util.find_library

    def _find(name):
        if name == "mpv":
            return bundled
        return _orig(name)

    ctypes.util.find_library = _find


if sys.platform == "darwin":
    from ..core.platform_macos import find_libmpv
    find_libmpv()
elif sys.platform == "win32":
    from ..core.platform_windows import find_libmpv
    find_libmpv()

_prepare_bundled_libmpv()

try:
    import mpv as _libmpv
except Exception as _e:
    _libmpv = None
    _libmpv_error = f"{type(_e).__name__}: {_e}"


# Set by probe_opengl() when the display offers no OpenGL at all.
_gl_error: str | None = None


def _glx_usable() -> bool | None:
    """Does the X server behind $DISPLAY offer a GLX window config at all?

    Asked through Xlib/GLX directly, because asking Qt is not safe: its GLX
    integration calls qFatal ("Could not initialize GLX") when it finds no
    matching FBConfig, and that aborts the process even for a bare
    QOpenGLContext. The attributes are the minimum Qt falls back to (RGBA,
    window-drawable, one bit per colour), so "none" here means Qt would
    fail too. False only on a definite no; None when it cannot tell - then
    nothing is disabled."""
    import ctypes
    try:
        x11 = ctypes.CDLL("libX11.so.6")
    except OSError:
        return None
    try:
        gl = ctypes.CDLL("libGL.so.1")
    except OSError:
        return False          # no GL library: Qt's GLX integration cannot load
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XDefaultScreen.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x11.XFree.argtypes = [ctypes.c_void_p]
    gl.glXQueryExtension.argtypes = [ctypes.c_void_p,
                                     ctypes.POINTER(ctypes.c_int),
                                     ctypes.POINTER(ctypes.c_int)]
    gl.glXChooseFBConfig.restype = ctypes.c_void_p
    gl.glXChooseFBConfig.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                     ctypes.POINTER(ctypes.c_int),
                                     ctypes.POINTER(ctypes.c_int)]
    dpy = x11.XOpenDisplay(None)
    if not dpy:
        return None
    try:
        err, ev = ctypes.c_int(), ctypes.c_int()
        if not gl.glXQueryExtension(dpy, ctypes.byref(err), ctypes.byref(ev)):
            return False
        # GLX_DRAWABLE_TYPE=WINDOW, GLX_RENDER_TYPE=RGBA, R/G/B >= 1, None
        attrs = (ctypes.c_int * 11)(0x8010, 1, 0x8011, 1, 8, 1, 9, 1, 10, 1,
                                    0)
        n = ctypes.c_int(0)
        cfgs = gl.glXChooseFBConfig(dpy, x11.XDefaultScreen(dpy), attrs,
                                    ctypes.byref(n))
        if cfgs:
            x11.XFree(cfgs)
        return n.value > 0
    finally:
        x11.XCloseDisplay(dpy)


def probe_opengl(platform: str) -> None:
    """Find out, before any video surface exists, whether this display can
    give us OpenGL at all.

    On X11 without a usable GLX - a virtual machine with no 3D acceleration,
    a VNC or remote X session, a headless test runner - Qt does not fail a
    video widget gracefully: it aborts the whole process ("Could not
    initialize GLX") the moment one is created. With the answer in hand the
    window is built without the embedded player, so the app opens and plays
    through an external player instead of dying on launch. Only X11 is
    asked; elsewhere nothing changes."""
    global _gl_error
    if platform != "xcb":
        return
    if "egl" in os.environ.get("QT_XCB_GL_INTEGRATION", "").lower():
        return                # EGL chosen explicitly: GLX is not in play
    try:
        usable = _glx_usable()
    except Exception as e:    # never let the probe itself stop start-up
        log.warning("OpenGL probe failed: %s", e)
        return
    if usable is False:
        _gl_error = ("this display offers no usable OpenGL (GLX), so video "
                     "opens in an external player")


def opengl_available() -> bool:
    """False only when probe_opengl() found no OpenGL on this display."""
    return _gl_error is None


def embedded_playback_reason(localized: bool = False) -> str | None:
    """None when in-app video works; otherwise why not - in English for the
    log, or in the user's language (*localized*) for Settings."""
    say = tr if localized else english
    if _gl_error:
        return say("play_err_no_gl")
    if _libmpv is None:
        hint = ""
        if sys.platform == "darwin":
            from ..core.platform_macos import libmpv_install_hint
            hint = libmpv_install_hint(say)
        elif sys.platform == "win32":
            from ..core.platform_windows import libmpv_load_hint
            hint = libmpv_load_hint(say)
        return say("play_err_libmpv", error=_libmpv_error) + hint
    if not hasattr(_libmpv, "MpvRenderContext"):
        return say("play_err_old_mpv")
    return None


def embedded_playback_supported() -> bool:
    return embedded_playback_reason() is None


# Spawning a system player from inside a frozen bundle needs the bundle's
# library paths stripped first, or the player loads OUR Qt/libmpv/ffmpeg and
# never starts. That now lives in core.xdg, because opening a LINK has
# exactly the same problem and exactly the same cure.
_system_env = system_env


def launch_player(player: str, url: str, title: str | None = None,
                  parent: object = None) -> None:
    """Spawn an external mpv or VLC process."""
    log.info("launching EXTERNAL %s for %s", player,
             title or redact_url(url))
    title = title or "dopeIPTV"
    exe = find_player_executable(player)
    if player == "mpv":
        cmd = [exe, "--force-media-title=" + title,
               "--user-agent=dopeIPTV/1.0", url] if exe else None
        name = "mpv"
    else:
        cmd = [exe, "--meta-title", title, "--http-user-agent=dopeIPTV/1.0",
               url] if exe else None
        name = "VLC"
    if not cmd:
        QMessageBox.warning(parent, tr("status_player_not_found"),
                            tr("status_player_not_found_msg", name=name))
        return
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, env=_system_env())


def _register_error_callback(mpv_instance: object, signal: pyqtSignal,
                             eof_signal: pyqtSignal | None = None) -> None:
    """Emit *signal* when a loaded file/stream ends with an error, and
    *eof_signal* when one ends normally.

    mpv's end-file event is the authoritative "this file is over" - it is
    delivered exactly once, whatever the options say. The player also polls
    the eof-reached property, but that one only ever becomes true while
    keep-open is in force and only if a poll happens to catch it, so on its
    own it is not something autoplay can be built on. Both routes funnel
    through the same once-only guard in the player."""
    @mpv_instance.event_callback("end-file")
    def _on_end_file(evt):
        try:
            data = evt.data
            reason = getattr(data, "reason", None)
            if reason == _libmpv.MpvEventEndFile.ERROR:
                try:
                    msg = _libmpv.ErrorCode.human_readable(data.error)
                except Exception:
                    msg = "playback failed"
                signal.emit(msg)
            elif (reason == _libmpv.MpvEventEndFile.EOF
                    and eof_signal is not None):
                # Fires on mpv's event thread; Qt queues the emit onto the
                # main thread, exactly as the error path above already does.
                eof_signal.emit()
        except Exception:
            pass


