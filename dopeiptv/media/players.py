"""External playback: launch an external mpv/VLC process, plus libmpv
detection used by the embedded player."""

from __future__ import annotations

import os
import subprocess
import sys

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QMessageBox

from ..i18n import tr

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


def embedded_playback_reason() -> str | None:
    """Returns None if in-app video is available, otherwise a short explanation."""
    if _libmpv is None:
        hint = ""
        if sys.platform == "darwin":
            from ..core.platform_macos import libmpv_install_hint
            hint = libmpv_install_hint()
        return (f"python-mpv/libmpv failed to load ({_libmpv_error})"
                + hint)
    if not hasattr(_libmpv, "MpvRenderContext"):
        return "installed python-mpv is too old (needs the render-api support)"
    return None


def embedded_playback_supported() -> bool:
    return embedded_playback_reason() is None


def _system_env() -> dict:
    """Environment for spawning a SYSTEM binary (mpv/VLC) from inside a
    frozen bundle. PyInstaller/AppImage prepend our bundled libraries to
    the loader/plugin paths; a system player that inherits those loads
    OUR Qt/libmpv/ffmpeg instead of its own and fails to start - which is
    why external mpv "doesn't work" from the AppImage/.deb. Restore the
    pre-launch values PyInstaller stashes as *_ORIG, or drop the vars
    entirely, so the child runs against the real system libraries. On a
    plain source run none of these are set, so this is a no-op there."""
    env = dict(os.environ)
    for var in ("LD_LIBRARY_PATH", "LD_PRELOAD", "QT_PLUGIN_PATH",
                "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH",
                "GST_PLUGIN_SYSTEM_PATH", "GST_PLUGIN_PATH", "GTK_PATH",
                "GDK_PIXBUF_MODULE_FILE", "FONTCONFIG_FILE",
                "FONTCONFIG_PATH", "PYTHONHOME", "PYTHONPATH"):
        orig = env.get(var + "_ORIG")
        if orig:
            env[var] = orig
        else:
            env.pop(var, None)
    return env


def launch_player(player: str, url: str, title: str | None = None,
                  parent: object = None) -> None:
    """Spawn an external mpv or VLC process."""
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


def _register_error_callback(mpv_instance: object, signal: pyqtSignal) -> None:
    """Emit *signal* when a loaded file/stream ends with an error. Used by the
    embedded player to surface a stream failure to the UI."""
    @mpv_instance.event_callback("end-file")
    def _on_end_file(evt):
        try:
            data = evt.data
            if getattr(data, "reason", None) == _libmpv.MpvEventEndFile.ERROR:
                try:
                    msg = _libmpv.ErrorCode.human_readable(data.error)
                except Exception:
                    msg = "playback failed"
                signal.emit(msg)
        except Exception:
            pass


