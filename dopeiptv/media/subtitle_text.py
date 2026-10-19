"""Direction marks a subtitle line should not carry.

A subtitle file translated from an Arabic or Hebrew one can keep that
file's right-to-left embedding (U+202B ... U+202C) or isolate (U+2067 ...
U+2069) around every line. mpv honours them, so a Swedish or English line
was laid out right to left, its full stop or question mark in front
(".Hej då"). Lines without a right-to-left letter lose those marks; a line
in Arabic or Hebrew keeps them.
"""
from __future__ import annotations

import unicodedata

# Embeddings, overrides, isolates and the right-to-left marks.
_MARKS = "‪‫‬‭‮⁦⁧⁨⁩‏؜"
_DROP = dict.fromkeys(map(ord, _MARKS))


def _has_rtl_letter(line: str) -> bool:
    return any(unicodedata.bidirectional(c) in ("R", "AL") for c in line)


def strip_stray_marks(text: str) -> str:
    """*text* without direction marks on the lines that have no
    right-to-left letter."""
    if not any(mark in text for mark in _MARKS):
        return text
    return "".join(
        line if _has_rtl_letter(line) else line.translate(_DROP)
        for line in text.splitlines(keepends=True))


def fixed_bytes(data: bytes) -> bytes | None:
    """A subtitle file's *data* without stray direction marks, in its own
    encoding - or None when it has none, or is not UTF-8 or UTF-16, the
    only encodings that can hold them."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        enc = "utf-16"
    elif data.startswith(b"\xef\xbb\xbf"):
        enc = "utf-8-sig"
    else:
        enc = "utf-8"
    try:
        text = data.decode(enc)
    except UnicodeDecodeError:
        return None
    fixed = strip_stray_marks(text)
    return None if fixed == text else fixed.encode(enc)
