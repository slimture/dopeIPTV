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


def find_libmpv() -> None:
    """Make a bundled ``mpv-2.dll`` loadable by python-mpv.

    python-mpv does ``CDLL("mpv-2.dll")`` on Windows, which searches the DLL
    path but not our PyInstaller bundle. Register the directory that holds the
    DLL (the frozen bundle, the exe's dir, or a ``libmpv\\`` subdir) on the DLL
    search path. No-op if no bundled DLL is found - a system-wide mpv on PATH
    still works then.
    """
    frozen = getattr(sys, "frozen", False)
    meipass = getattr(sys, "_MEIPASS", None)
    exe_dir = os.path.dirname(sys.executable) if frozen else ""
    repo_libmpv = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "libmpv")
    candidates = [meipass, exe_dir, repo_libmpv,
                  os.path.join(meipass or "", "libmpv") if meipass else ""]
    for d in dict.fromkeys(c for c in candidates if c):
        if not os.path.isdir(d):
            continue
        if glob.glob(os.path.join(d, "mpv-*.dll")) or glob.glob(
                os.path.join(d, "libmpv-*.dll")):
            try:
                os.add_dll_directory(d)          # Python 3.8+
            except (OSError, AttributeError):
                pass
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            return


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


def libmpv_install_hint() -> str:
    """User-facing hint when libmpv can't be loaded (dev runs without the DLL)."""
    return ("mpv-2.dll was not found. The installer bundles it; for a source "
            "run, put mpv-2.dll next to the app or on your PATH "
            "(https://mpv.io/installation/).")
