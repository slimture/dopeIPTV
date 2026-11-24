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

``source_name`` names a source the person gave no name: by its address's
host (and port), never by what the address carries before it - a login
written as user:password@ - nor by its path or query; a playlist file by
its own name.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

#: The separators a name's parts are joined with, with a space either side.
_JOIN = re.compile(r"\s+([·\-–—|:])\s+")
#: Longest a title is looked at; past it, it is kept as it came.
TEXT_MAX = 4096


#: Longest a name made from an address is.
NAME_MAX = 80
#: A Windows drive's path, a network share's, a path of this computer's.
_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/|~|\.{1,2}[\\/])")


def _file_name(path: str) -> str:
    base = re.split(r"[\\/]", path.rstrip("\\/"))[-1]
    stem = base.rsplit(".", 1)[0] if "." in base.lstrip(".") else base
    return "".join(c for c in stem if c.isprintable()).strip()[:NAME_MAX]


def source_name(address: str) -> str:
    """A source's name from its *address*, for one the person named
    nothing: a web address's host and port ("line.example.com:8080") -
    never a login in it, its path or its query - a playlist file's name
    without its extension (a path, a Windows path or a file: address);
    "" when there is none to be had."""
    text = (address or "").strip()
    if not text:
        return ""
    if text[:5].lower() == "file:":
        try:
            return _file_name(unquote(urlsplit(text).path))
        except ValueError:
            return ""
    if "://" not in text and (_PATH.match(text) or text.lower().endswith((".m3u", ".m3u8"))):
        return _file_name(text)
    try:
        parts = urlsplit(text if "://" in text else "http://" + text)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return ""
    host = "".join(c for c in host if c.isprintable())
    if not host:
        return ""
    if ":" in host:
        host = f"[{host}]"          # an IPv6 address
    return (f"{host}:{port}" if port else host)[:NAME_MAX]


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
