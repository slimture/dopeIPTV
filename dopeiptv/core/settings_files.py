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
"""
from __future__ import annotations

import os
import stat
import sys
from typing import Any, Mapping

from .log import log

FILE_MODE = 0o600
FOLDER_MODE = 0o700


def _narrow(path: str, mode: int) -> None:
    now = stat.S_IMODE(os.stat(path).st_mode)
    if now & ~mode:
        os.chmod(path, now & mode)


def keep_private(path: str, *, folder: bool = True, create: bool = True) -> bool:
    """*path* readable by this user alone, and with *folder* the folder it is
    in too (made at 0700 when missing). *create*: a missing file made empty
    at 0600 first. Never raises; False when it could not be done (logged)."""
    if os.name != "posix" or not path:
        return True
    try:
        if folder:
            parent = os.path.dirname(path)
            os.makedirs(parent, mode=FOLDER_MODE, exist_ok=True)
            _narrow(parent, FOLDER_MODE)
        if create and not os.path.lexists(path):
            try:
                os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE))
            except FileExistsError:
                pass
        if os.path.exists(path):
            _narrow(path, FILE_MODE)
        return True
    except OSError as e:
        log.warning("settings: %s not kept to this user (%s)", path, e)
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
