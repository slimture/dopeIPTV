"""What Local files watches on disk and when it reads again, pure (#34).

Local files notices a folder's changes by itself: a file renamed, added or
removed shows without Rescan. The window watches the folder shown in the
Folders view, or the library's folders (ui/mw_local_watch.py, the watcher
on a thread of its own: ui/local_watcher.py); this module decides, with
no Qt and the clock passed in:

* what to watch (``plan``): the folders on this computer's own disks, at
  most ``CAP`` of them (``cap_for``: a library on Windows its top alone) -
  a network share (gvfs, SMB, NFS, sshfs...) sends
  no events for what another computer changes there, so it, or a library
  past the cap, is read again lightly instead (``poll_due``: no sooner
  than ``POLL_MIN`` after the last read, nor than ``POLL_FACTOR`` times
  what that read took) while Local files is on screen;
* which file systems are on the network (``parse_mounts``,
  ``on_network``: Linux's mount table, a gvfs mount, a Mac's /Volumes, a
  Windows network path or drive);
* a library's folders from what it holds (``library_dirs``), when no
  walk of it said them;
* when to read again (``Refresh``): ``DEBOUNCE`` after the last change,
  never later than ``MAX_WAIT`` after the first of a burst, never while a
  walk is under way - one is queued for after it;
* what changed between two listings of a folder (``diff``): the rows to
  take out and the ones to put in, so the list is brought up to date in
  place, its scroll and its selection kept - or that it must be laid out
  anew, or that nothing changed.
"""

from __future__ import annotations

import ntpath
import os
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

#: At most this many folders watched (the system's watches are a budget of
#: the user's); a library with more is read again lightly instead.
CAP = 2000
#: On Windows a watch holds a handle on its folder: a library there has
#: its top watched and the rest read again lightly (the folder shown in the
#: Folders view is watched as Qt's own file dialogs watch theirs).
WINDOWS_LIBRARY_CAP = 1
#: Read again this long after the last change...
DEBOUNCE = 2.0
#: ...and never later than this after the first of a burst (a big copy).
MAX_WAIT = 10.0
#: A light re-walk where no change events come: no sooner than this after
#: the last read...
POLL_MIN = 180.0
#: ...nor sooner than this many times what that read took.
POLL_FACTOR = 10.0

#: File systems over the network: what another computer changes there
#: sends no event here.
NETWORK_FS = frozenset({
    "cifs", "smb", "smb2", "smb3", "smbfs", "nfs", "nfs4", "afs", "afpfs",
    "ncpfs", "9p", "ceph", "glusterfs", "lustre", "gpfs", "davfs", "webdav",
    "coda", "sshfs", "fuse.sshfs", "fuse.gvfsd-fuse", "fuse.gvfs-fuse-daemon",
    "fuse.rclone", "fuse.smbnetfs", "fuse.s3fs", "fuse.davfs", "fuse.glusterfs",
    "fuse.cephfs", "fuse.curlftpfs", "fuse.goofys", "fuse.juicefs",
})


# -- the network ---------------------------------------------------------------------

def _unescape(field: str) -> str:
    r"""A mount table's field as the path it is: "\040" a space and the
    other octal escapes the kernel writes."""
    if "\\" not in field:
        return field
    out, i = [], 0
    while i < len(field):
        c = field[i]
        code = field[i + 1:i + 4]
        if c == "\\" and len(code) == 3 and all(ch in "01234567" for ch in code):
            out.append(chr(int(code, 8)))
            i += 4
        else:
            out.append(c)
            i += 1
    return "".join(out)


def parse_mounts(text: str) -> list[tuple[str, str]]:
    """(mount point, file system type) of each line of a mount table
    (Linux's /proc/self/mounts); a line it cannot read left out."""
    out: list[tuple[str, str]] = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1].startswith("/"):
            out.append((_unescape(parts[1]), parts[2].lower()))
    return out


def mount_of(path: str, mounts: Sequence[tuple[str, str]]) -> tuple[str, str] | None:
    """The mount *path* is on: the longest mount point it is under."""
    p = os.path.normpath(path)
    best: tuple[str, str] | None = None
    for point, fs in mounts:
        root = os.path.normpath(point)
        inside = p == root or p.startswith(root.rstrip(os.sep) + os.sep) or root == os.sep
        if inside and (best is None or len(root) >= len(os.path.normpath(best[0]))):
            best = (point, fs)
    return best


def is_network_fs(fs: str) -> bool:
    fs = (fs or "").lower()
    return fs in NETWORK_FS or fs.startswith(("nfs", "smb", "cifs"))


def on_network(path: str, mounts: Sequence[tuple[str, str]] = (),
               platform: str | None = None, remote_drives: Iterable[str] = ()) -> bool:
    """Whether *path* is on a share over the network, where change events
    do not come for what another computer does: a gvfs mount (GNOME's
    SMB/NFS/SFTP), a network file system in *mounts*, on a Mac anything
    under /Volumes (shares mount there - a disk of its own there is read
    again too, lightly), on Windows a network path or one of
    *remote_drives* ("Z:")."""
    platform = platform or sys.platform
    p = str(path or "")
    if not p:
        return False
    if platform.startswith("win"):
        if p.startswith(("\\\\", "//")):
            return True
        drive = ntpath.splitdrive(p)[0].rstrip("\\/").upper()
        return bool(drive) and drive in {d.rstrip("\\/").upper() for d in remote_drives}
    norm = p.replace("\\", "/") + "/"
    if (norm.startswith("/run/user/") and "/gvfs/" in norm) or "/.gvfs/" in norm:
        return True
    norm = norm[:-1]
    if platform == "darwin":
        return norm.startswith("/Volumes/")
    hit = mount_of(p, mounts)
    return hit is not None and is_network_fs(hit[1])


# -- what to watch -------------------------------------------------------------------

@dataclass(frozen=True)
class Plan:
    """The folders to watch, and whether to read again lightly besides
    (*poll*): some folder is on the network, or there were more than the
    cap. *why*: "network", "cap" or ""."""
    watch: tuple[str, ...] = ()
    poll: bool = False
    why: str = ""


def plan(dirs: Iterable[str], network: Callable[[str], bool] | None = None,
         cap: int = CAP) -> Plan:
    """What to watch of *dirs* (the folder shown, or a library's folders -
    the most important first): each once, those *network* says are on a
    share left to the light re-walk, at most *cap* of the rest."""
    seen: set[str] = set()
    watch: list[str] = []
    net = over = False
    for d in dirs:
        if not d:
            continue
        p = os.path.normpath(d)
        if p in seen:
            continue
        seen.add(p)
        if network is not None and network(p):
            net = True
            continue
        if len(watch) >= cap:
            over = True
            continue
        watch.append(p)
    return Plan(tuple(watch), net or over, "network" if net else ("cap" if over else ""))


def cap_for(kind: str, platform: str | None = None) -> int:
    """How many folders a watch of *kind* ("folder" or "library") may hold
    on this system."""
    platform = platform or sys.platform
    if kind == "library" and platform.startswith("win"):
        return WINDOWS_LIBRARY_CAP
    return CAP


def library_dirs(root: str, paths: Iterable[str], limit: int = CAP + 1) -> list[str]:
    """A library's folders from the files (or folders) it holds, when no
    walk said them: *root*, then each folder they are in and every folder
    between it and *root*, the shallowest first, then by name - at most
    *limit* (one past the cap says the cap was passed)."""
    base = os.path.normpath(root)
    head = base.rstrip(os.sep) + os.sep
    found: set[str] = {base}
    for path in paths:
        if not path:
            continue
        d = os.path.normpath(os.path.dirname(os.path.normpath(str(path))))
        while d.startswith(head) and d not in found:
            found.add(d)
            d = os.path.dirname(d)
    rest = sorted((d for d in found if d != base),
                  key=lambda d: (d.count(os.sep), d.lower()))
    return ([base] + rest)[:max(1, limit)]


# -- when to read again ----------------------------------------------------------------

class Refresh:
    """When to read again after changes on disk: *debounce* seconds after
    the last change, but no later than *max_wait* after the first of a
    burst; never while a walk is under way - the read waits (queued) and
    is due once it ends. One read for any number of changes."""

    def __init__(self, debounce: float = DEBOUNCE, max_wait: float = MAX_WAIT) -> None:
        self.debounce = debounce
        self.max_wait = max_wait
        self.pending = False
        self._first = 0.0
        self._last = 0.0

    def changed(self, now: float) -> None:
        if not self.pending:
            self.pending = True
            self._first = now
        self._last = now

    def due_in(self, now: float) -> float | None:
        """Seconds until a read is due (0: now), None with nothing pending."""
        if not self.pending:
            return None
        at = min(self._last + self.debounce, self._first + self.max_wait)
        return max(0.0, at - now)

    def take(self, now: float, busy: bool = False) -> bool:
        """True when a read is to start now - and then it is no longer
        pending. While *busy* (a walk under way) it stays pending."""
        due = self.due_in(now)
        if due is None or due > 0 or busy:
            return False
        self.pending = False
        return True

    def forget(self) -> None:
        self.pending = False


def poll_due(last: float, cost: float, now: float, least: float = POLL_MIN,
             factor: float = POLL_FACTOR) -> bool:
    """A light re-walk due: *least* seconds since the last read ended
    (*last*), and *factor* times what it took (*cost*) - a walk of a
    minute over a share waits ten."""
    return now - last >= max(least, factor * max(0.0, cost))


# -- what changed ------------------------------------------------------------------------

@dataclass(frozen=True)
class Update:
    """How a list shown becomes the new listing: the rows to take out (their
    numbers in the list shown) and then the keys to put in (each at its
    number in the new list, in order); or, *reset*, laid out anew (the rows
    kept are not in the same order)."""
    removed: tuple[int, ...] = ()
    inserted: tuple[tuple[int, str], ...] = ()
    reset: bool = False


def diff(old: Sequence[str], new: Sequence[str]) -> Update | None:
    """What changed between the keys shown (*old*) and a new listing's
    (*new*); None when nothing did. Taking out ``removed`` (from the last)
    and then putting each of ``inserted`` in at its number, in order,
    gives *new*."""
    if list(old) == list(new):
        return None
    new_set, old_set = set(new), set(old)
    kept_old = [k for k in old if k in new_set]
    kept_new = [k for k in new if k in old_set]
    if kept_old != kept_new or len(new_set) != len(new) or len(old_set) != len(old):
        return Update(reset=True)
    removed = tuple(i for i, k in enumerate(old) if k not in new_set)
    inserted = tuple((i, k) for i, k in enumerate(new) if k not in old_set)
    return Update(removed, inserted)


def runs(rows: Iterable[int]) -> list[tuple[int, int]]:
    """*rows* as runs of consecutive numbers, (first, last) each, in order."""
    out: list[tuple[int, int]] = []
    for r in sorted(set(rows)):
        if out and r == out[-1][1] + 1:
            out[-1] = (out[-1][0], r)
        else:
            out.append((r, r))
    return out


def apply(items: list, update: Update, row_of: Callable[[str], object]) -> list:
    """*items* (the list shown) changed by *update* in place - its rows
    taken out, ``row_of(key)``'s put in - and returned; a reset is not
    applied here."""
    for a, b in reversed(runs(update.removed)):
        del items[a:b + 1]
    for at, key in update.inserted:
        items.insert(at, row_of(key))
    return items
