"""Windows platform support: libmpv discovery, OpenGL context, wakelock.

Mirrors ``platform_macos`` for the Windows target. Everything here is only
reached when ``sys.platform == "win32"``; the module is import-safe on other
platforms (no Windows-only symbols are touched at import time), so the test
suite can import it anywhere.
"""
from __future__ import annotations

import glob
import os
import sys


def _libmpv_candidate_dirs() -> list[str]:
    """Where a bundled ``mpv-2.dll`` may sit: the frozen bundle, the exe's
    dir, the repo's ``libmpv\\`` (source runs), a ``libmpv\\`` subdir."""
    frozen = getattr(sys, "frozen", False)
    meipass = getattr(sys, "_MEIPASS", None)
    exe_dir = os.path.dirname(sys.executable) if frozen else ""
    repo_libmpv = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "libmpv")
    candidates = [meipass, exe_dir, repo_libmpv,
                  os.path.join(meipass or "", "libmpv") if meipass else ""]
    return list(dict.fromkeys(c for c in candidates if c))


def _holds_libmpv(d: str) -> bool:
    return os.path.isdir(d) and bool(
        glob.glob(os.path.join(d, "mpv-*.dll"))
        or glob.glob(os.path.join(d, "libmpv-*.dll")))


def bundled_libmpv_dir() -> str | None:
    """The first candidate directory that actually holds a libmpv DLL."""
    return next((d for d in _libmpv_candidate_dirs() if _holds_libmpv(d)),
                None)


def find_libmpv() -> None:
    """Make a bundled ``mpv-2.dll`` loadable by python-mpv.

    python-mpv does ``CDLL("mpv-2.dll")`` on Windows, which searches the DLL
    path but not our PyInstaller bundle. Register the directory that holds the
    DLL (the frozen bundle, the exe's dir, or a ``libmpv\\`` subdir) on the DLL
    search path. No-op if no bundled DLL is found - a system-wide mpv on PATH
    still works then.
    """
    d = bundled_libmpv_dir()
    if d is None:
        return
    try:
        os.add_dll_directory(d)          # Python 3.8+
    except (OSError, AttributeError):
        pass
    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def loaded_module_path(name: str) -> str | None:
    """Full path of the DLL *name* as loaded in THIS process, or None if it
    is not loaded (or this is not Windows). Lets the self-check prove that
    a dependency came from the bundle rather than from the build machine."""
    if sys.platform != "win32":
        return None
    import ctypes
    try:
        k32 = ctypes.windll.kernel32
        k32.GetModuleHandleW.restype = ctypes.c_void_p
        k32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        k32.GetModuleFileNameW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
        handle = k32.GetModuleHandleW(name)
        if not handle:
            return None
        buf = ctypes.create_unicode_buffer(32768)
        if not k32.GetModuleFileNameW(handle, buf, len(buf)):
            return None
        return buf.value
    except Exception:
        return None


def setup_opengl() -> None:
    """Enable GL context sharing for the libmpv render widget.

    We deliberately do NOT force a global desktop-OpenGL surface format here.
    Setting ``QSurfaceFormat.setDefaultFormat()`` / ``AA_UseDesktopOpenGL``
    app-wide made Qt composite *every* top-level window through that one
    context, and on some machines that left the whole UI black (only the native
    window chrome drawn). The player's ``QOpenGLWidget`` negotiates a working
    context on its own - exactly like the Linux build, which sets nothing - so
    all we do is allow context sharing.
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)


class WakeLockWindows:
    """Keep the display and system awake via SetThreadExecutionState."""

    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    ES_DISPLAY_REQUIRED = 0x00000002

    def __init__(self) -> None:
        self._held = False

    @property
    def held(self) -> bool:
        return self._held

    def acquire(self, reason: str = "Playing video") -> None:
        import ctypes
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(
                self.ES_CONTINUOUS | self.ES_SYSTEM_REQUIRED
                | self.ES_DISPLAY_REQUIRED)
            self._held = True
        except Exception:
            pass

    def release(self) -> None:
        import ctypes
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(self.ES_CONTINUOUS)
        except Exception:
            pass
        self._held = False


def create_shortcut(desktop: bool = True, start_menu: bool = True,
                    name: str = "dopeIPTV") -> list[str]:
    """Create .lnk shortcuts to the running exe (Start menu and/or desktop),
    so the portable build feels installed without an installer. Returns the
    shortcut paths created. A no-op returning [] off Windows or from a source
    run. Uses PowerShell's WScript.Shell COM, so it needs no extra dependency
    and writes nothing to the registry - each shortcut is a single file the
    user can delete."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return []
    import subprocess

    exe = sys.executable
    workdir = os.path.dirname(exe)
    targets = []
    if start_menu and os.environ.get("APPDATA"):
        targets.append(os.path.join(
            os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu",
            "Programs", f"{name}.lnk"))
    if desktop and os.environ.get("USERPROFILE"):
        targets.append(os.path.join(
            os.environ["USERPROFILE"], "Desktop", f"{name}.lnk"))

    def _q(s: str) -> str:                 # PowerShell single-quote escaping
        return s.replace("'", "''")

    made: list[str] = []
    for lnk in targets:
        try:
            os.makedirs(os.path.dirname(lnk), exist_ok=True)
            ps = (
                "$w=New-Object -ComObject WScript.Shell;"
                f"$s=$w.CreateShortcut('{_q(lnk)}');"
                f"$s.TargetPath='{_q(exe)}';"
                f"$s.WorkingDirectory='{_q(workdir)}';"
                f"$s.IconLocation='{_q(exe)},0';"
                "$s.Save()"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                creationflags=0x08000000,   # CREATE_NO_WINDOW - no console flash
                timeout=15, check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            made.append(lnk)
        except Exception:
            pass
    return made


def libmpv_load_hint(say=None) -> str:
    """What to tell the user when python-mpv could not load libmpv.

    Shown after the raw error in Settings > Playback and in the log, so it
    has to say something a user can act on. Two failures look identical from
    python-mpv's side ("could not load it"):

    - the DLL is gone - an antivirus quarantined it (Defender flags
      PyInstaller apps, see WINDOWS-README.txt), or the exe was moved out
      of its folder;
    - the DLL is there but Windows refused it because a DLL *it* needs is
      missing. libmpv hard-links ``vulkan-1.dll``, the Vulkan loader, which
      Windows itself does not ship - graphics drivers install it, so a
      driver older than Vulkan, a VM or the basic display adapter has none.
      Releases now bundle the loader beside mpv-2.dll (issue #19); the
      check stays for older builds and for a bundle that lost the file.
    """
    from ..i18n import english
    say = say or english
    d = bundled_libmpv_dir()
    if d is None:
        if getattr(sys, "frozen", False):
            return " " + say("play_hint_win_quarantined")
        return " " + say("play_hint_win_missing")
    system32 = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    vulkan = next((os.path.join(p, "vulkan-1.dll") for p in (d, system32)
                   if os.path.isfile(os.path.join(p, "vulkan-1.dll"))), None)
    if vulkan is None:
        return " " + say("play_hint_win_no_vulkan", folder=d)
    return " " + say("play_hint_win_other", folder=d, vulkan=vulkan)
