"""The sidebar's own widgets: a nav entry with its selected bar and quiet
status, the places' layout - a column, or folded a compact block of icons
that wraps - with its fold control, the hairline between the sidebar's
groups, the active source's avatar at its foot and its name over the
categories."""

from __future__ import annotations

import math
import re

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPaintEvent, QPen,
)
from PyQt6.QtWidgets import QLayout, QPushButton, QSizePolicy, QToolButton, QWidget

from .theme import P, corner_radius, ink_on, tinted

#: Status kinds a nav entry can show.
DOT, PILL, COUNT = "dot", "pill", "count"

BAR_W = 3.0


class NavButton(QPushButton):
    """A sidebar entry: icon and label (the icon alone on the rail), a 3 px
    bar at the leading edge while it is the entry you are on, and a status
    drawn over it - a dot, a count pill or a muted count at the trailing
    end, a dot in the icon's corner on the rail. The status is painted, not
    laid out, so the label never moves when it comes or goes. Folded into
    the places' compact block (the ``tile`` property, the icon alone as on
    the rail) the bar lies under the icon instead."""

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
        if on and self.property("tile"):
            length = max(6.0, min(18.0, w - 16.0))
            pr.setBrush(QColor(ink))
            pr.drawRoundedRect(QRectF((w - length) / 2, h - BAR_W - 1.0, length, BAR_W),
                               BAR_W / 2, BAR_W / 2)
        elif on:
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


class PlacesFold(QToolButton):
    """The places' fold control: a small chevron, up under the entries (fold
    them into a compact block of icons, giving the categories the room) and
    down as the last tile of that block (the entries in full again). Painted
    in the theme's muted ink; its words are in the tooltip and for a screen
    reader (the window's)."""

    W, H = 28, 16

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("PlacesFold")
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # A click leaves no focus ring behind; Tab still reaches it.
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        # Grows to a tile in the folded block (a Fixed policy caps a
        # layout's geometry at the hint).
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._folded = False

    def sizeHint(self) -> QSize:
        return QSize(self.W, self.H)

    def minimumSizeHint(self) -> QSize:
        return QSize(self.W, self.H)

    def set_folded(self, on: bool) -> None:
        if bool(on) != self._folded:
            self._folded = bool(on)
            self.update()

    def is_folded(self) -> bool:
        return self._folded

    def chevron_points(self) -> tuple[QPointF, QPointF, QPointF]:
        """The chevron's three points: pointing up while the entries are in
        full, down while they are folded."""
        cx, cy = self.width() / 2.0, self.height() / 2.0
        half_w, half_h = 5.0, 2.5
        tip = cy + half_h if self._folded else cy - half_h
        ends = cy - half_h if self._folded else cy + half_h
        return QPointF(cx - half_w, ends), QPointF(cx, tip), QPointF(cx + half_w, ends)

    def paintEvent(self, _event: QPaintEvent) -> None:
        pr = QPainter(self)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        whole = QRectF(0.0, 0.0, float(self.width()), float(self.height()))
        radius = corner_radius(8.0 if self._folded else 6.0)
        if self.isDown() or self.underMouse():
            pr.setPen(Qt.PenStyle.NoPen)
            pr.setBrush(QColor(P.get("sel" if self.isDown() else "hover", "#1D1D24")))
            pr.drawRoundedRect(whole, radius, radius)
        if self.hasFocus():
            pr.setPen(QPen(QColor(P.get("accent", "#4C8DFF")), 1.5))
            pr.setBrush(Qt.BrushStyle.NoBrush)
            pr.drawRoundedRect(whole.adjusted(1, 1, -1, -1), radius, radius)
        ink = P.get("text2" if self.underMouse() else "muted", "#8B8B96")
        pen = QPen(QColor(ink), 1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pr.setPen(pen)
        pr.setBrush(Qt.BrushStyle.NoBrush)
        a, b, c = self.chevron_points()
        path = QPainterPath(a)
        path.lineTo(b)
        path.lineTo(c)
        pr.drawPath(path)
        pr.end()


class PlacesLayout(QLayout):
    """The places' entries in the sidebar. In full (and on the icon rail) a
    column: each entry under the one before at the sidebar's width, the
    hairline between the groups, the fold control centred under them -
    as a QVBoxLayout would put them. Folded: a compact block of tiles, as
    many to a row as fit with the rows as even as they can be (nine
    entries and the fold control: two rows of five), wrapping onto another
    row when the sidebar is narrow; the hairline left out, the fold control
    the last tile. A hidden widget takes no room. Mirrored right to left."""

    #: A tile is never narrower than this, nor wider than that; the gap.
    TILE_MIN, TILE_MAX, TILE_GAP = 34, 48, 4

    def __init__(self, parent: QWidget | None = None, spacing: int = 2) -> None:
        super().__init__(parent)
        self._items: list = []
        self._folded = False
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    # -- QLayout's plumbing ----------------------------------------------------

    def addItem(self, item) -> None:  # noqa: N802 - Qt's name
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):  # noqa: N802 - Qt's name
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):  # noqa: N802 - Qt's name
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802 - Qt's name
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt's name
        return self._folded

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt's name
        return self._tiles(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - Qt's name
        super().setGeometry(rect)
        if self._folded:
            self._tiles(rect, apply=True)
        else:
            self._column(rect)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        if self._folded:
            n = len(self._tiled())
            return QSize(n * self.TILE_MIN + max(0, n - 1) * self.TILE_GAP, self._tile_h())
        shown = self._shown()
        w = max((it.sizeHint().width() for it in shown), default=0)
        return QSize(w, self._column_h(shown))

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt's name
        if self._folded:
            return QSize(self.TILE_MIN, self._tile_h())
        shown = self._shown()
        w = max((it.minimumSize().width() for it in shown), default=0)
        return QSize(w, self._column_h(shown))

    # -- folded or in full -----------------------------------------------------

    def set_folded(self, on: bool) -> bool:
        """Tiles (*on*) or the column; True when that changed. The owner
        asks its widget for a new geometry (updateGeometry)."""
        if bool(on) == self._folded:
            return False
        self._folded = bool(on)
        self.invalidate()
        return True

    def is_folded(self) -> bool:
        return self._folded

    def grid(self, width: int, n: int) -> tuple[int, int, int]:
        """(columns, rows, a tile's width) for *n* tiles in *width*."""
        if n <= 0:
            return 0, 0, 0
        gap = self.TILE_GAP
        fit = max(1, (max(0, width) + gap) // (self.TILE_MIN + gap))
        rows = math.ceil(n / fit)
        cols = math.ceil(n / rows)
        tile_w = (width - (cols - 1) * gap) // cols if cols else 0
        return cols, rows, max(1, min(self.TILE_MAX, tile_w))

    def _shown(self) -> list:
        return [it for it in self._items if not it.isEmpty()]

    def _tiled(self) -> list:
        return [it for it in self._shown() if not isinstance(it.widget(), SideRule)]

    def _tile_h(self) -> int:
        hs = [it.sizeHint().height() for it in self._tiled()
              if not isinstance(it.widget(), PlacesFold)]
        return max([self.TILE_MIN - 2, *hs])

    def _column_h(self, shown: list) -> int:
        if not shown:
            return 0
        return sum(it.sizeHint().height() for it in shown) + self.spacing() * (len(shown) - 1)

    def _column(self, rect: QRect) -> None:
        y = rect.y()
        for n, it in enumerate(self._shown()):
            if n:
                y += self.spacing()
            hint = it.sizeHint()
            if isinstance(it.widget(), PlacesFold):
                w = min(rect.width(), hint.width())
                it.setGeometry(QRect(rect.x() + (rect.width() - w) // 2, y, w, hint.height()))
            else:
                it.setGeometry(QRect(rect.x(), y, rect.width(), hint.height()))
            y += hint.height()

    def _tiles(self, rect: QRect, apply: bool) -> int:
        tiles = self._tiled()
        cols, rows, tile_w = self.grid(rect.width(), len(tiles))
        if not tiles:
            return 0
        tile_h, gap = self._tile_h(), self.TILE_GAP
        if apply:
            parent = self.parentWidget()
            rtl = parent is not None and parent.isRightToLeft()
            for i, it in enumerate(tiles):
                r, c = divmod(i, cols)
                off = c * (tile_w + gap)
                x = rect.right() + 1 - off - tile_w if rtl else rect.x() + off
                it.setGeometry(QRect(x, rect.y() + r * (tile_h + gap), tile_w, tile_h))
        return rows * tile_h + (rows - 1) * gap


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


_WORD = re.compile(r"[^\W_]+")


def source_initials(name: str) -> str:
    """A source's initials for its avatar: the first letters of its first
    two words, or the first two letters of a single word ("Min IPTV" -> MI,
    "dopeIPTV" -> DO). Words are runs of letters and digits, so "TV-Box
    (Home)" gives TB; "?" for a name with none."""
    words = _WORD.findall(name or "")
    if not words:
        return "?"
    text = (words[0][0] + words[1][0]) if len(words) > 1 else words[0][:2]
    return text.upper()


class SourceAvatar(QPushButton):
    """The active source as an avatar: a rounded square in the accent with
    its initials, the full name in the tooltip. Painted from the theme each
    time, so a new theme or accent shows at once."""

    SIZE = 32

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("SourceAvatar")
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._initials = "?"
        self._fill: str | None = None

    def set_source(self, name: str, hint: str = "") -> None:
        """Show source *name*; *hint* (what a click does) under it in the
        tooltip."""
        self._initials = source_initials(name)
        self._fill = None
        self.setToolTip(f"{name}\n{hint}" if name and hint else (name or hint))
        self.setAccessibleName(name or hint)
        self.setAccessibleDescription(hint)
        self.update()

    def set_profile(self, name: str, fill: str, text: str, source: str = "",
                    hint: str = "") -> None:
        """Show the profile in use (two profiles or more): its initials or
        emoji (*text*) on its colour (*fill*), the source it uses under its
        name in the tooltip."""
        self._initials = text
        self._fill = fill
        lines = [x for x in (name, source, hint) if x]
        self.setToolTip("\n".join(lines))
        self.setAccessibleName(name)
        self.setAccessibleDescription(hint)
        self.update()

    def initials(self) -> str:
        return self._initials

    def colors(self) -> tuple[str, str]:
        """(fill, ink) as painted now."""
        fill = self._fill or P.get("accent", "#4C8DFF")
        if self.isDown():
            fill = QColor(fill).darker(115).name()
        elif self.underMouse():
            fill = P.get("accent_hi", fill)
        return fill, ink_on(fill)

    def paintEvent(self, _event: QPaintEvent) -> None:
        fill, ink = self.colors()
        pr = QPainter(self)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pr.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        pr.setPen(Qt.PenStyle.NoPen)
        pr.setBrush(QColor(fill))
        box = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        pr.drawRoundedRect(box, 8.0, 8.0)
        if self.hasFocus() and self.focusPolicy() != Qt.FocusPolicy.NoFocus:
            ring = QPen(QColor(P.get("text", "#ECECF1")), 1.5)
            pr.setPen(ring)
            pr.setBrush(Qt.BrushStyle.NoBrush)
            pr.drawRoundedRect(box.adjusted(1, 1, -1, -1), 7.0, 7.0)
        f = QFont(self.font())
        f.setPixelSize(13)
        f.setWeight(QFont.Weight.Bold)
        pr.setFont(f)
        pr.setPen(QColor(ink))
        pr.drawText(box, int(Qt.AlignmentFlag.AlignCenter), self._initials)
        pr.end()


class SourceLabel(QPushButton):
    """The active source over the categories: its name, and with more than
    one source its kind as a small muted chip after it, a chevron at the
    trailing end - a click opens the source menu. Painted, so a long name
    is cut with an ellipsis, the chip goes before the name is cut short, and
    a right-to-left language mirrors the row."""

    HEIGHT = 30
    PAD = 13.0
    GAP = 6.0
    CHEVRON = 8.0
    #: The name keeps at least this much room before the chip goes.
    MIN_NAME = 48.0

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("SourceLabel")
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        # A click must not leave a focus ring behind; Tab still reaches it.
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        # Asks for no width, as the nav entries: the sidebar stays free to
        # be dragged narrow and to collapse to the rail.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self.setFixedHeight(self.HEIGHT)
        self._name = ""
        self._kind = ""

    def set_source(self, name: str, kind: str = "", tip: str = "",
                   accessible: str = "", hint: str = "") -> None:
        """Show source *name*, with *kind* in a chip ("" for none); *tip*
        for the pointer, *accessible* and *hint* for a screen reader."""
        if (name, kind) != (self._name, self._kind):
            self._name, self._kind = name, kind
            self.update()
        self.setToolTip(tip)
        self.setAccessibleName(accessible or name)
        self.setAccessibleDescription(hint)

    def name(self) -> str:
        return self._name

    def kind(self) -> str:
        return self._kind

    # -- geometry -----------------------------------------------------------

    def _fonts(self) -> tuple[QFont, QFont]:
        name = QFont(self.font())
        name.setPixelSize(13)
        name.setWeight(QFont.Weight.DemiBold)
        chip = QFont(self.font())
        chip.setPixelSize(10)
        chip.setWeight(QFont.Weight.DemiBold)
        return name, chip

    def boxes(self) -> tuple[QRectF, QRectF | None, QRectF, str]:
        """(the name's box, the chip's box or None, the chevron's box, the
        name as it is drawn) at the present width - mirrored in a
        right-to-left language."""
        w, h = float(self.width()), float(self.height())
        name_font, chip_font = self._fonts()
        fm = QFontMetricsF(name_font)
        end = w - self.PAD - self.CHEVRON
        room = max(0.0, end - self.GAP - self.PAD)
        chip_w = (QFontMetricsF(chip_font).horizontalAdvance(self._kind) + 12.0
                  if self._kind else 0.0)
        name_w = fm.horizontalAdvance(self._name)
        if chip_w and room - self.GAP - chip_w < min(name_w, self.MIN_NAME):
            chip_w = 0.0                     # too narrow for both: the name only
        name_room = max(0.0, room - (self.GAP + chip_w if chip_w else 0.0))
        text = fm.elidedText(self._name, Qt.TextElideMode.ElideRight, name_room)
        shown_w = min(fm.horizontalAdvance(text), name_room)
        name_box = QRectF(self.PAD, 0.0, shown_w, h)
        chip_box = None
        if chip_w:
            ch = 16.0
            chip_box = QRectF(self.PAD + shown_w + self.GAP, (h - ch) / 2, chip_w, ch)
        chevron = QRectF(end, (h - 5.0) / 2, self.CHEVRON, 5.0)
        if self.isRightToLeft():
            name_box = self._mirror(name_box, w)
            chip_box = self._mirror(chip_box, w) if chip_box is not None else None
            chevron = self._mirror(chevron, w)
        return name_box, chip_box, chevron, text

    @staticmethod
    def _mirror(box: QRectF, w: float) -> QRectF:
        return QRectF(w - box.x() - box.width(), box.y(), box.width(), box.height())

    @staticmethod
    def chip_colors() -> tuple[str, str]:
        """(fill, ink) of the kind's chip: a quiet wash of the muted tone
        over the sidebar, its words readable at 4.5:1 on every theme."""
        side = P.get("side", "#101014")
        return tinted(P.get("muted", "#8B8B96"), side, 0.18,
                      P.get("text", "#ECECF1"))

    # -- painting -----------------------------------------------------------

    def paintEvent(self, _event: QPaintEvent) -> None:
        pr = QPainter(self)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pr.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        whole = QRectF(0.0, 0.0, float(self.width()), float(self.height()))
        radius = corner_radius(8.0)
        if self.isDown() or self.underMouse():
            pr.setPen(Qt.PenStyle.NoPen)
            pr.setBrush(QColor(P.get("sel" if self.isDown() else "hover", "#1D1D24")))
            pr.drawRoundedRect(whole, radius, radius)
        if self.hasFocus():
            pr.setPen(QPen(QColor(P.get("accent", "#4C8DFF")), 1.5))
            pr.setBrush(Qt.BrushStyle.NoBrush)
            pr.drawRoundedRect(whole.adjusted(1, 1, -1, -1), radius, radius)
        name_box, chip_box, chevron, text = self.boxes()
        name_font, chip_font = self._fonts()
        rtl = self.isRightToLeft()
        lead = Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft
        pr.setFont(name_font)
        pr.setPen(QColor(P.get("text", "#ECECF1")))
        pr.drawText(name_box, int(lead | Qt.AlignmentFlag.AlignVCenter), text)
        if chip_box is not None:
            fill, ink = self.chip_colors()
            pr.setPen(Qt.PenStyle.NoPen)
            pr.setBrush(QColor(fill))
            r = corner_radius(4.0)
            pr.drawRoundedRect(chip_box, r, r)
            pr.setFont(chip_font)
            pr.setPen(QColor(ink))
            pr.drawText(chip_box, int(Qt.AlignmentFlag.AlignCenter), self._kind)
        ink = P.get("text2" if self.underMouse() else "muted2", "#6E6E79")
        pen = QPen(QColor(ink), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pr.setPen(pen)
        pr.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath(QPointF(chevron.left(), chevron.top()))
        path.lineTo(QPointF(chevron.center().x(), chevron.bottom()))
        path.lineTo(QPointF(chevron.right(), chevron.top()))
        pr.drawPath(path)
        pr.end()
