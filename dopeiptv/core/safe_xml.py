"""XML from outside, read without a DTD of its own (AGENTS.md rule 5).

Every XML document the app parses is somebody else's: a box's answer, a
device's description, a guide. A DTD of its own - an internal subset, where
entities are declared - lets a few kilobytes balloon in the parse (a
"billion laughs"), and the Linux bundle takes the system's expat, which may
have no amplification limit. A look at the bytes for "<!ENTITY" misses one
written in UTF-16 or in a declared UTF-7, and a pattern for the DOCTYPE
misses one with a "]" inside a declaration. So expat itself reads the
prolog - the only place a DTD can be - decoding it as the parse will, up to
the root element, however long a comment before it is:

``refuse_dtd``  ValueError for a DOCTYPE with an internal subset or an
                entity declared, and for a declared encoding no codec here
                reads (expat's LookupError, which no caller expected); a
                document that is no XML is left to the parse to refuse. A DOCTYPE that only names an external DTD
                (<!DOCTYPE tv SYSTEM "xmltv.dtd">) has no subset and
                parses: neither expat nor ElementTree fetches one.
``fromstring``  ElementTree's, after that check.
``Prolog``      the same check fed a slice at a time, for a guide read as
                it streams.
``drop_dtd``    a DOCTYPE cut out of a box's text, as the boxes always did,
                in one pass - ``refuse_dtd`` after it is what proves none is
                left.
``slow_codec``  whether a charset an answer declares is one whose decoder
                takes time with the square of the text (punycode, and idna
                that runs it): no document is written in one, and one that
                says so is read as UTF-8.
"""

from __future__ import annotations

import codecs
import re
import xml.etree.ElementTree as ET
from typing import Any
from xml.parsers import expat

#: How much of a document expat is handed at a time.
_STEP = 1 << 18
#: Codecs no document is written in whose decoder takes time with the square
#: of the text: a box's answer declared in punycode, a megabyte of it, took
#: minutes (tests/test_fuzz_boxes.py).
_SLOW_CODECS = frozenset(("punycode", "idna"))

_DOCTYPE = re.compile(r"<!DOCTYPE", re.I)
#: What can end, open or hide a DOCTYPE's parts: a quoted literal, the
#: subset's brackets, the end, a comment or a processing instruction.
_STOP = re.compile(r"[\"'\[\]>]|<!--|<\?")


class _Root(Exception):
    """The root element began: the prolog, the only place for a DTD, is over."""


class Prolog:
    """The check, fed a document a slice at a time (``feed``: True once the
    root element began, so no DTD can follow). Raises ValueError for a DTD
    of its own or an encoding no codec reads; a document expat cannot read
    is left to the parse."""

    def __init__(self, what: str = "XML", encoding: str | None = None) -> None:
        self.what = what
        self.done = False
        self._p = expat.ParserCreate(encoding)
        self._p.StartDoctypeDeclHandler = self._doctype
        self._p.EntityDeclHandler = self._entity
        self._p.StartElementHandler = self._root

    def _doctype(self, _name: str, _sysid: Any, _pubid: Any, internal: int) -> None:
        if internal:
            raise ValueError(f"{self.what} declares a DTD of its own (entities); refusing to parse")

    def _entity(self, *_args: Any) -> None:
        raise ValueError(f"{self.what} declares entities; refusing to parse")

    def _root(self, *_args: Any) -> None:
        raise _Root

    def feed(self, data: bytes, final: bool = False) -> bool:
        if self.done:
            return True
        try:
            view = memoryview(data)
            for i in range(0, len(view), _STEP):
                self._p.Parse(bytes(view[i:i + _STEP]), False)
            if final:
                self._p.Parse(b"", True)
                self.done = True
        except (_Root, expat.ExpatError):
            self.done = True
        except LookupError:
            # encoding="rot13", "hex" or one nobody has: the parse could
            # not read it either (tests/test_fuzz_xmltv.py).
            self.done = True
            raise ValueError(f"{self.what} declares an encoding no reader has; refusing to parse") from None
        return self.done


def slow_codec(name: Any) -> bool:
    """Whether *name* (a declared charset) names one of _SLOW_CODECS."""
    if not isinstance(name, str) or not name:
        return False
    try:
        return codecs.lookup(name).name in _SLOW_CODECS
    except LookupError:
        return False


def refuse_dtd(data: bytes | str, what: str = "XML") -> None:
    """Raise ValueError when *data* declares a DTD of its own (see the
    module's docstring). Text is read as ElementTree reads text: UTF-8,
    whatever its declaration says."""
    if isinstance(data, str):
        Prolog(what, "utf-8").feed(data.encode("utf-8"), True)
    else:
        Prolog(what).feed(bytes(data), True)


def fromstring(data: bytes | str, what: str = "XML") -> ET.Element:
    """ElementTree's fromstring, once *data* declares no DTD of its own."""
    refuse_dtd(data, what)
    return ET.fromstring(data)


def drop_dtd(text: str) -> str:
    """*text* without its DOCTYPE and the internal subset in it, the
    subset's quoted literals, comments and processing instructions read
    whole - a "]" or ">" inside one ends nothing. A DOCTYPE that does not
    end is left where it is, for ``refuse_dtd`` to refuse."""
    m = _DOCTYPE.search(text)
    if m is None:
        return text
    i, subset = m.end(), False
    while True:
        s = _STOP.search(text, i)
        if s is None:
            return text
        token = s.group()
        if token in ("'", '"'):
            end = text.find(token, s.end())
            if end < 0:
                return text
            i = end + 1
        elif token == "<!--" or token == "<?":
            if not subset:
                return text
            end = text.find("-->" if token == "<!--" else "?>", s.end())
            if end < 0:
                return text
            i = end + (3 if token == "<!--" else 2)
        elif token == "[":
            subset, i = True, s.end()
        elif token == "]":
            subset, i = False, s.end()
        elif subset:                    # ">" closing a declaration inside the subset
            i = s.end()
        else:
            return text[:m.start()] + text[s.end():]
