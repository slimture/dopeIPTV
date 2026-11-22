"""Words from outside that came with HTML in them - a guide's description
copied from a web page ("Matchen börjar: <span class=...>15:00</span>.")
- as the plain text they say. The tags are dropped, never read: what is
left is shown as plain text, as before. Only HTML's own tag names count,
and only right after "<" or "</" as HTML has them, so "<Untitled>" or
"a < b > c" stay as written; an entity is decoded only
with its semicolon ("Barnes&noble" is no "&not"), and never into a control
character. Every pattern runs in time linear in the text."""

from __future__ import annotations

import html
import re

#: HTML's tag names a description copied from a page carries.
_TAGS = (
    "a abbr address article aside b bdi bdo big blockquote body br caption "
    "center cite code col colgroup dd del details dfn div dl dt em figcaption "
    "figure font footer h1 h2 h3 h4 h5 h6 head header hr html i iframe img "
    "ins kbd label li link main mark meta nav nobr noscript ol p picture pre "
    "q s samp script section small source span strike strong style sub "
    "summary sup table tbody td tfoot th thead time title tr tt u ul var wbr"
).split()
#: Those that end a line where the page did.
_BREAKS = frozenset(
    "address article blockquote br caption dd details div dl dt figcaption "
    "figure footer h1 h2 h3 h4 h5 h6 header hr li main nav ol p pre section "
    "summary table tr ul".split())
_TAG = re.compile(
    r"</?(" + "|".join(sorted(_TAGS, key=len, reverse=True))
    + r")(?![A-Za-z0-9:-])[^<>]*>", re.IGNORECASE)
_COMMENT = re.compile(r"<!--[^<>]*-->")
_ENTITY = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")
_SPACES = re.compile(r"[ \t ]+")
_AROUND = re.compile(r" ?\n ?")
_LINES = re.compile(r"\n{3,}")


def _tag(m: re.Match[str]) -> str:
    return "\n" if m.group(1).lower() in _BREAKS else ""


def _entity(m: re.Match[str]) -> str:
    got = html.unescape(m.group(0))
    if len(got) == 1 and (ord(got) < 32 and got not in "\t\n" or 0x7F <= ord(got) < 0xA0):
        return ""
    return got


def plain(text: str) -> str:
    """*text* without HTML's tags and with its entities decoded; text with
    neither comes back as it is (the same object)."""
    if "<" not in text and "&" not in text:
        return text
    tagged = False
    # Twice: a page's text escaped once more ("&lt;b&gt;") is tags too.
    for _ in range(2):
        cleaned = _TAG.sub(_tag, _COMMENT.sub("", text))
        tagged = tagged or cleaned != text
        cleaned = _ENTITY.sub(_entity, cleaned)
        if cleaned == text:
            break
        text = cleaned
    if not tagged:
        return text
    text = _SPACES.sub(" ", text)
    return _LINES.sub("\n\n", _AROUND.sub("\n", text)).strip()
