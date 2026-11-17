"""The TV place's own pieces, pure presentation (ui/mw_place_tv.py puts them
in the window; the shared ones are ui/media_cards.py's):

* ``guide_now_next``: a channel's programme now and next, from a guide
  already in memory (providers/epg.XmltvGuide.schedule) - never a load;
* ``TvRow`` / ``paint_tv_row``: a live channel's row in the middle list,
  painted by ui/channel_list.ChannelDelegate - its number, logo and name
  with the marks the list carries (stream health's dot and a box's tuner
  ring before the name; the favourite's star and catch-up's marker after
  it), what is on (the time it began, its title, a bar of how far) and
  "Then ..." next: compact on one line with the next programme in a column
  of its own, airy with a bigger logo and the next programme under the bar,
  on a narrow pane the name over what is on.
  The one playing (here or on a TV) on the accent's wash with its bar and
  Playing under its name; a "channel" that is only a heading in the
  provider's list ("-= Sweden =-", core/guide_rows) painted as a heading;
  Nerd's technical line under what is on; mirrored right to left;
* ``ChannelCardRow``: Recently watched - a heading over small cards (the
  logo, the name, what is on now) that scroll sideways;
* ``TvPlaceTop``: the shared header (the category, "48 channels · by
  number", the sort, Compact / Airy) over that row - the PlacePage form
  over a list that scrolls itself.

Every row is painted in a view of uniform rows: nothing here is a widget per
channel, and the guide is asked only for the rows that paint.
"""

from __future__ import annotations

import time
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from PyQt6.QtCore import QPoint, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QSizePolicy, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget

from ..core.guide_rows import section_heading
from ..i18n import tr
from .media_cards import (
    BOLD, CH_PAD_X, CHANNEL_DENSITY, LINE_PX, SMALL_PX, STRIP_CARD_W, STRIP_GAP, TITLE_PX, Card, CardRow,
    NowNext, PageHeader, Pictures, _aligned, begin_paint, card_pills, channel_parts, clock,
    colour, initials_of, paint_bar, paint_picture, paint_pills, paint_ring, pill_height,
    playing_fill, playing_ink, px_font, surface, then_text,
)
from .theme import P, corner_radius

__all__ = [
    "DENSITIES", "ChannelCardRow", "TvPlaceTop", "TvRow", "guide_now_next", "info_line_h",
    "paint_tv_row", "tv_row_height",
]

#: The row densities, as the header's switch offers them.
DENSITIES = ("compact", "airy")
#: How long a programme may run: the airing one began at most this long ago.
_MAX_PROG_SECS = 86400


# -- what is on (pure) ------------------------------------------------------------------

def guide_now(guide: Any) -> float:
    """The guide's own now: the clock less its delay (Settings > Guide)."""
    try:
        delay = float(getattr(guide, "delay_minutes", 0) or 0)
    except (TypeError, ValueError):
        delay = 0.0
    return time.time() - delay * 60


def guide_now_next(guide: Any, item: dict, now: float | None = None) -> NowNext | None:
    """*item*'s programme on the air and the one after it, from *guide*'s
    schedule as it is in memory - None without a guide in memory or a
    schedule for the channel. Never loads, never downloads."""
    if guide is None or not item:
        return None
    schedule = getattr(guide, "schedule", None)
    if not callable(schedule):
        # A guide that only says what is on (its title and how far).
        now_for = getattr(guide, "now_for", None)
        try:
            got = now_for(item) if callable(now_for) else None
        except Exception:
            got = None
        return NowNext(title=str(got[0] or "")) if got else None
    try:
        sched = schedule(item)
    except Exception:
        return None
    if not sched:
        return None
    starts, stops, titles = sched[0], sched[1], sched[2]
    n = len(starts)
    if not n:
        return None
    now = guide_now(guide) if now is None else now
    lo = bisect_left(starts, now - _MAX_PROG_SECS)
    hi = bisect_right(starts, now)
    cur = next((j for j in range(lo, hi) if stops[j] > now), None)
    nxt = hi if hi < n else None
    if cur is None and nxt is None:
        return None
    nn = NowNext()
    if cur is not None:
        nn.title = str(titles[cur] or "")
        nn.start, nn.stop = float(starts[cur]), float(stops[cur])
    if nxt is not None:
        nn.next_title = str(titles[nxt] or "")
        nn.next_start = float(starts[nxt])
    return nn


# -- a channel's row --------------------------------------------------------------------

#: The technical line's room under what is on (core/stream_info.py).
def info_line_h() -> int:
    return QFontMetrics(_font(SMALL_PX)).height() + 4


def tv_row_height(density: str, info_line: bool = False) -> int:
    """A row's height at *density*, with room for the technical line."""
    h = CHANNEL_DENSITY.get(density, CHANNEL_DENSITY["compact"])[0]
    return h + (info_line_h() if info_line else 0)


@dataclass(slots=True)
class TvRow:
    """What one channel's row shows, worked out by the delegate from the
    window: *number* as shown (the user's own with Nerd), *logo* the
    picture when it is in memory, *now* the guide's now and next and *now_at*
    the guide's own time for its bar, *playing* here or on a TV, *health*
    stream health's colour (None: no dot), *tuner* a box's quiet ring,
    *info* the technical line, *tint_fg* / *tint_bg* the row's own colours,
    *heading* the provider's section heading this "channel" is."""
    name: str = ""
    number: str = ""
    logo: QPixmap | None = None
    now: NowNext | None = None
    now_at: float = 0.0
    playing: bool = False
    favourite: bool = False
    archive: bool = False
    health: str | None = None
    tuner: bool = False
    info: str = ""
    tint_fg: str = ""
    tint_bg: str = ""
    heading: str | None = None


_fonts: dict[tuple[int, int], QFont] = {}


def _font(px: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    key = (px, int(weight.value))
    f = _fonts.get(key)
    if f is None:
        f = _fonts[key] = px_font(px, weight)
    return f


def heading_of(name: str | None) -> str | None:
    """The heading a provider's "channel" really is ("-= Sweden =-"), or
    None for a channel."""
    try:
        return section_heading(name)
    except Exception:
        return None


#: The chosen row's surface (``media_cards.surface``: 1 the rows', 2 hover).
SELECTED_LEVEL = 2.8


def _veil(tint_hex: str) -> QColor:
    """Hover over a row of its own colour: a veil that keeps the colour."""
    c = QColor(tint_hex)
    lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return QColor(0, 0, 0, 40) if lum > 140 else QColor(255, 255, 255, 40)


def paint_row_ground(p: QPainter, rect: QRect, row: int, count: int, rtl: bool,
                     playing: bool, hover: bool, selected: bool, tint_bg: str = "") -> None:
    """The rows as one surface: rounded at the first row's top and the last
    row's foot, a hairline between rows, a row's own colour kept (veiled on
    hover), the chosen row in the selection's colour, the one playing on
    the accent's wash with its bar at the start of the line."""
    radius = corner_radius(12)
    top, bottom = row == 0, row == count - 1
    r = QRectF(rect)
    path = QPainterPath()
    if top or bottom:
        rr = min(radius, r.height() / 2)
        path.addRoundedRect(r, rr, rr)
        if not top:
            path.addRect(QRectF(r.x(), r.y(), r.width(), rr))
        if not bottom:
            path.addRect(QRectF(r.x(), r.bottom() - rr, r.width(), rr + 1))
        path.setFillRule(Qt.FillRule.WindingFill)
        path = path.simplified()
    else:
        path.addRect(r)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if selected:
        # A shade past hover: the row a zap or an arrow lands on reads at
        # a glance on the rows' own surface (the theme's "sel" all but
        # vanished on it).
        p.fillPath(path, surface(SELECTED_LEVEL))
    elif playing:
        p.fillPath(path, playing_fill())
    else:
        if hover:
            if tint_bg:
                p.fillPath(path, QColor(tint_bg))
                p.fillPath(path, _veil(tint_bg))
            else:
                p.fillPath(path, surface(2.0))
        else:
            p.fillPath(path, QColor(tint_bg) if tint_bg else surface(1.0))
    if not top and not playing and not selected and not tint_bg:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(colour("bg", 0.9))
        p.drawRect(QRectF(r.x() + 12, r.y(), r.width() - 24, 1))
    if playing:
        bar = QRectF(r.right() - 3 if rtl else r.x(), r.y() + 8, 3, r.height() - 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(colour("accent"))
        p.drawRoundedRect(bar, 1.5, 1.5)
    p.restore()


#: Below this row width the name and what is on share one column.
NARROW_FROM = 440

#: The marks after a name: the favourite's star, catch-up's rewind.
STAR, ARCHIVE = "★", "◀◀"
DOT = 8
MARK_GAP = 6


def _marks_after(r: TvRow) -> list[str]:
    return [m for m, on in ((STAR, r.favourite), (ARCHIVE, r.archive)) if on]


def paint_tv_row(p: QPainter, rect: QRect, row: int, count: int, r: TvRow, density: str,
                 rtl: bool, hover: bool = False, selected: bool = False,
                 pictures: Pictures | None = None) -> dict[str, QRect]:
    """*r* painted in *rect* (the list's row *row* of *count*); the parts
    it went into (for the tests)."""
    pictures = pictures if pictures is not None else Pictures()
    begin_paint(p)
    try:
        paint_row_ground(p, rect, row, count, rtl, r.playing, hover, selected,
                         "" if selected else r.tint_bg)
        if r.heading:
            _paint_heading(p, rect, r.heading, rtl)
            return {"heading": rect}
        parts = channel_parts(rect, density, rtl)
        align = _aligned(rtl)
        small = _font(SMALL_PX)
        sfm = QFontMetrics(small)
        if r.number:
            p.setFont(small)
            p.setPen(colour("muted"))
            num_align = (Qt.AlignmentFlag.AlignLeft if rtl else Qt.AlignmentFlag.AlignRight) \
                | Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignAbsolute
            p.drawText(parts["num"], int(num_align), sfm.elidedText(
                r.number, Qt.TextElideMode.ElideLeft, parts["num"].width()))
        paint_picture(p, parts["logo"], r.logo, pictures, corner_radius(8),
                      initials_of(r.name, 3), fit=True)
        nn = r.now
        airy = density == "airy"
        if rect.width() < NARROW_FROM:
            # A narrow pane: the name over what is on, in one column (the
            # row's wash and bar say it plays).
            text = parts["name"].united(parts["now"])
            name_h = QFontMetrics(_font(TITLE_PX, BOLD)).height()
            now_h = _now_block(nn, r, airy)[3]
            gap = 4 if now_h else 0
            y = rect.y() + (rect.height() - name_h - gap - now_h) // 2
            parts["name"] = QRect(text.x(), y, text.width(), name_h)
            parts["now"] = QRect(text.x(), y + name_h + gap, text.width(), max(1, now_h))
            parts["name_text"] = _paint_name(p, parts["name"], r, rtl, selected,
                                             with_pill=False)
        else:
            parts["name_text"] = _paint_name(p, parts["name"], r, rtl, selected)
        _paint_now(p, parts["now"], nn, r, rtl, airy)
        nxt = parts["next"]
        if nxt.isValid() and nn is not None:
            text = then_text(nn)
            if text:
                p.setFont(small)
                p.setPen(colour("muted"))
                p.drawText(nxt, int(align), sfm.elidedText(
                    text, Qt.TextElideMode.ElideRight, nxt.width()))
        return parts
    finally:
        p.restore()


def _paint_heading(p: QPainter, rect: QRect, text: str, rtl: bool) -> None:
    f = QFont(_font(LINE_PX, BOLD))
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
    p.setFont(f)
    p.setPen(colour("muted"))
    box = rect.adjusted(CH_PAD_X, 0, -CH_PAD_X, 0)
    fm = QFontMetrics(f)
    p.drawText(box, int(_aligned(rtl)), fm.elidedText(
        text.upper(), Qt.TextElideMode.ElideRight, box.width()))


def _paint_name(p: QPainter, name_r: QRect, r: TvRow, rtl: bool, selected: bool,
                with_pill: bool = True) -> QRect:
    """The name with its marks, and Playing under it; where the name went."""
    name_f = _font(TITLE_PX, BOLD)
    nfm = QFontMetrics(name_f)
    pills = card_pills(Card(), r.playing) if with_pill else []
    ph = pill_height()
    block = nfm.height() + (4 + ph if pills else 0)
    y = name_r.y() + (name_r.height() - block) // 2
    line = QRect(name_r.x(), y, name_r.width(), nfm.height())
    # Before the name: stream health's dot, a box's ring (core/tuners.py).
    lead = [k for k, on in (("dot", bool(r.health)), ("ring", r.tuner)) if on]
    lead_w = len(lead) * (DOT + MARK_GAP)
    x = line.right() + 1 - DOT if rtl else line.x()
    cy = line.y() + line.height() / 2
    for kind in lead:
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(x, cy - DOT / 2, DOT, DOT)
        if kind == "dot":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(r.health or P["muted"]))
            p.drawEllipse(box)
        else:
            pen = QPen(colour("muted"))
            pen.setWidthF(1.4)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(box.adjusted(0.7, 0.7, -0.7, -0.7))
        p.restore()
        x += -(DOT + MARK_GAP) if rtl else DOT + MARK_GAP
    # After it: the favourite's star and catch-up's marker.
    marks = _marks_after(r)
    mark_f = _font(SMALL_PX, BOLD)
    mfm = QFontMetrics(mark_f)
    marks_w = sum(mfm.horizontalAdvance(m) + MARK_GAP for m in marks)
    room = max(10, line.width() - lead_w - marks_w)
    text = nfm.elidedText(r.name, Qt.TextElideMode.ElideRight, room)
    tw = min(room, nfm.horizontalAdvance(text))
    tx = line.right() + 1 - lead_w - tw if rtl else line.x() + lead_w
    name_box = QRect(tx, line.y(), tw + 1, line.height())
    p.setFont(name_f)
    if r.playing and not selected:
        ink = playing_ink()
    elif r.tint_fg and not selected:
        ink = QColor(r.tint_fg)
    else:
        ink = colour("text")
    p.setPen(ink)
    p.drawText(name_box, int(_aligned(rtl)), text)
    if marks:
        p.setFont(mark_f)
        mx = tx - MARK_GAP if rtl else tx + tw + MARK_GAP
        for m in marks:
            mw = mfm.horizontalAdvance(m)
            p.setPen(colour("warn"))
            box = QRect(mx - mw if rtl else mx, line.y(), mw + 1, line.height())
            p.drawText(box, int(Qt.AlignmentFlag.AlignCenter), m)
            mx += -(mw + MARK_GAP) if rtl else mw + MARK_GAP
    if pills:
        paint_pills(p, pills, QRect(name_r.x(), y + nfm.height() + 4, name_r.width(), ph), rtl)
    return name_box


def _now_block(nn: NowNext | None, row: TvRow, airy: bool):
    """(on now, "Then ...", how far, the block's height) of what is on."""
    sfm = QFontMetrics(_font(SMALL_PX))
    tfm = QFontMetrics(_font(TITLE_PX, QFont.Weight.Medium))
    has_now = nn is not None and bool(nn.title)
    then = then_text(nn) if (airy and nn is not None) else ""
    frac = nn.fraction(row.now_at or None) if has_now and nn is not None else None
    line_h = max(sfm.height(), tfm.height())
    block = ((line_h if has_now else 0) + (7 if frac is not None else 0)
             + ((4 if has_now else 0) + sfm.height() if then else 0)
             + ((4 if (has_now or then) else 0) + sfm.height() if row.info else 0))
    return has_now, then, frac, block


def _paint_now(p: QPainter, r: QRect, nn: NowNext | None, row: TvRow, rtl: bool,
               airy: bool) -> None:
    """What is on: the time it began and its title, the bar of how far,
    "Then ..." under it (airy), the technical line last."""
    small = _font(SMALL_PX)
    title_f = _font(TITLE_PX, QFont.Weight.Medium)
    sfm, tfm = QFontMetrics(small), QFontMetrics(title_f)
    has_now, then, frac, block = _now_block(nn, row, airy)
    line_h = max(sfm.height(), tfm.height())
    if block <= 0:
        return
    y = r.y() + (r.height() - block) // 2
    align = int(_aligned(rtl))
    if has_now and nn is not None:
        when = clock(nn.start)
        when_w = sfm.horizontalAdvance(when) + 8 if when else 0
        if when:
            p.setFont(small)
            p.setPen(colour("muted"))
            wr = QRect(r.right() - when_w + 1, y, when_w, line_h) if rtl \
                else QRect(r.x(), y, when_w, line_h)
            p.drawText(wr, align, when)
        tr_ = QRect(r.x(), y, r.width() - when_w, line_h) if rtl \
            else QRect(r.x() + when_w, y, r.width() - when_w, line_h)
        p.setFont(title_f)
        p.setPen(colour("text2"))
        p.drawText(tr_, align, tfm.elidedText(nn.title, Qt.TextElideMode.ElideRight,
                                              max(10, tr_.width())))
        y += line_h
        if frac is not None:
            w = min(360, r.width())
            x = r.right() - w + 1 if rtl else r.x()
            paint_bar(p, QRectF(x, y + 4, w, 3), frac, rtl)
            y += 7
    if then:
        y += 4 if has_now else 0
        p.setFont(small)
        p.setPen(colour("muted"))
        p.drawText(QRect(r.x(), y, r.width(), sfm.height()), align,
                   sfm.elidedText(then, Qt.TextElideMode.ElideRight, r.width()))
        y += sfm.height()
    if row.info:
        y += 4 if (has_now or then) else 0
        p.setFont(small)
        p.setPen(colour("muted"))
        p.drawText(QRect(r.x(), y, r.width(), sfm.height()), align,
                   sfm.elidedText(row.info, Qt.TextElideMode.ElideRight, r.width()))


# -- Recently watched -------------------------------------------------------------------

CARD_H = 56
CARD_LOGO = 36
CARD_PAD = 10


class ChannelCardDelegate(QStyledItemDelegate):
    """A small channel card: its logo on a tile, the name, what is on now
    (Playing before it on the one that plays)."""

    def __init__(self, strip) -> None:
        super().__init__(strip)
        self._strip = strip

    def sizeHint(self, option, index) -> QSize:
        return QSize(STRIP_CARD_W + STRIP_GAP, CARD_H)

    def paint(self, p: QPainter, option, index) -> None:
        s = self._strip
        card = s.model_.item_at(index.row())
        if card is None:
            return
        rtl = option.direction == Qt.LayoutDirection.RightToLeft
        playing = bool(card.key) and card.key == s.playing_key
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        focus = selected and s.hasFocus() and index == s.currentIndex()
        slot = option.rect
        box = QRect(slot.x() + (STRIP_GAP if rtl else 0), slot.y(),
                    max(1, slot.width() - STRIP_GAP), slot.height())
        begin_paint(p)
        try:
            radius = corner_radius(12)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(playing_fill() if playing
                       else surface(2.0 if (hover or selected) else 1.0))
            p.drawRoundedRect(QRectF(box), radius, radius)
            if focus:
                paint_ring(p, QRectF(box), radius)
            logo = QRect(box.x() + CARD_PAD, box.y() + (box.height() - CARD_LOGO) // 2,
                         CARD_LOGO, CARD_LOGO)
            tx = logo.right() + 1 + 10
            text = QRect(tx, box.y() + 6, max(10, box.right() - 12 - tx + 1),
                         box.height() - 12)
            if rtl:
                logo = QStyle.visualRect(Qt.LayoutDirection.RightToLeft, box, logo)
                text = QStyle.visualRect(Qt.LayoutDirection.RightToLeft, box, text)
            pm = s.images.get(card.image) if card.image else None
            paint_picture(p, logo, pm, s.pictures, corner_radius(8),
                          card.initials or initials_of(card.title, 3), fit=True)
            tf, lf = _font(TITLE_PX, BOLD), _font(LINE_PX)
            tfm, lfm = QFontMetrics(tf), QFontMetrics(lf)
            line = s.line_of(card) if s.line_of is not None else card.line
            pills = card_pills(card, playing)
            two = bool(line or pills)
            block = tfm.height() + (2 + max(lfm.height(), pill_height() if pills else 0)
                                    if two else 0)
            y = text.y() + (text.height() - block) // 2
            align = int(_aligned(rtl))
            p.setFont(tf)
            p.setPen(playing_ink() if playing else colour("text"))
            p.drawText(QRect(text.x(), y, text.width(), tfm.height()), align,
                       tfm.elidedText(card.title, Qt.TextElideMode.ElideRight, text.width()))
            y += tfm.height() + 2
            if two:
                lh = max(lfm.height(), pill_height() if pills else 0)
                lr = QRect(text.x(), y, text.width(), lh)
                used = paint_pills(p, pills[:1], lr, rtl) + 8 if pills else 0
                room = max(10, text.width() - used)
                p.setFont(lf)
                p.setPen(colour("muted"))
                p.drawText(QRect(lr.x() if rtl else lr.x() + used, y, room, lh), align,
                           lfm.elidedText(line, Qt.TextElideMode.ElideRight, room))
        finally:
            p.restore()


class ChannelCardRow(CardRow):
    """Recently watched: a heading over the channels' small cards in one row
    that scrolls sideways; hidden while it has none. *line_of(card)* gives a
    card's line as it is painted (what is on now)."""

    def __init__(self, title: str = "", loader=None, parent=None) -> None:
        super().__init__(title, loader, "wide", parent)
        self.setObjectName("TvRecentRow")
        self.strip.line_of = None
        self.strip.setItemDelegate(ChannelCardDelegate(self.strip))
        self.strip.setFixedHeight(CARD_H)

    def set_line_of(self, fn: Callable[[Card], str] | None) -> None:
        self.strip.line_of = fn
        self.strip.viewport().update()

    def keys(self) -> list[str]:
        return [c.key for c in self.cards()]

    def refresh(self) -> None:
        """Painted again: what is on moved on."""
        self.strip.viewport().update()


class TvPlaceTop(QWidget):
    """The TV place over its list: the shared header and Recently watched.
    Says what the person picks: an order (``sort_changed``), a density
    (``density_changed``), a card (``activated``), a card's menu
    (``menu``)."""

    sort_changed = pyqtSignal(str)
    density_changed = pyqtSignal(str)
    activated = pyqtSignal(object)
    menu = pyqtSignal(object, QPoint)
    #: "Hide this row" from Recently watched's heading (its row's name).
    hide_requested = pyqtSignal(str)

    # No inset of its own: the title and the cards line up with the filter
    # and the rows under them (6 px set them apart from everything below).
    MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM, SPACING = 0, 6, 2, 16
    #: Compact (Just the list): closer to the list's top and to the rows.
    COMPACT_TOP, COMPACT_SPACING = 0, 10

    def __init__(self, loader=None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("TvPlaceTop")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(self.MARGIN_X, self.MARGIN_TOP, self.MARGIN_X,
                               self.MARGIN_BOTTOM)
        lay.setSpacing(self.SPACING)
        self.header = PageHeader("")
        self.header.fit_sort = False        # _fit does it, with the switch
        self.header.sort_changed.connect(self.sort_changed)
        self.header.view_changed.connect(self.density_changed)
        lay.addWidget(self.header)
        self.recent = ChannelCardRow(tr("tv_recent"), loader)
        self.recent.activated.connect(self.activated)
        self.recent.menu.connect(self.menu)
        self.recent.set_hide_key("tv_recent")
        self.recent.hide_requested.connect(self.hide_requested)
        lay.addWidget(self.recent)
        self._lay = lay
        self._density = "compact"
        self._density_shown = True
        self._sort_opts: list[tuple[str, str]] = []
        self._views: list[tuple[str, str]] = []
        self._title = ""
        # Nothing here holds the middle pane wider than it may go (240 px):
        # the title and count are cut to the room, the sort shrinks to its
        # mark and the switch steps aside on a narrow pane (_fit).
        for label in (self.header.title, self.header.count):
            label.setMinimumWidth(1)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(1)
        self.set_density(self._density)

    #: Below these widths the sort shows its mark alone, and the switch goes.
    SORT_TEXT_FROM, SWITCH_FROM = 470, 340

    def restyle(self) -> None:
        self.header.restyle()
        self.recent.restyle()
        self._fit()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._fit()

    def _fit(self) -> None:
        w = self.width()
        style = (Qt.ToolButtonStyle.ToolButtonTextBesideIcon if w >= self.SORT_TEXT_FROM
                 else Qt.ToolButtonStyle.ToolButtonIconOnly)
        if self.header.sort_btn.toolButtonStyle() != style:
            self.header.sort_btn.setToolButtonStyle(style)
        self.header.switch.setVisible(self._density_shown and w >= self.SWITCH_FROM)
        title = self.header.title
        room = max(1, title.width())
        text = title.fontMetrics().elidedText(self._title, Qt.TextElideMode.ElideRight, room)
        if title.text() != text:
            title.setText(text)
        title.setToolTip(self._title if text != self._title else "")

    def retranslate(self) -> None:
        self.recent.set_title(tr("tv_recent"))
        self.set_density(self._density, self._density_shown)

    def set_compact(self, on: bool) -> None:
        """Compact (Just the list): the header's, and less room round it."""
        self._lay.setContentsMargins(self.MARGIN_X, self.COMPACT_TOP if on else self.MARGIN_TOP,
                                     self.MARGIN_X, self.MARGIN_BOTTOM)
        self._lay.setSpacing(self.COMPACT_SPACING if on else self.SPACING)
        self.header.set_compact(on)
        self._fit()

    def is_compact(self) -> bool:
        return self.header.is_compact()

    def set_title(self, text: str) -> None:
        self._title = text
        self.header.set_title(text)
        self._fit()

    def title_text(self) -> str:
        return self._title

    def set_count(self, text: str) -> None:
        self.header.set_count(text)

    def set_sort(self, options: Sequence[tuple[str, str]], current: str) -> None:
        options = list(options)
        if options != self._sort_opts:
            self._sort_opts = options
            self.header.set_sort(options, current)
        else:
            self.header.set_sort_key(current)

    def set_density(self, density: str, shown: bool = True) -> None:
        """The switch on *density* - or hidden (a grid has no density of
        rows)."""
        self._density = density if density in DENSITIES else "compact"
        self._density_shown = shown
        views = [("compact", tr("card_compact")), ("airy", tr("card_airy"))]
        if views != self._views:
            self._views = views
            self.header.set_views(views, self._density)
        else:
            self.header.set_view_key(self._density)
        self._fit()

    def density(self) -> str:
        return self._density

    def set_recent(self, cards: list[Card]) -> None:
        if [c.key for c in cards] == self.recent.keys():
            old = {c.key: c for c in self.recent.cards()}
            for c in cards:
                o = old.get(c.key)
                if o is not None:
                    o.title, o.line, o.image, o.data = c.title, c.line, c.image, c.data
            self.recent.setVisible(bool(cards))
            self.recent.refresh()
            return
        self.recent.set_cards(cards)

    def set_playing(self, key: str | None) -> None:
        self.recent.set_playing(key)
