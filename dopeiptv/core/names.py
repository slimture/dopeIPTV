"""What plays, named once: a title whose parts repeat said with each part once.

A name is built in many places - a provider's "Show - S02E03", a Continue
watching row's "Show · " in front, failover's " · Source" after - and a row
played from one of them and kept again carried what was put on it into the
next. The window's title came to read "Show · Show · Show · S2 * E3 - ...".
``collapse_repeats`` is the net where a title comes in (the playback path,
History, Continue watching): a part said again right after itself goes, for
a channel, a film, an episode, a local file or anything else. Only a part
the same as the one just before it - "Love - Actually - Love" stays as it
is. No Qt; linear in the text.
"""
from __future__ import annotations

import re

#: The separators a name's parts are joined with, with a space either side.
_JOIN = re.compile(r"\s+([·\-–—|:])\s+")
#: Longest a title is looked at; past it, it is kept as it came.
TEXT_MAX = 4096


def _same(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def collapse_repeats(text: str) -> str:
    """*text* with a part said again right after itself left out, whatever
    joined them: "A · A · A · B - C" -> "A · B - C". A text with nothing
    repeated comes back as it came (the same object)."""
    if not isinstance(text, str) or not text or len(text) > TEXT_MAX:
        return text
    pieces = _JOIN.split(text)
    if len(pieces) < 3:
        return text
    # pieces: part, separator, part, separator, ... part
    kept = [pieces[0]]
    changed = False
    for i in range(1, len(pieces) - 1, 2):
        sep, part = pieces[i], pieces[i + 1]
        if part.strip() and _same(part, kept[-1]):
            changed = True
            continue
        kept.extend((f" {sep} ", part))
    return "".join(kept) if changed else text
