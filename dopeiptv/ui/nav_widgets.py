"""The sidebar's own widgets: a nav entry with its selected bar and quiet
status, and the hairline between the sidebar's groups."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPaintEvent
from PyQt6.QtWidgets import QPushButton, QSizePolicy, QWidget

from .theme import P

#: Status kinds a nav entry can show.
DOT, PILL, COUNT = "dot", "pill", "count"

BAR_W = 3.0


class NavButton(QPushButton):
    """A sidebar entry: icon and label (the icon alone on the rail), a 3 px
    bar at the leading edge while it is the entry you are on, and a status
    drawn over it - a dot, a count pill or a muted count at the trailing
    end, a dot in the icon's corner on the rail. The status is painted, not
    laid out, so the label never moves when it comes or goes."""

    navigate = pyqtSignal(str)
    menu_requested = pyqtSignal(str, QPoint)

    def __init__(self, key: str, text: str, *, object_name: str = "NavBtn",
                 checkable: bool = True) -> None:
        super().__init__(text)
        self.setObjectName(object_name)
        self.key = key
        self.setCheckable(checkable)
        self.setFlat(True)
        # "Ignored" horizontally: fills the width when there is room but asks
        # for none, so the sidebar can be dragged narrow enough to collapse.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._badge: str | None = None
        self._count = 0
        self._fill = ""
        self._ink = ""
        self.clicked.connect(self._on_clicked)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_menu)

    def _on_clicked(self, _checked: bool = False) -> None:
        self.navigate.emit(self.key)

    def _on_menu(self, pos: QPoint) -> None:
        self.menu_requested.emit(self.key, self.mapToGlobal(pos))

    def set_active_colors(self, fill: str, ink: str) -> None:
        """The selected state's fill and ink for this entry ("" = the
        theme's) - its own colour when the user gave it one."""
        if (fill, ink) != (self._fill, self._ink):
            self._fill, self._ink = fill, ink
            self.update()

    def active_colors(self) -> tuple[str, str]:
        return (self._fill or P.get("nav_tint", P.get("sel", "#000000")),
                self._ink or P.get("nav_ink", P.get("accent", "#4C8DFF")))

    def set_badge(self, kind: str | None, count: int = 0) -> None:
        if (kind, count) != (self._badge, self._count):
            self._badge, self._count = kind, count
            self.update()

    def badge(self) -> tuple[str | None, int]:
        return self._badge, self._count

    # -- painting -----------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        on = self.isCheckable() and self.isChecked()
        if not on and not self._badge:
            return
        fill, ink = self.active_colors()
        pr = QPainter(self)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pr.setPen(Qt.PenStyle.NoPen)
        rtl = self.isRightToLeft()
        w, h = float(self.width()), float(self.height())
        if on:
            inset = max(6.0, h * 0.24)
            x = w - BAR_W if rtl else 0.0
            pr.setBrush(QColor(ink))
            pr.drawRoundedRect(QRectF(x, inset, BAR_W, h - 2 * inset),
                               BAR_W / 2, BAR_W / 2)
        if self._badge:
            under = fill if on else P.get("side", "#000000")
            if self.property("rail"):
                self._paint_corner_dot(pr, rtl, w, h, ink, under)
            else:
                self._paint_trailing(pr, rtl, w, h, on, ink)
        pr.end()

    def _dot_colour(self, ink: str) -> str:
        if self._badge == DOT:
            return P.get("rec", "#FF5C5C")
        if self._badge == PILL:
            return P.get("nav_ink", ink)
        return P.get("muted", "#8B8B96")

    def _paint_corner_dot(self, pr: QPainter, rtl: bool, w: float, h: float,
                          ink: str, under: str) -> None:
        s = float(self.iconSize().width())
        cx = w / 2 + (-s / 2 if rtl else s / 2) + (1.0 if rtl else -1.0)
        cy = h / 2 - s / 2 + 2.0
        r = 3.5
        pr.setBrush(QColor(under))
        pr.drawEllipse(QRectF(cx - r - 1.5, cy - r - 1.5, 2 * r + 3, 2 * r + 3))
        pr.setBrush(QColor(self._dot_colour(ink)))
        pr.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))

    def _paint_trailing(self, pr: QPainter, rtl: bool, w: float, h: float,
                        on: bool, ink: str) -> None:
        edge = 12.0
        if self._badge == DOT:
            r = 3.5
            cx = edge + r if rtl else w - edge - r
            pr.setBrush(QColor(P.get("rec", "#FF5C5C")))
            pr.drawEllipse(QRectF(cx - r, h / 2 - r, 2 * r, 2 * r))
            return
        text = str(self._count) if self._count < 100 else "99+"
        f = QFont(self.font())
        if self._badge == PILL:
            f.setPixelSize(10)
            f.setWeight(QFont.Weight.Bold)
            tw = QFontMetricsF(f).horizontalAdvance(text)
            ph = 16.0
            pw = max(ph, tw + 10.0)
            x = edge if rtl else w - edge - pw
            box = QRectF(x, (h - ph) / 2, pw, ph)
            pr.setBrush(QColor(P.get("nav_pill", P.get("sel", "#26262E"))))
            pr.drawRoundedRect(box, ph / 2, ph / 2)
            pr.setPen(QColor(P.get("nav_pill_ink", ink)))
        else:
            f.setPixelSize(12)
            f.setWeight(QFont.Weight.Normal)
            tw = QFontMetricsF(f).horizontalAdvance(text)
            x = edge if rtl else w - edge - tw
            box = QRectF(x, 0, tw, h)
            pr.setPen(QColor(ink if on else P.get("muted", "#8B8B96")))
        pr.setFont(f)
        pr.drawText(box, int(Qt.AlignmentFlag.AlignCenter), text)


class SideRule(QWidget):
    """A hairline between two of the sidebar's groups: across the sidebar,
    a short centred stroke on the icon rail. Its height is the gap, so the
    groups stay apart even where a theme's hairline barely shows."""

    def __init__(self, height: int = 13) -> None:
        super().__init__()
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._rail = False

    def set_rail(self, on: bool) -> None:
        if on != self._rail:
            self._rail = on
            self.update()

    def is_rail(self) -> bool:
        return self._rail

    def paintEvent(self, _event: QPaintEvent) -> None:
        w = float(self.width())
        length = min(24.0, w) if self._rail else max(0.0, w - 12.0)
        pr = QPainter(self)
        pr.fillRect(QRectF((w - length) / 2, self.height() // 2, length, 1.0),
                    QColor(P.get("border", "#232329")))
        pr.end()
