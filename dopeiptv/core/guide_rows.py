"""The guide's channel rows read from their names, no Qt (ui/epg_grid.py).

``section_heading``: a provider's list often has "channels" that are only
headings - "-= Sweden =-", "#### SE ####", "--- Sports ---" - with nothing
behind them. The guide draws such a row (when it has no programmes) as a
heading over the channels below it. Conservative on purpose: only a word
or a few between runs of decoration counts, so a real channel ("SE| SVT1",
"#1 Hits", "ESPN+", "[SE] TV4") is never taken for one.

``short_name``: a channel's name in a few letters ("SVT1 HD SE" -> "SVT1",
"Kanal 5" -> "K5") for a cell that has room for a logo and no name.
"""

from __future__ import annotations

import re
import unicodedata

# What decorates a heading: lines, hashes, stars, arrows, bullets.
_DECOR = frozenset("-=#*~_+|.:•·●○◉■□▪▫◆◇♦★☆✦✧✪✯▬═━─—–<>«»^/\\[](){}")
# One of these on each side is a heading by itself ("★ Sweden ★").
_ORNAMENT = frozenset("•●○◉■□▪▫◆◇♦★☆✦✧✪✯▬═━")
# A run on one side only is a heading when it is a line this long
# ("SE ━━━━━━━", "##### SPORT") - never dots, brackets or slashes.
_LINE = frozenset("-=#*~_▬═━─—–")
_ONE_SIDED = 4
# What may stand inside the heading's words ("SE | Sports", "Kids & Family").
_INNER = frozenset(" &'’.-/+!|:,")
_MAX_WORDS = 4
_MAX_LEN = 32


def _inner(c: str) -> bool:
    """A character a heading's words may have: letters and digits, a few
    joining marks, and symbols such as a flag ("== 🇸🇪 Sweden ==")."""
    return (c.isalnum() or c in _INNER
            or unicodedata.category(c) in ("So", "Sk", "Mn", "Cf"))


def _run(text: str) -> str:
    """The decoration (spaces left out) a string starts with."""
    out = []
    for c in text:
        if c in _DECOR:
            out.append(c)
        elif not c.isspace():
            break
    return "".join(out)


def section_heading(name: str | None) -> str | None:
    """The heading a "channel" named *name* stands for ("Sweden" for
    "-= Sweden =-"), "" for a line of decoration alone ("--------"), or None
    for a channel's real name."""
    s = (name or "").strip()
    if len(s) < 3 or len(s) > 64:
        return None
    lead, trail = _run(s), _run(s[::-1])
    i = 0
    while i < len(s) and (s[i] in _DECOR or s[i].isspace()):
        i += 1
    if i == len(s):                       # decoration alone
        return "" if len(lead) >= 3 else None
    j = len(s)
    while j > i and (s[j - 1] in _DECOR or s[j - 1].isspace()):
        j -= 1
    core = " ".join(s[i:j].split())
    if (not core or len(core) > _MAX_LEN
            or len(core.split()) > _MAX_WORDS
            or not any(c.isalnum() for c in core)
            or not all(_inner(c) for c in core)):
        return None
    if lead and trail:
        if len(lead) >= 2 and len(trail) >= 2:
            return core
        if set(lead) <= _ORNAMENT and set(trail) <= _ORNAMENT:
            return core
        return None
    run = lead or trail
    if len(run) >= _ONE_SIDED and set(run) <= _LINE:
        return core
    return None


# What says a stream's quality, not which channel it is.
_QUALITY = frozenset({
    "HD", "FHD", "UHD", "SD", "HQ", "LQ", "4K", "8K", "HEVC", "H264", "H265",
    "720P", "1080P", "1080I", "2160P", "50FPS", "60FPS", "HDR", "RAW", "VIP",
    "BACKUP", "ᴴᴰ",
})
_TAG_FIRST = re.compile(r"^\s*[\[(|]?\s*[A-Za-z]{2,3}\s*[\])|:]\s*(?=\S)")
_SPLIT = re.compile(r"[\s|/:_\-–—]+")
_COUNTRY = re.compile(r"[A-Z]{2}")
_NUMBER = re.compile(r"\d{1,3}")


def short_name(name: str | None, limit: int = 5) -> str:
    """*name* in at most *limit* characters, as a channel's short form:
    its first word when it is short ("SVT1", "N1"), a word's initial and
    its number ("K5", "TV4"), else the initials ("VSP", "FOS")."""
    raw = (name or "").strip()
    s = _TAG_FIRST.sub("", raw)
    words = [t for t in _SPLIT.split(s)
             if t and t.upper() not in _QUALITY and any(c.isalnum() for c in t)]
    if len(words) > 1 and _COUNTRY.fullmatch(words[-1]):
        words.pop()
    if not words:
        return raw[:limit]
    first = words[0]
    if len(words) > 1 and _NUMBER.fullmatch(words[-1]):
        lead = words[:-1]
        head = (lead[0] if len(lead) == 1 and len(lead[0]) <= 3
                else "".join(w[0] for w in lead[:3]))
        return (head.upper() + words[-1])[:limit]
    if len(first) == 1 and len(words) > 1:
        return "".join(w[0] for w in words[:3]).upper()[:limit]
    if len(first) <= limit:
        return first
    if len(words) > 1:
        return "".join(w[0] for w in words[:3]).upper()[:limit]
    return first[:4]
