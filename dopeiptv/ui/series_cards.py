"""A series' episodes as cards (ui/series_page.py lays the page out
around them): the season's list of uniform rows, its model, the painted
card - its still or the series' picture, "E5 · Title", length, two lines
of plot, the watched check, how far it got, the next one and the one
playing marked - and its keys. Mirrored in a right-to-left language
(``card_layout``).

Pictures through the window's image loaders (``ImageSource``): asked for
only for the cards painted, i.e. on screen, fetched on the loaders' own
threads and painted when they come.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from PyQt6.QtCore import (
    QAbstractListModel, QModelIndex, QPoint, QRect, QRectF, QSize, Qt, pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QFrame, QListView, QSizePolicy, QStyle, QStyledItemDelegate,
)

from ..core import series_view as sv
from ..i18n import tr
from .theme import P, corner_radius, tinted

if TYPE_CHECKING:
    from .series_page import SeriesPage

def _c(key: str, alpha: float = 1.0) -> QColor:
    c = QColor(P.get(key) or P.get("text") or "#808080")
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def _px_font(px: int, bold: bool = False) -> QFont:
    f = QFont()
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def _cover(pm: QPixmap, w: int, h: int) -> QPixmap:
    return pm.scaled(max(1, w), max(1, h), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                     Qt.TransformationMode.SmoothTransformation)


def wrap_lines(fm: QFontMetrics, text: str, width: int, lines: int) -> list[str]:
    """*text* in at most *lines* lines of *width* pixels, the last one cut
    with an ellipsis when there is more."""
    words = text.split()
    out: list[str] = []
    cur = ""
    i = 0
    while i < len(words) and len(out) < lines:
        trial = f"{cur} {words[i]}" if cur else words[i]
        if fm.horizontalAdvance(trial) <= width or not cur:
            cur = trial
            i += 1
            continue
        out.append(cur)
        cur = ""
    if cur and len(out) < lines:
        out.append(cur)
    if out and (i < len(words) or fm.horizontalAdvance(out[-1]) > width):
        rest = " ".join([out[-1]] + words[i:])
        out[-1] = fm.elidedText(rest, Qt.TextElideMode.ElideRight, max(1, width))
    return out


class ImageSource:
    """Pictures for the page: from a loader's memory when there, else
    asked for (on the loader's own threads) and the page repainted when
    they come. A picture is asked for once per page."""

    def __init__(self, loader, repaint) -> None:
        self.loader = loader
        self._repaint = repaint
        self._asked: set[str] = set()

    def get(self, url: str | None) -> QPixmap | None:
        if not url or self.loader is None:
            return None
        pm = self.loader.cache.get(url)
        if pm is not None:
            return pm if not pm.isNull() else None
        if url not in self._asked and not self.loader.is_dead(url):
            self._asked.add(url)
            self.loader.get(url, self._arrived)
        return None

    def forget(self) -> None:
        self._asked.clear()

    def _arrived(self, _pm) -> None:
        self._repaint()


@dataclass
class EpisodeState:
    """What a card says beside the episode itself: watched ("local",
    "trakt" or None), how far it got, a new one of a followed series."""
    watched: str | None = None
    progress: sv.Progress = field(default_factory=sv.Progress)
    new: bool = False


# -- the episodes --------------------------------------------------------------------

class EpisodeModel(QAbstractListModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: list[dict] = []

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._items)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        it = self._items[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return it
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            return str(it.get("_sp_label") or it.get("name") or "")
        return None

    def set_items(self, items: list[dict]) -> None:
        self.beginResetModel()
        self._items = list(items)
        self.endResetModel()

    def items(self) -> list[dict]:
        return list(self._items)

    def item_at(self, row: int) -> dict | None:
        return self._items[row] if 0 <= row < len(self._items) else None

    def row_of(self, key: str) -> int:
        return next((i for i, e in enumerate(self._items)
                     if sv.episode_key(e) == key), -1)


#: (pane width from which it applies, still width) - a card's picture is
#: 16:9; the row is as tall as the picture and its margins.
STILL_SIZES = ((600, 176), (440, 144), (0, 120))
CARD_PAD = 10
CARD_GAP = 4


def still_width(width: int) -> int:
    return next(w for edge, w in STILL_SIZES if width >= edge)


def row_height(width: int) -> int:
    return round(still_width(width) * 9 / 16) + 2 * CARD_PAD + CARD_GAP


def card_layout(rect: QRect, rtl: bool) -> dict[str, QRect]:
    """Where a card's parts go in *rect* (the row): the card, its picture
    and the text beside it - at the start of the line, mirrored in a
    right-to-left language."""
    card = rect.adjusted(0, CARD_GAP // 2, 0, -(CARD_GAP - CARD_GAP // 2))
    sw = still_width(rect.width())
    sh = round(sw * 9 / 16)
    still = QRect(card.left() + CARD_PAD, card.top() + CARD_PAD, sw, sh)
    text = QRect(still.right() + 1 + 14, card.top() + CARD_PAD - 1,
                 max(10, card.right() - still.right() - 14 - CARD_PAD), sh + 2)
    direction = Qt.LayoutDirection.RightToLeft if rtl else Qt.LayoutDirection.LeftToRight
    return {"card": card,
            "still": QStyle.visualRect(direction, card, still),
            "text": QStyle.visualRect(direction, card, text)}


class EpisodeDelegate(QStyledItemDelegate):
    """An episode's card: its still (else the series' own picture), "E5 ·
    Title", its length, two lines of its plot, the watched check, how far
    it got, the next one to watch and the one playing marked."""

    def __init__(self, page: "SeriesPage") -> None:
        super().__init__(page)
        self._page = page
        self._wrapped: dict[tuple, list[str]] = {}

    def forget_text(self) -> None:
        self._wrapped.clear()

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), row_height(self._page.view.viewport().width()))

    def paint(self, p: QPainter, option, index) -> None:
        page = self._page
        # The model's own row: through data() PyQt would copy the dict on
        # every paint.
        ep = page.model.item_at(index.row()) or {}
        rtl = option.direction == Qt.LayoutDirection.RightToLeft
        parts = card_layout(option.rect, rtl)
        card, still, text = parts["card"], parts["still"], parts["text"]
        key = sv.episode_key(ep)
        state = page.states.get(key) or EpisodeState()
        playing = bool(key) and key == page.playing_key
        nxt = bool(key) and key == page.next_key and not playing
        current = index == page.view.currentIndex() and page.view.hasFocus()
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        radius = corner_radius(10)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        fill = None
        if playing:
            fill = QColor(tinted(P["accent"], P["bg"], 0.16, P["text"])[0])
        elif current:
            fill = _c("sel")
        elif hover:
            fill = _c("hover")
        elif nxt:
            fill = _c("pane")
        if fill is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(QRectF(card), radius, radius)
        if nxt or playing:
            # The leading edge's rail, as the nav's selected entry has.
            rail = QRectF(card.right() - 3 if rtl else card.left(), card.top() + 10,
                          3, card.height() - 20)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_c("accent"))
            p.drawRoundedRect(rail, 1.5, 1.5)
        self._paint_still(p, ep, still, state, playing, rtl)
        self._paint_text(p, ep, text, state, playing, nxt, rtl)
        p.restore()

    def _paint_still(self, p: QPainter, ep: dict, r: QRect, state: EpisodeState,
                     playing: bool, rtl: bool) -> None:
        page = self._page
        radius = corner_radius(8)
        path = QPainterPath()
        path.addRoundedRect(QRectF(r), radius, radius)
        p.save()
        p.setClipPath(path)
        p.fillPath(path, _c("btn"))
        pm = page.stills.get(sv.still_of(ep))
        own = pm is not None
        if pm is None:
            pm = page.fallback_art()
        if pm is not None and not pm.isNull():
            img = _cover(pm, r.width(), r.height())
            p.drawPixmap(r.x() - (img.width() - r.width()) // 2,
                         r.y() - (img.height() - r.height()) // 2, img)
            if not own:
                # The series' own picture stands in: dimmed, so the cards
                # still read as one row each and not as one big poster.
                p.fillPath(path, _c("bg", 0.45))
        if not own:
            label = str(ep.get("_sp_short") or "")
            if label:
                p.setPen(_c("text", 0.9))
                p.setFont(_px_font(max(12, r.height() // 4), bold=True))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, label)
        frac = state.progress.fraction if state.progress.started else 0.0
        if playing:
            frac = max(frac, page.playing_fraction)
        if frac > 0 and not state.watched:
            bar = QRectF(r.x() + 6, r.bottom() - 8, r.width() - 12, 4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_c("bg", 0.7))
            p.drawRoundedRect(bar, 2, 2)
            done = QRectF(bar.x(), bar.y(), max(4.0, bar.width() * frac), bar.height())
            if rtl:
                done.moveRight(bar.right())
            p.setBrush(_c("accent"))
            p.drawRoundedRect(done, 2, 2)
        p.restore()
        if playing:
            d = min(34, r.height() // 2)
            disc = QRectF(r.center().x() - d / 2, r.center().y() - d / 2, d, d)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_c("accent"))
            p.drawEllipse(disc)
            tri = QPainterPath()
            tri.moveTo(disc.x() + d * 0.38, disc.y() + d * 0.28)
            tri.lineTo(disc.x() + d * 0.74, disc.y() + d * 0.5)
            tri.lineTo(disc.x() + d * 0.38, disc.y() + d * 0.72)
            tri.closeSubpath()
            p.setBrush(_c("on_accent"))
            p.drawPath(tri)
        if state.watched:
            size = max(16, min(22, r.height() // 4))
            x = r.left() + 5 if rtl else r.right() - size - 5
            y = r.top() + 5
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_c("accent"))
            p.drawEllipse(QRectF(x, y, size, size))
            pen = QPen(_c("on_accent"), max(2.0, size / 7))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            cx, cy = x + size / 2, y + size / 2
            check = QPainterPath()
            check.moveTo(cx - size * 0.22, cy + size * 0.02)
            check.lineTo(cx - size * 0.05, cy + size * 0.2)
            check.lineTo(cx + size * 0.24, cy - size * 0.18)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(check)

    def _pill(self, p: QPainter, text: str, r: QRect, rtl: bool, strong: bool) -> int:
        """A small pill at the end of the title line; its width."""
        f = _px_font(10, bold=True)
        fm = QFontMetrics(f)
        w, h = fm.horizontalAdvance(text) + 14, fm.height() + 2
        x = r.left() if rtl else r.right() - w
        y = r.top() + max(0, (r.height() - h) // 2)
        wash, ink = tinted(P["accent"], P["bg"], 0.22, P["text"])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c("accent") if strong else QColor(wash))
        p.drawRoundedRect(QRectF(x, y, w, h), h / 2, h / 2)
        p.setFont(f)
        p.setPen(_c("on_accent") if strong else QColor(ink))
        p.drawText(QRect(x, y, w, h), Qt.AlignmentFlag.AlignCenter, text)
        return w + 8

    def _paint_text(self, p: QPainter, ep: dict, r: QRect, state: EpisodeState,
                    playing: bool, nxt: bool, rtl: bool) -> None:
        title_f = _px_font(13, bold=True)
        meta_f = _px_font(11)
        plot_f = _px_font(12)
        tfm, mfm, pfm = QFontMetrics(title_f), QFontMetrics(meta_f), QFontMetrics(plot_f)
        y = r.top()
        title_r = QRect(r.left(), y, r.width(), tfm.height() + 2)
        used = 0
        pill_r = QRect(title_r)
        if playing:
            used = self._pill(p, tr("sp_playing"), pill_r, rtl, True)
        elif nxt:
            used = self._pill(p, tr("sp_up_next"), pill_r, rtl, False)
        elif state.new:
            used = self._pill(p, tr("sp_new"), pill_r, rtl, True)
        room = max(10, title_r.width() - used)
        title_text = str(ep.get("_sp_label") or ep.get("name") or "")
        p.setFont(title_f)
        p.setPen(QColor(tinted(P["accent"], P["bg"], 0.16, P["text"])[1]) if playing
                 else _c("text"))
        text_r = QRect(title_r.left() + (used if rtl else 0), title_r.top(), room,
                       title_r.height())
        p.drawText(text_r, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   tfm.elidedText(title_text, Qt.TextElideMode.ElideRight, room))
        y = title_r.bottom() + 2
        meta = self._meta(ep, state)
        if meta:
            meta_r = QRect(r.left(), y, r.width(), mfm.height())
            p.setFont(meta_f)
            p.setPen(_c("muted"))
            p.drawText(meta_r, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                       mfm.elidedText(meta, Qt.TextElideMode.ElideRight, r.width()))
            y = meta_r.bottom() + 3
        plot = sv.plot_of(ep)
        lines = min(2, max(0, (r.bottom() - y) // max(1, pfm.lineSpacing())))
        if plot and lines:
            key = (sv.episode_key(ep), r.width(), lines, len(plot))
            got = self._wrapped.get(key)
            if got is None:
                if len(self._wrapped) > 600:
                    self._wrapped.clear()
                got = self._wrapped[key] = wrap_lines(pfm, plot, r.width(), lines)
            p.setFont(plot_f)
            p.setPen(_c("text3"))
            for line in got:
                p.drawText(QRect(r.left(), y, r.width(), pfm.lineSpacing()),
                           int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                           line)
                y += pfm.lineSpacing()

    @staticmethod
    def _meta(ep: dict, state: EpisodeState) -> str:
        parts = []
        mins = sv.minutes_of(ep)
        if mins:
            parts.append(minutes_text(mins))
        if state.progress.started and not state.watched and state.progress.minutes_left:
            parts.append(tr("home_min_left", n=state.progress.minutes_left))
        aired = sv.year_of((ep.get("info") or {}).get("releasedate")
                           or (ep.get("info") or {}).get("air_date") or "")
        if aired and not parts:
            parts.append(aired)
        return " · ".join(parts)


def minutes_text(mins: int) -> str:
    if mins >= 60:
        h, m = divmod(mins, 60)
        return tr("sp_hours_minutes", h=h, m=m)
    return tr("opt_minutes", n=mins)


class EpisodeView(QListView):
    """The season's cards, one per row, as tall as they are: the page
    scrolls, not the list. Up/Down move, Enter (or a click) plays,
    Left/Right go to the season before or after, the mouse's back button
    goes back."""

    play = pyqtSignal(object)
    menu = pyqtSignal(object, QPoint)
    season_step = pyqtSignal(int)
    back = pyqtSignal()
    moved = pyqtSignal(QRect)
    width_changed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SeriesEpisodes")
        self.setUniformItemSizes(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._width_key = -1
        #: Whether one click plays an episode - the window's "Auto-play
        #: preview on selection", as in its lists. Off (the default), one
        #: click picks the card and a double click or Enter plays it.
        self.click_plays: Callable[[], bool] | None = None
        self.clicked.connect(self._clicked)
        self.restyle()

    def _one_click(self) -> bool:
        try:
            return bool(self.click_plays()) if self.click_plays else False
        except Exception:
            return False

    def _clicked(self, idx) -> None:
        if not self._one_click():
            return
        it = self._row(idx)
        if it is not None:
            self.play.emit(it)

    def restyle(self) -> None:
        self.setStyleSheet("QListView#SeriesEpisodes { background: transparent;"
                           " border: none; outline: none; }")

    def _row(self, idx) -> dict | None:
        """The model's own episode (not a copy) at *idx*."""
        model = self.model()
        if not idx.isValid() or not isinstance(model, EpisodeModel):
            return None
        return model.item_at(idx.row())

    def current_item(self) -> dict | None:
        return self._row(self.currentIndex())

    def keyPressEvent(self, e) -> None:
        k = e.key()
        plain = not (e.modifiers() & (Qt.KeyboardModifier.ControlModifier
                                      | Qt.KeyboardModifier.AltModifier
                                      | Qt.KeyboardModifier.MetaModifier))
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and plain:
            it = self.current_item()
            if it is not None:
                self.play.emit(it)
            return
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Right) and plain:
            step = -1 if k == Qt.Key.Key_Left else 1
            if self.isRightToLeft():
                step = -step
            self.season_step.emit(step)
            return
        if k == Qt.Key.Key_Menu:
            idx = self.currentIndex()
            it = self._row(idx)
            if it is not None:
                r = self.visualRect(idx)
                self.menu.emit(it, self.viewport().mapToGlobal(r.center()))
            return
        super().keyPressEvent(e)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.BackButton:
            self.back.emit()
            return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:
        e.accept()
        if self._one_click() or e.button() != Qt.MouseButton.LeftButton:
            return                      # the first click of the two played it
        it = self._row(self.indexAt(e.position().toPoint()))
        if it is not None:
            self.play.emit(it)

    def contextMenuEvent(self, e) -> None:
        idx = self.indexAt(e.pos())
        it = self._row(idx)
        if it is None:
            return
        self.setCurrentIndex(idx)
        self.menu.emit(it, e.globalPos())

    def wheelEvent(self, e) -> None:
        # The page scrolls (this list is as tall as its cards).
        e.ignore()

    def currentChanged(self, current, previous) -> None:
        super().currentChanged(current, previous)
        if current.isValid():
            self.moved.emit(self.visualRect(current))

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        key = still_width(self.viewport().width())
        if key != self._width_key:
            self._width_key = key
            self.scheduleDelayedItemsLayout()
            self.width_changed.emit()

    def focusInEvent(self, e) -> None:
        super().focusInEvent(e)
        if not self.currentIndex().isValid() and self.model() is not None \
                and self.model().rowCount():
            self.setCurrentIndex(self.model().index(0, 0))
        self.viewport().update()

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        self.viewport().update()
