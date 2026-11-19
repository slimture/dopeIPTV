"""The player's control glyphs, drawn rather than typed.

``_control_icon`` paints play, pause, stop, record, rewind, the zap and
next-episode arrows, fullscreen, exit, pop-out, cast, options and the
speaker as monochrome icons in one box, so every button's symbol sits at
the same height and size: the control bar's (media/embedded.py), the
centre play/pause button over the picture (media/player_overlays.py) and
the pop-out's (ui/mw_popout.py).
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF


def _control_icon(name: str, color: str, px: int = 28) -> QIcon:
    """Draw a media-control glyph as a crisp, perfectly centred monochrome
    icon. Hand-drawing (instead of relying on Unicode glyphs, whose ink sits
    at glyph-specific offsets inside the em box) is what makes every button's
    symbol line up at the exact same height and size."""
    scale = 3
    S = px * scale
    pm = QPixmap(S, S)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    col = QColor(color)
    m = S * 0.30
    L, R, T, B = m, S - m, m, S - m
    w, h = R - L, B - T
    midx, midy = S / 2, S / 2
    solid = Qt.PenStyle.SolidLine
    round_cap = Qt.PenCapStyle.RoundCap
    round_join = Qt.PenJoinStyle.RoundJoin

    def fill():
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)

    def stroke(width):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(col, width, solid, round_cap, round_join))

    if name in ("play", "next"):
        fill()
        p.drawPolygon(QPolygonF([QPointF(L, T), QPointF(L, B), QPointF(R, midy)]))
    elif name == "prev":
        fill()
        p.drawPolygon(QPolygonF([QPointF(R, T), QPointF(R, B), QPointF(L, midy)]))
    elif name == "pause":
        fill()
        bw = w * 0.30
        p.drawRoundedRect(QRectF(L, T, bw, h), bw * 0.25, bw * 0.25)
        p.drawRoundedRect(QRectF(R - bw, T, bw, h), bw * 0.25, bw * 0.25)
    elif name == "stop":
        fill()
        p.drawRoundedRect(QRectF(L, T, w, h), w * 0.14, w * 0.14)
    elif name == "record":
        fill()
        p.drawEllipse(QRectF(L, T, w, h))
    elif name == "rewind":
        fill()
        p.drawPolygon(QPolygonF([QPointF(midx, T), QPointF(midx, B),
                                 QPointF(L, midy)]))
        p.drawPolygon(QPolygonF([QPointF(R, T), QPointF(R, B),
                                 QPointF(midx, midy)]))
    elif name == "nextep":
        # Skip-to-next: a right-pointing triangle plus a vertical bar (⏭),
        # drawn to the same box as the others so it lines up perfectly.
        fill()
        tri_r = L + w * 0.66
        p.drawPolygon(QPolygonF([QPointF(L, T), QPointF(L, B),
                                 QPointF(tri_r, midy)]))
        bar_w = w * 0.16
        p.drawRoundedRect(QRectF(R - bar_w, T, bar_w, h),
                          bar_w * 0.25, bar_w * 0.25)
    elif name == "exit":
        stroke(S * 0.11)
        p.drawLine(QPointF(L, T), QPointF(R, B))
        p.drawLine(QPointF(R, T), QPointF(L, B))
    elif name == "fullscreen":
        stroke(S * 0.09)
        seg = w * 0.34
        p.drawPolyline(QPolygonF([QPointF(L, T + seg), QPointF(L, T),
                                  QPointF(L + seg, T)]))
        p.drawPolyline(QPolygonF([QPointF(R - seg, T), QPointF(R, T),
                                  QPointF(R, T + seg)]))
        p.drawPolyline(QPolygonF([QPointF(L, B - seg), QPointF(L, B),
                                  QPointF(L + seg, B)]))
        p.drawPolyline(QPolygonF([QPointF(R - seg, B), QPointF(R, B),
                                  QPointF(R, B - seg)]))
    elif name == "popout":
        # The ⧉ glyph, drawn: two equal squares offset diagonally. Drawn rather
        # than typed because the Unicode character came out at whatever size
        # the platform font happened to give it (fine on macOS, tiny on Linux).
        # Uses a WIDER box than the other icons on purpose - the glyph it
        # replaces fills the button, so the shared inner box looked shrunken.
        pl, pt = S * 0.22, S * 0.22
        span = S - 2 * pl                     # full drawing extent
        off = span * 0.26                     # diagonal offset between squares
        side = span - off                     # each square's size
        stroke(S * 0.065)
        rad = S * 0.045
        # Back square (lower-left), then the detached one (upper-right).
        p.drawRoundedRect(QRectF(pl, pt + off, side, side), rad, rad)
        p.drawRoundedRect(QRectF(pl + off, pt, side, side), rad, rad)
    elif name == "cast":
        # A screen with the cast waves in its corner, in the wider box the
        # outline glyphs use (as "popout").
        cl = S * 0.20
        span = S - 2 * cl
        stroke(S * 0.065)
        rad = S * 0.05
        frame = QPainterPath()
        frame.moveTo(cl, cl + span * 0.30)
        frame.lineTo(cl, cl + span * 0.06 + rad)
        frame.quadTo(cl, cl + span * 0.06, cl + rad, cl + span * 0.06)
        frame.lineTo(cl + span - rad, cl + span * 0.06)
        frame.quadTo(cl + span, cl + span * 0.06, cl + span, cl + span * 0.06 + rad)
        frame.lineTo(cl + span, cl + span * 0.94 - rad)
        frame.quadTo(cl + span, cl + span * 0.94, cl + span - rad, cl + span * 0.94)
        frame.lineTo(cl + span * 0.56, cl + span * 0.94)
        p.drawPath(frame)
        ox, oy = cl, cl + span * 0.94
        for r in (span * 0.20, span * 0.38):
            p.drawArc(QRectF(ox - r, oy - r, 2 * r, 2 * r), 0, 90 * 16)
        fill()
        p.drawEllipse(QPointF(ox + span * 0.03, oy - span * 0.03), S * 0.04, S * 0.04)
    elif name == "options":
        rows = (T, midy, B)
        knobs = (R - w * 0.18, L + w * 0.22, R - w * 0.34)
        rad = S * 0.07
        for y, kx in zip(rows, knobs, strict=True):
            stroke(S * 0.075)
            p.drawLine(QPointF(L, y), QPointF(R, y))
            fill()
            p.drawEllipse(QPointF(kx, y), rad, rad)
    elif name in ("volume", "mute"):
        # Like "popout", drawn in a WIDER box than the solid glyphs. A speaker
        # is a slim shape - a small body, a cone and thin waves - so inside the
        # box the filled icons use it covered barely a third of the button and
        # looked like a different, much smaller icon set next to play/pause.
        # These constants give it roughly the same optical weight as they have.
        vl, vt = S * 0.20, S * 0.20
        span = S - 2 * vl
        vmid = S / 2
        fill()
        body_w, body_h = span * 0.24, span * 0.34
        p.drawRect(QRectF(vl, vmid - body_h / 2, body_w, body_h))
        cone_x = vl + span * 0.46
        p.drawPolygon(QPolygonF([
            QPointF(vl + body_w, vmid - body_h / 2), QPointF(cone_x, vt),
            QPointF(cone_x, vt + span), QPointF(vl + body_w, vmid + body_h / 2)]))
        if name == "volume":
            stroke(S * 0.062)
            for rad in (span * 0.20, span * 0.38):
                p.drawArc(QRectF(cone_x - rad, vmid - rad, rad * 2, rad * 2),
                          -55 * 16, 110 * 16)
        else:
            stroke(S * 0.072)
            xx, dy = cone_x + span * 0.10, span * 0.15
            p.drawLine(QPointF(xx, vmid - dy),
                       QPointF(xx + span * 0.30, vmid + dy))
            p.drawLine(QPointF(xx + span * 0.30, vmid - dy),
                       QPointF(xx, vmid + dy))
    p.end()
    pm.setDevicePixelRatio(scale)
    return QIcon(pm)
