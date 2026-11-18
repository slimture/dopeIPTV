"""The made-up art of the website's pictures, drawn here in one style.

Everything a picture of the app shows that a provider or the user would
otherwise bring - channel logos, film and series posters, backdrops,
episode stills, and what plays in the player - is drawn by this module:
flat shapes from one friendly palette, rounded, no outlines, the titles
invented. tools/site_screenshots.py uses it; nothing here touches the app.

Needs a QGuiApplication (the fonts). Every function returns a QImage.
"""

from __future__ import annotations

import math
import random

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter,
    QPainterPath, QPen, QPolygonF, QRadialGradient,
)

#: The one palette everything is drawn from.
INK = "#1D2340"
NIGHT = "#141A3A"
CREAM = "#FFF4E2"
CORAL = "#FF6B57"
ORANGE = "#FF9F43"
SUN = "#FFC94A"
MINT = "#34CFA0"
TEAL = "#1FB5C1"
SKY = "#4C8DFF"
VIOLET = "#7C5CFF"
BERRY = "#E8508C"
PLUM = "#5B2A86"
LEAF = "#1E9E62"
DEEP = "#123C69"

FONT = "Inter"


def _c(hex_: str, alpha: int = 255) -> QColor:
    c = QColor(hex_)
    c.setAlpha(alpha)
    return c


def mix(a: str, b: str, t: float) -> str:
    """*a* toward *b* by *t* (0..1), as #rrggbb."""
    ca, cb = QColor(a), QColor(b)
    r = round(ca.red() + (cb.red() - ca.red()) * t)
    g = round(ca.green() + (cb.green() - ca.green()) * t)
    bl = round(ca.blue() + (cb.blue() - ca.blue()) * t)
    return f"#{r:02x}{g:02x}{bl:02x}"


def _image(w: int, h: int, clear: bool = False) -> tuple[QImage, QPainter]:
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent if clear else QColor(INK))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setPen(Qt.PenStyle.NoPen)
    return img, p


def _vgrad(p: QPainter, r: QRectF, stops: list[tuple[float, str]]) -> None:
    g = QLinearGradient(r.topLeft(), r.bottomLeft())
    for t, col in stops:
        g.setColorAt(t, QColor(col))
    p.fillRect(r, QBrush(g))


def _font(px: float, weight: QFont.Weight = QFont.Weight.Black) -> QFont:
    f = QFont(FONT)
    f.setPixelSize(max(1, round(px)))
    f.setWeight(weight)
    return f


def _poly(points: list[tuple[float, float]]) -> QPolygonF:
    return QPolygonF([QPointF(x, y) for x, y in points])


# -- channel logos --------------------------------------------------------------------

def _glyph(kind: str, s: float) -> QPainterPath:
    """A logo's glyph as one path in a box of side *s* at the origin."""
    path = QPainterPath()
    c = s / 2
    if kind == "globe":
        r = s * 0.36
        ring = QPainterPath()
        ring.addEllipse(QPointF(c, c), r, r)
        inner = QPainterPath()
        inner.addEllipse(QPointF(c, c), r * 0.8, r * 0.8)
        path = ring.subtracted(inner)
        stroke = QPainterPathStroker_like(s * 0.075)
        lines = QPainterPath()
        lines.addEllipse(QPointF(c, c), r * 0.42, r * 0.9)
        lines.moveTo(c - r, c)
        lines.lineTo(c + r, c)
        lines.moveTo(c - r * 0.85, c - r * 0.48)
        lines.lineTo(c + r * 0.85, c - r * 0.48)
        lines.moveTo(c - r * 0.85, c + r * 0.48)
        lines.lineTo(c + r * 0.85, c + r * 0.48)
        path = path.united(stroke(lines))
    elif kind == "signal":
        path.addEllipse(QPointF(c, c + s * 0.02), s * 0.09, s * 0.09)
        arcs = QPainterPath()
        for k, rr in ((1, 0.2), (2, 0.33)):
            box = QRectF(c - s * rr, c + s * 0.02 - s * rr, 2 * s * rr, 2 * s * rr)
            arcs.arcMoveTo(box, 140)
            arcs.arcTo(box, 140, 80)
            arcs.arcMoveTo(box, -40)
            arcs.arcTo(box, -40, 80)
            del k
        path = path.united(QPainterPathStroker_like(s * 0.075)(arcs))
    elif kind == "orbit":
        path.addEllipse(QPointF(c, c), s * 0.16, s * 0.16)
        box = QRectF(c - s * 0.31, c - s * 0.31, s * 0.62, s * 0.62)
        arc = QPainterPath()
        arc.arcMoveTo(box, 60)
        arc.arcTo(box, 60, 270)
        path = path.united(QPainterPathStroker_like(s * 0.06)(arc))
        a = math.radians(30)
        path = path.united(_ellipse_path(c + math.cos(a) * s * 0.31, c - math.sin(a) * s * 0.31,
                                         s * 0.075, s * 0.075))
    elif kind == "sparkle":
        def star(cx: float, cy: float, r: float) -> QPainterPath:
            st = QPainterPath()
            st.moveTo(cx, cy - r)
            st.quadTo(cx + r * 0.14, cy - r * 0.14, cx + r, cy)
            st.quadTo(cx + r * 0.14, cy + r * 0.14, cx, cy + r)
            st.quadTo(cx - r * 0.14, cy + r * 0.14, cx - r, cy)
            st.quadTo(cx - r * 0.14, cy - r * 0.14, cx, cy - r)
            st.closeSubpath()
            return st
        path = star(c - s * 0.05, c + s * 0.05, s * 0.33).united(
            star(c + s * 0.25, c - s * 0.24, s * 0.13))
    elif kind == "saturn":
        rot = QTransformRotate(c, c, -18)
        body = _ellipse_path(c, c, s * 0.2, s * 0.2)
        ring = rot.map(QPainterPathStroker_like(s * 0.055)(_ellipse_path(c, c, s * 0.42, s * 0.12)))
        gap = rot.map(QPainterPathStroker_like(s * 0.12)(_ellipse_path(c, c, s * 0.42, s * 0.12)))
        back = QPainterPath()
        back.addRect(QRectF(0, 0, s, c))
        back = rot.map(back)
        # The ring passes behind the planet at the top, in front below.
        path = body.subtracted(gap.subtracted(back)).united(ring.subtracted(body.intersected(back)))
    elif kind == "rocket":
        r = QPainterPath()
        r.moveTo(c, s * 0.12)
        r.cubicTo(c + s * 0.17, s * 0.24, c + s * 0.15, s * 0.5, c + s * 0.11, s * 0.66)
        r.lineTo(c - s * 0.11, s * 0.66)
        r.cubicTo(c - s * 0.15, s * 0.5, c - s * 0.17, s * 0.24, c, s * 0.12)
        fins = QPainterPath()
        fins.addPolygon(_poly([(c - s * 0.12, s * 0.48), (c - s * 0.25, s * 0.68),
                               (c - s * 0.1, s * 0.64)]))
        fins.addPolygon(_poly([(c + s * 0.12, s * 0.48), (c + s * 0.25, s * 0.68),
                               (c + s * 0.1, s * 0.64)]))
        flame = QPainterPath()
        flame.moveTo(c - s * 0.07, s * 0.7)
        flame.quadTo(c, s * 0.92, c + s * 0.07, s * 0.7)
        flame.closeSubpath()
        window = QPainterPath()
        window.addEllipse(QPointF(c, s * 0.36), s * 0.055, s * 0.055)
        path = r.united(fins).subtracted(window).united(flame)
    elif kind == "ball":
        disc = _ellipse_path(c, c, s * 0.36, s * 0.36)

        def pent(cx: float, cy: float, rr: float, rot: float) -> QPainterPath:
            pp = QPainterPath()
            pp.addPolygon(_poly([(cx + math.cos(math.radians(rot + 72 * i)) * rr,
                                  cy + math.sin(math.radians(rot + 72 * i)) * rr) for i in range(5)]))
            pp.closeSubpath()
            return pp
        cut = pent(c, c, s * 0.12, -90)
        for i in range(5):
            a = math.radians(-90 + 36 + 72 * i)
            cut = cut.united(pent(c + math.cos(a) * s * 0.33, c + math.sin(a) * s * 0.33,
                                  s * 0.1, -90 + 36 + 72 * i + 180))
        path = disc.subtracted(cut)
    elif kind == "tennis":
        path.addEllipse(QPointF(c, c), s * 0.36, s * 0.36)
        seam = QPainterPath()
        seam.moveTo(c - s * 0.3, c - s * 0.2)
        seam.cubicTo(c - s * 0.08, c - s * 0.05, c - s * 0.08, c + s * 0.05, c - s * 0.3, c + s * 0.2)
        seam.moveTo(c + s * 0.3, c - s * 0.2)
        seam.cubicTo(c + s * 0.08, c - s * 0.05, c + s * 0.08, c + s * 0.05, c + s * 0.3, c + s * 0.2)
        path = path.subtracted(QPainterPathStroker_like(s * 0.055)(seam))
    elif kind == "clapper":
        body = QPainterPath()
        body.addRoundedRect(QRectF(s * 0.18, s * 0.42, s * 0.64, s * 0.38), s * 0.05, s * 0.05)
        top = QPainterPath()
        top.addRoundedRect(QRectF(s * 0.18, s * 0.24, s * 0.64, s * 0.13), s * 0.03, s * 0.03)
        top = QTransformRotate(s * 0.18, s * 0.37, -12).map(top)
        stripes = QPainterPath()
        for i in range(3):
            x = s * (0.28 + i * 0.17)
            stripes.addPolygon(_poly([(x, s * 0.42), (x + s * 0.08, s * 0.42),
                                      (x + s * 0.02, s * 0.53), (x - s * 0.06, s * 0.53)]))
        path = body.subtracted(stripes).united(top)
    elif kind == "reel":
        path.addEllipse(QPointF(c, c), s * 0.36, s * 0.36)
        holes = QPainterPath()
        holes.addEllipse(QPointF(c, c), s * 0.05, s * 0.05)
        for i in range(5):
            a = math.radians(-90 + 72 * i)
            holes.addEllipse(QPointF(c + math.cos(a) * s * 0.19, c + math.sin(a) * s * 0.19),
                             s * 0.075, s * 0.075)
        path = path.subtracted(holes)
    elif kind == "star":
        pts = []
        for i in range(10):
            a = math.radians(-90 + 36 * i)
            rr = s * (0.38 if i % 2 == 0 else 0.17)
            pts.append((c + math.cos(a) * rr, c + s * 0.03 + math.sin(a) * rr))
        star = QPainterPath()
        star.addPolygon(_poly(pts))
        star.closeSubpath()
        path = QPainterPathStroker_like(s * 0.06, join=True)(star).united(star)
    elif kind == "note":
        stem = QPainterPath()
        stem.addRect(QRectF(c - s * 0.12, s * 0.22, s * 0.06, s * 0.46))
        stem.addRect(QRectF(c + s * 0.2, s * 0.17, s * 0.06, s * 0.46))
        beam = QPainterPath()
        beam.addPolygon(_poly([(c - s * 0.12, s * 0.22), (c + s * 0.26, s * 0.14),
                               (c + s * 0.26, s * 0.25), (c - s * 0.12, s * 0.33)]))
        heads = QPainterPath()
        for x, y in ((c - s * 0.2, s * 0.69), (c + s * 0.12, s * 0.64)):
            h = _ellipse_path(x, y, s * 0.11, s * 0.08)
            heads = heads.united(QTransformRotate(x, y, -20).map(h))
        path = stem.united(beam).united(heads)
    elif kind == "leaf":
        leaf = QPainterPath()
        leaf.moveTo(c - s * 0.26, c + s * 0.26)
        leaf.cubicTo(c - s * 0.3, c - s * 0.2, c + s * 0.05, c - s * 0.34, c + s * 0.32, c - s * 0.32)
        leaf.cubicTo(c + s * 0.34, c - s * 0.02, c + s * 0.18, c + s * 0.3, c - s * 0.26, c + s * 0.26)
        vein = QPainterPath()
        vein.moveTo(c - s * 0.3, c + s * 0.32)
        vein.quadTo(c, c, c + s * 0.22, c - s * 0.22)
        path = leaf.subtracted(QPainterPathStroker_like(s * 0.045)(vein)).united(
            QPainterPathStroker_like(s * 0.05)(_line(c - s * 0.36, c + s * 0.38, c - s * 0.24, c + s * 0.26)))
    elif kind == "mountain":
        m = QPainterPath()
        m.addPolygon(_poly([(s * 0.12, s * 0.74), (s * 0.4, s * 0.3), (s * 0.54, s * 0.5),
                            (s * 0.64, s * 0.38), (s * 0.88, s * 0.74)]))
        m.closeSubpath()
        snow = QPainterPath()
        snow.addPolygon(_poly([(s * 0.4, s * 0.3), (s * 0.48, s * 0.43), (s * 0.43, s * 0.41),
                               (s * 0.39, s * 0.45), (s * 0.35, s * 0.4), (s * 0.32, s * 0.43)]))
        snow.closeSubpath()
        path = QPainterPathStroker_like(s * 0.05, join=True)(m).united(m).subtracted(snow)
        path = path.united(_ellipse_path(s * 0.74, s * 0.24, s * 0.07, s * 0.07))
    elif kind == "sun":
        path.addEllipse(QPointF(c, c), s * 0.17, s * 0.17)
        rays = QPainterPath()
        for i in range(8):
            a = math.radians(45 * i)
            rays.moveTo(c + math.cos(a) * s * 0.26, c + math.sin(a) * s * 0.26)
            rays.lineTo(c + math.cos(a) * s * 0.36, c + math.sin(a) * s * 0.36)
        path = path.united(QPainterPathStroker_like(s * 0.06)(rays))
    elif kind == "bird":
        b = QPainterPath()
        b.moveTo(c - s * 0.36, c - s * 0.06)
        b.quadTo(c - s * 0.18, c - s * 0.2, c, c + s * 0.06)
        b.quadTo(c + s * 0.18, c - s * 0.2, c + s * 0.36, c - s * 0.06)
        b.quadTo(c + s * 0.16, c - s * 0.02, c, c + s * 0.2)
        b.quadTo(c - s * 0.16, c - s * 0.02, c - s * 0.36, c - s * 0.06)
        b.closeSubpath()
        path = b.united(_ellipse_path(c + s * 0.2, c - s * 0.26, s * 0.06, s * 0.06))
    elif kind == "atom":
        path.addEllipse(QPointF(c, c), s * 0.07, s * 0.07)
        for deg in (0, 60, 120):
            orbit = QTransformRotate(c, c, deg).map(_ellipse_path(c, c, s * 0.36, s * 0.13))
            path = path.united(QPainterPathStroker_like(s * 0.05)(orbit))
    elif kind == "flask":
        f = QPainterPath()
        f.moveTo(c - s * 0.08, s * 0.18)
        f.lineTo(c + s * 0.08, s * 0.18)
        f.lineTo(c + s * 0.08, s * 0.4)
        f.lineTo(c + s * 0.28, s * 0.74)
        f.quadTo(c + s * 0.31, s * 0.8, c + s * 0.24, s * 0.8)
        f.lineTo(c - s * 0.24, s * 0.8)
        f.quadTo(c - s * 0.31, s * 0.8, c - s * 0.28, s * 0.74)
        f.lineTo(c - s * 0.08, s * 0.4)
        f.closeSubpath()
        liquid = QPainterPath()
        liquid.addRect(QRectF(0, s * 0.56, s, s))
        hollow = QPainterPathStroker_like(s * 0.05, join=True)(f)
        path = hollow.united(f.intersected(liquid))
        path = path.united(_ellipse_path(c - s * 0.02, s * 0.46, s * 0.03, s * 0.03))
    elif kind == "fish":
        body = _ellipse_path(c - s * 0.04, c, s * 0.25, s * 0.16)
        tail = QPainterPath()
        tail.addPolygon(_poly([(c + s * 0.16, c), (c + s * 0.36, c - s * 0.15),
                               (c + s * 0.36, c + s * 0.15)]))
        eye = _ellipse_path(c - s * 0.16, c - s * 0.03, s * 0.035, s * 0.035)
        path = body.united(tail).subtracted(eye)
        bubble = QPainterPath()
        bubble.addEllipse(QPointF(c - s * 0.33, c - s * 0.25), s * 0.05, s * 0.05)
        path = path.united(bubble)
    return path


def _ellipse_path(x: float, y: float, rx: float, ry: float) -> QPainterPath:
    p = QPainterPath()
    p.addEllipse(QPointF(x, y), rx, ry)
    return p


def _line(x1: float, y1: float, x2: float, y2: float) -> QPainterPath:
    p = QPainterPath()
    p.moveTo(x1, y1)
    p.lineTo(x2, y2)
    return p


def QPainterPathStroker_like(width: float, join: bool = False):  # noqa: N802
    """A function that turns a path into its stroke, *width* wide, round
    ends (a filled outline, so it unites with the rest of a glyph)."""
    from PyQt6.QtGui import QPainterPathStroker
    st = QPainterPathStroker()
    st.setWidth(width)
    st.setCapStyle(Qt.PenCapStyle.RoundCap)
    st.setJoinStyle(Qt.PenJoinStyle.RoundJoin if join else Qt.PenJoinStyle.RoundJoin)
    return lambda path: st.createStroke(path).simplified()


class QTransformRotate:  # noqa: N801
    """A rotation by *deg* about (*x*, *y*)."""

    def __init__(self, x: float, y: float, deg: float) -> None:
        from PyQt6.QtGui import QTransform
        self.t = QTransform().translate(x, y).rotate(deg).translate(-x, -y)

    def map(self, path: QPainterPath) -> QPainterPath:
        return self.t.map(path)


def logo(glyph: str, colour: str, size: int = 256) -> QImage:
    """A channel's logo: a bold rounded square in *colour*, its glyph white."""
    img, p = _image(size, size, clear=True)
    r = QRectF(size * 0.03, size * 0.03, size * 0.94, size * 0.94)
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0, QColor(mix(colour, "#ffffff", 0.18)))
    g.setColorAt(1, QColor(mix(colour, "#000000", 0.12)))
    back = QPainterPath()
    back.addRoundedRect(r, size * 0.24, size * 0.24)
    p.fillPath(back, QBrush(g))
    shine = QPainterPath()
    shine.addEllipse(QPointF(size * 0.25, size * 0.12), size * 0.55, size * 0.32)
    p.fillPath(shine.intersected(back), _c("#ffffff", 22))
    gl = _glyph(glyph, size * 0.94)
    p.translate(size * 0.03, size * 0.03)
    p.fillPath(gl.translated(0, size * 0.012), _c("#000000", 40))
    p.fillPath(gl, QColor("#ffffff"))
    p.end()
    return img


# -- scenery: the motifs of posters, backdrops and stills -----------------------------

def _sun(p: QPainter, x: float, y: float, r: float, col: str, glow: bool = True) -> None:
    if glow:
        g = QRadialGradient(QPointF(x, y), r * 2.6)
        g.setColorAt(0, _c(col, 110))
        g.setColorAt(1, _c(col, 0))
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(x, y), r * 2.6, r * 2.6)
    p.setBrush(QColor(col))
    p.drawEllipse(QPointF(x, y), r, r)


def _mountains(p: QPainter, w: float, base: float, peaks: list[tuple[float, float]],
               col: str, snow: str | None = None) -> None:
    pts = [(0.0, base)] + [(x * w, y) for x, y in peaks] + [(w, base)]
    path = QPainterPath()
    path.addPolygon(_poly(pts + [(w, base + 4000), (0.0, base + 4000)]))
    p.setBrush(QColor(col))
    p.drawPath(path)
    if snow:
        p.setBrush(QColor(snow))
        for i in range(1, len(pts) - 1):
            x, y = pts[i]
            if y > base - (base * 0.25):
                continue
            px, py = pts[i - 1]
            nx, ny = pts[i + 1]
            d = (base - y) * 0.22
            lx = x + (px - x) * d / max(1.0, abs(py - y))
            rx = x + (nx - x) * d / max(1.0, abs(ny - y))
            cap = _poly([(x, y), (rx, y + d), (x + (rx - x) * 0.4, y + d * 0.75),
                         (x, y + d), (x - (x - lx) * 0.4, y + d * 0.75), (lx, y + d)])
            p.drawPolygon(cap)


def _hills(p: QPainter, w: float, h: float, y: float, amp: float, col: str,
           phase: float = 0.0, waves: float = 1.5) -> None:
    path = QPainterPath()
    path.moveTo(0, h)
    steps = 48
    for i in range(steps + 1):
        x = w * i / steps
        path.lineTo(x, y - amp * math.sin(phase + waves * math.pi * i / steps))
    path.lineTo(w, h)
    path.closeSubpath()
    p.setBrush(QColor(col))
    p.drawPath(path)


def _pine(p: QPainter, x: float, base: float, hgt: float, col: str) -> None:
    w = hgt * 0.42
    path = QPainterPath()
    for k in range(3):
        top = base - hgt + k * hgt * 0.22
        bot = base - hgt * 0.62 + k * hgt * 0.26
        ww = w * (0.55 + 0.25 * k)
        path.addPolygon(_poly([(x, top), (x + ww / 2, bot), (x - ww / 2, bot)]))
    path.addRect(QRectF(x - hgt * 0.035, base - hgt * 0.12, hgt * 0.07, hgt * 0.12))
    path.setFillRule(Qt.FillRule.WindingFill)
    p.setBrush(QColor(col))
    p.drawPath(path.simplified())


def _round_tree(p: QPainter, x: float, base: float, hgt: float, col: str, trunk: str) -> None:
    p.setBrush(QColor(trunk))
    p.drawRoundedRect(QRectF(x - hgt * 0.05, base - hgt * 0.45, hgt * 0.1, hgt * 0.45),
                      hgt * 0.03, hgt * 0.03)
    p.setBrush(QColor(col))
    p.drawEllipse(QPointF(x, base - hgt * 0.62), hgt * 0.3, hgt * 0.36)


def _waves(p: QPainter, w: float, h: float, y: float, cols: list[str], amp: float) -> None:
    for k, col in enumerate(cols):
        yy = y + k * (h - y) / max(1, len(cols))
        path = QPainterPath()
        path.moveTo(0, h)
        steps = 60
        for i in range(steps + 1):
            x = w * i / steps
            path.lineTo(x, yy + amp * math.sin(k * 1.7 + 4 * math.pi * i / steps))
        path.lineTo(w, h)
        path.closeSubpath()
        p.setBrush(QColor(col))
        p.drawPath(path)


def _city(p: QPainter, w: float, base: float, hgt: float, col: str, lit: str,
          seed: int, density: float = 0.35, windows: bool = True) -> None:
    rnd = random.Random(seed)
    x = -w * 0.02
    while x < w:
        bw = w * rnd.uniform(0.05, 0.11)
        bh = hgt * rnd.uniform(0.35, 1.0)
        top = base - bh
        p.setBrush(QColor(col))
        p.drawRect(QRectF(x, top, bw - w * 0.006, bh + 2))
        if rnd.random() < 0.3:
            p.drawRect(QRectF(x + bw * 0.42, top - hgt * 0.08, bw * 0.06, hgt * 0.08))
        if windows:
            p.setBrush(QColor(lit))
            ww = bw / 7
            cy = top + ww * 1.2
            while cy < base - ww * 1.5:
                for i in range(3):
                    if rnd.random() < density:
                        p.drawRect(QRectF(x + ww * (1 + 2 * i) - w * 0.003, cy, ww, ww * 1.1))
                cy += ww * 2.2
        x += bw
    p.setBrush(Qt.BrushStyle.NoBrush)


def _lighthouse(p: QPainter, x: float, base: float, hgt: float, body: str, stripe: str,
                beam: bool = True) -> None:
    w0, w1 = hgt * 0.16, hgt * 0.1
    tower = _poly([(x - w0 / 2, base), (x - w1 / 2, base - hgt * 0.78),
                   (x + w1 / 2, base - hgt * 0.78), (x + w0 / 2, base)])
    p.setBrush(QColor(body))
    p.drawPolygon(tower)
    clip = QPainterPath()
    clip.addPolygon(tower)
    p.save()
    p.setClipPath(clip)
    p.setBrush(QColor(stripe))
    for k in range(3):
        y = base - hgt * (0.2 + 0.22 * k)
        p.drawRect(QRectF(x - w0, y, w0 * 2, hgt * 0.1))
    p.restore()
    p.setBrush(QColor(stripe))
    p.drawRect(QRectF(x - w1 * 0.7, base - hgt * 0.84, w1 * 1.4, hgt * 0.06))
    p.setBrush(QColor(SUN))
    p.drawRect(QRectF(x - w1 * 0.4, base - hgt * 0.95, w1 * 0.8, hgt * 0.11))
    p.setBrush(QColor(stripe))
    p.drawPolygon(_poly([(x - w1 * 0.6, base - hgt * 0.95), (x, base - hgt * 1.04),
                         (x + w1 * 0.6, base - hgt * 0.95)]))
    if beam:
        g = QLinearGradient(QPointF(x, 0), QPointF(x + hgt * 1.6, 0))
        g.setColorAt(0, _c(SUN, 120))
        g.setColorAt(1, _c(SUN, 0))
        p.setBrush(QBrush(g))
        y = base - hgt * 0.9
        p.drawPolygon(_poly([(x, y - hgt * 0.02), (x + hgt * 1.6, y - hgt * 0.22),
                             (x + hgt * 1.6, y + hgt * 0.14), (x, y + hgt * 0.04)]))


def _cloud(p: QPainter, x: float, y: float, s: float, col: str, alpha: int = 255) -> None:
    p.setBrush(_c(col, alpha))
    path = QPainterPath()
    path.addRoundedRect(QRectF(x - s, y, s * 2, s * 0.5), s * 0.25, s * 0.25)
    path = path.united(_ellipse_path(x - s * 0.35, y + s * 0.05, s * 0.42, s * 0.38))
    path = path.united(_ellipse_path(x + s * 0.3, y - s * 0.05, s * 0.5, s * 0.48))
    p.drawPath(path)


def _stars(p: QPainter, w: float, h: float, n: int, seed: int, alpha: int = 200) -> None:
    rnd = random.Random(seed)
    for _ in range(n):
        r = rnd.uniform(0.0012, 0.0032) * w
        p.setBrush(_c("#ffffff", rnd.randint(alpha // 3, alpha)))
        p.drawEllipse(QPointF(rnd.uniform(0, w), rnd.uniform(0, h)), r, r)


#: The motifs posters, backdrops and stills are drawn from: name ->
#: (sky stops, motif). Each motif is drawn in a box w x h.
MOTIFS = ("sunset", "peaks", "sea", "lighthouse", "city", "moon", "forest",
          "desert", "balloons", "aurora", "island", "kites", "harbour", "orchard",
          "snow", "robots", "diner", "station")


def scenery(motif: str, w: int, h: int, variant: int = 0) -> QImage:
    """One of MOTIFS as a flat picture *w* x *h* (*variant* moves the light
    and the details a little, for a series' stills)."""
    img, p = _image(w, h)
    r = QRectF(0, 0, w, h)
    v = variant
    rnd = random.Random(hash((motif, v)) & 0xFFFF)
    if motif == "sunset":
        _vgrad(p, r, [(0, PLUM), (0.45, BERRY), (0.75, ORANGE), (1, SUN)])
        _sun(p, w * (0.5 + 0.08 * math.sin(v)), h * (0.58 + 0.03 * v % 0.1), h * 0.16, CREAM)
        _hills(p, w, h, h * 0.7, h * 0.05, mix(PLUM, INK, 0.2), 0.4 + v, 2.2)
        _hills(p, w, h, h * 0.8, h * 0.04, mix(PLUM, INK, 0.5), 2 + v, 1.6)
        _waves(p, w, h, h * 0.88, [mix(INK, PLUM, 0.4), INK], h * 0.01)
    elif motif == "peaks":
        _vgrad(p, r, [(0, mix(SKY, CREAM, 0.1)), (0.7, mix(SKY, CREAM, 0.65)), (1, CREAM)])
        _sun(p, w * (0.72 - 0.1 * (v % 3)), h * 0.24, h * 0.08, SUN)
        _cloud(p, w * 0.25, h * 0.2, w * 0.08, "#ffffff", 220)
        _mountains(p, w, h * 0.78, [(0.12, h * 0.42), (0.3, h * 0.6), (0.5, h * 0.3),
                                    (0.7, h * 0.55), (0.88, h * 0.38)], mix(VIOLET, SKY, 0.4), CREAM)
        _mountains(p, w, h * 0.86, [(0.2, h * 0.6), (0.45, h * 0.72), (0.66, h * 0.58),
                                    (0.95, h * 0.7)], mix(DEEP, VIOLET, 0.3))
        _hills(p, w, h, h * 0.88, h * 0.025, LEAF, v, 2)
        for i in range(7):
            _pine(p, w * (0.05 + 0.14 * i + 0.03 * (i % 2)), h * (0.98 - 0.02 * (i % 3)),
                  h * (0.16 + 0.04 * (i % 2)), mix(LEAF, INK, 0.45))
    elif motif == "sea":
        _vgrad(p, r, [(0, mix(SKY, "#ffffff", 0.25)), (0.6, mix(TEAL, "#ffffff", 0.5)), (1, TEAL)])
        _sun(p, w * 0.28, h * 0.3, h * 0.09, SUN)
        _cloud(p, w * 0.7, h * 0.18, w * 0.09, "#ffffff", 230)
        _waves(p, w, h, h * 0.58, [mix(TEAL, SKY, 0.3), TEAL, mix(TEAL, DEEP, 0.4), DEEP], h * 0.025)
        _boat(p, w * (0.6 + 0.05 * v), h * 0.6, h * 0.16)
    elif motif == "lighthouse":
        _vgrad(p, r, [(0, NIGHT), (0.6, mix(DEEP, VIOLET, 0.3)), (1, mix(DEEP, TEAL, 0.4))])
        _stars(p, w, h * 0.6, 60, 7 + v)
        p.setBrush(QColor(mix(INK, NIGHT, 0.4)))
        p.drawPolygon(_poly([(0, h * 0.82), (w * 0.12, h * 0.7), (w * 0.38, h * 0.68),
                             (w * 0.5, h * 0.8), (w * 0.5, h), (0, h)]))
        _lighthouse(p, w * 0.24, h * 0.71, h * 0.5, CREAM, CORAL)
        _waves(p, w, h, h * 0.8, [mix(DEEP, TEAL, 0.25), DEEP, mix(DEEP, NIGHT, 0.5)], h * 0.015)
    elif motif == "city":
        _vgrad(p, r, [(0, NIGHT), (0.7, mix(PLUM, NIGHT, 0.3)), (1, BERRY)])
        _stars(p, w, h * 0.5, 50, 3 + v)
        p.setBrush(QColor(CREAM))
        p.drawEllipse(QPointF(w * 0.8, h * 0.2), h * 0.07, h * 0.07)
        _city(p, w, h * 0.86, h * 0.55, mix(PLUM, NIGHT, 0.55), SUN, 11 + v, 0.25, True)
        _city(p, w, h * 0.92, h * 0.4, NIGHT, ORANGE, 23 + v, 0.35, True)
        p.setBrush(QColor(INK))
        p.drawRect(QRectF(0, h * 0.92, w, h * 0.08))
    elif motif == "moon":
        _vgrad(p, r, [(0, NIGHT), (1, mix(VIOLET, NIGHT, 0.4))])
        _stars(p, w, h, 90, 5 + v)
        p.setBrush(QColor(CREAM))
        moon = _ellipse_path(w * 0.6, h * 0.35, h * 0.16, h * 0.16)
        bite = _ellipse_path(w * 0.6 + h * 0.08, h * 0.32, h * 0.15, h * 0.15)
        p.drawPath(moon.subtracted(bite))
        _hills(p, w, h, h * 0.78, h * 0.06, mix(VIOLET, INK, 0.6), 1 + v, 1.4)
        _hills(p, w, h, h * 0.88, h * 0.04, INK, 3, 2)
    elif motif == "forest":
        _vgrad(p, r, [(0, mix(MINT, CREAM, 0.5)), (1, mix(LEAF, CREAM, 0.3))])
        _sun(p, w * 0.7, h * 0.25, h * 0.1, CREAM)
        for k, col in enumerate((mix(LEAF, CREAM, 0.4), mix(LEAF, CREAM, 0.15), LEAF,
                                 mix(LEAF, INK, 0.45))):
            base = h * (0.62 + 0.12 * k)
            for i in range(9):
                x = w * (i / 8.0) + (k * 37 + i * 13) % 40 - 20 + 6 * v
                _pine(p, x, base, h * (0.28 + 0.05 * k + 0.04 * ((i + k) % 3)), col)
    elif motif == "desert":
        _vgrad(p, r, [(0, ORANGE), (0.55, mix(SUN, CREAM, 0.3)), (1, CREAM)])
        _sun(p, w * 0.5, h * 0.42, h * 0.15, CREAM)
        _hills(p, w, h, h * 0.66, h * 0.07, mix(CORAL, ORANGE, 0.4), 0.5 + v, 1.3)
        _hills(p, w, h, h * 0.78, h * 0.05, CORAL, 2.1, 1.1)
        _hills(p, w, h, h * 0.9, h * 0.03, mix(CORAL, PLUM, 0.4), 4, 1.6)
        _cactus(p, w * 0.22, h * 0.92, h * 0.28, mix(LEAF, INK, 0.35))
    elif motif == "balloons":
        _vgrad(p, r, [(0, mix(SKY, "#ffffff", 0.2)), (1, mix(SUN, CREAM, 0.6))])
        _cloud(p, w * 0.2, h * 0.65, w * 0.12, "#ffffff", 230)
        _cloud(p, w * 0.82, h * 0.3, w * 0.1, "#ffffff", 210)
        for (bx, by, bs, col) in ((0.32, 0.33, 0.13, CORAL), (0.66, 0.5, 0.09, SUN),
                                  (0.82, 0.68, 0.06, VIOLET), (0.15, 0.2, 0.05, MINT)):
            _balloon(p, w * bx, h * (by + 0.02 * v), h * bs, col)
        _hills(p, w, h, h * 0.9, h * 0.03, LEAF, 1, 2)
    elif motif == "aurora":
        _vgrad(p, r, [(0, NIGHT), (1, mix(DEEP, NIGHT, 0.3))])
        _stars(p, w, h * 0.7, 80, 9 + v)
        for k, col in enumerate((MINT, TEAL, VIOLET)):
            path = QPainterPath()
            path.moveTo(0, h * (0.35 + 0.08 * k))
            for i in range(41):
                x = w * i / 40
                path.lineTo(x, h * (0.3 + 0.08 * k) + h * 0.08 * math.sin(i / 6 + k + v))
            for i in range(40, -1, -1):
                x = w * i / 40
                path.lineTo(x, h * (0.12 + 0.06 * k) + h * 0.1 * math.sin(i / 7 + k * 2 + v))
            g = QLinearGradient(QPointF(0, h * 0.1), QPointF(0, h * 0.5))
            g.setColorAt(0, _c(col, 0))
            g.setColorAt(1, _c(col, 150))
            p.setBrush(QBrush(g))
            p.drawPath(path)
        _mountains(p, w, h * 0.9, [(0.15, h * 0.68), (0.35, h * 0.78), (0.6, h * 0.62),
                                   (0.85, h * 0.74)], INK, mix(SKY, CREAM, 0.6))
    elif motif == "island":
        _vgrad(p, r, [(0, mix(SKY, TEAL, 0.3)), (0.55, mix(TEAL, "#ffffff", 0.55)), (1, TEAL)])
        _sun(p, w * 0.78, h * 0.22, h * 0.08, SUN)
        p.setBrush(QColor(mix(SUN, CREAM, 0.5)))
        p.drawEllipse(QPointF(w * 0.42, h * 0.66), w * 0.22, h * 0.07)
        _palm(p, w * 0.4, h * 0.63, h * 0.34)
        _waves(p, w, h, h * 0.68, [mix(TEAL, SKY, 0.2), TEAL, mix(TEAL, DEEP, 0.4)], h * 0.015)
    elif motif == "kites":
        _vgrad(p, r, [(0, mix(SKY, "#ffffff", 0.1)), (1, mix(MINT, CREAM, 0.6))])
        _cloud(p, w * 0.7, h * 0.18, w * 0.09, "#ffffff", 220)
        for (kx, ky, ks, col, rot) in ((0.3, 0.28, 0.12, CORAL, -12), (0.62, 0.42, 0.09, VIOLET, 10),
                                       (0.82, 0.2, 0.06, SUN, -5)):
            _kite(p, w * kx, h * (ky + 0.02 * v), h * ks, col, rot)
        _hills(p, w, h, h * 0.8, h * 0.05, MINT, 0.7, 1.4)
        _hills(p, w, h, h * 0.9, h * 0.04, LEAF, 2.3, 1.8)
    elif motif == "harbour":
        _vgrad(p, r, [(0, mix(ORANGE, CREAM, 0.4)), (0.6, mix(CORAL, CREAM, 0.5)), (1, TEAL)])
        _sun(p, w * 0.3, h * 0.42, h * 0.1, CREAM)
        cols = (CORAL, SUN, MINT, SKY, BERRY, ORANGE, TEAL)
        x = w * 0.45
        i = 0
        while x < w:
            bw = w * (0.06 + 0.015 * (i % 3))
            bh = h * (0.18 + 0.05 * ((i + v) % 3))
            p.setBrush(QColor(cols[i % len(cols)]))
            p.drawRect(QRectF(x, h * 0.66 - bh, bw, bh))
            p.drawPolygon(_poly([(x, h * 0.66 - bh), (x + bw / 2, h * 0.66 - bh - bw * 0.45),
                                 (x + bw, h * 0.66 - bh)]))
            p.setBrush(_c(CREAM, 220))
            p.drawRect(QRectF(x + bw * 0.3, h * 0.66 - bh * 0.6, bw * 0.4, bh * 0.25))
            x += bw
            i += 1
        _waves(p, w, h, h * 0.66, [mix(TEAL, DEEP, 0.2), mix(TEAL, DEEP, 0.5)], h * 0.01)
        _boat(p, w * 0.25, h * 0.7, h * 0.12)
    elif motif == "orchard":
        _vgrad(p, r, [(0, mix(SUN, CREAM, 0.5)), (1, mix(MINT, CREAM, 0.4))])
        _sun(p, w * 0.2, h * 0.22, h * 0.08, ORANGE)
        _hills(p, w, h, h * 0.72, h * 0.05, mix(LEAF, CREAM, 0.3), v, 1.3)
        for i in range(6):
            x = w * (0.1 + 0.16 * i)
            _round_tree(p, x, h * (0.88 + 0.02 * (i % 2)), h * 0.42, mix(LEAF, MINT, 0.3 * (i % 2)),
                        mix(CORAL, INK, 0.5))
            p.setBrush(QColor(CORAL))
            for k in range(4):
                p.drawEllipse(QPointF(x + h * 0.1 * math.cos(k * 1.7 + i),
                                      h * (0.62 + 0.05 * math.sin(k * 2.1 + i))), h * 0.018, h * 0.018)
        _hills(p, w, h, h * 0.93, h * 0.02, LEAF, 1, 2)
    elif motif == "snow":
        _vgrad(p, r, [(0, mix(SKY, NIGHT, 0.35)), (1, mix(SKY, "#ffffff", 0.55))])
        _mountains(p, w, h * 0.8, [(0.2, h * 0.4), (0.45, h * 0.55), (0.7, h * 0.32), (0.95, h * 0.5)],
                   mix(SKY, "#ffffff", 0.75), "#ffffff")
        _hills(p, w, h, h * 0.84, h * 0.03, "#ffffff", 1 + v, 1.5)
        for i in range(5):
            _pine(p, w * (0.1 + 0.2 * i), h * 0.9, h * 0.22, mix(DEEP, LEAF, 0.4))
        p.setBrush(_c("#ffffff", 220))
        for _ in range(70):
            rr = rnd.uniform(0.002, 0.005) * w
            p.drawEllipse(QPointF(rnd.uniform(0, w), rnd.uniform(0, h)), rr, rr)
    elif motif == "robots":
        _vgrad(p, r, [(0, mix(VIOLET, SKY, 0.3)), (1, mix(SKY, MINT, 0.5))])
        _cloud(p, w * 0.2, h * 0.18, w * 0.07, "#ffffff", 200)
        _hills(p, w, h, h * 0.82, h * 0.03, mix(MINT, LEAF, 0.4), v, 1.5)
        _robot(p, w * 0.38, h * 0.82, h * 0.42, CORAL)
        _robot(p, w * 0.64, h * 0.84, h * 0.3, SUN)
    elif motif == "diner":
        _vgrad(p, r, [(0, NIGHT), (1, mix(PLUM, NIGHT, 0.4))])
        _stars(p, w, h * 0.4, 30, 4 + v)
        p.setBrush(QColor(mix(TEAL, DEEP, 0.3)))
        p.drawRoundedRect(QRectF(w * 0.15, h * 0.45, w * 0.7, h * 0.35), h * 0.05, h * 0.05)
        p.setBrush(QColor(SUN))
        for i in range(5):
            p.drawRoundedRect(QRectF(w * (0.19 + 0.125 * i), h * 0.52, w * 0.1, h * 0.14),
                              h * 0.015, h * 0.015)
        p.setBrush(QColor(CORAL))
        p.drawRoundedRect(QRectF(w * 0.35, h * 0.33, w * 0.3, h * 0.1), h * 0.05, h * 0.05)
        p.setBrush(QColor(INK))
        p.drawRect(QRectF(0, h * 0.8, w, h * 0.2))
        p.setBrush(_c(SUN, 60))
        p.drawRect(QRectF(w * 0.15, h * 0.8, w * 0.7, h * 0.05))
    elif motif == "station":
        _vgrad(p, r, [(0, NIGHT), (1, mix(DEEP, NIGHT, 0.2))])
        _stars(p, w, h, 110, 13 + v)
        p.setBrush(QColor(SKY))
        p.drawEllipse(QPointF(w * 0.15, h * 1.15), h * 0.7, h * 0.7)
        p.setBrush(QColor(mix(MINT, SKY, 0.4)))
        p.drawEllipse(QPointF(w * 0.05, h * 1.05), h * 0.3, h * 0.18)
        cx, cy = w * (0.6 + 0.03 * v), h * 0.4
        p.setBrush(QColor(CREAM))
        p.drawRoundedRect(QRectF(cx - w * 0.06, cy - h * 0.05, w * 0.12, h * 0.1), h * 0.02, h * 0.02)
        p.setBrush(QColor(SKY))
        for dx in (-1, 1):
            p.drawRect(QRectF(cx + dx * w * 0.07 - (w * 0.14 if dx < 0 else 0), cy - h * 0.035,
                              w * 0.14, h * 0.07))
        p.setBrush(QColor(CORAL))
        p.drawEllipse(QPointF(cx, cy), h * 0.025, h * 0.025)
    else:
        _vgrad(p, r, [(0, SKY), (1, VIOLET)])
    p.end()
    return img


def _boat(p: QPainter, x: float, y: float, s: float) -> None:
    p.setBrush(QColor(CORAL))
    p.drawPolygon(_poly([(x - s * 0.5, y), (x + s * 0.5, y), (x + s * 0.35, y + s * 0.2),
                         (x - s * 0.35, y + s * 0.2)]))
    p.setBrush(QColor(CREAM))
    p.drawPolygon(_poly([(x, y - s * 0.8), (x, y - s * 0.05), (x + s * 0.4, y - s * 0.05)]))
    p.setBrush(QColor(SUN))
    p.drawPolygon(_poly([(x - s * 0.04, y - s * 0.65), (x - s * 0.04, y - s * 0.05),
                         (x - s * 0.34, y - s * 0.05)]))


def _cactus(p: QPainter, x: float, base: float, hgt: float, col: str) -> None:
    p.setBrush(QColor(col))
    w = hgt * 0.16
    p.drawRoundedRect(QRectF(x - w / 2, base - hgt, w, hgt), w / 2, w / 2)
    p.drawRoundedRect(QRectF(x - w * 1.6, base - hgt * 0.7, w * 0.8, hgt * 0.35), w * 0.4, w * 0.4)
    p.drawRect(QRectF(x - w * 1.2, base - hgt * 0.45, w, w * 0.6))
    p.drawRoundedRect(QRectF(x + w * 0.8, base - hgt * 0.85, w * 0.8, hgt * 0.4), w * 0.4, w * 0.4)
    p.drawRect(QRectF(x + w * 0.3, base - hgt * 0.55, w, w * 0.6))


def _balloon(p: QPainter, x: float, y: float, s: float, col: str) -> None:
    p.setBrush(QColor(col))
    path = QPainterPath()
    path.moveTo(x, y + s * 1.05)
    path.cubicTo(x - s * 1.25, y + s * 0.2, x - s * 0.9, y - s * 1.0, x, y - s * 1.0)
    path.cubicTo(x + s * 0.9, y - s * 1.0, x + s * 1.25, y + s * 0.2, x, y + s * 1.05)
    p.drawPath(path)
    p.setBrush(_c("#ffffff", 70))
    p.drawEllipse(QPointF(x, y - s * 0.1), s * 0.3, s * 0.85)
    p.setPen(QPen(_c(INK, 160), max(1.0, s * 0.03)))
    p.drawLine(QPointF(x - s * 0.25, y + s * 1.0), QPointF(x - s * 0.18, y + s * 1.35))
    p.drawLine(QPointF(x + s * 0.25, y + s * 1.0), QPointF(x + s * 0.18, y + s * 1.35))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(mix(ORANGE, INK, 0.3)))
    p.drawRoundedRect(QRectF(x - s * 0.2, y + s * 1.33, s * 0.4, s * 0.3), s * 0.05, s * 0.05)


def _palm(p: QPainter, x: float, base: float, hgt: float) -> None:
    trunk = QPainterPath()
    trunk.moveTo(x - hgt * 0.03, base)
    trunk.quadTo(x - hgt * 0.02, base - hgt * 0.5, x + hgt * 0.12, base - hgt * 0.95)
    trunk.lineTo(x + hgt * 0.16, base - hgt * 0.93)
    trunk.quadTo(x + hgt * 0.04, base - hgt * 0.5, x + hgt * 0.04, base)
    p.setBrush(QColor(mix(ORANGE, INK, 0.45)))
    p.drawPath(trunk)
    p.setBrush(QColor(LEAF))
    tx, ty = x + hgt * 0.14, base - hgt * 0.95
    for ang in (-160, -120, -60, -20, 20, 200):
        a = math.radians(ang)
        leaf = QPainterPath()
        leaf.moveTo(tx, ty)
        ex, ey = tx + math.cos(a) * hgt * 0.42, ty + math.sin(a) * hgt * 0.25 + hgt * 0.12
        leaf.quadTo(tx + math.cos(a) * hgt * 0.2, ty - hgt * 0.12, ex, ey)
        leaf.quadTo(tx + math.cos(a) * hgt * 0.22, ty + hgt * 0.02, tx, ty)
        p.drawPath(leaf)


def _kite(p: QPainter, x: float, y: float, s: float, col: str, rot: float) -> None:
    p.save()
    p.translate(x, y)
    p.rotate(rot)
    p.setBrush(QColor(col))
    p.drawPolygon(_poly([(0, -s), (s * 0.7, -s * 0.1), (0, s * 1.2), (-s * 0.7, -s * 0.1)]))
    p.setBrush(_c("#ffffff", 80))
    p.drawPolygon(_poly([(0, -s), (s * 0.7, -s * 0.1), (0, -s * 0.1)]))
    p.setPen(QPen(_c(INK, 150), max(1.0, s * 0.04)))
    path = QPainterPath()
    path.moveTo(0, s * 1.2)
    path.cubicTo(s * 0.4, s * 1.8, -s * 0.4, s * 2.4, s * 0.2, s * 3.0)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.restore()


def _robot(p: QPainter, x: float, base: float, hgt: float, col: str) -> None:
    w = hgt * 0.55
    p.setBrush(QColor(mix(col, INK, 0.3)))
    p.drawRoundedRect(QRectF(x - w * 0.35, base - hgt * 0.18, w * 0.22, hgt * 0.18), w * 0.05, w * 0.05)
    p.drawRoundedRect(QRectF(x + w * 0.13, base - hgt * 0.18, w * 0.22, hgt * 0.18), w * 0.05, w * 0.05)
    p.setBrush(QColor(col))
    p.drawRoundedRect(QRectF(x - w / 2, base - hgt * 0.55, w, hgt * 0.4), w * 0.15, w * 0.15)
    p.drawRoundedRect(QRectF(x - w * 0.42, base - hgt * 0.95, w * 0.84, hgt * 0.36), w * 0.18, w * 0.18)
    p.setBrush(QColor(CREAM))
    p.drawRoundedRect(QRectF(x - w * 0.32, base - hgt * 0.88, w * 0.64, hgt * 0.22), w * 0.1, w * 0.1)
    p.setBrush(QColor(INK))
    for dx in (-0.14, 0.14):
        p.drawEllipse(QPointF(x + w * dx, base - hgt * 0.78), w * 0.06, w * 0.06)
    p.setPen(QPen(QColor(mix(col, INK, 0.3)), max(1.0, w * 0.05), cap=Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(x, base - hgt * 0.95), QPointF(x, base - hgt * 1.05))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(SUN if col != SUN else CORAL))
    p.drawEllipse(QPointF(x, base - hgt * 1.07), w * 0.06, w * 0.06)
    p.setBrush(_c("#ffffff", 120))
    p.drawEllipse(QPointF(x, base - hgt * 0.36), w * 0.1, w * 0.1)


# -- posters ---------------------------------------------------------------------------

def _wrap(text: str, font: QFont, width: float) -> list[str]:
    fm = QFontMetricsF(font)
    words, lines, line = text.split(), [], ""
    for wd in words:
        trial = f"{line} {wd}".strip()
        if fm.horizontalAdvance(trial) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = wd
    if line:
        lines.append(line)
    return lines


def poster(title: str, motif: str, w: int = 400, h: int = 600, variant: int = 0,
           top: bool | None = None) -> QImage:
    """A 2:3 poster: *motif*'s picture, *title* large in bold type over a
    soft shade - at the top for a picture whose weight is low, else below."""
    art = scenery(motif, round(h * 0.82), h, variant)
    img, p = _image(w, h)
    p.drawImage(QRectF(0, 0, w, h), art,
                QRectF((art.width() - art.height() * w / h) / 2, 0, art.height() * w / h, art.height()))
    at_top = bool(top)
    shade = QLinearGradient(QPointF(0, 0 if at_top else h), QPointF(0, h * 0.45))
    shade.setColorAt(0, _c(INK, 175))
    shade.setColorAt(1, _c(INK, 0))
    p.fillRect(QRectF(0, 0, w, h), QBrush(shade))
    px = w * 0.155
    font = _font(px)
    lines = _wrap(title.upper(), font, w * 0.84)
    while len(lines) > 3 or any(QFontMetricsF(font).horizontalAdvance(ln) > w * 0.86 for ln in lines):
        px *= 0.92
        font = _font(px)
        lines = _wrap(title.upper(), font, w * 0.84)
    fm = QFontMetricsF(font)
    lh = fm.height() * 0.92
    y0 = h * 0.07 if at_top else h * 0.88 - lh * len(lines)
    p.setFont(font)
    for i, ln in enumerate(lines):
        box = QRectF(0, y0 + i * lh, w, lh)
        p.setPen(_c("#000000", 70))
        p.drawText(box.translated(0, px * 0.05), Qt.AlignmentFlag.AlignHCenter
                   | Qt.AlignmentFlag.AlignVCenter, ln)
        p.setPen(QColor("#ffffff"))
        p.drawText(box, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, ln)
    p.setPen(Qt.PenStyle.NoPen)
    bar_y = y0 + len(lines) * lh + h * 0.012 if at_top else y0 - h * 0.03
    p.setBrush(QColor(SUN))
    p.drawRoundedRect(QRectF(w / 2 - w * 0.07, bar_y, w * 0.14, h * 0.009), h * 0.005, h * 0.005)
    p.end()
    return img


# -- what plays: the player's pictures --------------------------------------------------

PLAYER_SCENES = ("studio", "pitch", "court", "lake", "reef", "skyline", "woods",
                 "cartoon", "stage")


def player_scene(kind: str, w: int = 1920, h: int = 1080) -> QImage:
    """A 16:9 picture for the player, in the posters' style: what a channel
    of that kind would show."""
    if kind in MOTIFS:
        return scenery(kind, w, h)
    img, p = _image(w, h)
    r = QRectF(0, 0, w, h)
    if kind == "studio":
        _vgrad(p, r, [(0, mix(DEEP, NIGHT, 0.3)), (1, NIGHT)])
        screen = QRectF(w * 0.08, h * 0.1, w * 0.84, h * 0.56)
        p.setBrush(QColor(mix(SKY, DEEP, 0.35)))
        p.drawRoundedRect(screen, h * 0.02, h * 0.02)
        p.save()
        clip = QPainterPath()
        clip.addRoundedRect(screen, h * 0.02, h * 0.02)
        p.setClipPath(clip)
        cx, cy, rr = w * 0.5, h * 0.38, h * 0.36
        p.setBrush(QColor(mix(SKY, DEEP, 0.15)))
        p.drawEllipse(QPointF(cx, cy), rr, rr)
        p.setBrush(QColor(mix(MINT, SKY, 0.35)))
        for (dx, dy, rx, ry) in ((-0.45, -0.25, 0.32, 0.22), (-0.2, 0.25, 0.18, 0.3),
                                 (0.25, -0.3, 0.3, 0.18), (0.35, 0.15, 0.2, 0.25),
                                 (0.05, 0.05, 0.12, 0.1)):
            p.drawEllipse(QPointF(cx + dx * rr, cy + dy * rr), rx * rr, ry * rr)
        p.setPen(QPen(_c("#ffffff", 50), h * 0.004))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k in (0.35, 0.7):
            p.drawEllipse(QPointF(cx, cy), rr * k, rr)
        p.drawLine(QPointF(cx - rr, cy), QPointF(cx + rr, cy))
        p.setPen(Qt.PenStyle.NoPen)
        p.restore()
        for i in range(2):
            x = w * (0.36 + 0.28 * i)
            p.setBrush(QColor((CORAL, VIOLET)[i]))
            p.drawRoundedRect(QRectF(x - w * 0.075, h * 0.58, w * 0.15, h * 0.3), w * 0.05, w * 0.05)
            p.setBrush(QColor(mix(ORANGE, CREAM, 0.5) if i == 0 else mix(CORAL, INK, 0.25)))
            p.drawEllipse(QPointF(x, h * 0.52), h * 0.075, h * 0.085)
            p.setBrush(QColor((INK, mix(INK, PLUM, 0.4))[i]))
            hair = _ellipse_path(x, h * 0.455, h * 0.08, h * 0.04)
            p.drawPath(hair)
            p.setBrush(QColor(INK))
            for dx in (-0.025, 0.025):
                p.drawEllipse(QPointF(x + h * dx, h * 0.52), h * 0.009, h * 0.011)
            p.setPen(QPen(QColor(INK), h * 0.006, cap=Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(x - h * 0.025, h * 0.53, h * 0.05, h * 0.03), 200 * 16, 140 * 16)
            p.setPen(Qt.PenStyle.NoPen)
        desk = QPainterPath()
        desk.moveTo(w * 0.12, h * 0.74)
        desk.quadTo(w * 0.5, h * 0.68, w * 0.88, h * 0.74)
        desk.lineTo(w * 0.84, h)
        desk.lineTo(w * 0.16, h)
        desk.closeSubpath()
        p.setBrush(QColor(mix(SKY, "#ffffff", 0.85)))
        p.drawPath(desk)
        p.setBrush(QColor(SKY))
        p.drawRect(QRectF(w * 0.14, h * 0.8, w * 0.72, h * 0.025))
        p.setBrush(QColor(CORAL))
        p.drawRect(QRectF(0, h * 0.885, w * 0.24, h * 0.07))
        p.setBrush(_c(CREAM, 235))
        p.drawRect(QRectF(w * 0.24, h * 0.885, w * 0.76, h * 0.07))
        p.setBrush(_c(INK, 120))
        for k in range(4):
            p.drawRoundedRect(QRectF(w * (0.28 + 0.17 * k), h * 0.91, w * 0.12, h * 0.018),
                              h * 0.009, h * 0.009)
        p.setPen(QColor("#ffffff"))
        p.setFont(_font(h * 0.04))
        p.drawText(QRectF(0, h * 0.885, w * 0.24, h * 0.07), Qt.AlignmentFlag.AlignCenter, "LIVE")
    elif kind == "pitch":
        p.fillRect(r, QColor(mix(LEAF, MINT, 0.25)))
        for i in range(12):
            if i % 2:
                p.fillRect(QRectF(w * i / 12, 0, w / 12, h), QColor(mix(LEAF, MINT, 0.38)))
        m = h * 0.07
        f = QRectF(m * 1.4, m, w - 2.8 * m, h - 2 * m)
        p.setPen(QPen(_c("#ffffff", 220), h * 0.006))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(f)
        p.drawLine(QPointF(w / 2, f.top()), QPointF(w / 2, f.bottom()))
        p.drawEllipse(QPointF(w / 2, h / 2), h * 0.14, h * 0.14)
        for side in (0, 1):
            bx = f.left() if side == 0 else f.right() - w * 0.13
            p.drawRect(QRectF(bx, h / 2 - h * 0.26, w * 0.13, h * 0.52))
            sx = f.left() if side == 0 else f.right() - w * 0.05
            p.drawRect(QRectF(sx, h / 2 - h * 0.11, w * 0.05, h * 0.22))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c("#ffffff", 220))
        p.drawEllipse(QPointF(w / 2, h / 2), h * 0.008, h * 0.008)
        rnd = random.Random(4)
        team = [(0.12, 0.5), (0.25, 0.25), (0.25, 0.75), (0.32, 0.45), (0.4, 0.2), (0.43, 0.62),
                (0.5, 0.38), (0.55, 0.8), (0.6, 0.55), (0.66, 0.3), (0.7, 0.68)]
        for k, col in enumerate((CORAL, SKY)):
            for (tx, ty) in team:
                x = tx if k == 0 else 1 - tx + rnd.uniform(-0.05, 0.05)
                y = ty if k == 0 else ty + rnd.uniform(-0.08, 0.08)
                p.setBrush(_c("#000000", 50))
                p.drawEllipse(QPointF(w * x + h * 0.006, h * y + h * 0.008), h * 0.022, h * 0.022)
                p.setBrush(QColor(col))
                p.drawEllipse(QPointF(w * x, h * y), h * 0.022, h * 0.022)
                p.setBrush(_c("#ffffff", 200))
                p.drawEllipse(QPointF(w * x, h * y), h * 0.008, h * 0.008)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QPointF(w * 0.58, h * 0.5), h * 0.011, h * 0.011)
        bug = QRectF(w * 0.03, h * 0.035, w * 0.2, h * 0.07)
        p.setBrush(_c(INK, 230))
        p.drawRoundedRect(bug, h * 0.012, h * 0.012)
        p.setBrush(QColor(CORAL))
        p.drawRect(QRectF(bug.left() + bug.width() * 0.05, bug.top() + bug.height() * 0.25,
                          bug.width() * 0.05, bug.height() * 0.5))
        p.setBrush(QColor(SKY))
        p.drawRect(QRectF(bug.left() + bug.width() * 0.5, bug.top() + bug.height() * 0.25,
                          bug.width() * 0.05, bug.height() * 0.5))
        p.setPen(QColor("#ffffff"))
        p.setFont(_font(h * 0.04, QFont.Weight.Bold))
        p.drawText(QRectF(bug.left() + bug.width() * 0.12, bug.top(), bug.width() * 0.36,
                          bug.height()), Qt.AlignmentFlag.AlignVCenter, "2 – 1")
        p.setFont(_font(h * 0.034, QFont.Weight.DemiBold))
        p.drawText(QRectF(bug.left() + bug.width() * 0.6, bug.top(), bug.width() * 0.38,
                          bug.height()), Qt.AlignmentFlag.AlignVCenter, "67:12")
    elif kind == "court":
        p.fillRect(r, QColor(mix(LEAF, MINT, 0.3)))
        c = QRectF(w * 0.2, h * 0.12, w * 0.6, h * 0.76)
        p.setBrush(QColor(mix(SKY, DEEP, 0.15)))
        p.drawRect(c.adjusted(-w * 0.03, -h * 0.05, w * 0.03, h * 0.05))
        p.setPen(QPen(_c("#ffffff", 235), h * 0.006))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(c)
        inner = c.adjusted(0, c.height() * 0.12, 0, -c.height() * 0.12)
        p.drawLine(inner.topLeft(), inner.topRight())
        p.drawLine(inner.bottomLeft(), inner.bottomRight())
        p.drawLine(QPointF(c.left() + c.width() * 0.25, inner.top()),
                   QPointF(c.left() + c.width() * 0.25, inner.bottom()))
        p.drawLine(QPointF(c.right() - c.width() * 0.25, inner.top()),
                   QPointF(c.right() - c.width() * 0.25, inner.bottom()))
        p.drawLine(QPointF(c.left() + c.width() * 0.25, c.center().y()),
                   QPointF(c.right() - c.width() * 0.25, c.center().y()))
        p.setPen(QPen(_c(CREAM, 255), h * 0.012))
        p.drawLine(QPointF(w / 2, c.top() - h * 0.03), QPointF(w / 2, c.bottom() + h * 0.03))
        p.setPen(Qt.PenStyle.NoPen)
        for (x, y, col) in ((0.3, 0.3, CORAL), (0.72, 0.66, SUN)):
            p.setBrush(_c("#000000", 60))
            p.drawEllipse(QPointF(w * x + h * 0.008, h * y + h * 0.01), h * 0.03, h * 0.03)
            p.setBrush(QColor(col))
            p.drawEllipse(QPointF(w * x, h * y), h * 0.03, h * 0.03)
        p.setBrush(QColor("#E8F25A"))
        p.drawEllipse(QPointF(w * 0.58, h * 0.44), h * 0.012, h * 0.012)
    elif kind == "lake":
        _vgrad(p, QRectF(0, 0, w, h * 0.6), [(0, PLUM), (0.5, BERRY), (1, ORANGE)])
        _sun(p, w * 0.55, h * 0.5, h * 0.12, CREAM)
        _mountains(p, w, h * 0.6, [(0.1, h * 0.3), (0.28, h * 0.45), (0.45, h * 0.25),
                                   (0.62, h * 0.42), (0.8, h * 0.28), (0.95, h * 0.4)],
                   mix(PLUM, INK, 0.25), mix(CORAL, CREAM, 0.6))
        _mountains(p, w, h * 0.62, [(0.05, h * 0.45), (0.3, h * 0.52), (0.7, h * 0.47),
                                    (0.92, h * 0.53)], mix(PLUM, INK, 0.55))
        water = QRectF(0, h * 0.6, w, h * 0.4)
        _vgrad(p, water, [(0, ORANGE), (0.4, BERRY), (1, mix(PLUM, INK, 0.4))])
        p.setBrush(_c(CREAM, 150))
        for k in range(7):
            ww = w * (0.18 - 0.02 * k)
            p.drawRoundedRect(QRectF(w * 0.55 - ww / 2, h * (0.63 + 0.04 * k), ww, h * 0.008),
                              h * 0.004, h * 0.004)
        for i in range(6):
            _pine(p, w * (0.02 + 0.045 * i), h * (0.66 + 0.01 * (i % 2)), h * (0.3 + 0.05 * (i % 3)),
                  mix(INK, PLUM, 0.2))
            _pine(p, w * (0.98 - 0.05 * i), h * (0.67 - 0.01 * (i % 2)), h * (0.28 + 0.06 * (i % 2)),
                  mix(INK, PLUM, 0.2))
    elif kind == "reef":
        _vgrad(p, r, [(0, mix(TEAL, "#ffffff", 0.2)), (0.5, TEAL), (1, DEEP)])
        for k in range(5):
            x = w * (0.1 + 0.2 * k)
            g = QLinearGradient(QPointF(0, 0), QPointF(0, h * 0.8))
            g.setColorAt(0, _c("#ffffff", 70))
            g.setColorAt(1, _c("#ffffff", 0))
            p.setBrush(QBrush(g))
            p.drawPolygon(_poly([(x, 0), (x + w * 0.06, 0), (x + w * 0.16, h * 0.8), (x + w * 0.02, h * 0.8)]))
        _hills(p, w, h, h * 0.86, h * 0.03, mix(SUN, CREAM, 0.4), 1, 2)
        for (x, hh, col) in ((0.12, 0.3, CORAL), (0.22, 0.2, BERRY), (0.78, 0.34, ORANGE),
                             (0.88, 0.22, CORAL), (0.5, 0.16, VIOLET)):
            _coral(p, w * x, h * 0.9, h * hh, col)
        for (x, y, s, col, d) in ((0.4, 0.4, 0.07, SUN, 1), (0.6, 0.3, 0.05, ORANGE, -1),
                                  (0.68, 0.55, 0.06, CORAL, 1), (0.3, 0.62, 0.04, SUN, -1),
                                  (0.52, 0.66, 0.035, VIOLET, 1)):
            _fish(p, w * x, h * y, h * s, col, d)
        p.setBrush(_c("#ffffff", 120))
        rnd = random.Random(2)
        for _ in range(16):
            rr = h * rnd.uniform(0.005, 0.014)
            p.drawEllipse(QPointF(w * rnd.uniform(0.3, 0.8), h * rnd.uniform(0.05, 0.6)), rr, rr)
    elif kind == "skyline":
        img2 = scenery("city", w, h, 2)
        p.drawImage(0, 0, img2)
    elif kind == "woods":
        img2 = scenery("forest", w, h, 1)
        p.drawImage(0, 0, img2)
        p.setBrush(_c(CREAM, 40))
        for k in range(4):
            x = w * (0.55 + 0.08 * k)
            p.drawPolygon(_poly([(x, 0), (x + w * 0.04, 0), (x - w * 0.2, h), (x - w * 0.3, h)]))
    elif kind == "cartoon":
        _vgrad(p, r, [(0, mix(SKY, "#ffffff", 0.15)), (1, mix(SKY, "#ffffff", 0.6))])
        cx, cy = w * 0.5, h * 0.95
        for k, col in enumerate((CORAL, ORANGE, SUN, MINT, SKY, VIOLET)):
            rr = h * (0.75 - 0.055 * k)
            p.setBrush(QColor(col))
            p.drawEllipse(QPointF(cx, cy), rr * 1.2, rr)
        p.setBrush(QColor(mix(SKY, "#ffffff", 0.45)))
        p.drawEllipse(QPointF(cx, cy), h * 0.42 * 1.2, h * 0.42)
        _sun(p, w * 0.14, h * 0.18, h * 0.08, SUN, glow=False)
        p.setPen(QPen(QColor(SUN), h * 0.012, cap=Qt.PenCapStyle.RoundCap))
        for k in range(10):
            a = math.radians(36 * k)
            p.drawLine(QPointF(w * 0.14 + math.cos(a) * h * 0.11, h * 0.18 + math.sin(a) * h * 0.11),
                       QPointF(w * 0.14 + math.cos(a) * h * 0.15, h * 0.18 + math.sin(a) * h * 0.15))
        p.setPen(Qt.PenStyle.NoPen)
        _cloud(p, w * 0.78, h * 0.16, w * 0.08, "#ffffff")
        _cloud(p, w * 0.35, h * 0.1, w * 0.05, "#ffffff")
        _hills(p, w, h, h * 0.78, h * 0.06, mix(MINT, LEAF, 0.2), 0.3, 1.6)
        _hills(p, w, h, h * 0.88, h * 0.05, mix(LEAF, MINT, 0.3), 2.0, 2.2)
        _robot(p, w * 0.3, h * 0.9, h * 0.34, CORAL)
        _critter(p, w * 0.68, h * 0.86, h * 0.13, VIOLET)
        _critter(p, w * 0.82, h * 0.9, h * 0.09, SUN)
    elif kind == "stage":
        _vgrad(p, r, [(0, NIGHT), (1, mix(PLUM, NIGHT, 0.5))])
        for k, col in enumerate((BERRY, SKY, SUN, VIOLET, MINT)):
            x = w * (0.1 + 0.2 * k)
            g = QLinearGradient(QPointF(x, 0), QPointF(x, h * 0.8))
            g.setColorAt(0, _c(col, 170))
            g.setColorAt(1, _c(col, 0))
            p.setBrush(QBrush(g))
            p.drawPolygon(_poly([(x - w * 0.012, 0), (x + w * 0.012, 0),
                                 (x + w * 0.12 * (1 if k % 2 else -1) + w * 0.09, h * 0.8),
                                 (x + w * 0.12 * (1 if k % 2 else -1) - w * 0.09, h * 0.8)]))
            p.setBrush(QColor(col))
            p.drawEllipse(QPointF(x, h * 0.02), h * 0.025, h * 0.025)
        p.setBrush(QColor(mix(PLUM, INK, 0.3)))
        p.drawRect(QRectF(0, h * 0.66, w, h * 0.06))
        p.setBrush(QColor(INK))
        for x in (0.22, 0.78):
            p.drawRoundedRect(QRectF(w * x - w * 0.05, h * 0.42, w * 0.1, h * 0.24), h * 0.01, h * 0.01)
            p.setBrush(QColor(mix(INK, NIGHT, 0.6)))
            p.drawEllipse(QPointF(w * x, h * 0.5), h * 0.04, h * 0.04)
            p.drawEllipse(QPointF(w * x, h * 0.6), h * 0.025, h * 0.025)
            p.setBrush(QColor(INK))
        p.setPen(QPen(QColor(INK), h * 0.01))
        p.drawLine(QPointF(w * 0.5, h * 0.66), QPointF(w * 0.5, h * 0.45))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(w * 0.5, h * 0.44), h * 0.018, h * 0.024)
        rnd = random.Random(8)
        for row in range(3):
            y = h * (0.86 + 0.06 * row)
            x = -w * 0.02 + row * w * 0.02
            while x < w * 1.02:
                rr = h * rnd.uniform(0.04, 0.055)
                p.setBrush(QColor(mix(NIGHT, INK, 0.3 + 0.2 * row)))
                p.drawEllipse(QPointF(x, y), rr, rr * 1.15)
                p.drawRoundedRect(QRectF(x - rr * 1.4, y + rr * 0.6, rr * 2.8, h * 0.2), rr, rr)
                x += rr * 2.4
        p.setBrush(_c(SUN, 200))
        p.drawEllipse(QPointF(w * 0.3, h * 0.8), h * 0.012, h * 0.012)
        p.drawEllipse(QPointF(w * 0.64, h * 0.78), h * 0.012, h * 0.012)
    p.end()
    return img


def _coral(p: QPainter, x: float, base: float, hgt: float, col: str) -> None:
    p.setPen(QPen(QColor(col), hgt * 0.1, cap=Qt.PenCapStyle.RoundCap,
                  join=Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    for ang in (-35, -12, 10, 32):
        a = math.radians(ang - 90)
        ex, ey = x + math.cos(a) * hgt, base + math.sin(a) * hgt
        path = QPainterPath()
        path.moveTo(x, base)
        path.quadTo(x + (ex - x) * 0.2, base - hgt * 0.5, ex, ey)
        p.drawPath(path)
        p.drawLine(QPointF(x + (ex - x) * 0.6, base + (ey - base) * 0.6),
                   QPointF(x + (ex - x) * 0.6 + hgt * 0.15 * (1 if ang > 0 else -1),
                           base + (ey - base) * 0.75))
    p.setPen(Qt.PenStyle.NoPen)


def _fish(p: QPainter, x: float, y: float, s: float, col: str, d: int) -> None:
    p.setBrush(QColor(col))
    p.drawEllipse(QPointF(x, y), s, s * 0.6)
    p.drawPolygon(_poly([(x - d * s * 0.8, y), (x - d * s * 1.5, y - s * 0.55),
                         (x - d * s * 1.5, y + s * 0.55)]))
    p.setBrush(_c("#ffffff", 90))
    p.drawRect(QRectF(x - s * 0.15, y - s * 0.55, s * 0.18, s * 1.1))
    p.setBrush(QColor("#ffffff"))
    p.drawEllipse(QPointF(x + d * s * 0.5, y - s * 0.12), s * 0.16, s * 0.16)
    p.setBrush(QColor(INK))
    p.drawEllipse(QPointF(x + d * s * 0.54, y - s * 0.12), s * 0.08, s * 0.08)


def _critter(p: QPainter, x: float, base: float, s: float, col: str) -> None:
    p.setBrush(QColor(col))
    p.drawEllipse(QPointF(x, base - s), s, s)
    p.drawEllipse(QPointF(x - s * 0.6, base - s * 1.8), s * 0.25, s * 0.35)
    p.drawEllipse(QPointF(x + s * 0.6, base - s * 1.8), s * 0.25, s * 0.35)
    p.setBrush(QColor("#ffffff"))
    for dx in (-0.35, 0.35):
        p.drawEllipse(QPointF(x + s * dx, base - s * 1.15), s * 0.24, s * 0.24)
    p.setBrush(QColor(INK))
    for dx in (-0.3, 0.4):
        p.drawEllipse(QPointF(x + s * dx, base - s * 1.12), s * 0.11, s * 0.11)
    p.setPen(QPen(QColor(INK), s * 0.08, cap=Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(x - s * 0.3, base - s * 0.95, s * 0.6, s * 0.4), 200 * 16, 140 * 16)
    p.setPen(Qt.PenStyle.NoPen)
