"""The cast card: what plays on the TV, in the video's place while a cast
runs - pure presentation (ui/mw_cast.py fills it and drives it; ui/
mw_cast_watch.py says its state).

Casting stops the player here (the receiver fetches the stream itself), so
the pane where the picture was shows this instead: the picture of what plays
(a channel's logo, a film's or a series' poster - one the window's image
loaders hold already, else its initials on the accent), its title, what is on
now or which episode, the TV it plays on with a cast or AirPlay mark, and its
state as a calm pill (``StatePill``: Playing, Paused, Buffering…,
Reconnecting… 1/2, Waiting for the Apple TV, Not playing) with Try again or
the AirPlay window beside it. Under that the transport as round buttons
(``RoundButton``: back, play/pause - the one on the accent -, forward,
Stop), the timeline (the player's own slider) with LIVE, and the volume with
the quieter square buttons (tracks, timeshift, the AirPlay window).

One look with the places (P only, flat, rounded, no borders); the parts on
one 4 px grid. Its content is at most ``MAX_W`` wide and centred in a wide
pane; in a narrow one the picture gets smaller, and below ``STACK_W`` it goes
above the words. Every control is a real button: Tab reaches it, Space
presses it, a screen reader names it. Mirrored right to left.
"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import (
    QElapsedTimer, QEvent, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QAbstractButton, QBoxLayout, QHBoxLayout, QLabel, QLayout, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from ..i18n import tr
from ..media.loading_overlay import paint_spinner
from ..media.seek_slider import _SeekSlider
from .media_cards import Pictures, initials_of, paint_picture
from .theme import P, corner_radius, ink_on, tinted
from .widgets import paint_strip_glyph, plain_text

#: The card's states (ui/mw_cast_watch.py picks one).
STARTING, PLAYING, PAUSED, BUFFERING = "starting", "playing", "paused", "buffering"
RECONNECTING, WAITING, FAILED, ENDED = "reconnecting", "waiting", "failed", "ended"
#: A seek on its way to the TV: back in time, or back to live.
MOVING, TO_LIVE = "moving", "to_live"
STATES = (STARTING, PLAYING, PAUSED, BUFFERING, RECONNECTING, WAITING, FAILED, ENDED,
          MOVING, TO_LIVE)
#: The states that turn the pill's spinner.
SPINNING = frozenset({STARTING, BUFFERING, RECONNECTING, WAITING, MOVING, TO_LIVE})
_WORDS = {STARTING: "cast_state_starting", PLAYING: "cast_state_playing",
          PAUSED: "cast_state_paused", BUFFERING: "cast_state_buffering",
          RECONNECTING: "cast_state_reconnecting", WAITING: "cast_state_waiting",
          FAILED: "cast_state_failed", ENDED: "cast_state_ended",
          MOVING: "cast_state_moving", TO_LIVE: "cast_state_to_live"}

#: The widest the content gets; a wider pane centres it.
MAX_W = 600
#: Below this content width the picture goes above the words.
STACK_W = 260
#: Below this content width the picture and the buttons are the smaller ones.
NARROW_W = 440
#: Room round the content, and between its parts.
PAD, GAP = 20, 16
#: The pictures: a logo's tile, a poster (2:3) - wide and narrow.
LOGO_W, LOGO_N = 112, 80
POSTER_W, POSTER_N = (88, 132), (64, 96)
#: The round buttons: the primary one and the rest - wide and narrow.
PRIMARY_W, PRIMARY_N = 60, 54
ROUND_W, ROUND_N = 46, 42
SQUARE = 34


def _wash(colour: str, ratio: float, over: str | None = None) -> tuple[str, str]:
    return tinted(colour, over or P["pane"], ratio, P["text"])


def card_ground() -> QColor:
    """The card's surface: a wash of the text colour over the pane - a
    shade lighter on a dark theme, darker on a light one."""
    return QColor(_wash(P["text"], 0.045)[0])


def state_text(state: str, attempt: int = 0, tries: int = 2) -> str:
    key = _WORDS.get(state, "cast_state_playing")
    if state == RECONNECTING:
        return tr(key, n=max(1, attempt), of=max(1, tries))
    return tr(key)


def state_colours(state: str) -> tuple[QColor, QColor, QColor]:
    """(fill, ink, mark) of the state pill: calm washes - the theme's green
    for playing, the accent while it waits, the warning colour while it
    reconnects, the error colour when it gave up, the text's own grey
    paused or ended."""
    base = {PLAYING: P["ok"], FAILED: P["error"], RECONNECTING: P["warn"],
            PAUSED: P["text"], ENDED: P["text"]}.get(state, P["accent"])
    fill, ink = _wash(base, 0.10 if base == P["text"] else 0.18, card_ground().name())
    mark = QColor(P["text2"] if base == P["text"] else ink)
    if state in (PLAYING, FAILED):
        mark = QColor(base)
    return QColor(fill), QColor(ink), mark


# -- the parts -------------------------------------------------------------------------

class StatePill(QWidget):
    """What the TV does, as a pill: a dot (or the spinner while it waits)
    and the words. Painted; the spinner turns only while it is on screen."""

    FRAME_MS = 33
    PX = 12
    H_PAD, MARK, MARK_GAP = 11, 8, 7

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = PLAYING
        self.text = state_text(PLAYING)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._clock = QElapsedTimer()
        self._clock.start()
        #: Milliseconds for the spinner (tests freeze it).
        self.elapsed: Callable[[], int] = self._clock.elapsed
        self._timer = QTimer(self)
        self._timer.setInterval(self.FRAME_MS)
        self._timer.timeout.connect(self.update)

    def _font(self) -> QFont:
        f = QFont(self.font())
        f.setPixelSize(self.PX)
        f.setWeight(QFont.Weight.DemiBold)
        return f

    def set_state(self, state: str, text: str) -> None:
        if state == self.state and text == self.text:
            return
        self.state, self.text = state, text
        self.setAccessibleName(text)
        self.updateGeometry()
        self._run()
        self.update()

    def sizeHint(self) -> QSize:                      # noqa: N802 - Qt hook
        fm = QFontMetrics(self._font())
        h = fm.ascent() + fm.descent() + 12
        w = self.H_PAD * 2 + self._mark_w(h) + self.MARK_GAP + fm.horizontalAdvance(self.text)
        return QSize(w, h)

    def minimumSizeHint(self) -> QSize:               # noqa: N802 - Qt hook
        return self.sizeHint()

    def _mark_w(self, h: int) -> int:
        return round(h * 0.46) if self.state in SPINNING else self.MARK

    def _run(self) -> None:
        if self.state in SPINNING and self.isVisible():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()

    def showEvent(self, event) -> None:               # noqa: N802 - Qt hook
        super().showEvent(event)
        self._run()

    def hideEvent(self, event) -> None:               # noqa: N802 - Qt hook
        super().hideEvent(event)
        self._timer.stop()

    def paintEvent(self, _event) -> None:             # noqa: N802 - Qt hook
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            fill, ink, mark = state_colours(self.state)
            r = QRectF(self.rect())
            h = r.height()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(r, h / 2, h / 2)
            rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
            mw = self._mark_w(int(h))
            mx = r.right() - self.H_PAD - mw if rtl else r.x() + self.H_PAD
            cy = r.center().y()
            if self.state in SPINNING:
                stroke = max(1.8, mw * 0.17)
                box = QRectF(mx + stroke / 2, cy - mw / 2 + stroke / 2, mw - stroke, mw - stroke)
                paint_spinner(p, box, stroke, int(self.elapsed()), rtl, 0.14, mark)
            elif self.state == PAUSED:
                p.setBrush(mark)
                bw = mw * 0.3
                for x in (mx + mw * 0.12, mx + mw * 0.58):
                    p.drawRoundedRect(QRectF(x, cy - mw * 0.45, bw, mw * 0.9), 1, 1)
            else:
                p.setBrush(mark)
                p.drawEllipse(QPointF(mx + mw / 2, cy), mw / 2, mw / 2)
            p.setFont(self._font())
            p.setPen(ink)
            gap = mw + self.MARK_GAP
            text_r = r.adjusted(self.H_PAD + (0 if rtl else gap), 0, -self.H_PAD - (gap if rtl else 0), 0)
            p.drawText(text_r, int(Qt.AlignmentFlag.AlignVCenter
                                   | (Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft)
                                   | Qt.AlignmentFlag.AlignAbsolute), self.text)
        finally:
            p.end()


class RoundButton(QAbstractButton):
    """A transport button: a drawn glyph on a round (or, *square*, a
    rounded square's) surface. *primary*: on the accent - play/pause, the
    one that matters most; *quiet*: no surface until the pointer is over
    it. Its words are its tooltip and what a screen reader says."""

    def __init__(self, glyph: str, tip: str, size: int = ROUND_W, primary: bool = False,
                 square: bool = False, quiet: bool = False, label: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.glyph, self.primary, self.square, self.quiet = glyph, primary, square, quiet
        self.label = label
        self._size = size
        self._keys = False          # the focus came by the keyboard: its ring shows
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.set_tip(tip)

    def set_tip(self, tip: str) -> None:
        self.setToolTip(tip)
        self.setAccessibleName(tip)

    def set_glyph(self, glyph: str, tip: str | None = None) -> None:
        if tip is not None:
            self.set_tip(tip)
        if glyph != self.glyph:
            self.glyph = glyph
            self.update()

    def set_size(self, size: int) -> None:
        if size != self._size:
            self._size = size
            self.setFixedSize(size, size)
            self.updateGeometry()

    def sizeHint(self) -> QSize:                      # noqa: N802 - Qt hook
        return QSize(self._size, self._size)

    def minimumSizeHint(self) -> QSize:               # noqa: N802 - Qt hook
        return self.sizeHint()

    def focusInEvent(self, event) -> None:            # noqa: N802 - Qt hook
        super().focusInEvent(event)
        self._keys = event.reason() in (Qt.FocusReason.TabFocusReason,
                                        Qt.FocusReason.BacktabFocusReason,
                                        Qt.FocusReason.ShortcutFocusReason)
        self.update()

    def focusOutEvent(self, event) -> None:           # noqa: N802 - Qt hook
        super().focusOutEvent(event)
        self._keys = False
        self.update()

    def enterEvent(self, event) -> None:              # noqa: N802 - Qt hook
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:              # noqa: N802 - Qt hook
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, _event) -> None:             # noqa: N802 - Qt hook
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            hover, down = self.underMouse(), self.isDown()
            ground = card_ground().name()
            if self.primary and self.isEnabled():
                fill: QColor | None = QColor(P["accent_hi"] if hover or down else P["accent"])
                ink = QColor(P["on_accent"])
            else:
                ratio = 0.20 if down else 0.14 if hover else 0.0 if self.quiet else 0.08
                fill = QColor(_wash(P["text"], ratio, ground)[0]) if ratio else None
                ink = QColor(P["text"] if self.isEnabled() else P["muted2"])
            radius = corner_radius(10) if self.square else r.height() / 2
            if fill is not None:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(fill)
                p.drawRoundedRect(r, radius, radius)
            if self.hasFocus() and self._keys:
                pen = QPen(QColor(P["accent"]), 2.0)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(1, 1, -1, -1), max(0.0, radius - 1), max(0.0, radius - 1))
            share = (0.62 if self.glyph in ("back", "forward") else 0.42 if self.primary
                     else 0.52 if self.square else 0.46)
            g = round(r.height() * share)
            p.translate(r.center().x() - g / 2, r.center().y() - g / 2)
            # A play triangle sits a touch right of the middle, as the eye
            # reads it centred.
            if self.glyph == "play":
                p.translate(g * 0.04, 0)
            glyph = self.glyph
            if self.layoutDirection() == Qt.LayoutDirection.RightToLeft:
                # The timeline runs right to left, so back winds the other way.
                glyph = {"back": "forward", "forward": "back"}.get(glyph, glyph)
            paint_strip_glyph(p, glyph, float(g), ink, self.label)
        finally:
            p.end()


class ArtTile(QWidget):
    """What plays, pictured: a poster covering its 2:3 box, a logo fitted
    on a tile, else the title's initials on the accent - rounded as the
    places' pictures are."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.pixmap: QPixmap | None = None
        self.shape = "logo"
        self.initials = ""
        self.pictures = Pictures(4 * 1024 * 1024)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_picture(self, pixmap: QPixmap | None, shape: str, initials: str) -> None:
        pm = pixmap if isinstance(pixmap, QPixmap) and not pixmap.isNull() else None
        if pm is self.pixmap and shape == self.shape and initials == self.initials:
            return
        self.pixmap, self.shape, self.initials = pm, shape, initials
        self.update()

    def paintEvent(self, _event) -> None:             # noqa: N802 - Qt hook
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            r = self.rect()
            radius = corner_radius(14 if self.shape == "logo" else 12)
            if self.pixmap is None:
                path = QPainterPath()
                path.addRoundedRect(QRectF(r), radius, radius)
                p.fillPath(path, QColor(P["accent"]))
                f = QFont(self.font())
                f.setPixelSize(max(14, min(34, r.height() // 3)))
                f.setWeight(QFont.Weight.Bold)
                p.setFont(f)
                p.setPen(QColor(ink_on(P["accent"])))
                p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), self.initials or "?")
                return
            if self.shape == "logo":
                path = QPainterPath()
                path.addRoundedRect(QRectF(r), radius, radius)
                p.fillPath(path, QColor(_wash(P["text"], 0.07, card_ground().name())[0]))
                inset = max(8, round(r.width() * 0.14))
                paint_picture(p, r.adjusted(inset, inset, -inset, -inset), self.pixmap,
                              self.pictures, 0, fit=True)
                return
            paint_picture(p, r, self.pixmap, self.pictures, radius)
        finally:
            p.end()


class _Centred(QLayout):
    """One widget, at most *max_w* wide, centred in the room - with its
    height for that width, so a wrapped line is paid for in height."""

    def __init__(self, parent: QWidget, max_w: int) -> None:
        super().__init__(parent)
        self._item = None
        self.max_w = max_w
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:                  # noqa: N802 - Qt hook
        self._item = item

    def count(self) -> int:
        return 1 if self._item is not None else 0

    def itemAt(self, i: int):                         # noqa: N802 - Qt hook
        return self._item if i == 0 else None

    def takeAt(self, i: int):                         # noqa: N802 - Qt hook
        if i == 0 and self._item is not None:
            item, self._item = self._item, None
            return item
        return None

    def _w(self, room: int) -> int:
        return max(1, min(room, self.max_w))

    def hasHeightForWidth(self) -> bool:              # noqa: N802 - Qt hook
        return True

    def heightForWidth(self, width: int) -> int:      # noqa: N802 - Qt hook
        if self._item is None:
            return 0
        return self._item.heightForWidth(self._w(width))

    def sizeHint(self) -> QSize:                      # noqa: N802 - Qt hook
        if self._item is None:
            return QSize()
        s = self._item.sizeHint()
        return QSize(min(s.width(), self.max_w), s.height())

    def minimumSize(self) -> QSize:                   # noqa: N802 - Qt hook
        if self._item is None:
            return QSize()
        return QSize(self._item.minimumSize().width(), 0)

    def setGeometry(self, rect: QRect) -> None:       # noqa: N802 - Qt hook
        super().setGeometry(rect)
        if self._item is None:
            return
        w = self._w(rect.width())
        x = rect.x() + (rect.width() - w) // 2
        self._item.setGeometry(QRect(x, rect.y(), w, rect.height()))


class ElidedLabel(QLabel):
    """A label on one line that ends in "…" where its room ends (a TV's
    long name, the timeline's programme) - never wider than it is given."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def minimumSizeHint(self) -> QSize:               # noqa: N802 - Qt hook
        return QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, _event) -> None:             # noqa: N802 - Qt hook
        p = QPainter(self)
        try:
            p.setLayoutDirection(Qt.LayoutDirection.LayoutDirectionAuto)
            r = self.contentsRect()
            fm = QFontMetrics(self.font())
            text = fm.elidedText(self.text(), Qt.TextElideMode.ElideRight, r.width())
            rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
            p.setPen(self.palette().color(self.foregroundRole()))
            p.drawText(r, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignAbsolute
                              | (Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft)),
                       text)
        finally:
            p.end()


def _row(spacing: int, *margins: int) -> tuple[QWidget, QHBoxLayout]:
    w = QWidget()
    w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    lay = QHBoxLayout(w)
    lay.setContentsMargins(*(margins or (0, 0, 0, 0)))
    lay.setSpacing(spacing)
    return w, lay


# -- the card ---------------------------------------------------------------------------

class CastCard(QWidget):
    """The card itself. Its parts are public: the window's cast code shows,
    hides and fills them (ui/mw_cast.py); ``sync`` takes a row away when
    nothing on it shows."""

    #: The pill's button: Try again (a cast that gave up) - or, waiting for
    #: an Apple TV, the AirPlay window.
    action_clicked = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CastCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        pol = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        pol.setHeightForWidth(True)
        self.setSizePolicy(pol)
        self.action_kind = ""
        self._narrow: bool | None = None
        self._stacked: bool | None = None

        self.body = QWidget(self)
        self.body.setObjectName("CastCardBody")
        self.body.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        bpol = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        bpol.setHeightForWidth(True)
        self.body.setSizePolicy(bpol)
        centre = _Centred(self, MAX_W + 2 * PAD)
        centre.addWidget(self.body)
        col = self._col = QVBoxLayout(self.body)
        col.setContentsMargins(PAD, PAD, PAD, PAD)
        col.setSpacing(GAP + 2)

        # -- the head: the picture and the words beside it
        self.head = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.head.setSpacing(GAP)
        self.art = ArtTile()
        self.head.addWidget(self.art, 0, Qt.AlignmentFlag.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(4)
        words.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel("")
        self.title.setObjectName("CastTitle")
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        words.addWidget(self.title)
        self.line = QLabel("")
        # A title, what is on, a receiver's words: never markup.
        for lbl in (self.title, self.line):
            lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.line.setObjectName("CastLine")
        self.line.setWordWrap(True)
        words.addWidget(self.line)
        dev, dev_lay = _row(6, 0, 4, 0, 0)
        self.device_mark = _Mark()
        dev_lay.addWidget(self.device_mark, 0, Qt.AlignmentFlag.AlignVCenter)
        self.device = ElidedLabel("")
        self.device.setObjectName("CastDevice")
        dev_lay.addWidget(self.device, 1)
        self.device_row = dev
        words.addWidget(dev)
        state, st_lay = _row(8, 0, 8, 0, 0)
        self.pill = StatePill()
        st_lay.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignVCenter)
        st_lay.addStretch(1)
        self.state_row = state
        words.addWidget(state)
        self.note = QLabel("")
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        self.note.setObjectName("CastNote")
        self.note.setWordWrap(True)
        self.note.hide()
        words.addWidget(self.note)
        self.action = QPushButton("")
        self.action.setObjectName("CastAction")
        self.action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action.clicked.connect(lambda: self.action_clicked.emit(self.action_kind))
        self.action.hide()
        act_row, act_lay = _row(0, 0, 4, 0, 0)
        act_lay.addWidget(self.action, 0)
        act_lay.addStretch(1)
        self.action_row = act_row
        act_row.hide()
        words.addWidget(act_row)
        words.addStretch(1)
        self.head.addLayout(words, 1)
        col.addLayout(self.head)

        # -- the transport
        self.transport, tl = _row(18)
        tl.addStretch(1)
        self.back = RoundButton("back", tr("cast_back_30"), label="30")
        self.pause = RoundButton("pause", tr("tooltip_pause_resume"), PRIMARY_W, primary=True)
        self.forward = RoundButton("forward", tr("cast_forward_30"), label="30")
        self.stop = RoundButton("stop", tr("cast_stop"))
        for b in (self.back, self.pause, self.forward, self.stop):
            tl.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
        tl.addStretch(1)
        col.addWidget(self.transport)

        # -- the timeline
        self.timeline = QWidget()
        self.timeline.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        tlv = QVBoxLayout(self.timeline)
        tlv.setContentsMargins(0, 0, 0, 0)
        tlv.setSpacing(4)
        self.seek = _SeekSlider()
        self.seek.setRange(0, 1000)
        self.seek.setToolTip(tr("cast_seek"))
        self.seek.setAccessibleName(tr("cast_seek"))
        self.seek.setMinimumWidth(80)
        tlv.addWidget(self.seek)
        under, ul = _row(8)
        self.time = ElidedLabel("")
        self.time.setObjectName("CastTime")
        ul.addWidget(self.time, 1)
        self.live = QPushButton(tr("mv_live"))
        self.live.setObjectName("CastLive")
        self.live.setCursor(Qt.CursorShape.PointingHandCursor)
        self.live.setToolTip(tr("ts_go_live"))
        self.live.setAccessibleName(tr("ts_go_live"))
        self.live.hide()
        ul.addWidget(self.live, 0, Qt.AlignmentFlag.AlignVCenter)
        tlv.addWidget(under)
        col.addWidget(self.timeline)

        # -- the volume and the quieter buttons
        self.bottom, bl = _row(8)
        self.mute = RoundButton("volume", tr("tooltip_mute_unmute"), SQUARE, square=True, quiet=True)
        bl.addWidget(self.mute, 0, Qt.AlignmentFlag.AlignVCenter)
        self.vol = _SeekSlider()
        self.vol.setRange(0, 100)
        self.vol.setValue(50)
        self.vol.setToolTip(tr("tooltip_volume"))
        self.vol.setAccessibleName(tr("tooltip_volume"))
        self.vol.setMinimumWidth(72)
        self.vol.setMaximumWidth(220)
        bl.addWidget(self.vol, 1)
        bl.addStretch(1)
        self.tracks = RoundButton("tracks", tr("cast_audio") + " / " + tr("cast_subtitles"),
                                  SQUARE, square=True, quiet=True)
        self.ts = RoundButton("rewind", tr("tooltip_timeshift"), SQUARE, square=True, quiet=True)
        self.airplay = RoundButton("airplay", tr("airplay_show_window"), SQUARE, square=True,
                                   quiet=True)
        self.airplay.hide()
        # The cast window on this cast: another TV, the picture setting.
        self.devices = RoundButton("cast", tr("cast_title"), SQUARE, square=True, quiet=True)
        for b in (self.tracks, self.ts, self.airplay, self.devices):
            bl.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addWidget(self.bottom)

        self.restyle()
        self._fit(MAX_W)
        self._align()

    def _align(self) -> None:
        """The wrapped words at the start of the line, absolute: a Latin
        title in a right-to-left window stays at its right, as the places'
        cards put it (each text in its own direction)."""
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        side = Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft
        for label in (self.title, self.line, self.note):
            label.setAlignment(side | Qt.AlignmentFlag.AlignAbsolute | Qt.AlignmentFlag.AlignTop)

    def changeEvent(self, event) -> None:             # noqa: N802 - Qt hook
        super().changeEvent(event)
        if event.type() == QEvent.Type.LayoutDirectionChange:
            self._align()

    # -- what it says ---------------------------------------------------------------

    def set_media(self, title: str, line: str, device: str, airplay: bool = False,
                  where: str = "") -> None:
        self.title.setText(title or "")
        self.title.setVisible(bool(title))
        self.line.setText(line or "")
        self.line.setVisible(bool(line))
        self.set_device(device, airplay, where)

    def set_device(self, device: str, airplay: bool = False, where: str = "") -> None:
        """The TV it plays on - and, in its tooltip, *where* that TV is (its
        address): which device the name stands for."""
        self.device.setText(device or "")
        self.device.setToolTip(plain_text(     # the device's own name
            f"{device}\n{where}" if device and where else device or ""))
        self.device.setAccessibleName(tr("cast_casting_to", name=device or ""))
        self.device_mark.set_glyph("airplay" if airplay else "cast")
        self.device_row.setVisible(bool(device))

    def set_art(self, pixmap: QPixmap | None, shape: str, title: str) -> None:
        self.art.set_picture(pixmap, shape, initials_of(title or "", 3 if shape == "logo" else 2))
        self._fit(self.body.width() or MAX_W, force=True)

    def set_state(self, state: str, attempt: int = 0, tries: int = 2, note: str = "",
                  action: str = "") -> None:
        """The pill: *state* (``STATES``), the try under way for
        Reconnecting; *note* a muted line under it; *action* "retry" (Try
        again) or "airplay" (the AirPlay window), "" for none."""
        self.pill.set_state(state, state_text(state, attempt, tries))
        self.note.setText(note or "")
        self.note.setVisible(bool(note))
        self.action_kind = action
        words = {"retry": tr("cast_try_again"), "airplay": tr("airplay_show_window")}
        self.action.setText(words.get(action, ""))
        self.action.setObjectName("Primary" if action == "retry" else "CastAction")
        self.action.style().unpolish(self.action)
        self.action.style().polish(self.action)
        self.action.setVisible(bool(action))
        self.action_row.setVisible(bool(action))
        self.setAccessibleDescription(self.pill.text)

    def sync(self) -> None:
        """A row whose every part is hidden takes no room (a broadcast with
        no way back has no timeline; AirPlay has no volume)."""
        self.transport.setVisible(any(not b.isHidden() for b in (
            self.back, self.pause, self.forward, self.stop)))
        self.timeline.setVisible(not self.seek.isHidden() or not self.time.isHidden()
                                 or not self.live.isHidden())
        self.bottom.setVisible(any(not w.isHidden() for w in (
            self.mute, self.vol, self.tracks, self.ts, self.airplay, self.devices)))
        self.updateGeometry()

    # -- look -----------------------------------------------------------------------

    def restyle(self) -> None:
        ground = card_ground().name()
        live_bg = P["rec"]
        self.setStyleSheet(
            f"QLabel#CastTitle {{ color:{P['text']}; font-weight:700; background:transparent; }}"
            f"QLabel#CastLine {{ color:{P['text2']}; font-size:13px; background:transparent; }}"
            f"QLabel#CastDevice {{ color:{P['muted']}; font-size:12px; background:transparent; }}"
            f"QLabel#CastNote {{ color:{P['text3']}; font-size:12px; background:transparent; }}"
            f"QLabel#CastTime {{ color:{P['text2']}; font-size:12px; background:transparent; }}"
            "QPushButton#CastAction, QPushButton#Primary { padding:7px 16px; font-size:12px;"
            " border-radius:9px; }"
            f"QPushButton#CastAction {{ background:{_wash(P['text'], 0.10, ground)[0]}; border:none; }}"
            f"QPushButton#CastAction:hover {{ background:{_wash(P['text'], 0.16, ground)[0]}; }}"
            f"QPushButton#CastLive {{ background:{live_bg}; color:{ink_on(live_bg)}; border:none;"
            " border-radius:10px; padding:3px 11px; font-size:11px; font-weight:700; }"
            f"QPushButton#CastLive:hover {{ background:{tinted(P['text'], live_bg, 0.15, P['text'])[0]}; }}")
        self._fit(self.body.width() or MAX_W, force=True)
        for w in self.findChildren(QWidget):
            w.update()
        self.update()

    def _fit(self, width: int, force: bool = False) -> None:
        """The parts sized for *width* (the content's): the smaller picture
        and buttons in a narrow pane, the picture above the words in the
        narrowest."""
        inner = width - 2 * PAD
        narrow, stacked = inner < NARROW_W, inner < STACK_W
        if not force and narrow == self._narrow and stacked == self._stacked:
            return
        self._narrow, self._stacked = narrow, stacked
        if self.art.shape == "logo":
            side = LOGO_N if narrow else LOGO_W
            self.art.setFixedSize(side, side)
        else:
            w, h = POSTER_N if narrow else POSTER_W
            self.art.setFixedSize(w, h)
        self.head.setDirection(QBoxLayout.Direction.TopToBottom if stacked
                               else QBoxLayout.Direction.LeftToRight)
        self.head.setAlignment(self.art, Qt.AlignmentFlag.AlignLeft if stacked
                               else Qt.AlignmentFlag.AlignTop)
        f = QFont(self.title.font())
        f.setPixelSize(17 if narrow else 20)
        f.setWeight(QFont.Weight.Bold)
        self.title.setFont(f)
        self.pause.set_size(PRIMARY_N if narrow else PRIMARY_W)
        for b in (self.back, self.forward, self.stop):
            b.set_size(ROUND_N if narrow else ROUND_W)
        self.transport.layout().setSpacing(12 if narrow else 18)
        self.updateGeometry()

    # -- Qt -------------------------------------------------------------------------

    def hasHeightForWidth(self) -> bool:              # noqa: N802 - Qt hook
        return True

    def heightForWidth(self, width: int) -> int:      # noqa: N802 - Qt hook
        lay = self.layout()
        return lay.heightForWidth(width) if lay is not None else super().heightForWidth(width)

    def resizeEvent(self, event) -> None:             # noqa: N802 - Qt hook
        super().resizeEvent(event)
        self._fit(min(event.size().width(), MAX_W + 2 * PAD))

    def paintEvent(self, _event) -> None:             # noqa: N802 - Qt hook
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(card_ground())
            radius = corner_radius(14)
            p.drawRoundedRect(QRectF(self.rect()), radius, radius)
        finally:
            p.end()


class _Mark(QWidget):
    """The small cast or AirPlay mark before the TV's name."""

    SIZE = 15

    def __init__(self) -> None:
        super().__init__()
        self.glyph = "cast"
        self.setFixedSize(self.SIZE, self.SIZE)

    def set_glyph(self, glyph: str) -> None:
        if glyph != self.glyph:
            self.glyph = glyph
            self.update()

    def paintEvent(self, _event) -> None:             # noqa: N802 - Qt hook
        p = QPainter(self)
        try:
            paint_strip_glyph(p, self.glyph, float(self.SIZE), QColor(P["muted"]))
        finally:
            p.end()
