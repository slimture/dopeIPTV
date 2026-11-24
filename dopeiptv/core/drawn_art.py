"""Pictures the app draws itself instead of fetching them: the demo's
channel logos (a channel's short name on a tile of an accent colour) and its
films' posters (the title on a gradient of one).

Each is an address of its own - ``drawn:logo?t=BB&c=green`` - that says
what to draw, so a row carries it as it carries a web address, and the image
loader (core/workers.LogoLoader) draws it on its pool in place of a fetch:
nothing goes over the network, nothing is read from or written to the disk.
Whoever writes such an address gets words on a colour and nothing more - the
words cut to a few dozen characters, the colour one of the accents.

The colours are the theme's accents (ui/theme.py ACCENTS) and the ink that
reads on them, handed in by the window (``use_palette``): core does not
import the UI, and a picture is drawn only once they are in.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Mapping
from urllib.parse import parse_qs, urlencode

if TYPE_CHECKING:
    from PyQt6.QtGui import QImage

SCHEME = "drawn:"
KINDS = ("logo", "poster")
#: The most a tile's or a poster's words may be.
TEXT_MAX = 40
LINE_MAX = 60
#: The size a picture is drawn at before the cache's fit scales it.
LOGO_PX = 256
POSTER_W, POSTER_H = 400, 600

_lock = threading.Lock()
_colours: dict[str, str] = {}
_ink: Callable[[str], str] | None = None


@dataclass(frozen=True)
class Drawn:
    kind: str
    text: str
    colour: str
    line: str = ""


def use_palette(colours: Mapping[str, str], ink: Callable[[str], str]) -> None:
    """The accents by name (#rrggbb) and the ink that reads on a colour."""
    global _ink
    kept = {str(k): str(v) for k, v in colours.items()
            if isinstance(v, str) and len(v) == 7 and v.startswith("#")}
    with _lock:
        _colours.clear()
        _colours.update(kept)
        _ink = ink


def address(kind: str, text: str, colour: str, line: str = "") -> str:
    """The address that draws *text* (and a poster's *line* under it) on
    the accent *colour*."""
    if kind not in KINDS:
        raise ValueError(kind)
    query = {"t": text[:TEXT_MAX], "c": colour}
    if line:
        query["l"] = line[:LINE_MAX]
    return f"{SCHEME}{kind}?{urlencode(query)}"


def is_drawn(value: object) -> bool:
    return isinstance(value, str) and value.startswith(SCHEME)


def parse(value: object) -> Drawn | None:
    """What *value* asks to be drawn, or None when it is no such address."""
    if not is_drawn(value):
        return None
    text = str(value)[len(SCHEME):]
    if len(text) > 512:
        return None
    kind, _, query = text.partition("?")
    if kind not in KINDS:
        return None
    try:
        got = parse_qs(query, keep_blank_values=True, max_num_fields=4)
    except ValueError:
        return None

    def one(key: str, most: int) -> str:
        found = got.get(key) or [""]
        # Words alone: no line break or other control character.
        return "".join(ch for ch in found[0] if ch.isprintable())[:most].strip()

    words = one("t", TEXT_MAX)
    if not words:
        return None
    return Drawn(kind, words, one("c", 16), one("l", LINE_MAX))


def _fill(colour: str) -> str | None:
    with _lock:
        if not _colours:
            return None
        return _colours.get(colour) or next(iter(_colours.values()))


def render(value: object, fit: int = 0) -> QImage | None:
    """(any thread) The picture *value* asks for as a QImage, scaled to fit a
    *fit* square as the image cache keeps it - or None when it is no such
    address or no palette is in yet."""
    drawn = parse(value)
    if drawn is None:
        return None
    fill = _fill(drawn.colour)
    ink = _ink
    if fill is None or ink is None:
        return None
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter

    poster = drawn.kind == "poster"
    w, h = (POSTER_W, POSTER_H) if poster else (LOGO_PX, LOGO_PX)
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        base = QColor(fill)
        if poster:
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0.0, base.lighter(115))
            grad.setColorAt(1.0, base.darker(320))
            p.setBrush(grad)
            p.drawRect(0, 0, w, h)
        else:
            p.setBrush(base)
            r = w * 0.2
            p.drawRoundedRect(QRectF(0, 0, w, h), r, r)
        # The words in the ink that reads where they stand: a poster's on
        # the darker lower part of its gradient.
        under = QColor(fill)
        if poster:
            low = base.darker(320)
            under = QColor((base.red() + 2 * low.red()) // 3, (base.green() + 2 * low.green()) // 3,
                           (base.blue() + 2 * low.blue()) // 3)
        p.setPen(QColor(ink(under.name())))
        font = QFont()
        font.setWeight(QFont.Weight.Bold)
        if poster:
            # The title in the lower half, wrapped; its line under it.
            font.setPixelSize(44)
            p.setFont(font)
            box = QRectF(w * 0.09, h * 0.42, w * 0.82, h * 0.40)
            p.drawText(box, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom
                                | Qt.TextFlag.TextWordWrap), drawn.text)
            if drawn.line:
                small = QFont()
                small.setPixelSize(22)
                p.setFont(small)
                p.drawText(QRectF(w * 0.09, h * 0.84, w * 0.82, h * 0.1),
                           int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
                               | Qt.TextFlag.TextWordWrap), drawn.line)
        else:
            letters = drawn.text[:4]
            font.setPixelSize(int(h * (0.42 if len(letters) <= 2 else 0.30)))
            p.setFont(font)
            p.drawText(QRectF(0, 0, w, h), int(Qt.AlignmentFlag.AlignCenter), letters)
    finally:
        p.end()
    if fit > 0 and (img.width() > fit or img.height() > fit):
        img = img.scaled(fit, fit, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    return img
