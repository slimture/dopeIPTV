"""The notices drawn (ui/notices.py holds their state): the activity line
and its ring on the icon rail, the panel of every job, the cards and their
stack - flat, in P's colours made to read on every theme, rounded like the
app's other surfaces, mirrored right to left, faded softly in and out
unless the desktop asks for less motion."""
from __future__ import annotations

from functools import partial
from typing import Any, Callable

from PyQt6.QtCore import (
    QEasingCurve, QEvent, QObject, QPoint, QPointF, QPropertyAnimation, QRect,
    QRectF, QSize, Qt, QTimer, pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from ..core.log import log
from ..i18n import tr
from .notices import Card, Entry, NoticeHub
from .theme import P, contrast, corner_radius, ink_on, tinted

# -- colours -------------------------------------------------------------------

_COLOURS: dict[tuple, dict[str, str]] = {}


def colours() -> dict[str, str]:
    """The notices' colours on the theme in use, each made to read: text at
    4.5:1 on its surface, the dismiss mark at 3:1, a surface set apart from
    the pane it lies on (a theme whose raised fill is its pane's gets one
    of its own)."""
    key = tuple(P.get(k, "") for k in ("bg", "pane", "side", "btn", "btn_hover", "text",
                                       "text2", "text3", "muted", "accent", "hover"))
    hit = _COLOURS.get(key)
    if hit is not None:
        return hit
    text = P["text"]

    def ink(colour: str, on: str, need: float = 4.5) -> str:
        return tinted(colour, on, 0.0, text, need)[1]

    def raised(fill: str, under: str) -> str:
        return fill if contrast(fill, under) >= 1.12 else tinted(text, under, 0.08, text)[0]

    surface = raised(P["btn"], P["pane"])
    line_bg = raised(P["hover"], P["side"])
    float_bg = raised(P["btn"], P["bg"])
    action_bg, action = tinted(P["accent"], surface, 0.16, text)
    out = {
        "surface": surface, "hover": tinted(text, surface, 0.06, text)[0],
        "title": ink(text, surface), "body": ink(P["text3"], surface),
        "muted": ink(P["muted"], surface), "dismiss": ink(P["muted"], surface, 3.0),
        "action_bg": action_bg, "action": action,
        "action_hover": tinted(P["accent"], surface, 0.26, text)[0],
        "line_bg": line_bg, "line_text": ink(P["text2"], line_bg),
        "line_more": ink(P["muted"], line_bg), "float_bg": float_bg,
        "float_text": ink(P["text2"], float_bg), "float_more": ink(P["muted"], float_bg),
        "icon": ink(P["text3"], line_bg, 3.0), "track": tinted(text, surface, 0.14, text)[0],
        "accent": P["accent"],
    }
    _COLOURS.clear()
    _COLOURS[key] = out
    return out


def reduced_motion() -> bool:
    """The desktop asked for fewer animations (Qt's general UI effect)."""
    try:
        return not QApplication.isEffectEnabled(Qt.UIEffect.UI_General)
    except Exception:
        return False


def _font(px: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont(QApplication.font())
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


def paint_kind(p: QPainter, kind: str, r: QRectF, colour: QColor) -> None:
    """A small line icon for a kind of work, in *r*."""
    p.save()
    pen = QPen(colour, max(1.4, r.width() / 11.0))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    s = r.width()
    x, y = r.left(), r.top()

    def pt(a: float, b: float) -> QPointF:
        return QPointF(x + a * s, y + b * s)

    if kind == "guide":
        for yy in (0.24, 0.5, 0.76):
            p.drawLine(pt(0.12, yy), pt(0.26, yy))
            p.drawLine(pt(0.42, yy), pt(0.88, yy))
    elif kind == "folder":
        path = QPainterPath(pt(0.1, 0.3))
        path.lineTo(pt(0.1, 0.82))
        path.lineTo(pt(0.9, 0.82))
        path.lineTo(pt(0.9, 0.38))
        path.lineTo(pt(0.48, 0.38))
        path.lineTo(pt(0.4, 0.22))
        path.lineTo(pt(0.1, 0.22))
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "search":
        p.drawEllipse(QRectF(x + 0.12 * s, y + 0.12 * s, 0.52 * s, 0.52 * s))
        p.drawLine(pt(0.58, 0.58), pt(0.88, 0.88))
    elif kind == "network":
        for i, rad in enumerate((0.18, 0.34, 0.5)):
            rr = QRectF(x + (0.5 - rad) * s, y + (0.86 - rad) * s, 2 * rad * s, 2 * rad * s)
            if i == 0:
                p.setBrush(colour)
                p.drawEllipse(QRectF(x + 0.44 * s, y + 0.8 * s, 0.12 * s, 0.12 * s))
                p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(rr, 45 * 16, 90 * 16)
    elif kind in ("sync", "update"):
        rr = QRectF(x + 0.16 * s, y + 0.16 * s, 0.68 * s, 0.68 * s)
        p.drawArc(rr, 30 * 16, 150 * 16)
        p.drawArc(rr, 210 * 16, 150 * 16)
        p.drawLine(pt(0.76, 0.12), pt(0.8, 0.32))
        p.drawLine(pt(0.8, 0.32), pt(0.6, 0.32))
        if kind == "sync":
            p.drawLine(pt(0.24, 0.88), pt(0.2, 0.68))
            p.drawLine(pt(0.2, 0.68), pt(0.4, 0.68))
    elif kind == "download":
        p.drawLine(pt(0.5, 0.12), pt(0.5, 0.66))
        p.drawLine(pt(0.3, 0.46), pt(0.5, 0.66))
        p.drawLine(pt(0.7, 0.46), pt(0.5, 0.66))
        p.drawLine(pt(0.16, 0.86), pt(0.84, 0.86))
    elif kind == "record":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(P["rec"]))
        p.drawEllipse(QRectF(x + 0.22 * s, y + 0.22 * s, 0.56 * s, 0.56 * s))
    else:   # list, work
        for yy in (0.26, 0.5, 0.74):
            p.drawLine(pt(0.14, yy), pt(0.86, yy))
    p.restore()


def paint_level(p: QPainter, level: str, r: QRectF) -> None:
    """A card's mark: a dot with "i" or "!" in its level's colour."""
    colour = QColor({"error": P["error"], "warning": P["warn"]}.get(level, P["accent"]))
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(colour)
    p.drawEllipse(r)
    ink = QColor(ink_on(colour.name()))
    pen = QPen(ink, max(1.6, r.width() / 8.0))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    cx, s = r.center().x(), r.width()
    if level == "info":
        p.drawPoint(QPointF(cx, r.top() + 0.3 * s))
        p.drawLine(QPointF(cx, r.top() + 0.46 * s), QPointF(cx, r.top() + 0.74 * s))
    else:
        p.drawLine(QPointF(cx, r.top() + 0.26 * s), QPointF(cx, r.top() + 0.56 * s))
        p.drawPoint(QPointF(cx, r.top() + 0.74 * s))
    p.restore()


def paint_progress(p: QPainter, r: QRectF, progress: float | None, angle: int) -> None:
    """A spinner arc (no progress known) or a ring filled to *progress*."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    w = max(2.0, r.width() / 8.0)
    track = QPen(QColor(colours()["track"]), w)
    p.setPen(track)
    p.drawEllipse(r)
    pen = QPen(QColor(P["accent"]), w)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    if progress is None:
        p.drawArc(r, -angle * 16, 100 * 16)
    else:
        p.drawArc(r, 90 * 16, -int(360 * 16 * max(0.02, progress)))
    p.restore()


class _Fader:
    """A soft fade in and out for a widget (none when the desktop asks for
    less motion); *after_out* runs once it has faded."""

    MS = 160

    def __init__(self, widget: QWidget) -> None:
        self._w = widget
        self._fx: QGraphicsOpacityEffect | None = None
        self._anim: QPropertyAnimation | None = None

    def show(self) -> None:
        w = self._w
        if not w.isHidden() and self._anim is None:
            return
        if reduced_motion() or not w.window().isVisible():
            self._stop()
            w.show()
            return
        self._run(0.0 if w.isHidden() else self._now(), 1.0, None)
        w.show()

    def hide(self, after_out: Callable[[], Any] | None = None) -> None:
        w = self._w
        if w.isHidden():
            if after_out is not None:
                after_out()
            return
        if reduced_motion() or not w.window().isVisible():
            self._stop()
            w.hide()
            if after_out is not None:
                after_out()
            return
        self._run(self._now(), 0.0, after_out)

    def _now(self) -> float:
        return self._fx.opacity() if self._fx is not None else 1.0

    def _run(self, a: float, b: float, after: Callable[[], Any] | None) -> None:
        if self._fx is None:
            self._fx = QGraphicsOpacityEffect(self._w)
            self._w.setGraphicsEffect(self._fx)
        if self._anim is not None:
            self._anim.stop()
        anim = QPropertyAnimation(self._fx, b"opacity", self._w)
        anim.setDuration(self.MS)
        anim.setStartValue(a)
        anim.setEndValue(b)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(partial(self._done, b, after))
        self._anim = anim
        anim.start()

    def _done(self, end: float, after: Callable[[], Any] | None) -> None:
        self._anim = None
        if end <= 0.0:
            self._w.hide()
            if after is not None:
                after()
        self._stop()

    def _stop(self) -> None:
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        if self._fx is not None:
            self._w.setGraphicsEffect(None)    # no effect left while it rests
            self._fx = None


class ActivityLine(QWidget):
    """The running work as one line (or, on the icon rail, a ring with a
    count). Enter, Space or a click opens its panel."""

    open_panel = pyqtSignal()
    TICK_MS = 50
    H = 34

    def __init__(self, hub: NoticeHub, parent: QWidget | None = None, *,
                 floating: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("NoticeActivity")
        self._hub = hub
        self._floating = floating
        self._ring = False
        self._angle = 0
        self._entries: list[Entry] = []
        self._fader = _Fader(self)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.H)
        self._font = _font(13, QFont.Weight.Medium)
        self._small = _font(10, QFont.Weight.Bold)
        self._spin = QTimer(self)
        self._spin.setInterval(self.TICK_MS)
        self._spin.timeout.connect(self._turn)
        self.hide()

    def set_ring(self, on: bool) -> None:
        """The icon rail: a ring with a count, as wide as the rail's icons."""
        if bool(on) == self._ring:
            return
        self._ring = bool(on)
        self.setFixedHeight(self.H + (4 if on else 0))
        self.updateGeometry()
        self.update()

    def refresh(self) -> None:
        self._entries = self._hub.entries()
        es = self._entries
        if es:
            words = [e.text + (f" {round(e.progress * 100)}%" if e.progress is not None else "")
                     for e in es]
            self.setToolTip("\n".join(words))
            self.setAccessibleName(tr("notice_working") + ": " + "; ".join(words))
            self._fader.show()
            self._sync_spin()
        else:
            self._spin.stop()
            self._fader.hide()
        self.update()

    def text(self) -> str:
        return self._hub.line_text()

    def sizeHint(self) -> QSize:
        if self._ring:
            return QSize(self.H, self.H + 4)
        fm = QFontMetrics(self._font)
        return QSize(min(320, fm.horizontalAdvance(self.text()) + 84), self.H)

    def _sync_spin(self) -> None:
        busy = bool(self._entries) and self._entries[0].progress is None
        if busy and self.isVisible() and not reduced_motion():
            self._spin.start()
        else:
            self._spin.stop()

    def _turn(self) -> None:
        self._angle = (self._angle + 12) % 360
        self.update()

    def showEvent(self, e) -> None:      # noqa: N802 - Qt hook
        super().showEvent(e)
        self._sync_spin()

    def hideEvent(self, e) -> None:      # noqa: N802 - Qt hook
        self._spin.stop()
        super().hideEvent(e)

    def mousePressEvent(self, e) -> None:    # noqa: N802 - Qt hook
        if e.button() == Qt.MouseButton.LeftButton and self._entries:
            e.accept()
            self.open_panel.emit()
            return
        super().mousePressEvent(e)

    def keyPressEvent(self, e) -> None:      # noqa: N802 - Qt hook
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and self._entries:
            self.open_panel.emit()
            return
        super().keyPressEvent(e)

    def paintEvent(self, _e) -> None:    # noqa: N802 - Qt hook
        if not self._entries:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        e = self._entries[0]
        rad = corner_radius(10)
        p.setPen(Qt.PenStyle.NoPen)
        c = colours()
        p.setBrush(QColor(c["float_bg"] if self._floating else c["line_bg"]))
        if self.hasFocus():
            p.setPen(QPen(QColor(P["accent"]), 1.5))
        p.drawRoundedRect(r, rad, rad)
        if self._ring:
            d = 20.0
            ring = QRectF((r.width() - d) / 2, (r.height() - d) / 2 - 1, d, d)
            paint_progress(p, ring, e.progress, self._angle)
            n = len(self._entries)
            if n > 1:
                self._badge(p, QPointF(ring.right() + 1, ring.top() + 2), str(n))
            p.end()
            return
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        pad = 10.0
        ring_d = 22.0
        ring = QRectF(r.right() - pad - ring_d if rtl else r.left() + pad,
                      (r.height() - ring_d) / 2, ring_d, ring_d)
        # The kind's icon in a ring that turns while the progress is not
        # known (known, the bar under the words says it): one place for both.
        if e.progress is None:
            paint_progress(p, ring.adjusted(0.8, 0.8, -0.8, -0.8), None, self._angle)
        ic = 12.0
        paint_kind(p, e.kind, QRectF(ring.center().x() - ic / 2, ring.center().y() - ic / 2,
                                     ic, ic), QColor(c["icon"]))
        more = len(self._entries) - 1
        # "+N" as a small chip at the trailing end: the words take the room
        # between, whatever their direction.
        chip_w = 0.0
        if more:
            fs = QFontMetrics(self._small)
            chip_w = fs.horizontalAdvance(f"+{more}") + 12.0
            end = r.left() + pad if rtl else r.right() - pad
            chip = QRectF(end if rtl else end - chip_w, (r.height() - 18) / 2, chip_w, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c["track"]))
            p.drawRoundedRect(chip, 9, 9)
            p.setFont(self._small)
            p.setPen(QColor(c["float_more"] if self._floating else c["line_more"]))
            p.drawText(chip, int(Qt.AlignmentFlag.AlignCenter), f"+{more}")
        tx0 = pad + ring_d + 8
        tx1 = pad + (chip_w + 6 if more else 0)
        text_r = QRectF(r.left() + (tx1 if rtl else tx0), 0,
                        r.width() - tx0 - tx1, r.height() - (3 if e.progress is not None else 0))
        p.setFont(self._font)
        fm = QFontMetrics(self._font)
        pct = f" {round(e.progress * 100)}%" if e.progress is not None else ""
        room = int(text_r.width() - fm.horizontalAdvance(pct))
        words = fm.elidedText(e.text, Qt.TextElideMode.ElideRight, max(20, room)) + pct
        align = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if rtl
                                                 else Qt.AlignmentFlag.AlignLeft)
        p.setPen(QColor(c["float_text"] if self._floating else c["line_text"]))
        p.drawText(text_r, int(align), words)
        if e.progress is not None:
            bar = QRectF(r.left() + pad, r.bottom() - 5, r.width() - 2 * pad, 2.5)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c["track"]))
            p.drawRoundedRect(bar, 1.2, 1.2)
            fill = QRectF(bar)
            fill.setWidth(bar.width() * e.progress)
            if rtl:
                fill.moveRight(bar.right())
            p.setBrush(QColor(P["accent"]))
            p.drawRoundedRect(fill, 1.2, 1.2)
        p.end()

    def _badge(self, p: QPainter, at: QPointF, text: str) -> None:
        p.setFont(self._small)
        fm = QFontMetrics(self._small)
        w = max(14.0, fm.horizontalAdvance(text) + 6.0)
        b = QRectF(at.x() - w / 2, at.y() - 7, w, 14)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(P["accent"]))
        p.drawRoundedRect(b, 7, 7)
        p.setPen(QColor(ink_on(P["accent"])))
        p.drawText(b, int(Qt.AlignmentFlag.AlignCenter), text)


class ActivityPanel(QFrame):
    """Every running job, live: its icon, words, progress and - where its
    producer can stop it - Cancel. A child of the window over everything
    (it shows in a grab of the window); Esc or a click elsewhere closes it,
    and it closes by itself when the last job ends."""

    W = 340

    def __init__(self, hub: NoticeHub, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("NoticePanel")
        self._hub = hub
        self._anchor: QWidget | None = None
        self._beside = False
        self._angle = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        head = QLabel(tr("notice_working"))
        head.setObjectName("NoticePanelHead")
        lay.addWidget(head)
        self._rows = QVBoxLayout()
        self._rows.setSpacing(10)
        lay.addLayout(self._rows)
        self.setFixedWidth(self.W)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._spin = QTimer(self)
        self._spin.setInterval(ActivityLine.TICK_MS)
        self._spin.timeout.connect(self._turn)
        hub.changed.connect(self._changed)
        self.hide()

    def open_at(self, anchor: QWidget, beside: bool = False) -> None:
        self._anchor, self._beside = anchor, beside
        self.restyle()
        self._fill()
        if not self._hub.entries():
            return
        self.show()
        self.raise_()
        self._place()
        self.setFocus()
        QApplication.instance().installEventFilter(self)
        if not reduced_motion():
            self._spin.start()

    def close_panel(self) -> None:
        if self.isHidden():
            return
        QApplication.instance().removeEventFilter(self)
        self._spin.stop()
        self.hide()
        if self._anchor is not None and self._anchor.isVisible():
            self._anchor.setFocus()

    def restyle(self) -> None:
        rad = int(round(corner_radius(12)))
        c = colours()
        self.setStyleSheet(
            f"#NoticePanel {{ background: {c['surface']}; border: none; border-radius: {rad}px; }}"
            f"#NoticePanelHead {{ color: {c['muted']}; font-size: 11px; font-weight: 700;"
            " letter-spacing: 0.5px; background: transparent; }"
            f"#NoticeRowText {{ color: {c['title']}; font-size: 13px; background: transparent; }}"
            f"#NoticeRowPct {{ color: {c['muted']}; font-size: 12px; background: transparent; }}"
            f"#NoticeRowCancel {{ color: {c['action']}; font-size: 12px; font-weight: 600;"
            f" background: {c['action_bg']}; border: none; border-radius: 7px; padding: 3px 10px; }}"
            f"#NoticeRowCancel:hover {{ background: {c['action_hover']}; }}")

    def _changed(self) -> None:
        if self.isHidden():
            return
        if not self._hub.entries():
            self.close_panel()
            return
        self._fill()
        self._place()

    def _fill(self) -> None:
        while self._rows.count():
            item = self._rows.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.hide()
                w.deleteLater()
        for e in self._hub.entries():
            self._rows.addWidget(_PanelRow(self, e))
        self.adjustSize()

    def _place(self) -> None:
        a = self._anchor
        if a is None:
            return
        parent = self.parentWidget()
        self.adjustSize()
        top_left = a.mapTo(parent, QPoint(0, 0))
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        if self._beside:
            x = top_left.x() - self.width() - 8 if rtl else top_left.x() + a.width() + 8
            y = top_left.y() + a.height() - self.height()
        else:
            x = top_left.x() + a.width() - self.width() if rtl else top_left.x()
            y = top_left.y() - self.height() - 8
        x = max(8, min(x, parent.width() - self.width() - 8))
        y = max(8, y)
        self.move(x, y)

    def _turn(self) -> None:
        self._angle = (self._angle + 12) % 360
        for i in range(self._rows.count()):
            item = self._rows.itemAt(i)
            w = item.widget() if item is not None else None
            if isinstance(w, _PanelRow):
                w.angle = self._angle
                w.update()

    def keyPressEvent(self, e) -> None:      # noqa: N802 - Qt hook
        if e.key() == Qt.Key.Key_Escape:
            self.close_panel()
            return
        super().keyPressEvent(e)

    def eventFilter(self, obj, event) -> bool:   # noqa: N802 - Qt hook
        if event.type() == QEvent.Type.MouseButtonPress and not self.isHidden():
            gp = event.globalPosition().toPoint()
            inside = self.rect().contains(self.mapFromGlobal(gp))
            on_anchor = (self._anchor is not None
                         and self._anchor.rect().contains(self._anchor.mapFromGlobal(gp)))
            if not inside:
                self.close_panel()
                return on_anchor        # a click on the line closes it, no reopen
        return False


class _PanelRow(QWidget):
    """One job (or group) in the panel."""

    def __init__(self, panel: ActivityPanel, e: Entry) -> None:
        super().__init__(panel)
        self._e = e
        self._panel = panel
        self.angle = 0
        lay = QHBoxLayout(self)
        lay.setContentsMargins(26, 0, 0, 0)
        lay.setSpacing(8)
        col = QVBoxLayout()
        col.setSpacing(5)
        col.setContentsMargins(0, 0, 0, 0)
        text = QLabel(e.text)
        text.setObjectName("NoticeRowText")
        text.setWordWrap(True)
        col.addWidget(text)
        self._bar_h = 3 if e.progress is not None else 0
        if self._bar_h:
            col.addSpacing(self._bar_h + 2)
        lay.addLayout(col, 1)
        if e.progress is not None:
            pct = QLabel(f"{round(e.progress * 100)}%")
            pct.setObjectName("NoticeRowPct")
            lay.addWidget(pct, 0, Qt.AlignmentFlag.AlignTop)
        if e.cancellable:
            btn = QPushButton(tr("common_cancel"))
            btn.setObjectName("NoticeRowCancel")
            btn.setFlat(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setAccessibleName(tr("common_cancel") + ": " + e.text)
            btn.clicked.connect(self._cancel)
            lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignTop)
        self.setAccessibleName(e.text + (f" {round(e.progress * 100)}%"
                                         if e.progress is not None else ""))

    def _cancel(self) -> None:
        self._panel._hub.request_cancel(self._e.tokens)

    def paintEvent(self, _ev) -> None:   # noqa: N802 - Qt hook
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        ic = 16.0
        x = self.width() - ic if rtl else 0.0
        c = colours()
        paint_kind(p, self._e.kind, QRectF(x, 2, ic, ic), QColor(c["body"]))
        if self._e.progress is None:
            if self.height() > 30:
                paint_progress(p, QRectF(x + 3, 24, 10.0, 10.0), None, self.angle)
        else:
            left = 26.0
            right = self.width() - 64.0 if not self._e.cancellable else self.width() - 120.0
            bar = QRectF(0 if rtl else left, self.height() - 5, max(20.0, right - left), 3)
            if rtl:
                bar.moveRight(self.width() - left)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c["track"]))
            p.drawRoundedRect(bar, 1.5, 1.5)
            fill = QRectF(bar)
            fill.setWidth(bar.width() * self._e.progress)
            if rtl:
                fill.moveRight(bar.right())
            p.setBrush(QColor(P["accent"]))
            p.drawRoundedRect(fill, 1.5, 1.5)
        p.end()


class NoticeCard(QFrame):
    """One card: its mark, title, line, action and dismiss - flat, on the
    theme's raised surface, rounded like the app's other surfaces."""

    def __init__(self, hub: NoticeHub, card: Card, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NoticeCard")
        self._hub = hub
        self.card_id = card.id
        self._level = card.level
        self._whole = False
        self.fader = _Fader(self)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 10, 8, 10)
        outer.setSpacing(10)
        self.mark = QWidget()
        self.mark.setFixedSize(18, 19)
        self.mark.paintEvent = self._paint_mark
        outer.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.title = QLabel()
        self.title.setObjectName("NoticeTitle")
        self.title.setWordWrap(True)
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        head.addWidget(self.title, 1)
        self.count = QLabel()
        self.count.setObjectName("NoticeCount")
        head.addWidget(self.count, 0, Qt.AlignmentFlag.AlignTop)
        col.addLayout(head)
        self.label = QLabel()
        self.label.setObjectName("NoticeText")
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        col.addWidget(self.label)
        self.action_btn = QPushButton()
        self.action_btn.setObjectName("NoticeAction")
        self.action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action_btn.clicked.connect(self._act)
        # Its own row, gone with the button: a card without an action is no
        # taller than its words.
        self._action_row = QWidget()
        row = QHBoxLayout(self._action_row)
        row.setContentsMargins(0, 4, 0, 0)
        row.addWidget(self.action_btn, 0)
        row.addStretch(1)
        col.addWidget(self._action_row)
        outer.addLayout(col, 1)
        self.close_btn = QToolButton()
        self.close_btn.setObjectName("NoticeDismiss")
        self.close_btn.setText("✕")
        self.close_btn.setAutoRaise(True)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setToolTip(tr("notice_dismiss"))
        self.close_btn.setAccessibleName(tr("notice_dismiss"))
        self.close_btn.clicked.connect(self._dismiss)
        outer.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.show_card(card)

    def show_card(self, card: Card) -> None:
        self._level = card.level
        self.title.setText(card.title)
        self.label.setText(card.body)
        self.label.setVisible(bool(card.body))
        self.count.setText(f"×{card.count}" if card.count > 1 else "")
        self.count.setVisible(card.count > 1)
        self.setAccessibleName(card.title + (" " + card.body if card.body else ""))
        self.setAccessibleDescription(tr("notice_" + card.level))
        has = card.action is not None
        self.action_btn.setVisible(has and bool(card.action_label))
        self._action_row.setVisible(has and bool(card.action_label))
        self.action_btn.setText(card.action_label or "")
        self.action_btn.setAccessibleName(card.action_label or "")
        # A note written to be clicked ("click here"): the card is its button.
        self._whole = has and not card.action_label
        if self._whole:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        else:
            self.unsetCursor()
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.restyle()

    def restyle(self) -> None:
        rad = int(round(corner_radius(12)))
        c = colours()
        self.setStyleSheet(
            f"#NoticeCard {{ background: {c['surface']}; border: none; border-radius: {rad}px; }}"
            f"#NoticeCard:focus {{ background: {c['hover']}; }}"
            f"#NoticeTitle {{ color: {c['title']}; font-size: 13px; font-weight: 600;"
            " background: transparent; }"
            f"#NoticeText {{ color: {c['body']}; font-size: 13px; background: transparent; }}"
            f"#NoticeCount {{ color: {c['muted']}; font-size: 12px; font-weight: 600;"
            " background: transparent; }"
            f"#NoticeAction {{ color: {c['action']}; font-size: 13px; font-weight: 600;"
            f" background: {c['action_bg']}; border: none; border-radius: 8px;"
            " padding: 5px 12px; }"
            f"#NoticeAction:hover {{ background: {c['action_hover']}; }}"
            f"#NoticeDismiss {{ color: {c['dismiss']}; background: transparent;"
            " border: none; font-size: 12px; padding: 2px 5px; }"
            f"#NoticeDismiss:hover {{ color: {c['title']}; }}")
        self.mark.update()

    def _paint_mark(self, _e) -> None:
        p = QPainter(self.mark)
        paint_level(p, self._level, QRectF(1, 2, 16, 16))
        p.end()

    def _act(self) -> None:
        self._hub.activate(self.card_id)

    def _dismiss(self) -> None:
        self._hub.dismiss(self.card_id)

    def mousePressEvent(self, e) -> None:    # noqa: N802 - Qt hook
        if self._whole and e.button() == Qt.MouseButton.LeftButton:
            e.accept()
            self._act()
            return
        super().mousePressEvent(e)

    def keyPressEvent(self, e) -> None:      # noqa: N802 - Qt hook
        if self._whole and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._act()
            return
        if e.key() == Qt.Key.Key_Escape:
            self._dismiss()
            return
        super().keyPressEvent(e)


def _announce(widget: QWidget) -> None:
    """Tell a screen reader a card came (an alert, as a toast's role is)."""
    try:
        from PyQt6.QtGui import QAccessible, QAccessibleEvent
        QAccessible.updateAccessibility(QAccessibleEvent(widget, QAccessible.Event.Alert))
    except Exception:
        log.debug("notices: no accessibility event", exc_info=True)


class CardStack(QWidget):
    """The cards, stacked upward from the corner: at most three, the newest
    on top, older ones folded into "+N more" that opens them. A free child
    of the window (a splitter would lay it out as a pane); clicks pass
    around it; the pointer resting on it holds their time."""

    MAX_W = 380
    INSET = 16
    GAP = 8

    def __init__(self, hub: NoticeHub, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("NoticeCards")
        self._hub = hub
        self._anchor: QRect | None = None
        self._rtl = False
        self._open = False
        self._cards: dict[int, NoticeCard] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(self.GAP)
        self._lay = lay
        self.fold = QPushButton()
        self.fold.setObjectName("NoticeFold")
        self.fold.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fold.clicked.connect(self._toggle)
        lay.addWidget(self.fold, 0, Qt.AlignmentFlag.AlignLeading)
        self.fold.hide()
        hub.changed.connect(self.sync)
        self.hide()

    def card_widgets(self) -> list[NoticeCard]:
        """The cards drawn, from the top."""
        return [self._cards[c.id] for c in self._hub.shown_cards()
                if c.id in self._cards and not self._cards[c.id].isHidden()]

    def _toggle(self) -> None:
        self._open = not self._open
        self.sync()

    def sync(self) -> None:
        hub = self._hub
        order = hub.shown_cards()
        alive = {c.id for c in order}
        for cid in list(self._cards):
            if cid not in alive:
                w = self._cards.pop(cid)
                self._lay.removeWidget(w)
                w.hide()
                w.deleteLater()
        fold_at = len(order) if self._open else min(len(order), hub.MAX_SHOWN)
        if len(order) <= hub.MAX_SHOWN:
            self._open = False
        fresh: list[NoticeCard] = []
        for i, c in enumerate(order):
            w = self._cards.get(c.id)
            if w is None:
                w = NoticeCard(hub, c, self)
                self._cards[c.id] = w
                fresh.append(w)
            else:
                w.show_card(c)
            self._lay.removeWidget(w)
            self._lay.insertWidget(i, w)
            w.setVisible(i < fold_at)
        hidden = len(order) - fold_at
        self._lay.removeWidget(self.fold)
        self._lay.addWidget(self.fold, 0, Qt.AlignmentFlag.AlignLeading)
        self.fold.setText(("+" + tr("notice_more", n=hidden)) if hidden
                          else tr("notice_less"))
        self.fold.setVisible(hidden > 0 or self._open)
        self.restyle_fold()
        self.place()
        if self.isVisible():
            for w in fresh:
                if not w.isHidden():
                    w.fader.show()
                    _announce(w)

    def restyle_fold(self) -> None:
        self.fold.setStyleSheet(
            f"#NoticeFold {{ color: {colours()['body']}; background: {colours()['surface']};"
            " border: none; border-radius: 10px; padding: 4px 12px; font-size: 12px;"
            " font-weight: 600; }"
            f"#NoticeFold:hover {{ color: {colours()['title']}; }}")

    def set_anchor(self, rect: QRect | None, rtl: bool = False) -> None:
        """Where the corner is: the bottom of *rect* (window coordinates),
        on its leading side; None draws nothing."""
        self._anchor = QRect(rect) if rect is not None else None
        self._rtl = bool(rtl)
        self.place()

    def place(self) -> None:
        r = self._anchor
        hub = self._hub
        if r is None or hub.held or not hub.cards():
            if hub.held or r is None:
                hub.pause(False)
            self.hide()
            return
        w = max(200, min(self.MAX_W, r.width() - 2 * self.INSET))
        self._lay.invalidate()
        for card in self._cards.values():
            card.setFixedWidth(w)
        self.setFixedWidth(w)
        self._lay.activate()
        # The cards' words wrap: their height at this width, not at the
        # width a size hint guesses (a card of one line came out of two).
        h = (self._lay.heightForWidth(w) if self._lay.hasHeightForWidth()
             else self._lay.sizeHint().height())
        self.setFixedHeight(min(h, max(60, r.height() - 2 * self.INSET)))
        x = r.right() - self.INSET - w + 1 if self._rtl else r.left() + self.INSET
        y = r.bottom() - self.INSET - self.height() + 1
        self.move(x, y)
        self.show()
        self.raise_()
        self._lay.activate()

    def enterEvent(self, e) -> None:     # noqa: N802 - Qt hook
        self._hub.pause(True)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:     # noqa: N802 - Qt hook
        self._hub.pause(False)
        super().leaveEvent(e)

    def restyle(self) -> None:
        for w in self._cards.values():
            w.restyle()
        self.restyle_fold()


class Follow(QObject):
    """Calls *callback* once, soon after any watched widget moved, changed
    size, showed or hid - the corner follows the window's panes."""

    _EVENTS = (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show, QEvent.Type.Hide,
               QEvent.Type.LayoutDirectionChange)

    def __init__(self, callback: Callable[[], Any], parent: QObject) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(callback)

    def watch(self, obj: QObject) -> None:
        obj.installEventFilter(self)

    def poke(self) -> None:
        self._timer.start()

    def eventFilter(self, obj, event) -> bool:   # noqa: N802 - Qt hook
        if event.type() in self._EVENTS:
            self._timer.start()
        return False


