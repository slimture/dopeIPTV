"""An SVG picture from outside, as Qt may draw it.

A logo or a poster may be an SVG, and Qt's SVG reader does what the file
says: ``<image xlink:href="/any/path">`` opens that file (on Windows
``//host/share/x.png`` logs in at that host with the person's account),
``<?xml-stylesheet href="/any/path"?>`` reads that file whole (``/dev/zero``
until the memory is gone), and both can hide - behind an entity, in an SVG
inside a ``data:`` address, in another encoding, in the second part of an
.svgz.

:func:`screened` gives back the picture as it came when it names nothing
but itself (``href="#..."`` and nothing else: nearly every logo), and
otherwise the same picture written out again from what an XML parser read,
without the stylesheet instruction and without every address that is not a
part of the picture or a picture carried in it. Qt then draws the bytes
that were looked at, not what a second parser might read differently. What
cannot be read at all, or is far larger than any picture, is no picture
(None).

No Qt and no network or disk: bytes in, bytes out.
"""

from __future__ import annotations

import base64
import binascii
import codecs
import re
import zlib
from xml.parsers import expat
from xml.sax.saxutils import escape, quoteattr

#: The most an SVG may be, unpacked. A logo is a few KB, a detailed map a
#: few MB.
SVG_MAX = 32 * 1024 * 1024
#: How deep a picture may carry another SVG in a data: address.
_NESTED_MAX = 3
#: Entities a picture may declare (old drawing programs name their
#: namespaces so), and how long one may be.
_ENTITIES_MAX = 128
_ENTITY_LEN_MAX = 8192

_GZIP = b"\x1f\x8b"
_HREF = re.compile(rb"href\s*=")
_HREF_PART = re.compile(rb"href\s*=\s*[\"']\s*#")
_DECLARED = re.compile(rb"^[^>]{0,400}?encoding\s*=\s*[\"']([A-Za-z0-9._:-]{1,40})[\"']")
_NOT_BASE64 = re.compile(r"[^A-Za-z0-9+/]+")
_BOMS = ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF32_LE, "utf-32"),
         (codecs.BOM_UTF32_BE, "utf-32"), (codecs.BOM_UTF16_LE, "utf-16"),
         (codecs.BOM_UTF16_BE, "utf-16"))

#: Encodings in which ASCII is itself and nothing else is: what the markup
#: says is in the bytes as it stands. Any other (UTF-16, UTF-7, EBCDIC, a
#: stateful one) is read and written out again.
_PLAIN = frozenset((
    "utf8", "usascii", "ascii", "latin1", "latin2", "latin9", "koi8r", "koi8u",
    "macintosh", "shiftjis", "sjis", "eucjp", "euckr", "gb2312", "gbk",
    "gb18030", "big5", "tis620", "cp437", "cp850", "cp866", "ibm437", "ibm850",
    "ibm866"))
_PLAIN_PREFIXES = ("iso8859", "windows125", "cp125")


class _Refused(Exception):
    """Not a picture the app draws."""


def _markup(data: bytes) -> bool:
    """Whether *data* begins as markup - what Qt takes an SVG by: a ``<``
    first, after a byte order mark and any white space (in UTF-16 too)."""
    head = data[:4096]
    for bom, enc in _BOMS:
        if head.startswith(bom):
            return head.decode(enc, "replace").lstrip().startswith("<")
    if head[:2] in (b"<\x00", b"\x00<") or head[:4] in (b"<\x00\x00\x00", b"\x00\x00\x00<"):
        return True
    return head.decode("utf-8", "replace").lstrip().startswith("<")


def maybe_svg(data: bytes) -> bool:
    """Whether Qt may read *data* as an SVG: it begins as markup, or is
    gzipped (an .svgz). Wider than Qt's own answer, never narrower."""
    return data[:2] == _GZIP or _markup(data)


def _unpacked(data: bytes) -> bytes | None:
    """*data* without its gzip wrapping (an .svgz) - every member of it, as
    Qt reads one; None when it unpacks to more than SVG_MAX or is damaged."""
    if data[:2] != _GZIP:
        return data if len(data) <= SVG_MAX else None
    out: list[bytes] = []
    size = 0
    rest = data
    while rest[:2] == _GZIP:
        d = zlib.decompressobj(31)
        try:
            piece = d.decompress(rest, SVG_MAX + 1 - size)
        except zlib.error:
            return None
        size += len(piece)
        if size > SVG_MAX or not d.eof:
            return None             # too large, or cut short
        out.append(piece)
        rest = d.unused_data
    return b"".join(out)


def _plain_encoding(data: bytes) -> bool:
    """Whether the bytes say what the markup says: no UTF-16/32 (a NUL), and
    an encoding of its own only when ASCII is itself in it."""
    if b"\x00" in data:
        return False
    head = data[:600].lstrip(b"\xef\xbb\xbf \t\r\n")
    if not head.startswith(b"<?xml"):
        return True
    end = head.find(b"?>")
    if end < 0:
        return False
    decl = head[:end]
    if b"encoding" not in decl:
        return True
    m = _DECLARED.match(decl)
    if m is None:
        return False
    name = re.sub(r"[^a-z0-9]", "", m.group(1).decode("ascii", "replace").lower())
    return name in _PLAIN or name.startswith(_PLAIN_PREFIXES)


def names_nothing_else(data: bytes) -> bool:
    """Whether an SVG's bytes can name nothing outside the picture: no
    entity (which could hide a name), no stylesheet instruction, and every
    href a part of the picture itself ("#...")."""
    if not _plain_encoding(data):
        return False
    if b"<!ENTITY" in data or b"xml-stylesheet" in data:
        return False
    return len(_HREF.findall(data)) == len(_HREF_PART.findall(data))


def _text(data: bytes) -> str:
    """An SVG's bytes as text, in the encoding its first bytes or its
    declaration name."""
    for bom, enc in _BOMS:
        if data.startswith(bom):
            return data.decode(enc, "replace")
    for pattern, enc in ((b"<\x00\x00\x00", "utf-32-le"), (b"\x00\x00\x00<", "utf-32-be"),
                         (b"<\x00", "utf-16-le"), (b"\x00<", "utf-16-be")):
        if data.startswith(pattern):
            return data.decode(enc, "replace")
    m = _DECLARED.match(data[:600].lstrip())
    enc = m.group(1).decode("ascii", "replace") if m else "utf-8"
    try:
        codecs.lookup(enc)
    except LookupError:
        raise _Refused(f"an encoding this app does not read ({enc})") from None
    return data.decode(enc, "replace")


def _is_href(name: str) -> bool:
    return name.lower().rpartition(":")[2] == "href"


def _qt_base64(text: str) -> bytes:
    """What Qt's reader makes of base64 text: every character outside the
    alphabet skipped (padding too), the bits read on across them, a last
    few that fill no byte dropped."""
    kept = _NOT_BASE64.sub("", text)
    whole = base64.b64decode(kept + "A" * (-len(kept) % 4))
    return whole[:len(kept) * 6 // 8]


def _carried(value: str, depth: int) -> str:
    """A data: address as it may stay: the picture it carries, written as
    base64 again so that Qt reads the very bytes looked at here - screened
    first when it may be an SVG itself; "" when it carries none Qt would
    read."""
    marker = value.rfind("base64,")
    if marker < 0:
        return ""                   # Qt reads no other form of it
    try:
        payload = _qt_base64(value[marker + 7:])
    except (binascii.Error, ValueError):
        return ""
    if maybe_svg(payload):
        inner = screened(payload, depth + 1) if depth < _NESTED_MAX else None
        if inner is None:
            return ""
        payload = inner
    return value[:marker + 7] + base64.b64encode(payload).decode("ascii")


def _written_again(text: str, depth: int = 0) -> bytes:
    """*text* (an SVG) as an XML parser reads it, written out: elements,
    attributes and text as they are (entities expanded), no DTD, no
    processing instruction, and no href that is not a part of the picture
    ("#...") or a picture carried in it (data:)."""
    out: list[str] = []
    size = 0
    open_tags: list[str] = []
    entities = 0

    def put(piece: str) -> None:
        nonlocal size
        size += len(piece)
        if size > SVG_MAX:
            raise _Refused("larger than any picture")
        out.append(piece)

    def start(name: str, attrs: list[str]) -> None:
        parts = [name]
        for i in range(0, len(attrs), 2):
            key, value = attrs[i], attrs[i + 1]
            if _is_href(key):
                v = value.strip()
                if v.startswith("data"):
                    value = _carried(v, depth)
                elif not v.startswith("#"):
                    value = ""
                if not value:
                    continue        # names nothing: as if it were not there
            parts.append(f"{key}={quoteattr(value)}")
        open_tags.append(name)
        put("<" + " ".join(parts) + ">")

    def end(name: str) -> None:
        if open_tags:
            open_tags.pop()
        put(f"</{name}>")

    def chars(data: str) -> None:
        put(escape(data))

    def entity(_name: str, _is_parameter: int, value: str | None, *_rest: object) -> None:
        nonlocal entities
        entities += 1
        # One that names another is how a few bytes become gigabytes.
        if entities > _ENTITIES_MAX or (value is not None and (
                len(value) > _ENTITY_LEN_MAX or "&" in value)):
            raise _Refused("entities that expand")

    p = expat.ParserCreate()
    p.ordered_attributes = True
    p.buffer_text = True
    p.StartElementHandler = start
    p.EndElementHandler = end
    p.CharacterDataHandler = chars
    p.EntityDeclHandler = entity
    try:
        p.Parse(text, True)
    except expat.ExpatError:
        # Not well-formed from here on: what was read so far, closed.
        if not out:
            raise _Refused("not XML") from None
        while open_tags:
            out.append(f"</{open_tags.pop()}>")
    return "".join(out).encode("utf-8")


def screened(data: bytes, depth: int = 0) -> bytes | None:
    """*data* as Qt may draw it. What is no SVG (:func:`maybe_svg`) comes
    back as it is; an SVG comes back as its own bytes (an .svgz unpacked)
    when the picture names nothing but itself, else written out again
    without what it names (see the module). None when it is an SVG the app
    does not draw: unreadable, or far larger than any picture."""
    if not maybe_svg(data):
        return data
    plain = _unpacked(data)
    if plain is None:
        return None
    if not _markup(plain):
        return data                 # something else gzipped: not an SVG to Qt
    if names_nothing_else(plain):
        # The bytes that were looked at: Qt reads them, and not its own
        # unpacking of what came.
        return plain
    try:
        return _written_again(_text(plain), depth)
    except _Refused:
        return None
    except (RecursionError, MemoryError, ValueError, LookupError):
        return None
