"""The loading indicator over the video: a stream on its way shows that it
is coming, not a black picture (GitHub issue #27).

A stream asked to play - a channel, a film, an episode, catch-up, a file,
a station, a web page - shows it from a third of a second on, so a channel
that starts at once never flashes it, until mpv says playback began
(``playback-restart``; for a station that is when its sound starts). While
a stream plays, a wait for its cache (``paused-for-cache``, how full in
``cache-buffering-state``), a seek, or the rewind buffer opened again at
another point shows it after a second, so a short stall does not. A stop,
an error and the next stream take it away at once.

What mpv says comes on its event thread and reaches the window's thread
through a signal, as the stream banner's does (media/stream_info.py); the
player's own position poll ends what an event never came for. Nothing here
asks mpv to do anything, opens a connection or reads the disk: the logo or
poster is one the window already holds.

``LoadingOverlay`` draws it; ``LoadingMixin`` is the player's side of it.
Both fail quietly: the indicator is never worth a stream.
"""
from __future__ import annotations

import functools
import math
import weakref
from dataclasses import dataclass

from PyQt6.QtCore import QElapsedTimer, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap, QTextLayout, QTextOption,
)
from PyQt6.QtWidgets import QWidget

from ..core.log import log
from ..i18n import tr
from ..ui.theme import P, corner_radius


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


#: Why mpv ended a file when a playlist's address gave way to its entry
#: (MPV_END_FILE_REASON_REDIRECT): the same start goes on.
END_REDIRECT = 5


def _end_reason(event) -> int | None:
    """Why mpv ended a file (python-mpv's MpvEventEndFile), read on mpv's
    event thread while the event is still its own; None when it cannot
    say."""
    try:
        reason = event.data.reason
    except Exception:
        return None
    return reason if isinstance(reason, int) else None


def quiet(fn):
    """The indicator's ways in: anything that goes wrong in it is logged at
    debug and goes no further - never into playback."""
    @functools.wraps(fn)
    def run(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            log.debug("loading indicator: %s: %s", fn.__name__, e)
            return None
    return run


def _luminance(c: QColor) -> float:
    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c.redF()) + 0.7152 * lin(c.greenF()) + 0.0722 * lin(c.blueF())


def contrast(a: QColor, b: QColor) -> float:
    """WCAG's contrast ratio of two opaque colours."""
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


#: How much of the theme's background the plate is.
PLATE_ALPHA = 0.92
COMPACT_PLATE_ALPHA = 0.55


def colours() -> tuple[QColor, QColor, QColor]:
    """(the plate, the name, the muted line) in the theme in use. The muted
    line is the quietest of the theme's text colours that still reads on
    the plate over a black picture (4.5:1) - on a light theme's plate over
    black text3 can be too faint."""
    plate = QColor(P["bg"])
    over_black = QColor.fromRgbF(plate.redF() * PLATE_ALPHA, plate.greenF() * PLATE_ALPHA,
                                 plate.blueF() * PLATE_ALPHA)
    muted = QColor(P["text"])
    for key in ("text3", "text2"):
        c = QColor(P.get(key) or P["text"])
        if contrast(c, over_black) >= 4.5:
            muted = c
            break
    plate.setAlphaF(PLATE_ALPHA)
    return plate, QColor(P["text"]), muted


@dataclass
class _Layout:
    size: QSize
    radius: float
    spin: QRectF
    stroke: float
    logo: QRectF | None = None
    title: QRectF | None = None
    title_text: str = ""
    title_font: QFont | None = None
    note: QRectF | None = None
    note_font: QFont | None = None


class LoadingOverlay(QWidget):
    """A spinner, the channel's logo or the title's poster when there is one,
    the name and a muted line, on a soft plate in the theme's colours - or,
    *compact* (a multiview cell), the spinner alone on a small disc.

    It takes no focus and lets every click through to what is under it. The
    animation runs only while it is on screen."""

    FRAME_MS = 33               # ~30 fps
    COMPACT_FRAME_MS = 40       # ~25 fps, up to nine cells at once
    TURN_MS = 1150              # one turn of the arc
    BREATH_MS = 1800            # the arc grows and shrinks
    #: Where its middle sits, as a share of the picture's height: a little
    #: above the centre, clear of TV mode's info bar along the bottom.
    MIDDLE = 0.47

    def __init__(self, parent: QWidget | None = None, compact: bool = False) -> None:
        super().__init__(parent)
        self.compact = compact
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._pixmap: QPixmap | None = None
        self._title = ""
        self._note = ""
        self._reserve = ""
        self._area = QRect()
        self._geo: _Layout | None = None
        self._scaled: tuple | None = None
        self._colour_cache: tuple | None = None
        self._clock = QElapsedTimer()
        self._clock.start()
        #: Milliseconds since it was made; the spinner's angle (tests freeze it).
        self.elapsed = self._clock.elapsed
        self._timer = QTimer(self)
        self._timer.setInterval(self.COMPACT_FRAME_MS if compact else self.FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self.hide()

    # -- content and place -------------------------------------------------------

    def set_content(self, pixmap: QPixmap | None, title: str, note: str,
                    reserve: str = "") -> bool:
        """What it says. *reserve* is the widest the muted line may get
        (a percentage counting up), so the plate does not jitter. Whether
        anything changed."""
        pm = pixmap if isinstance(pixmap, QPixmap) and not pixmap.isNull() else None
        title, note, reserve = title or "", note or "", reserve or ""
        if (pm is self._pixmap and title == self._title and note == self._note
                and reserve == self._reserve):
            return False
        if pm is not self._pixmap:
            self._scaled = None
        self._pixmap, self._title, self._note, self._reserve = pm, title, note, reserve
        self.setAccessibleName(" - ".join(t for t in (title, note) if t))
        if not self._area.isNull():
            self.place(self._area)
        return True

    def place(self, area: QRect) -> None:
        """Centre it over *area* (the picture, in the parent's coordinates),
        sized for it: small in the docked player, larger full screen."""
        self._area = QRect(area)
        g = self._geo = self._layout(max(1, area.width()), max(1, area.height()))
        w, h = g.size.width(), g.size.height()
        x = area.x() + (area.width() - w) // 2
        if self.compact:
            y = area.y() + (area.height() - h) // 2
        else:
            y = area.y() + round(area.height() * self.MIDDLE) - h // 2
            y = max(area.y() + 8, min(y, area.y() + area.height() - h - 8))
        self.setGeometry(x, y, w, h)
        self.update()

    def spinner_rect(self) -> QRectF:
        return QRectF(self._geo.spin) if self._geo is not None else QRectF()

    def _layout(self, w: int, h: int) -> _Layout:
        if self.compact:
            d = _clamp(min(w, h) * 0.22, 36, 84)
            disc = math.ceil(d + _clamp(d * 0.28, 10, 22))
            stroke = max(3.0, d * 0.07)
            off = (disc - d) / 2 + stroke / 2
            return _Layout(QSize(disc, disc), disc / 2,
                           QRectF(off, off, d - stroke, d - stroke), stroke)
        s = _clamp(h / 800.0, 0.4, 1.35)
        d = round(_clamp(64 * s, 28, 88))
        stroke = max(2.5, d * 0.085)
        gap = round(_clamp(14 * s, 7, 20))
        pad = round(_clamp(22 * s, 12, 30))
        widest = max(80, min(w - 32, round(560 * max(s, 0.75))))
        text_w = max(40, widest - 2 * pad)
        tf = QFont(self.font())
        tf.setPixelSize(round(_clamp(22 * s, 13, 30)))
        tf.setWeight(QFont.Weight.DemiBold)
        nf = QFont(self.font())
        nf.setPixelSize(round(_clamp(16 * s, 11, 22)))
        tfm, nfm = QFontMetrics(tf), QFontMetrics(nf)
        wrap = int(Qt.AlignmentFlag.AlignHCenter) | int(Qt.TextFlag.TextWordWrap)
        nb = nfm.boundingRect(QRect(0, 0, text_w, 10_000), wrap, self._note) \
            if self._note else QRect()
        note_h = self._note_height(nf, nfm, text_w, wrap, nb.height()) if self._note else 0
        note_w = min(text_w, max(nb.width(), nfm.horizontalAdvance(self._reserve)
                                 if self._reserve else 0))
        logo = QSize()
        # Not in a picture lower than the smallest docked player's: there it
        # would reach the stream banner along the top.
        if self._pixmap is not None and h >= 240:
            box_h = round(_clamp(84 * s, 34, 112))
            box = QSize(min(text_w, round(box_h * 2.2)), box_h)
            logo = self._pixmap.deviceIndependentSize().toSize().scaled(
                box, Qt.AspectRatioMode.KeepAspectRatio)
        title_gap = round(_clamp(5 * s, 2, 7))
        # The logo goes first when the picture is too low for it all, then
        # the name: the spinner and the line under it always stay.
        for with_logo, with_title in ((True, True), (False, True), (False, False)):
            lw = logo.width() if with_logo and not logo.isEmpty() else 0
            lh = logo.height() if lw else 0
            title = tfm.elidedText(self._title, Qt.TextElideMode.ElideRight, text_w) \
                if with_title and self._title and h >= 120 else ""
            th = tfm.height() if title else 0
            content_w = max(d, lw, note_w, tfm.horizontalAdvance(title) if title else 0)
            width = min(widest, max(content_w + 2 * pad, d + 4 * pad))
            height = (pad + (lh + gap if lh else 0) + d
                      + (gap if title or note_h else 0)
                      + (th + (title_gap if note_h else 0) if title else 0)
                      + note_h + pad)
            if height <= h - 16:
                break
        y = float(pad)
        logo_r = None
        if lw:
            logo_r = QRectF((width - lw) / 2, y, lw, lh)
            y += lh + gap
        spin = QRectF((width - d) / 2 + stroke / 2, y + stroke / 2, d - stroke, d - stroke)
        y += d + (gap if title or note_h else 0)
        title_r = None
        if title:
            title_r = QRectF(pad, y, width - 2 * pad, th)
            y += th + (title_gap if note_h else 0)
        note_r = QRectF(pad, y, width - 2 * pad, note_h) if note_h else None
        radius = corner_radius(round(_clamp(16 * s, 10, 22)))
        return _Layout(QSize(int(width), int(math.ceil(height))), radius, spin, stroke,
                       logo_r, title_r, title, tf, note_r, nf)

    def _note_height(self, font: QFont, fm: QFontMetrics, width: int, flags: int,
                     whole: int) -> int:
        """The muted line's height at *width*: all of it (*whole*, as it is
        drawn) up to two lines, the first two of a longer one - measured as
        drawn too, so a fallback font's taller glyphs (CJK, the ellipsis)
        are not cut off at the bottom as two line spacings cut them."""
        layout = QTextLayout(self._note, font)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WordWrap)
        layout.setTextOption(option)
        layout.beginLayout()
        ends: list[int] = []
        while len(ends) < 3:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            ends.append(line.textStart() + line.textLength())
        layout.endLayout()
        if len(ends) <= 2:
            return whole
        two = fm.boundingRect(QRect(0, 0, width, 10_000), flags, self._note[:ends[1]])
        return min(two.height(), whole)

    # -- the animation: only while on screen -------------------------------------

    def setVisible(self, visible: bool) -> None:      # noqa: N802 - Qt hook
        super().setVisible(visible)
        self._run(visible)

    def showEvent(self, event) -> None:               # noqa: N802 - Qt hook
        super().showEvent(event)
        self._run(True)

    def hideEvent(self, event) -> None:               # noqa: N802 - Qt hook
        # Also when what it sits in hides (TV mode's page over the player).
        super().hideEvent(event)
        self._run(False)

    def _run(self, on: bool) -> None:
        if on and not self.isHidden():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()

    @quiet
    def _tick(self) -> None:
        if not self.isVisible():
            self._timer.stop()      # shown, but in nothing on screen: idle
            return
        g = self._geo
        if g is None:
            self.update()
            return
        pad = int(g.stroke) + 2
        self.update(g.spin.toAlignedRect().adjusted(-pad, -pad, pad, pad))

    # -- painting ---------------------------------------------------------------

    def paintEvent(self, _event) -> None:             # noqa: N802 - Qt hook
        g = self._geo
        if g is None:
            return
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            plate, text, muted = self._colours()
            p.setPen(Qt.PenStyle.NoPen)
            if self.compact:
                plate.setAlphaF(COMPACT_PLATE_ALPHA)
                p.setBrush(plate)
                p.drawEllipse(QRectF(self.rect()))
            else:
                p.setBrush(plate)
                p.drawRoundedRect(QRectF(self.rect()), g.radius, g.radius)
            if g.logo is not None:
                self._paint_logo(p, g.logo)
            self._paint_spinner(p, g)
            if g.title is not None and g.title_font is not None:
                p.setPen(text)
                p.setFont(g.title_font)
                p.drawText(g.title, int(Qt.AlignmentFlag.AlignCenter), g.title_text)
            if g.note is not None and g.note_font is not None:
                p.setPen(muted)
                p.setFont(g.note_font)
                p.save()
                p.setClipRect(g.note)
                p.drawText(g.note, int(Qt.AlignmentFlag.AlignHCenter)
                           | int(Qt.AlignmentFlag.AlignTop)
                           | int(Qt.TextFlag.TextWordWrap), self._note)
                p.restore()
        except Exception as e:
            log.debug("loading indicator not drawn: %s", e)
        finally:
            p.end()

    def _colours(self) -> tuple[QColor, QColor, QColor]:
        # Worked out again only when the theme changes (the spinner repaints
        # thirty times a second).
        key = (P.get("bg"), P.get("text"), P.get("text2"), P.get("text3"))
        cached = self._colour_cache
        if cached is None or cached[0] != key:
            cached = self._colour_cache = (key, colours())
        plate, text, muted = cached[1]
        return QColor(plate), QColor(text), QColor(muted)

    def _paint_logo(self, p: QPainter, r: QRectF) -> None:
        pm = self._pixmap
        if pm is None:
            return
        dpr = max(1.0, float(self.devicePixelRatioF()))
        key = (round(r.width()), round(r.height()), dpr, pm.cacheKey())
        if self._scaled is None or self._scaled[0] != key:
            scaled = pm.scaled(QSize(round(r.width() * dpr), round(r.height() * dpr)),
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            self._scaled = (key, scaled)
        scaled = self._scaled[1]
        sw, sh = scaled.width() / dpr, scaled.height() / dpr
        p.drawPixmap(QPointF(r.x() + (r.width() - sw) / 2,
                             r.y() + (r.height() - sh) / 2), scaled)

    def _paint_spinner(self, p: QPainter, g: _Layout) -> None:
        r = g.spin
        pen = QPen()
        pen.setWidthF(g.stroke)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        track = QColor(P["text"])
        track.setAlphaF(0.18 if self.compact else 0.16)
        pen.setColor(track)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(r)
        t = self.elapsed()
        turn = (t % self.TURN_MS) * 360.0 / self.TURN_MS
        breath = (t % self.BREATH_MS) / self.BREATH_MS
        sweep = 40.0 + 230.0 * (0.5 - 0.5 * math.cos(2 * math.pi * breath))
        pen.setColor(QColor(P["accent"]))
        p.setPen(pen)
        # Clockwise, its tail behind it; mirrored right to left.
        if self.layoutDirection() == Qt.LayoutDirection.RightToLeft:
            p.drawArc(r, round((90 + turn) * 16), round(-sweep * 16))
        else:
            p.drawArc(r, round((90 - turn) * 16), round(sweep * 16))


class LoadingMixin:
    """The player's side of the loading indicator. Mixed into
    EmbeddedPlayer, whose ``_ld_event`` signal carries what mpv says to the
    window's thread; it reads the player's ``current_url``, ``_paused``,
    ``video`` and ``_ov_surface``. Every way in is ``quiet``."""

    #: Before it shows at all, while a stream starts.
    LOADING_SHOW_MS = 300
    #: When "Connecting…" becomes "Still loading…".
    LOADING_SLOW_MS = 8000
    #: Before a wait while playing (the cache, a seek) shows it.
    LOADING_WAIT_MS = 1000

    # Until _init_loading: the player's __init__ draws its icons (and asks
    # about the centre button) before the overlays are made.
    _ld_overlay: LoadingOverlay | None = None
    _ld_phase: str | None = None
    _ld_buffering = False
    # (pixmap, reconnect) for the next play() - set_loading_art.
    _ld_armed: tuple = (None, False)

    def _init_loading(self) -> None:
        self._ld_overlay = LoadingOverlay(self)
        self._ld_pixmap: QPixmap | None = None
        self._ld_title = ""
        self._ld_reconnect = False
        # "start": play() asked, until playback began; "wait": a seek or a
        # reopened file while playing; None: neither.
        self._ld_phase = None
        self._ld_seen_start = False     # this play()'s start-file came
        self._ld_show_fired = False
        self._ld_slow = False
        self._ld_buffering = False      # paused-for-cache, as last said
        self._ld_pct: int | None = None
        self._ld_wait_fired = False
        self._ld_core = None
        self._ld_show_timer = self._ld_timer(self._ld_show_due)
        self._ld_slow_timer = self._ld_timer(self._ld_slow_due)
        self._ld_wait_timer = self._ld_timer(self._ld_wait_due)
        self._ld_event.connect(self._ld_on_event)

    def _ld_timer(self, slot) -> QTimer:
        t = QTimer(self)
        t.setSingleShot(True)
        t.timeout.connect(slot)
        return t

    # -- what the window says ------------------------------------------------------

    def set_loading_art(self, pixmap: QPixmap | None = None,
                        reconnect: bool = False) -> None:
        """Arm, for the next play() alone, the logo or poster it shows while
        that stream opens (one the window holds already; None: the name
        only), and whether it opens again what dropped ("Reconnecting…")."""
        pm = pixmap if isinstance(pixmap, QPixmap) and not pixmap.isNull() else None
        self._ld_armed = (pm, bool(reconnect))

    def hide_loading(self) -> None:
        """The window says why nothing plays: the indicator goes."""
        self._ld_clear()

    def loading_shown(self) -> bool:
        ov = self._ld_overlay
        return ov is not None and not ov.isHidden()

    def _ld_take_armed(self) -> tuple:
        armed, self._ld_armed = self._ld_armed, (None, False)
        return armed

    # -- mpv: its event thread to the window's ------------------------------------

    @quiet
    def _ld_hook(self, core) -> None:
        """Follow *core*'s start, restart, seek and end of a file and its
        cache waits - once per core. The callbacks only hand them on."""
        ref = getattr(self, "_ld_core", None)
        if ref is not None and ref() is core:
            return
        # Marked first: a registration that failed is not tried again at
        # every play, each adding the ones before it once more.
        self._ld_core = weakref.ref(core)
        me = weakref.ref(self)

        def forward(name: str):
            def on_event(event) -> None:               # mpv's event thread
                player = me()
                if player is not None:
                    try:
                        player._ld_event.emit(
                            name, _end_reason(event) if name == "end-file" else None)
                    except (RuntimeError, AttributeError):
                        pass
            return on_event

        def on_change(name, value) -> None:            # mpv's event thread
            player = me()
            if player is not None:
                try:
                    player._ld_event.emit(str(name), value)
                except (RuntimeError, AttributeError):
                    pass

        for name in ("start-file", "playback-restart", "seek", "end-file"):
            core.event_callback(name)(forward(name))
        core.observe_property("paused-for-cache", on_change)
        core.observe_property("cache-buffering-state", on_change)

    @quiet
    def _ld_on_event(self, name: str, value) -> None:
        """What mpv said, on the window's thread."""
        if name == "start-file":
            if self._ld_phase == "start":
                self._ld_seen_start = True
            elif self.current_url is not None:
                self._ld_wait()            # the rewind buffer opened again
        elif name == "playback-restart":
            if self._ld_phase == "start":
                if self._ld_seen_start:     # not the last stream's
                    self._ld_started()
                    return
            elif self._ld_phase == "wait":
                self._ld_phase = None
        elif name == "seek":
            if self._ld_phase is None and self.current_url is not None:
                self._ld_wait()
        elif name == "end-file":
            if self._ld_phase == "start" and value == END_REDIRECT:
                return      # a playlist's address gave way to its entry
            # Before this play()'s start-file it is the last stream's.
            if not (self._ld_phase == "start" and not self._ld_seen_start):
                self._ld_clear()
                return
        elif name == "paused-for-cache":
            on = bool(value) and self.current_url is not None
            if on and not self._ld_buffering:
                self._ld_buffering = True
                self._ld_arm_wait()
            elif not on:
                self._ld_buffering, self._ld_pct = False, None
        elif name == "cache-buffering-state":
            self._ld_pct = int(value) if isinstance(value, (int, float)) else None
        self._ld_refresh()

    # -- the state ---------------------------------------------------------------

    @quiet
    def _ld_begin(self, title: str, armed: tuple) -> None:
        """play() opens a stream. Shown already - the stream before was still
        on its way (a quick zap) or stalled (the app's reconnect) - it stays
        and changes to this one."""
        shown = self.loading_shown()
        self._ld_clear(keep_shown=shown)
        self._ld_pixmap, self._ld_reconnect = armed
        self._ld_title = title or ""
        self._ld_phase = "start"
        self._ld_slow_timer.start(self.LOADING_SLOW_MS)
        if shown:
            self._ld_show_fired = True
            self._ld_refresh()
        else:
            self._ld_show_timer.start(self.LOADING_SHOW_MS)

    @quiet
    def _ld_started(self) -> None:
        self._ld_phase = None
        self._ld_seen_start = self._ld_show_fired = self._ld_slow = False
        self._ld_show_timer.stop()
        self._ld_slow_timer.stop()
        if self._ld_buffering:
            self._ld_arm_wait()
        self._ld_refresh()

    def _ld_wait(self) -> None:
        self._ld_phase = "wait"
        self._ld_arm_wait()

    def _ld_arm_wait(self) -> None:
        if not self._ld_wait_fired and not self._ld_wait_timer.isActive():
            self._ld_wait_timer.start(self.LOADING_WAIT_MS)

    @quiet
    def _ld_clear(self, keep_shown: bool = False) -> None:
        """Nothing to wait for: everything back, the indicator hidden."""
        self._ld_phase = None
        self._ld_seen_start = self._ld_show_fired = self._ld_slow = False
        self._ld_buffering, self._ld_pct = False, None
        self._ld_wait_fired = False
        for t in (self._ld_show_timer, self._ld_slow_timer, self._ld_wait_timer):
            t.stop()
        if not keep_shown and self._ld_overlay is not None and self.loading_shown():
            self._ld_overlay.hide()

    @quiet
    def _ld_show_due(self) -> None:
        if self._ld_phase != "start":
            return
        if self._ld_core_playing():
            self._ld_started()          # it plays; the event is on its way
            return
        self._ld_show_fired = True
        self._ld_refresh()

    @quiet
    def _ld_slow_due(self) -> None:
        if self._ld_phase == "start":
            self._ld_slow = True
            self._ld_refresh()

    @quiet
    def _ld_wait_due(self) -> None:
        if self._ld_phase != "wait" and not self._ld_buffering:
            return
        if self._ld_core_playing():
            if self._ld_phase == "wait":
                self._ld_phase = None
            self._ld_buffering, self._ld_pct = False, None
        else:
            self._ld_wait_fired = True
        self._ld_refresh()

    @quiet
    def _ld_poll(self, playing: bool) -> None:
        """The position poll saw the stream play: whatever event did not
        come, nothing is loading."""
        if not playing:
            return
        if self._ld_phase == "start":
            self._ld_started()
            return
        if self._ld_wait_fired:
            if self._ld_phase == "wait":
                self._ld_phase = None
            self._ld_buffering, self._ld_pct = False, None
            self._ld_refresh()

    def _ld_core_playing(self) -> bool:
        """mpv plays the stream now (what the position poll reads)."""
        m = getattr(getattr(self, "video", None), "mpv", None)
        if m is None or self.current_url is None:
            return False
        try:
            return (not m.pause and not m.core_idle
                    and m.playback_time is not None)
        except Exception:
            return False

    def _ld_note(self) -> tuple[str, str] | None:
        """(the muted line, the widest it gets), or None for nothing."""
        if self._ld_phase == "start":
            if not self._ld_show_fired:
                return None
            if self._ld_reconnect:
                return tr("status_reconnecting"), ""
            if self._ld_slow:
                return tr("player_loading_slow"), ""
            return tr("player_loading_connecting"), ""
        if (self._ld_phase == "wait" or self._ld_buffering) and self._ld_wait_fired:
            pct = self._ld_pct
            if self._ld_buffering and pct is not None and 0 <= pct <= 100:
                return (tr("player_loading_buffering_pct", n=pct),
                        tr("player_loading_buffering_pct", n=100))
            return tr("player_loading_buffering"), ""
        return None

    @quiet
    def _ld_refresh(self) -> None:
        """Show, change or hide the indicator as the state says. Paused it
        gives way to the play button."""
        ov = self._ld_overlay
        if ov is None:
            return
        if self._ld_phase != "wait" and not self._ld_buffering:
            self._ld_wait_timer.stop()
            self._ld_wait_fired = False
        said = self._ld_note()
        if said is None or getattr(self, "_paused", False):
            if not ov.isHidden():
                ov.hide()
            return
        note, reserve = said
        ov.set_content(self._ld_pixmap, self._ld_title, note, reserve)
        if ov.isHidden():
            self._place_loading()
            ov.show()
            ov.raise_()
            # The centre play/pause disc would sit right under it, taking
            # its clicks: the player's, and the pop-out window's own beside
            # the mirror (a sibling of the indicator there).
            timer = getattr(self, "_center_hide_timer", None)
            if timer is not None:
                timer.stop()
            btn = getattr(self, "center_btn", None)
            if btn is not None:
                btn.hide()
            par = ov.parentWidget()
            if par is not None:
                for other in par.findChildren(QWidget, "CenterPlay",
                                              Qt.FindChildOption.FindDirectChildrenOnly):
                    other.hide()

    @quiet
    def _place_loading(self) -> None:
        """Over the surface that shows the picture now: the docked video,
        or the mirror in the pop-out window."""
        ov = self._ld_overlay
        surf = getattr(self, "_ov_surface", None)
        par = ov.parentWidget() if ov is not None else None
        if surf is None or par is None:
            return
        if surf is not par and not par.isAncestorOf(surf):
            return          # mid-move into the pop-out: placed once there
        tl = QPoint(0, 0) if surf is par else surf.mapTo(par, QPoint(0, 0))
        ov.place(QRect(tl, surf.size()))
