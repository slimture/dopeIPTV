"""Read artist/album/title and embedded cover art out of music files.

Deliberately dependency-free and header-only: a tag lives at the start of
the file (FLAC metadata blocks, an ID3v2 tag, an MP4 header when the file
is written for streaming), so a few kilobytes are read and the rest is
never touched. That matters over SMB, where reading a whole album to
label it would cost minutes.

Handles the three formats a real library is made of - FLAC (Vorbis
comments + PICTURE), MP3/ID3v2.2-2.4 (text frames + APIC) and MP4/M4A
(ilst atoms) - and answers None for anything it does not understand, so
the caller falls back to the file name. mutagen is used when it happens
to be installed, purely as a breadth bonus; nothing depends on it.
"""
from __future__ import annotations

import os

from .log import log

# What the app asks for, in one shape regardless of container.
_FIELDS = ("artist", "album", "title", "track", "date", "albumartist")


# Vorbis spells some of them differently than everyone else.
_ALIAS = {"tracknumber": "track", "album_artist": "albumartist",
          "albumartistsort": "albumartist", "year": "date",
          "originaldate": "date"}


def _clean(v) -> str:
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    return str(v or "").strip().strip("\x00")


# -- FLAC --------------------------------------------------------------------

def _flac(fh) -> tuple[dict, bytes | None]:
    if fh.read(4) != b"fLaC":
        return {}, None
    tags: dict[str, str] = {}
    cover: bytes | None = None
    for _ in range(32):                      # a sane block ceiling
        head = fh.read(4)
        if len(head) < 4:
            break
        last = head[0] & 0x80
        kind = head[0] & 0x7F
        size = int.from_bytes(head[1:4], "big")
        if size < 0 or size > 30 * 1024 * 1024:
            break
        if kind == 4:                        # VORBIS_COMMENT
            data = fh.read(size)
            try:
                pos = 4 + int.from_bytes(data[:4], "little")
                count = int.from_bytes(data[pos:pos + 4], "little")
                pos += 4
                for _i in range(min(count, 200)):
                    n = int.from_bytes(data[pos:pos + 4], "little")
                    pos += 4
                    field = data[pos:pos + n].decode("utf-8", "replace")
                    pos += n
                    key, _, val = field.partition("=")
                    tags.setdefault(key.strip().lower(), val.strip())
            except (IndexError, ValueError):
                pass
        elif kind == 6 and cover is None:    # PICTURE
            data = fh.read(size)
            try:
                p = 4
                mlen = int.from_bytes(data[p:p + 4], "big")
                p += 4 + mlen                # MIME type
                dlen = int.from_bytes(data[p:p + 4], "big")
                p += 4 + dlen                # description
                p += 16                      # w/h/depth/colours
                plen = int.from_bytes(data[p:p + 4], "big")
                p += 4
                cover = data[p:p + plen] or None
            except (IndexError, ValueError):
                cover = None
        else:
            fh.seek(size, os.SEEK_CUR)
        if last:
            break
    return tags, cover


# -- ID3v2 (MP3) -------------------------------------------------------------

_ID3 = {"TPE1": "artist", "TP1": "artist", "TALB": "album", "TAL": "album",
        "TIT2": "title", "TT2": "title", "TRCK": "track", "TRK": "track",
        "TDRC": "date", "TYER": "date", "TYE": "date",
        "TPE2": "albumartist", "TP2": "albumartist"}


def _id3_text(raw: bytes) -> str:
    if not raw:
        return ""
    enc, body = raw[0], raw[1:]
    codec = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(
        enc, "latin-1")
    try:
        return body.decode(codec, "replace").strip("\x00").strip()
    except (LookupError, UnicodeDecodeError):
        return ""


def _id3(fh) -> tuple[dict, bytes | None]:
    head = fh.read(10)
    if len(head) < 10 or head[:3] != b"ID3":
        return {}, None
    major = head[3]
    size = 0
    for b in head[6:10]:                     # syncsafe
        size = (size << 7) | (b & 0x7F)
    blob = fh.read(min(size, 4 * 1024 * 1024))
    tags: dict[str, str] = {}
    cover: bytes | None = None
    p = 0
    fid_len, head_len = (3, 6) if major == 2 else (4, 10)
    while p + head_len <= len(blob):
        fid = blob[p:p + fid_len]
        if not fid.strip(b"\x00"):
            break
        if major == 2:
            fsize = int.from_bytes(blob[p + 3:p + 6], "big")
        elif major == 4:
            fsize = 0
            for b in blob[p + 4:p + 8]:
                fsize = (fsize << 7) | (b & 0x7F)
        else:
            fsize = int.from_bytes(blob[p + 4:p + 8], "big")
        p += head_len
        body = blob[p:p + fsize]
        p += fsize
        if fsize <= 0:
            continue
        name = fid.decode("latin-1", "replace")
        if name in _ID3:
            tags.setdefault(_ID3[name], _id3_text(body))
        elif name in ("APIC", "PIC") and cover is None:
            try:
                q = 1
                if name == "APIC":
                    q = body.index(b"\x00", 1) + 1
                else:
                    q = 4                     # 3-char image format
                q += 1                        # picture type
                zero = body.index(b"\x00", q)
                cover = body[zero + 1:] or None
            except ValueError:
                cover = None
    return tags, cover


# -- MP4 / M4A ---------------------------------------------------------------

_MP4 = {b"\xa9ART": "artist", b"\xa9alb": "album", b"\xa9nam": "title",
        b"trkn": "track", b"\xa9day": "date", b"aART": "albumartist"}


def _mp4_ilst(blob: bytes) -> tuple[dict, bytes | None]:
    tags: dict[str, str] = {}
    cover: bytes | None = None
    p = 0
    while p + 8 <= len(blob):
        size = int.from_bytes(blob[p:p + 4], "big")
        name = blob[p + 4:p + 8]
        if size < 8 or p + size > len(blob):
            break
        body = blob[p + 8:p + size]
        if name in _MP4 or name == b"covr":
            # value sits in a 'data' box: 4 size + 4 'data' + 4 type + 4 loc
            if len(body) >= 16 and body[4:8] == b"data":
                val = body[16:int.from_bytes(body[:4], "big")]
                if name == b"covr":
                    cover = cover or (val or None)
                elif name == b"trkn":
                    if len(val) >= 4:
                        tags.setdefault("track", str(
                            int.from_bytes(val[2:4], "big")))
                else:
                    tags.setdefault(_MP4[name], _clean(val))
        p += size
    return tags, cover


def _mp4(fh) -> tuple[dict, bytes | None]:
    fh.seek(0)
    if fh.read(8)[4:8] != b"ftyp":
        return {}, None
    fh.seek(0)
    blob = fh.read(3 * 1024 * 1024)          # header-only: moov at the front
    at = blob.find(b"ilst")
    if at < 0:
        return {}, None
    return _mp4_ilst(blob[at + 4:])


# -- the front door ----------------------------------------------------------

def read_tags(path: str, want_cover: bool = False):
    """(tags, cover_bytes) for *path*. Both may be empty/None; a file the
    reader does not understand simply answers nothing rather than raising."""
    try:
        with open(path, "rb") as fh:
            ext = os.path.splitext(path)[1].lower()
            if ext == ".flac":
                tags, cover = _flac(fh)
            elif ext in (".m4a", ".mp4", ".m4b", ".aac"):
                tags, cover = _mp4(fh)
            else:
                tags, cover = _id3(fh)
                if not tags and ext not in (".mp3",):
                    fh.seek(0)
                    tags, cover = _flac(fh)
    except OSError as e:
        log.debug("tag read failed for %s: %s", path, e)
        return {}, None
    out: dict[str, str] = {}
    for k, v in tags.items():
        key = _ALIAS.get(k, k)
        if key in _FIELDS and _clean(v):
            out.setdefault(key, _clean(v))
    return out, (cover if want_cover else None)


def display_name(path: str, tags: dict | None = None) -> str:
    """What to call the track in a list: "3. Jail" from the tags, or the
    file name when there are none."""
    t = tags if tags is not None else read_tags(path)[0]
    title = t.get("title")
    if not title:
        return os.path.splitext(os.path.basename(path))[0]
    num = (t.get("track") or "").split("/")[0].strip()
    try:
        return f"{int(num)}. {title}" if num else title
    except ValueError:
        return title


def cover_bytes(path: str) -> bytes | None:
    return read_tags(path, want_cover=True)[1]
