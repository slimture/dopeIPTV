"""Main application window: sidebar, channel list, detail panel, playback."""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import sys
import threading
import time
from datetime import datetime, timedelta

from PyQt6.QtCore import (
    QDateTime, QPointF, QRect, QRectF, QSettings, QSize, Qt, QThreadPool,
    QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QAction, QColor, QDesktopServices, QIcon, QKeySequence, QPainter,
    QPainterPath, QPen, QPixmap, QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDateTimeEdit, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from . import APP_NAME, ORG, VERSION
from .i18n import tr
from .channel_list import ChannelDelegate, ChannelListModel, ChannelListView
from .chromecast import CastDialog, ChromecastManager
from .client import XtreamClient, b64, epg_times
from .dialogs import (
    ContentManagerDialog, EpgGuideDialog, LoginDialog, PlaylistDialog,
)
from .embedded import EmbeddedPlayer
from .tmdb_match import TmdbMatchDialog
from .epg import XmltvGuide, epg_cache_path
from .metadata import PosterResolver, TmdbClient
from .players import (
    MpvIpcPlayer, MpvWindowPlayer, _libmpv, embedded_playback_reason,
    embedded_playback_supported, launch_player,
)
from .recording import RecordingManager, format_size, safe_filename
from .stores import (
    CategoryOverrides, ChannelOverrides, FavoriteStore, HistoryStore,
    ParentalControl, PlaylistStore, WatchedStore, WatchlistStore,
)
from .theme import ACCENT, ACCENTS, P, THEMES, apply_theme, build_style
from .trakt import TraktAuthError, TraktClient
from .wakelock import WakeLock
from .workers import (
    LogoLoader, choose_cover_url, clear_directory, default_image_cache_dir,
    dir_size_bytes, run_async, tmdb_url_from_provider)


class _ClickableWidget(QWidget):
    """Plain QWidget that emits clicked() on a left-button press."""

    clicked = pyqtSignal()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class _Toast(QLabel):
    """Non-intrusive overlay notification that fades away after a few seconds."""

    DURATION_MS = 3500

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            f"background: rgba(30,30,36,220); color: #ECECF1;"
            f"border-radius: 10px; padding: 10px 18px;"
            f"font-size: 12px; font-weight: 500;")
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._dismiss)

    def show_message(self, text: str, duration_ms: int = 0) -> None:
        self.setText(text)
        self.adjustSize()
        self.setFixedWidth(min(320, max(180, self.sizeHint().width() + 20)))
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        self._timer.start(duration_ms or self.DURATION_MS)

    def _place(self) -> None:
        p = self.parent()
        if p:
            self.move((p.width() - self.width()) // 2,
                      p.height() - self.height() - 30)

    def _dismiss(self) -> None:
        self.hide()


class _SidebarLogo(QWidget):
    """Themed mark at the top of the sidebar. A rounded accent pill with a
    play triangle on the left and three vertical audio/EQ bars on the right,
    like a stylised IPTV signal indicator. Wider than tall (roughly 2:1) so
    it fills the sidebar column nicely without a wordmark. Recolours live
    from ``P['accent']`` when the theme/accent changes (call ``update()``
    afterwards). Identical on Linux and macOS - no OS-specific paths."""

    LOGO_W = 92
    LOGO_H = 40

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(self.LOGO_H + 10)
        self.setToolTip(APP_NAME)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.LOGO_W), float(self.LOGO_H)
        x0 = (self.width() - w) / 2.0
        y0 = (self.height() - h) / 2.0
        accent = QColor(P.get("accent", "#4C8DFF"))
        # Rounded pill as the base.
        pill = QPainterPath()
        pill.addRoundedRect(x0, y0, w, h, h * 0.30, h * 0.30)
        painter.fillPath(pill, accent)

        # Left half: play triangle. Nudged right by a fraction so its
        # optical centre lines up with its geometric third of the pill.
        left_cx = x0 + w * 0.28
        cy = y0 + h * 0.50
        tri_h = h * 0.46
        tri_w = h * 0.42
        tri = QPainterPath()
        tri.moveTo(left_cx - tri_w * 0.55, cy - tri_h * 0.5)
        tri.lineTo(left_cx - tri_w * 0.55, cy + tri_h * 0.5)
        tri.lineTo(left_cx + tri_w * 0.55, cy)
        tri.closeSubpath()
        painter.fillPath(tri, QColor("white"))

        # Slim white divider between the play half and the signal half - a
        # subtle vertical rule that gives the mark structure.
        pen_div = QPen(QColor(255, 255, 255, 90))
        pen_div.setWidthF(1.2)
        painter.setPen(pen_div)
        div_x = x0 + w * 0.48
        painter.drawLine(QPointF(div_x, y0 + h * 0.22),
                         QPointF(div_x, y0 + h * 0.78))

        # Right half: three vertical bars of varying heights (the middle is
        # tallest), reading as an EQ / signal-strength indicator.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("white"))
        base_y = y0 + h * 0.72
        bar_w = h * 0.14
        heights = (h * 0.28, h * 0.46, h * 0.34)
        first_x = x0 + w * 0.58
        gap = h * 0.14
        for i, bh in enumerate(heights):
            bx = first_x + i * (bar_w + gap)
            painter.drawRoundedRect(
                QRectF(bx, base_y - bh, bar_w, bh),
                bar_w * 0.4, bar_w * 0.4)
        painter.end()


class MainWindow(QMainWindow):
    """Primary application window with sidebar, channel list, and detail panel."""

    epg_progress = pyqtSignal(int)

    def __init__(self, client: XtreamClient, settings: QSettings,
                 playlists: PlaylistStore | None = None) -> None:
        super().__init__()
        self.client = client
        self.settings = settings
        self.playlist_store = playlists
        active_pl = playlists.active() if playlists else None
        self.pool = QThreadPool.globalInstance()
        # 320 px covers the biggest cell we render (xlarge grid uses a 200 px
        # logo, and a HiDPI screen doubles the pixel budget), so channel
        # logos stay crisp even when the user picks large/xlarge grid. The
        # default 96 was fine when only compact/medium existed but blurred
        # noticeably on 4K displays.
        # Dedicated thread pools per image kind so scrolling through a
        # big grid never starves the shared pool that also runs the
        # category / channel / EPG API calls - that starvation is what
        # made "Loading categories" hang when a Movies category loaded
        # hundreds of poster fetches at once.
        # The two loaders share the same on-disk cache directory: they
        # cache the raw response bytes (not the scaled pixmap), so a
        # cover the detail panel already fetched is instantly usable
        # by the list delegate without another network round-trip.
        # That also matters for the dead-URL fallback: if `poster_art`
        # ever succeeded on a URL, the disk file exists, so `logos`
        # can serve it without hitting the network - which is what
        # went wrong when the two dirs were separate and a transient
        # 500 on one loader made the delegate fall back to an empty
        # `stream_icon` even though the detail panel had the artwork.
        shared_image_dir = default_image_cache_dir("images")
        self._logo_pool = QThreadPool()
        self._logo_pool.setMaxThreadCount(4)
        self.logos = LogoLoader(self._logo_pool, max_size=320,
                                cache_dir=shared_image_dir)
        # A poster plus up to 8 cast photos is up to 9 concurrent
        # downloads per selection, hence the separate pool + higher-res
        # cache (reusing `logos` blurs on the big detail-panel sizes).
        self._art_pool = QThreadPool()
        self._art_pool.setMaxThreadCount(4)
        self.poster_art = LogoLoader(
            self._art_pool, max_size=320,
            cache_dir=shared_image_dir)
        # A URL that fails on one loader may succeed on the other (or
        # the reverse); share the dead-URL blacklist so the delegate's
        # fallback logic isn't inconsistent between list and detail
        # panel for the same movie.
        self.poster_art.dead = self.logos.dead
        self.epg_progress.connect(self._on_epg_progress)
        pid = (active_pl or {}).get("id")
        self.xmltv = XmltvGuide(
            client, (active_pl or {}).get("epg_url") or None,
            cache_path=epg_cache_path(pid) if pid else None,
            progress_cb=self.epg_progress.emit)
        self.xmltv.delay_minutes = self._epg_delay_minutes()
        self.favs = FavoriteStore(
            settings, f"favorites_{pid}" if pid else "favorites")
        self.history = HistoryStore(
            settings, f"history_{pid}" if pid else "history")
        self._resume_key = f"resume_positions_{pid}" if pid else "resume_positions"
        try:
            self._resume: dict = json.loads(
                settings.value(self._resume_key, "") or "{}")
        except Exception:
            self._resume = {}
        if not isinstance(self._resume, dict):
            self._resume = {}
        self.overrides = CategoryOverrides(
            settings, f"category_overrides_{pid}" if pid else "category_overrides")
        self.channel_ov = ChannelOverrides(
            settings, f"channel_overrides_{pid}" if pid else "channel_overrides")
        self.parental = ParentalControl(settings)
        self.cast = ChromecastManager()
        self.rec = RecordingManager(settings, self)
        self.rec.jobs_changed.connect(self._recordings_changed)
        self.rec.recording_stopped.connect(self._on_recording_stopped)
        self.wake = WakeLock()
        self._full_catalog: list | None = None
        self.tmdb: PosterResolver | None = None
        self._prefer_tmdb_covers = False
        self._tmdb_pool: QThreadPool | None = None
        self._poster_refresh_timer = QTimer(self)
        self._poster_refresh_timer.setSingleShot(True)
        self._poster_refresh_timer.timeout.connect(self._flush_poster_refresh)
        self._init_metadata_provider()
        self.trakt = TraktClient(settings)
        self._trakt_active: dict | None = None
        self.watched = WatchedStore(settings)
        self.watchlist = WatchlistStore(settings)
        self._watched_sync_running = False
        self._raw_categories: list = []
        self.mpv = MpvIpcPlayer()
        self.mpv_window = MpvWindowPlayer() if _libmpv is not None else None
        if self.mpv_window:
            self.mpv_window.zap_requested.connect(self._zap)
            self.mpv_window.playback_error.connect(self._playback_error)
            self.mpv_window.closed.connect(lambda: self.wake.release())
        if not settings.value("playback_mode_v2"):
            settings.remove("playback_mode")
            settings.setValue("playback_mode_v2", "1")
        self.mode: str = "live"
        self.all_items: list = []
        self.series_ctx = None
        self._info_cache: dict = {}
        self._current_key = None
        self._playing_key = None
        self._playing_group: str | None = None
        self._playing_item = None
        self._pip_win = None
        self._last_player = None
        self._last_playlist_refresh = time.time()
        self._load_gen = 0

        self._base_title = (active_pl or {}).get("name", "")
        self.setWindowTitle(self._base_title)
        self.resize(1240, 780)
        self._build_ui()
        # Restore the window size from last session (falls back to the default
        # above on first run). The panel dividers are restored once more after
        # the window is shown at its real size - see _restore_splitter_state -
        # so their proportions don't drift when the geometry is applied.
        from PyQt6.QtCore import QByteArray
        geo = self.settings.value("window_geometry")
        if isinstance(geo, QByteArray) and geo.size() > 0:
            self.restoreGeometry(geo)
        QTimer.singleShot(0, self._restore_splitter_state)
        self.loading_bar.show()
        self._set_status(tr("status_loading_channels"))
        QTimer.singleShot(100, self._load_categories)
        # Cross-device sync of watched movies/episodes from Trakt. Deferred
        # so the initial category/EPG traffic goes first - the sync runs
        # for the full account which can take a couple of seconds.
        QTimer.singleShot(2500, self._maybe_sync_watched)

        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.timeout.connect(self._maybe_auto_refresh)
        self._auto_refresh_timer.start(5 * 60_000)

    # -- UI construction -------------------------------------------------------

    def _build_ui(self) -> None:
        menubar = self.menuBar()
        app_menu = menubar.addMenu(APP_NAME)
        settings_action = app_menu.addAction(tr("btn_settings") + "…")
        settings_action.triggered.connect(self.open_settings)
        refresh_action = app_menu.addAction(tr("menu_refresh_playlist"))
        refresh_action.triggered.connect(self.refresh_playlist)
        app_menu.addSeparator()
        about_action = app_menu.addAction(tr("menu_about"))
        about_action.triggered.connect(self.show_about)
        quit_action = app_menu.addAction(tr("menu_quit"))
        quit_action.triggered.connect(self.close)
        # On macOS these roles move the items into the standard application
        # menu (the bold "dopeIPTV" menu next to the Apple logo), which is
        # where a Mac user expects About / Settings / Quit. The role is a
        # no-op on Linux/Windows, so the GNOME menu is unchanged.
        about_action.setMenuRole(QAction.MenuRole.AboutRole)
        settings_action.setMenuRole(QAction.MenuRole.PreferencesRole)
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        # Kept for live language switching (see retranslate_ui).
        self._i18n_actions = {
            settings_action: lambda: tr("btn_settings") + "…",
            refresh_action: lambda: tr("menu_refresh_playlist"),
            about_action: lambda: tr("menu_about"),
            quit_action: lambda: tr("menu_quit"),
        }

        root = QSplitter(Qt.Orientation.Horizontal)
        root.setHandleWidth(6)
        self.setCentralWidget(root)

        # Sidebar
        side = QWidget(objectName="Sidebar")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 16, 12, 12)
        sl.setSpacing(4)

        # Small themed logo at the top of the sidebar (recolours with theme).
        self._sidebar_logo = _SidebarLogo()
        sl.addWidget(self._sidebar_logo)
        sl.addSpacing(6)

        self.nav_btns: dict[str, QPushButton] = {}
        for key, text in (("live", tr("nav_tv")), ("vod", tr("nav_movies")),
                          ("series", tr("nav_series")), ("fav", tr("nav_favorites")),
                          ("watchlist", tr("nav_watchlist")),
                          ("rec", tr("nav_recordings")), ("history", tr("nav_history"))):
            b = QPushButton(text, objectName="NavBtn")
            b.setCheckable(True)
            b.setFlat(True)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda _, k=key: self.switch_mode(k))
            sl.addWidget(b)
            self.nav_btns[key] = b
        self.nav_btns["live"].setChecked(True)

        self._cat_section_label = QLabel(
            tr("sidebar_categories"), objectName="SectionLabel")
        sl.addWidget(self._cat_section_label)
        self.cat_list = QListWidget()
        self.cat_list.currentItemChanged.connect(self._category_changed)
        self.cat_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.cat_list.customContextMenuRequested.connect(self._cat_menu)
        sl.addWidget(self.cat_list, 1)

        self._guide_btn = guide_btn = QPushButton(tr("btn_epg_guide"))
        guide_btn.clicked.connect(self._open_epg_guide)
        sl.addWidget(guide_btn)

        self._refresh_btn = refresh_btn = QPushButton(tr("btn_refresh"))
        refresh_btn.setToolTip(tr("tooltip_reload_channels_epg"))
        refresh_btn.clicked.connect(self.refresh_playlist)
        sl.addWidget(refresh_btn)

        self._settings_btn = settings_btn = QPushButton(tr("btn_settings"))
        settings_btn.clicked.connect(self.open_settings)
        sl.addWidget(settings_btn)

        # Middle column
        mid = QWidget(objectName="MiddlePane")
        ml = QVBoxLayout(mid)
        # No horizontal margins on the middle pane. Qt's IconMode reserves
        # ~16 px at the end of each row for its internal wrap check, and any
        # extra inset on either side compounds with that - even 6-8 px of
        # margin was enough to push the last column onto the next row,
        # leaving a huge gap on the right (see channel_list._justify_grid).
        ml.setContentsMargins(0, 14, 0, 10)
        ml.setSpacing(10)

        self.loading_bar = QProgressBar(objectName="LoadBar")
        self.loading_bar.setRange(0, 0)
        self.loading_bar.setTextVisible(False)
        self.loading_bar.hide()
        ml.addWidget(self.loading_bar)

        self.search = QLineEdit(objectName="Search")
        self.search.setPlaceholderText(tr("search_placeholder"))
        self.search.setClearButtonEnabled(True)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._play_preview)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_filter)
        self.search.textChanged.connect(lambda _t: self._search_timer.start(220))
        ml.addWidget(self.search)

        ctl = QHBoxLayout()
        ctl.setSpacing(6)
        self.size_box = self._combo(
            [("compact", tr("option_compact")), ("medium", tr("option_medium")),
             ("large", tr("option_large")), ("xlarge", tr("option_xlarge"))],
            self.settings.value("view_density", "medium"))
        self.size_box.setObjectName("InlineCombo")
        self.size_box.currentIndexChanged.connect(self._inline_view_changed)
        self.sort_box = self._combo(
            [("default", tr("label_default")), ("alpha_asc", "A→Z"),
             ("alpha_desc", "Z→A"), ("recent", tr("label_recent"))],
            self.settings.value("sort_order", "default"))
        self.sort_box.setObjectName("InlineCombo")
        self.sort_box.currentIndexChanged.connect(self._inline_view_changed)
        self.grid_btn = QPushButton(tr("btn_grid"), objectName="InlineToggle")
        self.grid_btn.setCheckable(True)
        self.grid_btn.setChecked(
            self.settings.value("view_grid", "false") == "true")
        self.grid_btn.toggled.connect(self._inline_view_changed)
        self._size_label = QLabel(tr("label_size"))
        self._sort_label = QLabel(tr("label_sort"))
        ctl.addWidget(self._size_label)
        ctl.addWidget(self.size_box)
        ctl.addWidget(self._sort_label)
        ctl.addWidget(self.sort_box)
        ctl.addStretch()
        ctl.addWidget(self.grid_btn)
        ml.addLayout(ctl)

        self.back_btn = QPushButton("<-  Back to series")
        self.back_btn.hide()
        self.back_btn.clicked.connect(self._leave_series)
        ml.addWidget(self.back_btn)

        self.clear_history_btn = QPushButton(tr("msg_clear_history_title"))
        self.clear_history_btn.hide()
        self.clear_history_btn.clicked.connect(self._clear_history)
        ml.addWidget(self.clear_history_btn)

        self.listw = ChannelListView(objectName="Channels")
        self.list_model = ChannelListModel()
        self.listw.setModel(self.list_model)
        self.delegate = ChannelDelegate(
            self, self.settings.value("view_density", "medium"))
        self.listw.setItemDelegate(self.delegate)
        self.listw.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.listw.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.listw.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.listw.setUniformItemSizes(True)
        self.listw.setMouseTracking(True)
        self.listw.selectionModel().currentChanged.connect(
            self._on_current_changed)
        self.listw.doubleClicked.connect(lambda _idx: self.play())
        self.listw.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.listw.customContextMenuRequested.connect(self._context_menu)
        ml.addWidget(self.listw, 1)

        self._loading_hint = QLabel(tr("status_loading_channels"))
        self._loading_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_hint.setStyleSheet(
            f"color:{P['muted2']}; font-size:13px; padding:40px 0;")
        ml.addWidget(self._loading_hint)

        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet(f"color:{P['muted3']}; font-size:11px;")
        status_row = QHBoxLayout()
        status_row.addWidget(self.count_lbl, 1)
        self.rec_indicator = QPushButton("● REC")
        self.rec_indicator.setFlat(True)
        self.rec_indicator.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rec_indicator.setStyleSheet(
            f"color:{P['rec']}; font-weight:700; font-size:11px;"
            "border:none; background:transparent; padding:0 4px;")
        self.rec_indicator.clicked.connect(self._rec_indicator_menu)
        self.rec_indicator.hide()
        status_row.addWidget(self.rec_indicator)
        ml.addLayout(status_row)

        # Detail panel
        det = QWidget(objectName="DetailPane")
        dl = QVBoxLayout(det)
        dl.setContentsMargins(20, 22, 20, 18)
        dl.setSpacing(12)

        self.player: EmbeddedPlayer | None = None
        if embedded_playback_supported():
            self.player = EmbeddedPlayer(settings=self.settings)
            self.player.hide()
            self.player.fs_btn.clicked.connect(self._toggle_player_fullscreen)
            self.player.double_clicked.connect(self._toggle_player_fullscreen)
            self.player.exit_fullscreen.connect(self._exit_player_fullscreen)
            self.player.timeshift_menu.connect(self._player_timeshift_menu)
            self.player.record_menu.connect(self._player_record_menu)
            self.rec.stop_inplayer_cb = self.player.stop_stream_record
            self.player.stop_btn.clicked.connect(
                lambda: self.rec.finish_all_inplayer("playback stopped"))
            self.player.stop_btn.clicked.connect(
                lambda: self.wake.release())
            self.player.stop_btn.clicked.connect(self._trakt_stop_current)
            self.player.playback_error.connect(self._playback_error)
            self.player.zap.connect(self._zap)
            self.player.pip_requested.connect(self._toggle_pip)
            self.player.pip_context_menu.connect(self._pip_context_menu)
            self.player.stop_btn.clicked.connect(self._exit_pip_if_active)
            self.player.stopped.connect(self._on_player_stopped)
            self.player.resume_requested.connect(self._resume_last)
            # Keep the player pane visible on stop - mpv clears to black -
            # instead of hiding it, so the window just goes black.
            dl.addWidget(self.player, 1)

        self.stream_error = QLabel("")
        self.stream_error.setStyleSheet(
            f"color:{P['error']}; font-size:12px;")
        self.stream_error.setWordWrap(True)
        self.stream_error.hide()
        dl.addWidget(self.stream_error)

        self._detail_name = tr("detail_select_something")

        header_row = QHBoxLayout()
        header_row.setSpacing(14)

        left_col = QVBoxLayout()
        left_col.setSpacing(8)
        self.d_logo = QLabel()
        self.d_logo.setFixedSize(84, 84)
        self.d_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.d_logo.setStyleSheet(
            f"background:{P['sel']}; border-radius:18px; "
            "font-size:30px; font-weight:700;")
        # Pin every item in this column to the left edge. Without an
        # explicit alignment a fixed-size widget (the poster) is centered
        # in the column's full width, which is what made the poster look
        # centered under the mini player instead of left-aligned.
        left_col.addWidget(self.d_logo, 0, Qt.AlignmentFlag.AlignLeft)

        # Movie/series rating, linked to IMDb when TMDB has the id. Sits
        # directly under the poster with no card/border around it - the
        # rating is a single line of text and doesn't need its own box.
        self.media_rating_lbl = QLabel("")
        self.media_rating_lbl.setOpenExternalLinks(True)
        self.media_rating_lbl.setStyleSheet("font-size:13px; font-weight:600;")
        self.media_rating_lbl.hide()
        left_col.addWidget(self.media_rating_lbl, 0, Qt.AlignmentFlag.AlignLeft)

        self.play_mpv = QPushButton("▶  " + tr("btn_play"), objectName="Primary")
        self.play_mpv.setToolTip(tr("tooltip_play_in_mpv"))
        self.play_mpv.setSizePolicy(QSizePolicy.Policy.Fixed,
                                    QSizePolicy.Policy.Fixed)
        self.play_mpv.clicked.connect(lambda: self.play("mpv"))
        left_col.addWidget(self.play_mpv, 0, Qt.AlignmentFlag.AlignLeft)
        left_col.addStretch(1)
        header_row.addLayout(left_col)

        # "Now playing" sits beside the logo instead of stacked below it -
        # the channel name is already visible in the middle list, the
        # window title bar, and the mini player's own control bar.
        self.now_card = QFrame(objectName="Card")
        nc = QVBoxLayout(self.now_card)
        nc.setContentsMargins(16, 14, 16, 14)
        nc.setSpacing(8)
        self.now_time = QLabel("", objectName="NowTime")
        self.now_title = QLabel("", objectName="NowTitle")
        self.now_title.setWordWrap(True)
        self.now_bar = QProgressBar(objectName="EpgBar")
        self.now_bar.setTextVisible(False)
        self.now_bar.setRange(0, 100)
        self.now_desc = QLabel("", objectName="NowDesc")
        self.now_desc.setWordWrap(True)
        for w in (self.now_time, self.now_title, self.now_bar, self.now_desc):
            nc.addWidget(w)
        self.now_card.hide()
        header_row.addWidget(self.now_card, 1, Qt.AlignmentFlag.AlignTop)

        # Movie/series synopsis + metadata, shown to the *right* of the
        # poster (only one of now_card / media_info is ever visible, since
        # live channels use now_card and VOD/series use this). Its height is
        # pinned to the poster's height in _show_detail so the box bottom
        # lines up with the poster's bottom, with the text scrolling inside.
        self.media_info = QScrollArea()
        self.media_info.setWidgetResizable(True)
        self.media_info.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.media_info.setStyleSheet(
            f"QScrollArea {{ background:{P['input']}; "
            f"border:1px solid {P['border_in']}; border-radius:12px; }}")
        mi_holder = QWidget()
        mi = QVBoxLayout(mi_holder)
        mi.setContentsMargins(16, 14, 16, 14)
        mi.setSpacing(8)
        self.media_plot = QLabel("", objectName="NowDesc")
        self.media_plot.setWordWrap(True)
        self.media_plot.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.media_meta = QLabel("", objectName="DetailMeta")
        self.media_meta.setWordWrap(True)
        self.media_meta.setTextFormat(Qt.TextFormat.RichText)
        self.media_meta.setOpenExternalLinks(False)
        self.media_meta.linkActivated.connect(self._on_cast_link)
        mi.addWidget(self.media_plot)
        mi.addWidget(self.media_meta)
        mi.addStretch(1)
        self.media_info.setWidget(mi_holder)
        self.media_info.hide()
        # Top-aligned with the poster; its fixed height (set per-selection in
        # _show_detail to the poster height) makes the bottoms line up too.
        header_row.addWidget(self.media_info, 1, Qt.AlignmentFlag.AlignTop)
        header_row.setAlignment(Qt.AlignmentFlag.AlignTop)
        dl.addLayout(header_row)

        self.cast_scroll = QScrollArea()
        self.cast_scroll.setWidgetResizable(True)
        self.cast_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.cast_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.cast_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cast_scroll.setFixedHeight(110)
        self.cast_scroll.setStyleSheet(
            "QScrollBar:horizontal { height: 5px; background: transparent; }"
            "QScrollBar::handle:horizontal { border-radius: 2px; }"
            "QScrollBar::add-line:horizontal, "
            "QScrollBar::sub-line:horizontal { width: 0; }"
        )
        self.cast_holder = QWidget()
        self.cast_lay = QHBoxLayout(self.cast_holder)
        self.cast_lay.setContentsMargins(2, 2, 2, 2)
        self.cast_lay.setSpacing(12)
        self.cast_lay.addStretch()
        self.cast_scroll.setWidget(self.cast_holder)
        self.cast_scroll.hide()
        dl.addWidget(self.cast_scroll)

        self.epg_scroll = QScrollArea()
        self.epg_scroll.setWidgetResizable(True)
        self.epg_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.epg_holder = QWidget()
        self.epg_lay = QVBoxLayout(self.epg_holder)
        self.epg_lay.setContentsMargins(0, 0, 0, 0)
        self.epg_lay.setSpacing(8)
        self.epg_lay.addStretch()
        self.epg_scroll.setWidget(self.epg_holder)
        dl.addWidget(self.epg_scroll, 1)

        root.addWidget(side)
        root.addWidget(mid)
        root.addWidget(det)
        root.setSizes([220, 560, 380])
        root.setCollapsible(0, False)
        root.setCollapsible(2, False)
        root.setStretchFactor(0, 0)
        root.setStretchFactor(1, 1)
        root.setStretchFactor(2, 0)
        # Save the panel layout every time the user drags a divider, so it
        # persists even if closeEvent doesn't run (Ctrl+C, force quit, sudden
        # kill). The window geometry is saved via moveEvent/resizeEvent below.
        root.splitterMoved.connect(self._schedule_save_layout)
        det.setMinimumWidth(280)
        self._side, self._mid, self._det = side, mid, det
        self._root = root
        self._toast = _Toast(root)

        self.tick = QTimer(self)
        self.tick.timeout.connect(self._refresh_progress)
        self.tick.start(60_000)

        # Periodically remember the playback position of movies/episodes so
        # they can be resumed later even if the app is closed abruptly.
        self._resume_timer = QTimer(self)
        self._resume_timer.timeout.connect(self._save_resume_position)
        self._resume_timer.start(12_000)
        self._current_epg = None
        self._player_fs = False

        QShortcut(QKeySequence("Ctrl+Right"), self,
                  activated=lambda: self._zap(1))
        QShortcut(QKeySequence("Ctrl+Left"), self,
                  activated=lambda: self._zap(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self,
                  activated=self._exit_player_fullscreen)
        QShortcut(QKeySequence(Qt.Key.Key_F), self,
                  activated=self._toggle_fullscreen_shortcut)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self,
                  activated=self._delete_pressed)
        QShortcut(QKeySequence(Qt.Key.Key_Space), self,
                  activated=self._toggle_pause_shortcut)

        self._apply_view_settings()

    def _toggle_pause_shortcut(self) -> None:
        if self.player and self.player.isVisible():
            self.player.toggle_pause()

    # -- fullscreen ----------------------------------------------------------------

    def _toggle_fullscreen_shortcut(self) -> None:
        if self.player and self.player.isVisible():
            self._toggle_player_fullscreen()
        elif self.mpv_window and self.mpv_window.is_active():
            self.mpv_window.toggle_fullscreen()

    def _toggle_player_fullscreen(self) -> None:
        if not self.player or not self.player.isVisible():
            return
        now = time.time()
        if now - getattr(self, "_fs_toggled_at", 0.0) < 0.4:
            return
        self._fs_toggled_at = now
        if self._pip_win is not None:
            self._toggle_pip_fullscreen()
            return
        if self._player_fs:
            self._exit_player_fullscreen()
            return
        self._player_fs = True
        self._fs_return_index = self.listw.currentIndex()
        self._fs_return_scroll = self.listw.verticalScrollBar().value()
        self._side.hide()
        self._mid.hide()
        self._det_hidden: list[QWidget] = []
        for w in self._det.children():
            if (isinstance(w, QWidget) and w is not self.player
                    and w.isVisible()):
                self._det_hidden.append(w)
                w.hide()
        det_lay = self._det.layout()
        self._det_margins = det_lay.contentsMargins()
        det_lay.setContentsMargins(0, 0, 0, 0)
        self._det.setStyleSheet(
            "#DetailPane { background:#000000; border:none; }")
        self.menuBar().hide()
        self.player.set_fullscreen_ui(True)
        self._was_fullscreen = self.isFullScreen()
        self.showFullScreen()

    def _exit_player_fullscreen(self) -> None:
        if not self._player_fs:
            if self.isFullScreen():
                self.showNormal()
            return
        self._player_fs = False
        self._side.show()
        self._mid.show()
        for w in getattr(self, "_det_hidden", []):
            w.show()
        self._det_hidden = []
        m = getattr(self, "_det_margins", None)
        if m is not None:
            self._det.layout().setContentsMargins(
                m.left(), m.top(), m.right(), m.bottom())
        self._det.setStyleSheet("")
        self.menuBar().show()
        if not getattr(self, "_was_fullscreen", False):
            self.showNormal()
        # Restore the window geometry *before* unlocking the video's
        # fixed height - _lock_video_box() reads the player's current
        # size, and computing it while the window is still fullscreen-
        # sized bakes in a wrong height that a later resize doesn't
        # reliably clear (the same class of bug as the PiP letterboxing).
        self.player.set_fullscreen_ui(False)
        idx = getattr(self, "_fs_return_index", None)
        scroll = getattr(self, "_fs_return_scroll", None)
        if idx is not None and idx.isValid():
            QTimer.singleShot(0, lambda: (
                self.listw.setCurrentIndex(idx),
                self.listw.scrollTo(
                    idx, QAbstractItemView.ScrollHint.PositionAtCenter)))
        elif scroll is not None:
            QTimer.singleShot(0, lambda: (
                self.listw.verticalScrollBar().setValue(scroll)))

    def _toggle_pip_fullscreen(self) -> None:
        if self.isFullScreen():
            self.setWindowFlags(
                Qt.WindowType.Tool
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint)
            self.showNormal()
            geo = getattr(self, "_pip_fs_geo", None)
            if not geo:
                self.resize(480, 270)
                screen_geo = self.screen().availableGeometry()
                self.move(screen_geo.right() - 500,
                          screen_geo.bottom() - 290)
                geo = self.geometry()
            # Restore the small PiP geometry *before* unlocking the video's
            # fixed height, so _lock_video_box() sizes it to the final PiP
            # dimensions instead of the still-fullscreen ones.
            self.player.set_fullscreen_ui(False)
            self.player.set_pip_mode(True)
            # Changing window flags recreates the native window on X11, and
            # the WM re-places the fresh frameless window (usually centered)
            # asynchronously. Setting the geometry once here races with that,
            # so also re-assert it on the next event-loop turns until it
            # sticks at the remembered PiP position.
            self.setGeometry(geo)
            for delay in (0, 30, 120):
                QTimer.singleShot(
                    delay, lambda g=geo: self._pip_win is not None
                    and not self.isFullScreen() and self.setGeometry(g))
        else:
            self._pip_fs_geo = self.geometry()
            self.setWindowFlags(Qt.WindowType.Window)
            self.player.set_fullscreen_ui(True)
            self.showFullScreen()

    # -- picture-in-picture (mini mode — no reparenting) ----------------------------

    def _toggle_pip(self) -> None:
        if not self.player:
            return
        if self._pip_win is not None:
            self._exit_pip()
            return
        if self._player_fs:
            self._exit_player_fullscreen()

        self._pip_win = True
        self._pip_geo = self.geometry()
        self._pip_state = self.windowState()

        self._side.hide()
        self._mid.hide()
        self._pip_det_hidden: list[QWidget] = []
        for w in self._det.children():
            if (isinstance(w, QWidget) and w is not self.player
                    and w.isVisible()):
                self._pip_det_hidden.append(w)
                w.hide()
        det_lay = self._det.layout()
        self._pip_margins = det_lay.contentsMargins()
        det_lay.setContentsMargins(0, 0, 0, 0)
        self._det.setStyleSheet(
            "#DetailPane { background:#000000; border:none; }")
        self.menuBar().hide()
        self.player.pip_btn.setToolTip(tr("tooltip_exit_pip"))
        self.player.fs_btn.hide()
        self.player.video.setMinimumHeight(0)
        self.player.set_pip_mode(True)

        if self.isFullScreen() or self.isMaximized():
            self.showNormal()
        self.setMinimumSize(240, 135)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint)
        self.show()
        # Restore the last PiP position/size, else default to bottom-right.
        geo = self._saved_pip_geometry()
        if geo is None:
            screen_geo = self.screen().availableGeometry()
            geo = QRect(screen_geo.right() - 500, screen_geo.bottom() - 290,
                        480, 270)
        self.setGeometry(geo)
        # Re-assert geometry + stacking across the next event-loop turns:
        # changing window flags recreates the native window on X11 and the WM
        # may otherwise re-place or lower it.
        for delay in (0, 40, 150):
            QTimer.singleShot(delay, lambda g=geo: self._pip_win is not None
                              and (self.setGeometry(g), self.raise_()))

    def _saved_pip_geometry(self) -> "QRect | None":
        raw = self.settings.value("pip_geometry", "")
        try:
            x, y, w, h = (int(v) for v in str(raw).split(","))
        except (ValueError, TypeError):
            return None
        if w < 200 or h < 120:
            return None
        return QRect(x, y, w, h)

    def _exit_pip(self) -> None:
        if self._pip_win is None:
            return
        # Remember the PiP window's position/size for next time.
        g = self.geometry()
        self.settings.setValue(
            "pip_geometry", f"{g.x()},{g.y()},{g.width()},{g.height()}")
        self._pip_win = None

        self.player.set_pip_mode(False)
        self.player.video.setMinimumHeight(190)
        self.setMinimumSize(0, 0)
        self._side.show()
        self._mid.show()
        for w in getattr(self, "_pip_det_hidden", []):
            w.show()
        self._pip_det_hidden = []
        m = getattr(self, "_pip_margins", None)
        if m is not None:
            self._det.layout().setContentsMargins(
                m.left(), m.top(), m.right(), m.bottom())
        self._det.setStyleSheet("")
        self.menuBar().show()
        self.player.pip_btn.setToolTip(tr("tooltip_pip"))
        self.player.fs_btn.show()

        self.setWindowFlags(
            Qt.WindowType.Window)
        self.show()
        geo = getattr(self, "_pip_geo", None)
        state = getattr(self, "_pip_state", None)
        if geo:
            self.setGeometry(geo)
        if state and state != Qt.WindowState.WindowNoState:
            self.setWindowState(state)

    def _pip_context_menu(self, global_pos) -> None:
        if self._pip_win is None:
            return
        m = QMenu(self)
        m.addAction(tr("tooltip_exit_pip"), self._exit_pip)
        m.exec(global_pos)

    def _exit_pip_if_active(self) -> None:
        if self._pip_win is not None:
            self._exit_pip()

    # -- playlists -----------------------------------------------------------------

    REFRESH_SECONDS = {
        "2h": 2 * 3600, "6h": 6 * 3600, "12h": 12 * 3600,
        "24h": 24 * 3600, "1w": 7 * 24 * 3600,
    }

    def _maybe_auto_refresh(self) -> None:
        pl = self.playlist_store.active() if self.playlist_store else None
        secs = self.REFRESH_SECONDS.get((pl or {}).get("refresh", ""))
        if secs and time.time() - self._last_playlist_refresh >= secs:
            self.refresh_playlist()

    def refresh_playlist(self) -> None:
        self._last_playlist_refresh = time.time()
        pl = self.playlist_store.active() if self.playlist_store else None
        pid = (pl or {}).get("id")
        self.xmltv = XmltvGuide(
            self.client, (pl or {}).get("epg_url") or None,
            cache_path=epg_cache_path(pid) if pid else None,
            progress_cb=self.epg_progress.emit)
        self._info_cache.clear()
        self._set_status(tr("status_refreshing_playlist"))
        self._show_toast(tr("status_refreshing_playlist"))
        self._load_categories()
        run_async(
            self.pool, lambda: self.xmltv.ensure_loaded(force=True),
            lambda ok: (self._epg_progress_finished(),
                        self.list_model.refresh_all() if ok else None),
            lambda _: self._epg_progress_finished())

    def switch_playlist(self, pid: str) -> None:
        pl = self.playlist_store.get(pid) if self.playlist_store else None
        if not pl:
            return
        self.loading_bar.show()
        self._set_status(tr("status_connecting", name=pl['name']))
        self._show_toast(tr("status_connecting", name=pl['name']))
        candidate = XtreamClient(pl["server"], pl["username"], pl["password"])

        def done(_auth):
            self.loading_bar.hide()
            self.playlist_store.set_active(pid)
            self.client = candidate
            self.favs = FavoriteStore(self.settings, f"favorites_{pid}")
            self.history = HistoryStore(self.settings, f"history_{pid}")
            self._base_title = pl["name"]
            self.setWindowTitle(self._base_title)
            self.refresh_playlist()

        def fail(msg):
            self.loading_bar.hide()
            self._set_status("")
            QMessageBox.warning(
                self, tr("playlist_msg_title"),
                tr("msg_could_not_connect", name=pl['name'], msg=msg))

        run_async(self.pool, candidate.authenticate, done, fail)

    # -- modes and categories ------------------------------------------------------

    def switch_mode(self, mode: str) -> None:
        for k, b in self.nav_btns.items():
            b.setChecked(k == mode)
        self.mode = mode
        self.series_ctx = None
        self.back_btn.hide()
        self.clear_history_btn.setVisible(mode == "history")
        self.listw.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
            if mode in ("history", "rec")
            else QAbstractItemView.SelectionMode.SingleSelection)
        self.search.clear()
        self._load_categories()

    def _load_categories(self) -> None:
        self._load_gen += 1
        gen = self._load_gen
        self.cat_list.clear()
        self.list_model.set_items([], self.mode)
        if self.mode == "rec":
            self.cat_list.blockSignals(True)
            for label, data in [("All recordings", None),
                                ("Active & scheduled", "__jobs__"),
                                ("Upcoming", "__upcoming__")]:
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, data)
                self.cat_list.addItem(item)
            for rel in self.rec.folders():
                item = QListWidgetItem(rel)
                item.setData(Qt.ItemDataRole.UserRole, rel)
                self.cat_list.addItem(item)
            self.cat_list.blockSignals(False)
            self.cat_list.setCurrentRow(0)
            return
        if self.mode in ("fav", "history"):
            self.cat_list.blockSignals(True)
            all_item = QListWidgetItem(tr("cat_all"))
            all_item.setData(Qt.ItemDataRole.UserRole, None)
            self.cat_list.addItem(all_item)
            if self.mode == "fav":
                for g in self.favs.group_names():
                    locked = (self.favs.is_locked(g)
                              and not self.parental.session_unlocked)
                    it = QListWidgetItem(f"{g}  [locked]" if locked else g)
                    it.setData(Qt.ItemDataRole.UserRole, g)
                    self.cat_list.addItem(it)
            self.cat_list.blockSignals(False)
            self.cat_list.setCurrentRow(0)
            return
        if self.mode == "watchlist":
            # Watch Later has two sub-categories - Movies and Series -
            # mirroring the two Trakt watchlist endpoints. 'All' up
            # top shows both stacked, movies first.
            self.cat_list.blockSignals(True)
            for label, data in [
                    (tr("cat_all"), None),
                    (tr("nav_movies"), "movies"),
                    (tr("nav_series"), "series")]:
                it = QListWidgetItem(label)
                it.setData(Qt.ItemDataRole.UserRole, data)
                self.cat_list.addItem(it)
            self.cat_list.blockSignals(False)
            self.cat_list.setCurrentRow(0)
            return
        self.loading_bar.show()
        self._set_status(tr("status_loading_categories"))
        fn = {"live": self.client.live_categories,
              "vod": self.client.vod_categories,
              "series": self.client.series_categories}[self.mode]
        request_mode = self.mode

        def done(cats):
            if gen != self._load_gen or self.mode != request_mode:
                return
            self.loading_bar.hide()
            self._raw_categories = cats or []
            self.cat_list.blockSignals(True)
            self.cat_list.clear()
            all_item = QListWidgetItem(tr("cat_all"))
            all_item.setData(Qt.ItemDataRole.UserRole, None)
            self.cat_list.addItem(all_item)
            for c in cats:
                cid = c.get("category_id")
                if self.overrides.is_hidden(self.mode, cid):
                    continue
                name = self.overrides.display_name(
                    self.mode, cid, c.get("category_name", "?"))
                if (self.overrides.is_locked(self.mode, cid)
                        and not self.parental.session_unlocked):
                    name += "  [locked]"
                ovr = self.overrides.get(self.mode, cid)
                icon = ovr.get("icon", "")
                if icon:
                    name = f"{icon}  {name}"
                it = QListWidgetItem(name)
                it.setData(Qt.ItemDataRole.UserRole, cid)
                color = ovr.get("color", "")
                if color:
                    it.setForeground(QColor(color))
                self.cat_list.addItem(it)
            self.cat_list.blockSignals(False)
            self.cat_list.setCurrentRow(
                1 if self.cat_list.count() > 1 else 0)

        def fail(msg):
            if gen != self._load_gen:
                return
            self._error(msg)

        run_async(self.pool, fn, done, fail)

    def _category_changed(self, cur, _prev=None) -> None:
        if not cur:
            return
        cat = cur.data(Qt.ItemDataRole.UserRole)
        locked = False
        if cat is not None:
            if self.mode == "fav":
                locked = self.favs.is_locked(cat)
            elif self.mode in ("live", "vod", "series"):
                locked = self.overrides.is_locked(self.mode, cat)
        if locked and not self.parental.session_unlocked:
            if not self._request_unlock():
                self.cat_list.blockSignals(True)
                self.cat_list.setCurrentRow(0)
                self.cat_list.blockSignals(False)
                self._load_items(None)
                return
            self._load_categories()
            return
        self.series_ctx = None
        self.back_btn.hide()
        self._load_items(cat)

    def _load_items(self, category_id) -> None:
        if self.mode == "rec":
            if category_id == "__jobs__":
                self.all_items = [self._job_item(j)
                                  for j in reversed(self.rec.jobs)]
            elif category_id == "__upcoming__":
                self.all_items = [
                    self._job_item(j) for j in reversed(self.rec.jobs)
                    if j["status"] == "scheduled"]
            else:
                self.all_items = self.rec.files(category_id)
            self._apply_filter()
            return
        if self.mode == "fav":
            exclude = (() if self.parental.session_unlocked
                       else self.favs.locked_groups())
            self.all_items = self.favs.items(
                category_id, exclude_groups=exclude)
            self._apply_filter()
            return
        if self.mode == "history":
            self.all_items = self.history.items()
            self._apply_filter()
            return
        if self.mode == "watchlist":
            self._watchlist_subcat = category_id
            movies = [
                {**m, "_kind": "vod"} for m in self.watchlist.movies]
            shows = [
                {**s, "_kind": "series"} for s in self.watchlist.shows]
            if category_id == "movies":
                self.all_items = movies
            elif category_id == "series":
                self.all_items = shows
            else:
                self.all_items = movies + shows
            self._apply_filter()
            return
        self.loading_bar.show()
        self._set_status(tr("status_loading_content"))
        fn = {"live": self.client.live_streams,
              "vod": self.client.vod_streams,
              "series": self.client.series_list}[self.mode]
        mode = self.mode
        gen = self._load_gen

        def done(items):
            if gen != self._load_gen or self.mode != mode:
                return
            self.loading_bar.hide()
            items = items or []
            if category_id is None:
                excluded = self.overrides.excluded_ids(
                    mode,
                    include_locked=not self.parental.session_unlocked)
                if excluded:
                    items = [it for it in items
                             if str(it.get("category_id")) not in excluded]
            self.all_items = items
            self._apply_filter()
            if self.mode == "live":
                self._ensure_xmltv_loaded()

        def fail(msg):
            if gen != self._load_gen:
                return
            self._error(msg)

        run_async(self.pool, lambda: fn(category_id), done, fail)

    def _ensure_xmltv_loaded(self) -> None:
        if self.xmltv._loaded or self.xmltv._failed:
            return
        run_async(self.pool, self.xmltv.ensure_loaded,
                  lambda ok: self.list_model.refresh_all() if ok else None)

    # -- metadata (TMDB artwork) -----------------------------------------------------

    def _init_metadata_provider(self) -> None:
        if self.tmdb:
            self.tmdb.flush()
        self.tmdb = None
        # The TMDB resolver is created whenever an API key is present -
        # NOT only when TMDB is the chosen cover source. It also powers
        # Trakt id resolution (Trakt's API is tmdb-keyed) and the
        # watched/watchlist badges, which must keep working even for a
        # user who prefers the provider's own artwork. The
        # metadata_source setting only decides whether the *list cover*
        # prefers the TMDB title-search poster or the provider's image.
        self._prefer_tmdb_covers = (
            self.settings.value("metadata_source", "playlist") == "tmdb")
        key = self.settings.value("tmdb_api_key", "") or ""
        if not key:
            return
        # Dedicated thread pool: TMDB lookups must never compete with
        # the shared pool used for channel/EPG loading, or a burst of
        # poster searches can starve real work and look like a freeze.
        # 6 workers is well under TMDB's 50 req/s limit but keeps a
        # newly-opened category with dozens of unseen titles from
        # taking half a minute to fill in the posters - each row
        # takes two sequential HTTP calls (search + details), so
        # fewer workers turn a 50-row scroll into visible latency.
        pool = QThreadPool()
        pool.setMaxThreadCount(6)
        self._tmdb_pool = pool
        self.tmdb = PosterResolver(pool, self.settings, TmdbClient(key))

    def _flush_poster_refresh(self) -> None:
        self.list_model.refresh_all()

    def poster_for(self, it, kind: str) -> str | None:
        if not self.tmdb or kind not in ("vod", "series"):
            return None
        title = it.get("name") or it.get("title") or ""
        if not title:
            return None
        return self.tmdb.get(
            title, kind,
            lambda _url: self._poster_refresh_timer.start(150))

    def tmdb_resolved(self, it, kind: str) -> bool:
        """True when we've either already got TMDB metadata for this
        row or TMDB isn't going to answer for it (live TV, no TMDB
        provider configured, empty title). The list delegate uses
        this to decide when it's safe to load the provider fallback
        cover: while TMDB is mid-fetch, painting the fallback would
        just be a wasted network round-trip that gets replaced 150 ms
        later when TMDB resolves."""
        if not self.tmdb or kind not in ("vod", "series"):
            return True
        title = it.get("name") or it.get("title") or ""
        if not title:
            return True
        return self.tmdb.is_resolved(title, kind)

    def _provider_cover(self, it) -> str | None:
        raw = it.get("stream_icon") or it.get("cover")
        return raw or None

    def cover_url(self, it, kind: str) -> str | None:
        """The URL the list delegate should paint for this row, chosen
        from an ordered candidate list (first that isn't blacklisted):

          1. TMDB poster from the title search (matches the detail
             panel, so the two columns agree once it resolves)
          2. TMDB poster extracted from the provider's own image URL
             (many panels proxy TMDB art under a broken host - going
             to image.tmdb.org gets the real file with no title-search
             dependency and no wait)
          3. the raw provider URL

        poster_for() is always called so the background TMDB lookup
        that feeds the watched-badge + detail panel still fires, even
        when the user prefers the provider's own artwork for the list -
        in that case we just drop the title-search poster as the *cover*
        candidate and let the provider image (or its embedded-TMDB
        rewrite) win."""
        title_tmdb = self.poster_for(it, kind)
        if not self._prefer_tmdb_covers:
            title_tmdb = None
        return choose_cover_url(
            title_tmdb, self._provider_cover(it),
            kind, self.logos.is_dead)

    def cover_should_fetch(self, url, it, kind: str) -> bool:
        """Whether the delegate should queue a network fetch for *url*
        now. TMDB URLs (title-search or embedded) fetch immediately;
        the raw provider URL waits until the TMDB lookup has answered
        so a pending row doesn't burn a request on art that's about to
        be replaced (and hammer flaky panel hosts into rate-limiting)."""
        if (not url or url in self.logos.waiting
                or self.logos.is_dead(url)):
            return False
        if "image.tmdb.org" in url:
            return True
        # When the user prefers the provider's own artwork, there's no
        # pending title-search poster that could replace this URL, so
        # fetch it straight away instead of waiting on TMDB.
        if not self._prefer_tmdb_covers:
            return True
        return self.tmdb_resolved(it, kind)

    # -- trakt scrobbling -------------------------------------------------------------

    def _trakt_start_for_item(self, kind: str, item) -> None:
        if not item or not self.trakt.is_connected():
            return
        if kind == "movie":
            title = item.get("name") or item.get("title") or ""
            if not title:
                return

            def job(title=title):
                movie = self.trakt.find_movie(title)
                if not movie:
                    return None
                payload = {"movie": movie}
                self.trakt.scrobble_start(payload, 0.0)
                return payload

        elif kind == "episode":
            show_title = ((self.series_ctx or {}).get("name")
                          or (self.series_ctx or {}).get("title") or "")
            try:
                season = int(item.get("season"))
                episode = int(item.get("episode_num"))
            except (TypeError, ValueError):
                return
            if not show_title:
                return

            def job(show_title=show_title, season=season, episode=episode):
                ep = self.trakt.find_episode(show_title, season, episode)
                if not ep:
                    return None
                payload = {"episode": ep}
                self.trakt.scrobble_start(payload, 0.0)
                return payload
        else:
            return

        def done(payload):
            if payload:
                self._trakt_active = {"payload": payload}

        run_async(self.pool, job, done, lambda _e: None)

    def _trakt_stop_current(self) -> None:
        if not self._trakt_active:
            return
        active = self._trakt_active
        self._trakt_active = None
        progress = self.player.progress_percent() if self.player else 0.0

        def job(active=active, progress=progress):
            self.trakt.scrobble_stop(active["payload"], progress)

        run_async(self.pool, job, lambda _r: None, lambda _e: None)

    # -- trakt watched-history sync --------------------------------------------

    # Skip the auto-sync if we synced this recently (an hour) - avoids
    # hammering Trakt on every restart while still catching the "watched a
    # movie on the couch, opened dopeIPTV the same evening" case.
    _WATCHED_SYNC_TTL: int = 3600

    def _maybe_sync_watched(self, force: bool = False) -> None:
        if not self.trakt.is_connected() or self._watched_sync_running:
            return
        if not force:
            age = int(datetime.now().timestamp()) - self.watched.last_sync_at
            if age < self._WATCHED_SYNC_TTL:
                return
        self._watched_sync_running = True

        # Runs on a daemon thread rather than the QThreadPool: the two
        # Trakt endpoints are 30-45 s worst case, and if the user closes
        # the app while the request is in flight a pool worker touching
        # emit signals during Python teardown segfaults the process on
        # exit. Daemon threads are killed instantly on process end so
        # no callback ever fires into a torn-down interpreter.
        def worker() -> None:
            try:
                movies = self.trakt.watched_movies()
                shows = self.trakt.watched_shows()
                wl_movies = self.trakt.watchlist_movies()
                wl_shows = self.trakt.watchlist_shows()
            except Exception:
                # Marshal back to the main thread just to flip the
                # running flag; QTimer.singleShot from a non-Qt thread
                # is safe as long as the target QObject lives there.
                QTimer.singleShot(0, self._on_watched_sync_failed)
                return
            self.watched.replace(movies, shows)
            self.watchlist.replace(wl_movies, wl_shows)
            # Cross-device: anything the user marked 'seen (local)' that
            # has since resolved to a TMDB id but isn't on Trakt yet gets
            # pushed up now, then promoted into the Trakt layer so it's
            # never POSTed twice (which would add duplicate watches).
            self._push_local_watched_to_trakt()
            QTimer.singleShot(0, self._on_watched_sync_done)

        threading.Thread(target=worker, daemon=True).start()

    def _push_local_watched_to_trakt(self) -> None:
        """Runs on the sync worker thread. Best-effort POST of local-only
        watched marks to Trakt; each accepted item is promoted to the
        Trakt layer so the next sync keeps it and we never double-post."""
        movies, episodes = self.watched.pending_trakt_pushes()
        for tid in movies:
            try:
                self.trakt.add_movie_history(tid)
            except Exception:
                continue
            self.watched.mark_movie_synced(tid)
        for show_id, season, episode in episodes:
            try:
                self.trakt.add_episode_history(show_id, season, episode)
            except Exception:
                continue
            self.watched.mark_episode_synced(show_id, season, episode)
        wl_movies, wl_shows = self.watchlist.pending_trakt_pushes()
        for tid in wl_movies:
            try:
                self.trakt.add_movie_watchlist(tid)
            except Exception:
                continue
            self.watchlist.mark_movie_synced(tid)
        for tid in wl_shows:
            try:
                self.trakt.add_show_watchlist(tid)
            except Exception:
                continue
            self.watchlist.mark_show_synced(tid)

    def _on_watched_sync_done(self) -> None:
        self._watched_sync_running = False
        # Nudge the visible list so the newly-known 'seen' markers
        # paint without needing a category switch.
        self.list_model.refresh_all()

    def _on_watched_sync_failed(self) -> None:
        self._watched_sync_running = False

    def is_movie_watched(self, item: dict) -> bool:
        if not item:
            return False
        if self.tmdb:
            title = item.get("name") or item.get("title") or ""
            if self.watched.is_movie_watched(
                    self.tmdb.tmdb_id_for(title, "vod")):
                return True
        sid = item.get("stream_id")
        try:
            return self.watched.is_movie_watched_by_stream(int(sid))
        except (TypeError, ValueError):
            return False

    def show_watched_count(self, item: dict) -> int:
        if not item:
            return 0
        count = 0
        if self.tmdb:
            title = item.get("name") or item.get("title") or ""
            count = self.watched.show_watched_count(
                self.tmdb.tmdb_id_for(title, "series"))
        if count > 0:
            return count
        # Fall back to the stream-id local mark so a series marked
        # watched before TMDB resolved still shows a badge (count 1).
        sid = item.get("series_id") or item.get("stream_id")
        try:
            if self.watched.is_series_watched_by_stream(int(sid)):
                return 1
        except (TypeError, ValueError):
            pass
        return 0

    def is_episode_watched(self, item: dict) -> bool:
        if not item or not self.series_ctx:
            return False
        if self.tmdb:
            show_title = (self.series_ctx.get("name")
                          or self.series_ctx.get("title") or "")
            show_id = self.tmdb.tmdb_id_for(show_title, "series")
            try:
                season = int(item.get("season"))
                episode = int(item.get("episode_num"))
                if self.watched.is_episode_watched(show_id, season, episode):
                    return True
            except (TypeError, ValueError):
                pass
        eid = item.get("id")
        try:
            return self.watched.is_episode_watched_by_stream(int(eid))
        except (TypeError, ValueError):
            return False

    def is_item_watched(self, item: dict, kind: str) -> bool:
        """Unified predicate the delegate calls once per paint - answers
        whether the given item should show the 'already seen' badge."""
        if kind == "vod":
            return self.is_movie_watched(item)
        if kind == "series":
            return self.show_watched_count(item) > 0
        if kind == "episode":
            return self.is_episode_watched(item)
        if kind == "history":
            hk = item.get("_kind")
            if hk == "movie":
                return self.is_movie_watched(item)
            if hk == "series":
                return self.show_watched_count(item) > 0
        return False

    def is_item_on_watchlist(self, item: dict, kind: str) -> bool:
        """Unified predicate the delegate calls once per paint - answers
        whether the given item is on the Watch Later list (its clock
        marker). Only movie/series rows can be on the list."""
        if kind == "vod":
            return self.is_on_watchlist(item, "vod")
        if kind == "series":
            return self.is_on_watchlist(item, "series")
        if kind == "history":
            hk = item.get("_kind")
            if hk == "movie":
                return self.is_on_watchlist(item, "vod")
            if hk == "series":
                return self.is_on_watchlist(item, "series")
        return False

    def _tmdb_id_for_item(self, item: dict, kind: str) -> int | None:
        if not self.tmdb or not item:
            return None
        title = item.get("name") or item.get("title") or ""
        return self.tmdb.tmdb_id_for(title, kind)

    def _show_tmdb_id_for_episode(self) -> int | None:
        if not self.tmdb or not self.series_ctx:
            return None
        show_title = (self.series_ctx.get("name")
                      or self.series_ctx.get("title") or "")
        return self.tmdb.tmdb_id_for(show_title, "series")

    # -- mark-as-watched (local toggle + optional Trakt push) -----------------

    def _mark_movie_watched(self, item: dict,
                            push_to_trakt: bool) -> None:
        tid = self._tmdb_id_for_item(item, "vod")
        if tid is not None:
            self.watched.mark_movie_local(tid)
        else:
            # Local mark works even before TMDB resolves - key on the
            # provider stream_id so the badge is immediate. Trakt push
            # still needs the TMDB id, so only skip it in that case.
            sid = item.get("stream_id")
            try:
                self.watched.mark_movie_local_by_stream(int(sid))
            except (TypeError, ValueError):
                return
            if push_to_trakt:
                self._error(tr("mark_needs_tmdb"))
                push_to_trakt = False
        if push_to_trakt and tid is not None and self.trakt.is_connected():
            def job(tid=tid):
                try:
                    self.trakt.add_movie_history(tid)
                except Exception:
                    pass
            threading.Thread(target=job, daemon=True).start()
        self.list_model.refresh_all()

    def _unmark_movie_watched(self, item: dict,
                              push_to_trakt: bool) -> None:
        tid = self._tmdb_id_for_item(item, "vod")
        if tid is not None:
            self.watched.unmark_movie(tid)
        sid = item.get("stream_id")
        try:
            self.watched.unmark_movie_by_stream(int(sid))
        except (TypeError, ValueError):
            pass
        if push_to_trakt and tid is not None and self.trakt.is_connected():
            def job(tid=tid):
                try:
                    self.trakt.remove_movie_history(tid)
                except Exception:
                    pass
            threading.Thread(target=job, daemon=True).start()
        self.list_model.refresh_all()

    def _mark_episode_watched(self, item: dict,
                              push_to_trakt: bool) -> None:
        sid = self._show_tmdb_id_for_episode()
        try:
            season = int(item.get("season"))
            episode = int(item.get("episode_num"))
        except (TypeError, ValueError):
            season = episode = None
        if sid is not None and season is not None:
            self.watched.mark_episode_local(sid, season, episode)
        else:
            eid = item.get("id")
            try:
                self.watched.mark_episode_local_by_stream(int(eid))
            except (TypeError, ValueError):
                return
            if push_to_trakt:
                self._error(tr("mark_needs_tmdb"))
                push_to_trakt = False
        if (push_to_trakt and sid is not None and season is not None
                and self.trakt.is_connected()):
            def job(sid=sid, s=season, e=episode):
                try:
                    self.trakt.add_episode_history(sid, s, e)
                except Exception:
                    pass
            threading.Thread(target=job, daemon=True).start()
        self.list_model.refresh_all()

    def _unmark_episode_watched(self, item: dict,
                                push_to_trakt: bool) -> None:
        sid = self._show_tmdb_id_for_episode()
        try:
            season = int(item.get("season"))
            episode = int(item.get("episode_num"))
        except (TypeError, ValueError):
            season = episode = None
        if sid is not None and season is not None:
            self.watched.unmark_episode(sid, season, episode)
        eid = item.get("id")
        try:
            self.watched.unmark_episode_by_stream(int(eid))
        except (TypeError, ValueError):
            pass
        if (push_to_trakt and sid is not None and season is not None
                and self.trakt.is_connected()):
            def job(sid=sid, s=season, e=episode):
                try:
                    self.trakt.remove_episode_history(sid, s, e)
                except Exception:
                    pass
            threading.Thread(target=job, daemon=True).start()
        self.list_model.refresh_all()

    def _mark_series_watched(self, item: dict,
                             push_to_trakt: bool) -> None:
        """Local-only 'seen the whole show' toggle. Trakt has no
        'watched entire show' primitive so the +Trakt variant is
        deliberately unavailable at the series-list level."""
        sid = item.get("series_id") or item.get("stream_id")
        try:
            self.watched.mark_series_local_by_stream(int(sid))
        except (TypeError, ValueError):
            return
        self.list_model.refresh_all()

    def _unmark_series_watched(self, item: dict,
                               push_to_trakt: bool) -> None:
        sid = item.get("series_id") or item.get("stream_id")
        try:
            self.watched.unmark_series_by_stream(int(sid))
        except (TypeError, ValueError):
            pass
        # Also clear any TMDB-keyed per-episode marks for this show so
        # the badge disappears immediately.
        if self.tmdb:
            title = item.get("name") or item.get("title") or ""
            tid = self.tmdb.tmdb_id_for(title, "series")
            if isinstance(tid, int):
                for ep_set in (self.watched.trakt_episodes,
                               self.watched.local_episodes):
                    ep_set.pop(tid, None)
                self.watched._save()
        self.list_model.refresh_all()

    # -- Watch Later (local toggle + optional Trakt push) --------------------

    def is_on_watchlist(self, item: dict, kind: str) -> bool:
        tid = self._tmdb_id_for_item(item, kind)
        sid = (item.get("stream_id") if kind == "vod"
               else item.get("series_id"))
        try:
            sid_int = int(sid) if sid is not None else None
        except (TypeError, ValueError):
            sid_int = None
        if kind == "vod":
            return self.watchlist.has_movie(tid, sid_int)
        if kind == "series":
            return self.watchlist.has_show(tid, sid_int)
        return False

    def _add_watchlist(self, item: dict, kind: str,
                       push_to_trakt: bool) -> None:
        tid = self._tmdb_id_for_item(item, kind)
        if kind == "vod":
            self.watchlist.add_movie_local(item, tid)
            trakt_fn = self.trakt.add_movie_watchlist
        else:
            self.watchlist.add_show_local(item, tid)
            trakt_fn = self.trakt.add_show_watchlist
        if push_to_trakt:
            if tid is None:
                self._error(tr("mark_needs_tmdb"))
            elif self.trakt.is_connected():
                def job(tid=tid, fn=trakt_fn):
                    try:
                        fn(tid)
                    except Exception:
                        pass
                threading.Thread(target=job, daemon=True).start()

    def _remove_watchlist(self, item: dict, kind: str,
                          push_to_trakt: bool) -> None:
        tid = self._tmdb_id_for_item(item, kind)
        sid = (item.get("stream_id") if kind == "vod"
               else item.get("series_id"))
        try:
            sid_int = int(sid) if sid is not None else None
        except (TypeError, ValueError):
            sid_int = None
        if kind == "vod":
            self.watchlist.remove_movie(tid, sid_int)
            trakt_fn = self.trakt.remove_movie_watchlist
        else:
            self.watchlist.remove_show(tid, sid_int)
            trakt_fn = self.trakt.remove_show_watchlist
        if push_to_trakt and tid is not None and self.trakt.is_connected():
            def job(tid=tid, fn=trakt_fn):
                try:
                    fn(tid)
                except Exception:
                    pass
            threading.Thread(target=job, daemon=True).start()
        # If we're currently in the Watch Later view, drop the row.
        if self.mode == "watchlist":
            self._load_items(getattr(self, "_watchlist_subcat", None))

    def _trakt_device_auth_dialog(self, parent) -> None:
        d = QDialog(parent)
        d.setWindowTitle(tr("trakt_connect_title"))
        d.setMinimumWidth(380)
        lay = QVBoxLayout(d)
        info = QLabel(tr("trakt_requesting_code"))
        info.setWordWrap(True)
        info.setStyleSheet("font-size:13px;")
        lay.addWidget(info)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(d.reject)
        lay.addWidget(buttons)

        state = {"cancelled": False, "device_code": None,
                 "interval": 5, "expires_at": 0.0}

        def poll() -> None:
            if state["cancelled"] or not state["device_code"]:
                return
            if time.time() > state["expires_at"]:
                info.setText(tr("trakt_code_expired"))
                return
            run_async(self.pool,
                      lambda: self.trakt.poll_device_token(
                          state["device_code"]),
                      poll_done, poll_failed)

        def poll_done(data) -> None:
            if state["cancelled"]:
                return
            if data is None:
                QTimer.singleShot(state["interval"] * 1000, poll)
                return
            info.setText(tr("trakt_connected_excl"))
            QTimer.singleShot(700, d.accept)

        def poll_failed(msg) -> None:
            if not state["cancelled"]:
                info.setText(tr("trakt_login_failed", msg=msg))

        def started(data) -> None:
            if state["cancelled"]:
                return
            state["device_code"] = data["device_code"]
            state["interval"] = data.get("interval", 5)
            state["expires_at"] = time.time() + data.get("expires_in", 600)
            info.setText(
                tr("trakt_enter_code",
                   url=data.get('verification_url'),
                   code=data['user_code']))
            QTimer.singleShot(state["interval"] * 1000, poll)

        def start_failed(msg) -> None:
            if not state["cancelled"]:
                info.setText(tr("trakt_could_not_start", msg=msg))

        buttons.rejected.connect(lambda: state.__setitem__("cancelled", True))
        run_async(self.pool, self.trakt.start_device_auth,
                  started, start_failed)
        d.exec()

    def _open_trakt_dialog(self, parent) -> None:
        if not self.trakt.is_connected():
            QMessageBox.information(
                parent, "Trakt", tr("msg_connect_trakt_first"))
            return
        d = QDialog(parent)
        d.setWindowTitle(tr("trakt_watchlist_title"))
        d.setMinimumSize(480, 500)
        lay = QVBoxLayout(d)
        tw = QTabWidget()
        lay.addWidget(tw)
        wl_list = QListWidget()
        hist_list = QListWidget()
        tw.addTab(wl_list, tr("trakt_tab_watchlist"))
        tw.addTab(hist_list, tr("nav_history"))
        status = QLabel(tr("common_loading"))
        status.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        lay.addWidget(status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(d.reject)
        buttons.accepted.connect(d.accept)
        lay.addWidget(buttons)

        def fmt_watchlist(e) -> str:
            for kind in ("movie", "show"):
                if e.get(kind):
                    x = e[kind]
                    return f"{x.get('title')} ({x.get('year') or '?'})"
            return "?"

        def fmt_history(e) -> str:
            watched = (e.get("watched_at") or "")[:10]
            if e.get("movie"):
                m = e["movie"]
                return f"{watched}  {m.get('title')} ({m.get('year') or '?'})"
            if e.get("episode") and e.get("show"):
                ep, s = e["episode"], e["show"]
                return (f"{watched}  {s.get('title')} "
                        f"S{ep.get('season')}E{ep.get('number')} - "
                        f"{ep.get('title') or ''}")
            return watched or "?"

        def load_failed(msg) -> None:
            status.setText(tr("trakt_load_failed", msg=msg))

        def load_history(items) -> None:
            hist_list.clear()
            for e in items:
                hist_list.addItem(fmt_history(e))
            status.setText("")

        def load_watchlist(items) -> None:
            wl_list.clear()
            for e in items:
                wl_list.addItem(fmt_watchlist(e))
            run_async(self.pool, lambda: self.trakt.history(50),
                      load_history, load_failed)

        run_async(self.pool, self.trakt.watchlist,
                  load_watchlist, load_failed)
        d.exec()

    # -- list and filtering --------------------------------------------------------

    LABELS = {
        "live": "channels", "vod": "movies", "series": "series",
        "episode": "episodes", "fav": "favorites",
        "history": "history items", "rec": "recordings",
        "watchlist": "on your list",
    }

    @staticmethod
    def _sort_key_name(it):
        return (it.get("name") or it.get("title") or "").lower()

    def _sorted(self, items: list) -> list:
        order = self.settings.value("sort_order", "default")
        if order == "alpha_asc":
            return sorted(items, key=MainWindow._sort_key_name)
        if order == "alpha_desc":
            return sorted(items, key=MainWindow._sort_key_name, reverse=True)
        if order == "recent":
            def added(it):
                try:
                    return int(it.get("added") or 0)
                except (TypeError, ValueError):
                    return 0
            return sorted(items, key=added, reverse=True)
        return items

    def channel_display_name(self, it) -> str:
        base = it.get("name") or it.get("title") or "?"
        mode = "episode" if self.series_ctx else self.mode
        if mode in ("live", "vod", "series", "fav"):
            key = self._item_key(it)
            if key is not None:
                ov_mode = "live" if mode == "fav" else mode
                return self.channel_ov.display_name(ov_mode, key, base)
        return base

    def _channel_hidden(self, it, kind: str) -> bool:
        if kind not in ("live", "vod", "series", "fav"):
            return False
        key = self._item_key(it)
        if key is None:
            return False
        ov_mode = "live" if kind == "fav" else kind
        return self.channel_ov.is_hidden(ov_mode, key)

    def _apply_filter(self) -> None:
        text = self.search.text().lower().strip()
        kind = "episode" if self.series_ctx else self.mode
        items = [it for it in self.all_items
                 if not self._channel_hidden(it, kind)]
        if text:
            filtered = [it for it in items
                        if text in self.channel_display_name(it).lower()]
        else:
            filtered = items
        filtered = self._sorted(filtered)
        self.list_model.set_items(filtered, kind)
        if self._loading_hint.isVisible():
            self._loading_hint.hide()
        self._set_status(f"{len(filtered)} {self.LABELS[kind]}")
        if kind == "fav" and not self.all_items:
            self._set_status(
                "No favorites yet - right-click a channel in TV to add one.")
        elif kind == "history" and not self.all_items:
            self._set_status("No watch history yet.")

    # -- item identity -------------------------------------------------------------

    @staticmethod
    def _item_key(it):
        if not it:
            return None
        return (it.get("stream_id") or it.get("series_id")
                or it.get("id") or it.get("_key"))

    def _history_kind(self) -> str:
        if self.series_ctx:
            return "episode"
        return {"live": "live", "fav": "live", "vod": "movie"}.get(
            self.mode, "other")

    # -- selection, EPG and detail panel -------------------------------------------

    def _on_current_changed(self, current, _previous=None) -> None:
        it = (self.list_model.item_at(current.row())
              if current.isValid() else None)
        self._current_key = self._item_key(it)
        self._show_detail(it)

    POSTER_SIZE_LIVE = (84, 84)
    POSTER_SIZE_MEDIA = (170, 255)

    def _show_detail(self, it) -> None:
        self.now_card.hide()
        self.media_info.hide()
        self.media_rating_lbl.hide()
        self._clear_cast_row()
        self._clear_epg_rows()
        self._current_epg = None
        self._tmdb_details = None
        if not it:
            self._detail_name = tr("detail_select_something")
            self.d_logo.setFixedSize(*self.POSTER_SIZE_LIVE)
            self.d_logo.setPixmap(QPixmap())
            self.d_logo.setText("")
            return
        name = self.channel_display_name(it)
        self._detail_name = name
        # History and Watch Later rows carry the original content kind
        # in "_kind"; a VOD or series shown from either view should still
        # resolve TMDB metadata + cover the same way the main Movies /
        # Series view does. History uses its own vocabulary
        # ("movie"/"episode"/"live") - normalise it to the vod/series
        # the detail panel and TMDB lookup speak.
        snap_kind = (it.get("_kind")
                     if self.mode in ("history", "watchlist") else None)
        snap_kind = {"movie": "vod", "episode": "series"}.get(
            snap_kind, snap_kind)
        media_kind = (
            "vod" if self.mode == "vod"
            else "series" if self.mode == "series"
            else snap_kind if snap_kind in ("vod", "series")
            else None)
        is_media = media_kind is not None
        poster_size = (self.POSTER_SIZE_MEDIA if is_media
                       else self.POSTER_SIZE_LIVE)
        self.d_logo.setFixedSize(*poster_size)
        # Make the Play button span the poster's width so its (centered) label
        # sits centered under the poster/TV icon rather than off to the left.
        self.play_mpv.setFixedWidth(poster_size[0])
        if is_media:
            # Match the info box to the poster height so their bottoms align.
            self.media_info.setFixedHeight(self.POSTER_SIZE_MEDIA[1])
        self.d_logo.setPixmap(QPixmap())
        self.d_logo.setStyleSheet(self.PLACEHOLDER_LOGO_STYLE)
        self.d_logo.setText(name.strip()[:1].upper())
        self._load_detail_poster(it, is_media, media_kind)

        if self.mode == "rec":
            return

        if self.mode == "history" and snap_kind not in ("vod", "series"):
            # For live/recording history rows the artwork + TMDB card
            # is all we have; only fall through for movie/series
            # history rows so they also get provider-side info
            # (plot, year, episode list).
            return

        if self.series_ctx:
            info = (it.get("info")
                    if isinstance(it.get("info"), dict) else {})
            self._show_media_info(info, self._current_key)
        elif self.mode in ("live", "fav"):
            if it.get("stream_id") is not None:
                self._request_epg()
                if (self.player and self._autoplay_preview()
                        and self.playback_mode() == "embedded"):
                    self._preview_timer.start(350)
        # In History / Watch Later, defer to the snapshot's _kind so
        # a movie row fetches movie info and a series row fetches
        # series info - same as the main Movies / Series views.
        # History rows carry the provider id under "_key" (not
        # stream_id/series_id), so fall back to that - this is what
        # makes the plot/genre/cast panel fill in for history items
        # even with no TMDB account.
        elif (self.mode == "vod"
              or (self.mode in ("watchlist", "history")
                  and snap_kind == "vod")):
            sid = it.get("stream_id")
            if sid is None and self.mode == "history":
                sid = it.get("_key")
            if sid is not None:
                self._request_media_info("vod", sid, self._current_key)
        elif (self.mode == "series"
              or (self.mode in ("watchlist", "history")
                  and snap_kind == "series")):
            sid = it.get("series_id")
            if sid is None and self.mode == "history":
                sid = it.get("_key")
            if sid is not None:
                self._request_media_info("series", sid, self._current_key)

    @property
    def PLACEHOLDER_LOGO_STYLE(self) -> str:
        return (f"background:{P['sel']}; border-radius:18px; "
                "font-size:30px; font-weight:700;")

    def _set_detail_logo(self, pm) -> None:
        w, h = self.d_logo.width(), self.d_logo.height()
        tile = QPixmap(w, h)
        tile.fill(Qt.GlobalColor.transparent)
        p = QPainter(tile)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        s = pm.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap((w - s.width()) // 2, (h - s.height()) // 2, s)
        p.end()
        self.d_logo.setStyleSheet("background:transparent;")
        self.d_logo.setText("")
        self.d_logo.setPixmap(tile)

    def _media_title_for_tmdb(self, it) -> str:
        if self.series_ctx:
            return (self.series_ctx.get("name")
                    or self.series_ctx.get("title") or "")
        return it.get("name") or it.get("title") or ""

    def _load_detail_poster(self, it, is_media: bool,
                            kind: str | None = None) -> None:
        raw_cover = it.get("stream_icon") or it.get("cover")
        # If the provider icon embeds a TMDB path, prefer the direct
        # image.tmdb.org URL - the panel's own proxy is often the one
        # that's 404ing/down. Same rewrite the list delegate uses.
        fallback_url = tmdb_url_from_provider(raw_cover) or raw_cover
        if not (is_media and self.tmdb):
            if fallback_url:
                self.poster_art.get(fallback_url, self._set_detail_logo)
            return
        title = self._media_title_for_tmdb(it)
        if not title:
            if fallback_url:
                self.poster_art.get(fallback_url, self._set_detail_logo)
            return
        kind = kind or ("vod" if self.mode == "vod" else "series")
        key = self._current_key

        def apply(details: dict) -> None:
            if key != self._current_key:
                return
            self._apply_media_card(details)
            url = details.get("poster_url") or fallback_url
            if url:
                self.poster_art.get(url, self._set_detail_logo)

        details = self.tmdb.get_full(title, kind, apply)
        if details is not None:
            apply(details)
        elif fallback_url:
            self.poster_art.get(fallback_url, self._set_detail_logo)

    def _apply_media_card(self, details: dict) -> None:
        self._tmdb_details = details
        rating = details.get("rating")
        imdb_id = details.get("imdb_id")
        cast = details.get("cast") or []
        stars = f"★ {rating:.1f}/10" if rating else ""
        if imdb_id:
            label = stars or "View on IMDb"
            self.media_rating_lbl.setText(
                f'<a href="https://www.imdb.com/title/{imdb_id}/" '
                f'style="color:{ACCENT}; text-decoration:none;">'
                f'{label}</a>')
            self.media_rating_lbl.show()
        elif stars:
            self.media_rating_lbl.setText(stars)
            self.media_rating_lbl.show()
        else:
            self.media_rating_lbl.hide()
        self._clear_cast_row()
        if cast:
            for member in cast:
                self.cast_lay.insertWidget(
                    self.cast_lay.count() - 1,
                    self._build_cast_chip(member.get("name") or "",
                                          member.get("profile_url"),
                                          member.get("person_id")))
            self.cast_scroll.show()
        else:
            self.cast_scroll.hide()
        if (details.get("overview") or details.get("genres")) \
                and not self.media_info.isVisible():
            self._show_media_info({}, self._current_key)

    CAST_PHOTO_SIZE = 56

    def _clear_cast_row(self) -> None:
        while self.cast_lay.count() > 1:
            w = self.cast_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.cast_scroll.hide()

    def _build_cast_chip(self, name: str, profile_url: str | None,
                         person_id=None) -> QWidget:
        size = self.CAST_PHOTO_SIZE
        chip = _ClickableWidget()
        if self.tmdb and name:
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip(f"Find other titles with {name} in your playlist")
            chip.clicked.connect(
                lambda: self._on_cast_clicked(name, person_id))
        v = QVBoxLayout(chip)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        photo = QLabel()
        photo.setFixedSize(size, size)
        photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        photo.setStyleSheet(
            f"background:{P['sel']}; border-radius:{size // 2}px; "
            "font-size:18px; font-weight:700;")
        photo.setText(name.strip()[:1].upper() if name else "?")
        v.addWidget(photo, 0, Qt.AlignmentFlag.AlignHCenter)
        name_lbl = QLabel(name)
        name_lbl.setStyleSheet(f"color:{P['muted']}; font-size:10px;")
        name_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        name_lbl.setFixedWidth(size + 16)
        name_lbl.setWordWrap(True)
        v.addWidget(name_lbl)
        if profile_url:
            def apply_photo(pm, photo=photo) -> None:
                photo.setPixmap(self._circular_pixmap(pm, size))
                photo.setText("")
                photo.setStyleSheet("background:transparent;")
            self.poster_art.get(profile_url, apply_photo)
        return chip

    @staticmethod
    def _circular_pixmap(pm: QPixmap, size: int) -> QPixmap:
        scaled = pm.scaled(
            size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation)
        x = max(0, (scaled.width() - size) // 2)
        y = max(0, (scaled.height() - size) // 2)
        cropped = scaled.copy(x, y, size, size)
        out = QPixmap(size, size)
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addEllipse(0, 0, size, size)
        p.setClipPath(path)
        p.drawPixmap(0, 0, cropped)
        p.end()
        return out

    # -- cast: find an actor's other titles in this playlist -----------------------

    # Noise commonly appended to VOD titles by providers (movies especially:
    # "Inception (2010) 1080p", "EN| Inception MULTI"), which stopped them
    # from matching TMDB's clean title.
    _TITLE_BRACKETS = re.compile(r"[\(\[\{].*?[\)\]\}]")
    _TITLE_NOISE = re.compile(
        r"\b(19|20)\d{2}\b|\b(2160p|1080p|720p|480p|4k|uhd|fhd|hd|sd|hevc|"
        r"x26[45]|h26[45]|web[- ]?dl|webrip|bluray|bdrip|dvdrip|remux|imax|"
        r"multi|dual|vostfr|truefrench)\b", re.IGNORECASE)
    _TITLE_PREFIX = re.compile(r"^\s*[a-z]{2,4}\s*[|:\-]\s*", re.IGNORECASE)

    @classmethod
    def _normalize_title(cls, s: str) -> str:
        s = (s or "").lower()
        s = cls._TITLE_BRACKETS.sub(" ", s)
        s = cls._TITLE_PREFIX.sub("", s)
        s = cls._TITLE_NOISE.sub(" ", s)
        return "".join(c for c in s if c.isalnum())

    def _ensure_full_catalog(self, callback) -> None:
        if self._full_catalog is not None:
            callback(self._full_catalog)
            return

        def fetch():
            vod = self.client.vod_streams(None) or []
            series = self.client.series_list(None) or []
            return ([(it, "vod") for it in vod]
                    + [(it, "series") for it in series])

        def done(items):
            self._full_catalog = items
            callback(items)

        run_async(self.pool, fetch, done, lambda _e: callback([]))

    def _find_playlist_matches(self, titles: list[str], callback) -> None:
        norm_titles = {self._normalize_title(t) for t in titles}

        long_titles = [t for t in norm_titles if len(t) >= 6]

        def with_catalog(catalog):
            matches = []
            for it, kind in catalog:
                cnorm = self._normalize_title(
                    it.get("name") or it.get("title") or "")
                if not cnorm:
                    continue
                # Exact match, or the provider title begins with a (reasonably
                # long) TMDB title — catches residual trailing noise.
                if cnorm in norm_titles or any(
                        cnorm.startswith(t) for t in long_titles):
                    matches.append((it, kind))
            callback(matches)

        self._ensure_full_catalog(with_catalog)

    def _ensure_cast_dialog(self) -> QDialog:
        """Create (once) a reusable, non-modal, always-on-top panel that
        lists a cast member's other titles. Non-modal so the cast strip on
        the right stays clickable - clicking another cast member just
        refreshes this same panel instead of stacking new dialogs."""
        d = getattr(self, "_cast_dialog", None)
        if d is not None:
            return d
        d = QDialog(self)
        # Tool + stay-on-top keeps it above the main window without blocking
        # it, so you can pick another cast member while it is open.
        d.setWindowFlags(Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
        d.setModal(False)
        d.setMinimumSize(440, 520)
        lay = QVBoxLayout(d)
        self._cast_status = QLabel("")
        self._cast_status.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        self._cast_status.setWordWrap(True)
        lay.addWidget(self._cast_status)
        self._cast_result_list = QListWidget()
        # Room for a poster thumbnail beside each title.
        self._cast_result_list.setIconSize(QSize(46, 69))
        self._cast_result_list.setStyleSheet(
            "QListWidget::item { padding: 4px; }")
        self._cast_result_list.itemDoubleClicked.connect(
            lambda item: self._play_cast_match(item, d))
        lay.addWidget(self._cast_result_list, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(d.hide)
        buttons.accepted.connect(d.hide)
        for b in buttons.buttons():
            b.setIcon(QIcon())
        lay.addWidget(buttons)
        self._cast_dialog = d
        return d

    def _on_cast_clicked(self, name: str, person_id) -> None:
        if not self.tmdb:
            return
        d = self._ensure_cast_dialog()
        d.setWindowTitle(tr("actor_other_titles", name=name))
        status = self._cast_status
        result_list = self._cast_result_list
        result_list.clear()
        status.setText(tr("actor_lookup_filmography"))
        # Each click starts a new lookup; stamp a token so stale async
        # callbacks from a previously clicked cast member are ignored.
        token = getattr(self, "_cast_token", 0) + 1
        self._cast_token = token

        def show_matches(titles: list[str]) -> None:
            if self._cast_token != token:
                return
            status.setText(tr("actor_searching_playlist"))

            def with_matches(matches) -> None:
                if self._cast_token != token:
                    return
                result_list.clear()
                if not matches:
                    status.setText(tr("actor_no_matches"))
                    return
                status.setText(tr("actor_matches_found", n=len(matches)))
                for it, kind in matches:
                    label = (f"{it.get('name') or it.get('title')}"
                            f"  ({tr('misc_movie') if kind == 'vod' else tr('misc_series')})")
                    item = QListWidgetItem(label)
                    item.setData(Qt.ItemDataRole.UserRole, (it, kind))
                    result_list.addItem(item)
                    self._load_cast_match_poster(it, kind, item)

            self._find_playlist_matches(titles, with_matches)

        def with_person_id(pid) -> None:
            if self._cast_token != token:
                return
            if not pid:
                status.setText(tr("actor_not_found_tmdb", name=name))
                return
            credits = self.tmdb.get_person_credits(pid, show_matches)
            if credits is not None:
                show_matches(credits)

        if person_id:
            with_person_id(person_id)
        else:
            # Cast names that came from the provider's plain-text list have
            # no TMDB id yet - resolve it by name first, then fetch credits.
            status.setText(tr("actor_lookup_member"))
            pid = self.tmdb.resolve_person_id(name, with_person_id)
            if pid is not None:
                with_person_id(pid)
        d.show()
        d.raise_()
        d.activateWindow()

    def _load_cast_match_poster(self, it, kind, item) -> None:
        """Fetch a poster thumbnail for a cast-filmography match and set it
        as the list item's icon (provider artwork first, then TMDB)."""
        def apply(pm) -> None:
            try:
                icon_pm = pm.scaled(
                    46, 69, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                item.setIcon(QIcon(icon_pm))
            except RuntimeError:
                pass  # the list item was cleared before the art arrived

        url = it.get("stream_icon") or it.get("cover")
        if url:
            self.poster_art.get(url, apply)
            return
        if self.tmdb:
            title = it.get("name") or it.get("title") or ""
            tmdb_kind = "vod" if kind == "vod" else "series"
            details = self.tmdb.get_full(
                title, tmdb_kind,
                lambda d: (d.get("poster_url")
                           and self.poster_art.get(d["poster_url"], apply)))
            if details and details.get("poster_url"):
                self.poster_art.get(details["poster_url"], apply)

    def _play_cast_match(self, item, dialog) -> None:
        it, kind = item.data(Qt.ItemDataRole.UserRole)
        dialog.hide()
        if kind == "vod":
            self.switch_mode("vod")
            self._navigate_to_item(it, "vod")
        else:
            self.switch_mode("series")
            self._navigate_to_item(it, "series")

    def _navigate_to_item(self, target, mode: str) -> None:
        """Switch to the right category and scroll/select a specific item."""
        cat_id = str(target.get("category_id") or "")
        for i in range(self.cat_list.count()):
            ci = self.cat_list.item(i)
            if ci and str(ci.data(Qt.ItemDataRole.UserRole) or "") == cat_id:
                self.cat_list.setCurrentRow(i)
                break
        else:
            self.cat_list.setCurrentRow(0)

        target_key = self._item_key(target)

        def after_load(target_key=target_key, target=target):
            for row in range(self.list_model.rowCount()):
                it = self.list_model.item_at(row)
                if it and self._item_key(it) == target_key:
                    idx = self.list_model.index(row)
                    self.listw.setCurrentIndex(idx)
                    self.listw.scrollTo(
                        idx, QAbstractItemView.ScrollHint.PositionAtCenter)
                    if mode == "series":
                        self._enter_series(it)
                    else:
                        self.play_item(it, "mpv")
                    return
            if mode == "series":
                self._enter_series(target)
            else:
                self.play_item(target, "mpv")

        QTimer.singleShot(300, after_load)

    def _clear_epg_rows(self) -> None:
        while self.epg_lay.count() > 1:
            w = self.epg_lay.takeAt(0).widget()
            if w:
                w.deleteLater()

    def _epg_note(self, text: str) -> None:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color:{P['muted2']}; font-size:12px;")
        lbl.setWordWrap(True)
        self.epg_lay.insertWidget(self.epg_lay.count() - 1, lbl)

    def _request_epg(self) -> None:
        it = self.list_model.item_at(self.listw.currentIndex().row())
        if not it or self.mode not in ("live", "fav") or self.series_ctx:
            return
        sid = it.get("stream_id")
        if sid is None:
            return
        key = self._item_key(it)
        self._clear_epg_rows()
        self._epg_note("Loading programme guide…")

        def fetch():
            listings = self.client.short_epg(sid, 8)
            if not listings:
                listings = self.client.epg_table(sid)
            if not listings:
                listings = self.xmltv.listings_for(it)
            return listings

        run_async(self.pool, fetch,
                  lambda e: self._show_epg(e, key),
                  lambda _: self._epg_error(key))

    def _epg_error(self, key) -> None:
        if key != self._current_key:
            return
        self.now_card.hide()
        self._clear_epg_rows()
        self._epg_note("Could not load the programme guide.")

    # -- movie/series info ---------------------------------------------------------

    def _request_media_info(self, kind: str, mid, key) -> None:
        cached = self._info_cache.get((kind, mid))
        if cached is not None:
            self._show_media_info(cached, key)
            return
        self._epg_note("Loading information…")
        if kind == "vod":
            fetch = lambda: (
                self.client.vod_info(mid) or {}).get("info") or {}
        else:
            fetch = lambda: (
                self.client.series_info(mid) or {}).get("info") or {}

        def done(info):
            if not isinstance(info, dict):
                info = {}
            self._info_cache[(kind, mid)] = info
            self._show_media_info(info, key)

        run_async(self.pool, fetch, done, lambda _: None)

    def _show_media_info(self, info: dict, key) -> None:
        if key != self._current_key:
            return
        self._clear_epg_rows()
        td = self._tmdb_details or {}
        plot = str(
            info.get("plot") or info.get("description")
            or td.get("overview") or "").strip()
        self.media_plot.setText(plot)
        self.media_plot.setVisible(bool(plot))

        parts: list[str] = []
        simple = (
            ("Genre", info.get("genre") or td.get("genres")),
            ("Director", info.get("director")),
            ("Released",
             info.get("releasedate") or info.get("releaseDate")
             or td.get("release_date")),
            ("Duration", info.get("duration")),
            ("Rating", info.get("rating")),
        )
        for label, value in simple:
            value = str(value or "").strip()
            if value:
                parts.append(f"<b>{label}:</b> {html.escape(value)}")
        cast = str(info.get("cast") or info.get("actors") or "").strip()
        if cast:
            names = [n.strip() for n in cast.split(",") if n.strip()]
            if self.tmdb:
                rendered = [
                    f'<a href="cast:{html.escape(n)}" '
                    f'style="color:{ACCENT}; text-decoration:none;">'
                    f'{html.escape(n)}</a>' for n in names]
            else:
                rendered = [html.escape(n) for n in names]
            parts.append("<b>Cast:</b> " + ", ".join(rendered))
        elif self.tmdb and td.get("cast"):
            names = [c.get("name") for c in td["cast"]
                     if c.get("name")]
            rendered = [
                f'<a href="cast:{html.escape(n)}" '
                f'style="color:{ACCENT}; text-decoration:none;">'
                f'{html.escape(n)}</a>' for n in names]
            if rendered:
                parts.append("<b>Cast:</b> " + ", ".join(rendered))
        self.media_meta.setText("<br>".join(parts))
        self.media_meta.setVisible(bool(parts))

        self.media_info.setVisible(bool(plot or parts))
        if not (plot or parts):
            self._epg_note("No further information available.")

    def _on_cast_link(self, href: str) -> None:
        if href.startswith("cast:"):
            self._on_cast_clicked(href[len("cast:"):], None)

    def _show_epg(self, listings, key) -> None:
        if key != self._current_key:
            return
        self.now_card.hide()
        self._clear_epg_rows()
        self._current_epg = None
        now = datetime.now().astimezone()
        all_posts, upcoming = [], []
        current = None
        seen: set = set()
        for e in listings or []:
            start, stop = epg_times(e)
            start, stop = self._apply_epg_delay(start), self._apply_epg_delay(stop)
            if e.get("_plain"):
                title, desc = (e.get("title") or "",
                               e.get("description") or "")
            else:
                title, desc = b64(e.get("title")), b64(e.get("description"))
            dedup_key = (int(start.timestamp()) if start else None,
                         title.strip().lower())
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            post = {"title": title, "desc": desc,
                    "start": start, "stop": stop}
            all_posts.append(post)
            if start and stop and start <= now < stop and not current:
                current = post
            elif start and start > now:
                upcoming.append(post)
        upcoming.sort(key=lambda p: p["start"])

        if current:
            self._current_epg = current
            self.now_time.setText(
                f"NOW * {current['start']:%H:%M}-{current['stop']:%H:%M}")
            self.now_title.setText(
                current["title"] or "Unknown programme")
            self.now_desc.setText(current["desc"][:400])
            self._refresh_progress()
            if not self._player_fs:
                self.now_card.show()
            if self.player:
                info = (
                    f"{self._detail_name}\n"
                    f"{current['title'] or 'Unknown programme'}   "
                    f"{current['start']:%H:%M}-{current['stop']:%H:%M}")
                nxt = upcoming[0] if upcoming else None
                if nxt:
                    info += (f"\nNext: {nxt['title'] or '?'}"
                             f" at {nxt['start']:%H:%M}")
                self.player.set_overlay_info(info)

        for post in upcoming[:6]:
            self._epg_card(post)

        if not current and not upcoming:
            dated = sorted(
                (p for p in all_posts if p["start"]),
                key=lambda p: p["start"])
            if dated:
                self._epg_note(
                    "The server's schedule times look wrong - "
                    "showing the most recent entries anyway.")
                for post in dated[-6:]:
                    self._epg_card(post, with_date=True)
            else:
                self._epg_note(
                    "No programme guide available for this channel.")

    def _epg_card(self, post, with_date: bool = False) -> None:
        card = QFrame(objectName="Card")
        kl = QVBoxLayout(card)
        kl.setContentsMargins(12, 9, 12, 9)
        kl.setSpacing(2)
        fmt = "%-d/%-m %H:%M" if with_date else "%H:%M"
        t = QLabel(post["start"].strftime(fmt), objectName="EpgRowTime")
        ti = QLabel(post["title"] or "Unknown", objectName="EpgRowTitle")
        ti.setWordWrap(True)
        kl.addWidget(t)
        kl.addWidget(ti)
        self.epg_lay.insertWidget(self.epg_lay.count() - 1, card)

    def _refresh_progress(self) -> None:
        e = self._current_epg
        if not e:
            return
        now = datetime.now().astimezone()
        if now >= e["stop"]:
            self._current_epg = None
            self._request_epg()
            return
        total = (e["stop"] - e["start"]).total_seconds()
        if total > 0:
            pct = (now - e["start"]).total_seconds() / total * 100
            self.now_bar.setValue(max(0, min(100, int(pct))))

    # -- series -> episodes --------------------------------------------------------

    def _enter_series(self, series) -> None:
        sid = series.get("series_id")
        if sid is None:
            return
        self.loading_bar.show()
        self._set_status(tr("status_loading_episodes"))

        def done(info):
            self.loading_bar.hide()
            episodes = []
            for season, eps in (info.get("episodes") or {}).items():
                for ep in eps:
                    ep["season"] = season
                    ep["name"] = (
                        f"S{season} * E{ep.get('episode_num', '?')} - "
                        f"{ep.get('title') or 'Episode'}")
                    episodes.append(ep)
            self.series_ctx = series
            self.all_items = episodes
            self.back_btn.show()
            self.search.clear()
            self._apply_filter()

        run_async(self.pool, lambda: self.client.series_info(sid),
                  done, self._error)

    def _leave_series(self) -> None:
        self.series_ctx = None
        self.back_btn.hide()
        cur = self.cat_list.currentItem()
        self._load_items(
            cur.data(Qt.ItemDataRole.UserRole) if cur else None)

    # -- playback ------------------------------------------------------------------

    def _stream_for(self, it) -> tuple[str | None, str]:
        title = it.get("name") or it.get("title") or "dopeIPTV"
        if self.series_ctx:
            return self.client.episode_url(
                it.get("id"), it.get("container_extension")), title
        if self.mode in ("live", "fav"):
            fmt = self.settings.value("stream_format", "ts")
            return self.client.live_url(it.get("stream_id"), fmt), title
        # Watch Later snapshots carry `_kind` set to "vod" or "series"
        # so the same movie playback code path works from that view
        # even though self.mode is 'watchlist'.
        eff_mode = self.mode
        if self.mode == "watchlist":
            eff_mode = it.get("_kind") or "vod"
        if eff_mode == "vod":
            return self.client.vod_url(
                it.get("stream_id"), it.get("container_extension")), title
        return None, title

    def play_live_channel(self, it) -> None:
        fmt = self.settings.value("stream_format", "ts")
        url = self.client.live_url(it.get("stream_id"), fmt)
        title = it.get("name") or "dopeIPTV"
        self._start_playback(url, title, it.get("stream_icon"),
                             self._item_key(it), "live", item=it)

    def play(self, player=None, external: bool = False) -> None:
        it = self.list_model.item_at(self.listw.currentIndex().row())
        self.play_item(it, player, external)

    def play_item(self, it, player=None, external: bool = False) -> None:
        if not it:
            return
        if self.mode == "series" and not self.series_ctx:
            self._enter_series(it)
            return
        # Series row from Watch Later: same 'drill in' behaviour as
        # from the Series view - open the episode list instead of
        # trying to play a series URL directly.
        if self.mode == "watchlist" and it.get("_kind") == "series":
            self._enter_series(it)
            return
        if self.mode == "rec":
            path = it.get("_path")
            if not path or not os.path.exists(path):
                return
            title = it.get("name") or "Recording"
            if external or player == "vlc":
                launch_player(player or "mpv", path, title, self)
                return
            self._start_playback(path, title, None, path, "recording",
                                 record=False)
            return
        if self.mode == "history":
            url = it.get("_url")
            title = it.get("name") or "dopeIPTV"
            icon, key, kind = (it.get("stream_icon"), it.get("_key"),
                               it.get("_kind"))
        else:
            url, title = self._stream_for(it)
            icon = it.get("stream_icon") or it.get("cover")
            key, kind = self._item_key(it), self._history_kind()
        if not url:
            return

        if external or player == "vlc":
            launch_player(player or "mpv", url, title, self)
            if self.mode != "history":
                self.history.add(url, title, icon, key, kind)
            return

        self._start_playback(url, title, icon, key, kind,
                             record=self.mode != "history", item=it)

    def _open_cast_dialog(self, it) -> None:
        if not ChromecastManager.available():
            QMessageBox.information(
                self, "Chromecast",
                tr("msg_cast_needs_package"))
            return
        if self.mode == "history":
            url, title = it.get("_url"), it.get("name") or "dopeIPTV"
        else:
            url, title = self._stream_for(it)
            if (self.mode in ("live", "fav")
                    and it.get("stream_id") is not None):
                url = self.client.live_url(it["stream_id"], "m3u8")
        if not url:
            return
        CastDialog(self, url, title).exec()

    def _autoplay_preview(self) -> bool:
        return self.settings.value("autoplay_preview", "true") == "true"

    def _play_preview(self) -> None:
        it = self.list_model.item_at(self.listw.currentIndex().row())
        if (not it or self.mode not in ("live", "fav") or self.series_ctx
                or not self.player
                or self.playback_mode() != "embedded"):
            return
        url, title = self._stream_for(it)
        if not url:
            return
        if self.player.current_url == url:
            return
        if not self._guard_stream_switch(url, title):
            return
        self.stream_error.hide()
        self._playing_key = self._item_key(it)
        self._playing_group = "live"
        self._playing_item = it
        # Also remember what's playing here (not just in _start_playback) so
        # a Stop -> Play round-trip after an auto-preview brings this channel
        # back instead of resuming nothing. Without this the Play button
        # after Stop prints '_resume_last: no _last_playback stored' because
        # auto-preview never went through _start_playback.
        self._last_playback = {"url": url, "title": title,
                               "icon_url": it.get("stream_icon"),
                               "key": self._item_key(it),
                               "kind": "live", "item": it}
        self._sync_player_buttons()
        self.listw.viewport().update()
        self.setWindowTitle(title or self._base_title)
        self._set_status(tr("status_playing", title=title))
        self.rec.finish_all_inplayer("channel changed")
        self.player.show()
        self.player.set_overlay_info(title)
        if self.player.play(url, title):
            self.wake.acquire(f"Playing {title}")

    def playback_mode(self) -> str:
        default = "embedded" if self.player else "window"
        mode = self.settings.value("playback_mode", default)
        if mode == "embedded" and not self.player:
            mode = "window"
        return mode

    # Content kinds whose playback position is worth remembering/resuming.
    _RESUMABLE = ("movie", "episode", "recording")

    def _save_resume_position(self) -> None:
        """Remember how far into the current title the user got, so it can be
        resumed later. Positions near the very start or end are dropped."""
        if not (self.player and self.player.current_url and self._playing_key
                and self._playing_group in ("vod", "episode", "rec")):
            return
        pos = self.player.playback_position()
        dur = self.player.playback_duration()
        rkey = f"{self._playing_group}:{self._playing_key}"
        if dur > 0 and 60 < pos < dur * 0.95:
            self._resume[rkey] = {"pos": round(pos), "dur": round(dur)}
        else:
            self._resume.pop(rkey, None)
        self.settings.setValue(self._resume_key, json.dumps(self._resume))

    def _resume_offset(self, key, kind: str) -> float:
        """Ask whether to resume a partly-watched title; return the start
        offset in seconds (0 to start from the beginning)."""
        group = {"movie": "vod", "episode": "episode",
                 "recording": "rec"}.get(kind)
        saved = self._resume.get(f"{group}:{key}") if group else None
        if not saved:
            return 0.0
        pos = float(saved.get("pos") or 0)
        if pos <= 60:
            return 0.0
        idx = self._choice_dialog(
            tr("resume_title"),
            tr("resume_prompt", time=self._fmt_hms(pos)),
            [(tr("resume_continue", time=self._fmt_hms(pos)), "primary"),
             (tr("resume_restart"), "normal")])
        return pos if idx == 0 else 0.0

    @staticmethod
    def _fmt_hms(seconds: float) -> str:
        seconds = max(0, int(seconds))
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    def _on_player_stopped(self) -> None:
        """The Stop button was pressed. Save the resume point while the player
        still knows the position, then clear the now-playing highlight/title.
        _last_playback is kept so Play can bring this title back."""
        self._save_resume_position()
        self._playing_key = None
        self._playing_group = None
        self._playing_item = None
        self._sync_player_buttons()
        self.listw.viewport().update()
        self.setWindowTitle(self._base_title)

    def _resume_last(self) -> None:
        """Replay the last-played title after a Stop (the mini-player Play
        button routes here when the player is empty)."""
        last = getattr(self, "_last_playback", None)
        if not last:
            return
        self._start_playback(last["url"], last["title"], last["icon_url"],
                             last["key"], last["kind"], record=False,
                             item=last["item"])

    def _start_playback(self, url: str, title: str, icon_url,
                        key, kind: str, record: bool = True,
                        item=None) -> None:
        if not self._guard_stream_switch(url, title):
            return
        # Remember where we were in whatever was playing before switching.
        self._save_resume_position()
        resume_at = (self._resume_offset(key, kind)
                     if kind in self._RESUMABLE else 0.0)
        self._trakt_stop_current()
        if record and kind:
            self.history.add(url, title, icon_url, key, kind)
        if kind in ("movie", "episode"):
            self._trakt_start_for_item(kind, item)
        self.stream_error.hide()
        self._playing_item = item if kind == "live" else None
        # Remember the full context so a Stop -> Play round-trip can replay
        # exactly this title (and resume where it left off) instead of falling
        # back to the first channel in the list.
        self._last_playback = {"url": url, "title": title, "icon_url": icon_url,
                               "key": key, "kind": kind, "item": item}
        self._sync_player_buttons()
        self._playing_key = key
        self._playing_group = {"live": "live", "movie": "vod",
                               "episode": "episode",
                               "recording": "rec"}.get(kind)
        self.listw.viewport().update()
        self.setWindowTitle(title or self._base_title)
        self._set_status(tr("status_playing", title=title))
        mode = self.playback_mode()
        if mode == "embedded" and self.player:
            self.rec.finish_all_inplayer("channel changed")
            self.player.show()
            self.player.set_overlay_info(title)
            if self.player.play(url, title, start=resume_at):
                self.wake.acquire(f"Playing {title}")
            else:
                self.player.hide()
                launch_player("mpv", url, title, self)
        elif mode == "window":
            if self.mpv_window:
                if self.mpv_window.play(url, title):
                    self.wake.acquire(f"Playing {title}")
                else:
                    launch_player("mpv", url, title, self)
            else:
                run_async(
                    self.pool, lambda: self.mpv.load(url, title),
                    lambda ok: None if ok
                    else self._player_missing("mpv"))
        else:
            launch_player("mpv", url, title, self)

    def _player_missing(self, name: str) -> None:
        QMessageBox.warning(
            self, tr("status_player_not_found"),
            tr("status_player_not_found_msg", name=name))

    def _show_toast(self, text: str, duration_ms: int = 0) -> None:
        self._toast.show_message(text, duration_ms)

    def _set_status(self, text: str, error: bool = False) -> None:
        self.count_lbl.setStyleSheet(
            f"color:{P['error']}; font-size:11px; font-weight:600;"
            if error
            else f"color:{P['muted3']}; font-size:11px;")
        self.count_lbl.setText(text)

    def _playback_error(self, msg: str) -> None:
        self.rec.finish_all_inplayer("stream error")
        self.wake.release()
        self._trakt_active = None
        if self.player:
            self.player.current_url = None
        self._set_status(f"Stream error: {msg}", error=True)
        if self._player_fs and self.player:
            self.player.set_overlay_info(f"Stream error: {msg}")
        else:
            self.stream_error.setText(tr("status_stream_error", msg=msg))
            self.stream_error.show()
        if self.player:
            self.player.title_lbl.setText("")

    def _zap(self, direction: int) -> None:
        if self.mode not in ("live", "fav", "vod", "series",
                             "history", "rec"):
            return
        if self.mode == "series" and not self.series_ctx:
            return
        count = self.list_model.rowCount()
        if count == 0:
            return
        row = self.listw.currentIndex().row()
        new_row = (row + direction) % count if row >= 0 else 0
        idx = self.list_model.index(new_row)
        self.listw.setCurrentIndex(idx)
        self.listw.scrollTo(idx)
        self.play()

    # -- context menu --------------------------------------------------------------

    def _context_menu(self, pos) -> None:
        idx = self.listw.indexAt(pos)
        if not idx.isValid():
            return
        it = self.list_model.item_at(idx.row())
        if not it:
            return
        if self.mode == "rec":
            self._rec_context_menu(pos, it)
            return
        m = QMenu(self)
        m.addAction(tr("ctx_play_in_mpv"), lambda: self.play_item(it, "mpv"))
        ext = m.addMenu(tr("ctx_open_externally"))
        ext.addAction("mpv",
                      lambda: self.play_item(it, "mpv", external=True))
        ext.addAction("VLC",
                      lambda: self.play_item(it, "vlc", external=True))
        if not (self.mode == "series" and not self.series_ctx):
            m.addAction(tr("ctx_cast_to_chromecast"),
                        lambda: self._open_cast_dialog(it))
        if (self.mode in ("live", "fav")
                and it.get("stream_id") is not None):
            if self._timeshift_days(it):
                m.addSeparator()
                self._build_timeshift_menu(
                    m.addMenu(tr("tooltip_timeshift")), it)
            m.addSeparator()
            self._build_record_menu(m.addMenu(tr("rec_record")), it)
        if (self.mode in ("live", "fav", "vod", "series")
                and it.get("stream_id") is not None):
            m.addSeparator()
            fav_menu = m.addMenu(tr("ctx_add_to_favorites"))
            for g in self.favs.group_names():
                fav_menu.addAction(
                    g, lambda g=g: self._add_fav(g, it))
            if self.favs.group_names():
                fav_menu.addSeparator()
            fav_menu.addAction(tr("ctx_new_group"),
                               lambda: self._add_fav(None, it))
            if (self.mode == "fav"
                    or self.favs.is_favorite(it.get("stream_id"))):
                m.addAction(tr("ctx_remove_from_favorites"),
                            lambda: self._remove_fav(it))
        if (self.mode in ("vod", "series") and not self.series_ctx
                and self.tmdb):
            m.addSeparator()
            m.addAction(tr("ctx_match_tmdb"),
                        lambda: self._open_tmdb_match_dialog(
                            it, "vod" if self.mode == "vod" else "series"))
        # Mark-as-watched and Watch-Later live behind the same TMDB
        # match. Both surface a 'local only' variant (no Trakt push)
        # and, when Trakt is connected, a 'and on Trakt' variant that
        # also POSTs the change so any other device sees it.
        # For rows in the Watch Later view, _kind on the snapshot
        # tells us whether the entry is a movie or a series so the
        # same code path works there.
        eff_mode = self.mode
        if self.mode == "watchlist":
            eff_mode = it.get("_kind") or "vod"
        elif self.mode == "history":
            # History stores 'movie'/'episode'/'live'; only movies get
            # the mark-as-watched actions here (episodes lack the
            # season/episode data those need).
            eff_mode = "vod" if it.get("_kind") == "movie" else None
        # Mark-as-watched does NOT need TMDB: the local flag keys on the
        # provider stream_id when no TMDB account is configured. Only
        # the '+ Trakt' variants need TMDB (Trakt's API is tmdb-keyed),
        # so those are gated on trakt-connected AND a TMDB resolver.
        if ((eff_mode == "vod")
                or (eff_mode == "series" and not self.series_ctx)
                or self.series_ctx):
            m.addSeparator()
            trakt_ok = self.trakt.is_connected() and self.tmdb is not None
            if self.series_ctx:
                mark = self._mark_episode_watched
                unmark = self._unmark_episode_watched
                watched = self.is_episode_watched(it)
            elif eff_mode == "vod":
                mark = self._mark_movie_watched
                unmark = self._unmark_movie_watched
                watched = self.is_movie_watched(it)
            else:
                # Series list item: local-only 'seen the whole show'.
                # Trakt has no whole-show watched primitive, only
                # per-episode, so we skip the +Trakt variant here.
                mark = self._mark_series_watched
                unmark = self._unmark_series_watched
                watched = self.show_watched_count(it) > 0
            if mark is not None:
                if watched:
                    m.addAction(tr("ctx_unmark_watched"),
                                lambda it=it: unmark(it, False))
                    if trakt_ok and mark is not self._mark_series_watched:
                        m.addAction(tr("ctx_unmark_watched_trakt"),
                                    lambda it=it: unmark(it, True))
                else:
                    m.addAction(tr("ctx_mark_watched"),
                                lambda it=it: mark(it, False))
                    if trakt_ok and mark is not self._mark_series_watched:
                        m.addAction(tr("ctx_mark_watched_trakt"),
                                    lambda it=it: mark(it, True))
        # Watch Later toggle only for movies and shows (not episodes).
        # Also works from within the Watch Later view - the row's
        # _kind maps it back to vod/series so the same store call
        # runs. That's what makes 'remove from Watch Later' reachable.
        # Watch Later also works without TMDB - the list stores a
        # snapshot keyed on the provider stream_id. TMDB only gates the
        # '+ Trakt' variants (Trakt watchlist is tmdb-keyed).
        wl_kind = None
        if not self.series_ctx:
            if self.mode in ("vod", "series"):
                wl_kind = self.mode
            elif self.mode == "watchlist":
                wl_kind = it.get("_kind")
            elif self.mode == "history":
                wl_kind = "vod" if it.get("_kind") == "movie" else None
        if wl_kind in ("vod", "series"):
            on_list = self.is_on_watchlist(it, wl_kind)
            trakt_ok = self.trakt.is_connected() and self.tmdb is not None
            if on_list:
                m.addAction(
                    tr("ctx_watchlist_remove"),
                    lambda it=it, k=wl_kind: self._remove_watchlist(it, k, False))
                if trakt_ok:
                    m.addAction(
                        tr("ctx_watchlist_remove_trakt"),
                        lambda it=it, k=wl_kind: self._remove_watchlist(
                            it, k, True))
            else:
                m.addAction(
                    tr("ctx_watchlist_add"),
                    lambda it=it, k=wl_kind: self._add_watchlist(it, k, False))
                if trakt_ok:
                    m.addAction(
                        tr("ctx_watchlist_add_trakt"),
                        lambda it=it, k=wl_kind: self._add_watchlist(
                            it, k, True))
        if (self.mode in ("live", "vod", "series")
                and not self.series_ctx):
            ov_mode = self.mode
            key = self._item_key(it)
            m.addSeparator()
            m.addAction(
                tr("ctx_rename_channel") if ov_mode == "live"
                else tr("ctx_rename"),
                lambda: self._rename_channel(ov_mode, key, it))
            m.addAction(
                tr("ctx_hide_channel") if ov_mode == "live" else tr("ctx_hide"),
                lambda: self._hide_channel(ov_mode, key))
            if self.channel_ov.get(ov_mode, key):
                m.addAction(
                    tr("ctx_reset_channel"),
                    lambda: self._reset_channel(ov_mode, key))
            if self.channel_ov.has_overrides(ov_mode):
                m.addAction(
                    tr("ctx_restore_defaults"),
                    lambda: self._restore_default_channels(ov_mode))
        if self.mode == "history":
            m.addSeparator()
            m.addAction(tr("ctx_remove_from_history"),
                        lambda: self._remove_history_selected(it))
        if (not (self.mode == "series" and not self.series_ctx)
                and self.mode != "history"):
            url, _ = self._stream_for(it)
            if url:
                m.addSeparator()
                m.addAction(
                    tr("ctx_copy_stream_url"),
                    lambda: QApplication.clipboard().setText(url))
        m.exec(self.listw.viewport().mapToGlobal(pos))

    # -- channel customizations ----------------------------------------------------

    def _rename_channel(self, mode: str, key, it) -> None:
        if key is None:
            return
        current = self.channel_ov.display_name(
            mode, key, it.get("name") or it.get("title") or "")
        name, ok = QInputDialog.getText(
            self, "Rename channel", "New name:", text=current)
        if ok:
            self.channel_ov.update(mode, key, name=name.strip())
            self._apply_filter()

    def _hide_channel(self, mode: str, key) -> None:
        if key is None:
            return
        self.channel_ov.update(mode, key, hidden=True)
        self._apply_filter()

    def _reset_channel(self, mode: str, key) -> None:
        self.channel_ov.update(mode, key, name="", hidden=False)
        self._apply_filter()

    def _open_tmdb_match_dialog(self, it: dict, kind: str) -> None:
        """Open the manual TMDB-match dialog for a movie/series and refresh
        the detail panel + list icon once the user picks (or clears) an
        override. kind is 'vod' for movies or 'series' for TV shows."""
        title = (it.get("name") or it.get("title") or "").strip()
        if not title or not self.tmdb:
            return

        def on_pick(_details) -> None:
            # Nudge the list model so the delegate re-queries the poster,
            # and rebuild the detail panel if this item is currently open.
            self.list_model.refresh_all()
            current = self.list_model.item_at(
                self.listw.currentIndex().row())
            if current and current is it:
                self._show_detail(it)

        TmdbMatchDialog(self, title, kind, on_pick).exec()

    def _restore_default_channels(self, mode: str) -> None:
        if QMessageBox.question(
                self, tr("ctx_restore_defaults").rstrip("."),
                tr("msg_restore_defaults_body")) \
                == QMessageBox.StandardButton.Yes:
            self.channel_ov.reset_mode(mode)
            self._apply_filter()

    # -- favorites -----------------------------------------------------------------

    def _add_fav(self, group, item) -> None:
        if group is None:
            group, ok = QInputDialog.getText(
                self, "New favorites group", "Group name:")
            group = (group or "").strip()
            if not ok or not group:
                return
        self.favs.add(group, item)
        if self.mode == "fav":
            self._load_categories()

    def _remove_fav(self, item) -> None:
        if self.mode == "fav":
            cur = self.cat_list.currentItem()
            group = cur.data(Qt.ItemDataRole.UserRole) if cur else None
            self.favs.remove(item.get("stream_id"), group)
            self._load_categories()
        else:
            self.favs.remove(item.get("stream_id"))
            self.list_model.refresh_all()

    # -- parental control ----------------------------------------------------------

    def _request_unlock(self) -> bool:
        if self.parental.session_unlocked:
            return True
        if not self.parental.has_pin():
            return True
        pin, ok = QInputDialog.getText(
            self, "Parental control", "Enter PIN:",
            QLineEdit.EchoMode.Password)
        if not ok:
            return False
        if self.parental.verify(pin.strip()):
            self.parental.session_unlocked = True
            return True
        QMessageBox.warning(self, tr("msg_parental_title"),
                            tr("msg_wrong_pin"))
        return False

    def _ensure_pin_configured(self) -> bool:
        if self.parental.has_pin():
            return True
        pin, ok = QInputDialog.getText(
            self, "Parental control",
            "No PIN is set yet. Choose a PIN to protect locked content:",
            QLineEdit.EchoMode.Password)
        pin = (pin or "").strip()
        if ok and pin:
            self.parental.set_pin(pin)
            return True
        return False

    # -- category context menu -----------------------------------------------------

    def _cat_menu(self, pos) -> None:
        it = self.cat_list.itemAt(pos)
        data = it.data(Qt.ItemDataRole.UserRole) if it else None
        m = QMenu(self)

        if self.mode == "fav":
            if not data:
                return
            group = data
            m.addAction(
                tr("ctx_remove_group", group=group),
                lambda: (self.favs.remove_group(group),
                         self._load_categories()))
            if self.favs.is_locked(group):
                m.addAction(tr("ctx_unlock_group"),
                            lambda: self._set_fav_lock(group, False))
            else:
                m.addAction(tr("ctx_lock_group"),
                            lambda: self._set_fav_lock(group, True))
            m.exec(self.cat_list.mapToGlobal(pos))
            return

        if self.mode not in ("live", "vod", "series"):
            return
        if data is not None:
            cid = data
            m.addAction(tr("ctx_rename_category"),
                        lambda: self._rename_category(cid))
            m.addAction(tr("ctx_set_icon"),
                        lambda: self._set_category_icon(cid))
            color_menu = m.addMenu(tr("ctx_set_color"))
            for label, hex_val in ((tr("color_default"), ""),
                                   (tr("accent_blue"), "#4C8DFF"),
                                   (tr("accent_green"), "#2FBF71"),
                                   (tr("accent_orange"), "#FF9F43"),
                                   (tr("accent_red"), "#FF5C5C"),
                                   (tr("accent_purple"), "#8E6BFF"),
                                   (tr("accent_teal"), "#2AC3C3"),
                                   (tr("accent_pink"), "#FF5C8A")):
                act = color_menu.addAction(label)
                act.triggered.connect(
                    lambda _, c=hex_val: (
                        self.overrides.update(self.mode, cid, color=c),
                        self._load_categories()))
            m.addSeparator()
            m.addAction(tr("ctx_hide_category"),
                        lambda: self._set_category_flag(cid, hidden=True))
            if self.overrides.is_locked(self.mode, cid):
                m.addAction(
                    tr("ctx_unlock_category"),
                    lambda: self._set_category_flag(cid, locked=False))
            else:
                m.addAction(tr("ctx_lock_category"),
                            lambda: self._lock_category(cid))
            m.addSeparator()
        m.addAction(tr("ctx_manage_categories"), self._open_content_manager)
        m.exec(self.cat_list.mapToGlobal(pos))

    def _set_fav_lock(self, group: str, locked: bool) -> None:
        if locked and not self._ensure_pin_configured():
            return
        self.favs.set_group_locked(group, locked)
        if locked:
            self.parental.lock_session()
        self._load_categories()

    def _rename_category(self, cid) -> None:
        current = self.overrides.display_name(
            self.mode, cid,
            next((c.get("category_name", "") for c in self._raw_categories
                  if c.get("category_id") == cid), ""))
        name, ok = QInputDialog.getText(
            self, "Rename category", "New name:", text=current)
        if ok:
            self.overrides.update(self.mode, cid, name=name.strip())
            self._load_categories()

    def _set_category_icon(self, cid) -> None:
        current = self.overrides.get(self.mode, cid).get("icon", "")
        icon, ok = QInputDialog.getText(
            self, "Set category icon",
            "Enter an emoji or short text (leave blank to remove):",
            text=current)
        if ok:
            self.overrides.update(self.mode, cid, icon=icon.strip())
            self._load_categories()

    def _set_category_flag(self, cid, **fields) -> None:
        self.overrides.update(self.mode, cid, **fields)
        self._load_categories()

    def _lock_category(self, cid) -> None:
        if not self._ensure_pin_configured():
            return
        self.parental.lock_session()
        self._set_category_flag(cid, locked=True)

    def _open_content_manager(self) -> None:
        if self.mode not in ("live", "vod", "series"):
            return
        ContentManagerDialog(
            self, self.mode, self._raw_categories, self.overrides).exec()
        self._load_categories()

    # -- history -------------------------------------------------------------------

    def _remove_history(self, item) -> None:
        self.history.remove(item.get("_key"), item.get("_kind"))
        self._load_items(None)

    def _remove_history_selected(self, clicked_item=None) -> None:
        items = [self.list_model.item_at(ix.row())
                 for ix in self.listw.selectionModel().selectedRows()]
        items = [it for it in items if it]
        if not items and clicked_item:
            items = [clicked_item]
        for it in items:
            self.history.remove(it.get("_key"), it.get("_kind"))
        if items:
            self._load_items(None)

    def _delete_pressed(self) -> None:
        if self.mode == "history":
            self._remove_history_selected()
        elif self.mode == "rec":
            self._delete_recordings_selected()

    # -- recordings ----------------------------------------------------------------

    def _job_item(self, j: dict) -> dict:
        label = {"recording": "● REC", "scheduled": "Scheduled",
                 "done": "Done", "failed": "Failed",
                 "cancelled": "Cancelled"}.get(j["status"], j["status"])
        start = datetime.fromtimestamp(
            j["start"]).strftime("%a %d %b %H:%M")
        stop = ("until stopped" if j.get("stop") is None
                else datetime.fromtimestamp(j["stop"]).strftime("%H:%M"))
        return {"name": f"[{label}] {j['title']}  ({start} – {stop})",
                "_job": j["id"], "_key": f"job:{j['id']}",
                "_kind": "recjob", "_status": j["status"],
                "_error": j.get("error") or "",
                "_path": j.get("path") or ""}

    def _recordings_changed(self) -> None:
        n = self.rec.active_count()
        self.rec_indicator.setText(
            f"● REC ({n})" if n > 1 else "● REC")
        self.rec_indicator.setVisible(n > 0)
        if self.player:
            for b in (self.player.rec_btn, self.player.fs_rec_btn):
                b.setToolTip(f"Recording ({n})" if n else "Record")
                b.setStyleSheet(
                    "color:#FF5C5C; font-weight:700;" if n
                    else "color:#FF5C5C;")
        if self.mode == "rec":
            cur = self.cat_list.currentItem()
            self._load_items(
                cur.data(Qt.ItemDataRole.UserRole) if cur else None)
        elif n:
            self._set_status(
                f"● Recording {n} stream{'s' if n > 1 else ''}...")

    def _on_recording_stopped(self, title: str, reason: str) -> None:
        abnormal = reason not in ("finished", "stopped")
        self._set_status(
            f"● Recording stopped: {title} ({reason})",
            error=abnormal)
        if self._player_fs and self.player:
            self.player.set_overlay_info(
                f"Recording stopped: {title} ({reason})")

    def _choice_dialog(self, title: str, message: str,
                       options: list[tuple[str, str]]) -> int | None:
        """A compact, themed confirmation dialog. *options* is a list of
        (label, kind) where kind is "primary"/"normal"/"danger"; returns the
        index of the clicked option, or None if dismissed. Replaces the
        oversized multi-button QMessageBox with clean stacked buttons."""
        d = QDialog(self)
        d.setWindowTitle(title)
        d.setModal(True)
        d.setMinimumWidth(420)
        lay = QVBoxLayout(d)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(16)
        lbl = QLabel(message)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"font-size:13px; color:{P['text2']};")
        lay.addWidget(lbl)
        btns = QVBoxLayout()
        btns.setSpacing(8)
        chosen: dict[str, int | None] = {"idx": None}
        for i, (label, kind) in enumerate(options):
            b = QPushButton(label)
            if kind == "primary":
                b.setObjectName("Primary")
            elif kind == "danger":
                b.setStyleSheet(
                    f"color:{P['rec']}; font-weight:600;")
            b.clicked.connect(
                lambda _c=False, i=i: (chosen.update(idx=i), d.accept()))
            btns.addWidget(b)
        lay.addLayout(btns)
        d.exec()
        return chosen["idx"]

    def _guard_stream_switch(self, url: str, title: str) -> bool:
        if not url or not str(url).startswith("http"):
            return True
        active = [j for j in self.rec.jobs if j["status"] == "recording"]
        if not active:
            return True
        inplayer = [j for j in active if j.get("inplayer")]
        if not inplayer and getattr(self, "_multi_stream_ok", False):
            return True
        if inplayer:
            if self.player and url == self.player.current_url:
                return True
            j = inplayer[0]
            can_cont = bool(j.get("url"))
            options = [("Stop recording and switch", "danger")]
            if can_cont:
                options.append(
                    ("Switch, keep recording on a new connection", "normal"))
            options.append(("Keep watching (recording continues)", "primary"))
            idx = self._choice_dialog(
                "Recording in progress",
                f"“{j['title']}” is being recorded from the stream you're "
                f"watching. Switching to “{title}” will stop that recording, "
                "unless you continue it over a second connection (needs a "
                "multi-stream account).",
                options)
            if idx == 0:
                self.rec.finish_all_inplayer("stopped")
                return True
            if can_cont and idx == 1:
                self.rec.finish_all_inplayer(
                    "continued over a new connection")
                self.rec.add_job(j["url"], f"{j['title']} (cont.)",
                                 time.time(), j.get("stop"))
                self._multi_stream_ok = True
                return True
            return False
        j = active[0]
        idx = self._choice_dialog(
            "Recording in progress",
            f"“{j['title']}” is being recorded over its own connection. If "
            f"your account only allows one stream at a time, starting "
            f"“{title}” can kill that recording.",
            [("Watch the recorded channel", "primary"),
             ("Play anyway (I have multiple streams)", "normal"),
             ("Cancel", "normal")])
        if idx == 0:
            self._watch_recording_file(j)
            return False
        if idx == 1:
            self._multi_stream_ok = True
            return True
        return False

    def _watch_recording_file(self, j: dict) -> None:
        path = j.get("path")
        if not path or not os.path.exists(path):
            QMessageBox.information(
                self, tr("rec_status_recording"),
                tr("msg_rec_file_not_ready"))
            return
        self._start_playback(path, f"{j['title']} (recording)", None,
                             path, "recording", record=False)

    def _rec_indicator_menu(self) -> None:
        m = QMenu(self)
        active = [j for j in self.rec.jobs if j["status"] == "recording"]
        for j in active:
            since = datetime.fromtimestamp(j["start"]).strftime("%H:%M")
            m.addAction(
                tr("rec_stop_named_since", title=j['title'], since=since),
                lambda jid=j["id"]: self.rec.cancel(jid))
        if active:
            m.addSeparator()
        m.addAction(tr("rec_open_recordings"),
                    lambda: self.switch_mode("rec"))
        m.exec(self.rec_indicator.mapToGlobal(
            self.rec_indicator.rect().bottomLeft()))

    def _sync_player_buttons(self) -> None:
        if not self.player:
            return
        it = self._playing_item
        live = bool(it) and it.get("stream_id") is not None
        ts = live and self._timeshift_days(it) > 0
        for b in (self.player.ts_btn, self.player.fs_ts_btn):
            b.setVisible(ts)
        for b in (self.player.rec_btn, self.player.fs_rec_btn):
            b.setVisible(live)

    def _player_timeshift_menu(self, anchor) -> None:
        it = self._playing_item
        if not it or not self._timeshift_days(it):
            return
        m = QMenu(self)
        self._build_timeshift_menu(m, it)
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _player_record_menu(self, anchor) -> None:
        it = self._playing_item
        if not it or it.get("stream_id") is None:
            return
        m = QMenu(self)
        self._build_record_menu(m, it)
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _recorder_ready(self) -> bool:
        if self.rec.recorder()[1]:
            return True
        QMessageBox.warning(
            self, tr("rec_status_recording"),
            tr("msg_rec_needs_ffmpeg"))
        return False

    def _build_record_menu(self, rec_menu, it) -> None:
        active = [j for j in self.rec.jobs if j["status"] == "recording"]
        for j in active:
            rec_menu.addAction(
                "■ " + tr("rec_stop_recording") + f": {j['title']}",
                lambda jid=j["id"]: self.rec.cancel(jid))
        if active:
            rec_menu.addSeparator()
        rec_menu.addAction(tr("rec_record_now_until_stopped"),
                           lambda: self._record_now(it, None))
        for dur_key, mins in (("dur_30min", 30), ("dur_1h", 60),
                              ("dur_2h", 120), ("dur_4h", 240)):
            rec_menu.addAction(
                tr("rec_record_now_duration", duration=tr(dur_key)),
                lambda mins=mins: self._record_now(it, mins))
        rec_menu.addSeparator()
        rec_menu.addAction(tr("rec_schedule_recording"),
                           lambda: self._schedule_recording(it))
        cap_menu = rec_menu.addMenu(tr("rec_size_limit_session"))
        current = self.rec.session_cap
        presets = (("From Settings", None), ("No limit", 0),
                   ("250 MB", 250 * 10**6),
                   ("500 MB", 500 * 10**6),
                   ("1 GB", 10**9), ("2 GB", 2 * 10**9),
                   ("5 GB", 5 * 10**9),
                   ("10 GB", 10 * 10**9),
                   ("50 GB", 50 * 10**9),
                   ("100 GB", 100 * 10**9))
        for label, cap in presets:
            act = cap_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(cap == current)
            act.triggered.connect(
                lambda _c, cap=cap: setattr(
                    self.rec, "session_cap", cap))
        cap_menu.addSeparator()
        known_caps = {cap for _label, cap in presets}
        custom_label = "Custom size..."
        if current and current not in known_caps:
            custom_label = f"Custom size... (currently {format_size(current)})"
        custom_act = cap_menu.addAction(custom_label)
        custom_act.setCheckable(True)
        custom_act.setChecked(bool(current) and current not in known_caps)
        custom_act.triggered.connect(self._set_custom_rec_cap)

    def _set_custom_rec_cap(self) -> None:
        d = QDialog(self)
        d.setWindowTitle(tr("rec_custom_size_title"))
        d.setMinimumWidth(320)
        f = QFormLayout(d)
        f.setSpacing(10)
        row = QHBoxLayout()
        val_edit = QLineEdit()
        val_edit.setPlaceholderText("e.g. 75")
        unit_box = self._combo(
            [("MB", "MB"), ("GB", "GB"), ("TB", "TB")], "GB")
        row.addWidget(val_edit)
        row.addWidget(unit_box)
        f.addRow(tr("rec_stop_recording_at"), row)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        for b in buttons.buttons():
            b.setIcon(QIcon())
        buttons.accepted.connect(d.accept)
        buttons.rejected.connect(d.reject)
        f.addRow(buttons)
        if not d.exec():
            return
        try:
            val = float(val_edit.text().strip().replace(",", "."))
        except ValueError:
            return
        if val <= 0:
            return
        mult = {"MB": 10**6, "GB": 10**9, "TB": 10**12}[unit_box.currentData()]
        self.rec.session_cap = int(val * mult)

    def _record_now(self, it, minutes) -> None:
        if it.get("stream_id") is None:
            return
        title = self.channel_display_name(it)
        now = time.time()
        stop_ts = None if minutes is None else now + minutes * 60
        length = ("until stopped" if minutes is None
                  else f"for {minutes} min")

        watching_this = (
            self.player is not None
            and self.player.isVisible()
            and self.playback_mode() == "embedded"
            and self._playing_group == "live"
            and self._playing_key == self._item_key(it))
        if watching_this:
            try:
                path = self.rec.build_path(title)
            except OSError as e:
                QMessageBox.warning(self, tr("rec_status_recording"), str(e))
                return
            if self.player.start_stream_record(path):
                self.rec.add_inplayer_job(
                    title, path, stop_ts,
                    url=self.client.live_url(it["stream_id"], "ts"))
                self._set_status(
                    f"● Recording {title} {length} - capturing "
                    "the stream you're watching (no extra connection)")
                return

        if not self._recorder_ready():
            return
        url = self.client.live_url(it["stream_id"], "ts")
        self.rec.add_job(url, title, now, stop_ts)
        self._set_status(
            f"● Recording {title} {length} → {self.rec.directory()}")

    def _schedule_recording(self, it) -> None:
        if not self._recorder_ready() or it.get("stream_id") is None:
            return
        d = QDialog(self)
        d.setWindowTitle(tr("rec_schedule_recording").rstrip("."))
        d.setMinimumWidth(380)
        f = QFormLayout(d)
        f.setSpacing(10)
        name_edit = QLineEdit(self.channel_display_name(it))
        start_edit = QDateTimeEdit(QDateTime.currentDateTime())
        start_edit.setCalendarPopup(True)
        start_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        stop_edit = QDateTimeEdit(
            QDateTime.currentDateTime().addSecs(3600))
        stop_edit.setCalendarPopup(True)
        stop_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        folder_box = QComboBox()
        folder_box.addItem("(Recordings folder)", "")
        for rel in self.rec.folders():
            folder_box.addItem(rel, rel)
        f.addRow(tr("playlist_name"), name_edit)
        f.addRow(tr("field_start"), start_edit)
        f.addRow(tr("field_stop"), stop_edit)
        f.addRow(tr("field_save_in"), folder_box)
        hint = QLabel(
            f"Saved under {self.rec.directory()} - change the "
            "location in Settings → Recording. The app must be "
            "running when the recording starts.")
        hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        hint.setWordWrap(True)
        f.addRow(hint)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        f.addRow(bb)
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        start_ts = start_edit.dateTime().toSecsSinceEpoch()
        stop_ts = stop_edit.dateTime().toSecsSinceEpoch()
        if stop_ts <= start_ts or stop_ts <= time.time():
            QMessageBox.warning(
                self, tr("rec_schedule_recording").rstrip("."),
                tr("msg_stop_time_future"))
            return
        url = self.client.live_url(it["stream_id"], "ts")
        title = (name_edit.text().strip()
                 or self.channel_display_name(it))
        self.rec.add_job(url, title, start_ts, stop_ts,
                         folder_box.currentData())
        when = datetime.fromtimestamp(start_ts).strftime(
            "%a %d %b %H:%M")
        self._set_status(
            f"Recording of {title} scheduled for {when}")

    def _edit_job_times(self, job_id: str) -> None:
        job = next((j for j in self.rec.jobs if j["id"] == job_id), None)
        if not job or job["status"] != "scheduled":
            return
        d = QDialog(self)
        d.setWindowTitle(tr("msg_edit_time_title"))
        d.setMinimumWidth(380)
        f = QFormLayout(d)
        f.setSpacing(10)
        start_edit = QDateTimeEdit(
            QDateTime.fromSecsSinceEpoch(int(job["start"])))
        start_edit.setCalendarPopup(True)
        start_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        stop_edit = QDateTimeEdit(
            QDateTime.fromSecsSinceEpoch(int(job["stop"] or (
                job["start"] + 3600))))
        stop_edit.setCalendarPopup(True)
        stop_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        f.addRow(tr("field_title"), QLabel(job["title"]))
        f.addRow(tr("field_start"), start_edit)
        f.addRow(tr("field_stop"), stop_edit)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        f.addRow(bb)
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        start_ts = start_edit.dateTime().toSecsSinceEpoch()
        stop_ts = stop_edit.dateTime().toSecsSinceEpoch()
        if stop_ts <= start_ts:
            QMessageBox.warning(
                self, tr("msg_edit_time_title"),
                tr("msg_stop_after_start"))
            return
        self.rec.update_job_times(job_id, start_ts, stop_ts)

    def _selected_recordings(self, clicked_item=None) -> list[dict]:
        items = [self.list_model.item_at(ix.row())
                 for ix in self.listw.selectionModel().selectedRows()]
        items = [it for it in items if it and it.get("_path")
                 and it.get("_kind") == "recording"]
        if (not items and clicked_item
                and clicked_item.get("_path")
                and clicked_item.get("_kind") == "recording"):
            items = [clicked_item]
        return items

    def _remove_jobs_selected(self, clicked_item=None) -> None:
        items = [self.list_model.item_at(ix.row())
                 for ix in self.listw.selectionModel().selectedRows()]
        items = [it for it in items if it and it.get("_job")]
        if not items and clicked_item and clicked_item.get("_job"):
            items = [clicked_item]
        for it in items:
            if it.get("_status") == "recording":
                continue
            if it.get("_status") == "scheduled":
                self.rec.cancel(it["_job"])
            self.rec.remove_job(it["_job"])

    def _delete_recordings_selected(self, clicked_item=None) -> None:
        cur = self.cat_list.currentItem()
        if cur and cur.data(Qt.ItemDataRole.UserRole) == "__jobs__":
            self._remove_jobs_selected(clicked_item)
            return
        items = self._selected_recordings(clicked_item)
        if not items:
            return
        what = (tr("rec_n_recordings", n=len(items)) if len(items) > 1
                else f"'{items[0]['name']}'")
        if QMessageBox.question(
                self, tr("msg_delete_rec_title"),
                tr("msg_delete_rec_body", what=what)) \
                != QMessageBox.StandardButton.Yes:
            return
        for it in items:
            try:
                os.remove(it["_path"])
                self.rec.prune_path(it["_path"])
            except OSError as e:
                self._set_status(
                    f"Could not delete: {e}", error=True)
        cur = self.cat_list.currentItem()
        self._load_items(
            cur.data(Qt.ItemDataRole.UserRole) if cur else None)

    def _rename_recording(self, it) -> None:
        path = it.get("_path")
        if not path:
            return
        name, ok = QInputDialog.getText(
            self, "Rename recording", "New name:",
            text=it.get("name", ""))
        name = (safe_filename(name.strip())
                if ok and name.strip() else "")
        if not name:
            return
        new_path = os.path.join(
            os.path.dirname(path),
            name + os.path.splitext(path)[1])
        try:
            os.rename(path, new_path)
        except OSError as e:
            QMessageBox.warning(self, tr("msg_rename_rec_title"), str(e))
        cur = self.cat_list.currentItem()
        self._load_items(
            cur.data(Qt.ItemDataRole.UserRole) if cur else None)

    def _move_recordings(self, items: list[dict],
                         folder: str) -> None:
        target = os.path.join(self.rec.directory(), folder)
        try:
            os.makedirs(target, exist_ok=True)
            for it in items:
                shutil.move(it["_path"], os.path.join(
                    target, os.path.basename(it["_path"])))
        except OSError as e:
            QMessageBox.warning(self, tr("msg_move_rec_title"), str(e))
        self._load_categories()

    def _new_rec_folder(self, items=None) -> None:
        name, ok = QInputDialog.getText(
            self, tr("msg_new_folder_title"), tr("msg_folder_name"))
        name = (safe_filename(name.strip())
                if ok and name.strip() else "")
        if not name:
            return
        try:
            os.makedirs(
                os.path.join(self.rec.directory(), name),
                exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "New folder", str(e))
            return
        if items:
            self._move_recordings(items, name)
        else:
            self._load_categories()

    # -- EPG / replay time offsets ---------------------------------------------------

    def _epg_delay_minutes(self) -> int:
        try:
            return int(self.settings.value("epg_delay_min", 0))
        except (TypeError, ValueError):
            return 0

    def _replay_delay_minutes(self) -> int:
        try:
            return int(self.settings.value("replay_delay_min", 0))
        except (TypeError, ValueError):
            return 0

    def _apply_epg_delay(self, dt):
        if dt is None:
            return None
        mins = self._epg_delay_minutes()
        return dt + timedelta(minutes=mins) if mins else dt

    # -- timeshift / catch-up ------------------------------------------------------

    @staticmethod
    def _timeshift_days(it) -> int:
        try:
            if int(it.get("tv_archive") or 0):
                return int(it.get("tv_archive_duration") or 1) or 1
        except (TypeError, ValueError):
            pass
        return 0

    def _play_timeshift(self, it, back_min=None, prog=None) -> None:
        sid = it.get("stream_id")
        days = self._timeshift_days(it)
        if sid is None or not days:
            return
        now = time.time()
        if prog:
            start = prog["start_timestamp"]
            duration_min = max(
                1, int((prog["stop_timestamp"] - start) // 60) + 2)
            what = prog.get("title") or "programme"
        else:
            start = now - (back_min or 30) * 60
            duration_min = max(1, int((now - start) // 60) + 1)
            what = None
        start = max(start, now - days * 86400)
        start += self._replay_delay_minutes() * 60
        url = self.client.timeshift_url(
            sid, datetime.fromtimestamp(start), duration_min)
        name = self.channel_display_name(it)
        title = (f"{what} ({name}, timeshift)" if what
                 else f"{name} (timeshift)")
        self._start_playback(url, title, it.get("stream_icon"),
                             self._item_key(it), "live", record=False,
                             item=it)

    # (minutes back, duration i18n key) - the label is "Go back <duration>".
    TIMESHIFT_STEPS = (
        (30, "dur_30min"), (60, "dur_1h"), (120, "dur_2h"), (360, "dur_6h"),
        (720, "dur_12h"), (1440, "dur_1d"), (2880, "dur_2d"),
        (4320, "dur_3d"), (7200, "dur_5d"), (10080, "dur_7d"),
    )

    def _build_timeshift_menu(self, ts_menu, it) -> None:
        days = self._timeshift_days(it)
        ts_menu.addAction(tr("ts_go_live"),
                          lambda: self.play_live_channel(it))
        ts_menu.addSeparator()
        prog = self.xmltv.current_programme(it)
        if prog:
            ts_menu.addAction(
                tr("ts_watch_from_start_named", title=prog['title']),
                lambda: self._play_timeshift(it, prog=prog))
        ts_menu.addAction(tr("ts_browse_past"),
                          lambda: self._open_catchup_dialog(it))
        ts_menu.addSeparator()
        for mins, dur_key in self.TIMESHIFT_STEPS:
            if mins > days * 1440:
                break
            ts_menu.addAction(
                tr("ts_go_back", t=tr(dur_key)),
                lambda mins=mins: self._play_timeshift(it, back_min=mins))
        note = ts_menu.addAction(tr("ts_archive_depth", n=days))
        note.setEnabled(False)

    def _open_catchup_dialog(self, it) -> None:
        days = self._timeshift_days(it)
        if not days:
            return
        d = QDialog(self)
        d.setWindowTitle(
            tr("ts_catchup_title", name=self.channel_display_name(it)))
        d.setMinimumSize(480, 500)
        lay = QVBoxLayout(d)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)
        info = QLabel(tr("ts_loading_past"))
        info.setWordWrap(True)
        lay.addWidget(info)
        lst = QListWidget()
        lay.addWidget(lst, 1)
        btns = QHBoxLayout()
        watch_btn = QPushButton(tr("common_watch"), objectName="Primary")
        close_btn = QPushButton(tr("common_close"))
        btns.addStretch()
        btns.addWidget(watch_btn)
        btns.addWidget(close_btn)
        lay.addLayout(btns)
        close_btn.clicked.connect(d.reject)

        def watch(_item=None):
            cur = lst.currentItem()
            p = cur.data(Qt.ItemDataRole.UserRole) if cur else None
            if p:
                self._play_timeshift(it, prog=p)
                d.accept()

        watch_btn.clicked.connect(watch)
        lst.itemDoubleClicked.connect(watch)

        def fetch():
            progs = self.xmltv.past_programmes(it, days)
            if progs or it.get("stream_id") is None:
                return progs
            now = time.time()
            out = []
            for e in self.client.epg_table(it["stream_id"]):
                start, stop = epg_times(e)
                start, stop = (self._apply_epg_delay(start),
                              self._apply_epg_delay(stop))
                if not start or not stop:
                    continue
                start_ts, stop_ts = start.timestamp(), stop.timestamp()
                if stop_ts <= now and start_ts >= now - days * 86400:
                    out.append({
                        "title": b64(e.get("title")) or "?",
                        "start_timestamp": int(start_ts),
                        "stop_timestamp": int(stop_ts),
                    })
            out.sort(key=lambda p: p["start_timestamp"], reverse=True)
            return out

        def done(progs):
            if not progs:
                info.setText(
                    "The guide has no past programmes for this "
                    "channel - use 'Go back ...' instead.")
                return
            info.setText(
                f"{len(progs)} programmes - the provider archives "
                f"{days} day{'s' if days != 1 else ''} back. "
                "Double-click to watch.")
            last_day = None
            for p in progs:
                start = datetime.fromtimestamp(p["start_timestamp"])
                stop = datetime.fromtimestamp(p["stop_timestamp"])
                day = start.strftime("%A %d %B")
                if day != last_day:
                    last_day = day
                    head = QListWidgetItem(f"—  {day}  —")
                    head.setFlags(Qt.ItemFlag.NoItemFlags)
                    lst.addItem(head)
                row = QListWidgetItem(
                    f"{start.strftime('%H:%M')}–"
                    f"{stop.strftime('%H:%M')}   "
                    f"{p.get('title') or '?'}")
                row.setData(Qt.ItemDataRole.UserRole, p)
                lst.addItem(row)

        run_async(self.pool, fetch, done,
                  lambda e: info.setText(
                      f"Could not load the guide: {e}"))
        d.exec()

    # -- recording context menu ----------------------------------------------------

    def _rec_context_menu(self, pos, it) -> None:
        m = QMenu(self)
        if it.get("_kind") == "recjob":
            status = it.get("_status")
            if it.get("_path"):
                m.addAction(tr("common_watch"), lambda: self.play_item(it))
            if status == "recording":
                m.addAction(tr("rec_stop_recording"),
                            lambda: self.rec.cancel(it["_job"]))
            elif status == "scheduled":
                m.addAction(tr("rec_edit_times"),
                            lambda: self._edit_job_times(it["_job"]))
                m.addAction(tr("rec_cancel_scheduled"),
                            lambda: self.rec.cancel(it["_job"]))
            else:
                m.addAction(tr("rec_remove_from_list"),
                            lambda: self._remove_jobs_selected(it))
            m.addSeparator()
            m.addAction(tr("rec_clear_finished"),
                        lambda: self.rec.clear_finished())
        else:
            items = self._selected_recordings(it)
            many = len(items) > 1
            m.addAction(tr("ctx_play_in_mpv"),
                        lambda: self.play_item(it, "mpv"))
            m.addAction(tr("ctx_play_in_vlc"),
                        lambda: self.play_item(it, "vlc"))
            m.addSeparator()
            m.addAction(tr("ctx_rename"),
                        lambda: self._rename_recording(it))
            move = m.addMenu(
                tr("ctx_move_to") if not many
                else tr("rec_move_n", n=len(items)))
            move.addAction(
                tr("rec_move_root"),
                lambda: self._move_recordings(items, ""))
            for rel in self.rec.folders():
                move.addAction(
                    rel,
                    lambda rel=rel: self._move_recordings(items, rel))
            move.addSeparator()
            move.addAction(tr("ctx_new_folder"),
                           lambda: self._new_rec_folder(items))
            m.addAction(
                tr("ctx_delete") if not many
                else tr("rec_delete_n", n=len(items)),
                lambda: self._delete_recordings_selected(it))
        m.addSeparator()
        m.addAction(tr("ctx_new_folder"), lambda: self._new_rec_folder())
        m.addAction(tr("rec_change_folder"),
                    lambda: self._choose_rec_dir())
        m.exec(self.listw.viewport().mapToGlobal(pos))

    def _choose_rec_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "Choose recordings folder", self.rec.directory(),
            QFileDialog.Option.DontUseNativeDialog
            | QFileDialog.Option.ShowDirsOnly)
        if d:
            self.rec.set_directory(d)
            if self.mode == "rec":
                self._load_categories()

    def _clear_history(self) -> None:
        if QMessageBox.question(
                self, tr("msg_clear_history_title"),
                tr("msg_clear_history_body")) \
                == QMessageBox.StandardButton.Yes:
            self.history.clear()
            self._load_items(None)

    # -- EPG guide -----------------------------------------------------------------

    def _open_epg_guide(self) -> None:
        self._ensure_xmltv_loaded()
        if self.mode == "live" and self.all_items:
            cat = self.cat_list.currentItem()
            cat_name = cat.text() if cat else "All"
            EpgGuideDialog(self, list(self.all_items), cat_name).exec()
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("btn_epg_guide"))
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(tr("status_loading_channels")))
        dlg.resize(300, 100)
        dlg.show()

        def done(channels):
            dlg.close()
            EpgGuideDialog(self, channels or []).exec()

        run_async(self.pool, lambda: self.client.live_streams(None),
                  done, lambda _: dlg.close())

    # -- view settings -------------------------------------------------------------

    def _apply_view_settings(self) -> None:
        density = self.settings.value("view_density", "medium")
        grid = self.settings.value("view_grid", "false") == "true"
        self.delegate.set_density(density)
        self.delegate.set_grid(grid)
        if grid:
            self.listw.setViewMode(QListView.ViewMode.IconMode)
            self.listw.setFlow(QListView.Flow.LeftToRight)
            self.listw.setWrapping(True)
            self.listw.setResizeMode(QListView.ResizeMode.Adjust)
            self.listw.set_grid_cell(self.delegate.grid_size())
        else:
            self.listw.setViewMode(QListView.ViewMode.ListMode)
            self.listw.setFlow(QListView.Flow.TopToBottom)
            self.listw.setWrapping(False)
            self.listw.set_grid_cell(None)
            self.listw.setGridSize(QSize())
        self.listw.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        step = (self.delegate.grid_size().height() // 2 if grid
                else self.delegate.row_h)
        self.listw.verticalScrollBar().setSingleStep(max(30, step))
        if hasattr(self, "size_box"):
            for box, key in (
                (self.size_box, density),
                (self.sort_box,
                 self.settings.value("sort_order", "default")),
            ):
                box.blockSignals(True)
                i = box.findData(key)
                if i >= 0:
                    box.setCurrentIndex(i)
                box.blockSignals(False)
            self.grid_btn.blockSignals(True)
            self.grid_btn.setChecked(grid)
            self.grid_btn.blockSignals(False)
        self._apply_filter()

    def _set_theme(self, theme: str, accent: str) -> None:
        self.settings.setValue("theme", theme)
        self.settings.setValue("accent", accent)
        apply_theme(self.settings)
        QApplication.instance().setStyleSheet(build_style())
        self._sidebar_logo.update()
        self.listw.viewport().update()
        self.count_lbl.setStyleSheet(
            f"color:{P['muted3']}; font-size:11px;")
        if self.d_logo.text():
            self.d_logo.setStyleSheet(self.PLACEHOLDER_LOGO_STYLE)
        if self.player:
            # The drawn control icons are baked with the old text colour;
            # redraw them for the new theme.
            self.player.refresh_icons()

    def _reset_all_settings(self, parent_dialog) -> None:
        """Wipe every stored preference (theme, layout, playlists, favorites,
        history, credentials, ...) and ask the user to restart. Two-step
        confirmation because it can't be undone. Playlists live in the same
        QSettings config, so this really does reset back to the login screen."""
        first = QMessageBox.question(
            parent_dialog, tr("settings_reset_all"),
            tr("settings_reset_confirm_1"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if first != QMessageBox.StandardButton.Yes:
            return
        second = QMessageBox.warning(
            parent_dialog, tr("settings_reset_all"),
            tr("settings_reset_confirm_2"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if second != QMessageBox.StandardButton.Yes:
            return
        # Clear() drops every key from this QSettings scope; sync() flushes
        # it to disk before we tell the user to restart.
        self.settings.clear()
        self.settings.sync()
        QMessageBox.information(
            parent_dialog, tr("settings_reset_all"),
            tr("settings_reset_done"))
        parent_dialog.reject()
        # Quit so the next launch reads a fresh (empty) config and shows the
        # login screen. Direct restart is intentionally left to the user so
        # they can inspect that everything actually went away.
        QApplication.instance().quit()

    def _set_language(self, code: str) -> None:
        from .i18n import set_language, current_language
        if code == current_language():
            return
        self.settings.setValue("language", code)
        set_language(code)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        """Re-apply every translatable string on the persistent chrome so
        the language switches live. The settings dialog and context menus
        are rebuilt each time they open, so they pick up the language on
        their own and don't need touching here."""
        for action, getter in getattr(self, "_i18n_actions", {}).items():
            action.setText(getter())
        nav_labels = {
            "live": "nav_tv", "vod": "nav_movies", "series": "nav_series",
            "fav": "nav_favorites", "watchlist": "nav_watchlist",
            "rec": "nav_recordings", "history": "nav_history",
        }
        for key, btn in self.nav_btns.items():
            if key in nav_labels:
                btn.setText(tr(nav_labels[key]))
        self._cat_section_label.setText(tr("sidebar_categories"))
        self._guide_btn.setText(tr("btn_epg_guide"))
        self._refresh_btn.setText(tr("btn_refresh"))
        self._refresh_btn.setToolTip(tr("tooltip_reload_channels_epg"))
        self._settings_btn.setText(tr("btn_settings"))
        self.search.setPlaceholderText(tr("search_placeholder"))
        self._size_label.setText(tr("label_size"))
        self._sort_label.setText(tr("label_sort"))
        self.play_mpv.setText("▶  " + tr("btn_play"))
        self.play_mpv.setToolTip(tr("tooltip_play_in_mpv"))
        # Player control tooltips (embedded bar + fullscreen overlay).
        if self.player:
            self.player.retranslate_ui()

    def _inline_view_changed(self, *_) -> None:
        self.settings.setValue(
            "view_density", self.size_box.currentData())
        self.settings.setValue(
            "sort_order", self.sort_box.currentData())
        self.settings.setValue(
            "view_grid",
            "true" if self.grid_btn.isChecked() else "false")
        self._apply_view_settings()

    # -- settings dialog -----------------------------------------------------------

    @staticmethod
    def _combo(items, current) -> QComboBox:
        box = QComboBox()
        for value, label in items:
            box.addItem(label, value)
        idx = box.findData(current)
        if idx >= 0:
            box.setCurrentIndex(idx)
        if sys.platform == "darwin":
            # On macOS the styled combo doesn't grow to fit its text, so the
            # closed box clips ("Playba…", "Sven…"). Size it to the widest
            # entry (plus a little for the arrow). Linux is left as-is.
            box.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToContents)
            longest = max((len(lbl) for _v, lbl in items), default=0)
            box.setMinimumContentsLength(longest + 2)
        return box

    def open_settings(self) -> None:
        d = QDialog(self)
        d.setWindowTitle(tr("settings_title"))
        d.setMinimumSize(820, 600)
        outer = QVBoxLayout(d)
        outer.setContentsMargins(18, 18, 18, 18)
        tabs = QTabWidget()
        # On macOS the native tab style hands each tab a fixed slot and elides
        # anything that doesn't fit ("Playba…", "Interfac…"). Ask the tab bar
        # to never elide and let scroll buttons appear if we run out of room
        # instead. No effect on Linux, where tabs already size to their text.
        if sys.platform == "darwin":
            tabs.tabBar().setElideMode(Qt.TextElideMode.ElideNone)
            tabs.tabBar().setUsesScrollButtons(True)
            tabs.setStyleSheet(
                "QTabBar::tab { padding: 6px 14px; min-width: 90px; }")
        outer.addWidget(tabs)

        # Playback tab
        play_tab = QWidget()
        pf = QFormLayout(play_tab)
        pf.setSpacing(10)
        mode_items = [("embedded", tr("option_embedded")),
                      ("window", tr("option_reused_window")),
                      ("external", tr("option_external_player"))]
        if not self.player:
            mode_items = [m for m in mode_items if m[0] != "embedded"]
        mode_box = self._combo(mode_items, self.playback_mode())
        autoplay_box = self._combo(
            [("true", tr("option_yes")), ("false", tr("option_no"))],
            "true" if self._autoplay_preview() else "false")
        fmt_box = self._combo(
            [("ts", "ts"), ("m3u8", "m3u8")],
            self.settings.value("stream_format", "ts"))
        LANGS = [
            ("", tr("option_lang_auto")), ("swe", tr("lang_swe")),
            ("eng", tr("lang_eng")), ("nor", tr("lang_nor")),
            ("dan", tr("lang_dan")), ("fin", tr("lang_fin")),
            ("ger", tr("lang_ger")), ("fre", tr("lang_fre")),
            ("spa", tr("lang_spa")), ("ita", tr("lang_ita")),
            ("por", tr("lang_por")), ("pol", tr("lang_pol")),
            ("ara", tr("lang_ara")), ("tur", tr("lang_tur")),
        ]
        alang_box = self._combo(
            LANGS, self.settings.value("audio_lang", ""))
        sub_box = self._combo(
            [("off", tr("option_sub_off")), ("auto", tr("option_sub_auto")),
             ("lang", tr("option_sub_lang")),
             ("forced", tr("option_sub_forced"))],
            self.settings.value("sub_mode", "auto"))
        slang_box = self._combo(
            LANGS, self.settings.value("sub_lang", ""))
        slang2_box = self._combo(
            LANGS, self.settings.value("sub_lang2", ""))
        aspect_box = self._combo(
            [("auto", tr("option_aspect_auto")), ("16:9", "16:9"),
             ("4:3", "4:3"), ("2.35:1", "2.35:1"),
             ("stretch", tr("option_aspect_stretch"))],
            self.settings.value("aspect_mode", "auto"))
        buf_box = self._combo(
            [("1", "1 s"), ("3", "3 s"), ("5", "5 s"),
             ("10", "10 s"), ("30", "30 s")],
            str(self.settings.value("cache_secs", "10")))

        def delay_row(key: str):
            try:
                total = int(self.settings.value(key, 0))
            except (TypeError, ValueError):
                total = 0
            sign_box = self._combo(
                [("+", "+ (later)"), ("-", "- (earlier)")],
                "-" if total < 0 else "+")
            hours, minutes = divmod(abs(total), 60)
            hours_box = QSpinBox()
            hours_box.setRange(0, 23)
            hours_box.setSuffix(" h")
            hours_box.setValue(hours)
            minutes_box = QSpinBox()
            minutes_box.setRange(0, 59)
            minutes_box.setSuffix(" m")
            minutes_box.setValue(minutes)
            row = QHBoxLayout()
            row.addWidget(sign_box)
            row.addWidget(hours_box)
            row.addWidget(minutes_box)
            row.addStretch(1)
            return row, sign_box, hours_box, minutes_box

        (replay_delay_row, replay_sign_box, replay_hours_box,
         replay_minutes_box) = delay_row("replay_delay_min")
        (epg_delay_row, epg_sign_box, epg_hours_box,
         epg_minutes_box) = delay_row("epg_delay_min")
        pf.addRow(tr("setting_playback_mode"), mode_box)
        pf.addRow(tr("setting_autoplay_preview"), autoplay_box)
        pf.addRow(tr("setting_stream_format"), fmt_box)
        pf.addRow(tr("setting_audio_lang"), alang_box)
        pf.addRow(tr("setting_subtitles"), sub_box)
        pf.addRow(tr("setting_sub_lang"), slang_box)
        pf.addRow(tr("setting_sub_lang_fallback"), slang2_box)
        pf.addRow(tr("setting_aspect_ratio"), aspect_box)
        pf.addRow(tr("setting_network_buffer"), buf_box)
        pf.addRow(tr("setting_replay_delay"), replay_delay_row)
        pf.addRow(tr("setting_epg_delay"), epg_delay_row)
        delay_hint = QLabel(
            "Replay delay shifts where catch-up/timeshift starts "
            "playback, for providers whose stream lags behind their "
            "listed schedule. EPG delay shifts all programme guide "
            "times shown in the app. Both default to no offset.")
        delay_hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        delay_hint.setWordWrap(True)
        pf.addRow(delay_hint)
        mode_hint = QLabel(
            "Embedded plays in the app. Reused mpv window keeps "
            "one external window you can zap in (Ctrl+←/→). "
            "External opens a fresh window each time.")
        mode_hint.setStyleSheet(
            f"color:{P['muted2']}; font-size:11px;")
        mode_hint.setWordWrap(True)
        pf.addRow(mode_hint)
        if not self.player:
            reason = embedded_playback_reason() or "unknown reason"
            hint = QLabel(
                f"Embedded playback unavailable: {reason}")
            hint.setStyleSheet(
                f"color:{P['muted2']}; font-size:11px;")
            hint.setWordWrap(True)
            pf.addRow(hint)
        tabs.addTab(play_tab, tr("tab_playback"))

        # Interface tab
        ui_tab = QWidget()
        uf = QFormLayout(ui_tab)
        uf.setSpacing(10)
        density_box = self._combo(
            [("compact", tr("option_compact")), ("medium", tr("option_medium")),
             ("large", tr("option_large")), ("xlarge", tr("option_xlarge"))],
            self.settings.value("view_density", "medium"))
        sort_box = self._combo(
            [("default", tr("option_sort_default")),
             ("alpha_asc", tr("option_sort_az")),
             ("alpha_desc", tr("option_sort_za")),
             ("recent", tr("option_sort_recent"))],
            self.settings.value("sort_order", "default"))
        theme_box = self._combo(
            [(key, tr(f"theme_{key}")) for key in THEMES],
            self.settings.value("theme", "graphite"))
        accent_box = self._combo(
            [(key, tr(f"accent_{key}")) for key in ACCENTS],
            self.settings.value("accent", "blue"))
        theme_box.currentIndexChanged.connect(
            lambda _i: self._set_theme(
                theme_box.currentData(), accent_box.currentData()))
        accent_box.currentIndexChanged.connect(
            lambda _i: self._set_theme(
                theme_box.currentData(), accent_box.currentData()))
        from .i18n import LANGUAGES, current_language
        lang_box = self._combo(
            [(code, name) for code, name in LANGUAGES.items()],
            current_language())
        lang_box.currentIndexChanged.connect(
            lambda _i: self._set_language(lang_box.currentData()))
        uf.addRow(tr("setting_language"), lang_box)
        uf.addRow(tr("setting_list_size"), density_box)
        uf.addRow(tr("setting_sort_by"), sort_box)
        uf.addRow(tr("setting_theme"), theme_box)
        uf.addRow(tr("setting_accent_color"), accent_box)
        theme_hint = QLabel(tr("misc_theme_applies_immediately"))
        theme_hint.setStyleSheet(
            f"color:{P['muted2']}; font-size:11px;")
        theme_hint.setWordWrap(True)
        uf.addRow(theme_hint)

        # Disk-cache controls: covers/logos accumulate under
        # QStandardPaths.CacheLocation and don't clean themselves.
        # The live cache is the shared "images" dir; the two legacy
        # dirs predate the loaders sharing one directory - keep them
        # in the sweep so files from old sessions still get counted
        # and cleared.
        cache_dirs = [default_image_cache_dir("images"),
                      default_image_cache_dir("logos"),
                      default_image_cache_dir("posters")]
        cache_lbl = QLabel()
        clear_cache_btn = QPushButton(tr("settings_image_cache_clear"))

        def _fmt_size(n: int) -> str:
            for unit in ("B", "KB", "MB", "GB"):
                if n < 1024 or unit == "GB":
                    return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
                n /= 1024
            return f"{n:.1f} GB"

        def refresh_cache_label() -> None:
            total = sum(dir_size_bytes(d) for d in cache_dirs)
            cache_lbl.setText(
                tr("settings_image_cache_label", size=_fmt_size(total)))
            clear_cache_btn.setEnabled(total > 0)

        def clear_cache() -> None:
            for d in cache_dirs:
                clear_directory(d)
            # Also drop the in-memory LRUs so a re-scroll doesn't just
            # rewrite the same pixmaps back to disk from RAM.
            self.logos.cache.clear()
            self.poster_art.cache.clear()
            refresh_cache_label()

        clear_cache_btn.clicked.connect(clear_cache)
        cache_row = QHBoxLayout()
        cache_row.addWidget(cache_lbl, 1)
        cache_row.addWidget(clear_cache_btn)
        uf.addRow(cache_row)
        cache_hint = QLabel(tr("settings_image_cache_hint"))
        cache_hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        cache_hint.setWordWrap(True)
        uf.addRow(cache_hint)
        refresh_cache_label()

        tabs.addTab(ui_tab, tr("tab_interface"))

        # Playlists tab
        pl_tab = QWidget()
        pv = QVBoxLayout(pl_tab)
        pv.setSpacing(10)
        pl_list = QListWidget()
        pv.addWidget(pl_list, 1)
        pl_btns = QHBoxLayout()
        add_btn = QPushButton(tr("btn_add"))
        edit_btn = QPushButton(tr("btn_edit"))
        remove_btn = QPushButton(tr("btn_remove"))
        refresh_pl_btn = QPushButton(tr("btn_refresh"))
        refresh_pl_btn.setToolTip(tr("tooltip_reload_channels_epg"))
        use_btn = QPushButton(tr("btn_use"), objectName="Primary")
        for b in (add_btn, edit_btn, remove_btn, refresh_pl_btn, use_btn):
            pl_btns.addWidget(b)
        pv.addLayout(pl_btns)
        io_btns = QHBoxLayout()
        export_btn = QPushButton(tr("btn_export"))
        export_btn.setToolTip(tr("settings_export_tip"))
        import_btn = QPushButton(tr("btn_import"))
        import_btn.setToolTip(tr("settings_import_tip"))
        io_btns.addWidget(export_btn)
        io_btns.addWidget(import_btn)
        io_btns.addStretch()
        pv.addLayout(io_btns)
        tabs.addTab(pl_tab, tr("tab_playlists"))

        # Parental tab
        par_tab = QWidget()
        parv = QVBoxLayout(par_tab)
        parv.setSpacing(10)
        pin_status = QLabel()
        parv.addWidget(pin_status)
        set_pin_btn = QPushButton(tr("pin_set_change"))
        remove_pin_btn = QPushButton(tr("pin_remove"))
        lock_now_btn = QPushButton(tr("pin_lock_now"))
        pin_btns = QHBoxLayout()
        pin_btns.setSpacing(8)
        pin_btns.addWidget(set_pin_btn)
        pin_btns.addWidget(remove_pin_btn)
        pin_btns.addWidget(lock_now_btn)
        pin_btns.addStretch(1)
        parv.addLayout(pin_btns)
        par_hint = QLabel(
            "Lock favorite groups (right-click a group under "
            "Favorites) or whole categories (right-click a "
            "category in TV/Movies/Series). Locked content is "
            "hidden - including from 'All' - until the PIN is "
            "entered.")
        par_hint.setStyleSheet(
            f"color:{P['muted2']}; font-size:11px;")
        par_hint.setWordWrap(True)
        parv.addWidget(par_hint)
        parv.addStretch()
        tabs.addTab(par_tab, tr("tab_parental"))

        # Recording tab
        rec_tab = QWidget()
        recv = QVBoxLayout(rec_tab)
        recv.setSpacing(10)
        rec_dir_lbl = QLabel(self.rec.directory())
        rec_dir_lbl.setWordWrap(True)
        recv.addWidget(QLabel(tr("rec_saved_in")))
        recv.addWidget(rec_dir_lbl)
        rec_dir_btn = QPushButton(tr("btn_choose_folder"))

        def pick_rec_dir():
            path = QFileDialog.getExistingDirectory(
                d, "Choose recordings folder", self.rec.directory(),
                QFileDialog.Option.DontUseNativeDialog
                | QFileDialog.Option.ShowDirsOnly)
            if path:
                self.rec.set_directory(path)
                rec_dir_lbl.setText(path)
                if self.mode == "rec":
                    self._load_categories()

        rec_dir_btn.clicked.connect(pick_rec_dir)
        rec_dir_row = QHBoxLayout()
        rec_dir_row.addWidget(rec_dir_btn)
        rec_dir_row.addStretch(1)
        recv.addLayout(rec_dir_row)
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel(
            "Stop a recording when the file reaches"))
        rec_max_edit = QLineEdit(
            str(self.settings.value("rec_max_value", "")))
        rec_max_edit.setPlaceholderText(tr("ph_no_limit"))
        rec_max_edit.setMaximumWidth(90)
        rec_max_unit = self._combo(
            [("MB", "MB"), ("GB", "GB"), ("TB", "TB")],
            self.settings.value("rec_max_unit", "GB"))
        size_row.addWidget(rec_max_edit)
        size_row.addWidget(rec_max_unit)
        size_row.addStretch()
        recv.addLayout(size_row)
        rk, rexe = self.rec.recorder()
        rec_hint = QLabel(
            f"Recorder: {rk} ({rexe})" if rexe else
            "No recorder found - install ffmpeg (recommended) "
            "or mpv.")
        rec_hint.setStyleSheet(
            f"color:{P['muted2']}; font-size:11px;")
        rec_hint.setWordWrap(True)
        recv.addWidget(rec_hint)
        rec_hint2 = QLabel(
            "Right-click a TV channel → Record to record "
            "immediately or on a start/stop timer. Scheduled "
            "recordings need the app to be running when they "
            "start. Manage files under Recordings in the sidebar.")
        rec_hint2.setStyleSheet(
            f"color:{P['muted2']}; font-size:11px;")
        rec_hint2.setWordWrap(True)
        recv.addWidget(rec_hint2)
        recv.addStretch()
        tabs.addTab(rec_tab, tr("tab_recording"))

        # Metadata tab (TMDB artwork)
        meta_tab = QWidget()
        mf = QFormLayout(meta_tab)
        mf.setSpacing(10)
        meta_source_box = self._combo(
            [("playlist", "Playlist (provider artwork)"),
             ("tmdb", "TMDB (fetch posters by title)")],
            self.settings.value("metadata_source", "playlist"))
        tmdb_key_row = QHBoxLayout()
        tmdb_key_edit = QLineEdit(self.settings.value("tmdb_api_key", ""))
        tmdb_key_edit.setPlaceholderText(tr("tmdb_key_placeholder"))
        tmdb_test_btn = QPushButton(tr("btn_test"))
        tmdb_key_row.addWidget(tmdb_key_edit, 1)
        tmdb_key_row.addWidget(tmdb_test_btn)
        mf.addRow(tr("setting_artwork_source"), meta_source_box)
        key_row_idx = mf.rowCount()
        mf.addRow(tr("setting_tmdb_key"), tmdb_key_row)
        tmdb_status = QLabel()
        tmdb_status.setWordWrap(True)
        status_row_idx = mf.rowCount()
        mf.addRow("", tmdb_status)
        meta_hint = QLabel(
            "Get a free key at themoviedb.org -> Settings -> API. "
            "Posters are matched by title and cached, so lookups "
            "only happen once per movie/series. Not used for live TV.")
        meta_hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        meta_hint.setWordWrap(True)
        mf.addRow(meta_hint)
        tabs.addTab(meta_tab, tr("tab_metadata"))

        def update_meta_visibility() -> None:
            show_tmdb = meta_source_box.currentData() == "tmdb"
            mf.setRowVisible(key_row_idx, show_tmdb)
            mf.setRowVisible(status_row_idx, show_tmdb)

        def test_tmdb_key() -> None:
            key = tmdb_key_edit.text().strip()
            if not key:
                tmdb_status.setText(tr("tmdb_enter_key"))
                tmdb_status.setStyleSheet(
                    f"color:{P['error']}; font-size:11px;")
                return
            tmdb_status.setText(tr("tmdb_checking"))
            tmdb_status.setStyleSheet(
                f"color:{P['muted2']}; font-size:11px;")

            def check():
                TmdbClient(key).poster_url("Inception", "vod")
                return True

            def ok(_r):
                tmdb_status.setText(tr("tmdb_key_works"))
                tmdb_status.setStyleSheet(
                    f"color:{P['accent']}; font-size:11px; font-weight:600;")

            def fail(msg):
                tmdb_status.setText(tr("tmdb_key_failed", msg=msg))
                tmdb_status.setStyleSheet(
                    f"color:{P['error']}; font-size:11px;")

            run_async(self.pool, check, ok, fail)

        meta_source_box.currentIndexChanged.connect(
            lambda _i: update_meta_visibility())
        tmdb_test_btn.clicked.connect(test_tmdb_key)
        update_meta_visibility()

        # Trakt tab
        trakt_tab = QWidget()
        tf = QVBoxLayout(trakt_tab)
        tf.setSpacing(10)
        trakt_setup_lbl = QLabel(
            "One-time setup (about 2 minutes): Trakt requires every app "
            "to have its own free API app for sign-in. Click below, "
            "create one (any name, redirect URI doesn't matter), then "
            "paste the Client ID and Secret it shows you. You only do "
            "this once - after that, Connect just shows you a short "
            "code to enter at trakt.tv, no password typing.")
        trakt_setup_lbl.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        trakt_setup_lbl.setWordWrap(True)
        tf.addWidget(trakt_setup_lbl)
        create_app_btn = QPushButton(tr("trakt_create_app"))
        create_app_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://trakt.tv/oauth/applications/new")))
        create_app_row = QHBoxLayout()
        create_app_row.addWidget(create_app_btn)
        create_app_row.addStretch(1)
        tf.addLayout(create_app_row)
        tform = QFormLayout()
        tform.setSpacing(10)
        trakt_id_edit = QLineEdit(self.trakt.client_id)
        trakt_id_edit.setPlaceholderText(tr("trakt_client_id_ph"))
        trakt_secret_edit = QLineEdit(self.trakt.client_secret)
        trakt_secret_edit.setPlaceholderText(tr("trakt_client_secret_ph"))
        trakt_secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        tform.addRow(tr("field_client_id"), trakt_id_edit)
        tform.addRow(tr("field_client_secret"), trakt_secret_edit)
        tf.addLayout(tform)
        trakt_status = QLabel()
        trakt_status.setWordWrap(True)
        tf.addWidget(trakt_status)
        trakt_btns = QHBoxLayout()
        trakt_connect_btn = QPushButton(tr("trakt_connect_btn"),
                                        objectName="Primary")
        trakt_disconnect_btn = QPushButton(tr("trakt_disconnect"))
        trakt_watchlist_btn = QPushButton(tr("trakt_watchlist_btn"))
        trakt_btns.addWidget(trakt_connect_btn)
        trakt_btns.addWidget(trakt_disconnect_btn)
        trakt_btns.addWidget(trakt_watchlist_btn)
        trakt_btns.addStretch()
        tf.addLayout(trakt_btns)
        trakt_hint = QLabel(
            "While connected, movies and episodes you play are "
            "scrobbled to Trakt. Live TV and recordings are not.")
        trakt_hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        trakt_hint.setWordWrap(True)
        tf.addWidget(trakt_hint)

        # Watched-history sync controls.
        sync_row = QHBoxLayout()
        sync_status = QLabel()
        sync_status.setWordWrap(True)
        sync_now_btn = QPushButton(tr("trakt_sync_now"))
        sync_row.addWidget(sync_status, 1)
        sync_row.addWidget(sync_now_btn)
        tf.addLayout(sync_row)
        sync_hint = QLabel(tr("trakt_sync_hint"))
        sync_hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        sync_hint.setWordWrap(True)
        tf.addWidget(sync_hint)

        tf.addStretch()

        def refresh_sync_status():
            n_m, n_e = (len(self.watched.movies),
                        sum(len(v) for v in self.watched.episodes.values()))
            if self.watched.last_sync_at:
                when = datetime.fromtimestamp(
                    self.watched.last_sync_at).strftime("%Y-%m-%d %H:%M")
                sync_status.setText(
                    tr("trakt_sync_status", when=when,
                       movies=n_m, episodes=n_e))
            else:
                sync_status.setText(tr("trakt_sync_never"))
            sync_now_btn.setEnabled(
                self.trakt.is_connected()
                and not self._watched_sync_running)

        def do_sync_now():
            sync_now_btn.setEnabled(False)
            sync_status.setText(tr("trakt_syncing"))
            # Kick a forced sync and re-poll status every second while it
            # runs so the label flips to "synced" without needing the
            # user to close and reopen the dialog.
            self._maybe_sync_watched(force=True)
            poll = QTimer(d)
            def tick():
                if not self._watched_sync_running:
                    poll.stop()
                    refresh_sync_status()
            poll.timeout.connect(tick)
            poll.start(400)

        sync_now_btn.clicked.connect(do_sync_now)

        def refresh_trakt_status():
            if self.trakt.is_connected():
                trakt_status.setText(tr("trakt_connected"))
            else:
                trakt_status.setText(tr("trakt_not_connected"))
            trakt_disconnect_btn.setEnabled(self.trakt.is_connected())
            refresh_sync_status()

        def do_trakt_connect():
            self.settings.setValue(
                "trakt_client_id", trakt_id_edit.text().strip())
            self.settings.setValue(
                "trakt_client_secret", trakt_secret_edit.text().strip())
            if not self.trakt.client_id or not self.trakt.client_secret:
                QMessageBox.warning(
                    d, "Trakt", tr("msg_trakt_enter_creds"))
                return
            self._trakt_device_auth_dialog(d)
            refresh_trakt_status()

        def do_trakt_disconnect():
            self.trakt.disconnect()
            refresh_trakt_status()

        trakt_connect_btn.clicked.connect(do_trakt_connect)
        trakt_disconnect_btn.clicked.connect(do_trakt_disconnect)
        trakt_watchlist_btn.clicked.connect(
            lambda: self._open_trakt_dialog(d))
        refresh_trakt_status()
        tabs.addTab(trakt_tab, tr("tab_trakt"))

        def refresh_pin_status():
            if self.parental.has_pin():
                state = ("unlocked for this session"
                         if self.parental.session_unlocked
                         else "locked")
                pin_status.setText(
                    f"PIN is set - currently {state}.")
            else:
                pin_status.setText(tr("pin_none_set"))
            remove_pin_btn.setEnabled(self.parental.has_pin())
            lock_now_btn.setEnabled(
                self.parental.has_pin()
                and self.parental.session_unlocked)

        def set_pin():
            if self.parental.has_pin() and not self._request_unlock():
                return
            pin, ok = QInputDialog.getText(
                d, "Parental control", "New PIN:",
                QLineEdit.EchoMode.Password)
            pin = (pin or "").strip()
            if ok and pin:
                self.parental.set_pin(pin)
            refresh_pin_status()

        def remove_pin():
            if not self._request_unlock():
                return
            self.parental.clear_pin()
            refresh_pin_status()
            self._load_categories()

        def lock_now():
            self.parental.lock_session()
            refresh_pin_status()
            self._load_categories()

        set_pin_btn.clicked.connect(set_pin)
        remove_pin_btn.clicked.connect(remove_pin)
        lock_now_btn.clicked.connect(lock_now)
        refresh_pin_status()

        store = self.playlist_store

        def reload_pl_list():
            pl_list.clear()
            if not store:
                pl_list.addItem(tr("pl_mgmt_unavailable"))
                return
            for p in store.playlists():
                suffix = ("   (active)"
                          if p["id"] == store.active_id else "")
                item = QListWidgetItem(
                    f"{p['name']}  -  {p['server']}{suffix}")
                item.setData(Qt.ItemDataRole.UserRole, p["id"])
                pl_list.addItem(item)

        def selected_pid():
            item = pl_list.currentItem()
            return (item.data(Qt.ItemDataRole.UserRole)
                    if item else None)

        def add_playlist():
            dlg = PlaylistDialog(d)
            if dlg.exec():
                store.add(dlg.values())
                reload_pl_list()

        def edit_playlist():
            pid = selected_pid()
            pl = store.get(pid) if (store and pid) else None
            if not pl:
                return
            dlg = PlaylistDialog(d, pl)
            if dlg.exec():
                store.update(pid, **dlg.values())
                reload_pl_list()
                if pid == store.active_id:
                    self.switch_playlist(pid)

        def remove_playlist():
            pid = selected_pid()
            if not (store and pid):
                return
            if QMessageBox.question(
                    d, tr("msg_remove_playlist_title"),
                    tr("msg_remove_playlist_body")) \
                    == QMessageBox.StandardButton.Yes:
                store.remove(pid)
                reload_pl_list()

        def use_playlist():
            pid = selected_pid()
            if store and pid and pid != store.active_id:
                self.switch_playlist(pid)
                reload_pl_list()

        def export_playlists():
            if not store or not store.playlists():
                QMessageBox.information(
                    d, "Export", "No playlists to export.")
                return
            path, _ = QFileDialog.getSaveFileName(
                d, "Export playlists", "playlists.json",
                "JSON files (*.json)",
                options=QFileDialog.Option.DontUseNativeDialog)
            if not path:
                return
            import json
            data = []
            for p in store.playlists():
                data.append({
                    "name": p.get("name", ""),
                    "server": p.get("server", ""),
                    "username": p.get("username", ""),
                    "password": p.get("password", ""),
                    "epg_url": p.get("epg_url", ""),
                    "refresh": p.get("refresh", "never"),
                })
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            QMessageBox.information(
                d, "Export",
                f"Exported {len(data)} playlist(s) to:\n{path}")

        def import_playlists():
            if not store:
                return
            path, _ = QFileDialog.getOpenFileName(
                d, "Import playlists", "",
                "JSON files (*.json);;All files (*)",
                options=QFileDialog.Option.DontUseNativeDialog)
            if not path:
                return
            import json
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as exc:
                QMessageBox.warning(
                    d, "Import", f"Could not read file:\n{exc}")
                return
            if not isinstance(data, list):
                QMessageBox.warning(
                    d, "Import", "Invalid format — expected a JSON list.")
                return
            added = 0
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                server = entry.get("server", "").strip()
                user = entry.get("username", "").strip()
                pw = entry.get("password", "").strip()
                if not (server and user and pw):
                    continue
                store.add({
                    "name": entry.get("name", "").strip()
                            or server.split("//")[-1].split("/")[0],
                    "server": server, "username": user,
                    "password": pw,
                    "epg_url": entry.get("epg_url", "").strip(),
                    "refresh": entry.get("refresh", "never"),
                })
                added += 1
            reload_pl_list()
            QMessageBox.information(
                d, "Import", f"Imported {added} playlist(s).")

        add_btn.clicked.connect(add_playlist)
        edit_btn.clicked.connect(edit_playlist)
        remove_btn.clicked.connect(remove_playlist)
        refresh_pl_btn.clicked.connect(self.refresh_playlist)
        use_btn.clicked.connect(use_playlist)
        export_btn.clicked.connect(export_playlists)
        import_btn.clicked.connect(import_playlists)
        if not store:
            for b in (add_btn, edit_btn, remove_btn,
                      refresh_pl_btn, use_btn, export_btn, import_btn):
                b.setEnabled(False)
        reload_pl_list()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Reset)
        for b in buttons.buttons():
            b.setIcon(QIcon())
        reset_btn = buttons.button(QDialogButtonBox.StandardButton.Reset)
        reset_btn.setText(tr("settings_reset_all"))
        reset_btn.clicked.connect(lambda: self._reset_all_settings(d))
        buttons.accepted.connect(d.accept)
        buttons.rejected.connect(d.reject)
        outer.addWidget(buttons)

        if d.exec():
            self.settings.setValue(
                "stream_format", fmt_box.currentData())
            self.settings.setValue(
                "autoplay_preview", autoplay_box.currentData())
            if mode_box.currentData():
                self.settings.setValue(
                    "playback_mode", mode_box.currentData())
            self.settings.setValue(
                "view_density", density_box.currentData())
            self.settings.setValue(
                "sort_order", sort_box.currentData())
            self.settings.setValue(
                "audio_lang", alang_box.currentData())
            self.settings.setValue(
                "sub_mode", sub_box.currentData())
            self.settings.setValue(
                "sub_lang", slang_box.currentData())
            self.settings.setValue(
                "sub_lang2", slang2_box.currentData())
            self.settings.setValue(
                "aspect_mode", aspect_box.currentData())
            self.settings.setValue(
                "cache_secs", buf_box.currentData())

            def delay_value(sign_box, hours_box, minutes_box) -> int:
                total = hours_box.value() * 60 + minutes_box.value()
                return -total if sign_box.currentData() == "-" else total

            self.settings.setValue(
                "replay_delay_min",
                delay_value(replay_sign_box, replay_hours_box,
                           replay_minutes_box))
            self.settings.setValue(
                "epg_delay_min",
                delay_value(epg_sign_box, epg_hours_box, epg_minutes_box))
            self.xmltv.delay_minutes = self._epg_delay_minutes()
            try:
                val = float(
                    rec_max_edit.text().replace(",", ".") or 0)
            except ValueError:
                val = 0
            self.settings.setValue(
                "rec_max_value", val if val > 0 else "")
            self.settings.setValue(
                "rec_max_unit", rec_max_unit.currentData())
            self.settings.setValue(
                "metadata_source", meta_source_box.currentData())
            self.settings.setValue(
                "tmdb_api_key", tmdb_key_edit.text().strip())
            self._init_metadata_provider()
            self.settings.setValue(
                "trakt_client_id", trakt_id_edit.text().strip())
            self.settings.setValue(
                "trakt_client_secret", trakt_secret_edit.text().strip())
            if self.player:
                self.player.apply_default_options()
            self._apply_view_settings()
            self.list_model.refresh_all()

    def show_about(self) -> None:
        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> {VERSION}<br><br>"
            "An elegant IPTV client for Xtream Codes with EPG,<br>"
            "embedded playback, favorites and history.<br><br>"
            "Playback via mpv (embedded/window) or VLC.")

    # -- EPG refresh with progress -------------------------------------------------

    def _on_epg_progress(self, value: int) -> None:
        self.loading_bar.show()
        if value >= 0:
            self._set_status(
                tr("status_loading_programme_guide_pct", pct=value))
        if value == 0:
            self._show_toast(tr("status_loading_programme_guide"))
        if value < 0:
            self.loading_bar.setRange(0, 0)
        else:
            self.loading_bar.setRange(0, 100)
            self.loading_bar.setValue(value)

    def _epg_progress_finished(self) -> None:
        self.loading_bar.setRange(0, 0)
        self.loading_bar.hide()

    def _error(self, msg: str) -> None:
        self.loading_bar.hide()
        self._set_status("Error: " + msg, error=True)

    # -- keyboard and close --------------------------------------------------------

    def keyPressEvent(self, event) -> None:
        if self._player_fs:
            if event.key() == Qt.Key.Key_Right:
                self._zap(1)
                return
            if event.key() == Qt.Key.Key_Left:
                self._zap(-1)
                return
        super().keyPressEvent(event)

    def _restore_splitter_state(self) -> None:
        """Restore the panel divider positions from last session. Runs after
        the window is shown at its restored size so the saved proportions land
        exactly instead of being rescaled from the default geometry."""
        from PyQt6.QtCore import QByteArray
        st = self.settings.value("splitter_state")
        if isinstance(st, QByteArray) and st.size() > 0:
            self._root.restoreState(st)

    def _schedule_save_layout(self, *_args) -> None:
        """Called on splitter drag / window move / window resize. Coalesces
        rapid updates into a single save 300 ms after the last event."""
        t = getattr(self, "_save_layout_timer", None)
        if t is None:
            t = QTimer(self)
            t.setSingleShot(True)
            t.setInterval(300)
            t.timeout.connect(self._save_layout)
            self._save_layout_timer = t
        t.start()

    def _save_layout(self) -> None:
        """Write the current window geometry + splitter state to disk. Skipped
        while in PiP or fullscreen (those sizes are transient)."""
        if not hasattr(self, "_root"):
            return
        if (self._pip_win is not None or self.isFullScreen()
                or self._player_fs):
            return
        self.settings.setValue("splitter_state", self._root.saveState())
        self.settings.setValue("window_geometry", self.saveGeometry())
        self.settings.sync()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._schedule_save_layout()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._schedule_save_layout()

    def closeEvent(self, event) -> None:
        # Close the non-modal cast panel first: as a separate top-level
        # window it would otherwise keep the app alive (quitOnLastWindowClosed
        # never fires) and leave the process hanging after the main window
        # closes.
        d = getattr(self, "_cast_dialog", None)
        if d is not None:
            d.close()
        # All persistence must land BEFORE we skip the interpreter
        # teardown below. Layout, resume position, TMDB cache flush,
        # recording state, mpv teardown, cast disconnect - each of
        # these is itself flush-and-return, no pending threads.
        self._save_layout()
        self._save_resume_position()
        self.wake.release()
        if self.tmdb:
            self.tmdb.flush()
        if self._trakt_active:
            active = self._trakt_active
            progress = self.player.progress_percent() if self.player else 0.0
            threading.Thread(
                target=lambda: self.trakt.scrobble_stop(
                    active["payload"], progress),
                daemon=True).start()
        self.rec.shutdown()
        if self.player:
            self.player.shutdown()
        if self.mpv_window:
            self.mpv_window.shutdown()
        self.mpv.stop()
        threading.Thread(target=self.cast.shutdown, daemon=True).start()
        # Cancel every queued background download. Wait a moderate
        # amount of time for in-flight workers to finish so libmpv,
        # Wayland handles and file descriptors have a chance to
        # release cleanly - a hard exit while any of those are mid-
        # teardown leaves stale state that can crash the NEXT startup.
        for pool in (self.pool, self._logo_pool, self._art_pool):
            try:
                pool.clear()
                pool.waitForDone(1500)
            except Exception:
                pass
        super().closeEvent(event)
        # Only fall back to os._exit if workers are still active - at
        # that point the interpreter would segfault emitting Qt
        # signals during teardown, so the abrupt exit is the lesser
        # evil. If workers drained normally, use a clean quit so
        # atexit / QApplication destructors run and every OS-level
        # resource (mpv sound device, Wayland surfaces) gets released
        # instead of being orphaned until the compositor times them
        # out.
        active_workers = any(
            p.activeThreadCount() > 0
            for p in (self.pool, self._logo_pool, self._art_pool))
        if active_workers:
            os._exit(0)
        QApplication.instance().quit()
