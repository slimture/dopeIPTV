"""Horizontal timeline EPG grid: channels as colour-coded rows, time along
the x-axis, programmes as blocks - the classic 'what's on now/next' guide.

Built on a single QGraphicsView. The time header and the channel column are
each a QGraphicsItemGroup pinned to the top / left by moving them to the
current scroll offset, so they stay in view while the programme area scrolls
under them.
"""

from __future__ import annotations

import time
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPen
from PyQt6.QtWidgets import (
    QDialog, QGraphicsItemGroup, QGraphicsRectItem, QGraphicsScene,
    QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QLabel, QLineEdit,
    QMenu, QPushButton, QVBoxLayout,
)

from ..i18n import tr
from .theme import ACCENT, P


class _GridView(QGraphicsView):
    """Hands clicks / double-clicks / right-clicks back to the dialog as
    scene positions."""

    def __init__(self, scene, dialog) -> None:
        super().__init__(scene)
        self._dlg = dialog

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.RightButton:
            self._dlg._context_at(self.mapToScene(e.pos()),
                                  e.globalPosition().toPoint())
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self._dlg._select_at(self.mapToScene(e.pos()))
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:
        self._dlg._play_at(self.mapToScene(e.pos()))
        super().mouseDoubleClickEvent(e)


class EpgGridDialog(QDialog):
    ROW_H = 48
    HEADER_H = 30
    CH_COL_W = 200
    PX_PER_MIN = 6            # 360 px per hour
    FUTURE_HOURS = 12       # how far ahead the board reaches
    MAX_PAST_HOURS = 48     # cap on how far back timeshift opens the board
    MAX_CHANNELS = 300       # a grid past this is unreadable anyway

    # Distinct, muted row accents that read on both light and dark themes.
    ROW_COLORS = [
        "#3b5ba5", "#3f7d5a", "#8a5a2b", "#7a3f6e", "#2f6f7a",
        "#8a4b4b", "#5a5a8a", "#6e7a2f", "#7a5a2f", "#4b6e8a",
        "#616a45", "#734b73",
    ]

    def __init__(self, window, channels, category_name=None) -> None:
        super().__init__(window)
        self.window = window
        self.channels = list(channels)[:self.MAX_CHANNELS]
        self._selected = None
        title = tr("btn_epg_guide")
        if category_name:
            title += f" — {category_name}"
        self.setWindowTitle(title)

        now = time.time()
        now_floor = (now // 1800) * 1800
        # Open the board further back for timeshift channels so their past
        # (catch-up-able) programmes are scrollable into view.
        max_ts_days = 0
        for ch in self.channels:
            try:
                max_ts_days = max(max_ts_days,
                                  self.window._timeshift_days(ch))
            except Exception:
                pass
        past_h = min(max(0.5, max_ts_days * 24), self.MAX_PAST_HOURS)
        self._start = now_floor - past_h * 3600
        self._stop = now_floor + self.FUTURE_HOURS * 3600
        self._grid_w = int((self._stop - self._start) / 60 * self.PX_PER_MIN)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)
        self.filter = QLineEdit(placeholderText=tr("epg_filter_channels"))
        self.filter.textChanged.connect(lambda _t: self._build())
        outer.addWidget(self.filter)

        self.scene = QGraphicsScene(self)
        self.view = _GridView(self.scene, self)
        # Anchor content top-left so a small scene isn't centred (which would
        # make mapToScene(0,0) negative and push the pinned header off-view).
        self.view.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.view.setBackgroundBrush(QColor(P["bg"]))
        # Chunky, accent-coloured scrollbars: the thin grey default blended
        # into the background and was easy to miss, and the horizontal one
        # matters here since the board is wide.
        self.view.setStyleSheet(
            "QScrollBar:horizontal{height:18px;margin:0;"
            "background:rgba(128,128,128,40);}"
            "QScrollBar:vertical{width:18px;margin:0;"
            "background:rgba(128,128,128,40);}"
            "QScrollBar::handle{background:%s;border-radius:7px;}"
            "QScrollBar::handle:horizontal{min-width:70px;}"
            "QScrollBar::handle:vertical{min-height:70px;}"
            "QScrollBar::handle:hover{background:%s;}"
            "QScrollBar::add-line,QScrollBar::sub-line{width:0;height:0;}"
            % (ACCENT, P["text"]))
        self.view.horizontalScrollBar().valueChanged.connect(self._pin)
        self.view.verticalScrollBar().valueChanged.connect(self._pin)
        outer.addWidget(self.view, 1)

        bar = QHBoxLayout()
        self.now_btn = QPushButton("⟳ " + tr("epg_jump_now"))
        self.now_btn.clicked.connect(self._scroll_to_now)
        bar.addWidget(self.now_btn)
        # Jumps the board back up to the channel you're watching (handy after
        # scrolling far down a long line-up). Only shown when one is playing.
        self.playing_btn = QPushButton("▶ " + tr("epg_jump_playing"))
        self.playing_btn.clicked.connect(self._scroll_to_playing)
        self.playing_btn.hide()
        bar.addWidget(self.playing_btn)
        self.info = QLabel(tr("epg_select_channel"))
        self.info.setStyleSheet(f"color:{P['muted']}; font-size:12px;")
        self.info.setWordWrap(True)
        bar.addWidget(self.info, 1)
        self.play_btn = QPushButton(tr("epg_play_channel"), objectName="Primary")
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self._play_selected)
        bar.addWidget(self.play_btn)
        outer.addLayout(bar)

        self._build()

        scr = self.screen().availableGeometry() if self.screen() else None
        want_w = self.CH_COL_W + self._grid_w + 40
        want_h = self.HEADER_H + len(self.channels) * self.ROW_H + 110
        w = min(want_w, (scr.width() - 80) if scr else 1200, 1500)
        h = min(want_h, (scr.height() - 80) if scr else 800, 900)
        self.resize(max(760, w), max(460, h))
        now_x = self.CH_COL_W + (now - self._start) / 60 * self.PX_PER_MIN
        self.view.horizontalScrollBar().setValue(max(0, int(now_x
                                                             - self.CH_COL_W - 40)))

    # -- geometry ------------------------------------------------------------

    def _x(self, ts: float) -> float:
        return self.CH_COL_W + (ts - self._start) / 60 * self.PX_PER_MIN

    def _scroll_to_now(self) -> None:
        """Bring the current time back into view (after scrolling far back)."""
        now_x = self._x(time.time())
        self.view.horizontalScrollBar().setValue(
            max(0, int(now_x - self.CH_COL_W - 40)))

    def _scroll_to_playing(self) -> None:
        """Scroll vertically back to the row of the channel you're watching."""
        row = getattr(self, "_playing_row", None)
        if row is None:
            return
        y = self.HEADER_H + row * self.ROW_H
        self.view.verticalScrollBar().setValue(
            max(0, int(y - self.HEADER_H - self.ROW_H)))

    # -- build ---------------------------------------------------------------

    def _build(self) -> None:
        self.scene.clear()
        self._selected = None
        self._sel_outline = None   # dropped by scene.clear(); recreated on pick
        self._playing_row = None
        self.play_btn.setEnabled(False)
        text = self.filter.text().lower().strip()
        chans = [c for c in self.channels
                 if not text or text in (c.get("name") or "").lower()]
        grid_h = max(self.ROW_H, len(chans) * self.ROW_H)
        self.scene.setSceneRect(0, 0, self.CH_COL_W + self._grid_w,
                                self.HEADER_H + grid_h)

        # Pinned groups (recreated each build - scene.clear() drops them).
        self._header_group = QGraphicsItemGroup()
        self._header_group.setZValue(20)
        self._chan_group = QGraphicsItemGroup()
        self._chan_group.setZValue(15)
        self.scene.addItem(self._header_group)
        self.scene.addItem(self._chan_group)

        self._draw_grid_lines(grid_h)
        self._draw_time_header()
        for row, ch in enumerate(chans):
            self._draw_channel_row(row, ch)
        self._draw_now_line(grid_h)

        head_bg = QColor(P["pane"])
        self._corner = self.scene.addRect(
            0, 0, self.CH_COL_W, self.HEADER_H,
            QPen(QColor(P["border"])), QBrush(head_bg))
        self._corner.setZValue(30)
        self._pin()
        if hasattr(self, "playing_btn"):
            self.playing_btn.setVisible(self._playing_row is not None)

    def _draw_grid_lines(self, grid_h: int) -> None:
        t = self._start
        while t <= self._stop:
            x = self._x(t)
            on_hour = int(t) % 3600 == 0
            line = self.scene.addLine(
                x, 0, x, self.HEADER_H + grid_h,
                QPen(QColor(P["border"]), 1 if on_hour else 0))
            line.setZValue(1)
            t += 1800

    def _draw_time_header(self) -> None:
        band = QGraphicsRectItem(0, 0, self.CH_COL_W + self._grid_w,
                                 self.HEADER_H)
        band.setBrush(QBrush(QColor(P["pane"])))
        band.setPen(QPen(Qt.PenStyle.NoPen))
        self._header_group.addToGroup(band)
        t = self._start
        while t <= self._stop:
            lbl = QGraphicsSimpleTextItem(
                datetime.fromtimestamp(t).strftime("%H:%M"))
            lbl.setBrush(QColor(P["text"]))
            lbl.setPos(self._x(t) + 4, 7)
            self._header_group.addToGroup(lbl)
            t += 3600

    def _is_playing(self, ch: dict) -> bool:
        """True if this channel is the one currently playing live, so the row
        can be flagged in the guide."""
        w = self.window
        if getattr(w, "_playing_group", None) != "live":
            return False
        player = getattr(w, "player", None)
        if not player or not player.isVisible():
            return False
        try:
            key = w._item_key(ch)
        except Exception:
            return False
        return key is not None and key == getattr(w, "_playing_key", None)

    def _draw_channel_row(self, row: int, ch: dict) -> None:
        y = self.HEADER_H + row * self.ROW_H
        base = QColor(self.ROW_COLORS[row % len(self.ROW_COLORS)])
        playing = self._is_playing(ch)
        if playing:
            self._playing_row = row
        try:
            has_ts = self.window._timeshift_days(ch) > 0
        except Exception:
            has_ts = False
        cell = QGraphicsRectItem(0, y, self.CH_COL_W, self.ROW_H)
        if playing:
            cell.setBrush(QBrush(QColor(ACCENT)))
            cell.setPen(QPen(QColor("#ffffff"), 2))
        else:
            cell.setBrush(QBrush(base.darker(120)))
            cell.setPen(QPen(QColor(P["bg"]), 1))
        self._chan_group.addToGroup(cell)
        # ⏪ marks a channel with catch-up (timeshift), matching the main list.
        label_text = (("▶  " if playing else "")
                      + ("⏪ " if has_ts else "")
                      + (ch.get("name") or "?"))
        name = QGraphicsSimpleTextItem(label_text)
        f = QFont()
        f.setBold(True)
        f.setPointSize(10)
        name.setFont(f)
        name.setBrush(QColor("#ffffff"))
        self._elide(name, self.CH_COL_W - 16)
        name.setPos(8, y + (self.ROW_H - name.boundingRect().height()) / 2)
        self._chan_group.addToGroup(name)
        if playing:
            # Tint the whole row across the timeline so the playing channel
            # is obvious even when the programme area is what you're looking
            # at. Sits above the blocks but carries no data(0), so it never
            # intercepts a click (hit-testing reads the block beneath it).
            band = QGraphicsRectItem(self.CH_COL_W, y, self._grid_w, self.ROW_H)
            glow = QColor(ACCENT)
            glow.setAlpha(48)
            band.setBrush(QBrush(glow))
            band.setPen(QPen(Qt.PenStyle.NoPen))
            band.setZValue(6)
            band.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.scene.addItem(band)

        for i, p in enumerate(
                self.window.xmltv.programmes_in(ch, self._start, self._stop)):
            x1 = max(self.CH_COL_W, self._x(p["start_timestamp"]))
            x2 = min(self.CH_COL_W + self._grid_w, self._x(p["stop_timestamp"]))
            if x2 - x1 < 2:
                continue
            shade = base.lighter(120) if i % 2 == 0 else base.lighter(102)
            block = QGraphicsRectItem(x1 + 1, y + 2, x2 - x1 - 2, self.ROW_H - 4)
            block.setBrush(QBrush(shade))
            block.setPen(QPen(QColor(P["bg"]), 1))
            block.setZValue(5)
            block.setData(0, {"channel": ch, "prog": p})
            self.scene.addItem(block)
            label = QGraphicsSimpleTextItem(p.get("title") or "?", block)
            label.setBrush(QColor("#ffffff"))
            self._elide(label, x2 - x1 - 10)
            label.setPos(x1 + 5, y + 7)
            label.setData(0, {"channel": ch, "prog": p})

    def _draw_now_line(self, grid_h: int) -> None:
        x = self._x(time.time())
        line = self.scene.addLine(x, 0, x, self.HEADER_H + grid_h,
                                  QPen(QColor("#e5354b"), 2))
        line.setZValue(19)

    @staticmethod
    def _elide(item: QGraphicsSimpleTextItem, max_w: float) -> None:
        if item.boundingRect().width() <= max_w:
            return
        text = item.text()
        while text and item.boundingRect().width() > max_w:
            text = text[:-1]
            item.setText(text + "…")

    # -- sticky header / column ---------------------------------------------

    def _pin(self, *_a) -> None:
        tl = self.view.mapToScene(0, 0)
        if hasattr(self, "_header_group"):
            self._header_group.setY(tl.y())
            self._chan_group.setX(tl.x())
            self._corner.setPos(tl.x(), tl.y())

    # -- interaction ---------------------------------------------------------

    def _block_at(self, scene_pos):
        for it in self.scene.items(scene_pos):
            data = it.data(0)
            if isinstance(data, dict) and "channel" in data:
                return data
        return None

    def _highlight_block(self, scene_pos) -> None:
        """Outline the picked programme block so it's clear which one is
        selected (and which one 'Play this programme' will act on)."""
        block = None
        for it in self.scene.items(scene_pos):
            d = it.data(0)
            if (isinstance(d, dict) and "channel" in d
                    and isinstance(it, QGraphicsRectItem)):
                block = it
                break
        if block is None:
            return
        if getattr(self, "_sel_outline", None) is None \
                or self._sel_outline.scene() is None:
            self._sel_outline = self.scene.addRect(
                block.rect(), QPen(QColor("#ffffff"), 2),
                QBrush(Qt.BrushStyle.NoBrush))
            self._sel_outline.setZValue(17)
            self._sel_outline.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        else:
            self._sel_outline.setRect(block.rect())
        self._sel_outline.show()

    def _select_at(self, scene_pos) -> None:
        data = self._block_at(scene_pos)
        if not data:
            return
        self._selected = data
        self._highlight_block(scene_pos)
        p = data["prog"]
        start = datetime.fromtimestamp(p["start_timestamp"]).strftime("%H:%M")
        stop = datetime.fromtimestamp(p["stop_timestamp"]).strftime("%H:%M")
        catchup = (p["stop_timestamp"] < time.time()
                   and self.window._timeshift_days(data["channel"]))
        tag = "  ⟲" if catchup else ""
        self.info.setText(
            f"{data['channel'].get('name') or '?'} · "
            f"{start}–{stop}  {p.get('title') or ''}{tag}")
        self.play_btn.setEnabled(True)

    def _play_at(self, scene_pos) -> None:
        data = self._block_at(scene_pos)
        if data:
            self._selected = data
            self._play_selected()

    def _play_selected(self) -> None:
        if not self._selected:
            return
        ch, p = self._selected["channel"], self._selected["prog"]
        # A finished programme on a timeshift channel plays as catch-up;
        # everything else tunes the channel live.
        if p["stop_timestamp"] < time.time() and self.window._timeshift_days(ch):
            self.window._play_timeshift(ch, prog=p)
        else:
            self.window.play_live_channel(ch)
        self.accept()

    def _context_at(self, scene_pos, global_pos) -> None:
        data = self._block_at(scene_pos)
        if not data:
            return
        ch, p = data["channel"], data["prog"]
        self._selected = data
        self.play_btn.setEnabled(True)
        m = QMenu(self)
        past = p["stop_timestamp"] < time.time()
        if past and self.window._timeshift_days(ch):
            m.addAction(tr("epg_play_this_programme"), self._play_selected)
        m.addAction(tr("ts_go_live") if past else tr("epg_play_channel"),
                    lambda: (self.window.play_live_channel(ch), self.accept()))
        if ch.get("stream_id") is not None and p["stop_timestamp"] > time.time():
            m.addAction(tr("rec_record_programme"), lambda: self._record(ch, p))
        m.exec(global_pos)

    def _record(self, ch: dict, p: dict) -> None:
        if not self.window._within_storage_cap():
            return
        url = self.window.client.live_url(ch.get("stream_id"), "ts")
        if not url:
            return
        self.window.rec.add_job(
            url, ch.get("name") or p.get("title") or "recording",
            max(time.time(), p["start_timestamp"]), p["stop_timestamp"])
        self.info.setText("● " + (p.get("title") or ch.get("name") or ""))
