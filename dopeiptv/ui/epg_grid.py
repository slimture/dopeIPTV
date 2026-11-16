"""Horizontal timeline EPG grid: channels as colour-coded rows, time along
the x-axis, programmes as blocks - the classic 'what's on now/next' guide.

Built on a single QGraphicsView. The time header and the channel column are
each a QGraphicsItemGroup pinned to the top / left by moving them to the
current scroll offset, so they stay in view while the programme area scrolls
under them.

``EpgGrid`` is the guide itself - filter, board, the selected programme and
the buttons - as a widget, so the same guide lives in the window's middle
pane (the Guide page, ui/mw_guide.py) or in a window of its own
(``EpgGridDialog``, the pop-out). Its timers run only while it is on screen.

The time scale fits the room the board has (Fit): as many hours as fit
while a half hour stays wide enough for a short title, in steady steps
(``ZOOM_SPANS``). − and + fix the span instead, remembered for the page
and the window apart; a new scale lays out the axis and the rows on screen
again, never the channel list.

The look is kept calm: flat cards with a small gap, the programme on the
air tinted with the accent and a thin strip of how far it got, the past
dimmed, one accent line at the current time. The channel column is narrow
and can be dragged wider (remembered); a narrow board shows the logos
alone. A row with no guide is an empty field, a "channel" that is only a
heading in the provider's list ("-= Sweden =-") a heading
(core/guide_rows.py).
"""

from __future__ import annotations

import time
from datetime import datetime

from PyQt6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QFontInfo, QFontMetrics, QFontMetricsF, QIcon,
    QPainter, QPainterPath, QPen, QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication, QDialog, QGraphicsItem, QGraphicsItemGroup,
    QGraphicsLineItem, QGraphicsPathItem, QGraphicsPixmapItem,
    QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem,
    QGraphicsView, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
    QVBoxLayout, QWidget,
)

from ..core.guide_rows import section_heading, short_name
from ..core.log import log
from ..i18n import tr
from .qt_i18n import format_when
from .theme import P, corner_radius


class _GridView(QGraphicsView):
    """Hands clicks / double-clicks / right-clicks back to the guide as
    scene positions."""

    def __init__(self, scene, grid) -> None:
        super().__init__(scene)
        self._grid = grid
        self.viewport().setMouseTracking(True)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.RightButton:
            self._grid._context_at(self.mapToScene(e.pos()),
                                   e.globalPosition().toPoint())
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            # The channel column's edge: dragged wider or narrower.
            if self._grid._col_edge_at(e.pos().x()):
                self._grid._col_drag_start(e.pos().x())
                e.accept()
                return
            self._grid._select_at(self.mapToScene(e.pos()))
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        g = self._grid
        if g._col_drag is not None:
            g._col_drag_move(e.pos().x())
            e.accept()
            return
        if g._col_edge_at(e.pos().x()):
            self.viewport().setCursor(Qt.CursorShape.SplitHCursor)
        else:
            self.viewport().unsetCursor()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if self._grid._col_drag is not None:
            self._grid._col_drag_end(e.pos().x())
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def leaveEvent(self, e) -> None:
        if self._grid._col_drag is None:
            self.viewport().unsetCursor()
        super().leaveEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:
        # On the column's edge: back to its own width.
        if (e.button() == Qt.MouseButton.LeftButton
                and self._grid._col_edge_at(e.pos().x())):
            self._grid._col_reset()
            e.accept()
            return
        self._grid._play_at(self.mapToScene(e.pos()))
        super().mouseDoubleClickEvent(e)

    def resizeEvent(self, e) -> None:
        # The viewport's size (QAbstractScrollArea hands its resizes here),
        # which is the room the time scale fits.
        super().resizeEvent(e)
        self._grid._viewport_resized()

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        self._grid._painted = True

    def wheelEvent(self, e) -> None:
        # Ctrl + wheel zooms the time scale; the plain wheel scrolls.
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._grid._wheel_zoom(e.angleDelta().y())
            e.accept()
            return
        super().wheelEvent(e)

    def keyPressEvent(self, e) -> None:
        if self._grid._zoom_key(e):
            e.accept()
            return
        # TV-style cell navigation: arrows move the selection between
        # programme blocks instead of just scrolling the view.
        k = e.key()
        if k == Qt.Key.Key_Left:
            self._grid._nav(-1, 0)
        elif k == Qt.Key.Key_Right:
            self._grid._nav(1, 0)
        elif k == Qt.Key.Key_Up:
            self._grid._nav(0, -1)
        elif k == Qt.Key.Key_Down:
            self._grid._nav(0, 1)
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._grid._play_selected()
        else:
            super().keyPressEvent(e)
            return
        e.accept()


class EpgGrid(QWidget):
    """The full EPG guide: a scrollable channel x time grid with now/next, day jumps, search and playable rows."""
    ROW_H = 44
    HEADER_H = 34
    # The channel column: its own width (CH_COL_W on a guide is the width
    # it has now), dragged anywhere from logos alone up to COL_MAX - and
    # narrower than COL_MIN_NAMES is the logos alone. A board narrower
    # than NARROW_VW shows the logos alone, the names in the tooltips; a
    # column dragged wide gives way to leave the board BOARD_MIN_W.
    COL_W = 160
    CH_COL_W = COL_W
    COL_LOGOS_W = 56
    COL_MIN_NAMES = 110
    COL_MAX = 420
    NARROW_VW = 760
    BOARD_MIN_W = 600
    COL_GRIP = 4             # how near its edge the column can be taken
    COL_KEY = "guide_channel_col"
    COL_ADAPTS = True        # TV mode's guide keeps its own, large column
    # The scale before the guide knows its room, and what its window is
    # sized by: 420 px per hour. Once on screen the board fits its width.
    PX_PER_MIN = 7
    # The hours the board shows at once, fitted (Fit) or picked (− / +):
    # steady steps, so a small change of size does not change the span.
    ZOOM_SPANS = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0)
    # Fit shows as many hours as keep a half hour this wide: room for a
    # short title ("Vem vet mest?", "Sportnytt") in a half-hour card.
    MIN_HALF_HOUR_W = 80
    FIT_DELAY_MS = 120       # a resize settles before the scale follows
    # A width change this small (a scroll bar, the player's column settling
    # by a few pixels) keeps the scale: the board was drawn anew - every
    # row on screen - for a span a minute or two different.
    REFIT_TOLERANCE = 0.03
    # The channel names' size, and the titles' and times' (0: the app's
    # own font) - TV mode's guide (ui/tv_mode.py) draws them larger. The
    # names in a quieter ink than the titles, which are what is read.
    NAME_PT = 10
    NAME_INK = "text2"
    LABEL_PX = 0
    # A card says its programme's start under the title from this many
    # times the width of "00:00" (and its padding) on; narrower, the title
    # alone. A title cut to fewer letters than this is not shown at all.
    TIME_ROOM = 2.5
    MIN_TITLE_CHARS = 4
    FUTURE_HOURS = 12       # how far ahead the board reaches
    MAX_PAST_HOURS = 48     # cap on how far back timeshift opens the board
    # A board kept to be shown again (the Guide page) is drawn afresh once
    # the day has turned or less than this much of the future is left on it.
    FRESH_AHEAD_HOURS = 6
    # Every channel gets a row, but only the rows near the viewport are drawn
    # (a row is some sixty scene items). "All" used to be cut to the first 300
    # channels, which with a big provider was one country's - the rest of the
    # line-up was simply not in the guide.
    PRELOAD_ROWS = 12        # drawn beyond each edge of the viewport
    # Those a few at a time, nearest the view first, a turn of the event loop
    # apart: two dozen rows in one go held the window (and the video beside
    # the guide) for a noticeable moment.
    PRELOAD_CHUNK = 4
    KEEP_ROWS = 240          # past this many drawn rows, the farthest go
    # Below this width the bar's longer buttons show their icon alone (the
    # middle pane is narrower than the guide's window used to be); above
    # WIDE_W they get their words back - apart, so it does not flicker.
    COMPACT_W = 860
    WIDE_W = 900

    #: A programme was played from the guide (the window closes on it).
    played = pyqtSignal()
    #: The guide's Close button (shown in its window only).
    close_requested = pyqtSignal()
    #: The Pop out button (shown on the Guide page only).
    pop_out_requested = pyqtSignal()

    def __init__(self, window, channels, category_name=None,
                 parent=None) -> None:
        super().__init__(parent)
        self.window = window
        self.category_name = category_name
        self.scope_key = None     # what the window keeps it for (mw_guide)
        self._all_channels = list(channels)
        self.channels = self._all_channels
        self._selected = None
        self._compact: bool | None = None
        self._words_w: dict[str, int] = {}
        self._refresh_pending = False
        self._built_unready = False
        # The time scale (px per minute) and how it is chosen: fitted to the
        # viewport (auto) or a span of hours picked with − / +. _fitted_vw is
        # the viewport width it was last worked out for; _painted, whether
        # the board has been painted since it came on screen (until then a
        # resize is followed at once, so it never shows at a wrong scale).
        self._ppm = float(self.PX_PER_MIN)
        self._zoom_auto = True
        self._zoom_span = self.ZOOM_SPANS[0]
        self._span = self.ZOOM_SPANS[0]
        self._fitted_vw: int | None = None
        self._painted = False
        self._wheel_acc = 0
        self._in_window = False
        # The channel column: the width chosen (dragged), the one it has
        # now (worked out with the scale, for the room there is), and a
        # drag of its edge under way (its start and where it is now).
        self._user_col = self._read_col()
        self.CH_COL_W = self._col_for(None)
        self._col_drag: float | None = None
        self._col_drag_from = 0.0
        self._col_line: QGraphicsLineItem | None = None
        # The board's "now" line, its piece in the header, the line said
        # once over a board with no guide at all, and whether any drawn row
        # had a guide or lacked one (since the board was built).
        self._now_line: QGraphicsLineItem | None = None
        self._now_head: QGraphicsLineItem | None = None
        self._note: QGraphicsSimpleTextItem | None = None
        self._guide_seen = False
        self._no_guide_seen = False
        self._tones: dict[str, QColor] | None = None
        # A resize (window, sidebar, the player's column, popping out)
        # settles before the time scale follows it. Stopped with the other
        # timers while the guide is off screen.
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(self.FIT_DELAY_MS)
        self._fit_timer.timeout.connect(self._refit)

        now = time.time()
        self._built_day = datetime.fromtimestamp(now).date()
        now_floor = (now // 1800) * 1800
        # Open the board further back for timeshift channels so their past
        # (catch-up-able) programmes are scrollable into view. Only a channel
        # with tv_archive can have catch-up, so the rest are not asked - this
        # runs over the whole line-up.
        max_ts_days = 0
        for ch in self.channels:
            try:
                if not int(ch.get("tv_archive") or 0):
                    continue
                max_ts_days = max(max_ts_days,
                                  self.window._timeshift_days(ch))
            except Exception:
                pass
            if max_ts_days * 24 >= self.MAX_PAST_HOURS:
                break
        past_h = min(max(0.5, max_ts_days * 24), self.MAX_PAST_HOURS)
        self._start = now_floor - past_h * 3600
        self._stop = now_floor + self.FUTURE_HOURS * 3600
        self._grid_w = int((self._stop - self._start) / 60 * self._ppm)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # Top: the guide's name and what it covers (on the page - its window
        # has them in the title bar), the channel filter, Pop out.
        top = QHBoxLayout()
        top.setSpacing(8)
        self.title_lbl = QLabel(tr("nav_guide"))
        self.title_lbl.setObjectName("GuideTitle")
        top.addWidget(self.title_lbl)
        self.scope_lbl = QLabel("")
        self.scope_lbl.setObjectName("GuideScope")
        top.addWidget(self.scope_lbl)
        self.filter = QLineEdit(placeholderText=tr("epg_filter_channels"))
        self.filter.setObjectName("Search")
        self.filter.setClearButtonEnabled(True)
        # Bound methods, not lambdas, for every signal of this guide's own
        # widgets and timers: PyQt lets go of a bound method with its object,
        # while a lambda holding *self* outlived a guide closed and collected -
        # its timer then called into freed memory (PyQt 6.9/6.10 crashed).
        self.filter.textChanged.connect(self._on_filter)
        top.addWidget(self.filter, 1)
        self.popout_btn = QPushButton(objectName="InlineToggle")
        self.popout_btn.setToolTip(tr("tooltip_popout"))
        self.popout_btn.setAccessibleName(tr("tooltip_popout"))
        self.popout_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.popout_btn.setFixedSize(34, 34)
        self.popout_btn.clicked.connect(self.pop_out_requested)
        top.addWidget(self.popout_btn)
        outer.addLayout(top)

        # The bar: search, the day and the moments to jump to, then what
        # the guide leads on to (refresh, reminders, scheduled recordings).
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.search_btn = self._tile(tr("epg_search_btn"))
        self.search_btn.clicked.connect(self._open_search)
        bar.addWidget(self.search_btn)
        # The jumps run along the board's own time axis, which is left to
        # right in every language: so do they, or under a right-to-left
        # layout "a day back" sat right of "a day forward".
        days = QWidget()
        days.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        dl = QHBoxLayout(days)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(6)
        self.day_back_btn = self._tile("")
        self.day_back_btn.setToolTip(tr("epg_day_back"))
        self.day_back_btn.setAccessibleName(tr("epg_day_back"))
        self.day_back_btn.clicked.connect(self._day_back)
        dl.addWidget(self.day_back_btn)
        self.now_btn = self._tile(tr("epg_jump_now"))
        self.now_btn.clicked.connect(self._scroll_to_now)
        dl.addWidget(self.now_btn)
        self.tonight_btn = self._tile(tr("epg_tonight"))
        self.tonight_btn.clicked.connect(self._scroll_tonight)
        dl.addWidget(self.tonight_btn)
        self.day_fwd_btn = self._tile("")
        self.day_fwd_btn.setToolTip(tr("epg_day_fwd"))
        self.day_fwd_btn.setAccessibleName(tr("epg_day_fwd"))
        self.day_fwd_btn.clicked.connect(self._day_fwd)
        dl.addWidget(self.day_fwd_btn)
        for b in (self.day_back_btn, self.day_fwd_btn):
            b.setFixedWidth(30)
        bar.addWidget(days)
        # Jumps the board back up to the channel you're watching (handy after
        # scrolling far down a long line-up). Only shown when one is playing.
        self.playing_btn = self._tile(tr("epg_jump_playing"))
        self.playing_btn.clicked.connect(self._scroll_to_playing)
        self.playing_btn.hide()
        bar.addWidget(self.playing_btn)
        bar.addStretch(1)
        # Zoom along the time axis: − shows more hours, + fewer, Fit (lit
        # while the scale follows the room by itself) the hours that fit.
        # Left to right in every language, like the day jumps.
        zoom = QWidget()
        zoom.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        zl = QHBoxLayout(zoom)
        zl.setContentsMargins(0, 0, 0, 0)
        zl.setSpacing(6)
        self.zoom_out_btn = self._tile("")
        self.zoom_out_btn.clicked.connect(self.zoom_out)
        self.fit_btn = self._tile("")
        self.fit_btn.setCheckable(True)
        self.fit_btn.clicked.connect(self.zoom_fit)
        self.zoom_in_btn = self._tile("")
        self.zoom_in_btn.clicked.connect(self.zoom_in)
        for b, key in ((self.zoom_out_btn, "epg_zoom_out"),
                       (self.fit_btn, "epg_zoom_fit"),
                       (self.zoom_in_btn, "epg_zoom_in")):
            b.setToolTip(tr(key))
            b.setAccessibleName(tr(key))
            b.setFixedWidth(30)
            zl.addWidget(b)
        bar.addWidget(zoom)
        self._zoom_box = zoom
        self._bar = bar
        # Fetch the guide again from here - where you notice it is missing.
        # Drawn, not "↻": that glyph is an empty box in fonts without it.
        self.refresh_btn = self._tile("")
        self.refresh_btn.setToolTip(tr("btn_refresh_epg"))
        self.refresh_btn.setAccessibleName(tr("btn_refresh_epg"))
        self.refresh_btn.setFixedWidth(30)
        self.refresh_btn.clicked.connect(self._refresh_guide)
        bar.addWidget(self.refresh_btn)
        self.reminders_btn = self._tile(tr("reminders_title"))
        self.reminders_btn.clicked.connect(self.window._open_reminders)
        bar.addWidget(self.reminders_btn)
        # Manage upcoming recordings without leaving the guide.
        self.rec_sched_btn = self._tile(tr("rec_scheduled_title"))
        self.rec_sched_btn.setToolTip(tr("rec_manage_scheduled"))
        self.rec_sched_btn.clicked.connect(self.window._show_scheduled_manager)
        bar.addWidget(self.rec_sched_btn)
        outer.addLayout(bar)

        self.scene = QGraphicsScene(self)
        self.view = _GridView(self.scene, self)
        # The board is drawn left to right - channels on the left, time
        # running rightwards - in every language. Under a right-to-left
        # layout Qt inverts the view's horizontal scrolling, so every
        # setValue() below landed hours away from the time it meant and
        # Arabic, Persian, Hebrew and Urdu opened the guide on an empty
        # stretch of the future.
        self.view.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        # Full-viewport repaint on scroll. The default minimal-update mode
        # scrolls the rendered pixels and only repaints the newly-exposed
        # strip - but the pinned header/column and the sticky, re-elided
        # programme titles must be redrawn at a fixed screen position every
        # scroll step, and minimal update leaves their previous pixels behind
        # as ghosts (the "titles on top of each other" seen on macOS/Retina).
        # A guide viewport holds only a few hundred items, so a full repaint
        # per scroll frame is cheap.
        self.view.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        # Anchor content top-left so a small scene isn't centred (which would
        # make mapToScene(0,0) negative and push the pinned header off-view).
        self.view.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.view.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.view.horizontalScrollBar().valueChanged.connect(self._pin)
        self.view.verticalScrollBar().valueChanged.connect(self._pin)
        self.view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        outer.addWidget(self.view, 1)

        # Programme description panel: fed from the XMLTV desc of whatever
        # block is selected - the data was always there, just never shown.
        self.desc = QLabel("")
        self.desc.setWordWrap(True)
        self.desc.setMaximumHeight(56)
        self.desc.hide()
        outer.addWidget(self.desc)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.info = QLabel(tr("epg_select_channel"))
        self.info.setWordWrap(True)
        foot.addWidget(self.info, 1)
        self.play_btn = QPushButton(tr("epg_play_channel"), objectName="Primary")
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self._play_selected)
        foot.addWidget(self.play_btn)
        self.close_btn = QPushButton(tr("common_close"))
        self.close_btn.clicked.connect(self.close_requested)
        foot.addWidget(self.close_btn)
        outer.addLayout(foot)
        self.restyle()

        # Keyboard-navigation and live-refresh state. _rows has one entry per
        # channel, (channel, blocks): blocks is a list of {"item": block,
        # "data": {...}} in air order (a no-EPG row has its single filler
        # block), and stays empty until the row is drawn. _row_items holds
        # the scene items of each drawn row, so a row far out of view can be
        # taken down again.
        self._build_gen = 0
        self._rows: list = []
        self._row_items: dict[int, list] = {}
        self._preload = QTimer(self)
        self._preload.setSingleShot(True)
        self._preload.setInterval(80)
        self._preload.timeout.connect(self._preload_rows)
        self._preload_more = QTimer(self)
        self._preload_more.setSingleShot(True)
        self._preload_more.setInterval(0)
        self._preload_more.timeout.connect(self._preload_rows)
        # The cards' brushes, one per fill colour (a theme makes a new
        # guide; restyle starts this afresh all the same).
        self._brushes: dict[int, QBrush] = {}
        self._focus: tuple[int, int] | None = None
        # Keep the board alive while it is on screen: the on-air cards and
        # the playing channel follow the clock instead of freezing at open.
        # None of the timers run while the guide is hidden (showEvent /
        # hideEvent) - a page left, a window closed, the pane folded away.
        self._refresh = QTimer(self)
        self._refresh.setInterval(30_000)
        self._refresh.timeout.connect(self._tick)
        # Opened before the EPG finished loading (startup race)? Every row
        # then draws as a bare band. Poll until the guide reports loaded and
        # rebuild once, so the board fills in by itself instead of needing a
        # close-and-reopen.
        self._epg_poll = QTimer(self)
        self._epg_poll.setInterval(1500)
        self._epg_poll.timeout.connect(self._maybe_reload_epg)
        self._guide_poll = QTimer(self)
        self._guide_poll.setInterval(500)
        self._guide_poll.timeout.connect(self._after_refresh)

        self.set_in_window(False)
        self._build()

        self._scroll_to_now()
        # Open on the channel you're watching (if any), so the guide lands on
        # what's playing instead of the top of a long line-up.
        if getattr(self, "_playing_row", None) is not None:
            self._scroll_to_playing()

    def _tile(self, text: str) -> QPushButton:
        """A button of the bar: the same small tile as the list's own
        control strip in the middle pane."""
        b = QPushButton(text, objectName="InlineToggle")
        b.setFixedHeight(28)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    # -- where it lives --------------------------------------------------------

    def title(self) -> str:
        t = tr("btn_epg_guide")
        if self.category_name:
            t += f" — {self.category_name}"
        return t

    def set_in_window(self, on: bool) -> None:
        """In its own window the title bar names it and Close ends it; on the
        page it says what it covers itself, and offers to pop out. Each
        place keeps its own zoom."""
        self._in_window = on
        self.close_btn.setVisible(on)
        for w in (self.title_lbl, self.scope_lbl, self.popout_btn):
            w.setVisible(not on)
        self.scope_lbl.setVisible(not on and bool(self.category_name))
        self._load_zoom()

    def restyle(self) -> None:
        """Colours and drawn icons from the current theme (P)."""
        self._brushes = {}
        self._tones = None
        self.title_lbl.setStyleSheet(
            f"color:{P['text']}; font-size:15px; font-weight:700;")
        self.scope_lbl.setStyleSheet(f"color:{P['muted']}; font-size:13px;")
        self.desc.setStyleSheet(f"color:{P['muted']}; font-size:12px;")
        self.info.setStyleSheet(f"color:{P['muted']}; font-size:12px;")
        self._set_scope_text()
        for btn, kind in ((self.search_btn, "search"),
                          (self.refresh_btn, "refresh"),
                          (self.reminders_btn, "bell"),
                          (self.rec_sched_btn, "record"),
                          (self.popout_btn, "popout"),
                          (self.zoom_out_btn, "zoom_out"),
                          (self.fit_btn, "fit"),
                          (self.zoom_in_btn, "zoom_in")):
            btn.setIcon(self._bar_icon(kind))
            btn.setIconSize(QSize(14, 14))
        self.day_back_btn.setIcon(self._tri_icon(left=True))
        self.day_fwd_btn.setIcon(self._tri_icon(left=False))
        self.view.setBackgroundBrush(QColor(P["bg"]))
        # Chunky, accent-coloured scrollbars: the thin grey default blended
        # into the background and was easy to miss, and the horizontal one
        # matters here since the board is wide.
        self.view.setStyleSheet(
            "QGraphicsView{border:none;}"
            "QScrollBar:horizontal{height:18px;margin:0;"
            "background:rgba(128,128,128,40);}"
            "QScrollBar:vertical{width:18px;margin:0;"
            "background:rgba(128,128,128,40);}"
            "QScrollBar::handle{background:%s;border-radius:7px;}"
            "QScrollBar::handle:horizontal{min-width:70px;}"
            "QScrollBar::handle:vertical{min-height:70px;}"
            "QScrollBar::handle:hover{background:%s;}"
            "QScrollBar::add-line,QScrollBar::sub-line{width:0;height:0;}"
            "QAbstractScrollArea::corner{background:%s;}"
            % (P["accent"], P["text"], P["bg"]))

    def _set_scope_text(self) -> None:
        name = self.category_name or ""
        f = QFont(self.scope_lbl.font())
        f.setPixelSize(13)
        fm = QFontMetrics(f)
        self.scope_lbl.setText(
            fm.elidedText(name, Qt.TextElideMode.ElideRight, 220))
        self.scope_lbl.setToolTip(name)

    def stale(self) -> bool:
        """True once the board kept for showing again should be drawn afresh:
        the day has turned, or the future it reaches is nearly used up."""
        now = time.time()
        return (datetime.fromtimestamp(now).date() != self._built_day
                or now > self._stop - self.FRESH_AHEAD_HOURS * 3600)

    def _apply_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        for btn, key in ((self.search_btn, "epg_search_btn"),
                         (self.reminders_btn, "reminders_title"),
                         (self.rec_sched_btn, "rec_scheduled_title")):
            btn.setText("" if compact else tr(key))
            if compact:
                btn.setFixedWidth(30)
                btn.setToolTip(tr("rec_manage_scheduled")
                               if btn is self.rec_sched_btn else tr(key))
            else:
                # The words are no minimum: one would keep the bar from
                # being narrowed to where they go (a big font made it wider
                # than COMPACT_W, so it never got there). What they take is
                # noted for _words_need.
                btn.setMinimumWidth(30)
                btn.setMaximumWidth(16777215)
                btn.ensurePolished()
                self._words_w[key] = btn.sizeHint().width()

    def _words_need(self) -> int:
        """The bar's width with the longer buttons' words and the zoom
        buttons, in the font it has."""
        zoom = self._zoom_box
        extra = zoom.sizeHint().width() + self._bar.spacing() if zoom.isHidden() else 0
        words = sum(max(0, w - 30) for w in self._words_w.values())
        # Measured now, not as the last layout pass left it: a button's
        # minimum (or the stylesheet's padding) may just have changed.
        self._bar.invalidate()
        return self._bar.minimumSize().width() + extra + words

    def _update_compact(self) -> None:
        w = self.width()
        # Where the words fit: COMPACT_W, or more in a font that needs it.
        need = self._words_need()
        if self._compact is True:
            self._apply_compact(w < max(self.WIDE_W, need + self.WIDE_W - self.COMPACT_W))
        else:
            self._apply_compact(w < max(self.COMPACT_W, need))
        self._fit_bar()

    def _fit_bar(self) -> None:
        """The zoom buttons give way in a bar with no room for them (a very
        narrow pane, where an hour is all the board shows anyway) rather
        than squeeze the day jumps; Ctrl + wheel and the keys still zoom."""
        zoom = self._zoom_box
        extra = zoom.sizeHint().width() + self._bar.spacing()
        need = self._bar.minimumSize().width() - (0 if zoom.isHidden()
                                                   else extra)
        zoom.setHidden(need + extra > self.width())

    # -- on screen or not --------------------------------------------------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_compact()
        # A taller window shows rows that were not drawn yet.
        self._pin()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_compact()
        self._start_timers()
        # Resized while away (the window, or popped out / back): the scale
        # follows before the first paint, not a moment after it.
        if self.view.viewport().width() != self._fitted_vw:
            self._refit()
        self._pin()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._stop_timers()
        self._painted = False

    def _start_timers(self) -> None:
        """The guide came (back) on screen: catch up with the clock and the
        channel playing, and run the timers again."""
        self._refresh.start()
        self._mark_now()
        self.refresh_playing()
        if not self._epg_ready():
            self._epg_poll.start()
        elif self._built_unready:
            self._maybe_reload_epg()
        if self._refresh_pending:
            self._guide_poll.start()

    def event(self, e) -> bool:
        # N and P are the guide's own keys. On the page, inside the main
        # window, the window's shortcuts would take P (pop the player out)
        # before the guide ever saw it - in its own window it always had it.
        # So are Ctrl + = / - / 0, the zoom, wherever the focus is in it.
        if e.type() == QEvent.Type.ShortcutOverride and (
                self._zoom_action(e) is not None
                or (e.key() in (Qt.Key.Key_N, Qt.Key.Key_P)
                    and not (e.modifiers()
                             & ~Qt.KeyboardModifier.KeypadModifier)
                    and not isinstance(QApplication.focusWidget(),
                                       QLineEdit))):
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, event) -> None:
        # In-guide shortcuts: N jumps to now, P to the playing channel, Enter
        # plays the selected programme, Ctrl + = / - / 0 zoom. Esc is the
        # host's: its window closes (QDialog), the page goes back (the main
        # window's Esc).
        if self._zoom_key(event):
            return
        k = event.key()
        if k == Qt.Key.Key_N:
            self._scroll_to_now()
            return
        if k == Qt.Key.Key_P:
            if getattr(self, "_playing_row", None) is not None:
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

    # -- zoom ----------------------------------------------------------------

    @classmethod
    def auto_span(cls, area_w: float) -> float:
        """The hours Fit shows in a time area *area_w* px wide: the most of
        ZOOM_SPANS that keep a half hour MIN_HALF_HOUR_W wide (the fewest
        when not even those do)."""
        fit = [s for s in cls.ZOOM_SPANS
               if area_w / (s * 2) >= cls.MIN_HALF_HOUR_W]
        return fit[-1] if fit else cls.ZOOM_SPANS[0]

    def span_hours(self) -> float:
        """The hours the board shows at once now."""
        return self._span

    def _settings_key(self) -> str:
        return "guide_zoom_window" if self._in_window else "guide_zoom_page"

    def _load_zoom(self) -> None:
        """This place's zoom (the page's, or the window's): "auto", or the
        span picked there."""
        st = getattr(self.window, "settings", None)
        try:
            raw = str(st.value(self._settings_key(), "auto")) if st else "auto"
            span = float(raw)
        except (TypeError, ValueError):
            span = 0.0
        self._zoom_auto = span not in self.ZOOM_SPANS
        if not self._zoom_auto:
            self._zoom_span = span
            self._span = span
        self._sync_zoom_buttons()
        if self.isVisible():
            self._refit()
        else:
            self._fitted_vw = None              # worked out as it shows

    def _save_zoom(self) -> None:
        st = getattr(self.window, "settings", None)
        if st is None:
            return
        value = "auto" if self._zoom_auto else f"{self._zoom_span:g}"
        key = self._settings_key()
        try:
            if str(st.value(key, "auto")) != value:
                st.setValue(key, value)
        except Exception as e:
            log.debug("guide: zoom not kept: %s", e)

    def _sync_zoom_buttons(self) -> None:
        self.zoom_out_btn.setEnabled(self._span < self.ZOOM_SPANS[-1])
        self.zoom_in_btn.setEnabled(self._span > self.ZOOM_SPANS[0])
        self.fit_btn.setChecked(self._zoom_auto)

    def zoom_in(self) -> None:
        """Fewer hours at once; the span stays until Fit."""
        self._step_zoom(-1)

    def zoom_out(self) -> None:
        """More hours at once; the span stays until Fit."""
        self._step_zoom(1)

    def zoom_fit(self) -> None:
        """Back to the hours that fit, following the room from now on."""
        self._set_zoom(True)

    def _step_zoom(self, step: int) -> None:
        spans = self.ZOOM_SPANS
        i = min(range(len(spans)), key=lambda j: abs(spans[j] - self._span))
        new = spans[max(0, min(len(spans) - 1, i + step))]
        if new != self._span:
            self._set_zoom(False, new)

    def _set_zoom(self, auto: bool, span: float | None = None) -> None:
        self._zoom_auto = auto
        if span is not None:
            self._zoom_span = span
        self._save_zoom()
        self._refit()

    @staticmethod
    def _zoom_action(e) -> str | None:
        """What a key asks of the zoom: Ctrl + = (or +) "in", Ctrl + -
        "out", Ctrl + 0 "fit"; None for any other key."""
        mods = e.modifiers()
        if (not mods & Qt.KeyboardModifier.ControlModifier
                or mods & (Qt.KeyboardModifier.AltModifier
                           | Qt.KeyboardModifier.MetaModifier)):
            return None
        k = e.key()
        if k in (Qt.Key.Key_Equal, Qt.Key.Key_Plus):
            return "in"
        if k == Qt.Key.Key_Minus:
            return "out"
        if k == Qt.Key.Key_0:
            return "fit"
        return None

    def _zoom_key(self, e) -> bool:
        act = self._zoom_action(e)
        if act == "in":
            self.zoom_in()
        elif act == "out":
            self.zoom_out()
        elif act == "fit":
            self.zoom_fit()
        return act is not None

    def _wheel_zoom(self, dy: int) -> None:
        """Ctrl + wheel: a step per notch (high-resolution wheels and
        touchpads add up to one); up zooms in."""
        if dy * self._wheel_acc < 0:
            self._wheel_acc = 0
        self._wheel_acc += dy
        while self._wheel_acc >= 120:
            self._wheel_acc -= 120
            self.zoom_in()
        while self._wheel_acc <= -120:
            self._wheel_acc += 120
            self.zoom_out()

    def _viewport_resized(self) -> None:
        if self.view.viewport().width() == self._fitted_vw:
            return
        if self._painted:
            self._fit_timer.start()
        else:
            self._refit()

    def _refit(self) -> None:
        """Work the scale out for the viewport as it is now: the span Fit
        gives this room, or the one picked; then the board at that scale."""
        self._fit_timer.stop()
        vw = self.view.viewport().width()
        col = self._col_for(vw)
        area = max(2 * self.MIN_HALF_HOUR_W, vw - col)
        span = self.auto_span(area) if self._zoom_auto else self._zoom_span
        ppm = area / (span * 60)
        self._fitted_vw = vw
        if (span == self._span and col == self.CH_COL_W
                and abs(ppm - self._ppm) <= self._ppm * self.REFIT_TOLERANCE):
            self._sync_zoom_buttons()
            self._pin()                         # the rows in view, drawn
            return
        self._span = span
        self._sync_zoom_buttons()
        self._apply_scale(ppm, col)

    def left_time(self) -> float:
        """The time at the board's left visible edge, beside the channels."""
        return (self._start
                + self.view.horizontalScrollBar().value() / self._ppm * 60)

    def scroll_to_time(self, ts: float) -> None:
        """Scroll sideways so *ts* is at the left visible edge."""
        self.view.horizontalScrollBar().setValue(
            max(0, round((ts - self._start) / 60 * self._ppm)))

    def _apply_scale(self, ppm: float, col: int | None = None) -> None:
        """The board at *ppm* px per minute (and a channel column *col*
        wide), the time at its left edge kept - and "now" kept in view when
        it was."""
        col = self.CH_COL_W if col is None else col
        if abs(ppm - self._ppm) < 1e-6 and col == self.CH_COL_W:
            self._pin()                         # the rows in view, drawn
            return
        vw = self.view.viewport().width()
        left = self.left_time()
        now = time.time()
        now_seen = left <= now <= left + (vw - self.CH_COL_W) / self._ppm * 60
        self._ppm = ppm
        self.CH_COL_W = col
        self._grid_w = int((self._stop - self._start) / 60 * ppm)
        if getattr(self, "_header_group", None) is None:
            return                              # not drawn yet
        sel, focus = self._selected, self._focus
        self._relayout()
        self.scroll_to_time(left)
        if now_seen and not left <= now <= left + (vw - col) / ppm * 60:
            self._scroll_to_now()
        self._pin()
        if sel:
            self._reselect(sel, focus)

    # -- the channel column ----------------------------------------------------

    def _norm_col(self, w: float) -> int:
        """A width chosen for the column, as it is kept: narrower than names
        need is the logos alone."""
        w = int(round(w))
        if w < self.COL_MIN_NAMES:
            return self.COL_LOGOS_W
        return min(self.COL_MAX, w)

    def _read_col(self) -> int:
        """The column's width chosen on this computer (window layout: never
        synced, never in a backup), else its own."""
        st = getattr(self.window, "settings", None)
        try:
            raw = st.value(self.COL_KEY, None) if st is not None else None
            return self._norm_col(float(raw)) if raw not in (None, "") else self.COL_W
        except (TypeError, ValueError):
            return self.COL_W

    def _col_for(self, vw: int | None) -> int:
        """The column's width on a board *vw* px wide (None: not on screen
        yet, the width chosen)."""
        if not self.COL_ADAPTS:
            return type(self).CH_COL_W
        user = self._user_col
        if user <= self.COL_LOGOS_W or (vw is not None and vw < self.NARROW_VW):
            return self.COL_LOGOS_W
        if vw is None:
            return user
        return max(min(user, self.COL_W), min(user, vw - self.BOARD_MIN_W))

    def _logos_only(self) -> bool:
        return self.COL_ADAPTS and self.CH_COL_W <= self.COL_LOGOS_W

    def _col_edge_at(self, x: float) -> bool:
        """Is viewport x on the column's edge, where it can be dragged? Not
        while the board is too narrow for names (the logos alone)."""
        return (self.COL_ADAPTS
                and getattr(self, "_header_group", None) is not None
                and (self._fitted_vw or 0) >= self.NARROW_VW
                and abs(x - self.CH_COL_W) <= self.COL_GRIP)

    def _col_bounds(self) -> tuple[int, int]:
        vw = self.view.viewport().width()
        return self.COL_LOGOS_W, max(self.COL_W, min(self.COL_MAX,
                                                     vw - self.BOARD_MIN_W))

    def _col_drag_start(self, x: float) -> None:
        self._col_drag = self._col_drag_from = float(x)
        self._show_col_line(x)

    def _col_drag_move(self, x: float) -> None:
        lo, hi = self._col_bounds()
        self._col_drag = float(max(lo, min(hi, x)))
        self._show_col_line(self._col_drag)

    def _col_drag_end(self, x: float) -> None:
        moved = abs(x - self._col_drag_from) >= 3
        self._col_drag_move(x)
        w = self._col_drag
        self._col_drag = None
        if self._col_line is not None:
            self.scene.removeItem(self._col_line)
            self._col_line = None
        self.view.viewport().unsetCursor()
        if moved and w is not None:
            self._set_user_col(self._norm_col(w))

    def _col_reset(self) -> None:
        self._set_user_col(self.COL_W)

    def _set_user_col(self, w: int) -> None:
        """The column *w* wide from now on (kept), the board laid out again
        for it."""
        self._user_col = w
        st = getattr(self.window, "settings", None)
        if st is not None:
            try:
                if str(st.value(self.COL_KEY, "")) != str(w):
                    st.setValue(self.COL_KEY, w)
            except Exception as e:
                log.debug("guide: column width not kept: %s", e)
        self._refit()

    def _show_col_line(self, x: float) -> None:
        """Where the column's edge goes, while it is dragged."""
        tl = self.view.mapToScene(0, 0)
        sx = tl.x() + x
        h = self.view.viewport().height()
        if self._col_line is None or self._col_line.scene() is None:
            pen = QPen(QColor(P["accent"]), 2)
            self._col_line = self.scene.addLine(0, 0, 0, 1, pen)
            self._col_line.setZValue(40)
            self._col_line.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._col_line.setLine(sx, tl.y(), sx, tl.y() + h)

    # -- geometry ------------------------------------------------------------

    def _x(self, ts: float) -> float:
        return self.CH_COL_W + (ts - self._start) / 60 * self._ppm

    def _open_search(self) -> None:
        """The Search page, across every source's guide (and the rest)."""
        self.window._open_search_page()

    def _bar_icon(self, kind: str) -> QIcon:
        """Drawn icons for the toolbar buttons (search / reminders / scheduled
        recordings). They used to be emoji prefixes in the button text, which
        the font stack resolves to a colour-emoji face whose glyphs are both
        bigger than the label text and sit on a different baseline - so those
        buttons grew taller than their neighbours and their text no longer
        lined up. Drawn in the same 14 px box as the day-jump triangles, every
        button in the row now has identical metrics on every platform. A
        checked button (Fit) draws its icon in the accent's ink."""
        icon = QIcon(self._bar_pixmap(kind, QColor(P["text"])))
        icon.addPixmap(self._bar_pixmap(kind, QColor(P["on_accent"])),
                       QIcon.Mode.Normal, QIcon.State.On)
        return icon

    @staticmethod
    def _bar_pixmap(kind: str, col: QColor) -> QPixmap:
        s, scale = 14, 3
        pm = QPixmap(s * scale, s * scale)
        pm.setDevicePixelRatio(float(scale))
        pm.fill(Qt.GlobalColor.transparent)
        pr = QPainter(pm)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if kind in ("zoom_out", "zoom_in"):
            # A minus, or a plus: the zoom's own marks.
            pen = QPen(col)
            pen.setWidthF(s * 0.12)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pr.setPen(pen)
            pr.drawLine(QPointF(s * 0.22, s * 0.5), QPointF(s * 0.78, s * 0.5))
            if kind == "zoom_in":
                pr.drawLine(QPointF(s * 0.5, s * 0.22),
                            QPointF(s * 0.5, s * 0.78))
        elif kind == "fit":
            # |<->| : the hours stretched to the board's two edges.
            pen = QPen(col)
            pen.setWidthF(s * 0.09)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pr.setPen(pen)
            pr.drawLine(QPointF(s * 0.10, s * 0.22), QPointF(s * 0.10, s * 0.78))
            pr.drawLine(QPointF(s * 0.90, s * 0.22), QPointF(s * 0.90, s * 0.78))
            pr.drawLine(QPointF(s * 0.25, s * 0.5), QPointF(s * 0.75, s * 0.5))
            for tip, back in ((0.25, 0.38), (0.75, 0.62)):
                path = QPainterPath()
                path.moveTo(s * back, s * 0.37)
                path.lineTo(s * tip, s * 0.5)
                path.lineTo(s * back, s * 0.63)
                pr.drawPath(path)
        elif kind == "search":
            pen = QPen(col)
            pen.setWidthF(s * 0.11)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pr.setPen(pen)
            pr.setBrush(Qt.BrushStyle.NoBrush)
            pr.drawEllipse(QRectF(s * 0.18, s * 0.18, s * 0.46, s * 0.46))
            pr.drawLine(QPointF(s * 0.60, s * 0.60), QPointF(s * 0.82, s * 0.82))
        elif kind == "bell":
            pr.setPen(Qt.PenStyle.NoPen)
            pr.setBrush(col)
            body = QPainterPath()
            # Dome with a flared skirt, then the clapper below it.
            body.moveTo(s * 0.20, s * 0.68)
            body.lineTo(s * 0.28, s * 0.58)
            body.lineTo(s * 0.28, s * 0.44)
            body.arcTo(QRectF(s * 0.28, s * 0.14, s * 0.44, s * 0.44),
                       180.0, -180.0)
            body.lineTo(s * 0.72, s * 0.58)
            body.lineTo(s * 0.80, s * 0.68)
            body.closeSubpath()
            pr.drawPath(body)
            pr.drawEllipse(QRectF(s * 0.42, s * 0.70, s * 0.16, s * 0.16))
        elif kind == "refresh":
            # A circular arrow: most of a ring, an arrowhead at its end.
            pen = QPen(col)
            pen.setWidthF(s * 0.11)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pr.setPen(pen)
            pr.setBrush(Qt.BrushStyle.NoBrush)
            ring = QRectF(s * 0.22, s * 0.22, s * 0.56, s * 0.56)
            pr.drawArc(ring, 70 * 16, 290 * 16)
            pr.setPen(Qt.PenStyle.NoPen)
            pr.setBrush(col)
            head = QPainterPath()
            head.moveTo(s * 0.50, s * 0.06)
            head.lineTo(s * 0.74, s * 0.21)
            head.lineTo(s * 0.50, s * 0.36)
            head.closeSubpath()
            pr.drawPath(head)
        elif kind == "popout":
            # The player's pop-out mark: two squares, one lifted off the other.
            pen = QPen(col)
            pen.setWidthF(s * 0.09)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pr.setPen(pen)
            pr.setBrush(Qt.BrushStyle.NoBrush)
            side, off = s * 0.50, s * 0.20
            pr.drawRoundedRect(QRectF(s * 0.14, s * 0.14 + off, side, side),
                               s * 0.06, s * 0.06)
            pr.drawRoundedRect(QRectF(s * 0.14 + off, s * 0.14, side, side),
                               s * 0.06, s * 0.06)
        else:                                   # "record": a plain filled dot
            pr.setPen(Qt.PenStyle.NoPen)
            pr.setBrush(col)
            pr.drawEllipse(QRectF(s * 0.24, s * 0.24, s * 0.52, s * 0.52))
        pr.end()
        return pm

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
        # "Ready" means the load has *concluded*, not necessarily succeeded: a
        # failed load (or a provider with no XMLTV source at all) must also
        # count, otherwise the poll never stops and every row stays stuck on
        # "loading programme guide..." instead of flipping to "no guide
        # available".
        try:
            x = self.window.xmltv
            return bool(x.is_loaded() or getattr(x, "_failed", False))
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
        bar.setValue(bar.value() + int(hours * 60 * self._ppm))

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

    def _on_filter(self, _text: str = "") -> None:
        self._build()

    def _day_back(self) -> None:
        self._scroll_hours(-24)

    def _day_fwd(self) -> None:
        self._scroll_hours(24)

    def _preload_rows(self) -> None:
        self._fill_rows(preload=True)

    def _stop_timers(self, *_a) -> None:
        for timer in (self._refresh, self._epg_poll, self._guide_poll,
                      self._preload, self._preload_more, self._fit_timer):
            timer.stop()

    # -- build ---------------------------------------------------------------

    def _build(self) -> None:
        self._clear_scene()
        self._selected = None
        self._build_gen += 1       # invalidates in-flight logo callbacks
        self._focus = None
        # Drawn while the guide still loads: drawn again once it is in, also
        # when that happened while the guide was off screen.
        self._built_unready = not self._epg_ready()
        self._guide_seen = self._no_guide_seen = False
        self.play_btn.setEnabled(False)
        self.desc.hide()
        text = self.filter.text().lower().strip()
        chans = [c for c in self._all_channels
                 if not text or text in (c.get("name") or "").lower()]
        self._rows = [(ch, []) for ch in chans]
        # Known before any row is drawn, so the guide can open on (and jump
        # to) the channel being watched even when its row is far down.
        self._playing_row = next(
            (i for i, ch in enumerate(chans) if self._is_playing(ch)), None)
        self._lay_out_board()
        self._pin()
        if hasattr(self, "playing_btn"):
            self.playing_btn.setVisible(self._playing_row is not None)
            self._fit_bar()

    def _relayout(self) -> None:
        """The board again at a new scale: the axis, its width and (as
        _pin asks for them) the rows on screen. The channel list, the filter
        and the playing row stay as they are, and no row off screen is
        drawn - a line-up of thousands costs what the rows in view cost."""
        for row in self._row_items:
            self._rows[row] = (self._rows[row][0], [])
        self._clear_scene()
        self._focus = None
        self._lay_out_board()

    def _clear_scene(self) -> None:
        # Null the pinned-group refs BEFORE clearing. scene.clear() deletes
        # the underlying C++ items, and clearing also nudges the scrollbars,
        # whose valueChanged fires _pin synchronously - which would then touch
        # a dangling QGraphicsItemGroup wrapper (RuntimeError, the crash seen
        # when the load-poll rebuilt a scrolled board). Blocking the scroll
        # signals across the clear closes the window entirely.
        self._header_group = None
        self._chan_group = None
        self._corner = None
        hbar = self.view.horizontalScrollBar()
        vbar = self.view.verticalScrollBar()
        was_h, was_v = hbar.blockSignals(True), vbar.blockSignals(True)
        try:
            self.scene.clear()
        finally:
            hbar.blockSignals(was_h)
            vbar.blockSignals(was_v)
        self._sel_outline = None   # dropped by scene.clear(); recreated on pick
        self._now_line = self._now_head = self._note = self._col_line = None
        self._row_items = {}

    def _lay_out_board(self) -> None:
        """The scene's size, the pinned groups, the grid lines, the time
        header and the corner, at the current scale - no rows yet."""
        grid_h = max(self.ROW_H, len(self._rows) * self.ROW_H)
        self.scene.setSceneRect(0, 0, self.CH_COL_W + self._grid_w,
                                self.HEADER_H + grid_h)

        # Pinned groups (recreated each build - scene.clear() drops them).
        self._header_group = QGraphicsItemGroup()
        self._header_group.setZValue(20)
        self._chan_group = QGraphicsItemGroup()
        self._chan_group.setZValue(15)
        self.scene.addItem(self._header_group)
        self.scene.addItem(self._chan_group)

        self._measure()
        self._draw_grid_lines(grid_h)
        self._draw_time_header()
        self._draw_now_line(grid_h)

        self._corner = self.scene.addRect(
            0, 0, self.CH_COL_W, self.HEADER_H,
            QPen(Qt.PenStyle.NoPen), QBrush(self._tone("cell")))
        self._corner.setZValue(30)
        # Said once, over the middle of the board, when no row drawn has a
        # guide (the rows' own fields are left empty).
        self._note = QGraphicsSimpleTextItem("")
        self._note.setFont(self._label_font())
        self._note.setBrush(QColor(P["muted"]))
        self._note.setZValue(9)
        self._note.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._note.hide()
        self.scene.addItem(self._note)

    def _measure(self) -> None:
        """Where a card's title (and start time) sit in a row, and from
        which width a card has room for its start time - in the fonts the
        board draws with now."""
        inset = self._card_inset()
        card_h = self.ROW_H - 2 * inset
        self._lf, self._tf = self._label_font(), self._time_font()
        lfm, tfm = QFontMetricsF(self._lf), QFontMetricsF(self._tf)
        lh, th = lfm.height(), tfm.height()
        self._ly1 = inset + (card_h - lh) / 2
        gap = max(1.0, self.ROW_H / 44)
        pair = lh + gap + th
        self._two_lines = card_h - pair >= 4
        self._ly2 = inset + (card_h - pair) / 2
        self._ty2 = self._ly2 + lh + gap
        self._time_w = tfm.horizontalAdvance("00:00")
        self._time_min_w = self.TIME_ROOM * self._time_w + 16

    def _card_inset(self) -> int:
        """The gap above and below a card in its row."""
        return max(2, round(self.ROW_H * 0.05))

    def _reselect(self, sel: dict, focus: tuple[int, int] | None) -> None:
        """The selection again after a new scale: the same programme (or
        channel) in its row as drawn now, outlined; it stays selected even
        when it is too short to draw at this scale."""
        ch = sel["channel"]
        row = focus[0] if focus and focus[0] < len(self._rows) else None
        if row is None or self._rows[row][0] is not ch:
            row = next((i for i, (c, _b) in enumerate(self._rows)
                        if c is ch), None)
        self._selected = sel
        if row is None:
            return
        self._ensure_row(row)
        blocks = self._rows[row][1]
        p = sel["prog"]
        for col, b in enumerate(blocks):
            bp = b["data"]["prog"]
            if (bp is p is None) or (
                    bp is not None and p is not None
                    and bp["start_timestamp"] == p["start_timestamp"]
                    and bp.get("title") == p.get("title")):
                self._selected = b["data"]
                self._focus = (row, col)
                self._highlight_item(b["item"])
                return
        if blocks:
            ts = (time.time() if p is None
                  else (p["start_timestamp"] + p["stop_timestamp"]) / 2)
            self._focus = (row, self._col_for_time(blocks, ts))
        cell = next((it for in_group, it in self._row_items.get(row, ())
                     if in_group and isinstance(it.data(0), dict)), None)
        if p is None and cell is not None:      # the channel's own cell
            self._highlight_item(cell)

    # -- rows on demand --------------------------------------------------------

    def _visible_rows(self) -> tuple[int, int]:
        """The rows in the viewport, [top, bottom). Before the guide is shown
        the viewport has no real height yet; assume a tall one."""
        v = self.view.verticalScrollBar().value()
        h = max(self.view.viewport().height(),
                900 if not self.isVisible() else 0)
        top = max(0, (v - self.HEADER_H) // self.ROW_H)
        bottom = (v + h) // self.ROW_H + 1
        return int(top), int(min(len(self._rows), bottom))

    def _ensure_row(self, row: int) -> None:
        if 0 <= row < len(self._rows) and row not in self._row_items:
            self._draw_channel_row(row, self._rows[row][0])

    def _fill_rows(self, preload: bool = False) -> None:
        """Draw the rows in the viewport, and take down the ones far away once
        too many are drawn - scrolling through a line-up of thousands keeps a
        few hundred rows in the scene, never all. The rows just beyond the
        viewport follow once scrolling pauses (*preload*), so dragging the
        scrollbar through thousands of channels draws only what is on screen
        at each step. Nothing is drawn before the board has its scale (as
        it first comes on screen) - it would only be drawn again."""
        if not self._rows or self._fitted_vw is None:
            return
        top, bottom = self._visible_rows()
        extra = self.PRELOAD_ROWS if preload else 1
        lo = max(0, top - extra)
        hi = min(len(self._rows), bottom + extra)
        if preload:
            mid = (top + bottom) / 2
            want = sorted((r for r in range(lo, hi)
                           if r not in self._row_items),
                          key=lambda r: abs(r - mid))
            for row in want[:self.PRELOAD_CHUNK]:
                self._ensure_row(row)
            if len(want) > self.PRELOAD_CHUNK and self.isVisible():
                self._preload_more.start()
        else:
            for row in range(lo, hi):
                self._ensure_row(row)
        # Nothing ticks while the guide is off screen: a scroll set by code
        # meanwhile (a rebuild keeping its place) waits for showEvent.
        if not preload and self.isVisible():
            self._preload.start()
        if len(self._row_items) <= self.KEEP_ROWS:
            return
        keep = {self._focus[0]} if self._focus else set()
        mid = (lo + hi) / 2
        far = sorted((r for r in self._row_items
                      if not lo <= r < hi and r not in keep),
                     key=lambda r: -abs(r - mid))
        for row in far[:len(self._row_items) - self.KEEP_ROWS * 3 // 4]:
            self._drop_row(row)

    def _drop_row(self, row: int) -> None:
        for in_group, it in self._row_items.pop(row, ()):
            if in_group:
                self._chan_group.removeFromGroup(it)
            self.scene.removeItem(it)
        self._rows[row] = (self._rows[row][0], [])

    def _half_hours(self) -> list[float]:
        """The board's half-hour marks, from its start to its stop."""
        n = int((self._stop - self._start) // 1800)
        return [self._start + i * 1800 for i in range(n + 1)]

    @staticmethod
    def _on_hour(ts: float) -> bool:
        """A mark on the hour of the clock here (not of UTC: a half-hour
        time zone has its hours on UTC's half hours)."""
        return datetime.fromtimestamp(ts).minute == 0

    def _draw_grid_lines(self, grid_h: int) -> None:
        for t in self._half_hours():
            x = self._x(t)
            line = self.scene.addLine(
                x, 0, x, self.HEADER_H + grid_h,
                QPen(QColor(P["border"]), 1 if self._on_hour(t) else 0))
            line.setZValue(1)

    def _draw_time_header(self) -> None:
        band = QGraphicsRectItem(0, 0, self.CH_COL_W + self._grid_w,
                                 self.HEADER_H)
        band.setBrush(QBrush(QColor(P["pane"])))
        band.setPen(QPen(Qt.PenStyle.NoPen))
        self._header_group.addToGroup(band)
        # Every half hour, the hours in the text colour: a narrow board
        # shows an hour of it or so, often no hour mark at all. Zoomed out
        # until a half hour is narrower than its label, the hours alone.
        marks = self._half_hours()
        label_w = QFontMetricsF(self._label_font()).horizontalAdvance("00:00")
        if 30 * self._ppm < label_w + 14 and len(marks) > 1:
            first = 1 if (not self._on_hour(marks[0])
                          and self._on_hour(marks[1])) else 0
            marks = marks[first::2]
        ly = (self.HEADER_H
              - QFontMetricsF(self._label_font()).height()) / 2
        for t in marks:
            lbl = QGraphicsSimpleTextItem(
                datetime.fromtimestamp(t).strftime("%H:%M"))
            lbl.setFont(self._label_font())
            lbl.setBrush(QColor(P["text"] if self._on_hour(t)
                                else P["muted"]))
            lbl.setPos(self._x(t) + 4, ly)
            self._header_group.addToGroup(lbl)

    def _now_pen(self) -> QPen:
        pen = QPen(QColor(P["accent"]), max(2, round(self.ROW_H / 30)))
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        return pen

    def _draw_now_line(self, grid_h: int) -> None:
        """One thin accent line at the current time, through the rows and
        the header; the timers that keep the board up to date move it (and
        only run while the guide is on screen). Under the titles and the
        pinned column, over the cards."""
        pen = self._now_pen()
        self._now_line = self.scene.addLine(
            0, self.HEADER_H, 0, self.HEADER_H + grid_h, pen)
        self._now_line.setZValue(7)
        self._now_line.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        # The header's piece is pinned with it: group first, then position
        # (group-local, as every other item of a pinned group).
        self._now_head = QGraphicsLineItem(0, 0, 0, self.HEADER_H)
        self._now_head.setPen(pen)
        self._now_head.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._header_group.addToGroup(self._now_head)
        self._place_now_line(time.time())

    def _place_now_line(self, now: float) -> None:
        if self._now_line is None or self._now_head is None:
            return
        on = self._start <= now <= self._stop
        x = self._x(now)
        for item in (self._now_line, self._now_head):
            item.setVisible(on)
            item.setPos(x, 0)

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

    @staticmethod
    def _mix(a: QColor, b: QColor, t: float) -> QColor:
        """*t* of *a* over *b*, opaque."""
        return QColor(round(a.red() * t + b.red() * (1 - t)),
                      round(a.green() * t + b.green() * (1 - t)),
                      round(a.blue() * t + b.blue() * (1 - t)))

    def _tone(self, key: str) -> QColor:
        """Monochrome guide palette, all derived from the theme so the board
        reads as one tonal surface - logos and the accent (now / playing /
        selected) are the only colour. On a dark theme the cards are a step
        lighter than the board; on a light one the board is the grey and the
        cards are the page's own white. Flat: no bevel, no border."""
        if self._tones is None:
            pane, text = QColor(P["pane"]), QColor(P["text"])
            accent = QColor(P["accent"])
            light = QColor(P["bg"]).lightness() > 128
            if light:
                board, block = self._mix(text, pane, 0.09), pane
            else:
                board, block = pane.darker(112), self._mix(text, pane, 0.08)
            self._tones = {
                "row": board,                    # recessed bg, so cards float
                "cell": pane,                    # channel column and header
                "block": block,                  # a programme card
                # On the air: the accent's tint (and its progress strip).
                "block_now": self._mix(accent, block, 0.16 if light else 0.22),
                "block_past": self._mix(block, board, 0.45),  # aired: recedes
                "empty": self._mix(block, board, 0.38),       # no guide
            }
        return self._tones[key]

    def _spans(self, ch: dict) -> list[tuple[dict, float, float]]:
        """The channel's programmes on the board, each with the x it starts
        and ends at; overlapping duplicates left out."""
        out: list[tuple[dict, float, float]] = []
        left, right = self.CH_COL_W, self.CH_COL_W + self._grid_w
        prev_x2, prev_title = -1.0, None
        for p in self.window.xmltv.programmes_in(ch, self._start, self._stop):
            x1 = max(left, self._x(p["start_timestamp"]))
            x2 = min(right, self._x(p["stop_timestamp"]))
            if x2 - x1 < 2:
                continue
            if x1 < prev_x2 - 1:
                # Overlaps what's already drawn. Provider EPGs routinely list
                # the same programme twice a minute apart (merged sources) -
                # since titles became top-level items, BOTH copies rendered
                # and interleaved into garbage text. Same title or fully
                # covered -> a duplicate, drop it; a genuinely different
                # overlapping entry is clipped to start where the previous
                # card ends.
                if p.get("title") == prev_title or x2 <= prev_x2 + 2:
                    continue
                x1 = prev_x2
                if x2 - x1 < 2:
                    continue
            prev_x2, prev_title = x2, p.get("title")
            out.append((p, x1, x2))
        return out

    def _cell_pad(self) -> int:
        return round(self.ROW_H * 0.18)

    def _draw_channel_row(self, row: int, ch: dict) -> None:
        y = self.HEADER_H + row * self.ROW_H
        # Every item of the row, (in the pinned column group?, item), so
        # _drop_row can take the row down again.
        items: list = []
        self._row_items[row] = items
        spans = self._spans(ch)
        # A "channel" that is only a heading in the provider's list, with
        # nothing behind it: drawn as the heading it is, nothing to pick.
        heading = None if spans else section_heading(ch.get("name"))
        if heading is not None:
            self._draw_heading(y, heading, items)
            self._rows[row] = (ch, [])
            return
        playing = self._is_playing(ch)
        if playing:
            self._playing_row = row
        try:
            has_ts = self.window._timeshift_days(ch) > 0
        except Exception:
            has_ts = False
        # The board behind the row's cards: one tone, the cards and the
        # gaps between them are the rhythm (no zebra).
        back = QGraphicsRectItem(self.CH_COL_W, y, self._grid_w, self.ROW_H)
        back.setBrush(QBrush(self._tone("row")))
        back.setPen(QPen(Qt.PenStyle.NoPen))
        back.setZValue(2)
        back.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.scene.addItem(back)
        items.append((False, back))
        self._draw_channel_cell(row, y, ch, playing, has_ts, items)
        if playing:
            # Tint the whole row across the timeline so the playing channel
            # is obvious even when the programme area is what you're looking
            # at. Sits above the blocks but carries no data(0), so it never
            # intercepts a click (hit-testing reads the block beneath it).
            band = QGraphicsRectItem(self.CH_COL_W, y, self._grid_w, self.ROW_H)
            glow = QColor(P["accent"])
            glow.setAlpha(48)
            band.setBrush(QBrush(glow))
            band.setPen(QPen(Qt.PenStyle.NoPen))
            band.setZValue(6)
            band.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.scene.addItem(band)
            items.append((False, band))

        row_blocks: list = []
        now = time.time()
        inset = self._card_inset()
        card_h = self.ROW_H - 2 * inset
        rec_spans = self._rec_spans(ch)
        for p, x1, x2 in spans:
            state = self._prog_state(p, now)
            data = {"channel": ch, "prog": p}
            block = self._card(x1 + 1, y + inset, x2 - x1 - 3, card_h,
                               state, data)
            # The whole title and its time, however narrow the card is
            # drawn (zoomed out, its title is cut short or left out).
            block.setToolTip(
                f"{p.get('title') or '?'}\n"
                f"{datetime.fromtimestamp(p['start_timestamp']):%H:%M}–"
                f"{datetime.fromtimestamp(p['stop_timestamp']):%H:%M}")
            # State glyphs on the block itself: a scheduled/running recording
            # and a set reminder used to be visible only in the context menu.
            prefix = ""
            if any(start < p["stop_timestamp"] and stop > p["start_timestamp"]
                   for start, stop in rec_spans):
                prefix += "⏺ "
            if (p["start_timestamp"] > now
                    and ch.get("stream_id") is not None
                    and getattr(self.window, "reminders", None) is not None
                    and self.window.reminders.has(ch.get("stream_id"),
                                                  p["start_timestamp"])):
                prefix += "🔔 "
            text = prefix + (p.get("title") or "?")
            # A card wide enough has its start time under the title; a
            # narrower one the title alone, centred.
            wide = self._two_lines and x2 - x1 >= self._time_min_w
            ly = y + (self._ly2 if wide else self._ly1)
            # No label on sliver cards (clipped overlaps): "…" alone is noise.
            label = (self._block_label(text, x1, x2, ly,
                                       QColor(P["muted"]) if state == "past"
                                       else QColor(P["text"]))
                     if x2 - x1 >= 26 else None)
            tm = (self._time_label(p, x1, y + self._ty2)
                  if wide and label is not None else None)
            strip = self._progress(block, now) if state == "now" else None
            row_blocks.append({"item": block, "data": data, "label": label,
                               "time": tm, "strip": strip,
                               "x1": x1, "x2": x2, "y": y, "ly": ly,
                               "ty": y + self._ty2, "text": text,
                               "_state": state})
            items.append((False, block))
            if label is not None:
                items.append((False, label))
            if tm is not None:
                items.append((False, tm))
        if spans:
            self._guide_seen = True
        else:
            # No EPG for this channel: an empty field across the timeline,
            # so the whole row is still clickable and plays the channel live.
            # Why it is empty (or that the guide is still loading) is in its
            # tooltip - and said once over the board when no row has a guide.
            data = {"channel": ch, "prog": None}
            block = self._card(self.CH_COL_W + 1, y + inset, self._grid_w - 3,
                               card_h, "empty", data)
            text = (tr("epg_no_guide_available") if self._epg_ready()
                    else tr("status_loading_programme_guide"))
            block.setToolTip(text)
            row_blocks.append({"item": block, "data": data, "label": None,
                               "time": None, "strip": None,
                               "x1": self.CH_COL_W,
                               "x2": self.CH_COL_W + self._grid_w,
                               "y": y, "ly": y + self._ly1,
                               "ty": y + self._ty2, "text": text,
                               "_state": "empty"})
            items.append((False, block))
            self._no_guide_seen = True
        self._rows[row] = (ch, row_blocks)

    def _draw_channel_cell(self, row: int, y: float, ch: dict, playing: bool,
                           has_ts: bool, items: list) -> None:
        """The row's piece of the pinned channel column: its logo, its name
        (or, logos alone, the name's short form until the logo is in) and
        a quiet catch-up mark."""
        cell = QGraphicsRectItem(0, y, self.CH_COL_W, self.ROW_H)
        cell.setBrush(QBrush(QColor(P["accent"]) if playing
                             else self._tone("cell")))
        cell.setPen(QPen(Qt.PenStyle.NoPen))
        # The channel-name cell is clickable too (prog=None -> tune live).
        # Hit-testing keys off data(0), which only programme blocks used to
        # carry - a channel without EPG rows had no clickable surface at all,
        # so it simply couldn't be played from the guide.
        cell.setData(0, {"channel": ch, "prog": None})
        name = ch.get("name") or "?"
        # The whole name, however it is cut (or left out, logos alone).
        cell.setToolTip(name)
        # Group first, then position (see the logo below): a row drawn after
        # the board scrolled sideways joins a column group that is already
        # shifted, and addToGroup keeps the item's scene position - which put
        # a late row's name hundreds of px left of the visible column.
        self._chan_group.addToGroup(cell)
        cell.setPos(0, 0)
        items.append((True, cell))
        ink = QColor(P["on_accent"] if playing else P[self.NAME_INK])
        quiet = QColor(P["on_accent"] if playing else P["muted"])
        logos_only = self._logos_only()
        pad = self._cell_pad()
        logo_h, logo_dy = self.ROW_H - 2 * pad, 0.0
        if logos_only:
            logo_x, logo_w = 6, self.CH_COL_W - 12
            if has_ts:
                # Room under the logo for the catch-up mark.
                logo_h, logo_dy = logo_h - 4, -2.0
        else:
            logo_x, logo_w = pad, round(logo_h * 1.3)
        stand_in = None
        if logos_only:
            # The name's short form, until (or without) a logo.
            stand_in = QGraphicsSimpleTextItem(short_name(name))
            f = QFont()
            f.setPointSizeF(self.NAME_PT * 0.85)
            f.setWeight(QFont.Weight.DemiBold)
            stand_in.setFont(f)
            stand_in.setBrush(ink)
            self._elide(stand_in, self.CH_COL_W - 8)
            self._chan_group.addToGroup(stand_in)
            br = stand_in.boundingRect()
            stand_in.setPos((self.CH_COL_W - br.width()) / 2,
                            y + (self.ROW_H - br.height()) / 2)
            items.append((True, stand_in))
        # Channel logo (async, like the main list): reserve a fixed slot so
        # names align whether the logo has arrived or not. The callback is
        # generation-guarded - a rebuild (filter typing) clears the scene and
        # a late pixmap must not be added to the dead one.
        logo_url = ch.get("stream_icon")
        if logo_url and getattr(self.window, "logos", None) is not None:
            gen = self._build_gen
            dpr = max(1.0, self.devicePixelRatioF())

            def _logo_cb(pm, y=y + logo_dy, gen=gen, row=row, items=items,
                         box=(logo_x, logo_w, logo_h), stand_in=stand_in):
                # The row may have been taken down (and drawn again, with a
                # logo request of its own) since this one was asked for.
                if (gen != self._build_gen or pm is None or pm.isNull()
                        or self._row_items.get(row) is not items):
                    return
                bx, bw, bh = box
                # At the screen's own pixels, so a small logo stays sharp.
                scaled = pm.scaled(
                    round(bw * dpr), round(bh * dpr),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                scaled.setDevicePixelRatio(dpr)
                size = scaled.deviceIndependentSize()
                it_pm = QGraphicsPixmapItem(scaled)
                it_pm.setTransformationMode(
                    Qt.TransformationMode.SmoothTransformation)
                # Group FIRST, then position. addToGroup preserves the item's
                # SCENE position, and the channel column is pinned by shifting
                # the group by the horizontal scroll offset (_pin) - which the
                # guide applies right after building, to open on "now". A logo
                # that arrived after that (i.e. every logo not already cached)
                # was therefore pinned at scene x=10, hundreds to thousands of
                # px left of the visible column: the reason logos only showed
                # up the *second* time the guide was opened, when the cache
                # made the callback run during the build. Positioning after the
                # add makes the coordinates group-local, like every other item
                # in the column, so the logo lands in its row whenever it
                # arrives. No data(0): clicks fall through to the channel cell.
                self._chan_group.addToGroup(it_pm)
                it_pm.setPos(bx + (bw - size.width()) / 2,
                             y + (self.ROW_H - size.height()) / 2)
                items.append((True, it_pm))
                if stand_in is not None:
                    stand_in.hide()

            self.window.logos.get(logo_url, _logo_cb)
        if logos_only:
            if has_ts:
                h = self._rewind_h() * 0.8
                self._rewind_mark(self.CH_COL_W - 4 - h * 1.25,
                                  y + self.ROW_H - 4 - h / 2, h, quiet, items)
            return
        text_x = logo_x + logo_w + pad if logo_url else pad + 2
        nm = QGraphicsSimpleTextItem(("▶ " if playing else "") + name)
        f = QFont()
        f.setPointSize(self.NAME_PT)
        nm.setFont(f)
        # The theme's ink, not white: on a light theme's pale column white
        # names were all but invisible (the accent cell takes its own ink).
        nm.setBrush(ink)
        mark_w = self._rewind_h() * 1.25 + 6 if has_ts else 0.0
        self._elide(nm, self.CH_COL_W - text_x - 6 - mark_w)
        # No data(0) on the text: hit-testing takes the topmost item carrying a
        # payload, so a click on the name would select via the *text* item and
        # the selection outline would be drawn tight around the letters instead
        # of around the cell. Without a payload the click falls through to the
        # cell underneath, which covers the whole row of the column.
        self._chan_group.addToGroup(nm)
        br = nm.boundingRect()
        nm.setPos(text_x, y + (self.ROW_H - br.height()) / 2)
        items.append((True, nm))
        if has_ts:
            # Catch-up (timeshift), as the main list marks it: a small,
            # quiet mark after the name.
            self._rewind_mark(text_x + br.width() + 6, y + self.ROW_H / 2,
                              self._rewind_h(), quiet, items)

    def _rewind_h(self) -> float:
        return max(6.0, self.ROW_H * 0.17)

    def _rewind_mark(self, x: float, cy: float, h: float, color: QColor,
                     items: list) -> None:
        """The catch-up mark (two small triangles pointing back), drawn: the
        "⏪" glyph came out a colour emoji, louder than the name."""
        w = h * 0.62
        path = QPainterPath()
        for dx in (0.0, w):
            path.moveTo(x + dx + w, cy - h / 2)
            path.lineTo(x + dx, cy)
            path.lineTo(x + dx + w, cy + h / 2)
            path.closeSubpath()
        mark = QGraphicsPathItem(path)
        mark.setBrush(QBrush(color))
        mark.setPen(QPen(Qt.PenStyle.NoPen))
        mark.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._chan_group.addToGroup(mark)
        mark.setPos(0, 0)
        items.append((True, mark))

    def _draw_heading(self, y: float, word: str, items: list) -> None:
        """A heading row ("-= Sweden =-" in the provider's list): the word
        alone, quiet capitals, across the board in its own tone - no logo,
        no card, nothing to click (no data(0) anywhere in it)."""
        tone = QBrush(self._tone("row"))
        band = QGraphicsRectItem(self.CH_COL_W, y, self._grid_w, self.ROW_H)
        band.setBrush(tone)
        band.setPen(QPen(Qt.PenStyle.NoPen))
        band.setZValue(2)
        band.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.scene.addItem(band)
        items.append((False, band))
        # Its piece of the column in the board's tone too, so the heading
        # runs across - and its word may run on past a narrow column.
        cell = QGraphicsRectItem(0, y, self.CH_COL_W, self.ROW_H)
        cell.setBrush(tone)
        cell.setPen(QPen(Qt.PenStyle.NoPen))
        self._chan_group.addToGroup(cell)
        cell.setPos(0, 0)
        items.append((True, cell))
        if not word:
            return
        t = QGraphicsSimpleTextItem(word)
        f = QFont()
        f.setPointSizeF(self.NAME_PT * 0.86)
        f.setWeight(QFont.Weight.DemiBold)
        f.setCapitalization(QFont.Capitalization.AllUppercase)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing,
                           max(1.0, self.ROW_H / 44))
        t.setFont(f)
        t.setBrush(QColor(P["muted"]))
        self._elide(t, max(self.CH_COL_W, 360) - 2 * self._cell_pad())
        self._chan_group.addToGroup(t)
        t.setPos(self._cell_pad() + 2,
                 y + (self.ROW_H - t.boundingRect().height()) / 2)
        items.append((True, t))

    @staticmethod
    def _prog_state(p: dict, now: float) -> str:
        if p["start_timestamp"] <= now < p["stop_timestamp"]:
            return "now"
        return "past" if p["stop_timestamp"] <= now else "future"

    def _card_brush(self, fill: QColor) -> QBrush:
        """A flat brush per fill colour, made once."""
        brush = self._brushes.get(fill.rgba())
        if brush is None:
            brush = self._brushes[fill.rgba()] = QBrush(fill)
        return brush

    def _style_card(self, item: QGraphicsPathItem, state: str) -> None:
        # Flat and borderless: the on-air card is told by its accent tint
        # (and its progress strip), an aired one recedes toward the board.
        key = {"now": "block_now", "past": "block_past",
               "empty": "empty"}.get(state, "block")
        item.setBrush(self._card_brush(self._tone(key)))
        item.setPen(QPen(Qt.PenStyle.NoPen))

    def _card(self, x, y, w, h, state, data) -> QGraphicsPathItem:
        """A rounded, flat programme card. Rounded (a path item, not a
        plain rect) for the softer look; carries the click payload."""
        path = QPainterPath()
        r = corner_radius(6)
        path.addRoundedRect(QRectF(x, y, w, h), r, r)
        item = QGraphicsPathItem(path)
        self._style_card(item, state)
        item.setZValue(5)
        item.setData(0, data)
        self.scene.addItem(item)
        return item

    def _progress(self, card: QGraphicsPathItem,
                  now: float) -> QGraphicsRectItem:
        """The on-air card's thin strip along its bottom edge: the part
        aired, in the accent, up to the "now" line. A child of the card,
        clipped to its rounded shape; it carries no payload, so a click on
        it is the card's."""
        card.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape,
                     True)
        strip = QGraphicsRectItem(card)
        strip.setPen(QPen(Qt.PenStyle.NoPen))
        strip.setBrush(QBrush(QColor(P["accent"])))
        strip.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._set_progress(card, strip, now)
        return strip

    def _set_progress(self, card: QGraphicsPathItem,
                      strip: QGraphicsRectItem, now: float) -> None:
        r = card.path().boundingRect()
        h = max(3, round(self.ROW_H * 0.07))
        w = max(0.0, min(r.width(), self._x(now) - r.x()))
        strip.setRect(r.x(), r.bottom() - h, w, h)

    @classmethod
    def _cut_to_nothing(cls, shown: str, full: str) -> bool:
        """A title cut to fewer than MIN_TITLE_CHARS letters ("Ny…", "Ti…"):
        noise on a narrow card, which shows none instead (its tooltip has
        the title and its times)."""
        return (shown != full
                and len(shown.rstrip("…").strip()) < cls.MIN_TITLE_CHARS)

    def _block_label(self, text, x1, x2, ly, color) -> \
            QGraphicsSimpleTextItem | None:
        """A top-level (not block-child) programme title, so it can be
        re-anchored to the left visible edge as the timeline scrolls
        (_reflow_labels) - the title stays readable while the card slides
        under the channel column. *ly* is where its top goes.

        Deliberately carries no data(0) payload: the title floats above its
        card, and when it answered hit-tests itself a click on the text
        selected through the *text* item - so the selection outline hugged the
        letters rather than the card. Only the card is clickable; the title is
        just paint on top of it."""
        label = QGraphicsSimpleTextItem(text)
        label.setFont(self._lf)
        self._elide(label, x2 - x1 - 14)
        if self._cut_to_nothing(label.text(), text):
            return None
        label.setBrush(color)
        label.setZValue(8)
        label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        label.setPos(x1 + 8, ly)
        self.scene.addItem(label)
        return label

    def _time_label(self, p: dict, x1: float,
                    ty: float) -> QGraphicsSimpleTextItem:
        """A wide card's start time, small and muted under its title; paint
        only, like the title."""
        t = QGraphicsSimpleTextItem(
            datetime.fromtimestamp(p["start_timestamp"]).strftime("%H:%M"))
        t.setFont(self._tf)
        t.setBrush(QColor(P["muted"]))
        t.setZValue(8)
        t.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        t.setPos(x1 + 8, ty)
        self.scene.addItem(t)
        return t

    def _label_font(self) -> QFont:
        f = QFont()
        if self.LABEL_PX:
            f.setPixelSize(self.LABEL_PX)
        return f

    def _time_font(self) -> QFont:
        """The start times' font: a step smaller than the titles'."""
        f = self._label_font()
        size = f.pixelSize() if f.pixelSize() > 0 else QFontInfo(f).pixelSize()
        f.setPixelSize(max(8, round(size * 0.82)))
        return f

    def _rec_spans(self, ch: dict) -> list[tuple[float, float]]:
        """The scheduled or running recordings of this channel (matched on
        the stream id in the job's URL), as (start, stop) - gone through once
        per row, not once per programme: the jobs list keeps every recording
        ever made, and a row has dozens of programmes."""
        rec = getattr(self.window, "rec", None)
        sid = ch.get("stream_id")
        if rec is None or sid is None:
            return []
        tag = f"/{sid}."
        return [(j.get("start", 0), j.get("stop") or float("inf"))
                for j in getattr(rec, "jobs", [])
                if j.get("status") in ("scheduled", "recording")
                and tag in (j.get("url") or "")]

    def _has_rec_job(self, ch: dict, p: dict) -> bool:
        """A scheduled/running recording overlapping this programme on this
        channel."""
        return any(start < p["stop_timestamp"] and stop > p["start_timestamp"]
                   for start, stop in self._rec_spans(ch))

    def _mark_now(self) -> None:
        """Re-mark which card is on air (and dim the ones that just ended),
        so the highlight rolls onto the next programme when one finishes;
        the on-air cards' strips and the "now" line follow the clock. Cheap:
        only cards whose state changed are restyled, only the on-air ones'
        strips moved."""
        now = time.time()
        for _ch, blocks in self._rows:
            for rb in blocks:
                p = rb["data"]["prog"]
                if not p:
                    continue
                state = self._prog_state(p, now)
                strip = rb.get("strip")
                if state == rb.get("_state"):
                    if strip is not None:
                        self._set_progress(rb["item"], strip, now)
                    continue
                rb["_state"] = state
                self._style_card(rb["item"], state)
                lbl = rb.get("label")
                if lbl is not None:
                    lbl.setBrush(QColor(P["muted"]) if state == "past"
                                 else QColor(P["text"]))
                if state == "now" and strip is None:
                    rb["strip"] = self._progress(rb["item"], now)
                elif state != "now" and strip is not None:
                    strip.setParentItem(None)
                    self.scene.removeItem(strip)
                    rb["strip"] = None
        self._place_now_line(now)

    def _tick(self) -> None:
        """30 s live refresh: roll the on-air card highlight forward, and
        follow a channel change made elsewhere (the guide on the page sits
        beside the player). The block layout itself stays put."""
        self._mark_now()
        self.refresh_playing()

    def refresh_playing(self) -> None:
        """Mark the channel playing now: its row and the one it took over
        from are drawn again, the rest stay as they are."""
        new = next((i for i, (ch, _b) in enumerate(self._rows)
                    if self._is_playing(ch)), None)
        old = getattr(self, "_playing_row", None)
        if new == old:
            return
        self._playing_row = new
        for row in {old, new} - {None}:
            if row in self._row_items:
                self._drop_row(row)
        self._fill_rows()
        self.playing_btn.setVisible(new is not None)
        self._fit_bar()

    @staticmethod
    def _elide(item: QGraphicsSimpleTextItem, max_w: float) -> None:
        if item.boundingRect().width() <= max_w:
            return
        # One measure for the whole title, not one per letter taken off: a
        # zoomed-out board has many narrow cards with long titles to cut.
        text = QFontMetricsF(item.font()).elidedText(
            item.text(), Qt.TextElideMode.ElideRight, max(0.0, max_w))
        item.setText(text or "…")
        text = item.text()
        while len(text) > 1 and item.boundingRect().width() > max_w:
            text = text[:-2] + "…"
            item.setText(text)

    # -- sticky header / column ---------------------------------------------

    def _pin(self, *_a) -> None:
        # None while a rebuild is in progress (see _build); skip until the new
        # groups exist rather than touch a deleted wrapper.
        if getattr(self, "_header_group", None) is None:
            return
        self._fill_rows()
        tl = self.view.mapToScene(0, 0)
        self._header_group.setY(tl.y())
        self._chan_group.setX(tl.x())
        self._corner.setPos(tl.x(), tl.y())
        self._reflow_labels(tl.x() + self.CH_COL_W)
        self._update_note(tl)

    def _update_note(self, tl: QPointF) -> None:
        """No row drawn has a guide (it is still loading, or there is none
        for these channels): said once, muted, over the middle of the board
        - not on every row."""
        note = self._note
        if note is None:
            return
        if self._guide_seen or not self._no_guide_seen:
            if note.isVisible():
                note.hide()
            return
        text = (tr("epg_no_guide_any") if self._epg_ready()
                else tr("status_loading_programme_guide"))
        board_w = self.view.viewport().width() - self.CH_COL_W
        if note.data(1) != (text, board_w):
            note.setData(1, (text, board_w))
            self._set_label(note, text, board_w - 24)
        br = note.boundingRect()
        top = tl.y() + self.HEADER_H
        bottom = min(tl.y() + self.view.viewport().height(),
                     self.HEADER_H + len(self._rows) * self.ROW_H)
        note.setPos(tl.x() + self.CH_COL_W + max(12.0, (board_w - br.width()) / 2),
                    top + max(0.0, (bottom - top - br.height()) / 2))
        note.show()

    def _set_label(self, item, full: str, max_w: float) -> None:
        item.setText(full)
        self._elide(item, max_w)

    def _reflow_labels(self, left: float) -> None:
        """Keep each programme title anchored to the left visible edge of its
        card as the timeline scrolls (TV-guide/SwipTV behaviour): a card whose
        start has slid under the channel column still shows its title at the
        column's right edge, until the card itself scrolls off. Only the rows
        actually on screen are reflowed, and only the one card per row that
        straddles the edge does the (costlier) re-elide - the rest keep their
        static build-time position."""
        if not self._rows:
            return
        pad = 6
        vbar = self.view.verticalScrollBar()
        top = int(vbar.value() // self.ROW_H)
        bottom = int((vbar.value() + self.view.viewport().height())
                     // self.ROW_H) + 1
        for row in range(max(0, top), min(len(self._rows), bottom)):
            for rb in self._rows[row][1]:
                lbl = rb.get("label")
                if lbl is None:
                    continue
                tm = rb.get("time")
                x1, x2 = rb["x1"], rb["x2"]
                if x2 <= left + 2:                    # card scrolled off left
                    self._show_label(rb, False, False)
                    continue
                if x1 >= left:                        # fully in view: static
                    if rb.get("_stuck"):
                        lbl.setPos(x1 + pad, rb["ly"])
                        self._set_label(lbl, rb["text"], x2 - x1 - 2 * pad)
                        if tm is not None:
                            tm.setPos(x1 + pad, rb["ty"])
                        rb["_stuck"] = False
                    self._show_label(rb, True, True)
                else:                                 # straddles: stick to edge
                    avail = x2 - left - 2 * pad
                    if avail < 18:                    # only a sliver left: hide
                        self._show_label(rb, False, False)  # rather than "…"
                        continue
                    lbl.setPos(left + pad, rb["ly"])
                    self._set_label(lbl, rb["text"], avail)
                    if tm is not None:
                        tm.setPos(left + pad, rb["ty"])
                    rb["_stuck"] = True
                    title = not self._cut_to_nothing(lbl.text(), rb["text"])
                    self._show_label(rb, title, title and tm is not None
                                     and avail >= tm.boundingRect().width())

    @staticmethod
    def _show_label(rb: dict, title: bool, start: bool) -> None:
        """A card's title (and start time) shown or hidden, touched only when
        that changes; ``_hidden`` says the title is."""
        lbl, tm = rb["label"], rb.get("time")
        if lbl.isVisible() != title:
            lbl.setVisible(title)
        if tm is not None and tm.isVisible() != start:
            tm.setVisible(start)
        rb["_hidden"] = not title

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
                item.sceneBoundingRect(), QPen(QColor(P["text"]), 2),
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
                    else format_when(day, "ddd d MMM") + " · ")
        start = day.strftime("%H:%M")
        stop = datetime.fromtimestamp(p["stop_timestamp"]).strftime("%H:%M")
        mins = int((p["stop_timestamp"] - p["start_timestamp"]) // 60)
        catchup = (p["stop_timestamp"] < time.time()
                   and self.window._timeshift_days(data["channel"]))
        tag = "  ⟲" if catchup else ""
        self.info.setText(
            f"{data['channel'].get('name') or '?'} · {date_tag}"
            f"{start}–{stop} ({tr('opt_minutes', n=mins)})  "
            f"{p.get('title') or ''}{tag}")
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
            self._ensure_row(row)
            if not self._rows[row][1]:          # a heading: the next row
                row = self._step_row(row, 1)
                if row is None:
                    return
            blocks = self._rows[row][1]
            self._select_block(row, self._col_for_time(blocks, time.time()))
            return
        row, col = self._focus
        row = max(0, min(len(self._rows) - 1, row))
        self._ensure_row(row)
        if dr:
            cur = self._rows[row][1][col]["data"]["prog"] \
                if col < len(self._rows[row][1]) else None
            ref = ((cur["start_timestamp"] + cur["stop_timestamp"]) / 2
                   if cur else time.time())
            new = self._step_row(row, dr)
            if new is None:
                return
            row = new
            blocks = self._rows[row][1]
            col = self._col_for_time(blocks, ref)
        else:
            blocks = self._rows[row][1]
            if not blocks:
                return
            col = max(0, min(len(blocks) - 1, col + dc))
        self._select_block(row, col)

    def _step_row(self, row: int, dr: int) -> int | None:
        """The row *dr* rows from *row* - or, when that is a heading (with
        nothing to pick), the nearest one past it that has something, else
        the nearest before it; None when there is none between. At the
        board's edge, the edge row itself."""
        n = len(self._rows)
        if not n or not dr:
            return None
        step = 1 if dr > 0 else -1
        target = max(0, min(n - 1, row + dr))
        r = target
        while 0 <= r < n:
            self._ensure_row(r)
            if self._rows[r][1]:
                return r
            r += step
        r = target - step
        while r != row and 0 <= r < n:
            self._ensure_row(r)
            if self._rows[r][1]:
                return r
            r -= step
        return None

    def _select_block(self, row: int, col: int) -> None:
        self._ensure_row(row)
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
        if p is not None and p["stop_timestamp"] < time.time() \
                and self.window._timeshift_days(ch):
            self.window._play_timeshift(ch, prog=p)
            self.played.emit()
        else:
            self._tune(ch)

    def _tune(self, ch: dict) -> None:
        """Play *ch* live. Its window closes on it (``played``); the page
        stays, beside the player, with the channel marked."""
        self.window.tune_from_guide(ch)
        self.played.emit()

    def _refresh_guide(self) -> None:
        """Fetch the guide again and redraw the board when it is in."""
        if self._guide_poll.isActive():
            return
        self._refresh_pending = True
        self.refresh_btn.setEnabled(False)
        self.info.setText(tr("status_loading_programme_guide"))
        self.window._refresh_epg_now()
        self._guide_poll.start()

    def _after_refresh(self) -> None:
        if getattr(self.window, "_guide_loading", None) is not None:
            return
        self._guide_poll.stop()
        self._refresh_pending = False
        self.refresh_btn.setEnabled(True)
        self.info.setText(tr("epg_select_channel"))
        hbar = self.view.horizontalScrollBar()
        vbar = self.view.verticalScrollBar()
        h, v = hbar.value(), vbar.value()
        self._build()
        hbar.setValue(h)
        vbar.setValue(v)

    def _context_at(self, scene_pos, global_pos) -> None:
        data = self._block_at(scene_pos)
        m = QMenu(self)
        if not data:
            m.addAction(tr("btn_refresh_epg"), self._refresh_guide)
            m.exec(global_pos)
            return
        ch, p = data["channel"], data["prog"]
        self._selected = data
        self.play_btn.setEnabled(True)
        if p is None:
            # No-EPG channel: no programme to time-shift/record/remind about,
            # so the menu is "play it live" - and fetch the guide again.
            m.addAction(tr("epg_play_channel"), lambda: self._tune(ch))
            m.addSeparator()
            m.addAction(tr("btn_refresh_epg"), self._refresh_guide)
            m.exec(global_pos)
            return
        past = p["stop_timestamp"] < time.time()
        if past and self.window._timeshift_days(ch):
            m.addAction(tr("epg_play_this_programme"), self._play_selected)
        m.addAction(tr("ts_go_live") if past else tr("epg_play_channel"),
                    lambda: self._tune(ch))
        if ch.get("stream_id") is not None and p["stop_timestamp"] > time.time():
            m.addAction(tr("rec_record_programme"), lambda: self._record(ch, p))
            box = getattr(self.window, "_box_record_action", None)
            if box is not None:
                box(m, ch, p.get("title") or "", p["start_timestamp"], p["stop_timestamp"])
        # Every airing of it from now on - also from an episode already over.
        if (ch.get("stream_id") is not None and (p.get("title") or "").strip()
                and hasattr(self.window, "_record_series")):
            m.addAction(tr("series_record_menu"), lambda: self._record_series(ch, p))
            m.addAction(tr("series_remind_menu"),
                        lambda: self._record_series(ch, p, remind=True))
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
        m.addSeparator()
        m.addAction(tr("btn_refresh_epg"), self._refresh_guide)
        m.exec(global_pos)

    def _record(self, ch: dict, p: dict) -> None:
        # The detail panel's path, not a job of its own: a programme on the
        # air now is recorded from the stream already playing when it is the
        # channel on screen (one connection, and the prompt otherwise), where
        # a job here always opened a second - which a single-connection
        # account answers by cutting off the playback or the recording.
        self.window._record_programme(
            ch, {"title": p.get("title")},
            p["start_timestamp"], p["stop_timestamp"])
        self.info.setText("● " + (p.get("title") or ch.get("name") or ""))

    def _record_series(self, ch: dict, p: dict, remind: bool = False) -> None:
        if remind:
            rule = self.window._record_series(ch, p.get("title") or "", parent=self,
                                              remind=True)
        else:
            rule = self.window._record_series(ch, p.get("title") or "", parent=self)
        if rule:
            self.info.setText(("🔔 " if remind else "● ") + rule["text"])


class EpgGridDialog(QDialog):
    """The guide in a window of its own: an ``EpgGrid`` of its own, or the
    one the Guide page popped out (*grid*), handed back by release_grid()."""

    def __init__(self, window, channels=None, category_name=None,
                 grid: EpgGrid | None = None) -> None:
        super().__init__(window)
        self.window = window
        self.grid = grid if grid is not None else EpgGrid(
            window, channels or [], category_name)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.addWidget(self.grid)
        self.grid.set_in_window(True)
        self.grid.show()
        self.grid.played.connect(self.accept)
        self.grid.close_requested.connect(self.reject)
        self.setWindowTitle(self.grid.title())

        g = self.grid
        scr = self.screen().availableGeometry() if self.screen() else None
        # Sized by the board at the reference scale (the board then fits
        # whatever the window is).
        want_w = g.CH_COL_W + int((g._stop - g._start) / 60
                                  * g.PX_PER_MIN) + 40
        want_h = g.HEADER_H + len(g.channels) * g.ROW_H + 150
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

    def release_grid(self) -> EpgGrid:
        """Take the guide back out (for the Guide page to keep it)."""
        g = self.grid
        g.played.disconnect(self.accept)
        g.close_requested.disconnect(self.reject)
        self.layout().removeWidget(g)
        g.setParent(None)
        g.set_in_window(False)
        return g

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Keyboard navigation should work immediately - focus the grid, not
        # the filter field (typing still lands in the filter on click).
        self.grid.view.setFocus()
        # Centre in the screen once, after the window has its final size and a
        # real screen - doing it in __init__ (pre-show) doesn't stick on macOS.
        if getattr(self, "_centred", False):
            return
        self._centred = True
        mw = self.window
        if mw is not None and mw.isVisible():
            c = mw.frameGeometry().center()
            x, y = c.x() - self.width() // 2, c.y() - self.height() // 2
        else:
            scr0 = self.screen()
            geo = scr0.availableGeometry() if scr0 else None
            if geo is None:
                return
            x, y = geo.x() + (geo.width() - self.width()) // 2, \
                geo.y() + (geo.height() - self.height()) // 2
        # Keep it fully on-screen even when the window sits near an edge.
        scr = (mw.screen() if mw else None) or self.screen()
        if scr is not None:
            a = scr.availableGeometry()
            x = max(a.x(), min(x, a.x() + a.width() - self.width()))
            y = max(a.y(), min(y, a.y() + a.height() - self.height()))
        self.move(x, y)
