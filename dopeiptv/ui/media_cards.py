"""The cards every place shares - Mitt, TV, Movies, Series - painted, one
look everywhere (flat, rounded, no borders, the palette ``P`` only):

* the pills (``paint_pill``: Live, New, Playing, Watched, Up next), sized
  from the glyphs' own ascent and descent with room around them;
* ``CardView``: titles as 2:3 posters or 16:9 cards in a grid, or as rows of
  a list (``set_layout``: "poster", "wide", "list") - a title, one muted
  line, the pills and how far it got;
* ``ChannelView``: channels as rows - number, logo, name, what is on with its
  time and a bar, "Then ..." next; compact or airy; the one playing marked
  with the accent's bar;
* ``CardRow``: a heading over a sideways row of small cards (Continue,
  Popular right now) - picture, title, a line, progress, a rank; its
  heading's right click offers "Hide this row" (``set_hide_key``);
* ``PageHeader``: a place's title, its count line, a sort control and a
  segmented switch (grid / list, compact / airy); compact (Just the list)
  the title smaller and on the count's line where they fit;
* ``PlacePage``: the shared form - the header, its rows, then the content -
  in one scroll area that paints only the strip coming into view.

Every list is one view of uniform rows or cells with a painted delegate:
no widget per item, so ten thousand channels cost what the screen shows.
Pictures come through the window's image loaders (core/workers.LogoLoader,
wrapped by series_cards.ImageSource): asked for only for what is on screen
and a few past it, fetched and decoded on the loaders' own threads, scaled
once per size (``Pictures``) and painted when they come.

Keyboard: the arrows move (a grid in two directions), Enter plays or opens
(``activated``), the context-menu key and a right click ask for a menu
(``menu``), the mouse's back button goes back (``back``). Mirrored in a
right-to-left language; every item has a name for a screen reader.

Pure presentation: nothing here plays, fetches (but pictures) or stores.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from PyQt6 import sip
from PyQt6.QtCore import (
    QAbstractListModel, QEvent, QModelIndex, QPoint, QPointF, QRect, QRectF, QSize, Qt,
    QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QAction, QActionGroup, QColor, QFont, QFontMetrics, QIcon, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit, QListView,
    QMenu, QPushButton, QScrollArea, QSizePolicy, QStyle, QStyledItemDelegate,
    QToolButton, QVBoxLayout, QWidget,
)

from ..i18n import tr
from .qt_i18n import format_when
from .series_cards import ImageSource
from .theme import P, corner_radius, ink_on, tinted

__all__ = [
    "Card", "CardModel", "CardRow", "CardStrip", "CardView", "ChannelRow", "ChannelView",
    "GridMetrics", "NowNext", "PageHeader", "Pictures", "PlacePage", "Segmented",
    "card_parts", "channel_parts", "grid_metrics", "paint_pill", "pill_size", "pill_text",
    "strip_parts",
]


# -- colours and type ----------------------------------------------------------------

def colour(key: str, alpha: float = 1.0) -> QColor:
    c = QColor(P.get(key) or P.get("text") or "#808080")
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def px_font(px: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont()
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


#: The type every card uses: a title, its muted line, the small print.
TITLE_PX, LINE_PX, SMALL_PX = 13, 11, 12
#: A heading's words among cards (``Card.heading``).
HEAD_PX = 15
BOLD = QFont.Weight.Bold


_washes: dict[tuple, tuple[str, str]] = {}


def _wash(colour_hex: str, ratio: float) -> tuple[str, str]:
    """tinted() over the page, kept per palette: a delegate asks for the
    same few on every row it paints."""
    key = (colour_hex, P.get("bg"), P.get("text"), ratio)
    got = _washes.get(key)
    if got is None:
        if len(_washes) > 256:
            _washes.clear()
        got = _washes[key] = tinted(colour_hex, P["bg"], ratio, P["text"])
    return got


def surface(level: float = 1.0) -> QColor:
    """A section's surface: a wash of the text colour over the page - a
    shade lighter on a dark theme, darker on a light one - so the surface
    reads on every theme without a border. *level* 2 is hover's."""
    return QColor(_wash(P["text"], round(0.05 * level, 3))[0])


def playing_fill() -> QColor:
    return QColor(_wash(P["accent"], 0.16)[0])


def playing_ink() -> QColor:
    return QColor(_wash(P["accent"], 0.16)[1])


# -- the pills ------------------------------------------------------------------------

#: The pills, the same everywhere: what is on the air, a new title or
#: episode, what plays here now, one already watched, the one up next, one
#: that failed (a recording).
PILL_KINDS = ("live", "new", "playing", "seen", "next", "fail")
_PILL_WORDS = {"live": "pill_live", "new": "pill_new", "playing": "pill_playing",
               "seen": "pill_seen", "next": "sp_up_next", "fail": "rec_status_failed"}
PILL_PX = 10
#: Room round the glyphs: on each side, and above and below their ink.
PILL_PAD_X, PILL_PAD_Y = 9, 3
PILL_GAP = 6


def pill_font() -> QFont:
    return px_font(PILL_PX, BOLD)


_pill_fm: list[QFontMetrics] = []


def pill_metrics() -> QFontMetrics:
    if not _pill_fm:
        _pill_fm.append(QFontMetrics(pill_font()))
    return _pill_fm[0]


def pill_height() -> int:
    fm = pill_metrics()
    return fm.ascent() + fm.descent() + 2 * PILL_PAD_Y


def pill_text(kind: str, text: str = "") -> str:
    return text or tr(_PILL_WORDS.get(kind, "pill_new"))


def pill_size(text: str, fm: QFontMetrics | None = None) -> QSize:
    """A pill's size for *text*: from the glyphs' own ascent and descent,
    with room round them - a font's line height alone left the text
    touching the pill's edges on Linux, where the system fonts sit taller."""
    fm = fm or pill_metrics()
    ink_h = fm.ascent() + fm.descent()
    return QSize(fm.horizontalAdvance(text) + 2 * PILL_PAD_X, ink_h + 2 * PILL_PAD_Y)


def pill_colours(kind: str) -> tuple[QColor, QColor]:
    """(fill, ink) of a pill of *kind*: Live in the recording red, New and
    Playing in the accent, Watched a soft wash of the theme's "ok" green,
    Failed one of its "error" red, Up next a soft wash of the accent - each
    ink made to read on its fill."""
    if kind == "live":
        return QColor(P["rec"]), QColor(ink_on(P["rec"]))
    if kind in ("new", "playing"):
        return QColor(P["accent"]), QColor(P["on_accent"])
    base = P["ok"] if kind == "seen" else P["error"] if kind == "fail" else P["accent"]
    wash, ink = _wash(base, 0.24)
    return QColor(wash), QColor(ink)


def paint_pill(p: QPainter, kind: str, x: float, y: float, text: str = "") -> QSize:
    """A pill of *kind* (its own words, or *text*) with its top-left at
    (*x*, *y*); its size."""
    text = pill_text(kind, text)
    f = pill_font()
    fm = pill_metrics()
    size = pill_size(text, fm)
    w, h = size.width(), size.height()
    fill, ink = pill_colours(kind)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fill)
    p.drawRoundedRect(QRectF(x, y, w, h), h / 2, h / 2)
    p.setFont(f)
    p.setPen(ink)
    ink_h = fm.ascent() + fm.descent()
    base = y + (h - ink_h) / 2 + fm.ascent()
    p.drawText(QPointF(x + (w - fm.horizontalAdvance(text)) / 2, base), text)
    p.restore()
    return size


Pill = tuple[str, str]


def pills_of(pills: Iterable) -> list[Pill]:
    """(kind, text) for each pill given as a kind or a (kind, text) pair;
    unknown kinds dropped, each kind once."""
    out: list[Pill] = []
    seen: set[str] = set()
    for pl in pills or ():
        kind, text = (pl, "") if isinstance(pl, str) else (str(pl[0]), str(pl[1] or ""))
        if kind in PILL_KINDS and kind not in seen:
            seen.add(kind)
            out.append((kind, text))
    return out


def pills_width(pills: Sequence[Pill], fm: QFontMetrics | None = None) -> int:
    fm = fm or pill_metrics()
    ws = [pill_size(pill_text(k, t), fm).width() for k, t in pills]
    return sum(ws) + PILL_GAP * max(0, len(ws) - 1)


def paint_pills(p: QPainter, pills: Sequence[Pill], r: QRect, rtl: bool,
                at_end: bool = False) -> int:
    """*pills* in a row in *r*, vertically centred, from its start (or its
    end) - mirrored in a right-to-left language; the width they took."""
    if not pills:
        return 0
    fm = pill_metrics()
    total = pills_width(pills, fm)
    from_left = (not at_end) != rtl
    x = r.left() if from_left else r.right() + 1 - total
    order = list(pills) if not rtl else list(reversed(pills))
    for kind, text in order:
        size = pill_size(pill_text(kind, text), fm)
        paint_pill(p, kind, x, r.top() + (r.height() - size.height()) / 2, text)
        x += size.width() + PILL_GAP
    return total


# -- what a card shows ---------------------------------------------------------------

@dataclass(slots=True)
class Card:
    """One title on a card: what the page feeds a ``CardView``, ``CardRow``
    or ``CardStrip``. *key* is stable (what ``playing_key`` and
    ``row_of`` compare); *image* the picture's address and *fallback* one
    to show while it has none (a series' poster for an episode's still);
    *progress* how far it got (0-1; None draws no bar); *pills* kinds from
    ``PILL_KINDS`` or (kind, text) pairs - Playing is added by the view for
    the card that plays; *rank* a number before the picture (Popular);
    *data* the page's own row, handed back with every signal."""
    key: str = ""
    title: str = ""
    line: str = ""
    image: str = ""
    fallback: str = ""
    progress: float | None = None
    pills: tuple = ()
    initials: str = ""
    rank: int = 0
    data: Any = None
    marks: tuple = ()
    #: A heading over the cards after it ("Today", a channel's name): a
    #: ``CardView`` gives it a line of its own; never picked or played.
    heading: bool = False


def is_heading(item: Any) -> bool:
    return bool(getattr(item, "heading", False))


#: The marks after a card's title, as the TV place's rows have them after a
#: channel's name: a favourite's star, Watch later's clock (*Card.marks*).
MARK_FAV, MARK_LATER = "fav", "later"
MARK_GAP = 6


def marks_width(fm: QFontMetrics, marks: Sequence[str]) -> int:
    """The room *marks* take after a title set in *fm*."""
    return sum(_mark_w(fm, m) + MARK_GAP for m in marks if m in (MARK_FAV, MARK_LATER))


def _mark_w(fm: QFontMetrics, mark: str) -> int:
    return fm.horizontalAdvance("\u2605") if mark == MARK_FAV else round(fm.ascent() * 0.86)


def paint_marks(p: QPainter, marks: Sequence[str], x: int, line: QRect, fm: QFontMetrics,
                rtl: bool) -> None:
    """*marks* in the title's *line*, beginning at *x* (going left in a
    right-to-left line): the star a glyph in the warning colour, as the TV
    rows draw it, the clock drawn in the same ink."""
    ink = colour("warn")
    for m in marks:
        if m not in (MARK_FAV, MARK_LATER):
            continue
        w = _mark_w(fm, m)
        left = x - MARK_GAP - w if rtl else x + MARK_GAP
        box = QRect(left, line.y(), w, line.height())
        p.save()
        if m == MARK_FAV:
            p.setPen(ink)
            p.drawText(box, int(Qt.AlignmentFlag.AlignCenter), "\u2605")
        else:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            d = float(w)
            cx, cy = box.x() + d / 2, box.y() + box.height() / 2
            pen = QPen(ink, max(1.3, d * 0.12))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(cx, cy), d / 2 - 1, d / 2 - 1)
            hands = QPainterPath()
            hands.moveTo(cx, cy - d * 0.28)
            hands.lineTo(cx, cy)
            hands.lineTo(cx + d * 0.22, cy + d * 0.12)
            p.drawPath(hands)
        p.restore()
        x = left if rtl else left + w


def _title_with_marks(p: QPainter, r: QRect, title: str, marks: Sequence[str],
                      fm: QFontMetrics, align: int, rtl: bool) -> None:
    """A card's title cut to leave room for its marks, the marks after it."""
    room = max(10, r.width() - marks_width(fm, marks))
    text = fm.elidedText(title, Qt.TextElideMode.ElideRight, room)
    p.drawText(r, align, text)
    if marks:
        used = fm.horizontalAdvance(text)
        x = r.right() + 1 - used if rtl else r.x() + used
        paint_marks(p, marks, x, r, fm, rtl)


def item_marks(window: Any, row: dict, kind: str) -> tuple:
    """A row's marks as the window knows them: favourite, on Watch later."""
    out = []
    try:
        if window.is_favorite_item(row, kind):
            out.append(MARK_FAV)
        if window.is_item_on_watchlist(row, kind):
            out.append(MARK_LATER)
    except Exception:
        return ()
    return tuple(out)


def initials_of(text: str, n: int = 2) -> str:
    words = [w for w in str(text or "").replace("-", " ").split() if w[:1].isalnum()]
    if not words:
        return str(text or "?").strip()[:1].upper() or "?"
    if len(words) == 1:
        return words[0][:n].upper() if n > 1 and len(words[0]) > 3 else words[0][:1].upper()
    return "".join(w[0] for w in words[:n]).upper()


def card_pills(card: Card, playing: bool) -> list[Pill]:
    pills = pills_of(card.pills)
    if playing and all(k != "playing" for k, _t in pills):
        pills.insert(0, ("playing", ""))
    return pills


def progress_of(card: Card) -> float:
    try:
        return max(0.0, min(1.0, float(card.progress or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def card_a11y(card: Card, playing: bool = False) -> str:
    """What a screen reader says for a card: its title, its line, its pills
    and how far it got."""
    parts = [card.title]
    if card.rank:
        parts.insert(0, tr("card_rank", n=card.rank))
    if card.line:
        parts.append(card.line)
    parts += [pill_text(k, t) for k, t in card_pills(card, playing)]
    frac = progress_of(card)
    if card.progress is not None and 0 < frac < 1:
        parts.append(tr("card_progress_a11y", n=round(frac * 100)))
    return ", ".join(p for p in parts if p)


@dataclass(slots=True)
class NowNext:
    """What a channel shows now and next, from the guide (epoch seconds)."""
    title: str = ""
    start: float = 0.0
    stop: float = 0.0
    next_title: str = ""
    next_start: float = 0.0

    def fraction(self, now: float | None = None) -> float | None:
        if self.stop <= self.start:
            return None
        now = time.time() if now is None else now
        return max(0.0, min(1.0, (now - self.start) / (self.stop - self.start)))


@dataclass(slots=True)
class ChannelRow:
    """A channel in a ``ChannelView``: *number* as shown (the user's own or
    the source's), *logo* its address, *now* what is on (or None - the
    view's ``guide`` may give it as the row is painted), *pills* as a
    card's; *data* the page's own row."""
    key: str = ""
    name: str = ""
    number: str = ""
    logo: str = ""
    now: NowNext | None = None
    pills: tuple = ()
    data: Any = None


def clock(ts: float) -> str:
    return format_when(ts, "HH:mm") if ts else ""


def then_text(nn: NowNext | None) -> str:
    if nn is None or not nn.next_title:
        return ""
    return tr("card_then", time=clock(nn.next_start), title=nn.next_title).strip()


def channel_a11y(row: ChannelRow, nn: NowNext | None, playing: bool = False) -> str:
    parts = [f"{row.number} {row.name}".strip()]
    if nn is not None and nn.title:
        parts.append(tr("epg_now_prefix", title=nn.title))
    nxt = then_text(nn)
    if nxt:
        parts.append(nxt)
    parts += [pill_text(k, t) for k, t in card_pills(Card(pills=row.pills), playing)]
    return ", ".join(p for p in parts if p)


# -- pictures ------------------------------------------------------------------------

class Pictures:
    """Pictures scaled once to a box - covering it (a poster, a still) or
    fitting in it (a channel's logo) - at the screen's pixel ratio, kept per
    picture, size and ratio, the least recently painted going first once
    *max_bytes* is spent. A smooth scale of every card's picture on every
    paint was most of what a scroll step cost."""

    def __init__(self, max_bytes: int = 24 * 1024 * 1024) -> None:
        self.max_bytes = max_bytes
        self._kept: OrderedDict[tuple, QPixmap] = OrderedDict()
        self._bytes = 0

    def get(self, pm: QPixmap, w: int, h: int, dpr: float = 1.0,
            fit: bool = False) -> QPixmap:
        key = (pm.cacheKey(), w, h, round(dpr * 100), fit)
        hit = self._kept.get(key)
        if hit is not None:
            self._kept.move_to_end(key)
            return hit
        mode = (Qt.AspectRatioMode.KeepAspectRatio if fit
                else Qt.AspectRatioMode.KeepAspectRatioByExpanding)
        img = pm.scaled(max(1, round(w * dpr)), max(1, round(h * dpr)), mode,
                        Qt.TransformationMode.SmoothTransformation)
        img.setDevicePixelRatio(dpr)
        self._kept[key] = img
        self._bytes += max(1024, img.width() * img.height() * 4)
        while len(self._kept) > 1 and self._bytes > self.max_bytes:
            _k, old = self._kept.popitem(last=False)
            self._bytes -= max(1024, old.width() * old.height() * 4)
        return img

    def clear(self) -> None:
        self._kept.clear()
        self._bytes = 0

    def __len__(self) -> int:
        return len(self._kept)


def _dpr(p: QPainter) -> float:
    dev = p.device()
    try:
        return float(dev.devicePixelRatioF()) if dev is not None else 1.0
    except Exception:
        return 1.0


def paint_picture(p: QPainter, r: QRect, pm: QPixmap | None, pics: Pictures,
                  radius: float, placeholder: str = "", fit: bool = False,
                  dim: bool = False) -> None:
    """A picture in *r* with rounded corners - covering it, or (*fit*) a
    logo fitted inside on the surface - else the surface with
    *placeholder* (initials) on it."""
    path = QPainterPath()
    path.addRoundedRect(QRectF(r), radius, radius)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setClipPath(path)
    p.fillPath(path, colour("btn"))
    if pm is not None and not pm.isNull():
        dpr = _dpr(p)
        if fit:
            inset = max(2, round(min(r.width(), r.height()) * 0.08))
            box = r.adjusted(inset, inset, -inset, -inset)
        else:
            box = r
        img = pics.get(pm, box.width(), box.height(), dpr, fit)
        iw, ih = img.width() / dpr, img.height() / dpr
        p.drawPixmap(QPointF(box.x() + (box.width() - iw) / 2,
                             box.y() + (box.height() - ih) / 2), img)
        if dim:
            p.fillPath(path, colour("bg", 0.45))
    if (pm is None or pm.isNull() or dim) and placeholder:
        p.setPen(colour("text", 0.85))
        p.setFont(px_font(max(11, min(28, r.height() // 4)), BOLD))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, placeholder)
    p.restore()


def paint_bar(p: QPainter, r: QRectF, frac: float, rtl: bool,
              track: QColor | None = None) -> None:
    """How far: a rounded track and the accent's part of it, from the start
    of the line."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    rad = r.height() / 2
    p.setBrush(track if track is not None else colour("btn"))
    p.drawRoundedRect(r, rad, rad)
    if frac > 0:
        done = QRectF(r.x(), r.y(), max(r.height(), r.width() * min(1.0, frac)), r.height())
        if rtl:
            done.moveRight(r.right())
        p.setBrush(colour("accent"))
        p.drawRoundedRect(done, rad, rad)
    p.restore()


def paint_ring(p: QPainter, r: QRectF, radius: float, width: float = 2.0) -> None:
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(colour("accent"), width)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    half = width / 2
    p.drawRoundedRect(r.adjusted(half, half, -half, -half), radius, radius)
    p.restore()


def _aligned(rtl: bool) -> Qt.AlignmentFlag:
    """The start of the line - absolute: a painter on a right-to-left
    widget would turn a plain AlignRight back into the left edge."""
    return (Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft) \
        | Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignAbsolute


def begin_paint(p: QPainter) -> None:
    """Each text in its own direction (a Latin title stays Latin in an
    Arabic window) - the painter took the widget's otherwise."""
    p.save()
    p.setLayoutDirection(Qt.LayoutDirection.LayoutDirectionAuto)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)


def _mirror(rect: QRect, outer: QRect, rtl: bool) -> QRect:
    if not rtl:
        return rect
    return QStyle.visualRect(Qt.LayoutDirection.RightToLeft, outer, rect)


# -- the grid's measures (pure) -----------------------------------------------------

#: Poster and wide cards: the picture's height per unit of width.
ASPECT = {"poster": 1.5, "wide": 9 / 16}
#: The narrowest a card may be, per layout: the grid takes as many columns
#: as fit at this width and widens them to fill the row.
MIN_CARD = {"poster": 150, "wide": 220}
GRID_GAP = 18
#: Room round a card's picture inside its tile (hover and focus show it).
CARD_PAD = 6
#: From the picture to the title, from the title to its line.
TEXT_TOP, TEXT_GAP = 8, 2


@dataclass(frozen=True)
class GridMetrics:
    """A grid's measures for one width: *cols* columns of *slot_w* each
    (the view's cells), a card *card_w* wide inside its slot - the first
    flush with the start, the last with the end, *gap* between them -
    and *cell_h* tall with the row's gap under it."""
    cols: int
    slot_w: int
    card_w: int
    cell_h: int
    gap: int
    picture_h: int

    @property
    def card_h(self) -> int:
        return self.cell_h - self.gap

    def lines(self, n: int) -> int:
        return (max(0, n) + self.cols - 1) // self.cols

    def height(self, n: int) -> int:
        return self.lines(n) * self.cell_h


def grid_metrics(avail: int, layout: str = "poster", title_h: int = 17, line_h: int = 14,
                 gap: int = GRID_GAP, min_card: int | None = None) -> GridMetrics:
    """The grid for *avail* pixels of width: as many columns as fit at the
    layout's narrowest card, then widened to fill the row exactly."""
    avail = max(2, int(avail))
    min_w = min_card or MIN_CARD.get(layout, MIN_CARD["poster"])
    # Qt wraps a row once the next cell would reach the width itself: the
    # cells together stay a pixel short of it.
    room = avail - 1
    cols = max(1, (room + gap) // (min_w + gap))
    slot_w = max(1, room // cols)
    card_w = max(1, (cols * slot_w - (cols - 1) * gap) // cols)
    pic_w = max(1, card_w - 2 * CARD_PAD)
    pic_h = round(pic_w * ASPECT.get(layout, ASPECT["poster"]))
    cell_h = CARD_PAD + pic_h + TEXT_TOP + title_h + TEXT_GAP + line_h + CARD_PAD + gap
    return GridMetrics(cols, slot_w, card_w, cell_h, gap, pic_h)


def left_column(slot_x: int, m: GridMetrics, view_w: int, rtl: bool) -> int:
    """The column a slot at *slot_x* is in, counted from the left: Qt lays a
    right-to-left grid out from the right edge."""
    origin = max(0, view_w - m.cols * m.slot_w) if rtl else 0
    col = round((slot_x - origin) / max(1, m.slot_w))
    return max(0, min(m.cols - 1, col))


def card_in_slot(slot: QRect, m: GridMetrics, col: int) -> QRect:
    """The card inside its slot: the slots are equal, the cards equal with
    *gap* between them, the first flush with the left edge and the last
    with the right - so each card sits a little further into its slot."""
    off = round(col * (m.cols * m.slot_w - m.card_w) / (m.cols - 1)) - col * m.slot_w \
        if m.cols > 1 else (m.slot_w - m.card_w) // 2
    return QRect(slot.x() + off, slot.y(), m.card_w, m.card_h)


def card_parts(card: QRect, m: GridMetrics, title_h: int, line_h: int) -> dict[str, QRect]:
    """Where a card's parts go: its picture at the top inside the tile's
    padding, the title and the line under it."""
    pic = QRect(card.x() + CARD_PAD, card.y() + CARD_PAD, card.width() - 2 * CARD_PAD,
                m.picture_h)
    title = QRect(pic.x(), pic.bottom() + 1 + TEXT_TOP, pic.width(), title_h)
    line = QRect(pic.x(), title.bottom() + 1 + TEXT_GAP, pic.width(), line_h)
    return {"card": card, "picture": pic, "title": title, "line": line}


# -- a list's and a channel's rows (pure) -------------------------------------------

LIST_PAD_X, LIST_PAD_Y = 14, 10
#: A list row's small picture: a poster's 2:3 or a still's 16:9, 54 tall.
LIST_PIC = {"poster": QSize(36, 54), "wide": QSize(96, 54)}
LIST_ROW_H = 54 + 2 * LIST_PAD_Y


def list_parts(rect: QRect, picture: str, pills_w: int, rtl: bool) -> dict[str, QRect]:
    """A list row's parts: its small picture at the start, the title and
    line beside it, the pills at the end - mirrored right to left."""
    size = LIST_PIC.get(picture, LIST_PIC["poster"])
    pic = QRect(rect.x() + LIST_PAD_X, rect.y() + (rect.height() - size.height()) // 2,
                size.width(), size.height())
    end = rect.right() - LIST_PAD_X
    pills = QRect(end - pills_w + 1, rect.y(), pills_w, rect.height())
    tx = pic.right() + 1 + 14
    text = QRect(tx, rect.y() + LIST_PAD_Y, max(10, (pills.left() - (12 if pills_w else 0))
                                               - tx), rect.height() - 2 * LIST_PAD_Y)
    return {k: _mirror(v, rect, rtl) for k, v in
            (("picture", pic), ("text", text), ("pills", pills))}


#: (row height, logo size) per density.
CHANNEL_DENSITY = {"compact": (56, 36), "airy": (84, 52)}
CH_PAD_X = 16
CH_NUM_W = 28
CH_GAP = 14
#: The next programme's own column (compact) from this row width.
CH_NEXT_FROM = 640
CH_NEXT_W = 200


def channel_parts(rect: QRect, density: str, rtl: bool) -> dict[str, QRect]:
    """A channel row's parts: number, logo, the name's column, what is on
    (and, airy, what is next under it), and in a compact row wide enough
    the next programme in a column of its own at the end."""
    row_h, logo = CHANNEL_DENSITY.get(density, CHANNEL_DENSITY["compact"])
    x = rect.x() + CH_PAD_X
    num = QRect(x, rect.y(), CH_NUM_W, rect.height())
    x = num.right() + 1 + CH_GAP
    logo_r = QRect(x, rect.y() + (rect.height() - logo) // 2, logo, logo)
    x = logo_r.right() + 1 + CH_GAP
    end = rect.right() - CH_PAD_X
    width = end - x + 1
    name_w = max(96, min(200, round(width * 0.28)))
    name = QRect(x, rect.y(), name_w, rect.height())
    x = name.right() + 1 + CH_GAP
    nxt = QRect()
    if density != "airy" and rect.width() >= CH_NEXT_FROM:
        nxt = QRect(end - CH_NEXT_W + 1, rect.y(), CH_NEXT_W, rect.height())
        end = nxt.left() - CH_GAP
    now = QRect(x, rect.y(), max(10, end - x + 1), rect.height())
    parts = {"num": num, "logo": logo_r, "name": name, "now": now, "next": nxt}
    return {k: (_mirror(v, rect, rtl) if v.isValid() else v) for k, v in parts.items()}


# -- the sideways row's card (pure) -------------------------------------------------

STRIP_CARD_W, STRIP_GAP, STRIP_PAD = 272, 12, 8
STRIP_PIC = {"poster": QSize(40, 60), "wide": QSize(104, 58)}
STRIP_RANK_W = 26


def strip_height(picture: str) -> int:
    return STRIP_PIC.get(picture, STRIP_PIC["wide"]).height() + 2 * STRIP_PAD


def strip_parts(rect: QRect, picture: str, ranked: bool, rtl: bool) -> dict[str, QRect]:
    """A small sideways card's parts in its slot: the card (the slot less
    the gap at its end - the left in a right-to-left row), a rank, the
    picture and the text beside it, mirrored inside the card."""
    card = QRect(rect.x() + (STRIP_GAP if rtl else 0), rect.y(),
                 max(1, rect.width() - STRIP_GAP), rect.height())
    size = STRIP_PIC.get(picture, STRIP_PIC["wide"])
    x = card.x() + STRIP_PAD
    rank = QRect()
    if ranked:
        rank = QRect(x, card.y(), STRIP_RANK_W, card.height())
        x = rank.right() + 1 + 6
    pic = QRect(x, card.y() + (card.height() - size.height()) // 2, size.width(),
                size.height())
    tx = pic.right() + 1 + 12
    text = QRect(tx, card.y() + STRIP_PAD, max(10, card.right() - 14 - tx + 1),
                 card.height() - 2 * STRIP_PAD)
    parts = {"rank": rank, "picture": pic, "text": text}
    out = {k: (_mirror(v, card, rtl) if v.isValid() else v) for k, v in parts.items()}
    out["card"] = card
    return out


# -- the model -------------------------------------------------------------------------

class CardModel(QAbstractListModel):
    """The cards (or channel rows) a view shows: kept as given, never
    copied through data() - a delegate reads ``item_at``. A new list is one
    reset; ``append`` adds a page at the end without one."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: list = []
        self._rows: dict[str, int] = {}
        #: The key that plays - the view's, for the screen reader's words.
        self.playing_key = ""
        self.nownext: Callable[[Any], NowNext | None] | None = None

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._items)

    def flags(self, index):
        if index.isValid() and 0 <= index.row() < len(self._items) \
                and is_heading(self._items[index.row()]):
            return Qt.ItemFlag.NoItemFlags      # a heading is never picked
        return super().flags(index)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        it = self._items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return getattr(it, "title", None) or getattr(it, "name", "") or ""
        if role == Qt.ItemDataRole.AccessibleTextRole:
            playing = bool(it.key) and it.key == self.playing_key
            if isinstance(it, ChannelRow):
                nn = it.now if it.now is not None else (
                    self.nownext(it) if self.nownext is not None else None)
                return channel_a11y(it, nn, playing)
            return card_a11y(it, playing)
        if role == Qt.ItemDataRole.ToolTipRole:
            title = getattr(it, "title", None) or getattr(it, "name", "")
            line = getattr(it, "line", "")
            return f"{title}\n{line}" if line else title
        return None

    def set_items(self, items: Iterable) -> None:
        self.beginResetModel()
        self._items = list(items)
        self._rows = {}
        for i, it in enumerate(self._items):
            if it.key and it.key not in self._rows:
                self._rows[it.key] = i
        self.endResetModel()

    def append(self, items: Iterable) -> None:
        new = list(items)
        if not new:
            return
        first = len(self._items)
        self.beginInsertRows(QModelIndex(), first, first + len(new) - 1)
        for i, it in enumerate(new, first):
            self._items.append(it)
            if it.key and it.key not in self._rows:
                self._rows[it.key] = i
        self.endInsertRows()

    def insert(self, at: int, items: Iterable) -> None:
        """*items* put in before row *at* without a reset (the selection,
        the current item and the scroll stay)."""
        new = list(items)
        if not new:
            return
        at = max(0, min(int(at), len(self._items)))
        self.beginInsertRows(QModelIndex(), at, at + len(new) - 1)
        self._items[at:at] = new
        self._index_keys()
        self.endInsertRows()

    def remove(self, rows: Iterable[int]) -> None:
        """The items at *rows* taken out without a reset, a run of them at
        a time from the last."""
        runs: list[list[int]] = []
        for r in sorted({int(r) for r in rows if 0 <= int(r) < len(self._items)}):
            if runs and r == runs[-1][1] + 1:
                runs[-1][1] = r
            else:
                runs.append([r, r])
        if not runs:
            return
        for a, b in reversed(runs):
            self.beginRemoveRows(QModelIndex(), a, b)
            del self._items[a:b + 1]
            self._index_keys()
            self.endRemoveRows()

    def _index_keys(self) -> None:
        self._rows = {}
        for i, it in enumerate(self._items):
            if it.key and it.key not in self._rows:
                self._rows[it.key] = i

    def items(self) -> list:
        return list(self._items)

    def item_at(self, row: int):
        return self._items[row] if 0 <= row < len(self._items) else None

    def row_of(self, key: str) -> int:
        return self._rows.get(key, -1) if key else -1

    def changed(self, key: str) -> None:
        """The item of *key* drawn again (its pills or progress changed)."""
        row = self.row_of(key)
        if row >= 0:
            idx = self.index(row, 0)
            self.dataChanged.emit(idx, idx)


# -- the views' shared ground -------------------------------------------------------

class _PaintedView(QListView):
    """What every painted list here shares: a model of cards or rows, a
    picture source, the one playing marked, Enter / a click / the menu key
    / the back button said as signals, and - shown inside a page that
    scrolls (``set_host``) - as tall as its content, the page scrolling,
    with pictures asked for only for what the page shows and a few lines
    past it."""

    #: The card or row (Enter, a click where one plays, a double click).
    activated = pyqtSignal(object)
    menu = pyqtSignal(object, QPoint)
    #: The current item moved (by the keyboard) - its rect in the view.
    moved = pyqtSignal(QRect)
    back = pyqtSignal()
    current_item_changed = pyqtSignal(object)

    #: Lines of cards past the screen's edges whose pictures are asked for.
    AHEAD = 2
    #: One click plays (or opens) unless ``click_plays`` says otherwise.
    CLICK_DEFAULT = True
    OBJECT_NAME = "MediaCards"

    def __init__(self, loader=None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName(self.OBJECT_NAME)
        self.setUniformItemSizes(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        self.model_ = CardModel(self)
        self.setModel(self.model_)
        self.pictures = Pictures()
        self.images = ImageSource(loader, self._repaint, self._arrived)
        self.playing_key = ""
        self.click_plays: Callable[[], bool] | None = None
        self._host: QScrollArea | None = None
        self._ahead: tuple | None = None
        self._fit_h = -1
        self.clicked.connect(self._clicked)
        self.selectionModel().currentChanged.connect(self._current_moved)
        self.model_.modelReset.connect(self._contents_changed)
        self.model_.rowsInserted.connect(self._contents_changed)
        self.model_.rowsRemoved.connect(self._contents_changed)
        self.restyle()

    # -- look --------------------------------------------------------------------

    def restyle(self) -> None:
        """The page's own colour under the cards, opaque: a scroll then
        moves what is drawn and paints only the strip coming in."""
        name = self.objectName() or self.OBJECT_NAME
        self.setStyleSheet(f"QListView#{name} {{ background:{P['bg']}; border:none;"
                           " outline:none; }")
        self.viewport().setAutoFillBackground(True)
        pal = self.viewport().palette()
        pal.setColor(self.viewport().backgroundRole(), QColor(P["bg"]))
        self.viewport().setPalette(pal)
        self.pictures.clear()
        self.viewport().update()

    # -- what it shows -----------------------------------------------------------

    def set_items(self, items: Iterable) -> None:
        self._ahead = None
        self.model_.set_items(items)

    def append_items(self, items: Iterable) -> None:
        self.model_.append(items)

    def insert_items(self, at: int, items: Iterable) -> None:
        """*items* put in before row *at*, the selection and scroll kept."""
        self.model_.insert(at, items)

    def remove_rows(self, rows: Iterable[int]) -> None:
        """The items at *rows* taken out, the selection and scroll kept."""
        self.model_.remove(rows)

    def items(self) -> list:
        return self.model_.items()

    def item_at(self, row: int):
        return self.model_.item_at(row)

    def current_item(self):
        idx = self.currentIndex()
        return self.model_.item_at(idx.row()) if idx.isValid() else None

    def row_of(self, key: str) -> int:
        return self.model_.row_of(key)

    def set_playing(self, key: str | None) -> None:
        """The item that plays now (marked; "" for none)."""
        key = key or ""
        if key == self.playing_key:
            return
        old, self.playing_key = self.playing_key, key
        self.model_.playing_key = key
        for k in (old, key):
            row = self.model_.row_of(k)
            if row >= 0:
                self.update(self.model_.index(row, 0))

    def focus_key(self, key: str, take_focus: bool = True) -> bool:
        """The item of *key* current and on screen."""
        row = self.model_.row_of(key)
        if row < 0:
            return False
        idx = self.model_.index(row, 0)
        self.setCurrentIndex(idx)
        self.reveal(idx)
        if take_focus:
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        return True

    def reveal(self, idx: QModelIndex) -> None:
        if self._host is None:
            self.scrollTo(idx)
        else:
            self.moved.emit(self.visualRect(idx))

    # -- a page that scrolls --------------------------------------------------------

    def set_host(self, area: QScrollArea | None) -> None:
        """Shown in *area*, which scrolls: the view as tall as its content,
        its own scroll bars off, the wheel passed on to the page; None: the
        view scrolls itself."""
        if self._host is not None and not sip.isdeleted(self._host):
            try:
                self._host.verticalScrollBar().valueChanged.disconnect(self._ask_ahead)
            except (TypeError, RuntimeError):
                pass
        self._host = area
        fit = area is not None
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff if fit
                                        else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed if fit else QSizePolicy.Policy.Expanding)
        if fit:
            area.verticalScrollBar().valueChanged.connect(self._ask_ahead)
        else:
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
            self._fit_h = -1
        self._relayout()

    def is_fitted(self) -> bool:
        return self._host is not None

    def content_height(self) -> int:
        raise NotImplementedError

    def _fit(self) -> None:
        if self._host is None:
            return
        h = max(4, self.content_height())
        if h != self._fit_h:
            self._fit_h = h
            self.setFixedHeight(h)

    def layout_width(self) -> int:
        """The width Qt lays items out in: the viewport the view could have,
        less a vertical scroll bar's room when it may show one (Qt keeps
        that room for a left-to-right flow even while the bar is hidden)."""
        w = self.maximumViewportSize().width()
        if (self.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
                and self.flow() == QListView.Flow.LeftToRight and self.isWrapping()):
            w -= self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent, None,
                                          self.verticalScrollBar())
        return max(1, w)

    def _relayout(self) -> None:
        self.scheduleDelayedItemsLayout()
        self._fit()
        self._ahead = None
        QTimer.singleShot(0, self._ask_ahead)

    def _contents_changed(self, *_a) -> None:
        self._fit()
        self._ahead = None
        QTimer.singleShot(0, self._ask_ahead)

    # -- what is on screen -----------------------------------------------------------

    def shown_span(self) -> tuple[int, int]:
        """The top and bottom of what shows of the view, in its viewport's
        coordinates: what the page's scroll area shows of it, or its own
        viewport."""
        vp = self.viewport()
        if self._host is not None and not sip.isdeleted(self._host):
            hv = self._host.viewport()
            top = vp.mapFrom(hv, QPoint(0, 0)).y()
            return top, top + hv.height() - 1
        return 0, vp.height() - 1

    def rows_on_screen(self, margin: int = 0) -> range:
        """The model rows shown now, *margin* more lines at either end."""
        raise NotImplementedError

    def wanted_urls(self, item) -> list[str]:
        """The pictures one item paints."""
        if isinstance(item, ChannelRow):
            return [item.logo] if item.logo else []
        return [u for u in (item.image, item.fallback) if u]

    def _ask_ahead(self, *_a) -> None:
        if sip.isdeleted(self) or self.images.loader is None or not self.isVisible():
            return
        rows = self.rows_on_screen(self.AHEAD)
        key = (self.model_.rowCount(), rows.start, rows.stop)
        if key == self._ahead:
            return
        self._ahead = key
        for row in rows:
            it = self.model_.item_at(row)
            if it is None:
                continue
            for url in self.wanted_urls(it)[:1]:
                self.images.want(url)

    def _repaint(self) -> None:
        if not sip.isdeleted(self):
            self.viewport().update()

    def _arrived(self, url: str) -> None:
        """A picture is in: the items on screen that show it painted again,
        nothing else."""
        if sip.isdeleted(self):
            return
        for row in self.rows_on_screen():
            it = self.model_.item_at(row)
            if it is not None and url in self.wanted_urls(it):
                self.update(self.model_.index(row, 0))

    # -- input ---------------------------------------------------------------------

    def _one_click(self) -> bool:
        if self.click_plays is None:
            return self.CLICK_DEFAULT
        try:
            return bool(self.click_plays())
        except Exception:
            return self.CLICK_DEFAULT

    def _clicked(self, idx) -> None:
        if self._one_click():
            it = self.model_.item_at(idx.row())
            if it is not None and not is_heading(it):
                self.activated.emit(it)

    def _current_moved(self, cur, _prev) -> None:
        it = self.model_.item_at(cur.row()) if cur.isValid() else None
        self.current_item_changed.emit(it)
        if cur.isValid() and self._host is not None:
            self.moved.emit(self.visualRect(cur))

    def keyPressEvent(self, e) -> None:
        k = e.key()
        plain = not (e.modifiers() & (Qt.KeyboardModifier.ControlModifier
                                      | Qt.KeyboardModifier.AltModifier
                                      | Qt.KeyboardModifier.MetaModifier))
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and plain:
            it = self.current_item()
            if it is not None and not is_heading(it):
                self.activated.emit(it)
            return
        if k == Qt.Key.Key_Menu:
            idx = self.currentIndex()
            it = self.model_.item_at(idx.row()) if idx.isValid() else None
            if it is not None and not is_heading(it):
                r = self.visualRect(idx)
                self.menu.emit(it, self.viewport().mapToGlobal(r.center()))
            return
        super().keyPressEvent(e)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.BackButton:
            self.back.emit()
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:
        e.accept()
        if self._one_click() or e.button() != Qt.MouseButton.LeftButton:
            return                      # the first click of the two did it
        it = self.model_.item_at(self.indexAt(e.position().toPoint()).row())
        if it is not None and not is_heading(it):
            self.activated.emit(it)

    def contextMenuEvent(self, e) -> None:
        idx = self.indexAt(e.pos())
        it = self.model_.item_at(idx.row()) if idx.isValid() else None
        if it is None or is_heading(it):
            return
        self.setCurrentIndex(idx)
        self.menu.emit(it, e.globalPos())

    def wheelEvent(self, e) -> None:
        if self._host is not None:
            e.ignore()                  # the page scrolls; this is as tall as its cards
            return
        super().wheelEvent(e)

    def focusInEvent(self, e) -> None:
        super().focusInEvent(e)
        if not self.currentIndex().isValid() and self.model_.rowCount():
            self.setCurrentIndex(self.model_.index(0, 0))
        self.viewport().update()

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        self.viewport().update()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._ahead = None
        QTimer.singleShot(0, self._ask_ahead)

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        self._ask_ahead()

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() == QEvent.Type.LayoutDirectionChange:
            self._relayout()


# -- cards in a grid or a list --------------------------------------------------------

class CardDelegate(QStyledItemDelegate):
    """A card: in a grid its picture (a poster or a still) with the pills
    on it and how far along its foot, the title and a muted line under it;
    in a list a row with a small picture, the title, the line and the pills
    at its end."""

    def __init__(self, view: "CardView") -> None:
        super().__init__(view)
        self._view = view

    def sizeHint(self, option, index) -> QSize:
        v = self._view
        head = is_heading(v.model_.item_at(index.row()))
        if v.layout_kind == "list":
            return QSize(1, v.HEAD_H if head else LIST_ROW_H)   # Qt makes a list's rows as wide as it
        m = v.metrics
        if head:
            return QSize(m.cols * m.slot_w, v.HEAD_H)       # a line of its own
        return QSize(m.slot_w, m.cell_h)

    def paint(self, p: QPainter, option, index) -> None:
        v = self._view
        card = v.model_.item_at(index.row())
        if card is None:
            return
        rtl = option.direction == Qt.LayoutDirection.RightToLeft
        if is_heading(card):
            self._paint_heading(p, option.rect, card, rtl)
            return
        playing = bool(card.key) and card.key == v.playing_key
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        focus = selected and v.hasFocus() and index == v.currentIndex()
        begin_paint(p)
        if v.layout_kind == "list":
            self._paint_row(p, option.rect, index.row(), card, rtl, playing, hover, selected)
        else:
            self._paint_card(p, option.rect, card, rtl, playing, hover, selected, focus)
        p.restore()

    def _paint_heading(self, p: QPainter, rect: QRect, card: Card, rtl: bool) -> None:
        """A heading's words at its line's foot, where the cards begin."""
        begin_paint(p)
        f = px_font(HEAD_PX, QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(P["text"]))
        pad = 4 if self._view.layout_kind != "list" else CH_PAD_X
        box = rect.adjusted(pad, 0, -pad, -8)
        text = QFontMetrics(f).elidedText(card.title, Qt.TextElideMode.ElideRight, max(1, box.width()))
        align = (Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft) \
            | Qt.AlignmentFlag.AlignBottom
        p.drawText(box, int(align), text)
        p.restore()

    def picture_fits(self, card: Card, pm: QPixmap | None) -> bool:
        """Whether *card*'s picture is fitted inside its box rather than
        covering it - posters and stills cover; a page's own delegate may
        fit a folder's glyph or a square cover instead."""
        return False

    def _picture(self, card: Card) -> tuple[QPixmap | None, bool]:
        images = self._view.images
        pm = images.get(card.image) if card.image else None
        if pm is not None:
            return pm, True
        if card.fallback:
            return images.get(card.fallback), False
        return None, False

    def _paint_card(self, p, slot, card, rtl, playing, hover, selected, focus) -> None:
        v = self._view
        m = v.metrics
        col = left_column(slot.x(), m, v.viewport().width(), rtl)
        box = card_in_slot(slot, m, col)
        th, lh = v.text_heights()
        parts = card_parts(box, m, th, lh)
        tile_r = corner_radius(12)
        if selected or hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(surface(2.0 if selected else 1.4))
            p.drawRoundedRect(QRectF(box), tile_r, tile_r)
        pic = parts["picture"]
        radius = corner_radius(10)
        pm, own = self._picture(card)
        paint_picture(p, pic, pm, v.pictures, radius,
                      card.initials or initials_of(card.title),
                      fit=self.picture_fits(card, pm), dim=bool(pm) and not own)
        pills = card_pills(card, playing)
        start = [pl for pl in pills if pl[0] != "seen"]
        end = [pl for pl in pills if pl[0] == "seen"]
        prow = QRect(pic.x() + 8, pic.y() + 8, pic.width() - 16, pill_height())
        used = paint_pills(p, start, prow, rtl)
        if end and pills_width(end) + used + PILL_GAP <= prow.width():
            paint_pills(p, end, prow, rtl, at_end=True)
        frac = progress_of(card)
        if card.progress is not None and frac > 0:
            bar = QRectF(pic.x() + 8, pic.bottom() - 9, pic.width() - 16, 4)
            paint_bar(p, bar, frac, rtl, colour("bg", 0.7))
        if playing:
            paint_ring(p, QRectF(pic), radius, 2.0)
        elif focus:
            paint_ring(p, QRectF(box), tile_r, 2.0)
        align = _aligned(rtl)
        tf, lf = v.fonts()
        p.setFont(tf)
        p.setPen(colour("text"))
        _title_with_marks(p, parts["title"], card.title, card.marks, QFontMetrics(tf),
                          int(align), rtl)
        if card.line:
            lr = parts["line"]
            p.setFont(lf)
            p.setPen(colour("muted"))
            p.drawText(lr, int(align), QFontMetrics(lf).elidedText(
                card.line, Qt.TextElideMode.ElideRight, lr.width()))

    def _paint_row(self, p, rect, row, card, rtl, playing, hover, selected) -> None:
        v = self._view
        paint_row_surface(p, rect, row, v.model_.rowCount(), rtl, playing, hover, selected)
        pills = card_pills(card, playing)
        parts = list_parts(rect, v.list_picture, pills_width(pills) if pills else 0, rtl)
        pm, own = self._picture(card)
        paint_picture(p, parts["picture"], pm, v.pictures, corner_radius(6),
                      card.initials or initials_of(card.title),
                      fit=self.picture_fits(card, pm), dim=bool(pm) and not own)
        if pills:
            paint_pills(p, pills, parts["pills"], rtl)
        text = parts["text"]
        tf, lf = v.fonts()
        tfm, lfm = QFontMetrics(tf), QFontMetrics(lf)
        frac = progress_of(card)
        bar = card.progress is not None and frac > 0
        block = tfm.height() + (TEXT_GAP + lfm.height() if card.line else 0) + (9 if bar else 0)
        y = text.y() + (text.height() - block) // 2
        align = _aligned(rtl)
        p.setFont(tf)
        p.setPen(colour("text"))
        _title_with_marks(p, QRect(text.x(), y, text.width(), tfm.height()), card.title,
                          card.marks, tfm, int(align), rtl)
        y += tfm.height()
        if card.line:
            y += TEXT_GAP
            p.setFont(lf)
            p.setPen(colour("muted"))
            p.drawText(QRect(text.x(), y, text.width(), lfm.height()), int(align),
                       lfm.elidedText(card.line, Qt.TextElideMode.ElideRight, text.width()))
            y += lfm.height()
        if bar:
            w = min(240, text.width())
            x = text.right() - w + 1 if rtl else text.x()
            paint_bar(p, QRectF(x, y + 6, w, 3), frac, rtl)


def paint_row_surface(p: QPainter, rect: QRect, row: int, count: int, rtl: bool,
                      playing: bool, hover: bool, selected: bool) -> None:
    """A list's rows as one surface: rounded at its first row's top and its
    last row's foot, a hairline between rows (not over the first), hover and
    the chosen row a shade more, the one playing the accent's wash with its
    bar at the start of the line."""
    radius = corner_radius(12)
    top = row == 0
    bottom = row == count - 1
    path = QPainterPath()
    r = QRectF(rect)
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
    if playing:
        fill = playing_fill()
    elif selected:
        fill = colour("sel")
    elif hover:
        fill = surface(2.0)
    else:
        fill = surface(1.0)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.fillPath(path, fill)
    if not top and not playing and not selected:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(colour("bg", 0.9))
        p.drawRect(QRectF(r.x() + 12, r.y(), r.width() - 24, 1))
    if playing:
        bar = QRectF(r.right() - 3 if rtl else r.x(), r.y() + 8, 3, r.height() - 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(colour("accent"))
        p.drawRoundedRect(bar, 1.5, 1.5)
    p.restore()


class CardView(_PaintedView):
    """Titles as cards: 2:3 posters ("poster") or 16:9 cards ("wide") in a
    grid that fills its width, or rows of a list ("list") - the header's
    grid / list switch is ``set_layout``. Feed it ``Card``s
    (``set_items``, ``append_items``); it says ``activated(card)`` (a
    click, Enter) and ``menu(card, global_pos)``."""

    OBJECT_NAME = "MediaCardView"
    #: A heading's line (``Card.heading``): words over the cards after it,
    #: across the grid's width, never picked.
    HEAD_H = 44

    def __init__(self, loader=None, layout: str = "poster", parent=None) -> None:
        self.layout_kind = "poster"
        self.list_picture = "poster"
        self.metrics = grid_metrics(600, "poster")
        self._reflow_timer: QTimer | None = None
        self._reflow_pending = False
        #: Whether a heading is among the cards: the grid then lays out by
        #: each card's size (a heading a line of its own), else by cells.
        self._headed = False
        super().__init__(loader, parent)
        self.setItemDelegate(CardDelegate(self))
        self.setAccessibleName(tr("card_view_a11y"))
        self.set_layout(layout)

    def set_layout(self, layout: str, list_picture: str | None = None) -> None:
        """"poster" or "wide" (a grid) or "list"; a list's small pictures
        are posters or stills (*list_picture*, else the grid's kind)."""
        if layout not in ("poster", "wide", "list"):
            layout = "poster"
        if layout != "list":
            self.list_picture = layout
        if list_picture in ("poster", "wide"):
            self.list_picture = list_picture
        self.layout_kind = layout
        grid = layout != "list"
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setFlow(QListView.Flow.LeftToRight if grid else QListView.Flow.TopToBottom)
        self.setWrapping(grid)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setSpacing(0)
        self._remeasure(force=True)

    def set_host(self, area) -> None:
        super().set_host(area)
        self._remeasure(force=True)

    def fonts(self) -> tuple[QFont, QFont]:
        return px_font(TITLE_PX, BOLD), px_font(LINE_PX)

    def text_heights(self) -> tuple[int, int]:
        tf, lf = self.fonts()
        return QFontMetrics(tf).height(), QFontMetrics(lf).height()

    def _remeasure(self, force: bool = False) -> None:
        if self.layout_kind == "list":
            self.setGridSize(QSize())
            self._relayout()
            return
        th, lh = self.text_heights()
        m = grid_metrics(self.layout_width(), self.layout_kind, th, lh)
        if m != self.metrics or force:
            self.metrics = m
            self.setGridSize(QSize() if self._headed else QSize(m.slot_w, m.cell_h))
            self._relayout()

    def _contents_changed(self, *a) -> None:
        headed = any(is_heading(it) for it in self.model_.items())
        if headed != self._headed:
            self._headed = headed
            self.setUniformItemSizes(not headed)
            self._remeasure(force=True)
        super()._contents_changed(*a)

    def _lines(self) -> list[tuple[int, int, int, int]]:
        """(top, height, first row, last row) of each line of a view with
        headings: a heading a line of its own, the cards after it from the
        start of the next."""
        items = self.model_.items()
        listed = self.layout_kind == "list"
        cols = 1 if listed else max(1, self.metrics.cols)
        cell = LIST_ROW_H if listed else self.metrics.cell_h
        out: list[tuple[int, int, int, int]] = []
        y = i = 0
        n = len(items)
        while i < n:
            if is_heading(items[i]):
                out.append((y, self.HEAD_H, i, i))
                y += self.HEAD_H
                i += 1
                continue
            j = i
            while j < n and j - i < cols and not is_heading(items[j]):
                j += 1
            out.append((y, cell, i, j - 1))
            y += cell
            i = j
        return out

    def content_height(self) -> int:
        n = self.model_.rowCount()
        if self._headed:
            lines = self._lines()
            return (lines[-1][0] + lines[-1][1] if lines else 0) + 2
        if self.layout_kind == "list":
            return n * LIST_ROW_H + 2
        return self.metrics.height(n) + 2

    def moveCursor(self, action, modifiers):  # noqa: N802 - Qt's name
        """The arrows step over a heading to the card beyond it."""
        idx = super().moveCursor(action, modifiers)
        if not (self._headed and idx.isValid() and is_heading(self.model_.item_at(idx.row()))):
            return idx
        A = QAbstractItemView.CursorAction
        back = action in (A.MoveUp, A.MoveLeft, A.MovePrevious, A.MovePageUp, A.MoveHome)
        n = self.model_.rowCount()
        for step in ((-1, 1) if back else (1, -1)):
            row = idx.row() + step
            while 0 <= row < n and is_heading(self.model_.item_at(row)):
                row += step
            if 0 <= row < n:
                return self.model_.index(row, 0)
        return self.currentIndex()

    def rows_on_screen(self, margin: int = 0) -> range:
        n = self.model_.rowCount()
        if not n:
            return range(0)
        top, bottom = self.shown_span()
        off = self.verticalOffset() if self._host is None else 0
        top, bottom = top + off, bottom + off
        if bottom < 0:
            return range(0)
        if self._headed:
            lines = self._lines()
            shown = [k for k, (y, h, _a, _b) in enumerate(lines) if y + h > top and y <= bottom]
            if not shown:
                return range(0)
            first = max(0, shown[0] - margin)
            last = min(len(lines) - 1, shown[-1] + margin)
            return range(lines[first][2], lines[last][3] + 1)
        if self.layout_kind == "list":
            a = max(0, top // LIST_ROW_H - margin)
            b = min(n - 1, bottom // LIST_ROW_H + margin)
            return range(a, b + 1) if b >= a else range(0)
        m = self.metrics
        first = max(0, top // m.cell_h - margin)
        last = bottom // m.cell_h + margin
        a, b = first * m.cols, min(n - 1, (last + 1) * m.cols - 1)
        return range(a, b + 1) if b >= a else range(0)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        # A splitter drag resizes on every move: the first step reflows at
        # once, the rest of the burst once at its end.
        t = self._reflow_timer
        if t is None:
            t = self._reflow_timer = QTimer(self)
            t.setSingleShot(True)
            t.setInterval(50)
            t.timeout.connect(self._reflow_trailing)
        if t.isActive():
            self._reflow_pending = True
            return
        self._remeasure()
        t.start()

    def _reflow_trailing(self) -> None:
        if self._reflow_pending:
            self._reflow_pending = False
            self._remeasure()
            if self._reflow_timer is not None:
                self._reflow_timer.start()


# -- channels ----------------------------------------------------------------------

class ChannelDelegate(QStyledItemDelegate):
    """A channel's row: its number, its logo, its name (and Playing under
    it), what is on - the time it began, the title, a bar of how far - and
    "Then ..." under it (airy) or in a column at the end (compact)."""

    def __init__(self, view: "ChannelView") -> None:
        super().__init__(view)
        self._view = view

    def sizeHint(self, option, index) -> QSize:
        return QSize(1, self._view.row_height())   # as wide as the view, by Qt

    def paint(self, p: QPainter, option, index) -> None:
        v = self._view
        row = v.model_.item_at(index.row())
        if row is None:
            return
        rtl = option.direction == Qt.LayoutDirection.RightToLeft
        rect = option.rect
        playing = bool(row.key) and row.key == v.playing_key
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        begin_paint(p)
        paint_row_surface(p, rect, index.row(), v.model_.rowCount(), rtl, playing, hover,
                          selected)
        parts = channel_parts(rect, v.density, rtl)
        align = _aligned(rtl)
        small = px_font(SMALL_PX)
        sfm = QFontMetrics(small)
        if row.number:
            p.setFont(small)
            p.setPen(colour("muted"))
            num_align = (Qt.AlignmentFlag.AlignLeft if rtl else Qt.AlignmentFlag.AlignRight) \
                | Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignAbsolute
            p.drawText(parts["num"], int(num_align),
                       sfm.elidedText(row.number, Qt.TextElideMode.ElideLeft,
                                      parts["num"].width()))
        pm = v.images.get(row.logo) if row.logo else None
        paint_picture(p, parts["logo"], pm, v.pictures, corner_radius(8),
                      initials_of(row.name, 3), fit=True)
        name_f = px_font(TITLE_PX, BOLD)
        nfm = QFontMetrics(name_f)
        pills = card_pills(Card(pills=row.pills), playing)
        ph = pill_height()
        name_r = parts["name"]
        block = nfm.height() + (4 + ph if pills else 0)
        y = name_r.y() + (name_r.height() - block) // 2
        p.setFont(name_f)
        p.setPen(playing_ink() if playing else colour("text"))
        p.drawText(QRect(name_r.x(), y, name_r.width(), nfm.height()), int(align),
                   nfm.elidedText(row.name, Qt.TextElideMode.ElideRight, name_r.width()))
        if pills:
            paint_pills(p, pills, QRect(name_r.x(), y + nfm.height() + 4, name_r.width(), ph),
                        rtl)
        nn = v.now_next(row)
        self._paint_now(p, parts["now"], nn, rtl, v.density == "airy")
        nxt = parts["next"]
        if nxt.isValid() and nn is not None:
            text = then_text(nn)
            if text:
                p.setFont(small)
                p.setPen(colour("muted"))
                p.drawText(nxt, int(align), sfm.elidedText(text, Qt.TextElideMode.ElideRight,
                                                           nxt.width()))
        p.restore()

    def _paint_now(self, p: QPainter, r: QRect, nn: NowNext | None, rtl: bool,
                   airy: bool) -> None:
        if nn is None or not nn.title:
            return
        small = px_font(SMALL_PX)
        title_f = px_font(TITLE_PX, QFont.Weight.Medium)
        sfm, tfm = QFontMetrics(small), QFontMetrics(title_f)
        then = then_text(nn) if airy else ""
        frac = nn.fraction()
        line_h = max(sfm.height(), tfm.height())
        block = line_h + (4 + 3 if frac is not None else 0) + (4 + sfm.height() if then else 0)
        y = r.y() + (r.height() - block) // 2
        when = clock(nn.start)
        when_w = sfm.horizontalAdvance(when) + 8 if when else 0
        line = QRect(r.x(), y, r.width(), line_h)
        if when:
            p.setFont(small)
            p.setPen(colour("muted"))
            wr = QRect(line.right() - when_w + 1, y, when_w, line_h) if rtl \
                else QRect(line.x(), y, when_w, line_h)
            p.drawText(wr, int(_aligned(rtl)), when)
        tr_ = QRect(line.x(), y, line.width() - when_w, line_h) if rtl \
            else QRect(line.x() + when_w, y, line.width() - when_w, line_h)
        p.setFont(title_f)
        p.setPen(colour("text2"))
        p.drawText(tr_, int(_aligned(rtl)), tfm.elidedText(
            nn.title, Qt.TextElideMode.ElideRight, max(10, tr_.width())))
        y += line_h
        if frac is not None:
            w = min(360, r.width())
            x = r.right() - w + 1 if rtl else r.x()
            paint_bar(p, QRectF(x, y + 4, w, 3), frac, rtl)
            y += 7
        if then:
            p.setFont(small)
            p.setPen(colour("muted"))
            p.drawText(QRect(r.x(), y + 4, r.width(), sfm.height()), int(_aligned(rtl)),
                       sfm.elidedText(then, Qt.TextElideMode.ElideRight, r.width()))


class ChannelView(_PaintedView):
    """Channels as rows, compact or airy (``set_density``). Feed it
    ``ChannelRow``s; what is on comes with each row (``now``) or from
    ``guide`` - a callable asked as a row is painted, its answer kept per
    channel for the minute. One click picks a row; a double click or Enter
    plays (``activated``) - unless ``click_plays`` says one click does."""

    CLICK_DEFAULT = False
    OBJECT_NAME = "MediaChannelView"
    #: How often what is on is looked at again and its bar moved.
    TICK_MS = 30_000

    def __init__(self, loader=None, density: str = "compact", parent=None) -> None:
        self.density = "compact"
        #: A row's now and next, asked only for rows that paint.
        self.guide: Callable[[ChannelRow], NowNext | None] | None = None
        self._guide_kept: dict[str, tuple[int, NowNext | None]] = {}
        super().__init__(loader, parent)
        self.setItemDelegate(ChannelDelegate(self))
        self.setAccessibleName(tr("card_channels_a11y"))
        self.model_.nownext = self.now_next
        self._tick = QTimer(self)
        self._tick.setInterval(self.TICK_MS)
        self._tick.timeout.connect(self.tick)
        self.set_density(density)

    def set_density(self, density: str) -> None:
        self.density = density if density in CHANNEL_DENSITY else "compact"
        self._relayout()

    def row_height(self) -> int:
        return CHANNEL_DENSITY[self.density][0]

    def now_next(self, row: ChannelRow) -> NowNext | None:
        if row.now is not None or self.guide is None:
            return row.now
        minute = int(time.time() // 60)
        kept = self._guide_kept.get(row.key)
        if kept is not None and kept[0] == minute:
            return kept[1]
        try:
            nn = self.guide(row)
        except Exception:
            nn = None
        if len(self._guide_kept) > 4000:
            self._guide_kept.clear()
        self._guide_kept[row.key] = (minute, nn)
        return nn

    def set_items(self, items: Iterable) -> None:
        self._guide_kept.clear()
        super().set_items(items)

    def tick(self) -> None:
        """What is on looked at again for the rows on screen, their bars
        moved (the guide's answers are kept for the minute)."""
        if not sip.isdeleted(self):
            self.viewport().update()

    def content_height(self) -> int:
        return self.model_.rowCount() * self.row_height() + 2

    def rows_on_screen(self, margin: int = 0) -> range:
        n = self.model_.rowCount()
        if not n:
            return range(0)
        top, bottom = self.shown_span()
        off = self.verticalOffset() if self._host is None else 0
        h = self.row_height()
        a = max(0, (top + off) // h - margin * 4)
        b = min(n - 1, (bottom + off) // h + margin * 4)
        return range(a, b + 1) if b >= a and bottom + off >= 0 else range(0)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._ahead = None
        self._ask_ahead()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._tick.start()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._tick.stop()


# -- the sideways rows ---------------------------------------------------------------

class StripDelegate(QStyledItemDelegate):
    """A small sideways card: a rank (Popular), the picture, the title, a
    line and how far it got under them."""

    def __init__(self, strip: "CardStrip") -> None:
        super().__init__(strip)
        self._strip = strip

    def sizeHint(self, option, index) -> QSize:
        return QSize(STRIP_CARD_W + STRIP_GAP, strip_height(self._strip.picture))

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
        parts = strip_parts(option.rect, s.picture, card.rank > 0, rtl)
        begin_paint(p)
        radius = corner_radius(12)
        box = QRectF(parts["card"])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(playing_fill() if playing else surface(2.0 if (hover or selected) else 1.0))
        p.drawRoundedRect(box, radius, radius)
        if focus:
            paint_ring(p, box, radius)
        if card.rank > 0:
            p.setFont(px_font(20, BOLD))
            p.setPen(colour("accent"))
            p.drawText(parts["rank"], int(Qt.AlignmentFlag.AlignCenter), str(card.rank))
        pm = s.images.get(card.image) if card.image else None
        own = pm is not None
        if pm is None and card.fallback:
            pm = s.images.get(card.fallback)
        # A card may ask for its picture fitted inside the box (a logo).
        paint_picture(p, parts["picture"], pm, s.pictures, corner_radius(6),
                      card.initials or initials_of(card.title),
                      fit=getattr(card, "fit", None) is True, dim=bool(pm) and not own)
        text = parts["text"]
        tf, lf = px_font(TITLE_PX, BOLD), px_font(LINE_PX)
        tfm, lfm = QFontMetrics(tf), QFontMetrics(lf)
        frac = progress_of(card)
        bar = card.progress is not None and frac > 0
        pills = card_pills(card, playing)
        block = tfm.height() + (TEXT_GAP + lfm.height() if (card.line or pills) else 0) \
            + (9 if bar else 0)
        y = text.y() + (text.height() - block) // 2
        align = _aligned(rtl)
        p.setFont(tf)
        p.setPen(colour("text"))
        p.drawText(QRect(text.x(), y, text.width(), tfm.height()), int(align),
                   tfm.elidedText(card.title, Qt.TextElideMode.ElideRight, text.width()))
        y += tfm.height()
        if card.line or pills:
            y += TEXT_GAP
            line_r = QRect(text.x(), y, text.width(), lfm.height())
            used = 0
            if pills:
                used = paint_pills(p, pills[:1], line_r, rtl) + 8
            p.setFont(lf)
            p.setPen(colour("muted"))
            room = max(10, text.width() - used)
            lr = QRect(line_r.x() if rtl else line_r.x() + used, y, room, lfm.height())
            p.drawText(lr, int(align), lfm.elidedText(card.line, Qt.TextElideMode.ElideRight,
                                                     room))
            y += lfm.height()
        if bar:
            paint_bar(p, QRectF(text.x(), y + 6, text.width(), 3), frac, rtl)
        p.restore()


class CardStrip(_PaintedView):
    """Small cards in one row that scrolls sideways (the horizontal wheel,
    Shift and the wheel, the arrows, ``CardRow``'s buttons); the page's
    vertical wheel passes through to the page."""

    AHEAD = 3
    OBJECT_NAME = "MediaCardStrip"

    def __init__(self, loader=None, picture: str = "wide", parent=None) -> None:
        self.picture = picture if picture in STRIP_PIC else "wide"
        super().__init__(loader, parent)
        self.setItemDelegate(StripDelegate(self))
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(strip_height(self.picture))
        self.setAccessibleName(tr("card_row_a11y"))

    def set_picture(self, picture: str) -> None:
        self.picture = picture if picture in STRIP_PIC else "wide"
        self.setFixedHeight(strip_height(self.picture))
        self._relayout()

    def content_height(self) -> int:
        return strip_height(self.picture)

    def set_host(self, area) -> None:
        """A strip scrolls sideways itself; the page's vertical scroll is
        only followed for its pictures."""
        self._host = None

    def rows_on_screen(self, margin: int = 0) -> range:
        n = self.model_.rowCount()
        if not n:
            return range(0)
        slot = STRIP_CARD_W + STRIP_GAP
        left = self.horizontalOffset()
        a = max(0, left // slot - margin)
        b = min(n - 1, (left + self.viewport().width()) // slot + margin)
        return range(a, b + 1)

    def step(self, direction: int) -> None:
        """One screen on (+1) or back (-1), in the reading direction."""
        bar = self.horizontalScrollBar()
        page = max(STRIP_CARD_W, self.viewport().width() - STRIP_CARD_W // 2)
        # The bar's value runs in the reading direction in either language.
        bar.setValue(bar.value() + direction * page)

    def wheelEvent(self, e) -> None:
        d = e.pixelDelta() if not e.pixelDelta().isNull() else e.angleDelta()
        dx = d.x()
        if not dx and e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            dx = d.y()
        if not dx:
            e.ignore()                  # up and down: the page's
            return
        bar = self.horizontalScrollBar()
        bar.setValue(bar.value() - dx)
        e.accept()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._ahead = None
        self._ask_ahead()

    #: How far the page's colour fades in over a card cut at an end.
    FADE = 56

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        bar = self.horizontalScrollBar()
        more_back, more_on = bar.value() > bar.minimum(), bar.value() < bar.maximum()
        if not (more_back or more_on):
            return
        vp = self.viewport()
        w, h = vp.width(), vp.height()
        fade = min(self.FADE, w // 4)
        if fade <= 0:
            return
        rtl = self.isRightToLeft()
        ground = QColor(P["bg"])
        clear = QColor(ground)
        clear.setAlpha(0)
        p = QPainter(vp)
        for more, ahead in ((more_on, True), (more_back, False)):
            if not more:
                continue
            right = ahead != rtl        # what comes next lies to the right in LTR
            x0 = w - fade if right else 0
            g = QLinearGradient(float(x0), 0.0, float(x0 + fade), 0.0)
            g.setColorAt(0.0, clear if right else ground)
            g.setColorAt(1.0, ground if right else clear)
            p.fillRect(QRect(x0, 0, fade, h), g)
        p.end()

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        if dx:
            # The fades stay at the ends: what was moved is drawn again.
            self.viewport().update()


class CardRow(QWidget):
    """A heading ("Continue", "Popular right now") over a ``CardStrip``,
    with a button at either end while there is more that way; hidden while
    it has no cards. Given a name (``set_hide_key``: a row of
    core/places.ROWS) a right click on its heading offers "Hide this row"
    and says so (``hide_requested``) - the window keeps it hidden."""

    activated = pyqtSignal(object)
    menu = pyqtSignal(object, QPoint)
    hide_requested = pyqtSignal(str)

    def __init__(self, title: str = "", loader=None, picture: str = "wide",
                 parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("MediaCardRow")
        self._hide_key = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.heading = QLabel(title)
        self.heading.setObjectName("MediaRowHeading")
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.heading.customContextMenuRequested.connect(self._heading_menu)
        lay.addWidget(self.heading)
        self.strip = CardStrip(loader, picture)
        self.strip.activated.connect(self.activated)
        self.strip.menu.connect(self.menu)
        lay.addWidget(self.strip)
        self._back = self._arrow(-1)
        self._on = self._arrow(1)
        self.strip.horizontalScrollBar().valueChanged.connect(self._sync_arrows)
        self.strip.horizontalScrollBar().rangeChanged.connect(self._sync_arrows)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.restyle()
        self.setVisible(False)

    def _arrow(self, direction: int) -> QToolButton:
        b = QToolButton(self.strip)
        b.setObjectName("MediaRowArrow")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setFixedSize(30, 30)
        tip = tr("card_scroll_on") if direction > 0 else tr("card_scroll_back")
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.clicked.connect(lambda _c=False, d=direction: self.strip.step(d))
        b.hide()
        return b

    def restyle(self) -> None:
        self.heading.setStyleSheet(f"color:{P['text']}; font-size:15px; font-weight:600;"
                                   " background:transparent;")
        r = round(corner_radius(15))
        for b in (self._back, self._on):
            b.setStyleSheet(
                f"QToolButton#MediaRowArrow {{ background:{P['btn']}; color:{P['text']};"
                f" border:none; border-radius:{r}px; }}"
                f"QToolButton#MediaRowArrow:hover {{ background:{P['btn_hover']}; }}")
        self._arrow_dirs()
        self.strip.restyle()

    def _arrow_dirs(self) -> None:
        rtl = self.isRightToLeft()
        self._back.setArrowType(Qt.ArrowType.RightArrow if rtl else Qt.ArrowType.LeftArrow)
        self._on.setArrowType(Qt.ArrowType.LeftArrow if rtl else Qt.ArrowType.RightArrow)

    def set_title(self, text: str) -> None:
        self.heading.setText(text)
        self.heading.setVisible(bool(text))

    def set_hide_key(self, name: str) -> None:
        """The row's name among the rows that can be hidden; "" for none
        (its heading offers nothing)."""
        self._hide_key = str(name or "")

    def hide_key(self) -> str:
        return self._hide_key

    def heading_menu(self) -> QMenu | None:
        """The heading's menu: "Hide this row" - None for a row with no
        name."""
        if not self._hide_key:
            return None
        m = QMenu(self)
        act = m.addAction(tr("row_hide"))
        act.triggered.connect(lambda _c=False, k=self._hide_key: self.hide_requested.emit(k))
        return m

    def _heading_menu(self, pos: QPoint) -> None:
        m = self.heading_menu()
        if m is None:
            return
        m.exec(self.heading.mapToGlobal(pos))
        m.deleteLater()

    def set_cards(self, cards: Iterable[Card]) -> None:
        cards = list(cards)
        self.strip.set_items(cards)
        self.setVisible(bool(cards))
        QTimer.singleShot(0, self._sync_arrows)

    def cards(self) -> list[Card]:
        return self.strip.items()

    def set_playing(self, key: str | None) -> None:
        self.strip.set_playing(key)

    def _sync_arrows(self, *_a) -> None:
        if sip.isdeleted(self):
            return
        bar = self.strip.horizontalScrollBar()
        more_back = bar.value() > bar.minimum()
        more_on = bar.value() < bar.maximum()
        vh = self.strip.height()
        y = (vh - 30) // 2
        w = self.strip.width()
        rtl = self.isRightToLeft()
        self._back.move(w - 34 if rtl else 4, y)
        self._on.move(4 if rtl else w - 34, y)
        self._back.setVisible(more_back)
        self._on.setVisible(more_on)
        self._back.raise_()
        self._on.raise_()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._sync_arrows()

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() == QEvent.Type.LayoutDirectionChange:
            self._arrow_dirs()
            self._sync_arrows()


# -- the header ---------------------------------------------------------------------

def sort_icon(ink: str, size: int = 14, dpr: float = 1.0) -> QIcon:
    """The sort control's mark, drawn the same on every system: three
    lines, each shorter than the one above."""
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(ink), max(1.4, size * 0.12))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    s = float(size)
    for y, w in ((0.28, 0.72), (0.5, 0.5), (0.72, 0.28)):
        p.drawLine(QPointF(s * 0.14, s * y), QPointF(s * (0.14 + w), s * y))
    p.end()
    return QIcon(pm)


class Segmented(QWidget):
    """A segmented switch: two or three choices side by side on one filled
    track, the chosen one raised (grid / list, compact / airy). Arrows move
    between them."""

    changed = pyqtSignal(str)

    def __init__(self, options: Sequence[tuple[str, str]] = (), current: str = "",
                 name: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("MediaSegmented")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(3, 3, 3, 3)
        self._lay.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.idClicked.connect(self._picked)
        self._keys: list[str] = []
        self.buttons: list[QPushButton] = []
        if name:
            self.setAccessibleName(name)
        self.set_options(options, current)
        self.restyle()

    def set_options(self, options: Sequence[tuple[str, str]], current: str = "") -> None:
        for b in self.buttons:
            self._group.removeButton(b)
            self._lay.removeWidget(b)
            b.hide()
            b.deleteLater()
        self.buttons = []
        self._keys = []
        for i, (key, label) in enumerate(options):
            b = QPushButton(label)
            b.setObjectName("MediaSegment")
            b.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setAccessibleName(label)
            self._group.addButton(b, i)
            self._lay.addWidget(b)
            self.buttons.append(b)
            self._keys.append(key)
        self.set_current(current or (self._keys[0] if self._keys else ""))
        self.setVisible(bool(self._keys))

    def restyle(self) -> None:
        r = round(corner_radius(10))
        ri = max(0, r - 3)
        self.setStyleSheet(
            f"QWidget#MediaSegmented {{ background:{P['input']}; border-radius:{r}px; }}"
            f"QPushButton#MediaSegment {{ background:transparent; color:{P['muted']};"
            f" border:none; border-radius:{ri}px; padding:5px 12px; font-size:12px;"
            " font-weight:600; }"
            f"QPushButton#MediaSegment:hover {{ color:{P['text']}; }}"
            f"QPushButton#MediaSegment:checked {{ background:{P['btn_hover']};"
            f" color:{P['text']}; }}"
            f"QPushButton#MediaSegment:focus {{ outline:none; }}")

    def current(self) -> str:
        i = self._group.checkedId()
        return self._keys[i] if 0 <= i < len(self._keys) else ""

    def keys(self) -> list[str]:
        return list(self._keys)

    def set_current(self, key: str) -> None:
        if key in self._keys:
            self.buttons[self._keys.index(key)].setChecked(True)

    def _picked(self, i: int) -> None:
        if 0 <= i < len(self._keys):
            self.changed.emit(self._keys[i])

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right) and self._keys:
            step = -1 if e.key() == Qt.Key.Key_Left else 1
            if self.isRightToLeft():
                step = -step
            i = max(0, min(len(self._keys) - 1, self._group.checkedId() + step))
            if self._keys[i] != self.current():
                self.buttons[i].setChecked(True)
                self.buttons[i].setFocus()
                self.changed.emit(self._keys[i])
            return
        super().keyPressEvent(e)


class PageHeader(QWidget):
    """A place's header, one quiet line at one height: what it shows - the
    category, the folder - and its count in small muted words ("Drama ·
    30 series"), a way back before them where there is one (`add_lead`),
    and at the end the page's own controls, the sort (its button names the
    order) and a segmented switch. Empty choices hide their control. A long
    name is cut with "…" first, the count kept whole while the name keeps
    a little room (`TITLE_MIN`); the whole line is in the tooltip."""

    sort_changed = pyqtSignal(str)
    view_changed = pyqtSignal(str)
    #: The switch at the foot of the sort's menu (``set_sort_toggle``).
    sort_toggled = pyqtSignal(bool)

    #: The line's words in pixels: what is shown, its count.
    TITLE_PX = 13
    #: Every control at the header's end is this tall (the sort, the
    #: switch, a filter) - and so is the header: one line, whatever the
    #: system's font makes of each.
    CONTROL_H = 32
    #: Below this width the sort shows its mark alone (its order in the
    #: tooltip), so the header never holds its page wider than the pane.
    #: *fit_sort* False: its owner fits it (the TV place's header).
    SORT_TEXT_FROM = 470
    fit_sort = True
    #: The least of a long name shown before the count is cut too.
    TITLE_MIN = 56
    #: Between the name, the dot and the count.
    GAP = 6

    def __init__(self, title: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("MediaPageHeader")
        self._title_full = title
        self._count_full = ""
        self._compact = False
        self._filter_max: dict[int, int] = {}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self._lead = QHBoxLayout()
        self._lead.setSpacing(8)
        lay.addLayout(self._lead)
        # The line: its labels as wide as `_fit_line` makes them, the rest of
        # its room left empty - it never holds the header wider than the pane.
        self._line = QWidget()
        self._line.setObjectName("MediaPageLine")
        self._line.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._line.setMinimumWidth(1)
        line = QHBoxLayout(self._line)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(self.GAP)
        line.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.title = QLabel(title)
        self.title.setObjectName("MediaPageTitle")
        self.title.setAccessibleName(title)
        self._dot = QLabel("·")
        self._dot.setObjectName("MediaPageDot")
        self._dot.setAccessibleName("")
        self.count = QLabel("")
        self.count.setObjectName("MediaPageCount")
        self.count.hide()
        for lab in (self.title, self._dot, self.count):
            lab.setTextFormat(Qt.TextFormat.PlainText)
            lab.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
            line.addWidget(lab, 0, Qt.AlignmentFlag.AlignVCenter)
        line.addStretch(1)
        self._line.installEventFilter(self)
        lay.addWidget(self._line, 1)
        self._extra = QHBoxLayout()
        self._extra.setSpacing(8)
        lay.addLayout(self._extra)
        self.sort_btn = QToolButton()
        self.sort_btn.setObjectName("MediaSort")
        self.sort_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.sort_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.sort_btn.setIconSize(QSize(14, 14))
        self.sort_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sort_menu = QMenu(self.sort_btn)
        self.sort_btn.setMenu(self._sort_menu)
        self._sort_group = QActionGroup(self)
        self._sort_group.setExclusive(True)
        self._sort_opts: list[tuple[str, str]] = []
        self._sort_key = ""
        self._toggle: tuple[str, bool] = ("", False)
        self._toggle_acts: list[QAction] = []
        self.sort_btn.hide()
        self.sort_btn.setFixedHeight(self.CONTROL_H)
        lay.addWidget(self.sort_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.switch = Segmented(name=tr("card_view_switch_a11y"))
        self.switch.setFixedHeight(self.CONTROL_H)
        self.switch.changed.connect(self.view_changed)
        lay.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.CONTROL_H)
        self.restyle()
        self._fit_line()

    def restyle(self) -> None:
        px = self.TITLE_PX
        self.title.setStyleSheet(f"color:{P['text2']}; font-size:{px}px; font-weight:600;"
                                 " background:transparent;")
        for lab in (self._dot, self.count):
            lab.setStyleSheet(f"color:{P['muted']}; font-size:{px}px; background:transparent;")
        r = round(corner_radius(10))
        self.sort_btn.setStyleSheet(
            f"QToolButton#MediaSort {{ background:{P['input']}; color:{P['text2']};"
            f" border:none; border-radius:{r}px; padding:6px 12px; font-size:12px;"
            " font-weight:600; }"
            f"QToolButton#MediaSort:hover {{ color:{P['text']}; background:{P['btn']}; }}"
            "QToolButton#MediaSort::menu-indicator { image:none; width:0; }")
        self.sort_btn.setIcon(sort_icon(P["text2"], 14, self.devicePixelRatioF() or 1.0))
        self.switch.restyle()
        self._fit_line()

    def add_lead(self, w: QWidget) -> None:
        """Something before the line (the way back), as tall as the line."""
        self._lead.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_title(self, text: str) -> None:
        """What the place shows: the category, the folder, the list."""
        self._title_full = text
        self.title.setAccessibleName(text)
        self._fit_line()

    def title_text(self) -> str:
        """The name in full (the label may hold it cut)."""
        return self._title_full

    def set_count(self, text: str) -> None:
        self._count_full = text
        self._fit_line()

    def line_text(self) -> str:
        """The whole line as it would read with room: "Drama · 30 series"."""
        return " · ".join(t for t in (self._title_full, self._count_full) if t)

    # -- compact ----------------------------------------------------------------

    def set_compact(self, on: bool) -> None:
        """Just the list: kept for the page round the header (its room);
        the line is the same."""
        self._compact = bool(on)

    def is_compact(self) -> bool:
        return self._compact

    def is_inline(self) -> bool:
        """The count is always on the name's line."""
        return True

    def eventFilter(self, obj, e) -> bool:  # noqa: N802 - Qt's name
        if obj is self._line and e.type() == QEvent.Type.Resize:
            self._fit_line()
        return super().eventFilter(obj, e)

    def _fit_line(self) -> None:
        """The name and the count in the line's room: the name cut with "…"
        first, the count whole while the name keeps `TITLE_MIN`, then the
        count cut too - never cut off mid-letter where the controls begin."""
        title, count = self._title_full, self._count_full
        both = bool(title and count)
        fm_t, fm_c = self.title.fontMetrics(), self.count.fontMetrics()
        tw = fm_t.horizontalAdvance(title) + 2 if title else 0
        cw = fm_c.horizontalAdvance(count) + 2 if count else 0
        dot = fm_c.horizontalAdvance("·") + 2
        dw = dot + 2 * self.GAP if both else 0
        self._give_room(tw + dw + cw)
        room = self._line.width() if self._line.isVisible() else 0
        full_t, full_c = tw, cw
        if room > 0 and tw + dw + cw > room:
            if not count:
                tw = room
            elif not title:
                cw = room
            elif room - dw - cw >= min(tw, self.TITLE_MIN):
                tw = room - dw - cw
            else:
                tw = min(tw, self.TITLE_MIN)
                cw = max(1, room - dw - tw)
        shown_t = (fm_t.elidedText(title, Qt.TextElideMode.ElideRight, max(1, tw - 2))
                   if tw < full_t else title)
        shown_c = (fm_c.elidedText(count, Qt.TextElideMode.ElideRight, max(1, cw - 2))
                   if cw < full_c else count)
        for lab, text, width in ((self.title, shown_t, tw), (self.count, shown_c, cw)):
            if lab.text() != text:
                lab.setText(text)
            lab.setFixedWidth(max(1, width))
            lab.setVisible(bool(text))
        self._dot.setFixedWidth(dot)
        self._dot.setVisible(both)
        cut = (shown_t != title) or (shown_c != count)
        tip = self.line_text() if cut else ""
        for lab in (self.title, self.count):
            if lab.toolTip() != tip:
                lab.setToolTip(tip)

    def _give_room(self, need: int) -> None:
        """A filter beside the line gives up its room first - down to its
        least - so the whole line stays where it can; it never asks the
        header for more than the pane gives it."""
        if not self._line.isVisible():
            return
        filters = [w for w in (self._extra.itemAt(i).widget() for i in range(self._extra.count()))
                   if isinstance(w, QLineEdit) and not w.isHidden()]
        if not filters:
            return
        avail = self._line.width() + sum(f.width() for f in filters)
        share = (avail - need) // len(filters)
        for f in filters:
            base = self._filter_max.setdefault(id(f), f.maximumWidth())
            width = max(f.minimumWidth(), min(base, share))
            if f.maximumWidth() != width:
                f.setMaximumWidth(width)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self.fit_sort:
            style = (Qt.ToolButtonStyle.ToolButtonTextBesideIcon
                     if self.width() >= self.SORT_TEXT_FROM
                     else Qt.ToolButtonStyle.ToolButtonIconOnly)
            if self.sort_btn.toolButtonStyle() != style:
                self.sort_btn.setToolButtonStyle(style)
        self._fit_line()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        QTimer.singleShot(0, lambda: None if sip.isdeleted(self) else self._fit_line())

    def set_sort(self, options: Sequence[tuple[str, str]], current: str = "",
                 shown: str = "") -> None:
        """The orders as (key, words); [] hides the control. *shown* is what
        the button says when it is not the picked entry's words (the order
        "As in all categories" stands for)."""
        self._sort_menu.clear()
        for a in self._sort_group.actions():
            self._sort_group.removeAction(a)
        self._sort_opts = list(options)
        for key, label in self._sort_opts:
            act = QAction(label, self._sort_menu)
            act.setCheckable(True)
            act.setData(key)
            act.triggered.connect(lambda _c=False, k=key: self._sort_picked(k))
            self._sort_group.addAction(act)
            self._sort_menu.addAction(act)
        self._toggle_acts = []              # cleared with the menu
        self._put_toggle()
        self.sort_btn.setVisible(bool(self._sort_opts))
        self._set_sort_key(current or (self._sort_opts[0][0] if self._sort_opts else ""), shown)

    def set_sort_toggle(self, text: str, on: bool = False) -> None:
        """A switch at the foot of the sort's menu, under a line ("Unwatched
        only"); "" takes it away. Said as ``sort_toggled``."""
        if (text, bool(on)) == self._toggle and (bool(self._toggle_acts) == bool(text)):
            return
        self._toggle = (text, bool(on))
        self._put_toggle()

    def _put_toggle(self) -> None:
        for act in self._toggle_acts:
            self._sort_menu.removeAction(act)
        self._toggle_acts = []
        text, on = self._toggle
        if not text or not self._sort_opts:
            return
        sep = self._sort_menu.addSeparator()
        act = QAction(text, self._sort_menu)
        act.setCheckable(True)
        act.setChecked(on)
        act.toggled.connect(self.sort_toggled)
        self._sort_menu.addAction(act)
        self._toggle_acts = [sep, act]

    def sort_key(self) -> str:
        return self._sort_key

    def set_sort_key(self, key: str, shown: str = "") -> None:
        self._set_sort_key(key, shown)

    def _set_sort_key(self, key: str, shown: str = "") -> None:
        labels = dict(self._sort_opts)
        if key not in labels:
            return
        self._sort_key = key
        for a in self._sort_group.actions():
            a.setChecked(a.data() == key)
        said = shown or labels[key]
        self.sort_btn.setText(said)
        words = tr("card_sort_a11y", order=said)
        self.sort_btn.setToolTip(words)
        self.sort_btn.setAccessibleName(words)

    def _sort_picked(self, key: str) -> None:
        if key != self._sort_key:
            self._set_sort_key(key)
            self.sort_changed.emit(key)

    def set_views(self, options: Sequence[tuple[str, str]], current: str = "") -> None:
        """The switch's choices as (key, words) - grid / list, compact /
        airy; [] hides it."""
        self.switch.set_options(options, current)

    def view_key(self) -> str:
        return self.switch.current()

    def set_view_key(self, key: str) -> None:
        self.switch.set_current(key)

    def add_widget(self, w: QWidget) -> None:
        """Something of the page's own beside the controls (a filter, a
        button): as tall as they are, on their line."""
        if isinstance(w, QLineEdit):
            w.setFixedHeight(self.CONTROL_H)
            # Its widest; `_give_room` narrows it for the line and back.
            self._filter_max[id(w)] = w.maximumWidth()
            # The app's filter field pads for a taller box; this one's room.
            name = w.objectName()
            if name:
                w.setStyleSheet(f"QLineEdit#{name} {{ padding: 4px 10px; }}")
        self._extra.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)


# -- the shared form ---------------------------------------------------------------

class _Body(QWidget):
    """What the page's scroll area moves: opaque in the page's own colour,
    so a scroll step shifts what is drawn and paints only the strip that
    comes into view (the app's style sheet takes the opaque mark off a
    scroll area's content on every polish: it is put back)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def event(self, e) -> bool:
        done = super().event(e)
        if e.type() in (QEvent.Type.Polish, QEvent.Type.StyleChange):
            self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        return done

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.fillRect(e.rect(), QColor(P["bg"]))
        p.end()


class PlacePage(QWidget):
    """A place's one form: the header, its rows (Continue, Popular right
    now, ...), then the content - a ``CardView``, a ``ChannelView`` or any
    widget. *scroll_whole*: all of it in one scroll area, the content as
    tall as it is (the page scrolls and the header goes with it); else the
    header and rows stay and the content scrolls itself."""

    MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM, SPACING = 24, 20, 24, 20

    def __init__(self, title: str = "", scroll_whole: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("MediaPlacePage")
        self.scroll_whole = scroll_whole
        self.header = PageHeader(title)
        self.content: QWidget | None = None
        self.rows: list[QWidget] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        if scroll_whole:
            self.area: QScrollArea | None = QScrollArea()
            self.area.setObjectName("MediaPlaceScroll")
            self.area.setWidgetResizable(True)
            self.area.setFrameShape(QFrame.Shape.NoFrame)
            self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.body: QWidget = _Body()
            self.area.setWidget(self.body)
            outer.addWidget(self.area)
        else:
            self.area = None
            self.body = _Body()
            outer.addWidget(self.body)
        self.body.setObjectName("MediaPlaceBody")
        self._lay = QVBoxLayout(self.body)
        self._lay.setContentsMargins(self.MARGIN_X, self.MARGIN_TOP, self.MARGIN_X,
                                     self.MARGIN_BOTTOM)
        self._lay.setSpacing(self.SPACING)
        self._lay.addWidget(self.header)
        self._rows_at = 1
        self._tail = None
        if scroll_whole:
            self._tail = self._lay.addStretch(1)
        self.restyle()

    def restyle(self) -> None:
        self.setStyleSheet(f"QWidget#MediaPlacePage {{ background:{P['bg']}; }}"
                           "QScrollArea#MediaPlaceScroll { background:transparent; border:none; }")
        self.header.restyle()
        for r in self.rows:
            fn = getattr(r, "restyle", None)
            if callable(fn):
                fn()
        fn = getattr(self.content, "restyle", None)
        if callable(fn):
            fn()
        self.body.update()

    #: Compact (Just the list): less room over the header and between the
    #: parts, so the content starts higher.
    COMPACT_TOP, COMPACT_SPACING = 6, 12

    def set_compact(self, on: bool) -> None:
        """The page compact (Just the list) or as ever: its header, and the
        room over it and between its parts."""
        top = min(self.MARGIN_TOP, self.COMPACT_TOP) if on else self.MARGIN_TOP
        self._lay.setContentsMargins(self.MARGIN_X, top, self.MARGIN_X, self.MARGIN_BOTTOM)
        self._lay.setSpacing(self.COMPACT_SPACING if on else self.SPACING)
        self.header.set_compact(on)

    def is_compact(self) -> bool:
        return self.header.is_compact()

    def add_row(self, row: QWidget) -> None:
        """A row between the header and the content, after those added
        before it."""
        self._lay.insertWidget(self._rows_at, row)
        self._rows_at += 1
        self.rows.append(row)

    def set_content(self, w: QWidget) -> None:
        """The place's content under its rows, in place of what was there."""
        if self.content is not None and self.content is not w:
            self._lay.removeWidget(self.content)
            self.content.setParent(None)
        self.content = w
        at = self._rows_at
        self._lay.insertWidget(at, w, 0 if self.scroll_whole else 1)
        if isinstance(w, _PaintedView):
            if self.scroll_whole:
                w.set_host(self.area)
                w.moved.connect(self._reveal_from)
            else:
                w.set_host(None)

    def _reveal_from(self, rect: QRect) -> None:
        w = self.sender()
        if isinstance(w, QWidget):
            self.reveal(w, rect)

    def reveal(self, w: QWidget, rect: QRect) -> None:
        """*rect* of *w* (its viewport's, for a view) on screen."""
        if self.area is None or sip.isdeleted(w):
            return
        src = w.viewport() if isinstance(w, QAbstractItemView) else w
        top = src.mapTo(self.body, rect.topLeft())
        self.area.ensureVisible(top.x() + rect.width() // 2, top.y() + rect.height() // 2,
                                0, rect.height() // 2 + 12)

    def scroll_to_top(self) -> None:
        if self.area is not None:
            self.area.verticalScrollBar().setValue(0)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.BackButton and isinstance(self.content, _PaintedView):
            self.content.back.emit()
            e.accept()
            return
        super().mousePressEvent(e)

