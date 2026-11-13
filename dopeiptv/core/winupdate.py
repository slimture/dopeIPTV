"""Update an installed dopeIPTV on Windows with the next installer.

The installer (packaging/windows/dopeiptv.nsi) installs per user, so the
app may run it itself, without an administrator:

1. download the release's installer for this build's CPU into the temp
   folder, checking its size and SHA-256 (``selfupdate.download``);
2. once this process has exited - so its settings are written - run it
   silently with ``/relaunch``: it installs over this copy, in the folder
   the last install recorded, and opens the new version.

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

# The question asked once the installer is downloaded (i18n key).
DONE_KEY = "update_done_install"
# The download is checked as it arrives; there is no start to test first.
CHECKS_START = False

_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000


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


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _child_env() -> dict:
    """The environment for the installer and the app it opens: nothing of
    this frozen process's own (PyInstaller's variables), so the new copy
    starts as it would from the Start menu."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("_PYI", "_MEI"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def restart_into(setup: Path, pid: int | None = None) -> None:
    """Run *setup* silently once this process (*pid*) has exited, so the
    settings are written before files are replaced; it opens the new
    version when done. Gives up after five minutes: an instance that never
    exits must not have its files replaced under it."""
    pid = pid or os.getpid()
    script = (f"Wait-Process -Id {pid} -Timeout 300 -ErrorAction SilentlyContinue; "
              f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{ exit 1 }}; "
              f"Start-Process -FilePath {_ps_quote(str(setup))} -ArgumentList '/S /relaunch'")
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-WindowStyle", "Hidden", "-Command", script],
        env=_child_env(), close_fds=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=_CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW)
    log.info("update: %s runs once this instance (%s) has exited", setup.name, pid)


def cleanup() -> None:
    """At start: the installers an earlier update left in the temp folder.
    One still running (it has just opened us) stays until next time."""
    shutil.rmtree(update_folder(), ignore_errors=True)
