"""Small standalone widgets used by the main window (no window-state coupling)."""

from __future__ import annotations

import sys

from PyQt6.QtCore import (
    QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap,
)
from PyQt6.QtWidgets import (
    QLabel, QLayout, QLayoutItem, QMessageBox, QPushButton, QWidget,
)

from .. import APP_NAME
from .theme import P


def exec_menu_over_video(menu, global_pos) -> None:
    """Pop *menu* at *global_pos* so it renders cleanly over the embedded
    video. On macOS a QMenu shown over a layer-backed QOpenGLWidget bleeds
    through - the GL surface keeps repainting behind the non-native popup, so
    the video shows through the menu. Forcing the menu to a native window
    (winId materialises its own NSWindow surface) makes it composite above the
    GL layer. A no-op cost elsewhere, so it's unconditional apart from darwin."""
    if sys.platform == "darwin":
        menu.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        menu.winId()
    menu.exec(global_pos)


RESIZE_MARGIN = 8          # grab band along a frameless window's edges
RESIZE_MIN_W = 320
RESIZE_MIN_H = 180


def frameless_resize_edges(win, gpos, margin: int = RESIZE_MARGIN) -> "Qt.Edge":
    """Which edges of *win* the pointer (a global QPoint) is close enough to
    grab for a resize.

    Our video windows (pop-out, multiview) are title-bar-less by default, which
    leaves them without any resize grips - the video itself has to be the
    handle. Returns no edges when the title bar is on (the system frame has its
    own grips), and while maximised or fullscreen, where a click near the
    screen edge must still reach the video."""
    if win is None or win.isMaximized() or win.isFullScreen():
        return Qt.Edge(0)
    if not (win.windowFlags() & Qt.WindowType.FramelessWindowHint):
        return Qt.Edge(0)
    g = win.frameGeometry()
    edges = Qt.Edge(0)
    if gpos.x() <= g.left() + margin:
        edges |= Qt.Edge.LeftEdge
    elif gpos.x() >= g.right() - margin:
        edges |= Qt.Edge.RightEdge
    if gpos.y() <= g.top() + margin:
        edges |= Qt.Edge.TopEdge
    elif gpos.y() >= g.bottom() - margin:
        edges |= Qt.Edge.BottomEdge
    return edges


def resize_edge_cursor(edges) -> "Qt.CursorShape":
    """The pointer shape for a grab on *edges*, so the (invisible) grab band is
    discoverable by hovering it."""
    if ((Qt.Edge.LeftEdge in edges and Qt.Edge.TopEdge in edges)
            or (Qt.Edge.RightEdge in edges and Qt.Edge.BottomEdge in edges)):
        return Qt.CursorShape.SizeFDiagCursor
    if ((Qt.Edge.RightEdge in edges and Qt.Edge.TopEdge in edges)
            or (Qt.Edge.LeftEdge in edges and Qt.Edge.BottomEdge in edges)):
        return Qt.CursorShape.SizeBDiagCursor
    if Qt.Edge.LeftEdge in edges or Qt.Edge.RightEdge in edges:
        return Qt.CursorShape.SizeHorCursor
    return Qt.CursorShape.SizeVerCursor


def start_frameless_resize(win, edges, gpos) -> dict | None:
    """Begin a resize of *win*. Hands the drag to the window manager where that
    exists (X11, Wayland, Windows) and returns None - it owns the drag from
    there. Where it does not (macOS), returns the state to feed back into
    ``drag_frameless_resize`` on every mouse move."""
    handle = win.windowHandle() if win is not None else None
    if handle is not None and handle.startSystemResize(edges):
        return None
    return {"edges": edges, "from": gpos, "geo": QRect(win.geometry())}


def drag_frameless_resize(win, state, gpos, min_w: int = RESIZE_MIN_W,
                          min_h: int = RESIZE_MIN_H) -> None:
    """Apply one step of a self-driven resize (see start_frameless_resize).
    The edges being dragged move; the opposite ones stay put, and the window
    can't be squeezed below *min_w* x *min_h*."""
    if win is None or state is None:
        return
    g = QRect(state["geo"])
    dx = gpos.x() - state["from"].x()
    dy = gpos.y() - state["from"].y()
    edges = state["edges"]
    if Qt.Edge.LeftEdge in edges:
        g.setLeft(min(g.left() + dx, g.right() - min_w))
    elif Qt.Edge.RightEdge in edges:
        g.setRight(max(g.right() + dx, g.left() + min_w))
    if Qt.Edge.TopEdge in edges:
        g.setTop(min(g.top() + dy, g.bottom() - min_h))
    elif Qt.Edge.BottomEdge in edges:
        g.setBottom(max(g.bottom() + dy, g.top() + min_h))
    win.setGeometry(g)


def confirm(parent, title: str, text: str, *, default_yes: bool = True) -> bool:
    """A Yes/No confirmation with no icon - the stock QMessageBox.question
    stamps a large question-mark glyph into the dialog that reads as clutter
    against the app's flat styling. Returns True on Yes. Use this everywhere a
    delete/reset/remove used to call QMessageBox.question."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.NoIcon)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(
        QMessageBox.StandardButton.Yes if default_yes
        else QMessageBox.StandardButton.No)
    return box.exec() == QMessageBox.StandardButton.Yes


class _HoverTextButton(QPushButton):
    """Icon button that reveals a text label while hovered (and drops it on
    leave). Used by the sidebar playlist switcher: a clean square icon at
    rest, the active playlist's name when you point at it. Disabled on the
    collapsed rail (the rail's fixed width can't fit text)."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.hover_text = ""

    def enterEvent(self, event) -> None:
        if self.hover_text and not self.property("rail"):
            self.setText(self.hover_text)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if self.text():
            self.setText("")
        super().leaveEvent(event)


class _ClickableWidget(QWidget):
    """Plain QWidget that emits clicked() on a left-button press."""

    clicked = pyqtSignal()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class _Toast(QLabel):
    """Non-intrusive overlay notification that fades away after a few seconds."""

    DURATION_MS = 3500

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            "background: rgba(30,30,36,220); color: #ECECF1;"
            "border-radius: 10px; padding: 10px 18px;"
            "font-size: 12px; font-weight: 500;")
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._dismiss)

    def show_message(self, text: str, duration_ms: int = 0) -> None:
        self.setText(text)
        self.adjustSize()
        self.setFixedWidth(min(320, max(180, self.sizeHint().width() + 20)))
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        self._timer.start(duration_ms or self.DURATION_MS)

    def _place(self) -> None:
        p = self.parent()
        if p:
            self.move((p.width() - self.width()) // 2,
                      p.height() - self.height() - 30)

    def _dismiss(self) -> None:
        self.hide()


class _SidebarLogo(QWidget):
    """Themed mark at the top of the sidebar. A rounded accent pill with a
    play triangle on the left and three vertical audio/EQ bars on the right,
    like a stylised IPTV signal indicator. Wider than tall (roughly 2:1) so
    it fills the sidebar column nicely without a wordmark. Recolours live
    from ``P['accent']`` when the theme/accent changes (call ``update()``
    afterwards). Identical on Linux and macOS - no OS-specific paths."""

    LOGO_W = 92
    LOGO_H = 40

    clicked = pyqtSignal()
    update_clicked = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(self.LOGO_H + 10)
        self.setToolTip(APP_NAME)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_on = False
        self._update_color = QColor("#E5484D")
        self._update_follow_accent = False
        self._bounce_dy = 0.0

    def set_compact(self, on: bool) -> None:
        """Rail mode: the same mark scaled down to fit the 60 px icon rail
        (instance attrs shadow the class constants; paintEvent reads self.*,
        so everything - pill, triangle, bars, badge - scales together)."""
        if on:
            # The 60 px rail minus the sidebar's 12 px side margins leaves
            # exactly 36 px. At 36 the mark filled that to the pixel and the
            # update badge - which overhangs the mark's right edge by half its
            # radius - was cut off. Leave a few px of slack for the badge and
            # for antialiasing on the edges.
            self.LOGO_W, self.LOGO_H = 30, 15
        else:
            for a in ("LOGO_W", "LOGO_H"):
                self.__dict__.pop(a, None)
        self.setFixedHeight(self.LOGO_H + 10)
        self.update()

    def set_update(self, on: bool, color=None, follow_accent: bool = False) -> None:
        """Show/hide the corner update badge and set its colour. Pass an
        explicit ``color`` for a fixed hue, or ``follow_accent=True`` to track
        the live theme accent (so it keeps matching when the theme changes)."""
        self._update_on = bool(on)
        self._update_follow_accent = bool(follow_accent)
        if color is not None:
            self._update_color = QColor(color)
        self.update()

    def _badge_rect(self) -> "QRectF":
        w, h = float(self.LOGO_W), float(self.LOGO_H)
        x0 = (self.width() - w) / 2.0
        y0 = (self.height() - h) / 2.0
        # Badge scales with the mark (10 px at full size, smaller on the rail).
        r = max(6.0, h * 0.25)
        cx, cy = x0 + w - r * 0.5, y0 + r * 0.5
        # Keep the badge inside the widget: it deliberately overhangs the
        # mark's corner, and on the narrow rail that overhang fell outside the
        # widget and got clipped.
        cx = min(cx, self.width() - r)
        cy = max(cy, r)
        return QRectF(cx - r, cy - r, 2 * r, 2 * r)

    def bounce(self, hops: int = 4, period_ms: int = 4000) -> None:
        """Hop the update badge ``hops`` times, one hop every ``period_ms``, to
        catch the eye at startup without nagging. Each cycle is a quick hop up
        and back down followed by a long rest before the next hop."""
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        amp = -10.0
        anim = QVariantAnimation(self)
        anim.setDuration(period_ms)
        anim.setStartValue(0.0)
        anim.setKeyValueAt(0.05, amp)        # up quickly
        anim.setKeyValueAt(0.16, 0.0)        # settle back down
        anim.setEndValue(0.0)                # long rest until the next hop
        anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        anim.setLoopCount(max(1, hops))
        anim.valueChanged.connect(self._on_bounce)
        anim.finished.connect(lambda: self._on_bounce(0.0))
        anim.start()
        self._bounce_anim = anim

    def _on_bounce(self, v) -> None:
        self._bounce_dy = float(v)
        self.update()

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            if self._update_on and self._badge_rect().contains(e.position()):
                self.update_clicked.emit()
            else:
                self.clicked.emit()
            e.accept()
            return
        super().mousePressEvent(e)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.LOGO_W), float(self.LOGO_H)
        x0 = (self.width() - w) / 2.0
        y0 = (self.height() - h) / 2.0
        accent = QColor(P.get("accent", "#4C8DFF"))
        # Rounded pill as the base.
        pill = QPainterPath()
        pill.addRoundedRect(x0, y0, w, h, h * 0.30, h * 0.30)
        painter.fillPath(pill, accent)

        # Left half: play triangle. Nudged right by a fraction so its
        # optical centre lines up with its geometric third of the pill.
        left_cx = x0 + w * 0.28
        cy = y0 + h * 0.50
        tri_h = h * 0.46
        tri_w = h * 0.42
        tri = QPainterPath()
        tri.moveTo(left_cx - tri_w * 0.55, cy - tri_h * 0.5)
        tri.lineTo(left_cx - tri_w * 0.55, cy + tri_h * 0.5)
        tri.lineTo(left_cx + tri_w * 0.55, cy)
        tri.closeSubpath()
        painter.fillPath(tri, QColor("white"))

        # Slim white divider between the play half and the signal half - a
        # subtle vertical rule that gives the mark structure.
        pen_div = QPen(QColor(255, 255, 255, 90))
        pen_div.setWidthF(1.2)
        painter.setPen(pen_div)
        div_x = x0 + w * 0.48
        painter.drawLine(QPointF(div_x, y0 + h * 0.22),
                         QPointF(div_x, y0 + h * 0.78))

        # Right half: three vertical bars of varying heights (the middle is
        # tallest), reading as an EQ / signal-strength indicator.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("white"))
        base_y = y0 + h * 0.72
        bar_w = h * 0.14
        heights = (h * 0.28, h * 0.46, h * 0.34)
        first_x = x0 + w * 0.58
        gap = h * 0.14
        for i, bh in enumerate(heights):
            bx = first_x + i * (bar_w + gap)
            painter.drawRoundedRect(
                QRectF(bx, base_y - bh, bar_w, bh),
                bar_w * 0.4, bar_w * 0.4)

        # Update badge: a small coloured circle with a white up-arrow in the
        # pill's top-right corner, drawn on top so it's always visible (a child
        # widget over this custom paint didn't render). Colour comes from the
        # caller (red at startup, theme accent after 30 s); a transient bounce
        # offset nudges it up.
        if self._update_on:
            br = self._badge_rect()
            cx = br.center().x()
            cy = br.center().y() + self._bounce_dy
            r = br.width() / 2.0
            badge_color = (QColor(P.get("accent", "#4C8DFF"))
                           if self._update_follow_accent else self._update_color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(badge_color)
            painter.drawEllipse(QPointF(cx, cy), r, r)
            aw, ah = r * 0.60, r * 0.72
            arrow = QPainterPath()
            arrow.moveTo(cx, cy - ah * 0.62)                 # tip
            arrow.lineTo(cx - aw, cy + ah * 0.12)
            arrow.lineTo(cx - aw * 0.42, cy + ah * 0.12)
            arrow.lineTo(cx - aw * 0.42, cy + ah * 0.62)     # stem
            arrow.lineTo(cx + aw * 0.42, cy + ah * 0.62)
            arrow.lineTo(cx + aw * 0.42, cy + ah * 0.12)
            arrow.lineTo(cx + aw, cy + ah * 0.12)
            arrow.closeSubpath()
            painter.fillPath(arrow, QColor("white"))
        painter.end()

def cast_strip_icon(kind: str, colour: str) -> QIcon:
    """Drawn icons for the cast strip's buttons.

    They were characters - a gear, a pause bar, a minus sign - and a font
    stack that has no glyph for one of them draws an empty box instead. Which
    is what the volume buttons turned into. Drawn in a 14 px box they look the
    same on every machine and cannot go missing.
    """
    size, scale = 14, 3
    pm = QPixmap(size * scale, size * scale)
    pm.setDevicePixelRatio(float(scale))
    pm.fill(Qt.GlobalColor.transparent)
    pr = QPainter(pm)
    pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    col = QColor(colour)
    pen = QPen(col)
    pen.setWidthF(size * 0.13)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pr.setPen(pen)
    s = float(size)
    if kind in ("minus", "plus"):
        pr.drawLine(QPointF(s * 0.22, s * 0.5), QPointF(s * 0.78, s * 0.5))
        if kind == "plus":
            pr.drawLine(QPointF(s * 0.5, s * 0.22), QPointF(s * 0.5, s * 0.78))
    elif kind == "pause":
        pen.setWidthF(s * 0.18)
        pr.setPen(pen)
        pr.drawLine(QPointF(s * 0.36, s * 0.22), QPointF(s * 0.36, s * 0.78))
        pr.drawLine(QPointF(s * 0.64, s * 0.22), QPointF(s * 0.64, s * 0.78))
    elif kind == "play":
        pr.setPen(Qt.PenStyle.NoPen)
        pr.setBrush(col)
        tri = QPainterPath()
        tri.moveTo(s * 0.30, s * 0.20)
        tri.lineTo(s * 0.80, s * 0.50)
        tri.lineTo(s * 0.30, s * 0.80)
        tri.closeSubpath()
        pr.drawPath(tri)
    elif kind in ("volume", "muted"):
        pr.setPen(Qt.PenStyle.NoPen)
        pr.setBrush(col)
        cone = QPainterPath()               # a small speaker
        cone.moveTo(s * 0.10, s * 0.38)
        cone.lineTo(s * 0.28, s * 0.38)
        cone.lineTo(s * 0.48, s * 0.20)
        cone.lineTo(s * 0.48, s * 0.80)
        cone.lineTo(s * 0.28, s * 0.62)
        cone.lineTo(s * 0.10, s * 0.62)
        cone.closeSubpath()
        pr.drawPath(cone)
        pen.setWidthF(s * 0.10)
        pr.setPen(pen)
        pr.setBrush(Qt.BrushStyle.NoBrush)
        if kind == "muted":                 # struck through
            pr.drawLine(QPointF(s * 0.60, s * 0.34),
                        QPointF(s * 0.88, s * 0.66))
            pr.drawLine(QPointF(s * 0.88, s * 0.34),
                        QPointF(s * 0.60, s * 0.66))
        else:                               # two waves
            for r in (0.16, 0.28):
                pr.drawArc(QRectF(s * (0.56 - r), s * (0.5 - r),
                                  s * r * 2, s * r * 2),
                           -60 * 16, 120 * 16)
    elif kind == "rewind":                  # a clock wound back
        pen.setWidthF(s * 0.10)
        pr.setPen(pen)
        pr.setBrush(Qt.BrushStyle.NoBrush)
        # Most of a circle, with an arrowhead where the gap is - the shape
        # every remote uses for going back in time.
        pr.drawArc(QRectF(s * 0.16, s * 0.16, s * 0.68, s * 0.68),
                   100 * 16, 300 * 16)
        pr.setPen(Qt.PenStyle.NoPen)
        pr.setBrush(col)
        head = QPainterPath()
        head.moveTo(s * 0.50, s * 0.04)
        head.lineTo(s * 0.50, s * 0.30)
        head.lineTo(s * 0.26, s * 0.17)
        head.closeSubpath()
        pr.drawPath(head)
        pr.setPen(pen)                      # the hands
        pr.drawLine(QPointF(s * 0.50, s * 0.50), QPointF(s * 0.50, s * 0.32))
        pr.drawLine(QPointF(s * 0.50, s * 0.50), QPointF(s * 0.66, s * 0.58))
    else:                                   # "tracks": three sliders
        for i, y in enumerate((0.28, 0.5, 0.72)):
            pr.drawLine(QPointF(s * 0.18, s * y), QPointF(s * 0.82, s * y))
            x = (0.62, 0.34, 0.70)[i]
            pr.setBrush(col)
            pr.drawEllipse(QPointF(s * x, s * y), s * 0.11, s * 0.11)
    pr.end()
    return QIcon(pm)


class FlowRow(QLayout):
    """A row that wraps onto the next line instead of squashing.

    A QHBoxLayout given less width than its contents need does not stop at
    their minimum: it goes on taking pixels away until the buttons sit on top
    of one another. The cast strip is where that shows, because it is in the
    right-hand column and that column is draggable - pull it in and the mute
    button, the volume slider and the timeshift, tracks, pause and stop
    controls pile up in the same forty pixels. Which is exactly the width
    that column has on a small laptop to begin with.

    So: fill a line, and when the next thing will not fit, start another one.
    The narrowest this can be asked to be is the widest single item in it,
    and everything stays reachable all the way down to that.

    One item per row may be marked as taking the slack, so with room to spare
    the layout still looks like the row it was: the label on the left, the
    controls out at the right edge.
    """

    def __init__(self, parent=None, spacing: int = 10,
                 line_spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._grow: set[int] = set()
        self._line_spacing = line_spacing
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    # -- QLayout plumbing --------------------------------------------------

    def addItem(self, item) -> None:            # noqa: N802 (Qt)
        self._items.append(item)

    def add(self, widget, grow: bool = False):
        """Add *widget*; *grow* gives it whatever is left over on its line."""
        if grow:
            self._grow.add(len(self._items))
        self.addWidget(widget)
        return widget

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):                   # noqa: N802 (Qt)
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):                   # noqa: N802 (Qt)
        if 0 <= i < len(self._items):
            self._grow = {g - 1 if g > i else g for g in self._grow if g != i}
            return self._items.pop(i)
        return None

    def expandingDirections(self):              # noqa: N802 (Qt)
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:        # noqa: N802 (Qt)
        return True

    def heightForWidth(self, width: int) -> int:   # noqa: N802 (Qt)
        return self._lay(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect) -> None:        # noqa: N802 (Qt)
        super().setGeometry(rect)
        self._lay(rect, apply=True)

    def _shown(self):
        """The items that are actually there. A hidden widget takes no room -
        the timeshift, pause and LIVE controls come and go with what is being
        cast, and reserving a gap for one that is not shown would break the
        line early and leave a hole where it used to be."""
        return [(i, it) for i, it in enumerate(self._items) if not it.isEmpty()]

    def sizeHint(self):                         # noqa: N802 (Qt)
        # Everything on one line: what the strip looks like when there is room
        # for it, which is what the column should be given if it can have it.
        left, top, right, bottom = self.getContentsMargins()
        w = h = 0
        for n, (_i, item) in enumerate(self._shown()):
            s = item.sizeHint()
            w += s.width() + (self.spacing() if n else 0)
            h = max(h, s.height())
        return QSize(w + left + right, h + top + bottom)

    def minimumSize(self):                      # noqa: N802 (Qt)
        # One item per line. Below this there is nothing left to give, and
        # this is the number that stops a parent from squashing us into an
        # overlap - a QHBoxLayout's minimum is the sum of them all, which is
        # far more than the column has and so gets ignored.
        left, top, right, bottom = self.getContentsMargins()
        w = max((item.minimumSize().width() for _i, item in self._shown()),
                default=0)
        return QSize(w + left + right,
                     self.heightForWidth(w + left + right) + top + bottom)

    # -- the one pass both the measuring and the placing use ---------------

    def _lay(self, rect, apply: bool) -> int:
        """Walk the items, breaking lines to fit *rect*'s width. Returns the
        height it took. With *apply*, the items are moved there too."""
        left, top, right, bottom = self.getContentsMargins()
        inner = rect.adjusted(left, top, -right, -bottom)
        x, y, line_h, gap = inner.x(), inner.y(), 0, self.spacing()
        line: list[tuple[int, QLayoutItem, int]] = []

        def flush() -> None:
            """Place the line that has been gathered, and hand any space left
            over on it to the item that was marked to take it."""
            nonlocal x, y, line_h
            if apply and line:
                used = sum(w for _i, _it, w in line) + gap * (len(line) - 1)
                slack = max(0, inner.width() - used)
                grower = next((n for n, (i, _it, _w) in enumerate(line)
                               if i in self._grow), None)
                at = inner.x()
                for n, (_i, item, w) in enumerate(line):
                    if n == grower:
                        w += slack
                    h = item.sizeHint().height()
                    if item.hasHeightForWidth():
                        h = max(h, item.heightForWidth(w))
                    # Centred on the line: a 36-pixel button next to a
                    # two-line label reads as a row, not as a staircase.
                    item.setGeometry(QRect(at, y + (line_h - h) // 2, w, h))
                    at += w + gap
            x, y = inner.x(), y + line_h + self._line_spacing
            line_h = 0
            line.clear()

        for i, item in self._shown():
            w = item.sizeHint().width()
            if i in self._grow:
                w = max(w, item.minimumSize().width())
            w = min(w, inner.width())           # never wider than the strip
            if line and x + w > inner.right() + 1:
                flush()
            h = item.sizeHint().height()
            if item.hasHeightForWidth():
                h = max(h, item.heightForWidth(w))
            line.append((i, item, w))
            line_h = max(line_h, h)
            x += w + gap
        empty = not line
        flush()
        if empty and y == inner.y() + self._line_spacing:
            return top + bottom             # nothing shown, so no lines
        # flush() left y past the line it placed, plus the gap that would
        # have gone before the next one - and there is no next one.
        return (y - self._line_spacing) - inner.y() + top + bottom
