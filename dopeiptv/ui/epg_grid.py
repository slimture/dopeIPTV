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

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap,
)
from PyQt6.QtWidgets import (
    QDialog, QGraphicsItemGroup, QGraphicsPixmapItem, QGraphicsRectItem,
    QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout,
    QLabel, QLineEdit, QMenu, QPushButton, QVBoxLayout,
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

    def keyPressEvent(self, e) -> None:
        # TV-style cell navigation: arrows move the selection between
        # programme blocks instead of just scrolling the view.
        k = e.key()
        if k == Qt.Key.Key_Left:
            self._dlg._nav(-1, 0)
        elif k == Qt.Key.Key_Right:
            self._dlg._nav(1, 0)
        elif k == Qt.Key.Key_Up:
            self._dlg._nav(0, -1)
        elif k == Qt.Key.Key_Down:
            self._dlg._nav(0, 1)
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._dlg._play_selected()
        else:
            super().keyPressEvent(e)
            return
        e.accept()


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
        self.view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        outer.addWidget(self.view, 1)

        # Programme description panel: fed from the XMLTV desc of whatever
        # block is selected - the data was always there, just never shown.
        self.desc = QLabel("")
        self.desc.setWordWrap(True)
        self.desc.setStyleSheet(f"color:{P['muted']}; font-size:12px;")
        self.desc.setMaximumHeight(56)
        self.desc.hide()
        outer.addWidget(self.desc)

        bar = QHBoxLayout()
        self.search_btn = QPushButton("🔍 " + tr("epg_search_btn"))
        self.search_btn.clicked.connect(self._open_search)
        bar.addWidget(self.search_btn)
        self.day_back_btn = QPushButton()
        self.day_back_btn.setIcon(self._tri_icon(left=True))
        self.day_back_btn.setToolTip(tr("epg_day_back"))
        self.day_back_btn.setFixedWidth(34)
        self.day_back_btn.clicked.connect(lambda: self._scroll_hours(-24))
        bar.addWidget(self.day_back_btn)
        self.now_btn = QPushButton(tr("epg_jump_now"))
        self.now_btn.clicked.connect(self._scroll_to_now)
        bar.addWidget(self.now_btn)
        self.tonight_btn = QPushButton(tr("epg_tonight"))
        self.tonight_btn.clicked.connect(self._scroll_tonight)
        bar.addWidget(self.tonight_btn)
        self.day_fwd_btn = QPushButton()
        self.day_fwd_btn.setIcon(self._tri_icon(left=False))
        self.day_fwd_btn.setToolTip(tr("epg_day_fwd"))
        self.day_fwd_btn.setFixedWidth(34)
        self.day_fwd_btn.clicked.connect(lambda: self._scroll_hours(24))
        bar.addWidget(self.day_fwd_btn)
        self.reminders_btn = QPushButton("🔔 " + tr("reminders_title"))
        self.reminders_btn.clicked.connect(self.window._open_reminders)
        bar.addWidget(self.reminders_btn)
        # Jumps the board back up to the channel you're watching (handy after
        # scrolling far down a long line-up). Only shown when one is playing.
        self.playing_btn = QPushButton(tr("epg_jump_playing"))
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
        self.close_btn = QPushButton(tr("common_close"))
        self.close_btn.clicked.connect(self.reject)
        bar.addWidget(self.close_btn)
        outer.addLayout(bar)

        # Keyboard-navigation and live-refresh state. _rows mirrors what's on
        # screen: one entry per visible channel row, each a list of
        # {"item": block, "data": {...}} in air order (a no-EPG row has its
        # single filler block). _build() fills them.
        self._build_gen = 0
        self._rows: list = []
        self._focus: tuple[int, int] | None = None
        self._progress: list = []
        self._now_line = None
        self._grid_h = 0
        # Keep the board alive while open: the now-line and the progress
        # fills tick along instead of freezing at open time.
        self._refresh = QTimer(self)
        self._refresh.setInterval(30_000)
        self._refresh.timeout.connect(self._tick)
        self._refresh.start()

        self._build()

        # Opened before the EPG finished loading (startup race)? Every row
        # then draws as a bare band. Poll until the guide reports loaded and
        # rebuild once, so the board fills in by itself instead of needing a
        # close-and-reopen.
        self._epg_poll = QTimer(self)
        self._epg_poll.setInterval(1500)
        self._epg_poll.timeout.connect(self._maybe_reload_epg)
        if not self._epg_ready():
            self._epg_poll.start()

        scr = self.screen().availableGeometry() if self.screen() else None
        want_w = self.CH_COL_W + self._grid_w + 40
        want_h = self.HEADER_H + len(self.channels) * self.ROW_H + 110
        # Cap to the main window, not the whole screen, so the guide stays in
        # proportion to the app instead of ballooning to 1500 px on a big
        # display while the window itself is small.
        mw = self.window
        if mw is not None and mw.width() > 200:
            cap_w, cap_h = int(mw.width() * 0.94), int(mw.height() * 0.94)
        else:
            cap_w = (scr.width() - 80) if scr else 1200
            cap_h = (scr.height() - 80) if scr else 800
        w = min(want_w, cap_w, 1500)
        h = min(want_h, cap_h, 900)
        self.resize(max(min(760, cap_w), w), max(min(460, cap_h), h))
        # Centring happens in showEvent - a move() here (before the window is
        # shown) doesn't stick on macOS, which then opens it low/offset.
        now_x = self.CH_COL_W + (now - self._start) / 60 * self.PX_PER_MIN
        self.view.horizontalScrollBar().setValue(max(0, int(now_x
                                                             - self.CH_COL_W - 40)))
        # Open on the channel you're watching (if any), so the guide lands on
        # what's playing instead of the top of a long line-up.
        if getattr(self, "_playing_row", None) is not None:
            self._scroll_to_playing()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Centre in the screen once, after the window has its final size and a
        # real screen - doing it in __init__ (pre-show) doesn't stick on macOS.
        # Keyboard navigation should work immediately - focus the grid, not
        # the filter field (typing still lands in the filter on click).
        self.view.setFocus()
        if getattr(self, "_centred", False):
            return
        self._centred = True
        mw = self.window
        if mw is not None and mw.isVisible():
            c = mw.frameGeometry().center()
            x, y = c.x() - self.width() // 2, c.y() - self.height() // 2
        else:
            scr0 = self.screen()
            g = scr0.availableGeometry() if scr0 else None
            if g is None:
                return
            x, y = g.x() + (g.width() - self.width()) // 2, \
                g.y() + (g.height() - self.height()) // 2
        # Keep it fully on-screen even when the window sits near an edge.
        scr = (mw.screen() if mw else None) or self.screen()
        if scr is not None:
            a = scr.availableGeometry()
            x = max(a.x(), min(x, a.x() + a.width() - self.width()))
            y = max(a.y(), min(y, a.y() + a.height() - self.height()))
        self.move(x, y)

    def keyPressEvent(self, event) -> None:
        # In-guide shortcuts: N jumps to now, P to the playing channel, Enter
        # plays the selected programme. Esc closes (QDialog default).
        k = event.key()
        if k == Qt.Key.Key_N:
            self._scroll_to_now()
            return
        if k == Qt.Key.Key_P and getattr(self, "_playing_row", None) is not None:
            self._scroll_to_playing()
            return
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._selected:
            self._play_selected()
            return
        # Arrows navigate cells even when focus sits on a button rather than
        # the grid view (which handles them itself).
        if k == Qt.Key.Key_Left:
            self._nav(-1, 0)
            return
        if k == Qt.Key.Key_Right:
            self._nav(1, 0)
            return
        if k == Qt.Key.Key_Up:
            self._nav(0, -1)
            return
        if k == Qt.Key.Key_Down:
            self._nav(0, 1)
            return
        super().keyPressEvent(event)

    # -- geometry ------------------------------------------------------------

    def _x(self, ts: float) -> float:
        return self.CH_COL_W + (ts - self._start) / 60 * self.PX_PER_MIN

    def _open_search(self) -> None:
        from .epg_search import EpgSearchDialog
        EpgSearchDialog(self.window).exec()

    def _tri_icon(self, left: bool) -> QIcon:
        """A drawn left/right triangle for the icon-only day-jump buttons.
        Drawn (not the ◀ ▶ glyphs) because those render as tofu boxes on
        fonts that lack them - the same reason the rest of the app draws its
        icons. Supersampled 3x so it stays crisp on any DPI."""
        s, scale = 14, 3
        pm = QPixmap(s * scale, s * scale)
        pm.setDevicePixelRatio(float(scale))
        pm.fill(Qt.GlobalColor.transparent)
        pr = QPainter(pm)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pr.setPen(Qt.PenStyle.NoPen)
        pr.setBrush(QColor(P["text"]))
        path = QPainterPath()
        if left:
            path.moveTo(s * 0.64, s * 0.24)
            path.lineTo(s * 0.36, s * 0.50)
            path.lineTo(s * 0.64, s * 0.76)
        else:
            path.moveTo(s * 0.36, s * 0.24)
            path.lineTo(s * 0.64, s * 0.50)
            path.lineTo(s * 0.36, s * 0.76)
        path.closeSubpath()
        pr.drawPath(path)
        pr.end()
        return QIcon(pm)

    def _epg_ready(self) -> bool:
        try:
            return bool(self.window.xmltv.is_loaded())
        except Exception:
            return True   # can't tell - treat as ready, don't poll forever

    def _maybe_reload_epg(self) -> None:
        if self._epg_ready():
            self._epg_poll.stop()
            self._build()

    def _scroll_to_now(self) -> None:
        """Bring the current time back into view (after scrolling far back)."""
        now_x = self._x(time.time())
        self.view.horizontalScrollBar().setValue(
            max(0, int(now_x - self.CH_COL_W - 40)))

    def _scroll_hours(self, hours: int) -> None:
        """Jump the timeline by whole days/hours without dragging."""
        bar = self.view.horizontalScrollBar()
        bar.setValue(bar.value() + int(hours * 60 * self.PX_PER_MIN))

    def _scroll_tonight(self) -> None:
        """Jump to prime time (20:00) today - the slot people actually plan
        their evening around."""
        tonight = datetime.now().replace(hour=20, minute=0, second=0,
                                         microsecond=0).timestamp()
        x = self._x(max(self._start, min(tonight, self._stop)))
        self.view.horizontalScrollBar().setValue(
            max(0, int(x - self.CH_COL_W - 40)))

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
        self._build_gen += 1       # invalidates in-flight logo callbacks
        self._rows = []
        self._focus = None
        self._progress = []
        self._now_line = None
        self.play_btn.setEnabled(False)
        self.desc.hide()
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

        self._grid_h = grid_h
        self._draw_grid_lines(grid_h)
        self._draw_time_header()
        for row, ch in enumerate(chans):
            self._draw_channel_row(row, ch)
        self._draw_now_line(grid_h)
        self._update_progress()

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
        # The channel-name cell is clickable too (prog=None -> tune live).
        # Hit-testing keys off data(0), which only programme blocks used to
        # carry - a channel without EPG rows had no clickable surface at all,
        # so it simply couldn't be played from the guide.
        cell.setData(0, {"channel": ch, "prog": None})
        self._chan_group.addToGroup(cell)
        # Channel logo (async, like the main list): reserve a fixed slot so
        # names align whether the logo has arrived or not. The callback is
        # generation-guarded - a rebuild (filter typing) clears the scene and
        # a late pixmap must not be added to the dead one.
        logo_url = ch.get("stream_icon")
        text_x = 44 if logo_url else 8
        if logo_url and getattr(self.window, "logos", None) is not None:
            gen = self._build_gen

            def _logo_cb(pm, y=y, gen=gen):
                if gen != self._build_gen or pm is None or pm.isNull():
                    return
                scaled = pm.scaled(
                    32, self.ROW_H - 16,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                it_pm = QGraphicsPixmapItem(scaled)
                it_pm.setPos(8 + (32 - scaled.width()) / 2,
                             y + (self.ROW_H - scaled.height()) / 2)
                it_pm.setData(0, {"channel": ch, "prog": None})
                self._chan_group.addToGroup(it_pm)

            self.window.logos.get(logo_url, _logo_cb)
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
        self._elide(name, self.CH_COL_W - text_x - 8)
        name.setPos(text_x, y + (self.ROW_H - name.boundingRect().height()) / 2)
        name.setData(0, {"channel": ch, "prog": None})
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

        row_blocks: list = []
        drew_any = False
        now = time.time()
        for i, p in enumerate(
                self.window.xmltv.programmes_in(ch, self._start, self._stop)):
            x1 = max(self.CH_COL_W, self._x(p["start_timestamp"]))
            x2 = min(self.CH_COL_W + self._grid_w, self._x(p["stop_timestamp"]))
            if x2 - x1 < 2:
                continue
            drew_any = True
            shade = base.lighter(120) if i % 2 == 0 else base.lighter(102)
            block = QGraphicsRectItem(x1 + 1, y + 2, x2 - x1 - 2, self.ROW_H - 4)
            block.setBrush(QBrush(shade))
            block.setPen(QPen(QColor(P["bg"]), 1))
            block.setZValue(5)
            data = {"channel": ch, "prog": p}
            block.setData(0, data)
            self.scene.addItem(block)
            # State glyphs on the block itself: a scheduled/running recording
            # and a set reminder used to be visible only in the context menu.
            prefix = ""
            if self._has_rec_job(ch, p):
                prefix += "⏺ "
            if (p["start_timestamp"] > now
                    and ch.get("stream_id") is not None
                    and getattr(self.window, "reminders", None) is not None
                    and self.window.reminders.has(ch.get("stream_id"),
                                                  p["start_timestamp"])):
                prefix += "🔔 "
            label = QGraphicsSimpleTextItem(prefix + (p.get("title") or "?"),
                                            block)
            label.setBrush(QColor("#ffffff"))
            self._elide(label, x2 - x1 - 10)
            label.setPos(x1 + 5, y + 7)
            label.setData(0, data)
            row_blocks.append({"item": block, "data": data})
        if not drew_any:
            # No EPG for this channel: fill the row with one muted block so
            # the whole timeline is still clickable and plays the channel
            # live - and says why it's empty.
            block = QGraphicsRectItem(self.CH_COL_W + 1, y + 2,
                                      self._grid_w - 2, self.ROW_H - 4)
            block.setBrush(QBrush(base.lighter(105)))
            block.setPen(QPen(QColor(P["bg"]), 1))
            block.setZValue(5)
            data = {"channel": ch, "prog": None}
            block.setData(0, data)
            self.scene.addItem(block)
            # While the guide is still loading, say so instead of the
            # misleading "no guide available for this channel".
            label = QGraphicsSimpleTextItem(
                tr("epg_no_guide_available") if self._epg_ready()
                else tr("status_loading_programme_guide"), block)
            label.setBrush(QColor("#9aa3b2"))
            self._elide(label, self._grid_w - 12)
            label.setPos(self.CH_COL_W + 6, y + 7)
            label.setData(0, data)
            row_blocks.append({"item": block, "data": data})
        self._rows.append((ch, row_blocks))

    def _has_rec_job(self, ch: dict, p: dict) -> bool:
        """A scheduled/running recording overlapping this programme on this
        channel (matched on the stream id embedded in the job's URL)."""
        rec = getattr(self.window, "rec", None)
        sid = ch.get("stream_id")
        if rec is None or sid is None:
            return False
        tag = f"/{sid}."
        for j in getattr(rec, "jobs", []):
            if j.get("status") not in ("scheduled", "recording"):
                continue
            if tag not in (j.get("url") or ""):
                continue
            stop = j.get("stop") or float("inf")
            if j.get("start", 0) < p["stop_timestamp"] \
                    and stop > p["start_timestamp"]:
                return True
        return False

    def _draw_now_line(self, grid_h: int) -> None:
        x = self._x(time.time())
        line = self.scene.addLine(x, 0, x, self.HEADER_H + grid_h,
                                  QPen(QColor("#e5354b"), 2))
        line.setZValue(19)
        self._now_line = line

    def _update_progress(self) -> None:
        """(Re)draw the elapsed-fraction fill along the bottom of every block
        whose programme is on air right now. Called at build and from the
        30 s tick, so the fills creep along while the guide is open."""
        for it in self._progress:
            if it.scene() is not None:
                self.scene.removeItem(it)
        self._progress = []
        now = time.time()
        fill = QColor(ACCENT)
        fill.setAlpha(210)
        for row, (_ch, blocks) in enumerate(self._rows):
            y = self.HEADER_H + row * self.ROW_H
            for b in blocks:
                p = b["data"]["prog"]
                if not p or not (p["start_timestamp"] <= now
                                 < p["stop_timestamp"]):
                    continue
                r = b["item"].rect()
                frac = ((now - p["start_timestamp"])
                        / max(1, p["stop_timestamp"] - p["start_timestamp"]))
                bar = QGraphicsRectItem(r.x(), y + self.ROW_H - 7,
                                        max(2.0, r.width() * frac), 3)
                bar.setBrush(QBrush(fill))
                bar.setPen(QPen(Qt.PenStyle.NoPen))
                bar.setZValue(7)
                bar.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                self.scene.addItem(bar)
                self._progress.append(bar)

    def _tick(self) -> None:
        """30 s live refresh: the now-line and the progress fills move; the
        block layout itself stays put (a full rebuild would drop selection
        and is not worth it for a line that moves 3 px)."""
        if self._now_line is None or self._now_line.scene() is None:
            return
        x = self._x(time.time())
        if x <= self.CH_COL_W + self._grid_w:
            self._now_line.setLine(x, 0, x, self.HEADER_H + self._grid_h)
        self._update_progress()

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

    def _hit(self, scene_pos):
        """(item, data) for whatever carries a data(0) payload at the given
        scene position - programme blocks, filler rows, channel cells."""
        for it in self.scene.items(scene_pos):
            data = it.data(0)
            if isinstance(data, dict) and "channel" in data:
                return it, data
        return None, None

    def _block_at(self, scene_pos):
        return self._hit(scene_pos)[1]

    def _highlight_item(self, item) -> None:
        # sceneBoundingRect, not rect(): the channel-name cells live in the
        # pinned (translated) column group, so their item coords are shifted
        # from scene coords once the view scrolls sideways.
        if getattr(self, "_sel_outline", None) is None \
                or self._sel_outline.scene() is None:
            self._sel_outline = self.scene.addRect(
                item.sceneBoundingRect(), QPen(QColor("#ffffff"), 2),
                QBrush(Qt.BrushStyle.NoBrush))
            self._sel_outline.setZValue(17)
            self._sel_outline.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        else:
            self._sel_outline.setRect(item.sceneBoundingRect())
        self._sel_outline.show()

    def _apply_selection(self, item, data) -> None:
        """One selection path for mouse and keyboard: outline the block,
        remember it, and fill the info line + description panel."""
        self._selected = data
        self._highlight_item(item)
        self.play_btn.setEnabled(True)
        p = data["prog"]
        if p is None:
            # A channel without EPG: selectable and playable, it just has
            # no programme to describe.
            self.info.setText(f"{data['channel'].get('name') or '?'} · "
                              + tr("epg_no_guide_available"))
            self.desc.hide()
            return
        day = datetime.fromtimestamp(p["start_timestamp"])
        # Date prefix only when the programme isn't today's - keeps the
        # common case short.
        date_tag = ("" if day.date() == datetime.now().date()
                    else day.strftime("%a %d %b") + " · ")
        start = day.strftime("%H:%M")
        stop = datetime.fromtimestamp(p["stop_timestamp"]).strftime("%H:%M")
        mins = int((p["stop_timestamp"] - p["start_timestamp"]) // 60)
        catchup = (p["stop_timestamp"] < time.time()
                   and self.window._timeshift_days(data["channel"]))
        tag = "  ⟲" if catchup else ""
        self.info.setText(
            f"{data['channel'].get('name') or '?'} · {date_tag}"
            f"{start}–{stop} ({mins} min)  {p.get('title') or ''}{tag}")
        d = (p.get("description") or "").strip()
        if d:
            self.desc.setText(d)
            self.desc.show()
        else:
            self.desc.hide()

    def _select_at(self, scene_pos) -> None:
        item, data = self._hit(scene_pos)
        if not data:
            return
        self._apply_selection(item, data)
        # Track (row, col) so arrow keys continue from the clicked block.
        row = int((scene_pos.y() - self.HEADER_H) // self.ROW_H)
        if 0 <= row < len(self._rows):
            blocks = self._rows[row][1]
            col = next((i for i, b in enumerate(blocks)
                        if b["data"] is data), None)
            if col is None:   # channel cell / logo clicked: land on "now"
                col = self._col_for_time(blocks, time.time())
            self._focus = (row, col) if blocks else None

    # -- keyboard navigation --------------------------------------------------

    @staticmethod
    def _col_for_time(blocks, ts: float) -> int:
        """The block index airing at *ts*, else the nearest one - how a TV
        guide picks the cell when you move vertically between rows."""
        best, best_d = 0, float("inf")
        for i, b in enumerate(blocks):
            p = b["data"]["prog"]
            if p is None:
                return i
            if p["start_timestamp"] <= ts < p["stop_timestamp"]:
                return i
            mid = (p["start_timestamp"] + p["stop_timestamp"]) / 2
            if abs(mid - ts) < best_d:
                best, best_d = i, abs(mid - ts)
        return best

    def _nav(self, dc: int, dr: int) -> None:
        """Move the selection one cell: dc=±1 along the row (prev/next
        programme), dr=±1 across rows (keeping the same time of day)."""
        if not self._rows:
            return
        if self._focus is None:
            row = self._playing_row if self._playing_row is not None else 0
            blocks = self._rows[row][1]
            if not blocks:
                return
            self._select_block(row, self._col_for_time(blocks, time.time()))
            return
        row, col = self._focus
        row = max(0, min(len(self._rows) - 1, row))
        if dr:
            cur = self._rows[row][1][col]["data"]["prog"] \
                if col < len(self._rows[row][1]) else None
            ref = ((cur["start_timestamp"] + cur["stop_timestamp"]) / 2
                   if cur else time.time())
            row = max(0, min(len(self._rows) - 1, row + dr))
            blocks = self._rows[row][1]
            if not blocks:
                return
            col = self._col_for_time(blocks, ref)
        else:
            blocks = self._rows[row][1]
            if not blocks:
                return
            col = max(0, min(len(blocks) - 1, col + dc))
        self._select_block(row, col)

    def _select_block(self, row: int, col: int) -> None:
        b = self._rows[row][1][col]
        self._focus = (row, col)
        self._apply_selection(b["item"], b["data"])
        self._ensure_block_visible(b["item"])

    def _ensure_block_visible(self, item) -> None:
        """Scroll the block into view, then push past the pinned header /
        channel column, which ensureVisible knows nothing about."""
        r = item.sceneBoundingRect()
        self.view.ensureVisible(r, 30, 20)
        tl = self.view.mapToScene(0, 0)
        hbar = self.view.horizontalScrollBar()
        vbar = self.view.verticalScrollBar()
        overlap_x = (tl.x() + self.CH_COL_W) - r.left()
        if overlap_x > 0 and r.left() > self.CH_COL_W - 1:
            hbar.setValue(int(hbar.value() - overlap_x - 10))
        overlap_y = (tl.y() + self.HEADER_H) - r.top()
        if overlap_y > 0:
            vbar.setValue(int(vbar.value() - overlap_y - 6))

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
        # everything else (incl. a no-EPG channel, prog=None) tunes live.
        if p is None:
            self.window.tune_from_guide(ch)
            self.accept()
            return
        if p["stop_timestamp"] < time.time() and self.window._timeshift_days(ch):
            self.window._play_timeshift(ch, prog=p)
        else:
            self.window.tune_from_guide(ch)
        self.accept()

    def _context_at(self, scene_pos, global_pos) -> None:
        data = self._block_at(scene_pos)
        if not data:
            return
        ch, p = data["channel"], data["prog"]
        self._selected = data
        self.play_btn.setEnabled(True)
        m = QMenu(self)
        if p is None:
            # No-EPG channel: no programme to time-shift/record/remind about,
            # so the menu is just "play it live".
            m.addAction(tr("epg_play_channel"),
                        lambda: (self.window.tune_from_guide(ch),
                                 self.accept()))
            m.exec(global_pos)
            return
        past = p["stop_timestamp"] < time.time()
        if past and self.window._timeshift_days(ch):
            m.addAction(tr("epg_play_this_programme"), self._play_selected)
        m.addAction(tr("ts_go_live") if past else tr("epg_play_channel"),
                    lambda: (self.window.tune_from_guide(ch), self.accept()))
        if ch.get("stream_id") is not None and p["stop_timestamp"] > time.time():
            m.addAction(tr("rec_record_programme"), lambda: self._record(ch, p))
        # Remind me when a future programme starts.
        if (ch.get("stream_id") is not None
                and p["start_timestamp"] > time.time()
                and getattr(self.window, "reminders", None) is not None):
            sid, start = ch.get("stream_id"), p["start_timestamp"]
            if self.window.reminders.has(sid, start):
                m.addAction(
                    tr("reminder_remove"),
                    lambda: self.window.reminders.remove(sid, start))
            else:
                m.addAction(
                    tr("reminder_add"),
                    lambda: self.window._add_reminder(ch, p))
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
