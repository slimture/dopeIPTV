"""sketch.py - how many different visitors, kept without a mark of any one.

A day's visitors are kept as a sketch: a field of BITS bits where each
visitor sets two, picked by its mark (stats_collect.py: a keyed hash of
address and browser, the key only in the service's memory). Sketches of
several days put together (or) tell how many different visitors came over
those days, within a few in a thousand; a visitor's own two bits tell,
to whoever holds the key, whether it was there - nobody else can read
anything from them: without the key the bits are random, and the field
holds no mark, no list and no order. The same two bits on two days say
only that someone came on both, never who, nor which of the day's visits
was theirs.

Standard library only.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import math
import re
import zlib

BITS = 1 << 17                 # 16 KiB a sketch: a month of 20 000 visitors within 1 %
_BYTES = BITS // 8
HEX = re.compile(r"[0-9a-f]{12,64}")


def spots(mark: str) -> tuple:
    """The two bits a mark sets: from its own hex (stats_collect's marks are
    HMAC-SHA256 cut short), else from a hash of it."""
    h = mark if HEX.fullmatch(mark or "") else hashlib.sha256((mark or "").encode()).hexdigest()
    return int(h[0:6], 16) % BITS, int(h[6:12], 16) % BITS


def add(field: int, mark: str) -> int:
    a, b = spots(mark)
    return field | (1 << a) | (1 << b)


def has(field: int, mark: str) -> bool:
    a, b = spots(mark)
    return bool((field >> a) & 1 and (field >> b) & 1)


def of(marks) -> int:
    field = 0
    for m in marks:
        field = add(field, m)
    return field


def count(field: int) -> int:
    """How many different marks set the field (two bits each), estimated as
    a Bloom filter's: -(m/2) ln(1 - set/m)."""
    on = bin(field).count("1")
    if not on:
        return 0
    if on >= BITS:
        return BITS
    return int(round(-(BITS / 2) * math.log(1 - on / BITS)))


def union(fields) -> int:
    out = 0
    for f in fields:
        out |= f
    return out


def encode(field: int) -> str:
    """The field as kept: packed, then base64 - a day of a few hundred
    visitors is a kilobyte or two."""
    return base64.b64encode(zlib.compress(field.to_bytes(_BYTES, "little"), 9)).decode("ascii")


def decode(text) -> int:
    """A kept field; anything that is not one (a hand edit, a cut file) is
    an empty field, never an error and never more than a field's size
    unpacked."""
    if not isinstance(text, str) or len(text) > 4 * _BYTES:
        return 0
    try:
        packed = base64.b64decode(text.encode("ascii"), validate=True)
        z = zlib.decompressobj()
        raw = z.decompress(packed, _BYTES + 1)
    except (ValueError, binascii.Error, zlib.error, UnicodeEncodeError):
        return 0
    if len(raw) != _BYTES or z.unconsumed_tail or not z.eof or z.unused_data:
        return 0                            # cut short, too long, or more after it
    return int.from_bytes(raw, "little")
