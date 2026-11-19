"""Settings -> Reset all, the folders' part: what the app keeps on disk
besides its settings files, emptied - so a reset leaves nothing of what the
person had or watched.

  the cache   pictures (logos, posters, frames of videos and recordings),
              downloaded guides and their indexes, the local library's
              index, subtitle copies, the week's popular titles, rewind
              buffers (QStandardPaths' CacheLocation)
  the logs    the log files Nerd's "Save a log file" writes and the native
              crash file (core/log.log_dir)
  yt-dlp      the copy the app downloaded itself (core/ytdlp.managed_dir)

The person's recordings stay - their files, in the folder they chose, which
is left alone even should it be inside one of these (keep) - and so does
the synced copy at dopeiptv.org: their other computers use it, and it is
deleted there after 180 days unused.

Emptied twice: on the window's way out, once the player and every worker
have let go (wipe), and at the next start before anything opens them again
(finish) - for what was still being written, or on Windows still open. A
marker beside the settings files (MARKER, request) carries it from the one
to the other, with what to keep.

Only a folder that is the app's own is touched (owned): its path names
dopeiptv near its end, and it is neither the home folder nor anything above
it - an empty or odd answer from the system is left alone. A link inside is
removed, never followed.
"""
from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterable
from pathlib import Path

from .log import log

#: Beside the settings files: a reset whose folders are to be emptied again
#: at the next start.
MARKER = "reset-pending"


def owned(path: str | Path) -> bool:
    """Whether *path* is a folder of the app's own to empty."""
    try:
        p = Path(path).expanduser().resolve()
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        return False
    parts = [x.lower() for x in p.parts]
    if len(parts) < 3 or not any("dopeiptv" in x for x in parts[-3:]):
        return False
    return p != home and p not in home.parents


def app_folders() -> list[Path]:
    """The folders a reset empties."""
    from . import ytdlp
    from .log import log_dir
    out: list[Path] = []
    try:
        from PyQt6.QtCore import QStandardPaths
        cache = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
        if cache:
            out.append(Path(cache))
    except Exception as e:                       # no Qt: the rest still goes
        log.info("reset: no cache folder (%s)", type(e).__name__)
    out += [Path(log_dir()), ytdlp.managed_dir()]
    return out


def _kept(path: str, keep: list[Path]) -> bool:
    """Whether *path* is one to keep, or holds one."""
    try:
        p = Path(path).resolve()
    except (OSError, RuntimeError):
        return True
    return any(p == k or p in k.parents for k in keep)


def wipe(folders: list[Path] | None = None, keep: Iterable[str | Path] = ()) -> int:
    """Empty each of *folders* (app_folders) that is the app's own, leaving
    *keep* (and what holds it) as it is; the folders themselves stay.
    Returns how many entries could not go."""
    kept: list[Path] = []
    for k in keep:
        try:
            kept.append(Path(k).expanduser().resolve())
        except (OSError, RuntimeError):
            continue
    left = 0
    for folder in app_folders() if folders is None else folders:
        if not folder.is_dir():
            continue
        if not owned(folder):
            log.warning("reset: %s is not the app's own folder - left as it is", folder)
            continue
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            if _kept(entry.path, kept):
                log.info("reset: %s kept (it holds the recordings)", entry.path)
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    shutil.rmtree(entry.path)
                else:
                    os.unlink(entry.path)
            except OSError:
                left += 1
    if left:
        log.info("reset: %d entries still in use - emptied at the next start", left)
    return left


def request(settings_folder: str | Path, keep: Iterable[str | Path] = ()) -> None:
    """Have the next start empty the folders again (finish), keeping *keep*:
    the settings that named it are gone by then."""
    try:
        os.makedirs(settings_folder, exist_ok=True)
        Path(settings_folder, MARKER).write_text(
            json.dumps({"keep": [str(k) for k in keep]}), encoding="utf-8")
    except OSError as e:
        log.warning("reset: no marker for the next start (%s)", e)


def finish(settings_folder: str | Path, folders: list[Path] | None = None) -> bool:
    """At start, before anything opens the folders: a reset's second wipe.
    Whether one was waiting."""
    marker = Path(settings_folder, MARKER)
    if not marker.exists():
        return False
    try:
        keep = json.loads(marker.read_text(encoding="utf-8")).get("keep") or []
        keep = [str(k) for k in keep if isinstance(k, str) and k]
    except (OSError, ValueError, AttributeError):
        keep = []
    wipe(folders, keep)
    try:
        marker.unlink()
    except OSError as e:
        log.warning("reset: marker not removed (%s)", e)
    log.info("reset: the app's folders emptied")
    return True
