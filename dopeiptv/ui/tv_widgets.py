"""TV mode's building blocks, drawn to be read from the sofa: tiles,
shelves, big lists and a poster grid, the on-screen keyboard, the colour
keys and the info bar over the picture.

Sizes are for a 1080-line screen (``px``: scaled to the one TV mode opens
on); colours are the theme's (``P``), the accent being the focus ring.
Pure presentation - what a tile does is the screen's (ui/tv_mode.py).
"""

from __future__ import annotations

import time

from PyQt6 import sip
from PyQt6.QtCore import QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QListView, QScrollArea, QStyledItemDelegate, QVBoxLayout,
    QWidget,
)

from ..core import tv_nav
from ..i18n import tr
from .channel_list import ChannelListModel
from .theme import ACCENTS, P

_unit = [1.0]


def set_unit(screen_height: int) -> float:
    """Scale TV mode's sizes to *screen_height* (1.0 on a 1080-line one)."""
    _unit[0] = max(0.6, min(1.6, (screen_height or 1080) / 1080.0))
    return _unit[0]


def px(n: float) -> int:
    return max(1, int(round(n * _unit[0])))


def font(size: float, bold: bool = False) -> QFont:
    f = QFont()
    f.setPixelSize(px(size))
    f.setBold(bold)
    return f


def colour(key: str, alpha: float = 1.0) -> QColor:
    c = QColor(P.get(key, P.get("text", "")))
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


#: The remote's colour keys, in their order, and the colour each is drawn in.
COLOUR_KEYS: tuple[tuple[str, str], ...] = (
    ("red", ACCENTS["red"][1]), ("green", ACCENTS["green"][1]),
    ("yellow", ACCENTS["gold"][1]), ("blue", ACCENTS["blue"][1]))


def reveal(w: QWidget) -> None:
    """Scroll every scroll area *w* is in so it is on screen."""
    parent = w.parentWidget()
    while parent is not None:
        if isinstance(parent, QScrollArea) and parent.widget() is not None:
            parent.ensureWidgetVisible(w, px(48), px(36))
        parent = parent.parentWidget()


def _rounded(r: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(r, radius, radius)
    return path


def _cover(pm: QPixmap, w: int, h: int) -> QPixmap:
    return pm.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                     Qt.TransformationMode.SmoothTransformation)


def _contain(pm: QPixmap, w: int, h: int) -> QPixmap:
    return pm.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)


def draw_image(p: QPainter, r: QRectF, pm: QPixmap | None, contain: bool,
               placeholder: str = "") -> None:
    """A picture in *r*: a logo whole on a plate (*contain*), a poster
    filling it, or the first letter of *placeholder* when there is none."""
    path = _rounded(r, px(12))
    p.save()
    p.setClipPath(path)
    p.fillPath(path, colour("pane" if contain else "btn"))
    if pm is not None and not pm.isNull():
        if contain:
            pad = px(18)
            img = _contain(pm, int(r.width()) - 2 * pad, int(r.height()) - 2 * pad)
            p.drawPixmap(int(r.x() + (r.width() - img.width()) / 2),
                         int(r.y() + (r.height() - img.height()) / 2), img)
        else:
            img = _cover(pm, int(r.width()), int(r.height()))
            p.drawPixmap(int(r.x() - (img.width() - r.width()) / 2),
                         int(r.y() - (img.height() - r.height()) / 2), img)
    elif placeholder:
        p.setPen(colour("muted"))
        p.setFont(font(min(r.height() * 0.4, 64), bold=True))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, placeholder[:1].upper())
    p.restore()


def draw_bar(p: QPainter, r: QRectF, frac: float, on_accent: bool = False) -> None:
    """A progress bar along *r*: the part done in the accent (in the ink on
    the accent, *on_accent*, over a row filled with it)."""
    frac = max(0.0, min(1.0, frac))
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(colour("on_accent" if on_accent else "text", 0.22))
    p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
    if frac > 0:
        done = QRectF(r.x(), r.y(), max(r.height(), r.width() * frac), r.height())
        p.setBrush(colour("on_accent" if on_accent else "accent"))
        p.drawRoundedRect(done, r.height() / 2, r.height() / 2)
    p.restore()


def draw_ring(p: QPainter, r: QRectF, radius: float) -> None:
    """The focus ring: thick, in the accent, with a thin line of the text
    colour inside it so it reads on a poster of any colour."""
    p.save()
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(colour("accent"), px(5)))
    p.drawRoundedRect(r, radius, radius)
    inner = r.adjusted(px(4), px(4), -px(4), -px(4))
    p.setPen(QPen(colour("text", 0.85), max(1, px(1.5))))
    p.drawRoundedRect(inner, max(1.0, radius - px(4)), max(1.0, radius - px(4)))
    p.restore()


# -- tiles --------------------------------------------------------------------------

class TvTile(QAbstractButton):
    """One focusable tile: a picture with a title and a line under it
    (*kind* "poster", "wide" or "logo"), or a plain button of words ("key",
    "option", "tab"). OK clicks it."""

    RING = 8        # room kept around the tile for the focus ring

    def __init__(self, title: str, sub: str = "", w: int = 300, h: int = 170,
                 kind: str = "logo", progress: float | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.title, self.sub = title, sub
        self.progress = progress
        self.payload: object = None
        self.current = False        # a tab: the screen on show
        self._pm: QPixmap | None = None
        self._img_w, self._img_h = px(w), px(h)
        self._elastic = False       # narrower where the room is (set_width)
        if kind == "tab":
            # As wide as its word, in any language.
            text_w = QFontMetrics(font(28, bold=True)).horizontalAdvance(title)
            self._img_w = max(self._img_w, text_w + px(56))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(title)
        ring = px(self.RING)
        if kind in ("key", "option", "tab"):
            self.setFixedSize(self._img_w + 2 * ring, self._img_h + 2 * ring)
        else:
            self.setFixedSize(self._img_w + 2 * ring,
                              self._img_h + 2 * ring + px(78))

    def set_text(self, title: str, sub: str = "") -> None:
        self.title, self.sub = title, sub
        self.setAccessibleName(title)
        self.update()

    def set_pixmap(self, pm: QPixmap | None) -> None:
        self._pm = pm
        self.update()

    def set_width(self, width: int, shrink: bool = False) -> None:
        """*width* pixels across, the focus ring's room included: fixed, or
        with *shrink* at most that - narrower where its layout has less
        room, its words then elided at their own size (a word tile)."""
        h = self.height()
        self._elastic = shrink
        if shrink:
            self.setMinimumSize(min(width, px(160)), h)
            self.setMaximumSize(width, h)
        else:
            self.setFixedSize(width, h)
        self._img_w = max(1, width - 2 * px(self.RING))
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(self.maximumWidth(), self.maximumHeight())

    def focusInEvent(self, e) -> None:
        super().focusInEvent(e)
        reveal(self)
        self.update()

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        ring = px(self.RING)
        focus = self.hasFocus()
        enabled = self.isEnabled()
        if self.kind in ("key", "option", "tab"):
            r = QRectF(ring, ring, max(1, self.width() - 2 * ring), self._img_h)
            radius = r.height() / 2 if self.kind == "tab" else px(14)
            if focus:
                fill, ink = colour("accent"), colour("on_accent")
            elif self.current:
                fill, ink = colour("sel"), colour("text")
            elif self.kind == "tab":
                fill, ink = QColor(Qt.GlobalColor.transparent), colour("text2")
            else:
                fill, ink = colour("btn"), colour("text")
            if not enabled:
                ink = colour("muted2")
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(r, radius, radius)
            if focus:
                draw_ring(p, r.adjusted(-ring / 2, -ring / 2, ring / 2, ring / 2),
                          radius + ring / 2)
            p.setPen(ink)
            if self.kind == "option":
                inner = r.adjusted(px(28), 0, -px(28), 0)
                p.setFont(font(28, bold=True))
                p.drawText(inner, Qt.AlignmentFlag.AlignVCenter
                           | Qt.AlignmentFlag.AlignLeft, self.title)
                if self.sub:
                    p.setFont(font(26))
                    p.drawText(inner, Qt.AlignmentFlag.AlignVCenter
                               | Qt.AlignmentFlag.AlignRight, self.sub)
            else:
                # Smaller until it fits: a word of some languages is long.
                # Still too long (or a tile that takes the room it gets):
                # cut at the end.
                size = 30 if self.kind == "key" else 28
                room = int(r.width() - px(48 if self._elastic else 16))
                p.setFont(font(size, bold=True))
                while (not self._elastic and size > 16
                       and p.fontMetrics().horizontalAdvance(self.title) > room):
                    size -= 2
                    p.setFont(font(size, bold=True))
                text = self.title
                if p.fontMetrics().horizontalAdvance(text) > room:
                    text = p.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight,
                                                      max(1, room))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
            if self.kind == "tab" and self.current and not focus:
                bar = QRectF(r.center().x() - px(22), r.bottom() - px(6), px(44), px(4))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(colour("accent"))
                p.drawRoundedRect(bar, px(2), px(2))
            p.end()
            return
        r = QRectF(ring, ring, self._img_w, self._img_h)
        draw_image(p, r, self._pm, self.kind == "logo", self.title)
        if self.progress is not None:
            draw_bar(p, QRectF(r.x() + px(14), r.bottom() - px(18),
                               r.width() - px(28), px(8)), self.progress)
        if focus:
            draw_ring(p, r.adjusted(-ring / 2, -ring / 2, ring / 2, ring / 2),
                      px(12) + ring / 2)
        text = QRectF(ring, r.bottom() + px(10), self._img_w, px(36))
        p.setPen(colour("text") if focus else colour("text2"))
        p.setFont(font(26, bold=focus))
        fm = p.fontMetrics()
        p.drawText(text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.title, Qt.TextElideMode.ElideRight, int(text.width())))
        if self.sub:
            sub = QRectF(ring, text.bottom() + px(2), self._img_w, px(30))
            p.setPen(colour("muted"))
            p.setFont(font(22))
            fm = p.fontMetrics()
            p.drawText(sub, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(self.sub, Qt.TextElideMode.ElideRight, int(sub.width())))
        p.end()


class TvShelf(QWidget):
    """A titled row of tiles that scrolls sideways as the focus moves."""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(px(8))
        self.title = QLabel(title)
        self.title.setFont(font(32, bold=True))
        self.title.setStyleSheet(f"color:{P['text']};")
        lay.addWidget(self.title)
        self.area = QScrollArea()
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setWidgetResizable(True)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.area.setStyleSheet("background: transparent;")
        self.area.viewport().setAutoFillBackground(False)
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        self._row = QHBoxLayout(row)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(px(14))
        self._row.addStretch(1)
        self.area.setWidget(row)
        lay.addWidget(self.area)
        self._tiles: list[TvTile] = []

    def add(self, tile: TvTile) -> TvTile:
        self._row.insertWidget(self._row.count() - 1, tile)
        self._tiles.append(tile)
        self.area.setFixedHeight(tile.height())
        return tile

    def tiles(self) -> list[TvTile]:
        return list(self._tiles)


class TvKeyboard(QWidget):
    """The on-screen keyboard: letters, digits, space, delete and clear,
    each a tile the arrows reach. ``pressed`` says which: a character,
    "space", "del" or "clear"."""

    pressed = pyqtSignal(str)
    CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"
    COLUMNS = 6

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(px(2))
        self.keys: list[TvTile] = []
        n = 0
        for ch in self.CHARS:
            self._add(grid, n, ch.upper(), ch, 1)
            n += 1
        for label, value, span in ((tr("tv_key_space"), "space", 2),
                                   (tr("tv_key_delete"), "del", 2),
                                   (tr("tv_key_clear"), "clear", 2)):
            self._add(grid, n, label, value, span)
            n += span

    def _add(self, grid: QGridLayout, n: int, label: str, value: str,
             span: int) -> None:
        w = 72 * span + (span - 1) * 18
        key = TvTile(label, w=w, h=72, kind="key")
        key.payload = value
        key.clicked.connect(self._clicked)
        grid.addWidget(key, n // self.COLUMNS, n % self.COLUMNS, 1, span)
        self.keys.append(key)

    def _clicked(self) -> None:
        key = self.sender()
        if isinstance(key, TvTile) and isinstance(key.payload, str):
            self.pressed.emit(key.payload)


class ColourLegend(QWidget):
    """What the remote's four colour keys do here: a dot and a word each."""

    def __init__(self, labels: dict[str, str], parent=None) -> None:
        super().__init__(parent)
        self._labels = labels
        self.setFixedHeight(px(40))

    def set_labels(self, labels: dict[str, str]) -> None:
        self._labels = labels
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(font(24))
        fm = p.fontMetrics()
        x = 0.0
        d = px(20)
        y = (self.height() - d) / 2
        for key, col in COLOUR_KEYS:
            text = self._labels.get(key, "")
            if not text:
                continue
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(col))
            p.drawEllipse(QRectF(x, y, d, d))
            x += d + px(10)
            p.setPen(colour("text2"))
            p.drawText(QRectF(x, 0, fm.horizontalAdvance(text) + 2, self.height()),
                       Qt.AlignmentFlag.AlignVCenter, text)
            x += fm.horizontalAdvance(text) + px(36)
        p.end()


# -- lists ----------------------------------------------------------------------------

class _ImageSource:
    """Pictures for a delegate: from the loader's memory when there,
    else asked for, the view repainted when they come."""

    def __init__(self, view: QAbstractItemView, loader) -> None:
        self._view = view
        self._loader = loader
        self._asked: set[str] = set()

    def get(self, url) -> QPixmap | None:
        if not url or self._loader is None:
            return None
        pm = self._loader.cache.get(url)
        if pm is not None:
            return pm
        if url not in self._asked and not self._loader.is_dead(url):
            self._asked.add(url)
            self._loader.get(url, self._arrived)
        return None

    def _arrived(self, _pm) -> None:
        view = self._view
        if not sip.isdeleted(view):
            view.viewport().update()


class TvRowDelegate(QStyledItemDelegate):
    """A big list row: a channel's number and logo, its name, and a line
    under it (what is on, for a channel)."""

    def __init__(self, view: "TvList", loader, sub_fn=None, logos: bool = True) -> None:
        super().__init__(view)
        self._view = view
        self._images = _ImageSource(view, loader)
        self._sub_fn = sub_fn
        self._logos = logos

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), px(self._view.ROW_H))

    def paint(self, p: QPainter, option, index) -> None:
        it = index.data(Qt.ItemDataRole.UserRole) or {}
        r = QRectF(option.rect).adjusted(px(4), px(3), -px(4), -px(3))
        current = index == self._view.currentIndex()
        focus = current and self._view.hasFocus()
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if focus:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(colour("accent"))
            p.drawRoundedRect(r, px(12), px(12))
            ink, sub_ink = colour("on_accent"), colour("on_accent", 0.85)
        else:
            if current:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(colour("sel"))
                p.drawRoundedRect(r, px(12), px(12))
            ink, sub_ink = colour("text"), colour("muted")
        x = r.x() + px(18)
        num = it.get("num")
        if num not in (None, "") and self._logos:
            p.setPen(sub_ink)
            p.setFont(font(24, bold=True))
            p.drawText(QRectF(x, r.y(), px(64), r.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, str(num))
            x += px(80)
        if self._logos:
            box = QRectF(x, r.y() + px(8), px(96), r.height() - px(16))
            pm = self._images.get(it.get("stream_icon"))
            if pm is not None and not pm.isNull():
                img = _contain(pm, int(box.width()), int(box.height()))
                p.drawPixmap(int(box.x() + (box.width() - img.width()) / 2),
                             int(box.y() + (box.height() - img.height()) / 2), img)
            x += box.width() + px(18)
        sub, frac = self._sub_fn(it) if self._sub_fn else ("", None)
        right = r.right() - px(18)
        name_r = QRectF(x, r.y() + (px(10) if sub else 0), right - x,
                        r.height() / 2 if sub else r.height())
        p.setPen(ink)
        p.setFont(font(28, bold=True))
        fm = p.fontMetrics()
        name = str(it.get("name") or it.get("title") or "?")
        p.drawText(name_r, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(name, Qt.TextElideMode.ElideRight, int(name_r.width())))
        if sub:
            sub_r = QRectF(x, r.center().y(), right - x, r.height() / 2 - px(14))
            p.setPen(sub_ink)
            p.setFont(font(22))
            fm = p.fontMetrics()
            p.drawText(sub_r, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       fm.elidedText(sub, Qt.TextElideMode.ElideRight, int(sub_r.width())))
        if frac is not None:
            draw_bar(p, QRectF(x, r.bottom() - px(10), min(px(260), right - x), px(5)), frac, focus)
        p.restore()


class TvList(QListView):
    """A big list the arrows move through row by row; at its top or bottom
    the arrow leaves it (``step`` says False)."""

    ROW_H = 92

    def __init__(self, loader=None, sub_fn=None, logos: bool = True,
                 row_h: int | None = None, parent=None) -> None:
        super().__init__(parent)
        if row_h:
            self.ROW_H = row_h
        self.model_ = ChannelListModel(self)
        self.setModel(self.model_)
        self.setItemDelegate(TvRowDelegate(self, loader, sub_fn, logos))
        self.setUniformItemSizes(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setStyleSheet("QListView { background: transparent; border: none; }")

    def set_items(self, items: list, kind: str = "live", row: int = 0) -> None:
        self.model_.set_items(list(items), kind)
        if items:
            self.set_row(max(0, min(row, len(items) - 1)))

    def items(self) -> list:
        return [self.model_.item_at(i) for i in range(self.model_.rowCount())]

    def count(self) -> int:
        return self.model_.rowCount()

    def row(self) -> int:
        return self.currentIndex().row()

    def current_item(self):
        return self.model_.item_at(self.row())

    def set_row(self, row: int) -> None:
        idx = self.model_.index(row)
        self.setCurrentIndex(idx)
        self.scrollTo(idx, QAbstractItemView.ScrollHint.EnsureVisible)
        self.viewport().update()

    def step(self, direction: str, by: int = 1) -> bool:
        """Move *by* rows up or down; False at the edge (or sideways)."""
        n, row = self.count(), self.row()
        if direction not in ("up", "down") or n == 0:
            return False
        if row < 0:
            self.set_row(0)
            return True
        new = row - by if direction == "up" else row + by
        if not 0 <= new < n:
            if by > 1:
                new = max(0, min(n - 1, new))
                if new == row:
                    return False
            else:
                return False
        self.set_row(new)
        return True

    def page_rows(self) -> int:
        return max(1, self.viewport().height() // max(1, px(self.ROW_H)))

    def focusInEvent(self, e) -> None:
        super().focusInEvent(e)
        if self.count() and self.row() < 0:
            self.set_row(0)
        self.viewport().update()

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        self.viewport().update()


class TvPosterDelegate(QStyledItemDelegate):
    """A poster in the grid with its title under it."""

    def __init__(self, view: "TvGrid", loader, art_fn) -> None:
        super().__init__(view)
        self._view = view
        self._images = _ImageSource(view, loader)
        self._art_fn = art_fn

    def sizeHint(self, option, index) -> QSize:
        return self._view.gridSize()

    def paint(self, p: QPainter, option, index) -> None:
        it = index.data(Qt.ItemDataRole.UserRole) or {}
        cell = QRectF(option.rect)
        ring = px(TvTile.RING)
        img = QRectF(cell.x() + ring, cell.y() + ring, cell.width() - 2 * ring,
                     cell.height() - 2 * ring - px(48))
        current = index == self._view.currentIndex()
        focus = current and self._view.hasFocus()
        name = str(it.get("name") or it.get("title") or "?")
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        draw_image(p, img, self._images.get(self._art_fn(it)), False, name)
        if focus:
            draw_ring(p, img.adjusted(-ring / 2, -ring / 2, ring / 2, ring / 2),
                      px(12) + ring / 2)
        text = QRectF(img.x(), img.bottom() + px(6), img.width(), px(36))
        p.setPen(colour("text") if focus else colour("text2"))
        p.setFont(font(24, bold=focus))
        fm = p.fontMetrics()
        p.drawText(text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(name, Qt.TextElideMode.ElideRight, int(text.width())))
        p.restore()


class TvGrid(QListView):
    """Posters in rows; the arrows move cell by cell and leave it at its
    edges."""

    CELL = (236, 400)

    def __init__(self, loader=None, art_fn=None, parent=None) -> None:
        super().__init__(parent)
        self.model_ = ChannelListModel(self)
        self.setModel(self.model_)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setGridSize(QSize(px(self.CELL[0]), px(self.CELL[1])))
        self.setItemDelegate(TvPosterDelegate(self, loader, art_fn or (lambda it: None)))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setStyleSheet("QListView { background: transparent; border: none; }")

    def set_items(self, items: list, kind: str = "vod") -> None:
        self.model_.set_items(list(items), kind)
        if items:
            self.set_row(0)

    def items(self) -> list:
        return [self.model_.item_at(i) for i in range(self.model_.rowCount())]

    def count(self) -> int:
        return self.model_.rowCount()

    def row(self) -> int:
        return self.currentIndex().row()

    def current_item(self):
        return self.model_.item_at(self.row())

    def set_row(self, row: int) -> None:
        idx = self.model_.index(row)
        self.setCurrentIndex(idx)
        self.scrollTo(idx, QAbstractItemView.ScrollHint.EnsureVisible)
        self.viewport().update()

    def columns(self) -> int:
        return max(1, self.viewport().width() // max(1, self.gridSize().width()))

    def step(self, direction: str, by: int = 1) -> bool:
        row = self.row()
        if row < 0:
            if self.count():
                self.set_row(0)
                return True
            return False
        new = row
        for _ in range(max(1, by)):
            nxt = tv_nav.grid_step(new, self.count(), self.columns(), direction)
            if nxt is None:
                break
            new = nxt
        if new == row:
            return False
        self.set_row(new)
        return True

    def focusInEvent(self, e) -> None:
        super().focusInEvent(e)
        if self.count() and self.row() < 0:
            self.set_row(0)
        self.viewport().update()

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        self.viewport().update()


# -- over the picture ------------------------------------------------------------------

class TvInfoBar(QWidget):
    """The big bar over the picture: the channel (its number, logo and
    name) or the title, what is on with its progress, what is next, the
    clock - and what the colour keys do. Shown on a key, gone by itself."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.info: dict = {}
        self.legend: dict[str, str] = {}
        self.hide()

    def set_info(self, info: dict, legend: dict[str, str]) -> None:
        self.info = dict(info)
        self.legend = dict(legend)
        self.update()

    def place(self, area: QRect) -> None:
        """Along the bottom of *area*, inside the screen's safe margins."""
        mx, my = int(area.width() * 0.05), int(area.height() * 0.05)
        h = px(300)
        self.setGeometry(area.x() + mx, area.bottom() - my - h,
                         max(px(400), area.width() - 2 * mx), h)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(colour("bg", 0.9))
        p.drawRoundedRect(r, px(22), px(22))
        info = self.info
        pad = px(32)
        x = r.x() + pad
        top = r.y() + pad
        logo = info.get("logo")
        if isinstance(logo, QPixmap) and not logo.isNull():
            box = QRectF(x, top, px(150), px(110))
            draw_image(p, box, logo, True)
            x = box.right() + px(28)
        right = r.right() - pad
        clock = time.strftime("%H:%M")
        p.setFont(font(30, bold=True))
        fm = p.fontMetrics()
        cw = fm.horizontalAdvance(clock)
        p.setPen(colour("text2"))
        p.drawText(QRectF(right - cw, top, cw, px(44)),
                   Qt.AlignmentFlag.AlignVCenter, clock)
        head = " ".join(str(v) for v in (info.get("num"), info.get("name")) if v)
        p.setPen(colour("text"))
        p.setFont(font(40, bold=True))
        fm = p.fontMetrics()
        p.drawText(QRectF(x, top, right - cw - px(24) - x, px(52)),
                   Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(head, Qt.TextElideMode.ElideRight, int(right - cw - px(24) - x)))
        y = top + px(60)
        title = str(info.get("title") or "")
        when = str(info.get("when") or "")
        p.setFont(font(30))
        fm = p.fontMetrics()
        ww = fm.horizontalAdvance(when)
        if when:
            p.setPen(colour("text2"))
            p.drawText(QRectF(right - ww, y, ww, px(42)), Qt.AlignmentFlag.AlignVCenter, when)
        p.setPen(colour("text"))
        p.drawText(QRectF(x, y, right - ww - px(24) - x, px(42)), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(title, Qt.TextElideMode.ElideRight, int(right - ww - px(24) - x)))
        y += px(52)
        frac = info.get("progress")
        if isinstance(frac, (int, float)):
            draw_bar(p, QRectF(x, y, right - x, px(10)), float(frac))
        y += px(22)
        nxt = str(info.get("next") or "")
        if nxt:
            p.setPen(colour("muted"))
            p.setFont(font(26))
            fm = p.fontMetrics()
            p.drawText(QRectF(x, y, right - x, px(38)), Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(nxt, Qt.TextElideMode.ElideRight, int(right - x)))
        # The colour keys along the bottom edge.
        p.setFont(font(24))
        fm = p.fontMetrics()
        lx = r.x() + pad
        d = px(20)
        ly = r.bottom() - pad - px(34)
        for key, col in COLOUR_KEYS:
            text = self.legend.get(key, "")
            if not text:
                continue
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(col))
            p.drawEllipse(QRectF(lx, ly + (px(34) - d) / 2, d, d))
            lx += d + px(10)
            p.setPen(colour("text2"))
            p.drawText(QRectF(lx, ly, fm.horizontalAdvance(text) + 2, px(34)),
                       Qt.AlignmentFlag.AlignVCenter, text)
            lx += fm.horizontalAdvance(text) + px(36)
        p.end()

