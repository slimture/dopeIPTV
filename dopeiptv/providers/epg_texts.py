"""A guide's programmes with their descriptions on disk, a channel at a
time: what the TV place's Every source shows of a row picked of another
source (services/search_sources.py ``Sources.texts``).

Reading another source's guide back whole (XmltvGuide.load_cached: its
index, every text in it) for one row's descriptions took about a second on a
large guide - hundreds of MB, tens of thousands of channels - and as long
again for each row picked after it. ``write`` goes through the guide once,
as it is in memory, and keeps beside its copy on disk every channel's
programmes on their own, each compressed; ``read`` takes back only the table
(where each channel's are, the names the guide knows the channels by), and
``GuideTexts.schedule`` one row's alone: a seek and a few KB unpacked.

What is kept is what the detail column shows (every_source.listings_of)
from a moment between *since* and *until*, in the guide's own time: of each
channel the programmes that end after *since* and begin before *until*, and
beyond those as many as it takes to have *after* more that end after
*until* - their titles and descriptions cut to *text_max*. A moment outside
that is not covered (``covers``): the file is made again.

The file, ``<guide>.texts`` (epg.TEXTS_SUFFIX), every number little-endian:

- a header (_HEAD): MAGIC, VERSION, the guide copy it was made from (its size
  and modification time in nanoseconds - one made from another copy is
  none), *since*, *until*, how many channels and names, where the table
  starts and how long it is;
- each channel's block, zlib: its number in the table, how many programmes,
  their starts and their stops (signed 64-bit), then their titles and their
  descriptions, UTF-8, each ended by a NUL; a channel with none kept has no
  block;
- the table, zlib: each channel's block's offset (64-bit) and length (32-bit),
  each name's channel (32-bit), then the channels' ids and the names, each
  ended by a NUL.

It is read as anything from outside is (AGENTS.md rule 5): the header's
counts and places checked against the limits and the file's size before
anything else is read, the table and a block unpacked no further than they
may be (TABLE_MAX, BLOCK_MAX), a block that is not its channel's own or that
does not add up refused, a file changed since its table was read ``Stale``.
No XML is read here: the guide's own parse (epg.parse_xmltv, with
core/safe_xml.py's check) took the guide in.
"""

from __future__ import annotations

import os
import struct
import sys
import zlib
from array import array
from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .epg import TEXTS_SUFFIX, Schedule, channel_key

MAGIC = b"dpIPTVtx"
VERSION = 1
#: magic, version, nothing yet, the guide copy's size and modification time
#: (ns), since, until, channels, names, the table's offset and length.
_HEAD = struct.Struct("<8sHHQqqqIIQQ")
#: A block's own: its channel's number in the table, how many programmes.
_BLOCK_HEAD = struct.Struct("<II")
#: An unsigned 32-bit array, whichever of array's codes is that here.
_U32 = next(code for code in "IL" if array(code).itemsize == 4)
#: At most this many channels, and names, in one file - more than any guide
#: has; its table in memory stays some tens of MB at most.
CHANNELS_MAX = 200_000
NAMES_MAX = 400_000
#: A block and the table unpacked, at most. A channel's block holds no more
#: programmes (the first ones kept) than fit in it however long each text is.
BLOCK_MAX = 16 << 20
TABLE_MAX = 64 << 20
#: A block as stored, at most: zlib's worst on what does not compress.
_PACKED_MAX = BLOCK_MAX + (BLOCK_MAX >> 8) + 1024
#: No programme runs longer than a day (the guide's lookups and listings_of
#: take that as given): what began before *since* less a day is never shown.
_LONGEST = 86_400
#: Quick rather than small: a large guide's descriptions are a few hundred
#: MB, and the level after this one takes twice as long for a tenth less.
_LEVEL = 1


class Stale(ValueError):
    """The file is no longer the one its table was read from (it was made
    again since): read it again."""


def path_for(guide_path: str) -> str:
    """Where the texts of the guide copy at *guide_path* are kept."""
    return guide_path + TEXTS_SUFFIX


def _identity(st: os.stat_result) -> tuple[int, int, int, int]:
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns


def _little(a: array) -> bytes:
    if sys.byteorder == "big":
        a = array(a.typecode, a)
        a.byteswap()
    return a.tobytes()


def _numbers(code: str, data: bytes) -> array:
    out = array(code)
    out.frombytes(data)
    if sys.byteorder == "big":
        out.byteswap()
    return out


def _word(text: Any, text_max: int) -> str:
    return str(text or "")[:text_max].replace("\x00", "�")


def _words(texts: Iterable[str]) -> bytes:
    return "".join(t + "\x00" for t in texts).encode("utf-8", "surrogatepass")


def _unpack(data: bytes, most: int) -> bytes:
    """*data* unzipped - ValueError when it is no whole zlib stream or would
    unpack to more than *most*."""
    d = zlib.decompressobj()
    try:
        out = d.decompress(data, most)
    except zlib.error as e:
        raise ValueError(f"no block ({e})") from None
    if not d.eof or d.unconsumed_tail or d.unused_data:
        raise ValueError("a block cut short, too long, or with more after it")
    return out


def _texts(data: bytes, count: int) -> list[str]:
    """*count* NUL-ended texts of *data* - ValueError unless exactly that."""
    if not count:
        if data:
            raise ValueError("texts where there are none")
        return []
    if data[-1:] != b"\x00":
        raise ValueError("a text not ended")
    try:
        texts = data[:-1].decode("utf-8", "surrogatepass").split("\x00")
    except UnicodeDecodeError:
        raise ValueError("a text that is no UTF-8") from None
    if len(texts) != count:
        raise ValueError("not the texts said")
    return texts


def _kept(starts: Sequence[int], stops: Sequence[int], since: int, until: int,
          after: int) -> list[int]:
    """Which of a channel's programmes (starts in order) a moment between
    *since* and *until* can show: those that end after *since* and begin
    before *until*, then those after them up to the *after*-th that ends
    after *until* - each of those is shown before anything later can be."""
    n = len(starts)
    base = bisect_left(starts, until)
    keep = [j for j in range(bisect_left(starts, since - _LONGEST), base) if stops[j] > since]
    sure, j = 0, base
    while j < n and sure < after:
        if stops[j] > since:
            keep.append(j)
            sure += stops[j] > until
        j += 1
    return keep


def _block(number: int, sched: Schedule, since: int, until: int, after: int,
           text_max: int) -> bytes:
    """One channel's block, b"" when nothing of it is kept."""
    starts, stops, titles = sched[0], sched[1], sched[2]
    descs = sched[3] if len(sched) > 3 else ()
    keep = _kept(starts, stops, since, until, after)
    # However long each text is, the block stays within BLOCK_MAX.
    keep = keep[:(BLOCK_MAX - _BLOCK_HEAD.size) // (16 + 2 * (4 * text_max + 1))]
    if not keep:
        return b""
    body = b"".join((
        _BLOCK_HEAD.pack(number, len(keep)),
        _little(array("q", (int(starts[j]) for j in keep))),
        _little(array("q", (int(stops[j]) for j in keep))),
        _words(_word(titles[j], text_max) for j in keep),
        _words(_word(descs[j] if j < len(descs) else "", text_max) for j in keep)))
    return zlib.compress(body, _LEVEL)


def _schedule_of(data: bytes, number: int) -> Schedule:
    """A block's programmes - ValueError unless it is channel *number*'s and
    adds up."""
    body = _unpack(data, BLOCK_MAX)
    if len(body) < _BLOCK_HEAD.size:
        raise ValueError("no block")
    who, n = _BLOCK_HEAD.unpack_from(body)
    times = _BLOCK_HEAD.size + 16 * n
    if who != number or not n or times + 2 * n > len(body):
        raise ValueError("not this channel's block")
    texts = _texts(body[times:], 2 * n)
    return (_numbers("q", body[_BLOCK_HEAD.size:_BLOCK_HEAD.size + 8 * n]),
            _numbers("q", body[_BLOCK_HEAD.size + 8 * n:times]),
            tuple(texts[:n]), tuple(texts[n:]))


class GuideTexts:
    """A guide's texts as written or read: the table in memory, each
    channel's block read when it is asked for (``schedule``)."""

    def __init__(self, path: str, identity: tuple[int, int, int, int], since: int, until: int,
                 ids: list[str], names: dict[str, str], offsets: array, lengths: array) -> None:
        self.path = path
        self.since, self.until = since, until
        self._identity = identity
        self._ids = {cid: i for i, cid in enumerate(ids)}
        self._names = names
        self._offsets, self._lengths = offsets, lengths

    def __len__(self) -> int:
        return len(self._ids)

    def covers(self, at: float) -> bool:
        """Whether what a moment *at* shows (the guide's own time) is here."""
        return self.since <= at < self.until

    def schedule(self, item: Mapping[str, Any]) -> Schedule | None:
        """(worker) Row *item*'s channel's programmes as kept here - its
        block alone read -, None when the guide has none of it. Stale when
        the file was made again since; ValueError, zlib.error or OSError
        when its block cannot be read."""
        cid = channel_key(item, self._ids, self._names)
        if cid is None:
            return None
        number = self._ids[cid]
        size = self._lengths[number]
        if not size:
            return None
        with open(self.path, "rb") as f:
            if _identity(os.fstat(f.fileno())) != self._identity:
                raise Stale("the texts were made again")
            f.seek(self._offsets[number])
            data = f.read(size)
        if len(data) != size:
            raise ValueError("a block cut short")
        return _schedule_of(data, number)


def _table(ids: list[str], names: list[tuple[str, int]], offsets: array, lengths: array) -> bytes:
    return b"".join((_little(offsets), _little(lengths),
                     _little(array(_U32, (i for _n, i in names))),
                     _words(ids), _words(n for n, _i in names)))


class _TooLarge(Exception):
    """A table past TABLE_MAX: no file."""


def write(guide: Any, path: str, stamp: tuple[int, int], since: int, until: int,
          after: int, text_max: int) -> GuideTexts | None:
    """(worker) The texts of *guide* - in memory, from the copy on disk whose
    size and modification time (ns) are *stamp* - written to *path* whole or
    not at all, for the moments from *since* to *until*. None when the guide
    has no tables to write (XmltvGuide.tables) or more channels or names
    than CHANNELS_MAX / NAMES_MAX, or its table would be larger than
    TABLE_MAX; OSError when the file cannot be written (none is left)."""
    tables = getattr(guide, "tables", None)
    if not callable(tables):
        return None
    by_id, by_name = tables()
    ids = list(by_id)
    number = {cid: i for i, cid in enumerate(ids)}
    names = [(n, number[c]) for n, c in by_name.items() if c in number]
    if len(ids) > CHANNELS_MAX or len(names) > NAMES_MAX:
        return None
    part = path + ".part"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0) \
        | getattr(os, "O_BINARY", 0)
    offsets, lengths = array("Q"), array(_U32)
    try:
        with os.fdopen(os.open(part, flags, 0o600), "wb") as f:
            f.write(bytes(_HEAD.size))
            at = _HEAD.size
            for i, cid in enumerate(ids):
                block = _block(i, by_id[cid], since, until, after, text_max)
                offsets.append(at if block else 0)
                lengths.append(len(block))
                if block:
                    f.write(block)
                    at += len(block)
            table = _table(ids, names, offsets, lengths)
            if len(table) > TABLE_MAX:
                raise _TooLarge
            packed = zlib.compress(table, 6)
            f.write(packed)
            f.seek(0)
            f.write(_HEAD.pack(MAGIC, VERSION, 0, stamp[0], stamp[1], since, until, len(ids),
                               len(names), at, len(packed)))
        os.replace(part, path)
        st = os.stat(path)
    except BaseException as e:
        try:
            os.unlink(part)
        except OSError:
            pass
        if isinstance(e, _TooLarge):
            return None
        raise
    return GuideTexts(path, _identity(st), since, until, ids,
                      {n: ids[i] for n, i in names}, offsets, lengths)


def read(path: str, stamp: tuple[int, int]) -> GuideTexts | None:
    """(worker) The texts at *path* made from the guide copy *stamp* (its
    size and modification time, ns) - None without them, made from another
    copy, by another version, or not adding up: whatever the file holds,
    never an exception and never memory it asks for past the limits."""
    try:
        with open(path, "rb") as f:
            st = os.fstat(f.fileno())
            head = f.read(_HEAD.size)
            if len(head) != _HEAD.size:
                return None
            (magic, version, _spare, size, mtime, since, until, channels, count, at,
             length) = _HEAD.unpack(head)
            if ((magic, version) != (MAGIC, VERSION) or (size, mtime) != tuple(stamp)
                    or channels > CHANNELS_MAX or count > NAMES_MAX or at < _HEAD.size
                    or at + length != st.st_size or length > _PACKED_MAX):
                return None
            f.seek(at)
            packed = f.read(length)
        if len(packed) != length:
            return None
        table = _unpack(packed, TABLE_MAX)
        fixed = 12 * channels + 4 * count
        if len(table) < fixed:
            return None
        offsets = _numbers("Q", table[:8 * channels])
        lengths = _numbers(_U32, table[8 * channels:12 * channels])
        targets = _numbers(_U32, table[12 * channels:fixed])
        texts = _texts(table[fixed:], channels + count)
        ids = texts[:channels]
        if len(set(ids)) != channels or any(t >= channels for t in targets):
            return None
        names = dict(zip(texts[channels:], (ids[t] for t in targets), strict=True))
        if len(names) != count:
            return None
        for o, n in zip(offsets, lengths, strict=True):
            if n and (o < _HEAD.size or o + n > at or n > _PACKED_MAX):
                return None
        return GuideTexts(path, _identity(st), since, until, ids, names, offsets, lengths)
    except (OSError, ValueError, struct.error, OverflowError):
        return None
