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
from .metadata import PosterResolver, TmdbClient, bundled_tmdb_key
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
from .widgets import _ClickableWidget, _SidebarLogo, _Toast
from .mw_settings import _SettingsMixin
from .mw_trakt import _TraktMixin
from .mw_recording import _RecordingMixin
from .workers import (
    LogoLoader, choose_cover_url, clear_directory, default_image_cache_dir,
    dir_size_bytes, run_async, tmdb_url_from_provider)


class MainWindow(_SettingsMixin, _TraktMixin, _RecordingMixin, QMainWindow):
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
                                cache_dir=shared_image_dir,
                                max_bytes=48 * 1024 * 1024)
        # A poster plus up to 8 cast photos is up to 9 concurrent
        # downloads per selection, hence the separate pool + higher-res
        # cache (reusing `logos` blurs on the big detail-panel sizes).
        self._art_pool = QThreadPool()
        self._art_pool.setMaxThreadCount(4)
        self.poster_art = LogoLoader(
            self._art_pool, max_size=320,
            cache_dir=shared_image_dir,
            max_bytes=96 * 1024 * 1024)
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
        # Flat, single-group favourites for movies and series - the
        # split Favorites column shows these under 'Movies' and 'Series'
        # alongside the grouped channel favourites.
        self.movie_favs = FavoriteStore(
            settings, f"movie_favorites_{pid}" if pid else "movie_favorites",
            id_key="stream_id")
        self.series_favs = FavoriteStore(
            settings, f"series_favorites_{pid}" if pid else "series_favorites",
            id_key="series_id")
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
        # Rebuilds the Watched -> Trakt list as tmdb-id lookups resolve.
        self._watched_subcat = None
        self._watched_refresh_timer = QTimer(self)
        self._watched_refresh_timer.setSingleShot(True)
        self._watched_refresh_timer.timeout.connect(self._reload_watched)
        # Favourites -> Trakt list: fetched ids + a debounce to rebuild
        # rows as their tmdb-id lookups resolve.
        self._fav_trakt_ids: tuple[list[int], list[int]] = ([], [])
        self._fav_refresh_timer = QTimer(self)
        self._fav_refresh_timer.setSingleShot(True)
        self._fav_refresh_timer.timeout.connect(self._rebuild_fav_trakt)
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
        # Which Favorites section is showing: "chan", "movie" or
        # "series". Drives the content kind (poster vs live, play as
        # movie vs drill into episodes) while self.mode stays "fav".
        self._fav_section: str = "chan"
        # True only while a right-click is moving the selection, so the
        # live-channel autoplay preview doesn't fire (right-click should
        # highlight a row, never start playing it).
        self._rmb_selecting: bool = False
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
                          ("watched", tr("nav_watched")),
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

        # Contextual "Sync now" - only shown in the Trakt-backed lists
        # (Watched, Watch Later, Favorites -> Trakt) so the user can pull
        # fresh data without digging into Settings.
        self._sync_now_btn = QPushButton(tr("btn_sync_now"))
        self._sync_now_btn.clicked.connect(self._sidebar_sync_now)
        # Deliberately loud (red, bold) so it's obvious when it appears -
        # it only shows in the Trakt-backed lists, so it shouldn't blend
        # in with the neutral sidebar buttons.
        self._sync_now_btn.setStyleSheet(
            "QPushButton{background:#e5354b; color:#ffffff; font-weight:700;"
            " border:none; border-radius:6px; padding:8px;}"
            "QPushButton:hover{background:#c8283b;}")
        self._sync_now_btn.hide()
        sl.addWidget(self._sync_now_btn)

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
            self.movie_favs = FavoriteStore(
                self.settings, f"movie_favorites_{pid}", id_key="stream_id")
            self.series_favs = FavoriteStore(
                self.settings, f"series_favorites_{pid}", id_key="series_id")
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
        self._update_sync_btn()
        # Opening Watched is a good moment to pull the latest Trakt
        # history (respects the 1 h TTL, so it's a no-op if just synced).
        if mode == "watched":
            self._maybe_sync_watched()

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
        if self.mode == "history":
            self.cat_list.blockSignals(True)
            all_item = QListWidgetItem(tr("cat_all"))
            all_item.setData(Qt.ItemDataRole.UserRole, None)
            self.cat_list.addItem(all_item)
            self.cat_list.blockSignals(False)
            self.cat_list.setCurrentRow(0)
            return
        if self.mode == "fav":
            # Split column: a Channels section (with its user-defined
            # groups + parental lock nested underneath), then flat
            # Movies and Series sections. The row data is a
            # (section, group) tuple - group is only meaningful for
            # channels.
            self.cat_list.blockSignals(True)
            chan = QListWidgetItem(tr("fav_channels"))
            chan.setData(Qt.ItemDataRole.UserRole, ("chan", None))
            self.cat_list.addItem(chan)
            for g in self.favs.group_names():
                locked = (self.favs.is_locked(g)
                          and not self.parental.session_unlocked)
                label = f"    {g}  [locked]" if locked else f"    {g}"
                it = QListWidgetItem(label)
                it.setData(Qt.ItemDataRole.UserRole, ("chan", g))
                self.cat_list.addItem(it)
            movies = QListWidgetItem(tr("fav_movies"))
            movies.setData(Qt.ItemDataRole.UserRole, ("movie", None))
            self.cat_list.addItem(movies)
            series = QListWidgetItem(tr("fav_series"))
            series.setData(Qt.ItemDataRole.UserRole, ("series", None))
            self.cat_list.addItem(series)
            if self.trakt.is_connected():
                trakt_row = QListWidgetItem(tr("watched_trakt"))
                trakt_row.setData(Qt.ItemDataRole.UserRole, ("trakt", None))
                self.cat_list.addItem(trakt_row)
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
        if self.mode == "watched":
            # Split into Local and Trakt. The Trakt row (and the
            # combined 'All') only appear when connected - a user with
            # no Trakt account just sees their local list.
            self.cat_list.blockSignals(True)
            connected = self.trakt.is_connected()
            rows = []
            if connected:
                rows.append((tr("cat_all"), None))
            rows.append((tr("watched_local"), "local"))
            if connected:
                rows.append((tr("watched_trakt"), "trakt"))
            for label, data in rows:
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
                # cat is a (section, group) tuple; only a named channel
                # group can be parental-locked.
                section, group = cat
                locked = (section == "chan" and group is not None
                          and self.favs.is_locked(group))
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
        self._update_sync_btn()
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
            # category_id is a (section, group) tuple (or None as a
            # fallback, meaning all channels).
            section, group = category_id if category_id else ("chan", None)
            self._fav_section = section
            if section == "movie":
                self.all_items = self.movie_favs.items()
            elif section == "series":
                self.all_items = self.series_favs.items()
            elif section == "trakt":
                # Favourites pulled from the Trakt 'dopeIPTV Favorites'
                # list - fetched over the network, so show an empty list
                # now and fill it in asynchronously.
                self.all_items = []
                self._apply_filter()
                self._load_trakt_favorites()
                return
            else:
                exclude = (() if self.parental.session_unlocked
                           else self.favs.locked_groups())
                self.all_items = self.favs.items(group, exclude_groups=exclude)
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
        if self.mode == "watched":
            self._watched_subcat = category_id
            local = self.watched.local_watched_items()
            if category_id == "local":
                self.all_items = local
            elif category_id == "trakt":
                self.all_items = self._trakt_watched_items()
            else:
                self.all_items = self._merge_watched(
                    local, self._trakt_watched_items())
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
        # A built-in key ships with release builds so TMDB works with no
        # setup. When one is present TMDB is the default artwork source;
        # otherwise we fall back to the provider's own images. A user who
        # explicitly picks a source in Settings overrides the default.
        bundled = bundled_tmdb_key()
        user_key = (self.settings.value("tmdb_api_key", "") or "").strip()
        # Three explicit sources:
        #   "tmdb"     - the built-in key (default when one ships)
        #   "custom"   - the user's own key
        #   "playlist" - the provider's own artwork
        source = self.settings.value("metadata_source", "") or ""
        if not source:
            source = "tmdb" if bundled else ("custom" if user_key
                                             else "playlist")
        self._prefer_tmdb_covers = source in ("tmdb", "custom")
        if source == "custom":
            key = user_key
        elif source == "tmdb":
            # Prefer the built-in key; fall back to a user key so an
            # older "tmdb" setting keeps working before this split.
            key = bundled or user_key
        else:  # provider artwork - still resolve ids for Trakt/badges
            key = user_key or bundled
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

    def _cover_kind(self, it, kind: str) -> str:
        """The kind to use for artwork lookup. Watch Later / Watched /
        Favourites / History rows are snapshots that carry the real
        content kind in "_kind"; map that to vod/series so their posters
        resolve from TMDB just like the Movies/Series lists, instead of
        being treated as a container kind that never gets a poster."""
        if kind in ("watchlist", "watched", "fav", "history"):
            hk = it.get("_kind")
            return {"movie": "vod", "vod": "vod", "series": "series",
                    "episode": "series"}.get(hk, kind)
        return kind

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
        eff = self._cover_kind(it, kind)
        # A Trakt-only row already carries a real TMDB poster URL as its
        # stream_icon, so skip the title search (it would be a wasted
        # round-trip that could even mismatch) and use that directly.
        title_tmdb = None if it.get("_trakt_only") else self.poster_for(it, eff)
        if not self._prefer_tmdb_covers:
            title_tmdb = None
        return choose_cover_url(
            title_tmdb, self._provider_cover(it),
            eff, self.logos.is_dead)

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
        return self.tmdb_resolved(it, self._cover_kind(it, kind))

    # -- trakt scrobbling -------------------------------------------------------------


    # -- list and filtering --------------------------------------------------------

    LABELS = {
        "live": "channels", "vod": "movies", "series": "series",
        "episode": "episodes", "fav": "favorites",
        "history": "history items", "rec": "recordings",
        "watchlist": "on your list", "watched": "watched",
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

    def _content_kind(self) -> str:
        """The kind of content the middle list is currently showing.
        Same as self.mode except inside the Favorites view, where the
        selected section decides whether the rows are channels ('fav',
        painted like live), movies ('vod') or series ('series')."""
        if self.series_ctx:
            return "episode"
        if self.mode == "fav":
            return {"movie": "vod", "series": "series"}.get(
                self._fav_section, "fav")
        return self.mode

    def _apply_filter(self) -> None:
        text = self.search.text().lower().strip()
        kind = self._content_kind()
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
        if self.mode == "fav" and not self.series_ctx and not self.all_items:
            where = {"movie": "a movie in Movies",
                     "series": "a series in Series"}.get(
                         self._fav_section, "a channel in TV")
            self._set_status(
                f"No favorites yet - right-click {where} to add one.")
        elif kind == "history" and not self.all_items:
            self._set_status("No watch history yet.")

    # -- item identity -------------------------------------------------------------

    @staticmethod
    def _as_int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

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
            self._content_kind(), "other")

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
                     if (self.mode in ("history", "watchlist", "watched")
                         or (self.mode == "fav" and it.get("_kind")))
                     else None)
        snap_kind = {"movie": "vod", "episode": "series"}.get(
            snap_kind, snap_kind)
        # In the Favorites view the content kind follows the selected
        # section, so a favourite movie/series gets the poster + info
        # panel just like it would in the Movies / Series views.
        ckind = self._content_kind()
        media_kind = (
            "series" if self.series_ctx
            else "vod" if ckind == "vod"
            else "series" if ckind == "series"
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
        elif ckind in ("live", "fav"):
            if it.get("stream_id") is not None:
                self._request_epg()
                if (self.player and self._autoplay_preview()
                        and self.playback_mode() == "embedded"
                        and not self._rmb_selecting):
                    self._preview_timer.start(350)
        # In History / Watch Later, defer to the snapshot's _kind so
        # a movie row fetches movie info and a series row fetches
        # series info - same as the main Movies / Series views.
        # History rows carry the provider id under "_key" (not
        # stream_id/series_id), so fall back to that - this is what
        # makes the plot/genre/cast panel fill in for history items
        # even with no TMDB account.
        elif (ckind == "vod"
              or (self.mode in ("watchlist", "history", "watched")
                  and snap_kind == "vod")):
            sid = it.get("stream_id")
            if sid is None and self.mode == "history":
                sid = it.get("_key")
            if sid is not None:
                self._request_media_info("vod", sid, self._current_key)
        elif (ckind == "series"
              or (self.mode in ("watchlist", "history", "watched")
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
        if (not it or self._content_kind() not in ("live", "fav")
                or self.series_ctx):
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
        # Favorite movies/series route by the selected section, not by
        # 'fav' meaning live - only the Channels section plays live.
        if self.mode == "live" or (
                self.mode == "fav" and self._fav_section == "chan"):
            fmt = self.settings.value("stream_format", "ts")
            return self.client.live_url(it.get("stream_id"), fmt), title
        # Watch Later snapshots carry `_kind` set to "vod" or "series"
        # so the same movie playback code path works from that view
        # even though self.mode is 'watchlist'.
        eff_mode = self.mode
        if self.mode in ("watchlist", "watched"):
            eff_mode = it.get("_kind") or "vod"
        elif self.mode == "fav":
            eff_mode = it.get("_kind") or (
                "vod" if self._fav_section == "movie" else "series")
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
        # Series row from Watch Later or the Favorites 'Series' section:
        # same 'drill in' behaviour as from the Series view - open the
        # episode list instead of trying to play a series URL directly.
        # A Trakt-only watched row (seen on another device, not in this
        # provider) has no stream to play or drill into - it's a record,
        # not content. Check this before the series-drill below.
        if (it.get("_trakt_only") and not it.get("stream_id")
                and not it.get("series_id")):
            self._error(tr("watched_trakt_only_note"))
            return
        if (self.mode in ("watchlist", "watched")
                and it.get("_kind") == "series"):
            self._enter_series(it)
            return
        if (self.mode == "fav" and self._fav_section == "series"
                and not self.series_ctx):
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
            if (self._content_kind() in ("live", "fav")
                    and it.get("stream_id") is not None):
                url = self.client.live_url(it["stream_id"], "m3u8")
        if not url:
            return
        CastDialog(self, url, title).exec()

    def _autoplay_preview(self) -> bool:
        return self.settings.value("autoplay_preview", "true") == "true"

    def _play_preview(self) -> None:
        it = self.list_model.item_at(self.listw.currentIndex().row())
        if (not it or self._content_kind() not in ("live", "fav")
                or self.series_ctx or not self.player
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
        # Select (highlight) the right-clicked row so it's clear which
        # item the menu acts on. The _rmb_selecting guard stops the
        # live-channel autoplay preview from firing, so right-clicking a
        # channel highlights it without starting playback or interrupting
        # whatever is already playing.
        self._rmb_selecting = True
        self.listw.setCurrentIndex(idx)
        self._rmb_selecting = False
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
        content_kind = self._content_kind()
        if (content_kind in ("live", "fav")
                and it.get("stream_id") is not None):
            if self._timeshift_days(it):
                m.addSeparator()
                self._build_timeshift_menu(
                    m.addMenu(tr("tooltip_timeshift")), it)
            m.addSeparator()
            self._build_record_menu(m.addMenu(tr("rec_record")), it)
        if (content_kind in ("live", "fav")
                and it.get("stream_id") is not None):
            # Channel favourites keep the full user-defined group +
            # parental-lock system.
            m.addSeparator()
            fav_menu = m.addMenu(tr("ctx_add_to_favorites"))
            for g in self.favs.group_names():
                fav_menu.addAction(
                    g, lambda g=g: self._add_fav(g, it))
            if self.favs.group_names():
                fav_menu.addSeparator()
            fav_menu.addAction(tr("ctx_new_group"),
                               lambda: self._add_fav(None, it))
            if (content_kind == "fav"
                    or self.favs.is_favorite(it.get("stream_id"))):
                m.addAction(tr("ctx_remove_from_favorites"),
                            lambda: self._remove_fav(it))
        elif content_kind == "vod" and it.get("stream_id") is not None:
            # Movie favourites are a flat list - a single toggle.
            m.addSeparator()
            if self.movie_favs.is_favorite(it.get("stream_id")):
                m.addAction(tr("ctx_remove_from_favorites"),
                            lambda: self._toggle_media_fav(it, "movie", False))
            else:
                m.addAction(tr("ctx_add_to_favorites"),
                            lambda: self._toggle_media_fav(it, "movie", True))
        elif content_kind == "series" and it.get("series_id") is not None:
            # Series favourites are a flat list - a single toggle.
            m.addSeparator()
            if self.series_favs.is_favorite(it.get("series_id")):
                m.addAction(tr("ctx_remove_from_favorites"),
                            lambda: self._toggle_media_fav(it, "series", False))
            else:
                m.addAction(tr("ctx_add_to_favorites"),
                            lambda: self._toggle_media_fav(it, "series", True))
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
        if self.mode in ("watchlist", "watched"):
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
                    if trakt_ok:
                        m.addAction(tr("ctx_unmark_watched_trakt"),
                                    lambda it=it: unmark(it, True))
                else:
                    m.addAction(tr("ctx_mark_watched"),
                                lambda it=it: mark(it, False))
                    if trakt_ok:
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
            data = cur.data(Qt.ItemDataRole.UserRole) if cur else None
            # fav category data is a (section, group) tuple; only the
            # channel section carries a group to scope the removal to.
            group = data[1] if isinstance(data, tuple) else None
            self.favs.remove(item.get("stream_id"), group)
            self._load_categories()
        else:
            self.favs.remove(item.get("stream_id"))
            self.list_model.refresh_all()

    def _toggle_media_fav(self, item, section: str, add: bool) -> None:
        """Flat add/remove for movie ('movie') and series ('series')
        favourites - no groups, no lock. Refreshes the list, and when
        we're inside the Favorites view of that section, drops/reloads
        the affected row."""
        store = self.movie_favs if section == "movie" else self.series_favs
        ident = item.get(store.id_key)
        kind = "vod" if section == "movie" else "series"
        tid = self._tmdb_id_for_item(item, kind)
        if add:
            # Stamp the resolved tmdb id onto the stored snapshot so a
            # later Trakt push/remove doesn't need to re-resolve it.
            snap = dict(item)
            if isinstance(tid, int):
                snap["_tmdb_id"] = tid
            store.add("all", snap)
        else:
            store.remove(ident)
        # Mirror to the Trakt 'dopeIPTV Favorites' list when connected.
        if self.trakt.is_connected():
            if isinstance(tid, int):
                self._trakt_push(
                    (lambda: self.trakt.add_favorite(tid, kind)) if add
                    else (lambda: self.trakt.remove_favorite(tid, kind)))
            elif add and self.tmdb is not None:
                self._resolve_tmdb_id_async(
                    item, kind,
                    lambda rid, k=kind: self._trakt_push(
                        lambda: self.trakt.add_favorite(rid, k))
                    if isinstance(rid, int) else None)
        if (self.mode == "fav"
                and self._fav_section == section
                and not add):
            cur = self.cat_list.currentItem()
            self._load_items(cur.data(Qt.ItemDataRole.UserRole) if cur
                             else (section, None))
        else:
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
