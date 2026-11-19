"""Mine ("Mitt"), pure presentation (the window's side is ui/mw_mitt.py, what
it shows core/mitt.py): one page in the middle pane for everything that is
a person's own, built from the shared cards (ui/media_cards.py).

At the top the page's name, a line on what it holds and its two tabs -
Overview and Favorite folders -; under them a search field and the chips
(All, Channels, Films, Series, Radio, Local) that filter the whole page.

Overview: the sections, each hidden while it has nothing to show -

* Continue: a sideways row of what is under way, any kind;
* Live now on your favorites: tiles (logo, name, what is on, a bar of how
  far, "Usually on now" on the top ones) with a heart that takes a channel
  out of the favorites and, pressed again, puts it back (the tile stays,
  dimmed, until the page is drawn again); the first few, then "Show all";
* Your favorites: films, series and web streams, a sideways row;
* To watch: Watch later as a queue - its place, a small poster, the title,
  what it is and how long, Move up, Move down and Remove; a row dragged to
  another place (Alt+Up / Alt+Down, Delete with the keyboard); its count
  and time in the heading;
* New episodes of the series one follows;
* History: a timeline under day headings, each row its time, its title,
  what it is and what its button does (Play, Continue, Play again).

Favorite folders: the person's folders as tiles (the favorites outside any
folder first, "New folder" last); one picked shows its titles under them,
in their order - dragged to another place in it, or onto another folder's
tile to move there.

Every list is one painted view of uniform rows or cells (no widget per
item), as tall as its content inside the page's one scroll area; pictures
come through the window's loaders only for what is on screen and a few
past it. One click plays (or opens); the context-menu key and a right
click ask for the item's menu. Tab goes from the search to the chips to
each section, the arrows move inside one, Enter plays, Esc empties the
search and then goes back. Mirrored right to left; each item has words for
a screen reader.
"""

from __future__ import annotations

import sys
import time
from typing import Any, Callable, Iterable, Sequence

from PyQt6 import sip
from PyQt6.QtCore import (
    QEvent, QMimeData, QModelIndex, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QAction, QColor, QDrag, QFont, QFontMetrics, QIcon, QPainter, QPainterPath, QPen, QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QHBoxLayout, QLabel, QLayout, QLineEdit,
    QListView,
    QPushButton, QSizePolicy, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from ..i18n import tr
from . import media_cards as mc
from .media_cards import Card, ChannelRow, NowNext
from .theme import P, corner_radius

__all__ = ["FolderItemsView", "FolderTileView", "Heading", "MittPage", "QueueView",
           "TileView", "TimelineView"]

#: The drag between a folder's titles and the folders' tiles.
MIME = "application/x-dopeiptv-mitt-favorites"


class Heading(Card):
    """A day's heading among History's rows."""


# -- the parts' measures (pure) ------------------------------------------------------

TILE_H, TILE_MIN, TILE_GAP = 76, 230, 12
TILE_LOGO, HEART = 48, 32
FOLDER_H, FOLDER_MIN = 92, 200
QUEUE_H, TIMELINE_H, FOLDER_ITEM_H = 64, 48, 58
BTN = 32


def tile_metrics(avail: int, min_w: int = TILE_MIN, tile_h: int = TILE_H,
                 gap: int = TILE_GAP) -> mc.GridMetrics:
    """A grid of tiles for *avail* pixels: as many columns as fit at the
    narrowest tile, widened to fill the row (a pixel short of where Qt
    wraps, as media_cards.grid_metrics)."""
    avail = max(2, int(avail))
    room = avail - 1
    cols = max(1, (room + gap) // (min_w + gap))
    slot_w = max(1, room // cols)
    card_w = max(1, (cols * slot_w - (cols - 1) * gap) // cols)
    return mc.GridMetrics(cols, slot_w, card_w, tile_h + gap, gap, tile_h)


def tile_parts(tile: QRect, rtl: bool, heart: bool = True) -> dict[str, QRect]:
    """A live tile's parts: the logo at the start, the heart at the end and
    the text between them - mirrored right to left."""
    logo = QRect(tile.x() + 12, tile.y() + (tile.height() - TILE_LOGO) // 2, TILE_LOGO,
                 TILE_LOGO)
    end = tile.right() - 8
    hrt = QRect(end - HEART + 1, tile.y() + (tile.height() - HEART) // 2, HEART, HEART) \
        if heart else QRect()
    tx = logo.right() + 1 + 12
    stop = (hrt.left() - 6) if heart else (tile.right() - 12)
    text = QRect(tx, tile.y() + 8, max(10, stop - tx + 1), tile.height() - 16)
    parts = {"logo": logo, "heart": hrt, "text": text}
    return {k: (mc._mirror(v, tile, rtl) if v.isValid() else v) for k, v in parts.items()}


def queue_parts(rect: QRect, rtl: bool) -> dict[str, QRect]:
    """A queue row's parts: its place, the poster, the text, then Move up,
    Move down and Remove at the end - mirrored right to left."""
    x = rect.x() + 12
    pos = QRect(x, rect.y(), 24, rect.height())
    x = pos.right() + 1 + 8
    pic = QRect(x, rect.y() + (rect.height() - 48) // 2, 32, 48)
    end = rect.right() - 10
    cy = rect.y() + (rect.height() - BTN) // 2
    remove = QRect(end - BTN + 1, cy, BTN, BTN)
    down = QRect(remove.left() - 6 - BTN, cy, BTN, BTN)
    up = QRect(down.left() - 6 - BTN, cy, BTN, BTN)
    tx = pic.right() + 1 + 12
    text = QRect(tx, rect.y() + 8, max(10, up.left() - 12 - tx), rect.height() - 16)
    parts = {"pos": pos, "picture": pic, "text": text, "up": up, "down": down,
             "remove": remove}
    return {k: mc._mirror(v, rect, rtl) for k, v in parts.items()}


def timeline_parts(rect: QRect, action_w: int, kind_w: int, rtl: bool) -> dict[str, QRect]:
    """A History row's parts: its time, the title, what it is and its
    button at the end - mirrored right to left."""
    x = rect.x() + 14
    when = QRect(x, rect.y(), 76, rect.height())
    end = rect.right() - 10
    act = QRect(end - action_w + 1, rect.y() + (rect.height() - 30) // 2, action_w, 30)
    kind = QRect(act.left() - 10 - kind_w, rect.y(), kind_w, rect.height())
    tx = when.right() + 1 + 8
    title = QRect(tx, rect.y(), max(10, kind.left() - 12 - tx), rect.height())
    parts = {"when": when, "title": title, "kind": kind, "action": act}
    return {k: mc._mirror(v, rect, rtl) for k, v in parts.items()}


def folder_item_parts(rect: QRect, rtl: bool) -> dict[str, QRect]:
    pic = QRect(rect.x() + 14, rect.y() + (rect.height() - 42) // 2, 42, 42)
    tx = pic.right() + 1 + 12
    text = QRect(tx, rect.y() + 8, max(10, rect.right() - 14 - tx), rect.height() - 16)
    return {k: mc._mirror(v, rect, rtl) for k, v in (("picture", pic), ("text", text))}


def drop_index(y: int, row_h: int, count: int) -> int:
    """Where a row dropped at *y* (the view's coordinates) goes: before the
    row whose upper half it is in, after the last past the end."""
    if count <= 0:
        return 0
    return max(0, min(count, (y + row_h // 2) // max(1, row_h)))


# -- painting helpers -----------------------------------------------------------------

def heart_path(r: QRectF) -> QPainterPath:
    """A heart in *r*."""
    w, h = r.width(), r.height()
    x, y = r.x(), r.y()
    p = QPainterPath()
    p.moveTo(x + w * 0.5, y + h * 0.86)
    p.cubicTo(x + w * 0.1, y + h * 0.6, x + w * 0.0, y + h * 0.36, x + w * 0.16, y + h * 0.2)
    p.cubicTo(x + w * 0.3, y + h * 0.06, x + w * 0.46, y + h * 0.12, x + w * 0.5, y + h * 0.27)
    p.cubicTo(x + w * 0.54, y + h * 0.12, x + w * 0.7, y + h * 0.06, x + w * 0.84, y + h * 0.2)
    p.cubicTo(x + w * 1.0, y + h * 0.36, x + w * 0.9, y + h * 0.6, x + w * 0.5, y + h * 0.86)
    p.closeSubpath()
    return p


def paint_heart(p: QPainter, r: QRect, on: bool, hover: bool) -> None:
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if hover:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(mc.surface(2.6))
        p.drawEllipse(QRectF(r))
    box = QRectF(r).adjusted(8, 8.5, -8, -7.5)
    path = heart_path(box)
    if on:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(P["rec"]))
    else:
        pen = QPen(mc.colour("muted"), 1.8)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    p.restore()


def paint_glyph(p: QPainter, r: QRect, glyph: str, ink: QColor, hover: bool) -> None:
    """A small round button with a drawn mark: "up", "down" or "x"."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(mc.surface(2.8 if hover else 2.0))
    rad = corner_radius(8)
    p.drawRoundedRect(QRectF(r), rad, rad)
    pen = QPen(ink, 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    c = QRectF(r).center()
    s = 4.5
    if glyph == "up":
        p.drawPolyline([QPointF(c.x() - s, c.y() + s / 2), QPointF(c.x(), c.y() - s / 2),
                        QPointF(c.x() + s, c.y() + s / 2)])
    elif glyph == "down":
        p.drawPolyline([QPointF(c.x() - s, c.y() - s / 2), QPointF(c.x(), c.y() + s / 2),
                        QPointF(c.x() + s, c.y() - s / 2)])
    else:
        p.drawLine(QPointF(c.x() - s, c.y() - s), QPointF(c.x() + s, c.y() + s))
        p.drawLine(QPointF(c.x() + s, c.y() - s), QPointF(c.x() - s, c.y() + s))
    p.restore()


def paint_chip(p: QPainter, r: QRect, text: str, fill: QColor, ink: QColor,
               font: QFont) -> None:
    """Words on a small rounded plate (a History row's kind, its button)."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fill)
    rad = min(r.height() / 2, corner_radius(9))
    p.drawRoundedRect(QRectF(r), rad, rad)
    p.setFont(font)
    p.setPen(ink)
    fm = QFontMetrics(font)
    p.drawText(r, int(Qt.AlignmentFlag.AlignCenter),
               fm.elidedText(text, Qt.TextElideMode.ElideRight, max(4, r.width() - 12)))
    p.restore()


def paint_group_surface(p: QPainter, rect: QRect, top: bool, bottom: bool, rtl: bool,
                        playing: bool, hover: bool, selected: bool) -> None:
    """One row of a group's surface: rounded at the group's first row's top
    and its last row's foot, a hairline between rows, hover and the chosen
    row a shade more, the one playing the accent's wash with its bar."""
    radius = corner_radius(12)
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
    if playing:
        fill = mc.playing_fill()
    elif selected:
        fill = mc.colour("sel")
    elif hover:
        fill = mc.surface(2.0)
    else:
        fill = mc.surface(1.0)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.fillPath(path, fill)
    if not top and not playing and not selected:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(mc.colour("bg", 0.9))
        p.drawRect(QRectF(r.x() + 12, r.y(), r.width() - 24, 1))
    if playing:
        bar = QRectF(r.right() - 3 if rtl else r.x(), r.y() + 8, 3, r.height() - 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(mc.colour("accent"))
        p.drawRoundedRect(bar, 1.5, 1.5)
    p.restore()


def _state(option, view, index) -> tuple[bool, bool, bool, bool]:
    rtl = option.direction == Qt.LayoutDirection.RightToLeft
    hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
    selected = bool(option.state & QStyle.StateFlag.State_Selected)
    focus = selected and view.hasFocus() and index == view.currentIndex()
    return rtl, hover, selected, focus


def _text(p: QPainter, r: QRect, text: str, font: QFont, ink: QColor, rtl: bool,
          align: Qt.AlignmentFlag | None = None) -> None:
    p.setFont(font)
    p.setPen(ink)
    flags = mc._aligned(rtl) if align is None else align
    p.drawText(r, int(flags), QFontMetrics(font).elidedText(
        text, Qt.TextElideMode.ElideRight, max(4, r.width())))


def _on_tv_line(line: str, playing: bool) -> str:
    """A row's line, with "On TV" first while it plays on a TV (a cast)."""
    return f"{tr('pill_on_tv')} · {line}" if playing and mc.on_tv() else line


# -- the live tiles --------------------------------------------------------------------

class TileDelegate(QStyledItemDelegate):
    def __init__(self, view: "TileView") -> None:
        super().__init__(view)
        self._view = view

    def sizeHint(self, option, index) -> QSize:
        m = self._view.metrics
        return QSize(m.slot_w, m.cell_h)

    def paint(self, p: QPainter, option, index) -> None:
        v = self._view
        row = v.model_.item_at(index.row())
        if row is None:
            return
        rtl, hover, selected, focus = _state(option, v, index)
        m = v.metrics
        col = mc.left_column(option.rect.x(), m, v.viewport().width(), rtl)
        tile = mc.card_in_slot(option.rect, m, col)
        tile.setHeight(TILE_H)
        playing = bool(row.key) and row.key == v.playing_key
        off = row.key in v.off
        parts = tile_parts(tile, rtl)
        mc.begin_paint(p)
        radius = corner_radius(12)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(mc.playing_fill() if playing
                   else mc.surface(2.0 if (hover or selected) else 1.0))
        p.drawRoundedRect(QRectF(tile), radius, radius)
        if focus or playing:
            mc.paint_ring(p, QRectF(tile), radius)
        if off:
            p.setOpacity(0.5)
        pm = v.images.get(row.logo) if row.logo else None
        mc.paint_picture(p, parts["logo"], pm, v.pictures, corner_radius(10),
                         mc.initials_of(row.name, 3), fit=True)
        text = parts["text"]
        name_f = mc.px_font(mc.TITLE_PX, mc.BOLD)
        small = mc.px_font(mc.SMALL_PX)
        note_f = mc.px_font(mc.LINE_PX, QFont.Weight.DemiBold)
        nfm, sfm, xfm = QFontMetrics(name_f), QFontMetrics(small), QFontMetrics(note_f)
        nn = v.now_next(row)
        line = v.line_of(row, nn)
        frac = nn.fraction() if nn is not None else None
        # What plays on a TV (a cast) says so where "Usually watched now" goes.
        on_tv = playing and mc.on_tv()
        habit = (row.key in v.habit and not off) or on_tv
        block = nfm.height() + (2 + sfm.height() if line else 0) \
            + (6 + 3 if frac is not None else 0) + (3 + xfm.height() if habit else 0)
        y = text.y() + (text.height() - block) // 2
        _text(p, QRect(text.x(), y, text.width(), nfm.height()), row.name, name_f,
              mc.playing_ink() if playing else mc.colour("text"), rtl)
        y += nfm.height()
        if line:
            y += 2
            _text(p, QRect(text.x(), y, text.width(), sfm.height()), line, small,
                  mc.colour("text2"), rtl)
            y += sfm.height()
        if frac is not None:
            y += 6
            mc.paint_bar(p, QRectF(text.x(), y, text.width(), 3), frac, rtl)
            y += 3
        if habit:
            y += 3
            _text(p, QRect(text.x(), y, text.width(), xfm.height()),
                  tr("pill_on_tv") if on_tv else tr("mitt_habit"), note_f, mc.playing_ink(), rtl)
        p.setOpacity(1.0)
        paint_heart(p, parts["heart"], not off, v.hover_heart == row.key)
        p.restore()


class TileView(mc.CardView):
    """The favourite channels (and stations) live now, as tiles. Feed it
    ``ChannelRow``s; what is on comes from ``guide`` (asked as a tile
    paints, kept for the minute). Its heart says ``heart(row)``; ``off``
    holds the keys taken out meanwhile, ``habit`` those watched now most."""

    OBJECT_NAME = "MittTileView"
    heart = pyqtSignal(object)

    def __init__(self, loader=None, parent=None) -> None:
        self.guide: Callable[[ChannelRow], NowNext | None] | None = None
        self.line_for: Callable[[ChannelRow], str] | None = None
        self.off: set[str] = set()
        self.habit: set[str] = set()
        self.hover_heart = ""
        self._kept: dict[str, tuple[int, NowNext | None]] = {}
        super().__init__(loader, "poster", parent)
        self.setItemDelegate(TileDelegate(self))
        self.model_.nownext = self.now_next
        self._tick = QTimer(self)
        self._tick.setInterval(30_000)
        self._tick.timeout.connect(self.viewport().update)

    def _remeasure(self, force: bool = False) -> None:
        m = tile_metrics(self.layout_width())
        if m != self.metrics or force:
            self.metrics = m
            self.setGridSize(QSize(m.slot_w, m.cell_h))
            self._relayout()

    def set_items(self, items: Iterable) -> None:
        self._kept.clear()
        super().set_items(items)

    def now_next(self, row: ChannelRow) -> NowNext | None:
        if row.now is not None or self.guide is None:
            return row.now
        minute = int(time.time() // 60)
        kept = self._kept.get(row.key)
        if kept is not None and kept[0] == minute:
            return kept[1]
        try:
            nn = self.guide(row)
        except Exception:
            nn = None
        if len(self._kept) > 2000:
            self._kept.clear()
        self._kept[row.key] = (minute, nn)
        return nn

    def line_of(self, row: ChannelRow, nn: NowNext | None) -> str:
        if nn is not None and nn.title:
            when = mc.clock(nn.start)
            return f"{when}  {nn.title}" if when else nn.title
        if self.line_for is not None:
            try:
                return self.line_for(row)
            except Exception:
                return ""
        return ""

    def wanted_urls(self, item) -> list[str]:
        return [item.logo] if getattr(item, "logo", "") else []

    def heart_at(self, pos: QPoint) -> ChannelRow | None:
        idx = self.indexAt(pos)
        row = self.model_.item_at(idx.row()) if idx.isValid() else None
        if row is None:
            return None
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        m = self.metrics
        slot = self.visualRect(idx)
        col = mc.left_column(slot.x(), m, self.viewport().width(), rtl)
        tile = mc.card_in_slot(slot, m, col)
        tile.setHeight(TILE_H)
        return row if tile_parts(tile, rtl)["heart"].contains(pos) else None

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            row = self.heart_at(e.position().toPoint())
            if row is not None:
                self.setCurrentIndex(self.model_.index(self.model_.row_of(row.key), 0))
                self.heart.emit(row)
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.heart_at(e.position().toPoint()):
            e.accept()                  # the heart's press did it: no click
            return
        super().mouseReleaseEvent(e)

    def mouseMoveEvent(self, e) -> None:
        row = self.heart_at(e.position().toPoint())
        key = row.key if row is not None else ""
        if key != self.hover_heart:
            self.hover_heart = key
            self.setCursor(Qt.CursorShape.PointingHandCursor if key
                           else Qt.CursorShape.ArrowCursor)
            self.viewport().update()
        super().mouseMoveEvent(e)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Delete:
            row = self.current_item()
            if row is not None:
                self.heart.emit(row)
            return
        super().keyPressEvent(e)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._tick.start()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._tick.stop()


# -- lists of uniform rows -------------------------------------------------------------

class _RowList(mc._PaintedView):
    """A painted list of uniform rows, as tall as its content in the page."""

    ROW_H = 56

    def __init__(self, loader=None, parent=None) -> None:
        super().__init__(loader, parent)
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setFlow(QListView.Flow.TopToBottom)
        self.setWrapping(False)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setSpacing(0)

    def content_height(self) -> int:
        return self.model_.rowCount() * self.ROW_H + 2

    def rows_on_screen(self, margin: int = 0) -> range:
        n = self.model_.rowCount()
        if not n:
            return range(0)
        top, bottom = self.shown_span()
        off = self.verticalOffset() if self._host is None else 0
        a = max(0, (top + off) // self.ROW_H - margin * 4)
        b = min(n - 1, (bottom + off) // self.ROW_H + margin * 4)
        return range(a, b + 1) if b >= a and bottom + off >= 0 else range(0)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._ahead = None
        self._ask_ahead()


class _RowDelegate(QStyledItemDelegate):
    def __init__(self, view: _RowList) -> None:
        super().__init__(view)
        self._view = view

    def sizeHint(self, option, index) -> QSize:
        return QSize(1, self._view.ROW_H)


class QueueDelegate(_RowDelegate):
    def paint(self, p: QPainter, option, index) -> None:
        v = self._view
        card = v.model_.item_at(index.row())
        if card is None:
            return
        rtl, hover, selected, focus = _state(option, v, index)
        rect = option.rect
        playing = bool(card.key) and card.key == v.playing_key
        mc.begin_paint(p)
        n = v.model_.rowCount()
        paint_group_surface(p, rect, index.row() == 0, index.row() == n - 1, rtl, playing,
                            hover, selected)
        if focus:
            mc.paint_ring(p, QRectF(rect).adjusted(1, 1, -1, -1), corner_radius(10), 1.6)
        parts = queue_parts(rect, rtl)
        small = mc.px_font(mc.SMALL_PX)
        _text(p, parts["pos"], str(index.row() + 1), small, mc.colour("muted"), rtl,
              Qt.AlignmentFlag.AlignCenter)
        pm, _own = mc.card_picture(v.images, card)
        mc.paint_picture(p, parts["picture"], pm, v.pictures, corner_radius(6),
                         card.initials or mc.initials_of(card.title))
        text = parts["text"]
        tf, lf = mc.px_font(mc.TITLE_PX, mc.BOLD), mc.px_font(mc.LINE_PX)
        tfm, lfm = QFontMetrics(tf), QFontMetrics(lf)
        block = tfm.height() + (2 + lfm.height() if card.line else 0)
        y = text.y() + (text.height() - block) // 2
        _text(p, QRect(text.x(), y, text.width(), tfm.height()), card.title, tf,
              mc.playing_ink() if playing else mc.colour("text"), rtl)
        if card.line:
            _text(p, QRect(text.x(), y + tfm.height() + 2, text.width(), lfm.height()),
                  _on_tv_line(card.line, playing), lf, mc.colour("muted"), rtl)
        hot = v.hover_button if v.hover_button[0] == card.key else ("", "")
        ink = mc.colour("text2")
        first, last = index.row() == 0, index.row() == n - 1
        for name, glyph in (("up", "up"), ("down", "down"), ("remove", "x")):
            if (name == "up" and first) or (name == "down" and last):
                continue
            paint_glyph(p, parts[name], glyph, ink if name != "remove" else mc.colour("muted"),
                        hot[1] == name)
        if v.drop_at is not None and v.drop_at in (index.row(), index.row() + 1):
            y = rect.top() if v.drop_at == index.row() else rect.bottom() - 1
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(mc.colour("accent"))
            p.drawRoundedRect(QRectF(rect.x() + 8, y - 1, rect.width() - 16, 3), 1.5, 1.5)
        p.restore()


class QueueView(_RowList):
    """Watch later as a queue: rows that say their place, with Move up,
    Move down and Remove; a row dragged to another place (``moved_to``),
    Alt+Up / Alt+Down (``nudged``) and Delete (``removed``) with the keys."""

    ROW_H = QUEUE_H
    OBJECT_NAME = "MittQueueView"
    nudged = pyqtSignal(object, int)
    moved_to = pyqtSignal(object, int)
    removed = pyqtSignal(object)

    def __init__(self, loader=None, parent=None) -> None:
        self.hover_button: tuple[str, str] = ("", "")
        self.drop_at: int | None = None
        self._press: tuple[int, QPoint] | None = None
        self._button: tuple[int, str] | None = None
        self._dragging = False
        super().__init__(loader, parent)
        self.setItemDelegate(QueueDelegate(self))

    def button_at(self, pos: QPoint) -> tuple[int, str] | None:
        idx = self.indexAt(pos)
        if not idx.isValid():
            return None
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        parts = queue_parts(self.visualRect(idx), rtl)
        n = self.model_.rowCount()
        for name in ("up", "down", "remove"):
            if (name == "up" and idx.row() == 0) or (name == "down" and idx.row() == n - 1):
                continue
            if parts[name].contains(pos):
                return idx.row(), name
        return None

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            pos = e.position().toPoint()
            hit = self.button_at(pos)
            if hit is not None:
                self._button = hit
                self.setCurrentIndex(self.model_.index(hit[0], 0))
                e.accept()
                return
            idx = self.indexAt(pos)
            self._press = (idx.row(), pos) if idx.isValid() else None
            self._dragging = False
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        pos = e.position().toPoint()
        if self._press is not None and e.buttons() & Qt.MouseButton.LeftButton:
            if not self._dragging and (pos - self._press[1]).manhattanLength() \
                    >= QApplication.startDragDistance():
                self._dragging = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if self._dragging:
                at = drop_index(pos.y(), self.ROW_H, self.model_.rowCount())
                if at != self.drop_at:
                    self.drop_at = at
                    self.viewport().update()
                e.accept()
                return
        hit = self.button_at(pos)
        card = self.model_.item_at(hit[0]) if hit is not None else None
        hover = (card.key, hit[1]) if card is not None and hit is not None else ("", "")
        if hover != self.hover_button:
            self.hover_button = hover
            self.setCursor(Qt.CursorShape.PointingHandCursor if hover[0]
                           else Qt.CursorShape.ArrowCursor)
            self.viewport().update()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            button, self._button = self._button, None
            if button is not None:
                hit = self.button_at(e.position().toPoint())
                card = self.model_.item_at(button[0])
                if hit == button and card is not None:
                    if button[1] == "remove":
                        self.removed.emit(card)
                    else:
                        self.nudged.emit(card, -1 if button[1] == "up" else 1)
                e.accept()
                return
            press, self._press = self._press, None
            if self._dragging:
                self._dragging = False
                at, self.drop_at = self.drop_at, None
                self.unsetCursor()
                self.viewport().update()
                card = self.model_.item_at(press[0]) if press is not None else None
                if card is not None and at is not None and at not in (press[0], press[0] + 1):
                    self.moved_to.emit(card, at)
                e.accept()
                return
        super().mouseReleaseEvent(e)

    def leaveEvent(self, e) -> None:
        super().leaveEvent(e)
        if self.hover_button != ("", ""):
            self.hover_button = ("", "")
            self.viewport().update()

    def keyPressEvent(self, e) -> None:
        k = e.key()
        mods = e.modifiers()
        moving = mods & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ControlModifier)
        card = self.current_item()
        if card is not None and moving and k in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.nudged.emit(card, -1 if k == Qt.Key.Key_Up else 1)
            return
        if card is not None and k == Qt.Key.Key_Delete:
            self.removed.emit(card)
            return
        super().keyPressEvent(e)


class TimelineDelegate(_RowDelegate):
    def paint(self, p: QPainter, option, index) -> None:
        v = self._view
        item = v.model_.item_at(index.row())
        if item is None:
            return
        rtl, hover, selected, focus = _state(option, v, index)
        rect = option.rect
        mc.begin_paint(p)
        if isinstance(item, Heading):
            f = mc.px_font(mc.SMALL_PX, mc.BOLD)
            f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 104)
            fm = QFontMetrics(f)
            r = QRect(rect.x() + 4, rect.bottom() - fm.height() - 8, rect.width() - 8,
                      fm.height())
            _text(p, r, item.title.upper(), f, mc.colour("muted"), rtl)
            p.restore()
            return
        top, bottom = v.group_ends(index.row())
        playing = bool(item.key) and item.key == v.playing_key
        paint_group_surface(p, rect, top, bottom, rtl, playing, hover, selected)
        if focus:
            mc.paint_ring(p, QRectF(rect).adjusted(1, 1, -1, -1), corner_radius(10), 1.6)
        chip_f = mc.px_font(mc.LINE_PX, QFont.Weight.DemiBold)
        act_f = mc.px_font(mc.SMALL_PX, QFont.Weight.DemiBold)
        cfm, afm = QFontMetrics(chip_f), QFontMetrics(act_f)
        kind, action, when = v.words(item)
        kind_w = cfm.horizontalAdvance(kind) + 20 if kind else 0
        action_w = max(72, afm.horizontalAdvance(action) + 26)
        parts = timeline_parts(rect, action_w, kind_w, rtl)
        if playing and mc.on_tv():
            _text(p, parts["when"], tr("pill_on_tv"), mc.px_font(mc.SMALL_PX, mc.BOLD),
                  mc.playing_ink(), rtl)
        else:
            _text(p, parts["when"], when, mc.px_font(mc.SMALL_PX), mc.colour("muted"), rtl)
        _text(p, parts["title"], item.title, mc.px_font(mc.TITLE_PX, QFont.Weight.Medium),
              mc.playing_ink() if playing else mc.colour("text"), rtl)
        if kind:
            kr = parts["kind"]
            chip = QRect(kr.x(), kr.y() + (kr.height() - (cfm.height() + 6)) // 2,
                         kr.width(), cfm.height() + 6)
            paint_chip(p, chip, kind, mc.surface(2.6), mc.colour("text2"), chip_f)
        hot = v.hover_action == item.key
        paint_chip(p, parts["action"], action, mc.surface(3.4 if hot else 2.6),
                   mc.colour("text"), act_f)
        p.restore()


class TimelineView(_RowList):
    """History as a timeline: day ``Heading``s and the rows under them,
    each its group's surface; the arrows step over the headings. A row's
    words beside its title (its kind, its button, its time) come from
    ``words_for``."""

    ROW_H = TIMELINE_H
    OBJECT_NAME = "MittTimelineView"
    #: The rows picked to go from History (Delete); ui/mw_mitt removes them.
    remove_rows = pyqtSignal(list)

    def __init__(self, loader=None, parent=None) -> None:
        self.words_for: Callable[[Card], tuple[str, str, str]] | None = None
        self.hover_action = ""
        super().__init__(loader, parent)
        self.setItemDelegate(TimelineDelegate(self))
        # Several rows picked with Ctrl / Shift, to take them out at once.
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

    def selected_cards(self) -> list[Card]:
        rows = sorted(i.row() for i in self.selectionModel().selectedRows())
        cards = [self.model_.item_at(r) for r in rows]
        return [c for c in cards if c is not None and not isinstance(c, Heading)]

    def words(self, item: Card) -> tuple[str, str, str]:
        if self.words_for is None:
            return "", tr("btn_play"), item.line
        try:
            return self.words_for(item)
        except Exception:
            return "", tr("btn_play"), ""

    def group_ends(self, row: int) -> tuple[bool, bool]:
        prev = self.model_.item_at(row - 1)
        nxt = self.model_.item_at(row + 1)
        return (prev is None or isinstance(prev, Heading),
                nxt is None or isinstance(nxt, Heading))

    def wanted_urls(self, item) -> list[str]:
        return []

    def moveCursor(self, action, modifiers) -> QModelIndex:
        idx = super().moveCursor(action, modifiers)
        n = self.model_.rowCount()
        back = action in (QAbstractItemView.CursorAction.MoveUp,
                          QAbstractItemView.CursorAction.MovePrevious,
                          QAbstractItemView.CursorAction.MovePageUp,
                          QAbstractItemView.CursorAction.MoveHome)
        guard = 0
        while idx.isValid() and isinstance(self.model_.item_at(idx.row()), Heading) and guard < 4:
            row = idx.row() + (-1 if back and idx.row() > 0 else 1)
            if not 0 <= row < n:
                return self.currentIndex()
            idx = self.model_.index(row, 0)
            guard += 1
        return idx

    def focusInEvent(self, e) -> None:
        super().focusInEvent(e)
        cur = self.currentIndex()
        if cur.isValid() and isinstance(self.model_.item_at(cur.row()), Heading):
            nxt = self.model_.index(cur.row() + 1, 0)
            if nxt.isValid():
                self.setCurrentIndex(nxt)

    def _clicked(self, idx) -> None:
        if isinstance(self.model_.item_at(idx.row()), Heading):
            return
        if QApplication.keyboardModifiers() & (
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
                | Qt.KeyboardModifier.MetaModifier):
            return                      # gathering rows to take out: nothing plays
        super()._clicked(idx)

    def mouseMoveEvent(self, e) -> None:
        idx = self.indexAt(e.position().toPoint())
        item = self.model_.item_at(idx.row()) if idx.isValid() else None
        key = item.key if item is not None and not isinstance(item, Heading) else ""
        if key != self.hover_action:
            self.hover_action = key
            self.setCursor(Qt.CursorShape.PointingHandCursor if key
                           else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(e)

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                and isinstance(self.current_item(), Heading):
            return
        # Delete (a Mac's delete key is Backspace): the rows picked go.
        if e.key() == Qt.Key.Key_Delete or (sys.platform == "darwin" and e.key() == Qt.Key.Key_Backspace):
            cards = self.selected_cards()
            if cards:
                self.remove_rows.emit(cards)
                e.accept()
                return
        super().keyPressEvent(e)


# -- the folders ------------------------------------------------------------------------

class FolderTileDelegate(QStyledItemDelegate):
    def __init__(self, view: "FolderTileView") -> None:
        super().__init__(view)
        self._view = view

    def sizeHint(self, option, index) -> QSize:
        m = self._view.metrics
        return QSize(m.slot_w, m.cell_h)

    def paint(self, p: QPainter, option, index) -> None:
        v = self._view
        card = v.model_.item_at(index.row())
        if card is None:
            return
        rtl, hover, selected, focus = _state(option, v, index)
        m = v.metrics
        col = mc.left_column(option.rect.x(), m, v.viewport().width(), rtl)
        tile = mc.card_in_slot(option.rect, m, col)
        tile.setHeight(FOLDER_H)
        picked = card.key == v.current_key
        target = card.key == v.drop_key
        mc.begin_paint(p)
        radius = corner_radius(12)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(mc.playing_fill() if (picked or target)
                   else mc.surface(2.0 if (hover or selected) else 1.0))
        p.drawRoundedRect(QRectF(tile), radius, radius)
        if focus or target:
            mc.paint_ring(p, QRectF(tile), radius)
        sw = QRect(tile.x() + 14, tile.y() + 14, 36, 36)
        sw = mc._mirror(sw, tile, rtl)
        fill, ink = v.swatch(card)
        p.setBrush(fill)
        p.drawRoundedRect(QRectF(sw), corner_radius(10), corner_radius(10))
        glyph = "+" if card.key == FolderTileView.NEW else (card.initials
                                                           or mc.initials_of(card.title))
        _text(p, sw, glyph, mc.px_font(16 if glyph == "+" else 13, mc.BOLD), ink, rtl,
              Qt.AlignmentFlag.AlignCenter)
        tf, lf = mc.px_font(mc.TITLE_PX, mc.BOLD), mc.px_font(mc.LINE_PX)
        tfm, lfm = QFontMetrics(tf), QFontMetrics(lf)
        tr_ = QRect(tile.x() + 14, sw.bottom() + 10, tile.width() - 28, tfm.height())
        _text(p, mc._mirror(tr_, tile, rtl), card.title, tf,
              mc.colour("text2") if card.key == FolderTileView.NEW else
              (mc.playing_ink() if picked else mc.colour("text")), rtl)
        if card.line:
            lr = QRect(tile.x() + 14, tr_.bottom() + 2, tile.width() - 28, lfm.height())
            _text(p, mc._mirror(lr, tile, rtl), card.line, lf, mc.colour("muted"), rtl)
        p.restore()


class FolderTileView(mc.CardView):
    """The favourite folders as tiles; the picked one marked, a tile the
    dragged titles would go to lit (``dropped_on(card)``)."""

    OBJECT_NAME = "MittFolderView"
    NEW = "__new__"
    dropped_on = pyqtSignal(object)

    def __init__(self, loader=None, parent=None) -> None:
        self.current_key = ""
        self.drop_key = ""
        self.swatch_for: Callable[[Card], tuple[QColor, QColor] | None] | None = None
        super().__init__(loader, "poster", parent)
        self.setItemDelegate(FolderTileDelegate(self))
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)

    def _remeasure(self, force: bool = False) -> None:
        m = tile_metrics(self.layout_width(), FOLDER_MIN, FOLDER_H)
        if m != self.metrics or force:
            self.metrics = m
            self.setGridSize(QSize(m.slot_w, m.cell_h))
            self._relayout()

    def wanted_urls(self, item) -> list[str]:
        return []

    def swatch(self, card: Card) -> tuple[QColor, QColor]:
        if self.swatch_for is not None:
            got = self.swatch_for(card)
            if got is not None:
                return got
        return mc.surface(3.0), mc.colour("text2")

    def set_current(self, key: str) -> None:
        self.current_key = key or ""
        self.viewport().update()

    def _target(self, pos: QPoint) -> Card | None:
        idx = self.indexAt(pos)
        card = self.model_.item_at(idx.row()) if idx.isValid() else None
        return card if card is not None and card.key != self.NEW else None

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e) -> None:
        card = self._target(e.position().toPoint())
        key = card.key if card is not None and card.key != self.current_key else ""
        if key != self.drop_key:
            self.drop_key = key
            self.viewport().update()
        if key:
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragLeaveEvent(self, e) -> None:
        self.drop_key = ""
        self.viewport().update()

    def dropEvent(self, e) -> None:
        card = self._target(e.position().toPoint())
        self.drop_key = ""
        self.viewport().update()
        if card is None or card.key == self.current_key or not e.mimeData().hasFormat(MIME):
            e.ignore()
            return
        e.acceptProposedAction()
        self.dropped_on.emit(card)


class FolderItemDelegate(_RowDelegate):
    def paint(self, p: QPainter, option, index) -> None:
        v = self._view
        card = v.model_.item_at(index.row())
        if card is None:
            return
        rtl, hover, selected, focus = _state(option, v, index)
        rect = option.rect
        playing = bool(card.key) and card.key == v.playing_key
        mc.begin_paint(p)
        n = v.model_.rowCount()
        paint_group_surface(p, rect, index.row() == 0, index.row() == n - 1, rtl, playing,
                            hover, selected)
        if focus:
            mc.paint_ring(p, QRectF(rect).adjusted(1, 1, -1, -1), corner_radius(10), 1.6)
        parts = folder_item_parts(rect, rtl)
        pm, _own = mc.card_picture(v.images, card)
        mc.paint_picture(p, parts["picture"], pm, v.pictures, corner_radius(8),
                         card.initials or mc.initials_of(card.title, 3),
                         fit=card.rank == FolderItemsView.LOGO)
        text = parts["text"]
        tf, lf = mc.px_font(mc.TITLE_PX, mc.BOLD), mc.px_font(mc.LINE_PX)
        tfm, lfm = QFontMetrics(tf), QFontMetrics(lf)
        block = tfm.height() + (2 + lfm.height() if card.line else 0)
        y = text.y() + (text.height() - block) // 2
        _text(p, QRect(text.x(), y, text.width(), tfm.height()), card.title, tf,
              mc.playing_ink() if playing else mc.colour("text"), rtl)
        if card.line:
            _text(p, QRect(text.x(), y + tfm.height() + 2, text.width(), lfm.height()),
                  _on_tv_line(card.line, playing), lf, mc.colour("muted"), rtl)
        if v.drop_at is not None and v.drop_at in (index.row(), index.row() + 1):
            y = rect.top() if v.drop_at == index.row() else rect.bottom() - 1
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(mc.colour("accent"))
            p.drawRoundedRect(QRectF(rect.x() + 8, y - 1, rect.width() - 16, 3), 1.5, 1.5)
        p.restore()


class FolderItemsView(_RowList):
    """A folder's titles in their order: dragged to another place in it
    (``reordered(cards, index)``) or onto a folder's tile (the page's
    ``drag_cards`` say which). A card's *rank* ``LOGO`` fits its picture
    (a channel's logo) rather than covering the box with it."""

    ROW_H = FOLDER_ITEM_H
    OBJECT_NAME = "MittFolderItems"
    LOGO = 1
    reordered = pyqtSignal(list, int)
    drag_started = pyqtSignal(list)

    def __init__(self, loader=None, parent=None) -> None:
        self.drop_at: int | None = None
        self.can_reorder = True
        self._press: tuple[int, QPoint] | None = None
        super().__init__(loader, parent)
        self.setItemDelegate(FolderItemDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)

    def _clicked(self, idx) -> None:
        mods = QApplication.keyboardModifiers()
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
                   | Qt.KeyboardModifier.MetaModifier):
            return                      # gathering rows for a move: nothing plays
        super()._clicked(idx)

    def selected_cards(self) -> list[Card]:
        rows = sorted(i.row() for i in self.selectionModel().selectedRows())
        cards = [self.model_.item_at(r) for r in rows]
        return [c for c in cards if c is not None]

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            idx = self.indexAt(e.position().toPoint())
            self._press = (idx.row(), e.position().toPoint()) if idx.isValid() else None
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        press = self._press
        if press is not None and e.buttons() & Qt.MouseButton.LeftButton and \
                (e.position().toPoint() - press[1]).manhattanLength() \
                >= QApplication.startDragDistance():
            self._press = None
            self._start_drag(press[0])
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        self._press = None
        super().mouseReleaseEvent(e)

    def _start_drag(self, row: int) -> None:
        cards = self.selected_cards()
        card = self.model_.item_at(row)
        if card is None:
            return
        if card not in cards:
            cards = [card]
        self.drag_started.emit(cards)
        drag = QDrag(self)
        data = QMimeData()
        data.setData(MIME, b"1")
        drag.setMimeData(data)
        pm = QPixmap(220, 36)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        paint_chip(p, QRect(0, 0, 220, 36), card.title if len(cards) == 1
                   else f"{card.title}  +{len(cards) - 1}", mc.colour("accent"),
                   mc.colour("on_accent"), mc.px_font(mc.SMALL_PX, mc.BOLD))
        p.end()
        drag.setPixmap(pm)
        drag.setHotSpot(QPoint(16, 18))
        drag.exec(Qt.DropAction.MoveAction)
        self.drop_at = None
        self.viewport().update()

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasFormat(MIME) and e.source() is self and self.can_reorder:
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e) -> None:
        if not (e.mimeData().hasFormat(MIME) and e.source() is self and self.can_reorder):
            e.ignore()
            return
        at = drop_index(e.position().toPoint().y(), self.ROW_H, self.model_.rowCount())
        if at != self.drop_at:
            self.drop_at = at
            self.viewport().update()
        e.acceptProposedAction()

    def dragLeaveEvent(self, e) -> None:
        self.drop_at = None
        self.viewport().update()

    def dropEvent(self, e) -> None:
        at, self.drop_at = self.drop_at, None
        self.viewport().update()
        if at is None or e.source() is not self:
            e.ignore()
            return
        e.acceptProposedAction()
        self.reordered.emit(self.selected_cards(), at)


# -- the search field and the chips -----------------------------------------------------

def search_icon(ink: str, size: int = 16, dpr: float = 1.0) -> QIcon:
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(ink), 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    s = float(size)
    p.drawEllipse(QRectF(s * 0.16, s * 0.16, s * 0.54, s * 0.54))
    p.drawLine(QPointF(s * 0.62, s * 0.62), QPointF(s * 0.86, s * 0.86))
    p.end()
    return QIcon(pm)


class FlowLayout(QLayout):
    """Widgets in a row that wraps onto the next line when it runs out of
    room (the chips at any width of the pane) - mirrored right to left."""

    def __init__(self, parent=None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list = []
        self._gap = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802 - Qt's name
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):  # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):  # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._place(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._place(rect, False)

    def sizeHint(self) -> QSize:  # noqa: N802
        w = sum(it.sizeHint().width() for it in self._items) \
            + self._gap * max(0, len(self._items) - 1)
        h = max((it.sizeHint().height() for it in self._items), default=0)
        return QSize(w, h)

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        return size

    def _place(self, rect: QRect, test: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        parent = self.parentWidget()
        rtl = parent is not None and parent.isRightToLeft()
        for it in self._items:
            hint = it.sizeHint()
            if x > rect.x() and x + hint.width() > rect.right() + 1:
                x, y, line = rect.x(), y + line + self._gap, 0
            if not test:
                r = QRect(QPoint(x, y), hint)
                if rtl:
                    r.moveLeft(rect.right() - (x - rect.x()) - hint.width() + 1)
                it.setGeometry(r)
            x += hint.width() + self._gap
            line = max(line, hint.height())
        return y + line - rect.y()


class ChipBar(QWidget):
    """The filter's chips: one stop for Tab (the chip picked), the arrows
    move between them and pick."""

    changed = pyqtSignal(str)

    def __init__(self, options: Sequence[tuple[str, str]], parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("MittChips")
        lay = FlowLayout(self, 6)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: list[QPushButton] = []
        self.keys: list[str] = []
        for i, (key, label) in enumerate(options):
            b = QPushButton(label)
            b.setObjectName("MittChip")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setAccessibleName(label)
            b.installEventFilter(self)
            self._group.addButton(b, i)
            lay.addWidget(b)
            self.buttons.append(b)
            self.keys.append(key)
        self._group.idClicked.connect(self._picked)
        if self.buttons:
            self.buttons[0].setChecked(True)
        self._sync_focus()
        self.restyle()

    def restyle(self) -> None:
        r = 17
        self.setStyleSheet(
            f"QPushButton#MittChip {{ background:{P['input']}; color:{P['text2']};"
            f" border:none; border-radius:{r}px; padding:0 14px; min-height:34px;"
            " font-size:13px; font-weight:600; }"
            f"QPushButton#MittChip:hover {{ color:{P['text']}; background:{P['btn']}; }}"
            f"QPushButton#MittChip:checked {{ background:{P['accent']};"
            f" color:{P['on_accent']}; }}"
            f"QPushButton#MittChip:focus {{ outline:none; border:2px solid {P['accent_hi']}; }}")

    def retranslate(self, labels: Sequence[str]) -> None:
        for b, label in zip(self.buttons, labels, strict=False):
            b.setText(label)
            b.setAccessibleName(label)

    def current(self) -> str:
        i = self._group.checkedId()
        return self.keys[i] if 0 <= i < len(self.keys) else ""

    def set_current(self, key: str) -> None:
        if key in self.keys:
            self.buttons[self.keys.index(key)].setChecked(True)
            self._sync_focus()

    def _sync_focus(self) -> None:
        for b in self.buttons:
            b.setFocusPolicy(Qt.FocusPolicy.StrongFocus if b.isChecked()
                             else Qt.FocusPolicy.ClickFocus)

    def _picked(self, i: int) -> None:
        self._sync_focus()
        if 0 <= i < len(self.keys):
            self.changed.emit(self.keys[i])

    def eventFilter(self, obj, e) -> bool:
        if e.type() == QEvent.Type.KeyPress and obj in self.buttons and \
                e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = -1 if e.key() == Qt.Key.Key_Left else 1
            if self.isRightToLeft():
                step = -step
            i = max(0, min(len(self.buttons) - 1, self.buttons.index(obj) + step))
            if self.buttons[i] is not obj:
                self.buttons[i].setChecked(True)
                self.buttons[i].setFocus(Qt.FocusReason.TabFocusReason)
                self._picked(i)
            return True
        return False


class _SectionHead(QWidget):
    """A section's heading: its name, a muted note beside it and, at the
    end, a button of its own (Show all, Show more)."""

    def __init__(self, title: str = "", parent=None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.title = QLabel(title)
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.note = QLabel("")
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        self.note.setWordWrap(True)
        self.note.hide()
        lay.addWidget(self.title, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.note, 1, Qt.AlignmentFlag.AlignVCenter)
        self.button = QPushButton("")
        self.button.setObjectName("MittSectionBtn")
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.hide()
        lay.addWidget(self.button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.restyle()

    def restyle(self) -> None:
        self.title.setStyleSheet(f"color:{P['text']}; font-size:15px; font-weight:600;"
                                 " background:transparent;")
        self.note.setStyleSheet(f"color:{P['muted']}; font-size:12px; background:transparent;")
        r = round(corner_radius(9))
        self.button.setStyleSheet(
            f"QPushButton#MittSectionBtn {{ background:{P['input']}; color:{P['text2']};"
            f" border:none; border-radius:{r}px; padding:5px 12px; font-size:12px;"
            " font-weight:600; }"
            f"QPushButton#MittSectionBtn:hover {{ background:{P['btn']}; color:{P['text']}; }}"
            f"QPushButton#MittSectionBtn:focus {{ outline:none;"
            f" border:2px solid {P['accent_hi']}; }}")

    def set_note(self, text: str) -> None:
        self.note.setText(text)
        self.note.setVisible(bool(text))

    def set_button(self, text: str) -> None:
        self.button.setText(text)
        self.button.setAccessibleName(text)
        self.button.setVisible(bool(text))


class _Section(QWidget):
    """A heading over a view; hidden while it has nothing to show."""

    def __init__(self, head: QWidget, body: QWidget, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lay.addWidget(head)
        lay.addWidget(body)
        self.head, self.body = head, body
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


# -- the page --------------------------------------------------------------------------

#: The sections, in order: (name, what the views' signals say).
SECTIONS = ("continue", "live", "favorites", "queue", "new", "history")


class MittPage(QWidget):
    """Mine. The window fills it (``set_section``, ``set_folders``,
    ``set_folder_items``) and acts on what it says: ``activated(section,
    item)``, ``menu_requested(section, item, pos)``, the live tiles'
    ``heart``, the queue's ``queue_nudged`` / ``queue_moved`` /
    ``queue_removed``, the filter's ``filter_changed``, the tabs'
    ``tab_changed``, a section's ``more``; the folders' ``folder_picked``,
    ``folder_new``, ``folder_menu``, ``folder_drop(cards, folder)`` and
    ``folder_reorder(cards, index)``; ``back``."""

    activated = pyqtSignal(str, object)
    menu_requested = pyqtSignal(str, object, QPoint)
    #: History's rows picked and Delete pressed (TimelineView.remove_rows).
    history_remove = pyqtSignal(list)
    heart = pyqtSignal(object)
    queue_nudged = pyqtSignal(object, int)
    queue_moved = pyqtSignal(object, int)
    queue_removed = pyqtSignal(object)
    filter_changed = pyqtSignal()
    tab_changed = pyqtSignal(str)
    more = pyqtSignal(str)
    back = pyqtSignal()
    folder_picked = pyqtSignal(object)
    folder_new = pyqtSignal(QPoint)
    folder_menu = pyqtSignal(object, QPoint)
    folder_drop = pyqtSignal(list, object)
    folder_reorder = pyqtSignal(list, int)

    #: How long typing rests before the filter runs.
    SEARCH_MS = 160

    def __init__(self, logos=None, posters=None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("MittPage")
        self.drag_cards: list[Card] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.place = mc.PlacePage(tr("nav_mine"))
        lay.addWidget(self.place)
        h = self.place.header
        h.set_count(tr("mitt_subtitle"))
        h.count.setWordWrap(True)
        h.set_views([("overview", tr("mitt_tab_overview")),
                     ("folders", tr("mitt_tab_folders"))], "overview")
        h.switch.setAccessibleName(tr("mitt_tabs_a11y"))
        h.view_changed.connect(self._tab_picked)

        # -- the search field and the chips --------------------------------------------
        bar = QWidget()
        bar.setObjectName("MittBar")
        bl = QVBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(10)
        self.field = QLineEdit()
        self.field.setObjectName("MittSearch")
        self.field.setClearButtonEnabled(True)
        self.field.setMinimumWidth(160)
        self._icon = QAction(self.field)
        self.field.addAction(self._icon, QLineEdit.ActionPosition.LeadingPosition)
        self.field.textChanged.connect(self._typed)
        bl.addWidget(self.field)
        self.chips = ChipBar(self._chip_options())
        self.chips.setAccessibleName(tr("mitt_filter_a11y"))
        self.chips.changed.connect(lambda _k: self.filter_changed.emit())
        bl.addWidget(self.chips)
        self.place.add_row(bar)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(self.SEARCH_MS)
        self._search_timer.timeout.connect(self.filter_changed.emit)

        # -- the overview --------------------------------------------------------------
        self.content = QWidget()
        self.content.setObjectName("MittContent")
        cl = QVBoxLayout(self.content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(28)
        self.overview = QWidget()
        ol = QVBoxLayout(self.overview)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(28)
        self.rows: dict[str, mc.CardRow] = {}
        self.sections: dict[str, QWidget] = {}
        self.heads: dict[str, _SectionHead] = {}

        self.rows["continue"] = mc.CardRow(tr("card_continue"), posters, picture="wide")
        self.tiles = TileView(logos)
        self.tiles.setAccessibleName(tr("mitt_live"))
        self.heads["live"] = _SectionHead(tr("mitt_live"))
        self.heads["live"].button.clicked.connect(lambda: self.more.emit("live"))
        self.rows["favorites"] = mc.CardRow(tr("mitt_favorites"), posters, picture="poster")
        self.queue = QueueView(posters)
        self.queue.setAccessibleName(tr("mitt_queue"))
        self.heads["queue"] = _SectionHead(tr("mitt_queue"))
        self.rows["new"] = mc.CardRow(tr("mitt_new"), posters, picture="poster")
        self.timeline = TimelineView(None)
        self.timeline.setAccessibleName(tr("nav_history"))
        self.heads["history"] = _SectionHead(tr("nav_history"))
        self.history_more = QPushButton(tr("card_scroll_on"))
        self.history_more.setObjectName("MittSectionBtn")
        self.history_more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.history_more.clicked.connect(lambda: self.more.emit("history"))
        self.history_more.hide()
        hist_body = QWidget()
        hb = QVBoxLayout(hist_body)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.setSpacing(12)
        hb.addWidget(self.timeline)
        hb.addWidget(self.history_more, 0, Qt.AlignmentFlag.AlignLeft)

        self.sections["continue"] = self.rows["continue"]
        self.sections["live"] = _Section(self.heads["live"], self.tiles)
        self.sections["favorites"] = self.rows["favorites"]
        self.sections["queue"] = _Section(self.heads["queue"], self.queue)
        self.sections["new"] = self.rows["new"]
        self.sections["history"] = _Section(self.heads["history"], hist_body)
        for name in SECTIONS:
            ol.addWidget(self.sections[name])
            self.sections[name].setVisible(False)
        self.empty = QLabel("")
        self.empty.setObjectName("MittEmpty")
        self.empty.setWordWrap(True)
        self.empty.setTextFormat(Qt.TextFormat.PlainText)
        self.empty.hide()
        ol.addWidget(self.empty)
        cl.addWidget(self.overview)

        # -- the folders ---------------------------------------------------------------
        self.folders = QWidget()
        fl = QVBoxLayout(self.folders)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(16)
        self.folders_note = QLabel(tr("mitt_folders_note"))
        self.folders_note.setWordWrap(True)
        self.folders_note.setTextFormat(Qt.TextFormat.PlainText)
        fl.addWidget(self.folders_note)
        self.folder_tiles = FolderTileView(None)
        self.folder_tiles.setAccessibleName(tr("mitt_tab_folders"))
        fl.addWidget(self.folder_tiles)
        self.folder_head = _SectionHead("")
        fl.addWidget(self.folder_head)
        self.folder_items = FolderItemsView(posters)
        fl.addWidget(self.folder_items)
        self.folder_empty = QLabel("")
        self.folder_empty.setWordWrap(True)
        self.folder_empty.setTextFormat(Qt.TextFormat.PlainText)
        self.folder_empty.hide()
        fl.addWidget(self.folder_empty)
        self.folders.hide()
        cl.addWidget(self.folders)
        self.place.set_content(self.content)

        # -- the views' signals ----------------------------------------------------------
        for name in ("continue", "favorites", "new"):
            row = self.rows[name]
            row.activated.connect(lambda it, n=name: self.activated.emit(n, it))
            row.menu.connect(lambda it, pos, n=name: self.menu_requested.emit(n, it, pos))
            row.strip.back.connect(self.back)
            row.strip.setAccessibleName(row.heading.text())
        for name, view in (("live", self.tiles), ("queue", self.queue),
                           ("history", self.timeline)):
            self._fit(view)
            view.activated.connect(lambda it, n=name: self._activate(n, it))
            view.menu.connect(lambda it, pos, n=name: self._menu(n, it, pos))
            view.back.connect(self.back)
        self.timeline.remove_rows.connect(self.history_remove.emit)
        self.tiles.heart.connect(self.heart)
        self.queue.nudged.connect(self.queue_nudged)
        self.queue.moved_to.connect(self.queue_moved)
        self.queue.removed.connect(self.queue_removed)
        self._fit(self.folder_tiles)
        self._fit(self.folder_items)
        self.folder_tiles.activated.connect(self._folder_activated)
        self.folder_tiles.menu.connect(self._folder_menu)
        self.folder_tiles.back.connect(self.back)
        self.folder_tiles.dropped_on.connect(self._dropped_on)
        self.folder_items.activated.connect(lambda it: self.activated.emit("folder", it))
        self.folder_items.menu.connect(
            lambda it, pos: self.menu_requested.emit("folder", it, pos))
        self.folder_items.back.connect(self.back)
        self.folder_items.drag_started.connect(self._drag_started)
        self.folder_items.reordered.connect(self.folder_reorder)
        self._chain()
        self.restyle()
        self.retranslate()

    # -- building ---------------------------------------------------------------------

    def _fit(self, view: mc._PaintedView) -> None:
        view.set_host(self.place.area)
        view.moved.connect(lambda r, v=view: self.place.reveal(v, r))

    def _chain(self) -> None:
        """Tab: the search, the chips, then each section in order."""
        order: list[QWidget] = [self.field, *self.chips.buttons]
        order += [self.rows["continue"].strip, self.tiles, self.heads["live"].button,
                  self.rows["favorites"].strip, self.queue, self.rows["new"].strip,
                  self.timeline, self.history_more, self.folder_tiles, self.folder_items]
        for a, b in zip(order, order[1:], strict=False):
            QWidget.setTabOrder(a, b)

    @staticmethod
    def _chip_options() -> list[tuple[str, str]]:
        return [("all", tr("mitt_all")), ("channel", tr("fav_channels")),
                ("film", tr("nav_movies")), ("series", tr("nav_series")),
                ("radio", tr("nav_radio")), ("local", tr("nav_local"))]

    def restyle(self) -> None:
        self.place.restyle()
        r = round(corner_radius(10))
        self.field.setStyleSheet(
            f"QLineEdit#MittSearch {{ background:{P['input']}; color:{P['text']};"
            f" border:2px solid transparent; border-radius:{r}px; padding:0 10px;"
            " min-height:36px; font-size:14px; }"
            f"QLineEdit#MittSearch:focus {{ border:2px solid {P['accent_hi']}; }}")
        self._icon.setIcon(search_icon(P["muted"], 16, self.devicePixelRatioF() or 1.0))
        self.chips.restyle()
        for head in (*self.heads.values(), self.folder_head):
            head.restyle()
        self.history_more.setStyleSheet(self.heads["history"].button.styleSheet())
        for row in self.rows.values():
            row.restyle()
        for v in (self.tiles, self.queue, self.timeline, self.folder_tiles, self.folder_items):
            v.restyle()
        for lbl in (self.empty, self.folder_empty, self.folders_note):
            lbl.setStyleSheet(f"color:{P['muted']}; font-size:13px; background:transparent;")
        self.update()

    def retranslate(self) -> None:
        h = self.place.header
        h.set_title(tr("nav_mine"))
        h.set_count(tr("mitt_subtitle"))
        key = h.view_key()
        h.set_views([("overview", tr("mitt_tab_overview")),
                     ("folders", tr("mitt_tab_folders"))], key)
        self.field.setPlaceholderText(tr("mitt_search_hint"))
        self.field.setAccessibleName(tr("mitt_search_a11y"))
        self.chips.retranslate([label for _k, label in self._chip_options()])
        self.chips.setAccessibleName(tr("mitt_filter_a11y"))
        self.rows["continue"].set_title(tr("card_continue"))
        self.rows["favorites"].set_title(tr("mitt_favorites"))
        self.rows["new"].set_title(tr("mitt_new"))
        for name in ("continue", "favorites", "new"):
            self.rows[name].strip.setAccessibleName(self.rows[name].heading.text())
        self.heads["live"].title.setText(tr("mitt_live"))
        self.heads["queue"].title.setText(tr("mitt_queue"))
        self.heads["history"].title.setText(tr("nav_history"))
        self.history_more.setText(tr("card_scroll_on"))
        self.folders_note.setText(tr("mitt_folders_note"))
        self.tiles.setAccessibleName(tr("mitt_live"))
        self.tiles.setAccessibleDescription(tr("mitt_live_keys"))
        self.queue.setAccessibleName(tr("mitt_queue"))
        self.queue.setAccessibleDescription(tr("mitt_queue_keys"))
        self.timeline.setAccessibleName(tr("nav_history"))
        self.folder_tiles.setAccessibleName(tr("mitt_tab_folders"))

    # -- what the window puts in -------------------------------------------------------

    def set_section(self, name: str, items: Sequence[Any], note: str = "",
                    button: str = "") -> None:
        """Section *name*'s items (Cards; ChannelRows for "live"); its
        heading's note and button. Empty: the section is hidden."""
        items = list(items)
        if name in self.rows:
            self.rows[name].set_cards(items)
        else:
            view = {"live": self.tiles, "queue": self.queue, "history": self.timeline}[name]
            view.set_items(items)
            head = self.heads[name]
            head.set_note(note)
            if name == "history":
                self.history_more.setText(button)
                self.history_more.setVisible(bool(button))
            else:
                head.set_button(button)
            self.sections[name].setVisible(bool(items))

    def section_items(self, name: str) -> list:
        if name in self.rows:
            return self.rows[name].cards()
        return {"live": self.tiles, "queue": self.queue, "history": self.timeline}[name].items()

    def set_empty(self, text: str) -> None:
        self.empty.setText(text)
        self.empty.setVisible(bool(text))

    def set_playing(self, keys: set[str]) -> None:
        """What plays, marked wherever it is on the page."""
        views = [r.strip for r in self.rows.values()]
        views += [self.tiles, self.queue, self.timeline, self.folder_items]
        for v in views:
            key = next((k for k in keys if v.row_of(k) >= 0), "")
            v.set_playing(key)

    def set_folders(self, cards: Sequence[Card], current: str) -> None:
        self.folder_tiles.set_items(cards)
        self.folder_tiles.set_current(current)

    def set_folder_items(self, title: str, note: str, cards: Sequence[Card],
                         empty: str, reorder: bool) -> None:
        self.folder_head.title.setText(title)
        self.folder_head.set_note(note)
        self.folder_head.setVisible(bool(title))
        self.folder_items.set_items(cards)
        self.folder_items.can_reorder = reorder
        self.folder_items.setVisible(bool(cards))
        self.folder_empty.setText(empty)
        self.folder_empty.setVisible(bool(empty) and not cards)

    # -- the filter ---------------------------------------------------------------------

    def query(self) -> str:
        return self.field.text()

    def chip(self) -> str:
        return self.chips.current() or "all"

    def tab(self) -> str:
        return self.place.header.view_key() or "overview"

    def set_tab(self, key: str) -> None:
        self.place.header.set_view_key(key)
        self._show_tab(key)

    def _typed(self, _text: str) -> None:
        self._search_timer.start()

    def escape(self) -> bool:
        """Esc: the search emptied first (True); else nothing (False) - the
        window goes back."""
        if self.field.text():
            self.field.clear()
            self._search_timer.stop()
            self.filter_changed.emit()
            self.field.setFocus()
            return True
        return False

    def focus_field(self) -> None:
        self.field.setFocus(Qt.FocusReason.OtherFocusReason)

    def first_view(self) -> QWidget | None:
        """The first section on screen (where the keyboard starts)."""
        for name in SECTIONS:
            sec = self.sections[name]
            if sec.isVisibleTo(self):
                view = {"continue": self.rows["continue"].strip, "live": self.tiles,
                        "favorites": self.rows["favorites"].strip, "queue": self.queue,
                        "new": self.rows["new"].strip, "history": self.timeline}[name]
                return view
        return None

    def _tab_picked(self, key: str) -> None:
        self._show_tab(key)
        self.tab_changed.emit(key)

    def _show_tab(self, key: str) -> None:
        folders = key == "folders"
        self.overview.setVisible(not folders)
        self.folders.setVisible(folders)
        self.place.scroll_to_top()

    # -- the views' signals --------------------------------------------------------------

    def _activate(self, name: str, item) -> None:
        if not isinstance(item, Heading):
            self.activated.emit(name, item)

    def set_click_plays(self, fn: Callable[[], bool]) -> None:
        """One click plays only when *fn* says so (Settings > Playback's
        autoplay): every list here that plays, as the app's other lists -
        else a click picks and a double click or Enter plays."""
        views = [self.tiles, self.queue, self.timeline, self.folder_items]
        views += [row.strip for row in self.rows.values()]
        for v in views:
            v.click_plays = fn

    def _menu(self, name: str, item, pos: QPoint) -> None:
        if not isinstance(item, Heading):
            self.menu_requested.emit(name, item, pos)

    def _folder_activated(self, card: Card) -> None:
        if card.key == FolderTileView.NEW:
            idx = self.folder_tiles.model_.index(self.folder_tiles.row_of(card.key), 0)
            r = self.folder_tiles.visualRect(idx)
            self.folder_new.emit(self.folder_tiles.viewport().mapToGlobal(r.bottomLeft()))
            return
        self.folder_picked.emit(card)

    def _folder_menu(self, card: Card, pos: QPoint) -> None:
        if card.key != FolderTileView.NEW:
            self.folder_menu.emit(card, pos)

    def _drag_started(self, cards: list) -> None:
        self.drag_cards = list(cards)

    def _dropped_on(self, folder: Card) -> None:
        cards, self.drag_cards = self.drag_cards, []
        if cards:
            self.folder_drop.emit(cards, folder)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.BackButton:
            self.back.emit()
            e.accept()
            return
        super().mousePressEvent(e)

    def gone(self) -> bool:
        return sip.isdeleted(self)
