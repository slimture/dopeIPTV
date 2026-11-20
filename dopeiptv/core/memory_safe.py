"""Whether what the app writes to a folder in memory stays in memory - never
on a disk, now or after the computer restarts - for private viewing: a
channel's rewind buffer, a cast's work files, a subtitle fetched for it.

A folder qualifies when it is on a file system in memory (tmpfs, ramfs - by
the mount table, where the folder really is) and what the system may move
out of that memory cannot be read from a disk afterwards:

- ramfs is never swapped, nor is a tmpfs mounted ``noswap`` (Linux 6.4);
- otherwise every swap device must keep nothing readable without its key:
  zram (compressed memory, with no backing device), a dm-crypt mapping -
  directly, or under LVM or another mapping whose every part is one - and
  a swap file on such a device. No swap at all qualifies as well.

Anything else - swap on a plain partition, a swap file on a plain disk, a
system that cannot be asked - does not: the caller keeps nothing at all
then (private viewing's rule: never on a disk, rather nothing).

Only Linux has such a folder for the app (``$XDG_RUNTIME_DIR``, this user's
own tmpfs); macOS and Windows get None, so a private playback keeps no
buffer there and converts nothing for a cast.
"""
from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path

from .log import log

MOUNTS = "/proc/self/mounts"
SWAPS = "/proc/swaps"
SYS = "/sys"
#: The kinds of file system that live in memory alone.
IN_MEMORY = ("tmpfs", "ramfs")
#: How deep a stack of mappings is followed (LVM on LUKS is two).
_DEPTH = 8


def _unescape(field: str) -> str:
    # /proc/mounts writes a space, a tab, a newline and a backslash in octal.
    for code, ch in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        field = field.replace(code, ch)
    return field


def _read(path: str, limit: int = 1 << 20) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return None


def mount_of(path: Path | str, mounts: str | None = None) -> tuple[str, str, tuple[str, ...]]:
    """The mount *path* is on - its point, kind and options - by the mount
    table: the longest mount point it is under (the newest of equals, as
    the kernel shows it). ("", "", ()) when the table cannot be read."""
    if mounts is None:
        mounts = _read(MOUNTS) or ""
    where = os.path.realpath(str(path))
    best, kind = "", ""
    opts: tuple[str, ...] = ()
    for line in mounts.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        point = _unescape(parts[1]).rstrip("/") or "/"
        under = where == point or where.startswith(point.rstrip("/") + "/")
        if under and len(point) >= len(best):
            best, kind = point, parts[2]
            opts = tuple(parts[3].split(",")) if len(parts) > 3 else ()
    return best, kind, opts


def in_memory(path: Path | str, mounts: str | None = None) -> bool:
    """Whether *path* is on a file system in memory (tmpfs, ramfs)."""
    return mount_of(path, mounts)[1] in IN_MEMORY


def swap_list(text: str) -> list[tuple[str, str]]:
    """The swap areas in /proc/swaps: (path, "partition" or "file")."""
    out = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("/"):
            out.append((_unescape(parts[0]), parts[1]))
    return out


def _block_of(path: str, kind: str, sys_root: str) -> str | None:
    """The block device's kernel name a swap area is on: the device itself,
    or for a swap file the device of its file system (None when that is no
    block device - btrfs and the like give an anonymous one)."""
    try:
        if kind == "file":
            dev = os.stat(path).st_dev
        else:
            st = os.stat(path)
            if not stat.S_ISBLK(st.st_mode):
                return None
            dev = st.st_rdev
    except OSError:
        return None
    major, minor = os.major(dev), os.minor(dev)
    if major == 0:
        return None
    real = os.path.realpath(os.path.join(sys_root, "dev", "block", f"{major}:{minor}"))
    name = os.path.basename(real)
    return name or None


def encrypted_block(name: str, sys_root: str = SYS, depth: int = 0) -> bool:
    """Whether block device *name* keeps nothing readable without its key:
    zram without a backing device, a dm-crypt mapping, or a mapping (LVM,
    RAID...) whose every part is one of those."""
    if depth > _DEPTH or not name or "/" in name or name.startswith("."):
        return False
    block = os.path.join(sys_root, "class", "block", name)
    if name.startswith("zram"):
        backing = (_read(os.path.join(block, "backing_dev"), 4096) or "none").strip()
        return backing in ("", "none")
    uuid = (_read(os.path.join(block, "dm", "uuid"), 4096) or "").strip()
    if uuid.startswith("CRYPT-"):
        return True
    try:
        parts = sorted(os.listdir(os.path.join(block, "slaves")))
    except OSError:
        return False
    return bool(parts) and all(encrypted_block(p, sys_root, depth + 1) for p in parts)


def swap_safe(swaps: str | None = None, sys_root: str = SYS) -> tuple[bool, str]:
    """Whether what the system swaps out can be read from a disk afterwards:
    (True, why) when there is no swap or every area is encrypted or in
    memory; (False, why) otherwise - why in a few words for the log."""
    if swaps is None:
        swaps = _read(SWAPS)
        if swaps is None:
            return False, "the system's swap could not be asked"
    areas = swap_list(swaps)
    if not areas:
        return True, "no swap"
    plain = [p for p, kind in areas
             if not encrypted_block(_block_of(p, kind, sys_root) or "", sys_root)]
    if plain:
        return False, "swap not encrypted (" + ", ".join(os.path.basename(p) for p in plain) + ")"
    return True, "swap encrypted"


def stays_in_memory(path: Path | str, mounts: str | None = None, swaps: str | None = None,
                    sys_root: str = SYS) -> tuple[bool, str]:
    """Whether what is written under *path* stays in memory and never
    reaches a disk: (True or False, why)."""
    _point, kind, opts = mount_of(path, mounts)
    if kind not in IN_MEMORY:
        return False, "not in memory"
    if kind == "ramfs" or "noswap" in opts:
        return True, "never swapped"
    return swap_safe(swaps, sys_root)


def own_folder(path: Path) -> bool:
    """*path* - made already - a folder itself (never a link to one), this
    user's own and 0700 (narrowed when it is not)."""
    try:
        st = os.lstat(str(path))
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
            return False
        if st.st_mode & 0o077:
            os.chmod(str(path), 0o700)
            if os.lstat(str(path)).st_mode & 0o077:
                return False
    except OSError:
        return False
    return True


def private_base(name: str, env: dict | None = None, mounts: str | None = None,
                 platform: str | None = None) -> Path | None:
    """``$XDG_RUNTIME_DIR/dopeiptv-<name>`` - where private viewing's files
    may go in memory: only on Linux, only when that folder is this user's
    own (0700, theirs) and on a file system in memory; None anywhere else.
    Whether it also stays out of swap is stays_in_memory's to say."""
    if (platform or sys.platform) != "linux":
        return None
    run = (os.environ if env is None else env).get("XDG_RUNTIME_DIR") or ""
    if not run or not os.path.isabs(run):
        return None
    try:
        st = os.stat(run)
    except OSError:
        return None
    if not stat.S_ISDIR(st.st_mode) or st.st_mode & 0o077 or st.st_uid != os.getuid():
        return None
    if not in_memory(Path(run), mounts):
        return None
    return Path(run) / f"dopeiptv-{name}"


#: Said once per reason and kind: why private viewing keeps nothing there.
_SAID: set = set()


def private_folder(name: str, mounts: str | None = None, swaps: str | None = None,
                   env: dict | None = None, platform: str | None = None) -> Path | None:
    """The folder for private viewing's *name* files (made, checked): in
    memory, this user's own, out of reach of a disk (stays_in_memory) - or
    None, and why said once in the log."""
    base = private_base(name, env, mounts, platform)
    if base is None:
        why = "no folder of this user's own in memory"
    else:
        try:
            base.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as e:
            why = f"its folder could not be made ({e.strerror or e})"
        else:
            if not own_folder(base):
                why = "its folder is not this user's own"
            else:
                ok, why = stays_in_memory(base, mounts, swaps)
                if ok:
                    return base
    key = (name, why)
    if key not in _SAID:
        _SAID.add(key)
        log.info("private viewing: nothing of it kept for %s - %s", name, why)
    return None


def sweep(base: Path, older_than: float = 0.0) -> int:
    """What a run of the app that ended without tidying up left in *base*
    (each entry ``<pid>-...``): gone once that process is. The number."""
    gone = 0
    try:
        entries = list(base.iterdir())
    except OSError:
        return 0
    now = time.time()
    for entry in entries:
        head = entry.name.split("-", 1)[0]
        if not head.isdigit() or int(head) == os.getpid():
            continue
        try:
            os.kill(int(head), 0)
            continue                # still running: its own to tidy
        except ProcessLookupError:
            pass
        except (PermissionError, OSError):
            continue
        try:
            if now - entry.lstat().st_mtime < older_than:
                continue
        except OSError:
            continue
        import shutil
        shutil.rmtree(entry, ignore_errors=True)
        gone += 1
    return gone
