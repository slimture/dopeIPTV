"""Virtualized channel list: model, view, and custom-painted delegate."""

from __future__ import annotations

from PyQt6.QtCore import QAbstractListModel, QModelIndex, QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QListView, QSizePolicy, QStyle, QStyledItemDelegate,
)

from .theme import ACCENT, P


class ChannelListView(QListView):
    """Right-click never moves the selection (avoids switching the preview)."""

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.RightButton:
            e.accept()
            return
        super().mousePressEvent(e)


class ChannelListModel(QAbstractListModel):
    """Flat data model for the channel/movie/episode list."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: list = []
        self.kind: str = "live"

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._items)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._items)):
            return None
        it = self._items[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return it
        if role == Qt.ItemDataRole.DisplayRole:
            return it.get("name") or it.get("title") or "?"
        return None

    def set_items(self, items: list, kind: str) -> None:
        self.beginResetModel()
        self._items = items
        self.kind = kind
        self.endResetModel()

    def item_at(self, row: int):
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def refresh_all(self) -> None:
        if self._items:
            self.dataChanged.emit(
                self.index(0), self.index(len(self._items) - 1))


class ChannelDelegate(QStyledItemDelegate):
    """Custom-painted delegate for list and grid modes at three densities."""

    DENSITIES = {
        "compact": (50, 32, 10, 8),
        "medium":  (66, 44, 11, 9),
        "large":   (92, 64, 13, 10),
    }
    GRID = {
        "compact": (108, 116, 60, 9),
        "medium":  (140, 150, 84, 10),
        "large":   (184, 196, 120, 11),
    }

    def __init__(self, window, density: str = "medium",
                 grid: bool = False) -> None:
        super().__init__(window)
        self.window = window
        self.grid = grid
        self.set_density(density)

    def set_density(self, level: str) -> None:
        self.level = level if level in self.DENSITIES else "medium"
        self.row_h, self.logo_sz, self.name_pt, self.sub_pt = \
            self.DENSITIES[self.level]
        self.cell_w, self.cell_h, self.grid_logo, self.grid_name_pt = \
            self.GRID[self.level]

    def set_grid(self, grid: bool) -> None:
        self.grid = grid

    def grid_size(self) -> QSize:
        return QSize(self.cell_w, self.cell_h)

    def sizeHint(self, option, index) -> QSize:
        if self.grid:
            return QSize(self.cell_w, self.cell_h)
        return QSize(0, self.row_h)

    def paint(self, painter, option, index) -> None:
        if self.grid:
            self._paint_grid(painter, option, index)
        else:
            self._paint_list(painter, option, index)

    def _is_playing(self, it, kind: str) -> bool:
        group = {"live": "live", "fav": "live", "vod": "vod",
                 "episode": "episode", "history": "history",
                 "rec": "rec"}.get(kind)
        w = self.window
        if w._playing_key is None:
            return False
        if kind == "history":
            return (it.get("_key") == w._playing_key
                    and {"live": "live", "movie": "vod",
                         "episode": "episode"}.get(it.get("_kind"))
                    == w._playing_group)
        return (group == w._playing_group
                and w._item_key(it) == w._playing_key)

    def _paint_grid(self, painter, option, index) -> None:
        it = index.data(Qt.ItemDataRole.UserRole) or {}
        kind = index.model().kind
        rect = option.rect
        logo_sz = self.grid_logo
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        playing = self._is_playing(it, kind)
        inner = rect.adjusted(5, 5, -5, -5)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(P["sel"]))
            painter.drawRoundedRect(inner, 12, 12)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(P["hover"]))
            painter.drawRoundedRect(inner, 12, 12)
        if playing:
            pen = QPen(QColor(ACCENT))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(inner, 12, 12)

        name = self.window.channel_display_name(it)
        logo_x = rect.left() + (rect.width() - logo_sz) // 2
        logo_y = rect.top() + 12
        logo_rect = QRect(logo_x, logo_y, logo_sz, logo_sz)
        radius = max(8, logo_sz // 5)
        url = it.get("stream_icon") or it.get("cover")
        pm = self.window.logos.cache.get(url) if url else None
        if pm:
            path = QPainterPath()
            path.addRoundedRect(QRectF(logo_rect), radius, radius)
            painter.setClipPath(path)
            scaled = pm.scaled(
                logo_sz, logo_sz,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap(
                logo_x + (logo_sz - scaled.width()) // 2,
                logo_y + (logo_sz - scaled.height()) // 2, scaled)
            painter.setClipping(False)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(P["sel"]))
            painter.drawRoundedRect(logo_rect, radius, radius)
            painter.setPen(QColor(P["text"]))
            f = QFont()
            f.setPointSize(max(14, logo_sz // 3))
            f.setBold(True)
            painter.setFont(f)
            painter.drawText(logo_rect, Qt.AlignmentFlag.AlignCenter,
                             name.strip()[:1].upper())
            if url and url not in self.window.logos.waiting:
                self.window.logos.get(
                    url,
                    lambda _pm: self.window.listw.viewport().update())

        painter.setPen(
            QColor(ACCENT) if playing else QColor(P["text"]))
        fname = QFont()
        fname.setPointSize(self.grid_name_pt)
        fname.setBold(True)
        painter.setFont(fname)
        text_rect = QRect(
            rect.left() + 4, logo_y + logo_sz + 6,
            rect.width() - 8,
            rect.bottom() - (logo_y + logo_sz + 6))
        fm = painter.fontMetrics()
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            fm.elidedText(
                name, Qt.TextElideMode.ElideRight, text_rect.width()))
        painter.restore()

    def _paint_list(self, painter, option, index) -> None:
        it = index.data(Qt.ItemDataRole.UserRole) or {}
        kind = index.model().kind
        rect = option.rect
        logo_sz = self.logo_sz
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        playing = self._is_playing(it, kind)

        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, QColor(P["sel"]))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(rect, QColor(P["hover"]))
        if playing:
            painter.fillRect(
                QRect(rect.left(), rect.top() + 4, 3, rect.height() - 8),
                QColor(ACCENT))

        name = self.window.channel_display_name(it)
        logo_rect = QRect(
            rect.left() + 10,
            rect.top() + (rect.height() - logo_sz) // 2,
            logo_sz, logo_sz)
        radius = max(6, logo_sz // 4)
        url = it.get("stream_icon") or it.get("cover")
        pm = self.window.logos.cache.get(url) if url else None
        if pm:
            path = QPainterPath()
            path.addRoundedRect(QRectF(logo_rect), radius, radius)
            painter.setClipPath(path)
            scaled = pm.scaled(
                logo_sz, logo_sz,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            x = logo_rect.x() + (logo_sz - scaled.width()) // 2
            y = logo_rect.y() + (logo_sz - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
            painter.setClipping(False)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(P["sel"]))
            painter.drawRoundedRect(logo_rect, radius, radius)
            painter.setPen(QColor(P["text"]))
            f = QFont()
            f.setPointSize(max(12, logo_sz // 3))
            f.setBold(True)
            painter.setFont(f)
            painter.drawText(logo_rect, Qt.AlignmentFlag.AlignCenter,
                             name.strip()[:1].upper())
            if url and url not in self.window.logos.waiting:
                self.window.logos.get(
                    url,
                    lambda _pm: self.window.listw.viewport().update())

        num_w = 0
        if kind in ("live", "fav") and it.get("num"):
            has_archive = self.window._timeshift_days(it) > 0
            num_w = 52 if has_archive else 34
            painter.setPen(QColor(P["muted3"]))
            fnum = QFont()
            fnum.setPointSize(10)
            painter.setFont(fnum)
            num_rect = QRect(
                rect.right() - 12 - num_w, rect.top(),
                num_w, rect.height())
            painter.drawText(
                num_rect,
                Qt.AlignmentFlag.AlignVCenter
                | Qt.AlignmentFlag.AlignRight,
                ("⏪ " if has_archive else "") + str(it["num"]))

        text_x = logo_rect.right() + 12
        text_w = max(0, rect.right() - 12 - num_w - text_x)
        now = (self.window.xmltv.now_for(it)
               if kind in ("live", "fav") else None)

        name_h = self.name_pt + 8
        sub_h = (self.sub_pt + 6) if now else 0
        bar_h = 6 if now else 0
        block_h = name_h + sub_h + bar_h
        y = rect.top() + (rect.height() - block_h) // 2

        painter.setPen(
            QColor(ACCENT) if playing else QColor(P["text"]))
        fname = QFont()
        fname.setPointSize(self.name_pt)
        fname.setBold(True)
        painter.setFont(fname)
        fm = painter.fontMetrics()
        painter.drawText(
            QRect(text_x, y, text_w, name_h),
            Qt.AlignmentFlag.AlignVCenter,
            fm.elidedText(
                name, Qt.TextElideMode.ElideRight, text_w))

        if now:
            title, pct = now
            painter.setPen(QColor(P["muted"]))
            fsub = QFont()
            fsub.setPointSize(self.sub_pt)
            painter.setFont(fsub)
            fm2 = painter.fontMetrics()
            painter.drawText(
                QRect(text_x, y + name_h, text_w, sub_h),
                Qt.AlignmentFlag.AlignVCenter,
                fm2.elidedText(
                    "Now: " + title,
                    Qt.TextElideMode.ElideRight, text_w))
            bar_rect = QRect(text_x, y + name_h + sub_h, text_w, 4)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#2A2A32"))
            painter.drawRoundedRect(bar_rect, 2, 2)
            fill_w = int(
                bar_rect.width() * max(0, min(100, pct)) / 100)
            if fill_w > 0:
                painter.setBrush(QColor(ACCENT))
                painter.drawRoundedRect(
                    QRect(bar_rect.x(), bar_rect.y(),
                          fill_w, bar_rect.height()), 2, 2)
        painter.restore()
