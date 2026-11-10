"""Home's building blocks: the featured strip at the top, the shelves and
the cards on them. Pure presentation - HomePage (mw_home.py) fills them.

Every picture a card shows is composed into ONE opaque pixmap - artwork,
rank numeral, badges, progress - painted on the page background, so the
label under it keeps WA_OpaquePaintEvent and a page of a hundred cards
scrolls without compositing layers (see _Card)."""

from __future__ import annotations

import re
import time

from PyQt6.QtCore import (
    QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from ..i18n import tr
from .theme import HEART, P, corner_radius, on_accent, rounded

# Card geometry (image area; the card adds a text strip underneath).
POSTER_W, POSTER_H = 160, 240      # shelf posters
CHAN_W, CHAN_H = 264, 148          # live channel tiles
WIDE_W, WIDE_H = 296, 166          # continue watching
RANK_W = 64                        # room before a poster for its rank
RADIUS = 12
HERO_H = 440

_AA = QPainter.RenderHint.Antialiasing
_SMOOTH = QPainter.RenderHint.SmoothPixmapTransform
_FAST = Qt.TransformationMode.SmoothTransformation
_YEAR = re.compile(r"\s*[\(\[](19|20)(\d\d)[\)\]]\s*$")


def _alpha(key: str, a: float) -> QColor:
    c = QColor(P[key])
    c.setAlphaF(a)
    return c


def split_year(name: str) -> tuple[str, str]:
    """"Copper Sky (2022)" -> ("Copper Sky", "2022")."""
    m = _YEAR.search(name or "")
    if not m:
        return name or "", ""
    return name[:m.start()].rstrip(), m.group(1) + m.group(2)


def rating_text(it: dict) -> str:
    try:
        r = float(it.get("rating") or it.get("rating_5based") or 0)
    except (TypeError, ValueError):
        return ""
    if "rating" not in it and r:
        r *= 2
    return f"★ {r:.1f}" if 0 < r <= 10 else ""


def chevron_icon(size: int, color: str, right: bool = False) -> QIcon:
    """A drawn chevron (font glyphs render as tofu on some systems)."""
    scale = 3
    pm = QPixmap(size * scale, size * scale)
    pm.setDevicePixelRatio(float(scale))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(_AA, True)
    pen = QPen(QColor(color))
    pen.setWidthF(max(1.6, size * 0.14))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    a, b = (0.40, 0.62) if right else (0.60, 0.38)
    p.drawLine(QPointF(size * a, size * 0.26), QPointF(size * b, size * 0.50))
    p.drawLine(QPointF(size * b, size * 0.50), QPointF(size * a, size * 0.74))
    p.end()
    return QIcon(pm)


def play_icon(size: int, color: str) -> QIcon:
    scale = 3
    pm = QPixmap(size * scale, size * scale)
    pm.setDevicePixelRatio(float(scale))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(_AA, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    path = QPainterPath()
    path.moveTo(size * 0.26, size * 0.16)
    path.lineTo(size * 0.84, size * 0.50)
    path.lineTo(size * 0.26, size * 0.84)
    path.closeSubpath()
    p.drawPath(path)
    p.end()
    return QIcon(pm)


def _rounded(rect: QRectF, radius: float = RADIUS) -> QPainterPath:
    path = QPainterPath()
    r = corner_radius(radius)
    path.addRoundedRect(rect, r, r)
    return path


def _cover(pm: QPixmap, w: int, h: int) -> QPixmap:
    """*pm* scaled to cover w x h, cropped to it."""
    s = pm.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding, _FAST)
    return s.copy((s.width() - w) // 2, (s.height() - h) // 2, w, h)


def blurred(pm: QPixmap, w: int, h: int, factor: int = 18) -> QPixmap:
    """A soft, cheap blur of *pm* covering w x h: down to a few pixels and
    smoothly back up - an ambient backdrop, not a picture."""
    c = _cover(pm, max(1, w), max(1, h))
    small = c.scaled(max(2, w // factor), max(2, h // factor),
                     Qt.AspectRatioMode.IgnoreAspectRatio, _FAST)
    small = small.scaled(max(2, w // (factor // 2)), max(2, h // (factor // 2)),
                         Qt.AspectRatioMode.IgnoreAspectRatio, _FAST)
    return small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, _FAST)


def mean_color(pm: QPixmap) -> QColor:
    img = pm.toImage().scaled(1, 1, Qt.AspectRatioMode.IgnoreAspectRatio,
                              _FAST)
    return img.pixelColor(0, 0)


def _cover_pixmap(pm: QPixmap, w: int, h: int, radius: int = RADIUS) -> QPixmap:
    """Scale-crop *pm* to exactly w x h with rounded corners, the corners
    painted in the page background so the pixmap fills its whole rect."""
    out = QPixmap(w, h)
    out.fill(QColor(P["bg"]))
    p = QPainter(out)
    p.setRenderHint(_AA)
    p.setClipPath(_rounded(QRectF(0, 0, w, h), radius))
    p.drawPixmap(0, 0, _cover(pm, w, h))
    p.end()
    return out


def _contain_pixmap(pm: QPixmap, w: int, h: int, radius: int = RADIUS) -> QPixmap:
    """A channel logo, whole, on a panel (logos must not be cropped)."""
    out = QPixmap(w, h)
    out.fill(QColor(P["bg"]))
    p = QPainter(out)
    p.setRenderHint(_AA)
    _paint_tile(p, QRectF(0, 0, w, h), pm)
    p.end()
    return out


def _placeholder_pixmap(w: int, h: int, letter: str,
                        radius: int = RADIUS) -> QPixmap:
    out = QPixmap(w, h)
    out.fill(QColor(P["bg"]))
    p = QPainter(out)
    p.setRenderHint(_AA)
    _paint_placeholder(p, QRectF(0, 0, w, h), letter)
    p.end()
    return out


def _paint_placeholder(p: QPainter, r: QRectF, letter: str) -> None:
    path = _rounded(r)
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0, QColor(P["pane"]).lighter(130))
    g.setColorAt(1, QColor(P["pane"]).lighter(108))
    p.fillPath(path, g)
    f = QFont()
    f.setPixelSize(int(min(r.width(), r.height()) * 0.30))
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor(P["muted"]))
    p.drawText(r, Qt.AlignmentFlag.AlignCenter,
               (letter or "?").strip()[:1].upper())


def _paint_tile(p: QPainter, r: QRectF, logo: QPixmap | None) -> None:
    """A channel tile: a panel tinted by the logo's own colour, the logo
    whole in the middle."""
    path = _rounded(r)
    base = QColor(P["pane"]).lighter(122)
    tint = mean_color(logo) if logo is not None and not logo.isNull() else base
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    t = QColor(tint)
    t.setAlphaF(1.0)
    g.setColorAt(0, _mix(base, t, 0.30))
    g.setColorAt(1, _mix(base, t, 0.06))
    p.fillPath(path, g)
    if logo is None or logo.isNull():
        return
    s = logo.scaled(int(r.width() * 0.50), int(r.height() * 0.52),
                    Qt.AspectRatioMode.KeepAspectRatio, _FAST)
    p.save()
    p.setClipPath(path)
    p.drawPixmap(int(r.x() + (r.width() - s.width()) / 2),
                 int(r.y() + (r.height() - s.height()) / 2 - r.height() * 0.03),
                 s)
    p.restore()


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(int(a.red() + (b.red() - a.red()) * t),
                  int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def _pill(p: QPainter, x: float, y: float, text: str, bg: QColor,
          fg: QColor, px: int = 11, right: bool = False) -> None:
    f = QFont()
    f.setPixelSize(px)
    f.setBold(True)
    p.setFont(f)
    fm = p.fontMetrics()
    w = fm.horizontalAdvance(text) + 14
    h = fm.height() + 4
    if right:
        x -= w
    rect = QRectF(x, y, w, h)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(rect, h / 2, h / 2)
    p.setPen(fg)
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


def _bar(p: QPainter, r: QRectF, frac: float) -> None:
    """A progress bar along the bottom of *r*, inset."""
    frac = max(0.0, min(1.0, frac))
    track = QRectF(r.x() + 10, r.bottom() - 12, r.width() - 20, 4)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_alpha("text", 0.25))
    p.drawRoundedRect(track, 2, 2)
    if frac > 0:
        p.setBrush(QColor(P["accent"]))
        p.drawRoundedRect(QRectF(track.x(), track.y(),
                                 max(4.0, track.width() * frac), 4), 2, 2)


class _Card(QFrame):
    """One clickable tile: a picture, a title, an optional subtitle. The
    picture is one composed pixmap (see the module's docstring); *style* is
    "poster", "wide" (continue watching: the artwork whole on a blurred copy
    of itself) or "tile" (a channel logo). *rank* puts the chart position
    before a poster, *live* is a programme's (start, stop) for the LIVE pill
    and its progress, *progress* a percentage watched."""

    clicked = pyqtSignal()
    right_clicked = pyqtSignal()

    def __init__(self, w: int, h: int, title: str, subtitle: str = "",
                 progress: int | None = None, *, style: str = "poster",
                 badge: str = "", rank: int | None = None,
                 live: tuple[float, float] | None = None) -> None:
        super().__init__()
        self._w, self._h = w, h
        self._style, self._badge, self._rank = style, badge, rank
        self._live, self._progress = live, progress
        self._x0 = RANK_W if rank else 0
        self._title = title
        iw = w + self._x0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(iw)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.img = QLabel()
        self.img.setFixedSize(iw, h)
        self.img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Opaque: every pixmap shown here paints its whole rect (corners in
        # the page background), so Qt skips compositing beneath each card -
        # smooth scrolling on a page of ~100 cards.
        self.img.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._hover: QFrame | None = None
        self.img.setPixmap(self._compose(None))
        lay.addWidget(self.img)
        t = QLabel(title or "?")
        t.setStyleSheet("font-weight:600; font-size:13px;")
        t.setFixedWidth(iw)
        t.setContentsMargins(self._x0 + 2, 4, 0, 0)
        t.setWordWrap(False)
        lay.addWidget(t)
        self._elide(t, w - 4)
        if subtitle:
            s = QLabel(subtitle)
            s.setStyleSheet(f"color:{P['muted']}; font-size:12px;")
            s.setFixedWidth(iw)
            s.setContentsMargins(self._x0 + 2, 0, 0, 0)
            lay.addWidget(s)
            self._elide(s, w - 4)
        lay.addStretch(1)

    @staticmethod
    def _elide(lbl: QLabel, w: int) -> None:
        fm = lbl.fontMetrics()
        lbl.setText(fm.elidedText(lbl.text(), Qt.TextElideMode.ElideRight, w))

    def set_pixmap(self, pm: QPixmap, contain: bool = False) -> None:
        self.img.setText("")
        self.img.setPixmap(self._compose(pm, contain))

    # -- the picture -----------------------------------------------------------

    def _compose(self, pm: QPixmap | None, contain: bool = False) -> QPixmap:
        w, h, x0 = self._w, self._h, self._x0
        out = QPixmap(w + x0, h)
        out.fill(QColor(P["bg"]))
        p = QPainter(out)
        p.setRenderHint(_AA)
        p.setRenderHint(_SMOOTH)
        if self._rank:
            self._paint_rank(p, h)
        r = QRectF(x0, 0, w, h)
        path = _rounded(r)
        if contain or self._style == "tile":
            _paint_tile(p, r, pm)
        elif pm is None or pm.isNull():
            _paint_placeholder(p, r, self._title)
        elif self._style == "wide":
            self._paint_wide(p, r, path, pm)
        else:
            p.save()
            p.setClipPath(path)
            p.drawPixmap(int(r.x()), 0, _cover(pm, w, h))
            p.restore()
        if self._live:
            start, stop = self._live
            _pill(p, r.x() + 9, 9, tr("mv_live"), QColor(P["rec"]),
                  QColor("white"), 10)
            if stop > start:
                _bar(p, r, (time.time() - start) / (stop - start))
        elif self._progress is not None:
            _bar(p, r, self._progress / 100.0)
        if self._badge:
            _pill(p, r.right() - 8, 8, self._badge, _alpha("bg", 0.78),
                  QColor(P["text"]), 11, right=True)
        p.end()
        return out

    def _paint_wide(self, p: QPainter, r: QRectF, path: QPainterPath,
                    pm: QPixmap) -> None:
        """The artwork whole, beside a blurred copy of itself."""
        p.save()
        p.setClipPath(path)
        p.drawPixmap(int(r.x()), int(r.y()),
                     blurred(pm, int(r.width()), int(r.height())))
        g = QLinearGradient(r.topLeft(), r.topRight())
        g.setColorAt(0, _alpha("bg", 0.15))
        g.setColorAt(1, _alpha("bg", 0.55))
        p.fillRect(r, g)
        ph = r.height() - 22
        s = pm.scaled(int(r.width() * 0.6), int(ph),
                      Qt.AspectRatioMode.KeepAspectRatio, _FAST)
        art = QRectF(r.x() + 12, r.y() + (r.height() - s.height()) / 2 - 3,
                     s.width(), s.height())
        p.setClipPath(_rounded(art, 7))
        p.drawPixmap(art.toRect(), s)
        p.restore()
        # A play mark on the free side: this one carries on where it was.
        cx = art.right() + (r.right() - art.right()) / 2
        cy = r.center().y() - 4
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_alpha("bg", 0.55))
        p.drawEllipse(QPointF(cx, cy), 22, 22)
        tri = QPainterPath()
        tri.moveTo(cx - 6, cy - 10)
        tri.lineTo(cx + 11, cy)
        tri.lineTo(cx - 6, cy + 10)
        tri.closeSubpath()
        p.setBrush(QColor(P["text"]))
        p.drawPath(tri)

    def _paint_rank(self, p: QPainter, h: int) -> None:
        """The chart position, big and outlined, tucked behind the poster."""
        f = QFont()
        f.setPixelSize(int(h * 0.62))
        f.setBold(True)
        f.setWeight(QFont.Weight.Black)
        path = QPainterPath()
        text = str(self._rank)
        path.addText(0, 0, f, text)
        br = path.boundingRect()
        dx = self._x0 + 26 - br.right()
        dy = h - 6 - br.bottom()
        path.translate(dx, dy)
        p.setPen(QPen(_alpha("muted", 0.9), 3))
        p.setBrush(QColor(P["bg"]))
        p.drawPath(path)

    # -- hover and clicks ------------------------------------------------------

    def enterEvent(self, e) -> None:
        if self._hover is None:
            # A transparent frame that draws the accent border on TOP of the
            # artwork (a CSS border on the image label fought the rounded
            # pixmap and clipped at the corners).
            hv = QFrame(self.img)
            hv.setGeometry(self._x0, 0, self._w, self._h)
            hv.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            hv.setStyleSheet(rounded(
                f"background:transparent; border:2px solid {P['accent']};"
                f"border-radius:{RADIUS}px;"))
            self._hover = hv
        self._hover.raise_()
        self._hover.show()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        if self._hover is not None:
            self._hover.hide()
        super().leaveEvent(e)

    def mousePressEvent(self, e) -> None:
        # Nothing happens on press: a card acts on release, like a button.
        e.accept()

    def mouseReleaseEvent(self, e) -> None:
        # Act on RELEASE, and only once the event has been fully delivered.
        #
        # A click opens things that run their own event loop - the "resume
        # where you left off?" dialog, the recording guard, a context menu -
        # and it leaves Home, which deletes this very card. Emitting from
        # inside the press handler ran all of that while Qt still held the
        # press as an implicit mouse grab on this card. Closing the resume
        # dialog with the window's close button (where Qt sees no press or
        # release of its own inside the dialog) left that grab pointing at a
        # card that was deleted meanwhile, and the next mouse event crashed
        # the app in QApplication::notify - the macOS crash from "Continue
        # watching". Released and deferred, the click is over and the grab
        # is gone before anything opens.
        e.accept()
        if not self.rect().contains(e.position().toPoint()):
            return                     # dragged off the card: no click
        if e.button() == Qt.MouseButton.LeftButton:
            sig = self.clicked
        elif e.button() == Qt.MouseButton.RightButton:
            sig = self.right_clicked
        else:
            return

        def fire() -> None:
            from PyQt6 import sip
            if not sip.isdeleted(self):
                sig.emit()
        QTimer.singleShot(0, fire)


def _round_button(icon: QIcon, size: int = 30) -> QPushButton:
    b = QPushButton()
    b.setIcon(icon)
    b.setFixedSize(size, size)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setStyleSheet(rounded(
        "QPushButton { background:%s; border:1px solid %s; border-radius:%dpx; }"
        "QPushButton:hover { border-color:%s; }"
        "QPushButton:disabled { background:transparent; border-color:%s; }" % (
            QColor(P["pane"]).lighter(118).name(),
            QColor(P["pane"]).lighter(140).name(), size // 2, P["accent"],
            QColor(P["pane"]).lighter(118).name())))
    return b


class _Shelf(QWidget):
    """A titled, horizontally scrolling row of cards. No scrollbar: the
    arrows by the title page through it (and a sideways swipe does too);
    "See all" opens the section it samples, when there is one."""

    def __init__(self, title: str, more=None) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        head_row = QHBoxLayout()
        head_row.setContentsMargins(0, 0, 0, 0)
        head_row.setSpacing(8)
        head = QLabel(title)
        # Opaque background: a transparent label ghosts on macOS when the Home
        # canvas scrolls (the old glyphs aren't cleared, so the title paints on
        # top of itself and reads as garbled, overlapping text).
        head.setAutoFillBackground(True)
        head.setStyleSheet(
            f"font-size:19px; font-weight:700; background:{P['bg']};")
        head_row.addWidget(head)
        head_row.addStretch(1)
        if more is not None:
            see = QPushButton(tr("home_see_all"))
            see.setCursor(Qt.CursorShape.PointingHandCursor)
            see.setStyleSheet(
                "QPushButton { background:transparent; border:none;"
                f" color:{P['muted']}; font-size:13px; font-weight:600;"
                " padding:4px 6px; }"
                f"QPushButton:hover {{ color:{P['accent']}; }}")
            see.clicked.connect(more)
            head_row.addWidget(see)
        self._prev = _round_button(chevron_icon(14, P["text"]))
        self._next = _round_button(chevron_icon(14, P["text"], right=True))
        self._prev.clicked.connect(lambda: self._page(-1))
        self._next.clicked.connect(lambda: self._page(1))
        head_row.addWidget(self._prev)
        head_row.addWidget(self._next)
        lay.addLayout(head_row)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Opaque backgrounds on the viewport + row: without them a horizontal
        # scroll on macOS leaves the previous cards behind as trails.
        self._scroll.viewport().setStyleSheet(f"background:{P['bg']};")
        self._row = QWidget()
        self._row.setAutoFillBackground(True)
        self._row.setStyleSheet(f"background:{P['bg']};")
        self._h = QHBoxLayout(self._row)
        self._h.setContentsMargins(0, 0, 0, 0)
        self._h.setSpacing(16)
        self._h.addStretch(1)
        self._scroll.setWidget(self._row)
        lay.addWidget(self._scroll)
        bar = self._scroll.horizontalScrollBar()
        bar.rangeChanged.connect(lambda *_: self._sync_arrows())
        bar.valueChanged.connect(lambda *_: self._sync_arrows())
        self._anim: QPropertyAnimation | None = None
        self._sync_arrows()

    def add(self, card: _Card) -> None:
        self._h.insertWidget(self._h.count() - 1, card)

    def finish(self, img_h: int) -> None:
        # Height = image + the title and subtitle under it.
        self._scroll.setFixedHeight(img_h + 50)

    def count(self) -> int:
        return self._h.count() - 1

    def _page(self, step: int) -> None:
        bar = self._scroll.horizontalScrollBar()
        target = max(bar.minimum(), min(bar.maximum(), bar.value() + step * int(
            self._scroll.viewport().width() * 0.85)))
        anim = QPropertyAnimation(bar, b"value", self)
        anim.setDuration(380)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(bar.value())
        anim.setEndValue(target)
        anim.start()
        self._anim = anim

    def _sync_arrows(self) -> None:
        bar = self._scroll.horizontalScrollBar()
        scrolls = bar.maximum() > 0
        self._prev.setVisible(scrolls)
        self._next.setVisible(scrolls)
        self._prev.setEnabled(bar.value() > bar.minimum())
        self._next.setEnabled(bar.value() < bar.maximum())


class _Hero(QWidget):
    """The strip at the top of Home: the page's own bar, and a few titles
    featured big - this week's most popular the provider has, else what it
    added last - turning every few seconds, with Play and More info. With
    nothing to feature it is just the bar."""

    play = pyqtSignal(dict, str)
    info = pyqtSignal(dict, str)

    TURN_MS = 9000

    def __init__(self, bar: QWidget, loader, art_for) -> None:
        super().__init__()
        self._loader, self._art_for = loader, art_for
        self._items: list[tuple[dict, str, str]] = []
        self._i = 0
        self._art: dict[int, QPixmap] = {}
        self._bg: tuple | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        v = QVBoxLayout(self)
        v.setContentsMargins(40, 14, 40, 22)
        v.setSpacing(0)
        v.addWidget(bar)
        v.addStretch(1)
        self._kicker = self._label(12, 800, P["accent"])
        self._title = self._label(36, 800, P["text"])
        self._title.setWordWrap(True)
        self._meta = self._label(14, 600, P["text2"] if "text2" in P
                                 else P["muted"])
        self._plot = self._label(14, 400, P["muted"])
        self._plot.setWordWrap(True)
        for lbl, gap in ((self._kicker, 0), (self._title, 6), (self._meta, 8),
                         (self._plot, 8)):
            v.addSpacing(gap)
            v.addWidget(lbl)
        v.addSpacing(18)
        row = QHBoxLayout()
        row.setSpacing(12)
        self._play_btn = QPushButton()
        self._play_btn.setIcon(play_icon(14, P.get("on_accent", "white")))
        self._play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._play_btn.setStyleSheet(rounded(
            f"QPushButton {{ background:{P['accent']}; color:{P.get('on_accent', 'white')};"
            " border:none; border-radius:18px; padding:0 24px 0 18px;"
            " font-size:14px; font-weight:700; }"
            f"QPushButton:hover {{ background:{P['accent_hi']}; }}"))
        self._play_btn.clicked.connect(lambda: self._emit(self.play))
        self._info_btn = QPushButton(tr("home_more_info"))
        self._info_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._info_btn.setStyleSheet(rounded(
            "QPushButton { background:%s; color:%s; border:1px solid %s;"
            " border-radius:18px; padding:0 22px; font-size:14px;"
            " font-weight:700; }"
            "QPushButton:hover { border-color:%s; }" % (
                _alpha("pane", 0.70).name(QColor.NameFormat.HexArgb),
                P["text"], _alpha("text", 0.25).name(QColor.NameFormat.HexArgb),
                P["accent"])))
        self._info_btn.clicked.connect(lambda: self._emit(self.info))
        for b in (self._play_btn, self._info_btn):
            b.setFixedHeight(40)        # a radius under half the height
            row.addWidget(b)
        row.addSpacing(18)
        self._dots = QHBoxLayout()
        self._dots.setSpacing(7)
        row.addLayout(self._dots)
        row.addStretch(1)
        v.addLayout(row)
        self._detail = [self._kicker, self._title, self._meta, self._plot,
                        self._play_btn, self._info_btn]
        self._bar_h = bar.sizeHint().height()
        self._timer = QTimer(self)
        self._timer.setInterval(self.TURN_MS)
        self._timer.timeout.connect(lambda: self.show_item(self._i + 1))
        self.set_items([])

    @staticmethod
    def _label(px: int, weight: int, color: str) -> QLabel:
        lbl = QLabel()
        lbl.setStyleSheet(f"font-size:{px}px; font-weight:{weight};"
                          f" color:{color}; background:transparent;")
        lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        return lbl

    def _emit(self, sig) -> None:
        if self._items:
            it, kind, _k = self._items[self._i]
            sig.emit(it, kind)

    def set_items(self, items: list[tuple[dict, str, str]]) -> None:
        """(item, kind, kicker) each; kind is "vod" or "series"."""
        self._items = list(items)[:6]
        self._art.clear()
        self._bg = None
        has = bool(self._items)
        for wdg in self._detail:
            wdg.setVisible(has)
        while self._dots.count():
            d = self._dots.takeAt(0).widget()
            if d is not None:
                d.deleteLater()
        if len(self._items) > 1:
            for n in range(len(self._items)):
                d = QPushButton()
                d.setFixedSize(9, 9)
                d.setCursor(Qt.CursorShape.PointingHandCursor)
                d.clicked.connect(lambda _c=False, n=n: self.show_item(n))
                self._dots.addWidget(d)
        self.setFixedHeight(HERO_H if has else self._bar_h + 36)
        self._i = 0
        if has:
            self.show_item(0)
            if len(self._items) > 1:
                self._timer.start()
        else:
            self._timer.stop()
            self.update()

    def show_item(self, n: int) -> None:
        if not self._items:
            return
        if self._timer.isActive():
            self._timer.start()       # a full turn for the one now shown
        self._i = n % len(self._items)
        it, kind, kicker = self._items[self._i]
        name, year = split_year(it.get("name") or it.get("title") or "")
        year = year or str(it.get("year") or it.get("releaseDate") or "")[:4]
        self._kicker.setText(kicker.upper())
        self._title.setText(name or "?")
        meta = [x for x in (year, rating_text(it),
                            tr("misc_series" if kind == "series"
                               else "misc_movie")) if x]
        self._meta.setText("   ·   ".join(meta))
        plot = (it.get("plot") or it.get("description") or "").strip()
        self._plot.setText(plot[:220] + ("…" if len(plot) > 220 else ""))
        self._plot.setVisible(bool(plot))
        self._play_btn.setText("  " + tr(
            "btn_play" if kind != "series" else "common_open"))
        for k in range(self._dots.count()):
            d = self._dots.itemAt(k).widget()
            if d is not None:
                on = k == self._i
                d.setStyleSheet(rounded(
                    "QPushButton { border:none; border-radius:4px;"
                    f" background:{P['accent'] if on else P['muted']}; }}"))
        self._want_art(self._i)
        self._want_art((self._i + 1) % len(self._items))
        self._bg = None
        self.update()

    def _want_art(self, n: int) -> None:
        if n in self._art or self._loader is None:
            return
        it, kind, _k = self._items[n]
        url = self._art_for(it, kind)
        if not url:
            return
        items = self._items

        def got(pm, n=n, items=items) -> None:
            if items is not self._items or pm is None or pm.isNull():
                return
            self._art[n] = pm
            if n == self._i:
                self._bg = None
                self.update()
        try:
            self._loader.get(url, got)
        except Exception:
            pass

    def enterEvent(self, e) -> None:
        self._timer.stop()        # held still while the pointer is on it
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        if len(self._items) > 1:
            self._timer.start()
        super().leaveEvent(e)

    def resizeEvent(self, e) -> None:
        self._bg = None
        avail = max(320, int(self.width() * 0.48))
        for lbl in (self._title, self._plot):
            lbl.setMaximumWidth(avail)
        super().resizeEvent(e)

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(_AA)
        p.setRenderHint(_SMOOTH)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(P["bg"]))
        pm = self._art.get(self._i) if self._items else None
        if pm is not None and not pm.isNull():
            key = (self._i, w, h, id(pm))
            if self._bg is None or self._bg[0] != key:
                self._bg = (key, blurred(pm, w, h, 22))
            p.setOpacity(0.85)
            p.drawPixmap(0, 0, self._bg[1])
            p.setOpacity(1.0)
            # Readable text on the left, the page's colour at the bottom.
            g = QLinearGradient(0, 0, w, 0)
            g.setColorAt(0.0, _alpha("bg", 0.97))
            g.setColorAt(0.45, _alpha("bg", 0.80))
            g.setColorAt(1.0, _alpha("bg", 0.30))
            p.fillRect(self.rect(), g)
            g = QLinearGradient(0, h * 0.55, 0, h)
            g.setColorAt(0.0, _alpha("bg", 0.0))
            g.setColorAt(1.0, QColor(P["bg"]))
            p.fillRect(self.rect(), g)
            # The artwork itself, whole, on the right.
            ph = min(h - self._bar_h - 70, 320)
            s = pm.scaled(int(ph * 0.75), int(ph),
                          Qt.AspectRatioMode.KeepAspectRatio, _FAST)
            x = w - 64 - s.width()
            y = self._bar_h + 36 + (h - self._bar_h - 60 - s.height()) / 2
            art = QRectF(x, y, s.width(), s.height())
            for k in range(6, 0, -1):          # a soft shadow
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_alpha("bg", 0.10))
                p.drawRoundedRect(art.adjusted(-k * 2, -k + 6, k * 2, k * 3),
                                  corner_radius(RADIUS + k),
                                  corner_radius(RADIUS + k))
            p.save()
            p.setClipPath(_rounded(art, RADIUS))
            p.drawPixmap(art.toRect(), s)
            p.restore()
        p.end()


class _BeatingHeart(QWidget):
    """A heart that beats twice when it comes into view."""

    def __init__(self, size: int = 34) -> None:
        super().__init__()
        self._size = size
        self._scale = 1.0
        self.setFixedSize(size + 8, size + 8)
        from PyQt6.QtCore import QVariantAnimation
        self._beat = QVariantAnimation(self)
        self._beat.setDuration(1000)
        self._beat.setStartValue(1.0)
        for at, v in ((0.15, 1.22), (0.3, 0.95), (0.45, 1.14), (0.62, 1.0)):
            self._beat.setKeyValueAt(at, v)
        self._beat.setEndValue(1.0)
        self._beat.valueChanged.connect(self._set)

    def _set(self, v) -> None:
        self._scale = float(v)
        self.update()

    def showEvent(self, event) -> None:
        QTimer.singleShot(350, self._beat.start)
        super().showEvent(event)

    def paintEvent(self, _event) -> None:
        from .widgets import heart_path
        pr = QPainter(self)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        s = self._size * self._scale
        pr.translate((self.width() - s) / 2, (self.height() - s) / 2)
        grad = QLinearGradient(0, 0, s, s)
        grad.setColorAt(0.0, QColor(HEART).lighter(135))
        grad.setColorAt(0.5, QColor(HEART))
        grad.setColorAt(1.0, QColor(HEART).darker(125))
        pr.setPen(Qt.PenStyle.NoPen)
        pr.setBrush(grad)
        pr.drawPath(heart_path(s))
        pr.end()


class DonateCard(QFrame):
    """Home's one note about donating: a heart, two lines and Donate / Not
    now - a card among the shelves, never a window that pops up. Shown only
    after real use (mw_donate decides when)."""

    donate = pyqtSignal()
    later = pyqtSignal()
    already = pyqtSignal()

    def __init__(self, title: str, body: str, donate_text: str,
                 later_text: str, already_text: str = "") -> None:
        super().__init__()
        self.setObjectName("DonateCard")
        tint = QColor(HEART)
        wash = f"rgba({tint.red()}, {tint.green()}, {tint.blue()}"
        ink = on_accent(HEART)
        self.setStyleSheet(rounded(
            f"#DonateCard {{ border-radius: 14px; border: 1px solid {wash}, 0.28);"
            f" background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            f" stop:0 {wash}, 0.16), stop:1 {P['pane']}); }}"
            f"#DonateCard QLabel {{ background: transparent; }}"
            f"#DonateTitle {{ color: {P['text']}; font-size: 15px; font-weight: 700; }}"
            f"#DonateBody {{ color: {P['text2']}; font-size: 13px; }}"
            f"#DonateGo {{ background: {HEART}; color: {ink}; border: none;"
            f" border-radius: 16px; padding: 8px 18px; font-weight: 700; }}"
            f"#DonateGo:hover {{ background: {tint.lighter(112).name()}; }}"
            f"#DonateLater {{ background: transparent; color: {P['text2']};"
            f" border: none; padding: 8px 10px; font-weight: 600; }}"
            f"#DonateLater:hover {{ color: {P['text']}; }}"
            f"#DonateAlready {{ background: transparent; color: {P['text2']}; border: none;"
            f" padding: 2px 0 0 0; font-size: 12px; text-align: left; }}"
            f"#DonateAlready:hover {{ color: {P['text']}; text-decoration: underline; }}"))
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 14, 18, 14)
        h.setSpacing(14)
        h.addWidget(_BeatingHeart(), 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(3)
        t = QLabel(title)
        t.setObjectName("DonateTitle")
        b = QLabel(body)
        b.setObjectName("DonateBody")
        b.setWordWrap(True)
        text.addWidget(t)
        text.addWidget(b)
        if already_text:
            done = QPushButton(already_text)
            done.setObjectName("DonateAlready")
            done.setCursor(Qt.CursorShape.PointingHandCursor)
            done.clicked.connect(self.already.emit)
            text.addWidget(done, 0, Qt.AlignmentFlag.AlignLeft)
        h.addLayout(text, 1)
        from .widgets import heart_pixmap
        go = QPushButton(donate_text)
        go.setObjectName("DonateGo")
        go.setIcon(QIcon(heart_pixmap(14, ink)))
        go.setCursor(Qt.CursorShape.PointingHandCursor)
        go.clicked.connect(self.donate.emit)
        no = QPushButton(later_text)
        no.setObjectName("DonateLater")
        no.setCursor(Qt.CursorShape.PointingHandCursor)
        no.clicked.connect(self.later.emit)
        h.addWidget(no, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(go, 0, Qt.AlignmentFlag.AlignVCenter)
