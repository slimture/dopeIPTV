"""Small standalone widgets used by the main window (no window-state coupling)."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QLabel, QWidget

from .. import APP_NAME
from .theme import P


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
        r, cx, cy = 10.0, x0 + w - 5.0, y0 + 5.0
        return QRectF(cx - r, cy - r, 2 * r, 2 * r)

    def bounce(self, duration_ms: int = 15_000) -> None:
        """Hop the update badge for roughly ``duration_ms`` to catch the eye at
        startup, then rest so it's noticed without nagging forever. Each cycle
        is a clear hop up and back down followed by a short pause."""
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        hop = 1200
        amp = -10.0
        anim = QVariantAnimation(self)
        anim.setDuration(hop)
        anim.setStartValue(0.0)
        anim.setKeyValueAt(0.14, amp)        # up quickly
        anim.setKeyValueAt(0.34, 0.0)        # settle back down
        anim.setEndValue(0.0)                # rest until the next hop
        anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        anim.setLoopCount(max(1, round(duration_ms / hop)))
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
