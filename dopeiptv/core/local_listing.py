"""One folder of Local files read in slices, pure.

``FolderListing`` reads a folder the way its listing shows it - its
folders first, then its media files, each group by name without regard to
case, a name with a leading dot left out - in slices (``step``), so a pool
job never holds a thread for long on a network share and the rows can show
as they are read; ``cancel`` from any thread stops it at the next entry.

Three parts, each in as many slices as it takes: the folder itself read
(one listing: the names and, on most file systems, which are folders);
each entry told folder or file (a stat where the file system did not say -
a gvfs mount does not); each media file's size and time (a stat). Folders
come out as they are told, in order, and files in order too: a file whose
telling took a stat comes out at once (the entry keeps what the stat said,
so its size costs nothing more - on a gvfs share every file does), the
others once every entry is told. So the folders and the files each only
grow at their end, and folders + files is the folder's order all along.

Nothing here touches the disk before ``step``: the object is made on the
window's thread and read on the pool's.

The order a person picks for every folder (``order_rows``, ``ORDERS``,
``local_sort``): by name A-Z (the listing's own) or Z-A, the last changed
or the oldest first, the largest first, by type - folders always first,
by name; between headings each part on its own.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass

from .log import log


@dataclass
class Item:
    """One row's worth: a folder, or a media file with its size and time."""

    path: str
    is_dir: bool
    size: int = 0
    mtime: int = 0


class FolderListing:
    """The folders and media files directly in *base*."""

    #: An is_dir slower than this asked the file system: the entry holds
    #: the stat now. The disk's own cache answers in microseconds.
    ASKED = 0.0005

    def __init__(self, base: str, exts: Iterable[str]) -> None:
        self.base = base
        self.exts = tuple(e.lower() for e in exts)
        self.done = False
        #: The folder was not there (gone, or not a folder) when read.
        self.missing = False
        self._entries: list[os.DirEntry] | None = None
        self._at = 0                      # the next entry to tell
        self._files: list[os.DirEntry] = []
        self._fat = 0                     # the next media file to read
        self._dirs: set[str] = set()      # names told to be folders
        self._out: list[Item] = []
        self._cancel = threading.Event()

    # -- control -----------------------------------------------------------

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def told(self) -> bool:
        """Every entry told folder or file: media_names is the folder's."""
        return self._entries is not None and self._at >= len(self._entries)

    def step(self, budget: float = 0.25) -> bool:
        """Read on for about *budget* seconds (at least one entry). True
        once the folder is read, could not be, or the listing cancelled."""
        end = time.monotonic() + budget
        while not self.done:
            if self._cancel.is_set():
                self.done = True
                break
            if self._entries is None:
                self._entries = self._read()
            elif self._at < len(self._entries):
                self._tell(self._entries[self._at])
                self._at += 1
            elif self._fat < len(self._files):
                self._stat(self._files[self._fat])
                self._fat += 1
            else:
                self.done = True
                break
            if time.monotonic() >= end:
                break
        return self.done

    def take(self) -> list[Item]:
        """What was read since the last take, in the listing's order."""
        out, self._out = self._out, []
        return out

    def media_names(self) -> list[str]:
        """The media files' names in the listing's order - what a file of
        this folder plays on into - before each has been read: once every
        entry is told, exactly the files the listing will show; before,
        every media name not told a folder."""
        if self._entries is None:
            return []
        if self.told:
            return [e.name for e in self._files]
        return [e.name for e in self._entries
                if not e.name.startswith(".") and e.name not in self._dirs
                and e.name.lower().endswith(self.exts)]

    # -- the reading ---------------------------------------------------------

    def _read(self) -> list[os.DirEntry]:
        try:
            with os.scandir(self.base) as it:
                return sorted(it, key=lambda e: e.name.lower())
        except (FileNotFoundError, NotADirectoryError):
            self.missing = True
            return []                     # gone, or not a folder: nothing
        except OSError as err:
            log.warning("local browse failed for %s: %s", self.base, err)
            return []

    def _tell(self, e: os.DirEntry) -> None:
        if e.name.startswith("."):
            return
        t0 = time.perf_counter()
        try:
            is_dir = e.is_dir()           # follows a link, as isdir does
        except OSError:
            is_dir = False
        asked = time.perf_counter() - t0 >= self.ASKED
        if is_dir:
            self._dirs.add(e.name)
            self._out.append(Item(e.path, True))
        elif e.name.lower().endswith(self.exts):
            self._files.append(e)
            if asked and self._fat == len(self._files) - 1:
                # Its stat is in hand and no file before it waits: out now.
                self._stat(e)
                self._fat += 1

    def _stat(self, e: os.DirEntry) -> None:
        try:
            st = e.stat()                 # a link's target, as os.stat
        except OSError:
            return                        # gone, or a link to nothing
        self._out.append(Item(e.path, False, st.st_size, int(st.st_mtime)))


# -- the order picked -------------------------------------------------------------------

#: The orders Local files can be in, the first the default (the listing's).
NAME, NAME_DESC, MODIFIED, OLDEST, LARGEST, TYPE = (
    "name", "name_desc", "modified", "oldest", "largest", "type")
ORDERS = (NAME, NAME_DESC, MODIFIED, OLDEST, LARGEST, TYPE)
ORDER_KEY = "local_sort"


def order_of(settings) -> str:
    order = str(settings.value(ORDER_KEY, NAME) or NAME)
    return order if order in ORDERS else NAME


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _name(row: dict) -> str:
    return str(row.get("name") or os.path.basename(str(row.get("_path") or ""))).casefold()


def _ext(row: dict) -> str:
    return os.path.splitext(str(row.get("_path") or ""))[1].lower()


def _ordered(part: list[dict], order: str) -> list[dict]:
    folders = [r for r in part if r.get("_kind") != "local"]
    files = [r for r in part if r.get("_kind") == "local"]
    desc = order == NAME_DESC
    folders.sort(key=_name, reverse=desc)
    if order == NAME_DESC:
        files.sort(key=_name, reverse=True)
    elif order == MODIFIED:
        files.sort(key=lambda r: (-_num(r.get("added")), _name(r)))
    elif order == OLDEST:
        files.sort(key=lambda r: (_num(r.get("added")) or float("inf"), _name(r)))
    elif order == LARGEST:
        files.sort(key=lambda r: (-_num(r.get("_size")), _name(r)))
    elif order == TYPE:
        files.sort(key=lambda r: (_ext(r), _name(r)))
    else:
        files.sort(key=_name)
    return folders + files


def order_rows(rows: list[dict], order: str) -> list[dict]:
    """*rows* (a folder's, the library's) in *order*: folders and shelves
    first by name (Z-A for Z-A), then the files - by name, the last changed
    or the oldest first (a file's time; one that says none last), the
    largest first, by type (its extension); each part between headings
    ordered on its own, the headings where they were. A new list."""
    if order not in ORDERS:
        order = NAME
    out: list[dict] = []
    part: list[dict] = []
    for r in rows:
        if isinstance(r, dict) and r.get("_header"):
            out += _ordered(part, order)
            out.append(r)
            part = []
        elif isinstance(r, dict):
            part.append(r)
    return out + _ordered(part, order)
