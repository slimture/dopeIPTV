"""The notices drawn (ui/notices.py holds their state), all at the
sidebar's foot: the activity line (its ring on the icon rail) with the count
of unread messages, the panel of the work and the messages, and the one card
for a decision - flat, in P's colours made to read on every theme, rounded
like the app's other surfaces, mirrored right to left, faded softly in and
out unless the desktop asks for less motion."""
from __future__ import annotations

from functools import partial
from typing import Any, Callable

from PyQt6.QtCore import (
    QEasingCurve, QEvent, QObject, QPoint, QPointF, QPropertyAnimation, QRectF,
    QSize, Qt, QTimer, pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMenu,
    QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from ..core.log import log
from ..i18n import tr
# Places to break in a long run (an address, a path): a run Qt cannot break
# is as wide as itself, and a row given the height of fewer lines than it
# took was cut off at the top and the foot.
from .message_box import breakable
from .notices import Entry, HistoryItem, Message, NoticeHub
from .theme import P, contrast, corner_radius, ink_on, tinted
from .widgets import plain_text

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
    elif kind == "note":
        p.drawEllipse(QRectF(x + 0.14 * s, y + 0.14 * s, 0.72 * s, 0.72 * s))
        p.drawLine(pt(0.34, 0.52), pt(0.46, 0.64))
        p.drawLine(pt(0.46, 0.64), pt(0.68, 0.4))
    elif kind == "bell":
        path = QPainterPath(pt(0.22, 0.72))
        path.lineTo(pt(0.78, 0.72))
        path.cubicTo(pt(0.7, 0.62), pt(0.72, 0.5), pt(0.72, 0.42))
        path.cubicTo(pt(0.72, 0.24), pt(0.6, 0.16), pt(0.5, 0.16))
        path.cubicTo(pt(0.4, 0.16), pt(0.28, 0.24), pt(0.28, 0.42))
        path.cubicTo(pt(0.28, 0.5), pt(0.3, 0.62), pt(0.22, 0.72))
        p.drawPath(path)
        p.drawLine(pt(0.44, 0.84), pt(0.56, 0.84))
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


def paint_history_mark(p: QPainter, level: str, r: QRectF) -> None:
    """A history item's mark: a message's for what was said, a tick in
    the success colour for work done, a cross for work cancelled."""
    if level not in ("done", "cancelled"):
        paint_level(p, level, r)
        return
    colour = QColor(P["ok"] if level == "done" else P["muted2"])
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(colour)
    p.drawEllipse(r)
    pen = QPen(QColor(ink_on(colour.name())), max(1.6, r.width() / 8.0))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    x, y, s = r.left(), r.top(), r.width()
    if level == "done":
        p.drawPolyline([QPointF(x + 0.28 * s, y + 0.52 * s), QPointF(x + 0.44 * s, y + 0.68 * s),
                        QPointF(x + 0.72 * s, y + 0.36 * s)])
    else:
        p.drawLine(QPointF(x + 0.34 * s, y + 0.34 * s), QPointF(x + 0.66 * s, y + 0.66 * s))
        p.drawLine(QPointF(x + 0.66 * s, y + 0.34 * s), QPointF(x + 0.34 * s, y + 0.66 * s))
    p.restore()


def history_when(at: float, now: float | None = None) -> str:
    """A history item's time: its clock time today, the day before it
    otherwise - in the app's language (QLocale)."""
    import time as _time
    from PyQt6.QtCore import QDateTime, QLocale
    now = _time.time() if now is None else now
    dt = QDateTime.fromSecsSinceEpoch(int(at))
    loc = QLocale()
    clock = dt.time().toString("HH:mm:ss")
    if _time.localtime(at)[:3] == _time.localtime(now)[:3]:
        return clock
    return loc.toString(dt.date(), QLocale.FormatType.ShortFormat) + " " + clock


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


def two_lines(text: str, font: QFont, width: int) -> str:
    """*text* wrapped to *width* in two lines at most, the second cut with
    an ellipsis when it runs on (a long word cut too)."""
    fm = QFontMetrics(font)
    width = max(20, int(width))
    words = str(text or "").split()
    if not words:
        return ""
    first: list[str] = []
    while words:
        trial = " ".join([*first, words[0]])
        if fm.horizontalAdvance(trial) <= width or not first:
            first.append(words.pop(0))
            if fm.horizontalAdvance(" ".join(first)) > width:
                break
        else:
            break
    line1 = fm.elidedText(" ".join(first), Qt.TextElideMode.ElideRight, width)
    if not words:
        return line1
    return line1 + "\n" + fm.elidedText(" ".join(words), Qt.TextElideMode.ElideRight, width)


def one_line(text: str, fm: QFontMetrics, width: int) -> str:
    """*text* cut to *width* with an ellipsis; words ending in " · N found"
    keep that count and give up the start instead ("Searching in Mo… · 12
    found"), while the count takes half the room at most."""
    width = max(20, int(width))
    if fm.horizontalAdvance(text) <= width:
        return text
    head, sep, tail = text.rpartition(" · ")
    if sep and head:
        tail_w = fm.horizontalAdvance(sep + tail)
        if tail_w <= width // 2:
            return fm.elidedText(head, Qt.TextElideMode.ElideRight, width - tail_w) + sep + tail
    return fm.elidedText(text, Qt.TextElideMode.ElideRight, width)


class ActivityLine(QWidget):
    """The line at the sidebar's foot: a passing note, else the running work
    (or, on the icon rail, a ring), and the count of unread messages on a
    dot - unless a bell beside it says that (``set_bell``): then the line
    shows only while something happens, and goes when nothing does. Enter,
    Space or a click opens the panel."""

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
        self._note = ""
        self._unread = 0
        self._bell = False
        self._fader = _Fader(self)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        # Its words follow what it shows (refresh); this until then.
        self.setAccessibleName(tr("notice_working"))
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.H)
        self._font = _font(13, QFont.Weight.Medium)
        self._small = _font(10, QFont.Weight.Bold)
        self._spin = QTimer(self)
        self._spin.setInterval(self.TICK_MS)
        self._spin.timeout.connect(self._turn)
        self.hide()

    def set_ring(self, on: bool) -> None:
        """The icon rail: a ring with the count, as wide as the rail's icons."""
        if bool(on) == self._ring:
            return
        self._ring = bool(on)
        self.setFixedHeight(self.H + (4 if on else 0))
        self.updateGeometry()
        self.update()

    def set_bell(self, on: bool) -> None:
        """A bell beside it carries the unread count (NoticeBell): the line
        itself is only the work and the passing words."""
        self._bell = bool(on)
        self.refresh()

    def refresh(self) -> None:
        hub = self._hub
        self._entries = hub.entries()
        self._note = hub.note_text()
        self._unread = 0 if self._bell else hub.unread()
        if self._entries or self._note or self._unread:
            self.setToolTip(plain_text(self._tip()))
            self.setAccessibleName(self._tip().replace("\n", "; "))
            self._fader.show()
            self._sync_spin()
        else:
            self._spin.stop()
            self._fader.hide()
        self.update()

    def _tip(self) -> str:
        lines = [self._note] if self._note else []
        lines += [e.text + (f" {round(e.progress * 100)}%" if e.progress is not None else "")
                  for e in self._entries]
        if self._unread:
            lines.append(tr("notice_unread", n=self._unread))
        return "\n".join(lines)

    def badge(self) -> int:
        """The count on its dot: the messages not read yet."""
        return self._unread

    def text(self) -> str:
        """What the line says now."""
        if self._ring:
            return ""
        if self._note:
            return self._note
        if self._entries:
            return self._hub.line_text()
        return tr("notice_unread", n=self._unread) if self._unread else ""

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

    def _opens(self) -> bool:
        """Whatever the line shows opens the panel - work, a message, or a
        passing note (kept in History, where the panel then opens)."""
        return bool(self._entries or self._note or self._unread) or bool(self._hub.messages())

    def _activate(self) -> None:
        """A click or Enter: the one job's own act (a recording asks to
        stop), else the panel."""
        act = self._hub.direct()
        if act is not None:
            try:
                act()
            except Exception:
                log.debug("notices: a job's act failed", exc_info=True)
            return
        self.open_panel.emit()

    def mousePressEvent(self, e) -> None:    # noqa: N802 - Qt hook
        if e.button() == Qt.MouseButton.LeftButton and self._opens():
            e.accept()
            self._activate()
            return
        super().mousePressEvent(e)

    def keyPressEvent(self, e) -> None:      # noqa: N802 - Qt hook
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and self._opens():
            self._activate()
            return
        super().keyPressEvent(e)

    def paintEvent(self, _e) -> None:    # noqa: N802 - Qt hook
        if not (self._entries or self._note or self._unread):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        c = colours()
        rad = corner_radius(10)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["float_bg"] if self._floating else c["line_bg"]))
        if self.hasFocus():
            p.setPen(QPen(QColor(P["accent"]), 1.5))
        p.drawRoundedRect(r, rad, rad)
        e = self._entries[0] if self._entries and not self._note else None
        kind = e.kind if e is not None else ("note" if self._note else "bell")
        if self._ring:
            d = 20.0
            ring = QRectF((r.width() - d) / 2, (r.height() - d) / 2 - 1, d, d)
            if e is not None:
                paint_progress(p, ring, e.progress, self._angle)
            ic = 10.0
            paint_kind(p, kind, QRectF(ring.center().x() - ic / 2, ring.center().y() - ic / 2,
                                       ic, ic), QColor(c["icon"]))
            if self._unread:
                self._badge(p, QPointF(ring.right() + 1, ring.top() + 2), str(self._unread))
            p.end()
            return
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        pad = 10.0
        ring_d = 22.0
        ring = QRectF(r.right() - pad - ring_d if rtl else r.left() + pad,
                      (r.height() - ring_d) / 2, ring_d, ring_d)
        # The kind's icon in a ring that turns while the progress is not
        # known (known, the bar under the words says it).
        if e is not None and e.progress is None:
            paint_progress(p, ring.adjusted(0.8, 0.8, -0.8, -0.8), None, self._angle)
        ic = 12.0
        paint_kind(p, kind, QRectF(ring.center().x() - ic / 2, ring.center().y() - ic / 2,
                                   ic, ic), QColor(c["icon"]))
        if self._unread:
            corner = QPointF(ring.left() + 3 if rtl else ring.right() - 3, ring.top() + 4)
            self._badge(p, corner, str(self._unread))
        more = len(self._entries) - 1 if e is not None else 0
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
        known = e is not None and e.progress is not None
        text_r = QRectF(r.left() + (tx1 if rtl else tx0), 0,
                        r.width() - tx0 - tx1, r.height() - (3 if known else 0))
        p.setFont(self._font)
        fm = QFontMetrics(self._font)
        pct = f" {round(e.progress * 100)}%" if known and e is not None and e.progress is not None \
            else ""
        room = int(text_r.width() - fm.horizontalAdvance(pct))
        words = one_line(self.text() if e is None else e.text, fm, room) + pct
        align = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if rtl
                                                 else Qt.AlignmentFlag.AlignLeft)
        quiet = e is None and not self._note
        if quiet:
            p.setPen(QColor(c["float_more"] if self._floating else c["line_more"]))
        else:
            p.setPen(QColor(c["float_text"] if self._floating else c["line_text"]))
        p.drawText(text_r, int(align), words)
        if known and e is not None and e.progress is not None:
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
        paint_badge(p, at, text, self._hub.unread_level(), self._small)


def paint_badge(p: QPainter, at: QPointF, text: str, level: str, font: QFont) -> None:
    """The count of unread messages on a dot centred on *at*, in the worst
    one's colour."""
    p.setFont(font)
    fm = QFontMetrics(font)
    w = max(14.0, fm.horizontalAdvance(text) + 6.0)
    b = QRectF(at.x() - w / 2, at.y() - 7, w, 14)
    fill = {"error": P["error"], "warning": P["warn"]}.get(level, P["accent"])
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(fill))
    p.drawRoundedRect(b, 7, 7)
    p.setPen(QColor(ink_on(fill)))
    p.drawText(b, int(Qt.AlignmentFlag.AlignCenter), text)


class NoticeBell(QPushButton):
    """What the notices fold to while nothing happens: a small bell at the
    sidebar's foot beside the source and Settings, the count of unread
    messages on its dot. A click opens the panel - the messages, else what
    was said since the app started (History) - and a click again closes it.
    The line over the foot (ActivityLine) comes only while work runs or a
    passing note is said."""

    GLYPH = 18

    def __init__(self, hub: NoticeHub, size: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SideIcon")
        self._hub = hub
        self._unread = 0
        self._small = _font(10, QFont.Weight.Bold)
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh()

    def badge(self) -> int:
        """The count on its dot: the messages not read yet."""
        return self._unread

    @staticmethod
    def name() -> str:
        """Its words: the menu entry's, without the dots."""
        return tr("menu_activity").rstrip("…. ")

    def refresh(self) -> None:
        self._unread = self._hub.unread()
        words = self.name()
        if self._unread:
            unread = tr("notice_unread", n=self._unread)
            self.setToolTip(f"{words}\n{unread}")
            self.setAccessibleName(f"{words}; {unread}")
        else:
            self.setToolTip(words)
            self.setAccessibleName(words)
        self.update()

    def paintEvent(self, e) -> None:     # noqa: N802 - Qt hook
        super().paintEvent(e)            # its background, from the style sheet
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = float(self.GLYPH)
        r = QRectF((self.width() - g) / 2, (self.height() - g) / 2, g, g)
        paint_kind(p, "bell", r, QColor(P["text2"]))
        if self._unread:
            rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
            at = QPointF(r.left() + 2 if rtl else r.right() - 2, r.top() + 3)
            paint_badge(p, at, str(self._unread), self._hub.unread_level(), self._small)
        p.end()


class ActivityPanel(QFrame):
    """The work and the messages, live, and what was said: two tabs. "Now" -
    "Working on" (each job's icon, words, progress and, where its producer
    can stop it, Cancel) and "Messages", the newest first, each with its
    action and a dismiss, the ones read before faded, Clear all. "History" -
    everything said since the app started and the person's work that ended
    (HistoryView). A child of the window over everything (it shows in a grab
    of the window); Esc or a click elsewhere closes it. It opens on Now
    while anything is there, else on History, and goes over to History when
    Now empties. Opening it marks the messages read."""

    W = 340
    #: Its width on the History tab (a line of time and words wants more).
    W_HISTORY = 380
    MAX_ROWS = 8

    def __init__(self, hub: NoticeHub, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("NoticePanel")
        self._hub = hub
        self._anchor: QWidget | None = None
        self._align: QWidget | None = None
        self._beside: QWidget | bool = False
        self._angle = 0
        self._fresh: set[int] = set()
        self._tab = "now"
        self._w = self.W
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        tabs = QHBoxLayout()
        tabs.setContentsMargins(0, 0, 0, 0)
        tabs.setSpacing(4)
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        self.tab_now = self._tab_button(tr("notice_tab_now"), "now")
        self.tab_history = self._tab_button(tr("nav_history"), "history")
        tabs.addWidget(self.tab_now)
        tabs.addWidget(self.tab_history)
        tabs.addStretch(1)
        # A way to close it in its corner, beside Esc and a click elsewhere.
        self.close_btn = QToolButton()
        self.close_btn.setObjectName("NoticeDismiss")
        self.close_btn.setText("✕")
        self.close_btn.setAutoRaise(True)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setToolTip(tr("common_close"))
        self.close_btn.setAccessibleName(tr("common_close"))
        self.close_btn.clicked.connect(self.close_panel)
        tabs.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(tabs)
        self._now = QWidget()
        now = QVBoxLayout(self._now)
        now.setContentsMargins(0, 0, 0, 0)
        now.setSpacing(10)
        self._work_head = QLabel(tr("notice_working"))
        self._work_head.setObjectName("NoticePanelHead")
        now.addWidget(self._work_head)
        self._rows = QVBoxLayout()
        self._rows.setSpacing(10)
        now.addLayout(self._rows)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self._msg_head = QLabel(tr("notice_messages"))
        self._msg_head.setObjectName("NoticePanelHead")
        head.addWidget(self._msg_head, 1)
        self.clear_btn = QPushButton(tr("notice_clear_all"))
        self.clear_btn.setObjectName("NoticeRowCancel")
        self.clear_btn.setFlat(True)
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.clicked.connect(hub.clear)
        head.addWidget(self.clear_btn)
        self._msg_head_row = QWidget()
        self._msg_head_row.setLayout(head)
        now.addWidget(self._msg_head_row)
        self._msgs = QVBoxLayout()
        self._msgs.setSpacing(8)
        now.addLayout(self._msgs)
        self._nothing = QLabel(tr("notice_now_nothing"))
        self._nothing.setObjectName("NoticeRowBody")
        self._nothing.setWordWrap(True)
        now.addWidget(self._nothing)
        lay.addWidget(self._now)
        self.history = HistoryView(hub, self)
        lay.addWidget(self.history)
        self.history.hide()
        self.setFixedWidth(self.W)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(tr("notice_messages"))
        self._spin = QTimer(self)
        self._spin.setInterval(ActivityLine.TICK_MS)
        self._spin.timeout.connect(self._turn)
        hub.changed.connect(self._changed)
        self.hide()

    def _tab_button(self, word: str, name: str) -> QPushButton:
        b = QPushButton(word)
        b.setObjectName("NoticeTab")
        b.setCheckable(True)
        b.setFlat(True)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setAccessibleName(word)
        b.clicked.connect(lambda _c=False, n=name: self.show_tab(n))
        self._tab_group.addButton(b)
        return b

    def _empty(self) -> bool:
        """Nothing on Now: no work, no message."""
        return not self._hub.entries() and not self._hub.messages()

    @property
    def tab(self) -> str:
        return self._tab

    def show_tab(self, name: str) -> None:
        """Now or History shown (the other hidden), the panel fitted."""
        self._tab = "history" if name == "history" else "now"
        hist = self._tab == "history"
        self.tab_now.setChecked(not hist)
        self.tab_history.setChecked(hist)
        self._now.setVisible(not hist)
        self.history.setVisible(hist)
        self._w = self.W_HISTORY if hist else self.W
        self.setFixedWidth(self._w)
        if hist:
            self.history.refresh()
        else:
            self._fill()
        if not self.isHidden():
            self._place()

    def open_at(self, anchor: QWidget, beside: QWidget | bool = False,
                tab: str | None = None, align: QWidget | None = None) -> None:
        """Opened upward from *anchor* - or beside it, from the edge of
        *beside* when that is a widget (the icon rail) - on *tab*, else on
        Now while anything is there and on History when not. With *align*
        its start lines up with that widget's (the bell's panel with the
        sidebar's foot, not hanging off the bell into the list)."""
        self._anchor, self._beside, self._align = anchor, beside, align
        self._fresh = {m.id for m in self._hub.messages() if not m.read}
        self.restyle()
        self._fill()
        self.show_tab(tab or ("history" if self._empty() else "now"))
        self.show()
        self.raise_()
        self._place()
        self.setFocus()
        QApplication.instance().installEventFilter(self)
        if not reduced_motion():
            self._spin.start()
        self._hub.mark_read()        # seen now: the count on the line goes

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
            f"#NoticeRowRead {{ color: {c['muted']}; font-size: 13px; background: transparent; }}"
            f"#NoticeRowBody {{ color: {c['body']}; font-size: 12px; background: transparent; }}"
            f"#NoticeRowPct {{ color: {c['muted']}; font-size: 12px; background: transparent; }}"
            f"#NoticeRowCancel {{ color: {c['action']}; font-size: 12px; font-weight: 600;"
            f" background: {c['action_bg']}; border: none; border-radius: 7px; padding: 3px 10px; }}"
            f"#NoticeRowCancel:hover {{ background: {c['action_hover']}; }}"
            f"#NoticeDismiss {{ color: {c['dismiss']}; background: transparent;"
            " border: none; font-size: 12px; padding: 2px 5px; }"
            f"#NoticeDismiss:hover {{ color: {c['title']}; }}"
            f"#NoticeTab, #NoticeChip {{ color: {c['muted']}; background: transparent;"
            f" border: none; border-radius: 7px; font-weight: 600; }}"
            "#NoticeTab { font-size: 12px; padding: 4px 10px; }"
            "#NoticeChip { font-size: 11px; padding: 3px 8px; }"
            f"#NoticeTab:hover, #NoticeChip:hover {{ color: {c['title']}; }}"
            f"#NoticeTab:checked, #NoticeChip:checked {{ color: {c['action']};"
            f" background: {c['action_bg']}; }}"
            f"#NoticeTab:focus, #NoticeChip:focus {{ border: 1px solid {P['accent']}; }}"
            "#NoticeHistScroll, #NoticeHistBody { background: transparent; border: none; }")

    def _changed(self) -> None:
        if self.isHidden():
            return
        if self._tab == "now":
            if self._empty():
                self.show_tab("history")
                return
            self._fill()
        self._place()

    @staticmethod
    def _clear(lay: QVBoxLayout) -> None:
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.hide()
                w.deleteLater()

    def _fill(self) -> None:
        self._clear(self._rows)
        self._clear(self._msgs)
        entries = self._hub.entries()
        for e in entries:
            self._add(self._rows, _PanelRow(self, e))
        self._work_head.setVisible(bool(entries))
        msgs = self._hub.messages()
        for m in msgs[:self.MAX_ROWS]:
            self._add(self._msgs, _MessageRow(self, m, fresh=m.id in self._fresh))
        if len(msgs) > self.MAX_ROWS:
            more = QLabel("+" + tr("notice_more", n=len(msgs) - self.MAX_ROWS))
            more.setObjectName("NoticeRowPct")
            self._add(self._msgs, more)
        self._msg_head_row.setVisible(bool(msgs))
        self._nothing.setVisible(not entries and not msgs)
        self._fit()

    @staticmethod
    def _add(lay: QVBoxLayout, w: QWidget) -> None:
        """A row in its place and shown at once: Qt shows a row added to a
        panel on screen only a moment later, and the panel's height, worked
        out before that, left it out - the rows were squeezed over each
        other into a panel a line tall."""
        lay.addWidget(w)
        w.show()

    def _fit(self) -> None:
        """As tall as its rows are at its own width: adjustSize alone sizes a
        wrapped line for the width it would like, wider than W, and a long
        message was cut off at the top and the foot."""
        lay = self.layout()
        if lay is not None:
            lay.invalidate()            # a row gone, a tab changed: smaller too
            lay.activate()
        self.adjustSize()
        if lay is not None and lay.hasHeightForWidth():
            need = lay.totalHeightForWidth(self._w)
            self.resize(self._w, need if need > 0 else self.height())

    def _place(self) -> None:
        a = self._anchor
        if a is None:
            return
        parent = self.parentWidget()
        self._fit()
        top_left = a.mapTo(parent, QPoint(0, 0))
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        if self._beside is not False and self._beside is not None:
            edge = self._beside if isinstance(self._beside, QWidget) else a
            ex = edge.mapTo(parent, QPoint(0, 0)).x()
            x = ex - self.width() - 8 if rtl else ex + edge.width() + 8
            y = top_left.y() + a.height() - self.height()
        else:
            ref = self._align if self._align is not None and self._align.isVisible() else a
            rx = ref.mapTo(parent, QPoint(0, 0)).x()
            x = rx + ref.width() - self.width() if rtl else rx
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


class HistoryView(QWidget):
    """The panel's History: what the app said since it started and the
    person's work that ended, the newest first - each with its mark, words
    and time; chips to see only errors, warnings, notes or work done; Copy
    (scrubbed of the person's values once more) and Clear. Built only while
    it shows; the hub keeps it in memory alone (NoticeHub.history)."""

    #: The chips: (key, the levels it shows - None all of them, its word).
    FILTERS: tuple[tuple[str, tuple[str, ...] | None, str], ...] = (
        ("all", None, "history_chip_all"),
        ("error", ("error",), "notice_hist_errors"),
        ("warning", ("warning",), "notice_hist_warnings"),
        ("info", ("info",), "notice_hist_notes"),
        ("done", ("done", "cancelled"), "rec_status_done"),
    )
    #: The tallest the list gets before it scrolls, and the most rows drawn.
    MAX_H = 380
    MAX_SHOWN = 300

    def __init__(self, hub: NoticeHub, panel: QWidget) -> None:
        super().__init__(panel)
        self._hub = hub
        self._levels: tuple[str, ...] | None = None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        chips = QHBoxLayout()
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(4)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.chips: dict[str, QPushButton] = {}
        for key, levels, word in self.FILTERS:
            b = QPushButton(tr(word))
            b.setObjectName("NoticeChip")
            b.setCheckable(True)
            b.setFlat(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setAccessibleName(tr(word))
            b.setChecked(levels is None)
            b.clicked.connect(partial(self._pick, levels))
            self._group.addButton(b)
            chips.addWidget(b)
            self.chips[key] = b
        chips.addStretch(1)
        v.addLayout(chips)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("NoticeHistScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._body = QWidget()
        self._body.setObjectName("NoticeHistBody")
        self._rows = QVBoxLayout(self._body)
        self._rows.setContentsMargins(0, 0, 6, 0)
        self._rows.setSpacing(10)
        self._rows.addStretch(1)
        self.scroll.setWidget(self._body)
        v.addWidget(self.scroll)
        self.empty = QLabel()
        self.empty.setObjectName("NoticeRowBody")
        self.empty.setWordWrap(True)
        v.addWidget(self.empty)
        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.setSpacing(6)
        foot.addStretch(1)
        # The list as it shows (the filter too), without the person's logins;
        # a row's own right click copies that one.
        self.copy_btn = self._foot_button(tr("notice_hist_copy_list"), self._copy)
        self.copy_btn.setToolTip(tr("notice_hist_copy_tip"))
        self.clear_btn = self._foot_button(tr("btn_clear"), hub.clear_history)
        foot.addWidget(self.copy_btn)
        foot.addWidget(self.clear_btn)
        v.addLayout(foot)
        hub.history_changed.connect(self._changed)

    @staticmethod
    def _foot_button(word: str, act: Callable[[], Any]) -> QPushButton:
        b = QPushButton(word)
        b.setObjectName("NoticeRowCancel")
        b.setFlat(True)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setAccessibleName(word)
        b.clicked.connect(lambda _c=False: act())
        return b

    def _pick(self, levels: tuple[str, ...] | None) -> None:
        self._levels = levels
        self.refresh()
        panel = self.parentWidget()
        if isinstance(panel, ActivityPanel) and not panel.isHidden():
            panel._place()

    def _changed(self) -> None:
        if self.isVisible():
            self.refresh()
            panel = self.parentWidget()
            if isinstance(panel, ActivityPanel) and not panel.isHidden():
                panel._place()

    def refresh(self) -> None:
        """The rows drawn again from the hub's history, as the chip says."""
        while self._rows.count() > 1:
            item = self._rows.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.hide()
                w.deleteLater()
        everything = self._hub.history()
        items = self._hub.history(self._levels)[:self.MAX_SHOWN]
        for i, h in enumerate(items):
            row = _HistoryRow(self._body, h, self)
            self._rows.insertWidget(i, row)
            row.show()
        self.scroll.setVisible(bool(items))
        self.empty.setText(tr("notice_hist_empty") if not everything
                           else tr("notice_hist_none"))
        self.empty.setVisible(not items)
        self.copy_btn.setEnabled(bool(items))
        self.clear_btn.setEnabled(bool(everything))
        if items:
            self._body.layout().activate()
            # The width it will have on the History tab (its own may still be
            # a hidden widget's guess, which wraps every line and is far taller).
            width = ActivityPanel.W_HISTORY - 28 - self.scroll.verticalScrollBar().sizeHint().width()
            need = self._rows.totalHeightForWidth(width - 6)
            self.scroll.setFixedHeight(max(40, min(self.MAX_H, need + 2)))
            self.scroll.verticalScrollBar().setValue(0)
        # Its own layout measured anew now: the panel asks its height next,
        # and a cached one is the list as it was before this.
        lay = self.layout()
        if lay is not None:
            lay.invalidate()
            lay.activate()
        self.updateGeometry()

    def _copy(self) -> None:
        """Copy list: what the list shows now, as plain text."""
        QApplication.clipboard().setText(self._text(None))
        self.copy_btn.setText(tr("problem_copied"))
        QTimer.singleShot(1500, self._copy_back)

    def copy_one(self, item_id: int) -> None:
        """A row's own Copy: that one, as Copy list writes it."""
        QApplication.clipboard().setText(self._text({item_id}))

    def row_menu(self, row: QWidget, pos: QPoint) -> None:
        menu = QMenu(row)
        menu.addAction(tr("problem_copy"), lambda: self.copy_one(row.item_id))
        menu.exec(row.mapToGlobal(pos))
        menu.deleteLater()

    def _text(self, ids: set[int] | None) -> str:
        names = {"error": tr("notice_error"), "warning": tr("notice_warning"),
                 "info": tr("notice_info"), "done": tr("rec_status_done"),
                 "cancelled": tr("rec_status_cancelled")}
        personal: Any = ()
        win = self.window()
        values = getattr(win, "_personal_values", None)
        if callable(values):
            try:
                personal = values()
            except Exception:
                log.debug("notices: the person's values not had for the copy", exc_info=True)
        return self._hub.history_text(self._levels, names, personal, ids=ids)

    def _copy_back(self) -> None:
        try:
            self.copy_btn.setText(tr("notice_hist_copy_list"))
        except RuntimeError:            # the window went meanwhile
            pass


class _HistoryRow(QWidget):
    """One thing in the history: its mark, its words and its time."""

    def __init__(self, parent: QWidget, h: HistoryItem, view: HistoryView | None = None) -> None:
        super().__init__(parent)
        self._level = h.level
        self.item_id = h.id
        # A right click: Copy this one (HistoryView.row_menu).
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        if view is not None:
            self.customContextMenuRequested.connect(lambda pos: view.row_menu(self, pos))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.mark = QWidget()
        self.mark.setFixedSize(16, 18)
        self.mark.paintEvent = self._paint_mark
        lay.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        head, _nl, body = h.text.partition("\n")
        text = QLabel(breakable(head))
        text.setObjectName("NoticeRowText")
        text.setWordWrap(True)
        text.setTextFormat(Qt.TextFormat.PlainText)     # a channel's, a server's words
        col.addWidget(text)
        if body.strip():
            more = QLabel(breakable(body.strip()))
            more.setObjectName("NoticeRowBody")
            more.setWordWrap(True)
            more.setTextFormat(Qt.TextFormat.PlainText)
            col.addWidget(more)
        stamp = history_when(h.at)
        if h.level in ("done", "cancelled"):
            stamp += " · " + tr("rec_status_" + h.level)
        when = QLabel(stamp)
        when.setObjectName("NoticeRowPct")
        when.setTextFormat(Qt.TextFormat.PlainText)
        col.addWidget(when)
        lay.addLayout(col, 1)
        self.text = h.text
        word = {"error": "notice_error", "warning": "notice_warning", "info": "notice_info",
                "done": "rec_status_done", "cancelled": "rec_status_cancelled"}[h.level]
        self.setAccessibleName(f"{tr(word)}: {h.text.replace(chr(10), ' - ')}, "
                               f"{history_when(h.at)}")

    def _paint_mark(self, _e) -> None:
        p = QPainter(self.mark)
        paint_history_mark(p, self._level, QRectF(1, 2, 14, 14))
        p.end()


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
        text.setTextFormat(Qt.TextFormat.PlainText)     # "Recording <a guide's title>"
        text.setObjectName("NoticeRowText")
        text.setWordWrap(True)
        col.addWidget(text)
        if e.progress is not None:
            col.addSpacing(5)
        lay.addLayout(col, 1)
        if e.progress is not None:
            pct = QLabel(f"{round(e.progress * 100)}%")
            pct.setObjectName("NoticeRowPct")
            lay.addWidget(pct, 0, Qt.AlignmentFlag.AlignTop)
        if e.cancellable:
            word = e.cancel_text or tr("common_cancel")
            btn = QPushButton(word)
            btn.setObjectName("NoticeRowCancel")
            btn.setFlat(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setAccessibleName(word + ": " + e.text)
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
        if self._e.progress is not None:
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


class _MessageRow(QWidget):
    """One message in the panel: its mark, words (faded once read), its
    action and a dismiss."""

    def __init__(self, panel: ActivityPanel, m: Message, fresh: bool) -> None:
        super().__init__(panel)
        self.msg_id = m.id
        self._panel = panel
        self._level = m.level
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.mark = QWidget()
        self.mark.setFixedSize(16, 18)
        self.mark.paintEvent = self._paint_mark
        lay.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        title = QLabel(breakable(m.title) + (f"  ×{m.count}" if m.count > 1 else ""))
        title.setObjectName("NoticeRowText" if fresh or m.decide else "NoticeRowRead")
        title.setWordWrap(True)
        title.setTextFormat(Qt.TextFormat.PlainText)
        col.addWidget(title)
        if m.body:
            body = QLabel(breakable(m.body))
            body.setObjectName("NoticeRowBody")
            body.setWordWrap(True)
            body.setTextFormat(Qt.TextFormat.PlainText)
            col.addWidget(body)
        if m.action is not None:
            act = QPushButton(m.action_label or tr("notice_open"))
            act.setObjectName("NoticeRowCancel")
            act.setFlat(True)
            act.setCursor(Qt.CursorShape.PointingHandCursor)
            act.setAccessibleName((m.action_label or tr("notice_open")) + ": " + m.title)
            act.clicked.connect(self._act)
            row = QHBoxLayout()
            row.setContentsMargins(0, 4, 0, 0)
            row.addWidget(act)
            row.addStretch(1)
            col.addLayout(row)
        lay.addLayout(col, 1)
        close = QToolButton()
        close.setObjectName("NoticeDismiss")
        close.setText("✕")
        close.setAutoRaise(True)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setToolTip(tr("notice_dismiss"))
        close.setAccessibleName(tr("notice_dismiss") + ": " + m.title)
        close.clicked.connect(self._dismiss)
        lay.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        self.dismiss_btn = close
        self.setAccessibleName(m.text)
        self.setAccessibleDescription(tr("notice_" + m.level))

    def _paint_mark(self, _e) -> None:
        p = QPainter(self.mark)
        paint_level(p, self._level, QRectF(1, 2, 14, 14))
        p.end()

    def _act(self) -> None:
        self._panel._hub.activate(self.msg_id)

    def _dismiss(self) -> None:
        self._panel._hub.dismiss(self.msg_id)


class DecisionCard(QFrame):
    """What needs a decision now, one at a time: a small card of the
    sidebar's width right above the activity line - its mark, its words in
    two lines at most (a click on them shows them whole, another folds them
    again; the whole of them in its tooltip too), one action, a dismiss, and
    "+N" when more wait behind it. In the sidebar's layout, so the
    categories above it only get shorter."""

    def __init__(self, hub: NoticeHub, parent: QWidget | None = None, *,
                 floating: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("NoticeDecision")
        self._hub = hub
        self._floating = floating
        self._msg: Message | None = None
        self._words = ""
        #: Its words shown whole (a click on them) - for this message alone.
        self._whole = False
        self.fader = _Fader(self)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 6, 8)
        outer.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.mark = QWidget()
        self.mark.setFixedSize(16, 18)
        self.mark.paintEvent = self._paint_mark
        top.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignTop)
        self.label = QLabel()
        self.label.setObjectName("NoticeDecisionText")
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.label.mousePressEvent = self._toggle_whole
        top.addWidget(self.label, 1)
        self.close_btn = QToolButton()
        self.close_btn.setObjectName("NoticeDismiss")
        self.close_btn.setText("✕")
        self.close_btn.setAutoRaise(True)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setToolTip(tr("notice_dismiss"))
        self.close_btn.setAccessibleName(tr("notice_dismiss"))
        self.close_btn.clicked.connect(self._dismiss)
        top.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(top)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(24, 0, 4, 0)
        self.action_btn = QPushButton(tr("notice_open"))     # the decision's own words (refresh)
        self.action_btn.setObjectName("NoticeDecisionAction")
        self.action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action_btn.clicked.connect(self._act)
        bottom.addWidget(self.action_btn, 0)
        bottom.addStretch(1)
        self.more = QLabel()
        self.more.setObjectName("NoticeDecisionMore")
        bottom.addWidget(self.more, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(bottom)
        self.hide()

    def refresh(self) -> bool:
        """The first decision waiting, drawn; False when none waits."""
        waiting = self._hub.decisions()
        if not waiting:
            self._msg = None
            self.hide()
            return False
        m = waiting[0]
        if m is not self._msg:
            self._whole = False
        self._msg = m
        # The title and its words apart, as the panel shows them in two.
        self._words = m.title + (" - " + m.body if m.body else "")
        self.setToolTip(plain_text(self._words))
        self.setAccessibleName(self._words)
        self.setAccessibleDescription(tr("notice_" + m.level))
        self.action_btn.setText(m.action_label or tr("notice_open"))
        self.action_btn.setAccessibleName(m.action_label or tr("notice_open"))
        self.action_btn.setVisible(m.action is not None)
        self.more.setText(f"+{len(waiting) - 1}" if len(waiting) > 1 else "")
        self.more.setVisible(len(waiting) > 1)
        self.restyle()
        self._wrap()
        if self.isHidden():
            self.fader.show()
            _announce(self)
        return True

    def text(self) -> str:
        return self._words

    def _wrap(self) -> None:
        if self._whole:
            self.label.setWordWrap(True)
            self.label.setText(breakable(self._words))
        else:
            self.label.setWordWrap(False)
            self.label.setText(two_lines(self._words, self._text_font(), self._room()))
        if self._whole:
            self.label.setFixedHeight(self.label.heightForWidth(self._room()))
        else:
            self.label.setMinimumHeight(0)
            self.label.setMaximumHeight(16777215)      # QWIDGETSIZE_MAX: as it was
        self.updateGeometry()

    def cut(self) -> bool:
        """Whether two lines cannot hold its words (a click shows them whole)."""
        shown = two_lines(self._words, self._text_font(), self._room())
        return " ".join(shown.split()) != " ".join(self._words.split())

    def _toggle_whole(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton or (not self._whole and not self.cut()):
            return
        self._whole = not self._whole
        self._wrap()

    def _room(self) -> int:
        """The words' width: the card's less its margins, the mark and the
        dismiss - before the sidebar has laid a new card out, the width its
        foot will give it, so its height is right the first time (and the
        categories above give up the right room at once)."""
        w = self.width()
        host = self.parentWidget()
        lay = host.layout() if host is not None and not self._floating else None
        if lay is not None and (self.isHidden() or w < 60):
            w = lay.contentsRect().width() or w
        m = self.layout().contentsMargins()
        top = self.layout().itemAt(0).layout()
        gaps = 2 * top.spacing() if top is not None else 16
        return max(40, w - m.left() - m.right() - self.mark.width() - gaps
                   - self.close_btn.sizeHint().width())

    def _text_font(self) -> QFont:
        return _font(13, QFont.Weight.Medium)

    def resizeEvent(self, e) -> None:    # noqa: N802 - Qt hook
        super().resizeEvent(e)
        if self._msg is not None:
            self._wrap()

    def restyle(self) -> None:
        rad = int(round(corner_radius(10)))
        c = colours()
        bg = c["float_bg"] if self._floating else c["line_bg"]
        text = c["float_text"] if self._floating else c["line_text"]
        self.setStyleSheet(
            f"#NoticeDecision {{ background: {bg}; border: none; border-radius: {rad}px; }}"
            f"#NoticeDecisionText {{ color: {text}; font-size: 13px; font-weight: 500;"
            " background: transparent; }"
            f"#NoticeDecisionMore {{ color: {c['line_more']}; font-size: 11px;"
            " font-weight: 700; background: transparent; }"
            f"#NoticeDecisionAction {{ color: {c['action']}; font-size: 12px; font-weight: 600;"
            f" background: {c['action_bg']}; border: none; border-radius: 7px;"
            " padding: 4px 10px; }"
            f"#NoticeDecisionAction:hover {{ background: {c['action_hover']}; }}"
            f"#NoticeDismiss {{ color: {c['dismiss']}; background: transparent;"
            " border: none; font-size: 12px; padding: 2px 4px; }"
            f"#NoticeDismiss:hover {{ color: {text}; }}")
        self.label.setFont(self._text_font())
        self.mark.update()

    def _paint_mark(self, _e) -> None:
        if self._msg is None:
            return
        p = QPainter(self.mark)
        paint_level(p, self._msg.level, QRectF(1, 2, 14, 14))
        p.end()

    def _act(self) -> None:
        if self._msg is not None:
            self._hub.activate(self._msg.id)

    def _dismiss(self) -> None:
        if self._msg is not None:
            self._hub.dismiss(self._msg.id)

    def keyPressEvent(self, e) -> None:      # noqa: N802 - Qt hook
        if e.key() == Qt.Key.Key_Escape:
            self._dismiss()
            return
        super().keyPressEvent(e)


def _announce(widget: QWidget) -> None:
    """Tell a screen reader something came that wants a decision (an
    alert, as a toast's role is)."""
    try:
        from PyQt6.QtGui import QAccessible, QAccessibleEvent
        QAccessible.updateAccessibility(QAccessibleEvent(widget, QAccessible.Event.Alert))
    except Exception:
        log.debug("notices: no accessibility event", exc_info=True)


class Follow(QObject):
    """Calls *callback* once, soon after any watched widget moved, changed
    size, showed or hid - the notices follow the window's panes."""

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
