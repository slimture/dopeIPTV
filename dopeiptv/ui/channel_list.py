"""Virtualized channel list: model, view, and custom-painted delegate."""

from __future__ import annotations

from PyQt6.QtCore import QAbstractListModel, QModelIndex, QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QListView, QStyle, QStyledItemDelegate,
)

from .theme import ACCENT, P


class CategoryColorDelegate(QStyledItemDelegate):
    """Paints a category/folder row's custom background. The list's stylesheet
    makes a plain QListWidgetItem.setBackground invisible, so fill it here
    (under the normal text painting) for non-selected rows."""

    def paint(self, painter, option, index) -> None:
        bg = index.data(Qt.ItemDataRole.BackgroundRole)
        if (bg is not None
                and not (option.state & QStyle.StateFlag.State_Selected)):
            painter.save()
            painter.fillRect(option.rect, bg)
            painter.restore()
        super().paint(painter, option, index)


class ChannelListView(QListView):
    """Right-click never moves the selection (avoids switching the preview).
    In grid mode the columns are stretched to fill the viewport width so the
    last column sits against the right edge instead of leaving a ragged gap."""

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self._grid_cell: QSize | None = None
        # Kill IconMode's default 4-6 px padding around every item and any
        # frame inset - our justified slots already fill the viewport, so any
        # extra spacing just adds a visible gap on the right.
        self.setSpacing(0)
        self.setContentsMargins(0, 0, 0, 0)
        self.setViewportMargins(0, 0, 0, 0)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.RightButton:
            e.accept()
            return
        super().mousePressEvent(e)

    def set_grid_cell(self, cell: "QSize | None") -> None:
        """Remember the delegate's base grid cell (or None for list mode) and
        re-justify. Called whenever the view mode / density changes."""
        self._grid_cell = cell
        self._justify_grid()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._justify_grid()

    def _justify_grid(self) -> None:
        cell = self._grid_cell
        if cell is None or cell.width() <= 0:
            return
        # setViewportMargins fires a resizeEvent that would re-enter here;
        # guard against that so we settle on the intended layout in one pass.
        if getattr(self, "_justifying", False):
            return
        self._justifying = True
        try:
            self._do_justify(cell)
        finally:
            self._justifying = False

    def _do_justify(self, cell) -> None:
        # Total width available to the grid = current viewport + any margins
        # we set on a previous pass. This gives us a stable frame of reference
        # even if Qt hasn't fully rebuilt the layout between our margin edits.
        m = self.viewportMargins()
        raw_vw = self.viewport().width() + m.left() + m.right()
        if raw_vw <= cell.width():
            return
        # Find the largest N of columns that will ACTUALLY render N (not one
        # fewer) after we apply the balancing left shift. Qt's IconMode
        # reserves ~2*N+12 px on the right for its wrap check; the shift eats
        # another 14 px + 2*qt_reserve of headroom. Combining those the
        # constraint reduces to:
        #     slot ≤ (raw_vw - 14 - 2*qt_reserve) / N
        # so we walk N down from the naive maximum until a natural cell fits.
        # Without this the previous version predicted N cols and Qt silently
        # dropped one, leaving the huge right gap the user reported at 2-3
        # cols despite the balancing working fine at 4+ cols.
        cell_w = cell.width()
        slot_w = raw_vw
        cols = 1
        for candidate in range(raw_vw // cell_w, 1, -1):
            qt_reserve = 2 * candidate + 12
            max_slot = (raw_vw - 14 - 2 * qt_reserve) // candidate
            if max_slot >= cell_w:
                cols = candidate
                slot_w = max_slot
                break
        # 1-column fallback: stretch the slot to the full viewport so the icon
        # is naturally centred with no explicit shift.
        if cols == 1:
            slot_w = raw_vw
        self.setGridSize(QSize(slot_w, cell.height()))
        leftover = raw_vw - cols * slot_w
        # The +7 fixed offset compensates for Qt's ~14 px vertical-scrollbar
        # reservation on the right of the viewport - even when the scrollbar
        # isn't showing, that space is set aside and would otherwise appear as
        # asymmetric right padding. Same constant applies at every column count.
        left_pad = (leftover // 2 + 7)
        self.setViewportMargins(left_pad, 0, 0, 0)


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

    def flags(self, index):
        # Section headers are not content: not selectable, not clickable.
        it = self.item_at(index.row())
        if it and it.get("_header"):
            return Qt.ItemFlag.NoItemFlags
        return super().flags(index)

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
        "xlarge":  (130, 90, 15, 11),   # readable from the couch on a TV.
    }
    GRID = {
        "compact": (100, 108, 68, 9),
        "medium":  (130, 140, 96, 10),
        "large":   (170, 182, 132, 11),
        "xlarge":  (240, 260, 200, 13),  # ~200 px icon, big-screen friendly.
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

    def _header_h(self) -> int:
        # Scale the section header with the density so it stays legible next
        # to Extra-Large rows, but keep the band tight - it used to reserve a
        # lot of empty space above/below the text between sections.
        return min(max(22, self.name_pt * 2), 34)

    def sizeHint(self, option, index) -> QSize:
        it = index.data(Qt.ItemDataRole.UserRole)
        if it and it.get("_header"):
            # Headers only appear in list mode (combined views force it),
            # where they are a full-width row.
            return QSize(0, self._header_h())
        if self.grid:
            return QSize(self.cell_w, self.cell_h)
        return QSize(0, self.row_h)

    def paint(self, painter, option, index) -> None:
        it = index.data(Qt.ItemDataRole.UserRole)
        if it and it.get("_header"):
            self._paint_header(painter, option, it["_header"])
        elif self.grid:
            self._paint_grid(painter, option, index)
        else:
            self._paint_list(painter, option, index)

    def _paint_header(self, painter, option, text: str) -> None:
        painter.save()
        f = QFont()
        f.setBold(True)
        f.setPointSize(self.name_pt + 3)   # scales with the density
        painter.setFont(f)
        painter.setPen(QColor(P["text"]))
        # Bottom-align so the label hugs the item right below it - that reads
        # as "this header belongs to the section under it" and keeps the first
        # poster/row close to its heading, with the breathing room above.
        r = option.rect.adjusted(14, 0, -12, -2)
        painter.drawText(
            r, int(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft),
            (text or "").upper())
        painter.restore()

    def _paint_watched_badge(self, painter, anchor: QRect,
                             size: int, source: str = "local") -> None:
        """Small circle with a white check in the top-right corner of
        *anchor* - the 'you've already seen this' marker. Coloured by
        source: the accent colour for a local-only mark, Trakt red when
        the mark is known to Trakt, so the two read distinctly. Painted
        after the logo/poster so it's not clipped by the rounded-rect
        path used for the artwork."""
        pad = 3
        x = anchor.right() - size - pad
        y = anchor.top() + pad
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ed1c24" if source == "trakt" else ACCENT))
        painter.drawEllipse(x, y, size, size)
        # Check mark: two short strokes inside the circle.
        pen = QPen(QColor("#ffffff"))
        pen.setWidth(max(2, size // 6))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        cx, cy = x + size / 2, y + size / 2
        path = QPainterPath()
        path.moveTo(cx - size * 0.22, cy + size * 0.02)
        path.lineTo(cx - size * 0.05, cy + size * 0.20)
        path.lineTo(cx + size * 0.24, cy - size * 0.18)
        painter.drawPath(path)
        painter.restore()

    def _paint_watchlist_badge(self, painter, anchor: QRect,
                               size: int) -> None:
        """Small clock badge in the top-LEFT corner of *anchor* - the
        'on your Watch Later list' marker. Left corner so it never
        collides with the top-right watched check, and a blue disc so
        the two markers read as distinct at a glance."""
        pad = 3
        x = anchor.left() + pad
        y = anchor.top() + pad
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#2f80ed"))
        painter.drawEllipse(x, y, size, size)
        pen = QPen(QColor("#ffffff"))
        pen.setWidth(max(2, size // 7))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        cx, cy = x + size / 2.0, y + size / 2.0
        # Clock hands: one up (12 o'clock), one to the right (~4 o'clock).
        path = QPainterPath()
        path.moveTo(cx, cy)
        path.lineTo(cx, cy - size * 0.26)
        path.moveTo(cx, cy)
        path.lineTo(cx + size * 0.20, cy + size * 0.12)
        painter.drawPath(path)
        painter.restore()

    def _paint_fav_star(self, painter, anchor: QRect, size: int) -> None:
        """Gold star in the bottom-right corner of *anchor* - the same
        favourite marker channels use, now on movie/series posters too.
        Bottom-right so it clears the watched (top-right) and Watch Later
        (top-left) badges. A dark disc behind it keeps it readable on
        light posters."""
        pad = 3
        x = anchor.right() - size - pad
        y = anchor.bottom() - size - pad
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 130))
        painter.drawEllipse(x, y, size, size)
        painter.setPen(QColor("#FFD700"))
        f = QFont()
        f.setPointSize(max(9, int(size * 0.62)))
        painter.setFont(f)
        painter.drawText(QRect(x, y, size, size),
                         Qt.AlignmentFlag.AlignCenter, "★")
        painter.restore()

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
        kind = it.get("_ekind") or index.model().kind
        rect = option.rect
        logo_sz = self.grid_logo
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        playing = self._is_playing(it, kind)
        tint_fg, tint_bg = self.window.item_tint(it, kind)
        inner = rect.adjusted(3, 3, -3, -3)
        if tint_bg:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tint_bg))
            painter.drawRoundedRect(inner, 12, 12)
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
        logo_y = rect.top() + 8
        logo_rect = QRect(logo_x, logo_y, logo_sz, logo_sz)
        radius = max(8, logo_sz // 5)
        # cover_url picks TMDB (title search), then a TMDB path
        # extracted from the provider's own icon URL, then the raw
        # provider URL - first that isn't blacklisted. is_dead()
        # covers both the single-URL blacklist and the per-host
        # circuit breaker.
        url = self.window.cover.cover_url(it, kind)
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
            # Fetch decision lives on the window: TMDB URLs go now,
            # the raw provider URL waits until the title lookup has
            # answered so pending rows don't burn requests on art
            # that's about to be replaced.
            if self.window.cover.should_fetch(url, it, kind):
                self.window.logos.get(
                    url,
                    lambda _pm: self.window.listw.viewport().update())

        wsrc = self.window.watched_source(it, kind)
        if wsrc:
            self._paint_watched_badge(
                painter, logo_rect, max(18, logo_sz // 5), wsrc)
        if self.window.is_item_on_watchlist(it, kind):
            self._paint_watchlist_badge(
                painter, logo_rect, max(18, logo_sz // 5))
        if self.window.is_favorite_item(it, kind):
            self._paint_fav_star(painter, logo_rect, max(18, logo_sz // 5))

        painter.setPen(
            QColor(ACCENT) if playing
            else QColor(tint_fg) if tint_fg else QColor(P["text"]))
        fname = QFont()
        fname.setPointSize(self.grid_name_pt)
        fname.setBold(True)
        painter.setFont(fname)
        text_rect = QRect(
            rect.left() + 4, logo_y + logo_sz + 4,
            rect.width() - 8,
            rect.bottom() - (logo_y + logo_sz + 4))
        fm = painter.fontMetrics()
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            fm.elidedText(
                name, Qt.TextElideMode.ElideRight, text_rect.width()))
        painter.restore()

    def _paint_list(self, painter, option, index) -> None:
        it = index.data(Qt.ItemDataRole.UserRole) or {}
        kind = it.get("_ekind") or index.model().kind
        rect = option.rect
        logo_sz = self.logo_sz
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        playing = self._is_playing(it, kind)
        tint_fg, tint_bg = self.window.item_tint(it, kind)

        if tint_bg:
            painter.fillRect(rect, QColor(tint_bg))
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
        # cover_url picks TMDB (title search), then a TMDB path
        # extracted from the provider's own icon URL, then the raw
        # provider URL - first that isn't blacklisted. is_dead()
        # covers both the single-URL blacklist and the per-host
        # circuit breaker.
        url = self.window.cover.cover_url(it, kind)
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
            # Fetch decision lives on the window: TMDB URLs go now,
            # the raw provider URL waits until the title lookup has
            # answered so pending rows don't burn requests on art
            # that's about to be replaced.
            if self.window.cover.should_fetch(url, it, kind):
                self.window.logos.get(
                    url,
                    lambda _pm: self.window.listw.viewport().update())

        wsrc = self.window.watched_source(it, kind)
        if wsrc:
            self._paint_watched_badge(
                painter, logo_rect, max(16, logo_sz // 4), wsrc)
        if self.window.is_item_on_watchlist(it, kind):
            self._paint_watchlist_badge(
                painter, logo_rect, max(16, logo_sz // 4))

        num_w = 0
        is_fav = self.window.is_favorite_item(it, kind)
        if kind in ("live", "fav") and it.get("num"):
            has_archive = self.window._timeshift_days(it) > 0
            num_w = 52 if has_archive else 34
            if is_fav:
                num_w += 18
            painter.setPen(QColor(P["muted3"]))
            fnum = QFont()
            fnum.setPointSize(10)
            painter.setFont(fnum)
            num_rect = QRect(
                rect.right() - 12 - num_w, rect.top(),
                num_w, rect.height())
            suffix = str(it["num"])
            if has_archive:
                suffix = "⏪ " + suffix
            painter.drawText(
                num_rect,
                Qt.AlignmentFlag.AlignVCenter
                | Qt.AlignmentFlag.AlignRight, suffix)
            if is_fav:
                painter.setPen(QColor("#FFD700"))
                fstar = QFont()
                fstar.setPointSize(12)
                painter.setFont(fstar)
                star_rect = QRect(
                    num_rect.left() - 16, rect.top(), 16, rect.height())
                painter.drawText(
                    star_rect,
                    Qt.AlignmentFlag.AlignVCenter
                    | Qt.AlignmentFlag.AlignRight, "★")
        elif is_fav:
            num_w = 22
            painter.setPen(QColor("#FFD700"))
            fnum = QFont()
            fnum.setPointSize(12)
            painter.setFont(fnum)
            num_rect = QRect(
                rect.right() - 12 - num_w, rect.top(),
                num_w, rect.height())
            painter.drawText(
                num_rect,
                Qt.AlignmentFlag.AlignVCenter
                | Qt.AlignmentFlag.AlignRight, "★")

        text_x = logo_rect.right() + 12
        text_w = max(0, rect.right() - 12 - num_w - text_x)
        now = (self.window.xmltv.now_for(it)
               if kind in ("live", "fav") else None)

        resume_pct = it.get("_progress_pct")
        name_h = self.name_pt + 8
        sub_h = (self.sub_pt + 6) if now else 0
        bar_h = 6 if (now or resume_pct is not None) else 0
        block_h = name_h + sub_h + bar_h
        y = rect.top() + (rect.height() - block_h) // 2

        painter.setPen(
            QColor(ACCENT) if playing
            else QColor(tint_fg) if tint_fg else QColor(P["text"]))
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
        elif resume_pct is not None:
            # Continue-watching resume progress (movies have no EPG 'now').
            bar_rect = QRect(text_x, y + name_h, text_w, 4)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#2A2A32"))
            painter.drawRoundedRect(bar_rect, 2, 2)
            fill_w = int(bar_rect.width() * max(0, min(100, resume_pct)) / 100)
            if fill_w > 0:
                painter.setBrush(QColor(ACCENT))
                painter.drawRoundedRect(
                    QRect(bar_rect.x(), bar_rect.y(),
                          fill_w, bar_rect.height()), 2, 2)
        painter.restore()
