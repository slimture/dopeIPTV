"""Local files' search: a folder and every subfolder under it, pure.

``TreeSearch`` walks the folder searched breadth first - the nearest
matches first - in slices (``step``), so a pool job never holds a thread
for long on a slow network share and the matches can show as they are
found; ``cancel`` from any thread stops it at the next entry. A file
matches when every word typed is in its name (case, accents and
punctuation left out, as the command palette matches); a folder matches
the same way and comes back as a folder to open.

Bounded: ``MAX_DEPTH`` folder levels under the one searched, ``MAX_ENTRIES``
files and folders looked at, ``MAX_HITS`` matches - and it says which
bound it met (``capped``: matches; ``partial``: folders left out). Hidden
folders and files (a leading dot; on Windows the hidden or system
attribute) and a NAS's housekeeping folders (*skip*) are left out. A linked
folder (a symlink, a Windows junction) is walked only when it leads
outside what this walk covers, never back into it - which is how a link
loops.

Nothing here touches the disk before ``step``: the object is made on the
window's thread and walked on the pool's.
"""

from __future__ import annotations

import os
import stat as _stat
import threading
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

from .log import log
from .palette_match import fold, words

MAX_DEPTH = 8
MAX_ENTRIES = 30_000
MAX_HITS = 500

_HIDDEN = getattr(_stat, "FILE_ATTRIBUTE_HIDDEN", 2) | getattr(
    _stat, "FILE_ATTRIBUTE_SYSTEM", 4)
_REPARSE = getattr(_stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@dataclass
class Hit:
    """One match: its path, the folder it is in relative to the one
    searched ("" for that folder itself), and for a file its size and
    modification time."""

    path: str
    folder: str
    is_dir: bool
    size: int = 0
    mtime: int = 0


class TreeSearch:
    """One query through *base* and its subfolders."""

    def __init__(self, base: str, query: str, exts: Iterable[str],
                 skip: Iterable[str] = (), max_depth: int = MAX_DEPTH,
                 max_entries: int = MAX_ENTRIES,
                 max_hits: int = MAX_HITS) -> None:
        self.base = base
        self.terms = words(query)
        self._raw = (query or "").strip().casefold()
        self.exts = tuple(e.lower() for e in exts)
        self.skip = {s.lower() for s in skip}
        self.max_depth = max_depth
        self.max_entries = max_entries
        self.max_hits = max_hits
        self.hits: list[Hit] = []
        # The media files of each folder that holds a matching file, in
        # the order its own listing shows them: what a match plays on into.
        self.siblings: dict[str, list[str]] = {}
        self.entries = 0
        self.folders = 0
        self.done = False
        self.capped = False
        self.partial = False
        self._queue: deque[tuple[str, int]] = deque([(base, 0)])
        self._roots: list[str] | None = None
        self._cancel = threading.Event()

    # -- control -----------------------------------------------------------

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def step(self, budget: float = 0.5) -> bool:
        """Walk on for about *budget* seconds (at least one folder). True
        once the walk is over: everything searched, a bound met, or
        cancelled."""
        if self._roots is None:
            self._roots = [_real(self.base)]
        end = time.monotonic() + budget
        while not self.done:
            if self._cancel.is_set() or not self._queue:
                self.done = True
                break
            if self.entries >= self.max_entries:
                self.partial = True
                self.done = True
                break
            path, depth = self._queue.popleft()
            self._walk(path, depth)
            if time.monotonic() >= end:
                break
        return self.done

    # -- the walk ------------------------------------------------------------

    def matches(self, name: str) -> bool:
        if self.terms:
            folded = fold(name)
            return all(t in folded for t in self.terms)
        return bool(self._raw) and self._raw in name.casefold()

    def _walk(self, path: str, depth: int) -> None:
        try:
            with os.scandir(path) as it:
                entries = sorted(it, key=lambda e: e.name.lower())
        except OSError as err:
            log.debug("local search: a folder could not be read: %s", err)
            return
        self.folders += 1
        rel = os.path.relpath(path, self.base)
        rel = "" if rel == os.curdir else rel
        files: list[str] = []
        dir_hits: list[Hit] = []
        file_hits: list[Hit] = []
        for e in entries:
            if self._cancel.is_set():
                break
            if self.entries >= self.max_entries:
                self.partial = True
                break
            self.entries += 1
            name = e.name
            if name.startswith(".") or _hidden(e):
                continue
            try:
                is_dir = e.is_dir()
            except OSError:
                continue
            if is_dir:
                if name.lower() in self.skip:
                    continue
                if self.matches(name):
                    dir_hits.append(Hit(e.path, rel, True))
                if depth >= self.max_depth:
                    self.partial = True
                elif self._may_enter(e):
                    self._queue.append((e.path, depth + 1))
            elif name.lower().endswith(self.exts):
                files.append(name)
                if self.matches(os.path.splitext(name)[0]):
                    try:
                        st = e.stat()
                    except OSError:
                        continue
                    file_hits.append(Hit(e.path, rel, False, st.st_size,
                                         int(st.st_mtime)))
        if file_hits:
            self.siblings[path] = files
        for h in dir_hits + file_hits:
            if len(self.hits) >= self.max_hits:
                self.capped = True
                self.done = True
                return
            self.hits.append(h)

    def _may_enter(self, e: os.DirEntry) -> bool:
        """A plain folder always; a linked one only when it leads outside
        the folders this walk covers - not into them, not above them."""
        if not _linked(e):
            return True
        real = _real(e.path)
        if not real:
            return False
        if real == os.path.normcase(os.path.abspath(e.path)):
            return True          # marked as a link but leads nowhere else
        roots = self._roots or []
        for root in roots:
            if _within(real, root) or _within(root, real):
                return False
        roots.append(real)
        return True


def _real(path: str) -> str:
    try:
        return os.path.normcase(os.path.realpath(path))
    except (OSError, ValueError):
        return ""


def _within(path: str, root: str) -> bool:
    """*path* is *root* or under it (both normalised)."""
    if path == root:
        return True
    return path.startswith(root.rstrip(os.sep) + os.sep)


def _hidden(e: os.DirEntry) -> bool:
    if os.name != "nt":
        return False
    try:
        attrs = getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & _HIDDEN)


def _linked(e: os.DirEntry) -> bool:
    try:
        if e.is_symlink():
            return True
        if os.name == "nt":
            attrs = getattr(e.stat(follow_symlinks=False),
                            "st_file_attributes", 0)
            return bool(attrs & _REPARSE)
    except OSError:
        return True
    return False
