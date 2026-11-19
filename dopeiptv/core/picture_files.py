"""Pictures from files on this computer, and whose word a file's path is.

A row's picture is an address: a web address for a provider's logo or
poster, a file's path for what the app made or found itself - a frame from
a video or a recording and an album's art in its cache, a cover beside the
person's own music, a picon from Nerd's folder. The loader cannot tell from
the address who wrote it, and a provider writes its rows' pictures: a logo
called ``/dev/zero`` was read until the memory ran out, and on Windows
``//host/share/logo.png`` logs in at that host with the person's account.

So a file is read as a picture only where the app's own pictures are
(:func:`trust` names those folders: its cache, Nerd's picon folder, Local
files' folders and where the system mounts shares and discs), only when it
is a plain file, and only up to a size no picture has (:func:`read`).
Anything else is no picture: the row shows its placeholder.

No Qt, nothing read before :func:`read`.
"""

from __future__ import annotations

import os
import stat
import threading
from typing import Iterable

#: The most a picture file may be. Covers and frames are a few hundred KB,
#: a scanned sleeve a few tens of MB.
LOCAL_PICTURE_MAX = 64 * 1024 * 1024

_lock = threading.Lock()
_roots: dict[str, tuple[str, ...]] = {}


def _norm(path: str) -> str:
    """*path* absolute and without ``..``, as written (a link inside one of
    the person's folders is theirs to make); lower case where the system
    does not tell cases apart."""
    return os.path.normcase(os.path.abspath(path))


def _folders(folders: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_norm(os.path.expanduser(str(f)))
                               for f in folders if f and str(f).strip()))


def trust(group: str, folders: Iterable[str]) -> None:
    """The folders the app's own pictures of *group* are in ("cache",
    "picons", "local"...), in place of the group's earlier ones."""
    kept = _folders(folders)
    with _lock:
        if kept:
            _roots[group] = kept
        else:
            _roots.pop(group, None)


def trust_also(group: str, folder: str) -> None:
    """One more folder of *group* (an image loader's own cache)."""
    more = _folders([folder])
    with _lock:
        _roots[group] = tuple(dict.fromkeys(_roots.get(group, ()) + more))


def trusted_folders(group: str | None = None) -> list[str]:
    with _lock:
        if group is not None:
            return list(_roots.get(group, ()))
        return [f for kept in _roots.values() for f in kept]


def local_path(address: object) -> str | None:
    """The file a picture's address names - ``/...`` or ``file://...`` (as
    the loader always read them) - or None for a web address."""
    text = str(address or "")
    if text.startswith("file://"):
        return text[len("file://"):]
    return text if text.startswith("/") else None


def _inside(path: str, folders: list[str]) -> bool:
    if not path or "\x00" in path:
        return False
    try:
        full = _norm(path)
    except (OSError, ValueError):
        return False
    return any(full != root and full.startswith(root.rstrip(os.sep) + os.sep)
               for root in folders)


def trusted(path: str) -> bool:
    """Whether *path* is in one of the folders the app's own pictures are
    in. By its name alone - nothing is asked of the disk, so no share is
    touched for a path that is not."""
    return _inside(path, trusted_folders())


def in_group(group: str, path: str) -> bool:
    """Whether *path* is in one of *group*'s folders."""
    return _inside(path, trusted_folders(group))


def read_file(path: str, limit: int = LOCAL_PICTURE_MAX) -> bytes | None:
    """The bytes of *path* when it is a plain file (not a device, a pipe or
    a folder) of at most *limit* bytes; None otherwise."""
    try:
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            return None
        with open(path, "rb") as fh:
            data = fh.read(limit + 1)
    except (OSError, ValueError):
        return None
    return data if len(data) <= limit else None


def read(path: str, limit: int = LOCAL_PICTURE_MAX) -> bytes | None:
    """The bytes of the picture file *path*, or None when it is not one the
    app reads: outside the trusted folders, not a plain file, larger than
    *limit*, or unreadable."""
    return read_file(path, limit) if trusted(path) else None


def mount_parents() -> list[str]:
    """Where the system puts the shares and discs Local files lists
    (ui/mw_local.py ``_mounted_roots``): gvfs and /media/<user> on Linux,
    /Volumes on a Mac."""
    import sys
    if sys.platform == "darwin":
        return ["/Volumes"]
    if sys.platform.startswith("linux"):
        out = []
        getuid = getattr(os, "getuid", None)
        if getuid is not None:
            out.append(f"/run/user/{getuid()}/gvfs")
        user = os.environ.get("USER", "")
        if user:
            out.append(os.path.join("/media", user))
        return out
    return []


def picture_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) a PNG, JPEG, GIF, WebP or BMP says it has, read from
    its first bytes; None for any other format or a header that is not
    there. Read here and not asked of Qt (core/workers.py says why)."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
        if data[:2] == b"BM" and len(data) >= 26:
            if int.from_bytes(data[14:18], "little") == 12:
                return int.from_bytes(data[18:20], "little"), int.from_bytes(data[20:22], "little")
            return (abs(int.from_bytes(data[18:22], "little", signed=True)),
                    abs(int.from_bytes(data[22:26], "little", signed=True)))
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
            kind = data[12:16]
            if kind == b"VP8X":
                return (1 + int.from_bytes(data[24:27], "little"),
                        1 + int.from_bytes(data[27:30], "little"))
            if kind == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
                return (int.from_bytes(data[26:28], "little") & 0x3FFF,
                        int.from_bytes(data[28:30], "little") & 0x3FFF)
            if kind == b"VP8L" and data[20] == 0x2F:
                bits = int.from_bytes(data[21:25], "little")
                return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
            return None
        if data[:2] == b"\xff\xd8":
            return _jpeg_size(data)
    except (IndexError, ValueError):
        return None
    return None


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """A JPEG's size from its frame header, found segment by segment."""
    pos, end = 2, len(data)
    for _ in range(4096):                   # more segments than any JPEG has
        while pos < end and data[pos] != 0xFF:
            pos += 1
        while pos < end and data[pos] == 0xFF:
            pos += 1
        if pos >= end:
            return None
        marker = data[pos]
        pos += 1
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            continue                        # no length
        if marker in (0xD9, 0xDA) or pos + 2 > end:
            return None                     # the picture's data began: no frame
        length = int.from_bytes(data[pos:pos + 2], "big")
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if pos + 7 > end:
                return None
            return (int.from_bytes(data[pos + 5:pos + 7], "big"),
                    int.from_bytes(data[pos + 3:pos + 5], "big"))
        pos += max(2, length)
    return None
