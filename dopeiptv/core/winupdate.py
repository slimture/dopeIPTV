"""Update an installed dopeIPTV on Windows with the next installer.

The installer (packaging/windows/dopeiptv.nsi) installs per user, so the
app may run it itself, without an administrator:

1. download the release's installer for this build's CPU into the temp
   folder, checking its size and SHA-256 (``selfupdate.download``);
2. start it while this window is still in front, as an update
   (``/update``: only its progress, closing itself when done): it waits
   for this process to exit - so the settings are written first -
   installs over this copy, in the folder the last install recorded, and
   opens the new version (``/relaunch``). Started by the window in front,
   Windows lets its progress, and the new version after it, come to the
   front; started from the background, the new version opened behind
   whatever was there, and nothing showed for half a minute.

Only a copy the installer put there (``uninstall.exe`` beside the exe)
updates itself; a portable copy from the zip is never touched, and is
offered the download page instead. No Qt here; the UI drives it from a
worker.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from .log import log
from .selfupdate import UpdateError, download

# What the update dialog says once the installer is downloaded (i18n key:
# install it, and dopeIPTV opens again); the download is checked as it
# arrives, with nothing to say after it.
DONE_KEY = "update_ready_install"
VERIFY_KEY: str | None = None

_CREATE_NEW_PROCESS_GROUP = 0x00000200


def installed_dir() -> Path | None:
    """The folder of an installed copy, or None (not Windows, not built,
    or a portable copy from the zip)."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return None
    d = Path(sys.executable).resolve().parent
    return d if (d / "uninstall.exe").is_file() else None


def arch() -> str | None:
    """This build's CPU as the installer's name spells it: the bundled
    Python's, so an x64 copy running emulated on ARM stays x64."""
    plat = sysconfig.get_platform().lower()
    if plat.endswith("amd64"):
        return "x64"
    if plat.endswith("arm64"):
        return "arm64"
    return None


def can_self_update() -> bool:
    d = installed_dir()
    return d is not None and arch() is not None and os.access(d, os.W_OK)


def _bare(version: str) -> str:
    return version.strip().lstrip("vV")


def pick_asset(assets: list[dict], version: str, cpu: str) -> dict | None:
    """The release asset holding the installer for *cpu*."""
    want = f"dopeIPTV-{_bare(version)}-windows-{cpu}-setup.exe"
    for a in assets:
        if a.get("name") == want and a.get("url"):
            return a
    return None


def update_folder() -> Path:
    return Path(tempfile.gettempdir()) / "dopeIPTV-update"


def install(release: dict, current_version: str,
            progress: Callable[[int, int], None] | None = None,
            cancel: threading.Event | None = None) -> Path:
    """Download and check the release's installer; returns its path. The
    installing itself waits for :func:`restart_into`, after this process.
    Raises UpdateError, with nothing changed, on any failure."""
    cpu = arch()
    if not can_self_update() or cpu is None:
        raise UpdateError("upd_err_not_installed")
    tag = release.get("tag") or ""
    asset = pick_asset(release.get("assets") or [], tag, cpu)
    if asset is None:
        raise UpdateError("upd_err_no_setup", cpu=cpu)
    folder = update_folder()
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / asset["name"]
    download(asset, dest, progress, cancel)
    log.info("update: %s downloaded to %s", tag, dest)
    return dest


def _child_env() -> dict:
    """The environment for the installer and the app it opens: nothing of
    this frozen process's own (PyInstaller's variables), so the new copy
    starts as it would from the Start menu."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("_PYI", "_MEI"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def restart_into(setup: Path, pid: int | None = None) -> None:
    """Start *setup* now, as an update of this copy: it shows its progress,
    waits for this process (*pid*) to exit before it replaces anything -
    two minutes at most - and opens the new version when done. The caller
    closes the app straight after."""
    pid = pid or os.getpid()
    proc = subprocess.Popen(
        [str(setup), "/update", "/relaunch", f"/waitpid={pid}"],
        env=_child_env(), close_fds=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=_CREATE_NEW_PROCESS_GROUP)
    _hand_over_the_front(proc)
    log.info("update: %s started; it installs once this instance (%s) has exited", setup.name, pid)


def _hand_over_the_front(proc: subprocess.Popen) -> None:
    """While this window is still in front: let the installer take the
    front, and wait - five seconds at most - until its window is up. A
    window opened later, by a process this one no longer vouches for, went
    behind whatever was there, and the update looked like nothing at all."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.AllowSetForegroundWindow(proc.pid)
        handle = getattr(proc, "_handle", None)
        if handle is not None:
            user32.WaitForInputIdle(int(handle), 5000)
    except Exception as e:                     # a nicety; the update goes on
        log.debug("update: could not hand the front to the installer: %s", e)


def cleanup() -> None:
    """At start: the installers an earlier update left in the temp folder.
    One still running (it has just opened us) stays until next time."""
    shutil.rmtree(update_folder(), ignore_errors=True)
