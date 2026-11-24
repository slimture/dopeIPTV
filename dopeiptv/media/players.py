"""External playback: launch an external mpv/VLC process, plus libmpv
detection used by the embedded player."""

from __future__ import annotations

import os
import subprocess
import sys

from ..core import offline
from ..core.log import log, redact_url
from ..core.privacy import for_log
from ..core.xdg import system_env

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QMessageBox

from ..i18n import english, tr

from ..core.player_exec import HANDOFF_S as _HANDOFF_S
from ..core.player_exec import (address_scheme, carries_login, fetchable,
                                find_player_executable, handed_privately, private_file)

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


def numeric_c() -> bool:
    """Whether LC_NUMERIC is "C" now - set so first when it is not.

    libmpv makes no core under another numeric locale: mpv_create() says
    "Non-C locale detected" and hands back NULL, and python-mpv passes that
    NULL straight on to mpv_set_option_string - a segfault (crash report
    a8f41ba99eed: Nerd's check of the mpv options made its core on a
    worker before the player had made one, on a system in Swedish). Qt's
    QApplication sets every category from the environment on Unix - a
    LC_ALL there wins over the LC_NUMERIC the start puts beside it - and a
    native file dialog can again, so every core is made after this; Qt and
    Python format numbers without the C locale, so nothing else changes.
    Asked first and set only when it is not "C": the locale is the whole
    process's, and a worker should not write it while nothing needs it."""
    import locale
    try:
        was = locale.setlocale(locale.LC_NUMERIC)
        if was == "C":
            return True
        locale.setlocale(locale.LC_NUMERIC, "C")
        log.info("LC_NUMERIC was %s: set to C for libmpv", was)
        return locale.setlocale(locale.LC_NUMERIC) == "C"
    except locale.Error as e:
        log.warning("could not set LC_NUMERIC=C: %s", e)
        return False


def throwaway_mpv():
    """A core that shows and plays nothing, to try options on (Nerd's
    checks); None without libmpv, or where libmpv would make none."""
    if _libmpv is None or not numeric_c():
        return None
    return _libmpv.MPV(vo="null", ao="null", terminal=False, idle=True)


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


_TELETEXT_DECODER = "libzvbi_teletextdec"
_teletext_answer: list[bool | None] = []


def _loaded_avcodec() -> list[str]:
    """The libavcodec files loaded in this process (libmpv's), where the
    system tells: /proc/self/maps on Linux, dyld's images on macOS, the
    usual DLL names on Windows."""
    found: list[str] = []
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/self/maps", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    path = line.rsplit(" ", 1)[-1].strip()
                    if "libavcodec" in os.path.basename(path) and path not in found:
                        found.append(path)
        elif sys.platform == "darwin":
            import ctypes
            dyld = ctypes.CDLL(None)
            dyld._dyld_image_count.restype = ctypes.c_uint32
            dyld._dyld_get_image_name.restype = ctypes.c_char_p
            dyld._dyld_get_image_name.argtypes = [ctypes.c_uint32]
            for i in range(dyld._dyld_image_count()):
                name = (dyld._dyld_get_image_name(i) or b"").decode("utf-8", "replace")
                if "libavcodec" in os.path.basename(name):
                    found.append(name)
        elif sys.platform == "win32":
            import ctypes
            kernel = ctypes.WinDLL("kernel32")
            kernel.GetModuleHandleW.restype = ctypes.c_void_p
            for v in range(58, 64):
                if kernel.GetModuleHandleW(f"avcodec-{v}.dll"):
                    found.append(f"avcodec-{v}.dll")
    except Exception as e:
        log.debug("teletext: libavcodec not looked up: %s", e)
    return found


def teletext_decoder() -> bool | None:
    """Can the FFmpeg inside this libmpv decode teletext - was it built with
    libzvbi? True / False, or None when it cannot tell (an FFmpeg linked
    into libmpv itself, as on Windows): then a track that will not play
    says so once tried (media/teletext_subs.py). Asked once."""
    if _teletext_answer:
        return _teletext_answer[0]
    answer: bool | None = None
    if _libmpv is not None:
        import ctypes
        libs: list[object] = [getattr(_libmpv, "backend", None)]
        for path in _loaded_avcodec():
            try:
                libs.append(ctypes.CDLL(path))
            except OSError:
                continue
        for lib in libs:
            find = getattr(lib, "avcodec_find_decoder_by_name", None) if lib is not None else None
            if find is None:
                continue
            try:
                find.restype = ctypes.c_void_p
                find.argtypes = [ctypes.c_char_p]
                answer = bool(find(_TELETEXT_DECODER.encode()))
            except Exception as e:
                log.debug("teletext: decoder not asked: %s", e)
                continue
            if answer:
                break
    log.info("teletext decoder (libzvbi) in libmpv's FFmpeg: %s",
             {True: "yes", False: "no", None: "cannot tell"}[answer])
    _teletext_answer.append(answer)
    return answer


# Spawning a system player from inside a frozen bundle needs the bundle's
# library paths stripped first, or the player loads OUR Qt/libmpv/ffmpeg and
# never starts. That now lives in core.xdg, because opening a LINK has
# exactly the same problem and exactly the same cure.
_system_env = system_env


def player_command(player: str, exe: str, url: str, title: str,
                   handoff: str = "") -> list[str]:
    """The external player's command line for *url* - or, given *handoff*,
    for the playlist file that holds it (``handoff_file``).

    The address comes after "--", which ends the options for mpv and VLC
    alike. A playlist's line is whatever its author wrote, and one that
    starts with a dash was read as an option of the player's:
    "--log-file=~/.bashrc" overwrote that file with mpv's log - the title,
    also the playlist's, written into it - and "--include=http://..." took
    mpv's whole configuration from a stranger's server. After "--" such a
    line is a file that does not open, and nothing else.

    With a handoff the title is the playlist's own line (#EXTINF), which
    both players show, and stands nowhere in the command line: every
    account on a Linux computer reads that, and a title watched privately
    is not theirs to read. A file's path is there anyway, so a file keeps
    its title beside it."""
    if player == "mpv":
        if handoff:
            return [exe, "--user-agent=dopeIPTV/1.0", "--playlist=" + handoff]
        return [exe, "--force-media-title=" + title, "--user-agent=dopeIPTV/1.0", "--", url]
    if handoff:
        return [exe, "--http-user-agent=dopeIPTV/1.0", "--", handoff]
    return [exe, "--meta-title", title, "--http-user-agent=dopeIPTV/1.0", "--", url]


#: How long a handoff file waits for its player before it is deleted: the
#: player reads it as it starts.
HANDOFF_S = _HANDOFF_S
_HANDOFF_PREFIX = "dopeiptv-play-"


def handoff_file(url: str, title: str, folder: str | None = None) -> str:
    """A one-entry playlist holding *url*, this user's alone (0600, a name
    nobody can guess, in their runtime folder on Linux), deleted once its
    player has had HANDOFF_S to read it; "" when it cannot be made. An
    http(s) address, and any other with a login in it, goes to an external
    player this way, never as an argument: every account on a Linux
    computer can read a process's command line (/proc/<pid>/cmdline), and
    an Xtream address there is its login."""
    if any(c in url for c in "\r\n"):
        return ""
    name = "".join(c for c in title if c.isprintable()) or "dopeIPTV"
    return private_file(f"#EXTM3U\n#EXTINF:-1,{name}\n{url}\n", _HANDOFF_PREFIX, ".m3u",
                        folder, keep=HANDOFF_S)


def handed_over(url: str) -> bool:
    """Whether *url* goes to an external player in a handoff file: an
    http(s) address, any other network address but a multicast group's,
    and a group's with a login in it (player_exec.handed_privately)."""
    scheme = address_scheme(url)
    return scheme in ("http", "https") or handed_privately(url) or (
        scheme not in ("", "file") and carries_login(url))


def allowed_page(player: str, url: str) -> bool:
    """mpv's own word for a page it opens through yt-dlp (ytdl://<a web
    address>), which the app itself hands it (core/ytdlp.mpv_address)."""
    if player != "mpv" or not url.startswith("ytdl://"):
        return False
    rest = url[len("ytdl://"):]
    return rest.lower().startswith(("http://", "https://")) and fetchable(rest)


def launch_player(player: str, url: str, title: str | None = None,
                  parent: object = None) -> None:
    """Spawn an external mpv or VLC process - never on an address offline
    mode refuses."""
    log.info("launching EXTERNAL %s for %s", player,
             for_log(title or redact_url(url)))
    title = title or "dopeIPTV"
    name = "mpv" if player == "mpv" else "VLC"
    if not fetchable(url) and not allowed_page(player, url):
        # No stream's address and no file's path (player_exec.fetchable): a
        # playlist's line that would have the player read this computer's
        # files by another name, or one that starts with a dash. Never
        # handed to a player, whatever "--" then does with it.
        log.warning("external %s not started: not a stream's address (%s:)",
                    name, address_scheme(url)[:20] or "-")
        QMessageBox.warning(parent, name, tr("open_address_bad"))
        return
    if offline.stream_refusal(url):
        # Offline mode: the player (an external mpv's own yt-dlp too) would
        # go to the internet itself - said as the window's door says it.
        log.info("external %s not started: offline mode - it needs the network", name)
        say = getattr(parent, "_offline_refused", None)
        if callable(say):
            say("an external player")
        else:
            QMessageBox.information(parent, name, tr("offline_needs_network"))
        return
    exe = find_player_executable(player)
    if not exe:
        QMessageBox.warning(parent, tr("status_player_not_found"),
                            tr("status_player_not_found_msg", name=name))
        return
    handoff = handoff_file(url, title) if handed_over(url) else ""
    if handed_over(url) and not handoff:
        # Never the address in its command line instead, where every
        # account on this computer could read it: not started, and said.
        log.warning("external %s not started: no private file for its address", name)
        QMessageBox.warning(parent, name, tr("status_player_no_handoff", name=name))
        return
    cmd = player_command(player, exe, url, title, handoff)
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


