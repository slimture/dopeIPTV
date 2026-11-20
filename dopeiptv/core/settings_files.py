"""The app's settings files kept to the person who runs it.

QSettings makes its files with 0666 less the umask - 0644 and a 0755
folder under the usual umask - and they hold every playlist's login, the
sync code, the remote API's, phone remote's and server's tokens, Trakt's
and Simkl's. Another account on the same computer could read them wherever
the home folder lets it in.

So once at start, before anything writes them, each file is set to 0600 and
the app's own folder to 0700. A file not there yet is made empty at 0600
first: Qt keeps an existing file's mode when it saves it again (QSaveFile),
so even the first write is never wider - and an empty INI file reads as no
keys. Modes are only ever narrowed: what the owner may do stays as it was.

Linux and macOS only. On Windows the main settings are a registry key and
the files sit in the user's own profile; nothing is done there. A macOS
plist (the main settings there) is never made here and its folder, the
system's ~/Library/Preferences, is left alone.

A file system that keeps no permissions (a FAT or exFAT drive, some network
shares) refuses the change or takes it and keeps the old mode. The app
starts all the same: the file is logged by its name (never what it holds)
and kept in ``not_private``; the window asks in a window that waits for an
answer - close, or go on with that file (ACCEPTED_KEY) - and says so each
start after (ui/mw_settings.py); server mode without a window does not start
(headless.py) until the person says so.
"""
from __future__ import annotations

import os
import stat
import sys
import threading
from typing import Any, Mapping

from .log import log

FILE_MODE = 0o600
FOLDER_MODE = 0o700

#: The settings files this start could not keep to this user, in order.
_not_private: list[str] = []
#: The file the person said to go on with, readable by others all the same
#: (accepted_mark): asked again for any other file, or a moved one.
ACCEPTED_KEY = "settings_open_accepted"
_lock = threading.Lock()


def _narrow(path: str, mode: int) -> bool:
    """*path*'s mode narrowed to within *mode*; whether it is now - a file
    system without permissions takes the change and keeps the old mode."""
    now = stat.S_IMODE(os.stat(path).st_mode)
    if now & ~mode:
        os.chmod(path, now & mode)
        now = stat.S_IMODE(os.stat(path).st_mode)
    return not now & ~mode


def _note(path: str, why: str) -> None:
    log.warning("settings: %s could not be kept to this user (%s) - it holds logins; "
                "another account on this computer may be able to read it", path, why)
    with _lock:
        if path not in _not_private:
            _not_private.append(path)


def accepted_mark(path: str) -> str:
    """What ACCEPTED_KEY holds for *path*: its hash, not the path itself (a
    backup or a report of the settings names no folder)."""
    import hashlib
    return hashlib.sha256(os.path.abspath(path).encode("utf-8", "replace")).hexdigest()[:32]


def not_private() -> list[str]:
    """The settings files this start could not make readable by this user
    alone (their paths; empty when every one is)."""
    with _lock:
        return list(_not_private)


def keep_private(path: str, *, folder: bool = True, create: bool = True) -> bool:
    """*path* readable by this user alone, and with *folder* the folder it is
    in too (made at 0700 when missing). *create*: a missing file made empty
    at 0600 first. Never raises; False when it could not be done - logged
    with the file's name and kept in not_private()."""
    if os.name != "posix" or not path:
        return True
    try:
        kept = True
        if folder:
            parent = os.path.dirname(path)
            os.makedirs(parent, mode=FOLDER_MODE, exist_ok=True)
            kept = _narrow(parent, FOLDER_MODE)
        if create and not os.path.lexists(path):
            try:
                os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE))
            except FileExistsError:
                pass
        if os.path.exists(path):
            kept = _narrow(path, FILE_MODE) and kept
        if not kept:
            _note(path, "its drive keeps no permissions")
        return kept
    except OSError as e:
        _note(path, e.strerror or type(e).__name__)
        return False


def protect(stores: Mapping[str, Any] | None = None) -> None:
    """Every settings file of the app kept to this user (core/backup's
    open_stores: the main settings, resume, follow, cache). Never in the way
    of starting."""
    if os.name != "posix":
        return
    try:
        from PyQt6.QtCore import QSettings

        from .. import ORG
        if stores is None:
            from .backup import open_stores
            stores = open_stores()
        for s in stores.values():
            path = str(s.fileName())
            plain = s.format() == QSettings.Format.IniFormat or sys.platform != "darwin"
            own = os.path.basename(os.path.dirname(path)) == ORG
            keep_private(path, folder=plain and own, create=plain)
    except Exception as e:
        log.warning("settings: not kept to this user (%s)", e)
