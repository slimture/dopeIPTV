"""The player's seek bar: a click lands where it points, a drag scrubs,
the seek goes on release, and hovering names the time - and on a timeline
the programme - under the pointer. Used by the player's bars, the cast
strip and multiview."""
from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QLabel, QSlider, QStyle, QStyleOptionSlider, QWidget


def _format_time(seconds: float | None) -> str:
    seconds = max(0, int(seconds or 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class _SeekSlider(QSlider):
    """Horizontal seek bar: click jumps to position, drag scrubs, seek on
    release, and hovering says what is under the cursor.

    Used by the player's own bar and by the cast strip - a slider you have to
    drag, when every other bar of its kind takes a click, reads as broken.
    """

    seek_requested = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        # Hover without a button held: a widget only gets move events without
        # one when it tracks the mouse itself, and the time under the cursor
        # is exactly what you want BEFORE deciding where to click.
        self.setMouseTracking(True)
        self.dragging = False
        self._markers: list[float] = []   # programme boundaries as 0..1 fractions
        self._segments: list = []         # (start_frac, end_frac, title, time)
        # Optional callable frac(0..1) -> label, so hovering the bar can show
        # the wall-clock time at that point (used by the timeshift timeline).
        self._time_for_frac = None

    def set_markers(self, fractions) -> None:
        """Programme-boundary ticks along the groove, as fractions 0..1 (left
        to right). Empty (the default) draws nothing, so the plain VOD seek bar
        is unaffected."""
        fr = sorted(f for f in fractions if 0.0 < f < 1.0)
        if fr != self._markers:
            self._markers = fr
            self.update()

    def set_segments(self, segments) -> None:
        """Programme segments for boundary ticks and hover tooltips, each a
        (start_frac, end_frac, title, time_label) tuple along the groove."""
        self._segments = list(segments)
        self.set_markers(s[0] for s in self._segments)

    def set_time_provider(self, fn) -> None:
        """Install a callable frac(0..1) -> str|None that names the time at a
        hover point, so hovering the bar tells you roughly where a click lands.
        Passing None disables it (the plain VOD bar shows no time)."""
        self._time_for_frac = fn

    def _segment_at(self, frac: float):
        for seg in self._segments:
            if seg[0] <= frac <= seg[1]:
                return seg
        return None

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._markers:
            return
        w, h = self.width(), self.height()
        painter = QPainter(self)
        pen = QPen(QColor(255, 255, 255, 130))
        pen.setWidth(1)
        painter.setPen(pen)
        y0, y1 = int(h * 0.28), int(h * 0.72)
        for f in self._markers:
            x = int(f * (w - 1))
            painter.drawLine(x, y0, x, y1)
        painter.end()

    def _value_for(self, event) -> int:
        # Through the style, not a plain fraction of the width. Qt insets
        # the groove by half a handle at each end, because the handle is
        # DRAWN centred on its position - so x/width put a click up to half
        # a handle away from where the handle then landed, which on a strip
        # a couple of hundred pixels wide is several per cent. Dragging hid
        # it: the eye corrects as the handle follows the pointer. Clicking
        # had nothing to correct against, so it simply went to the wrong
        # place, and 0:30 twice in a row was the same wrong place.
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        style = self.style()
        groove = style.subControlRect(
            QStyle.ComplexControl.CC_Slider, opt,
            QStyle.SubControl.SC_SliderGroove, self)
        handle = style.subControlRect(
            QStyle.ComplexControl.CC_Slider, opt,
            QStyle.SubControl.SC_SliderHandle, self)
        span = groove.width() - handle.width()
        pos = int(event.position().x()) - handle.width() // 2 - groove.x()
        return style.sliderValueFromPosition(
            self.minimum(), self.maximum(), pos, max(1, span),
            opt.upsideDown)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.setValue(self._value_for(event))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.dragging:
            self.setValue(self._value_for(event))
            self._show_segment_tooltip(event)
            event.accept()
            return
        self._show_segment_tooltip(event)
        super().mouseMoveEvent(event)

    def _hover_tip_label(self) -> QLabel:
        """A private tooltip label parented to the top-level window - QToolTip
        adds an OS-specific offset (large on macOS) that either buried the tip
        far below the bar or parked it on the pointer; an own label gives
        exact placement. Recreated if the slider was reparented (pop-out)."""
        win = self.window()
        lbl = getattr(self, "_hover_tip", None)
        try:
            stale = lbl is None or lbl.parent() is not win
        except RuntimeError:
            # The old tip's C++ object went away with a destroyed window
            # (pop-out reparent); treat it as gone and make a fresh one.
            lbl, stale = None, True
        if stale:
            if lbl is not None:
                lbl.deleteLater()
            lbl = QLabel(win)
            lbl.setObjectName("SeekHoverTip")
            lbl.setStyleSheet(
                "#SeekHoverTip { background: rgba(24,24,30,235);"
                " color: #ECECF1; border: 1px solid rgba(255,255,255,0.14);"
                " border-radius: 6px; padding: 3px 8px; font-size: 11px; }")
            lbl.hide()
            self._hover_tip = lbl
        return lbl

    def _hide_hover_tip(self) -> None:
        lbl = getattr(self, "_hover_tip", None)
        if lbl is not None:
            try:
                lbl.hide()
            except RuntimeError:
                # The tip is parented to the top-level window; when the slider
                # is reparented (pop-out) the old window is destroyed and takes
                # the C++ QLabel with it, leaving a dangling Python wrapper.
                # Drop the stale reference - a fresh tip is made on next hover.
                self._hover_tip = None

    def leaveEvent(self, event) -> None:
        self._hide_hover_tip()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        self._hide_hover_tip()
        super().hideEvent(event)

    def _show_segment_tooltip(self, event) -> None:
        """On the timeline, show the wall-clock time under the cursor (so you
        can see roughly where a click lands) and, when programme data is known,
        name what was on at that point. Placed just BELOW the bar, centred on
        the cursor - close enough to read as attached, out of the click path."""
        # The same scale the click uses, or the tip names one time and the
        # click lands on another.
        span = self.maximum() - self.minimum()
        frac = ((self._value_for(event) - self.minimum()) / span
                if span else 0.0)
        parts: list[str] = []
        if self._time_for_frac is not None:
            t = self._time_for_frac(frac)
            if t:
                parts.append(t)
        seg = self._segment_at(frac)
        if seg and seg[2]:
            parts.append(seg[2])
        if not parts:
            self._hide_hover_tip()
            return
        lbl = self._hover_tip_label()
        lbl.setText(" · ".join(parts))
        lbl.adjustSize()
        win = self.window()
        base = self.mapTo(win, QPoint(int(event.position().x()),
                                      self.height()))
        x = max(4, min(base.x() - lbl.width() // 2,
                       win.width() - lbl.width() - 4))
        y = min(base.y() + 6, win.height() - lbl.height() - 4)
        lbl.move(x, y)
        lbl.show()
        lbl.raise_()

    def mouseReleaseEvent(self, event) -> None:
        if self.dragging and event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            self.seek_requested.emit(self.value())
            event.accept()
            return
        super().mouseReleaseEvent(event)
