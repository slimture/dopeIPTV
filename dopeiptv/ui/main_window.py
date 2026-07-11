"""Main application window: sidebar, channel list, detail panel, playback."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime

from PyQt6.QtCore import (
    QEvent, QRect, QSettings, QSize, Qt, QThreadPool,
    QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QAction, QColor, QKeySequence, QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QSplitter, QToolButton, QVBoxLayout, QWidget,
)

from .. import APP_NAME
from ..i18n import tr
from .channel_list import (
    CategoryColorDelegate, ChannelDelegate, ChannelListModel, ChannelListView,
)
from ..providers.chromecast import CastDialog, ChromecastManager
from ..providers.client import (
    DemoClient, OfflineClient, XtreamClient, make_client,
)
from ..media.embedded import EmbeddedPlayer
from ..providers.epg import XmltvGuide, epg_cache_path
from ..services.coverart import CoverArtService
from ..services.resume import ResumeStore
from ..media.players import (
    MpvIpcPlayer, MpvWindowPlayer, _libmpv, embedded_playback_supported, launch_player,
)
from ..core.recording import RecordingManager
from ..core.stores import (
    CategoryOverrides, ChannelOverrides, FavoriteStore, HistoryStore,
    ParentalControl, PlaylistStore, WatchedStore, WatchlistStore,
)
from .theme import P
from ..providers.trakt import TraktClient
from ..core.wakelock import WakeLock
from .widgets import _SidebarLogo, _Toast
from .welcome import WelcomeOverlay
from .mw_settings import _SettingsMixin
from .mw_trakt import _TraktMixin
from .mw_recording import _RecordingMixin
from .mw_context import _ContextMenuMixin
from .mw_detail import _DetailMixin
from ..core.workers import (
    LogoLoader, default_image_cache_dir, run_async)


# Sentinel for "no pending category to reselect" - distinct from None, which
# is a real category id (the "All" row).
_UNSET = object()


class MainWindow(_SettingsMixin, _TraktMixin, _RecordingMixin,
                 _ContextMenuMixin, _DetailMixin, QMainWindow):
    """Primary application window with sidebar, channel list, and detail panel."""

    epg_progress = pyqtSignal(int)

    def __init__(self, client: XtreamClient, settings: QSettings,
                 playlists: PlaylistStore | None = None) -> None:
        super().__init__()
        self._welcome = None  # first-run onboarding overlay; created on demand
        self._add_provider_btn = None  # "+ Add provider" hint when offline
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
        # The detail panel shows one title's poster + cast at a time, so a
        # huge RAM budget here just holds artwork nobody's looking at; 64 MB
        # is still hundreds of posters and evicted ones reload from disk in a
        # few ms.
        self.poster_art = LogoLoader(
            self._art_pool, max_size=320,
            cache_dir=shared_image_dir,
            max_bytes=64 * 1024 * 1024)
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
        self.resume = ResumeStore(settings, pid)
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
        self.cover = CoverArtService(
            settings, self.logos,
            lambda: self._poster_refresh_timer.start(150))
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
        self._focus_mode = False
        self._fav_view_tint = ("", "")
        self._pending_cat_select = _UNSET
        self._pending_jump_key = None
        self._stream_retries = 0
        self._last_stream_error_ts = 0.0
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
        else:
            self._size_to_screen()   # first run: fit the actual display
        QTimer.singleShot(0, self._restore_splitter_state)
        self._show_busy()
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
        self._sidebar_logo.setMinimumWidth(0)
        self._sidebar_logo.setSizePolicy(QSizePolicy.Policy.Ignored,
                                         QSizePolicy.Policy.Fixed)
        self._sidebar_logo.setToolTip(tr("tooltip_jump_playing"))
        self._sidebar_logo.clicked.connect(self._jump_to_now_playing)
        sl.addWidget(self._sidebar_logo)
        sl.addSpacing(6)

        # Glyphs used when the sidebar is collapsed to an icon rail.
        self._rail_glyphs = {
            "live": "📺", "vod": "🎬", "series": "🎞", "fav": "★",
            "watchlist": "🕒", "watched": "✓", "rec": "⏺", "history": "🕘",
        }
        self._nav_texts: dict[str, str] = {}
        self.nav_btns: dict[str, QPushButton] = {}
        for key, text in (("live", tr("nav_tv")), ("vod", tr("nav_movies")),
                          ("series", tr("nav_series")), ("fav", tr("nav_favorites")),
                          ("watchlist", tr("nav_watchlist")),
                          ("watched", tr("nav_watched")),
                          ("rec", tr("nav_recordings")), ("history", tr("nav_history"))):
            b = QPushButton(text, objectName="NavBtn")
            b.setCheckable(True)
            b.setFlat(True)
            b.setToolTip(text)
            # "Ignored" horizontal policy: the button fills the width when
            # there's room but imposes no text-based minimum, so the sidebar
            # can be dragged narrow enough to cross the auto-collapse threshold
            # (a plain minimumWidth(0) doesn't lower the minimumSizeHint the
            # splitter actually honours).
            b.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda _, k=key: self.switch_mode(k))
            # Right-click to give this entry a custom colour.
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.customContextMenuRequested.connect(
                lambda pos, k=key, bt=b: self._nav_color_menu(
                    k, bt.mapToGlobal(pos)))
            sl.addWidget(b)
            self.nav_btns[key] = b
            self._nav_texts[key] = text
            self._apply_nav_color(key)
        self.nav_btns["live"].setChecked(True)

        # "Categories" header with a small "solo" toggle on the right that
        # collapses the list to just the active category. Kept in a zero-margin
        # row so it takes exactly the label's height - the nav-button spacing
        # above stays untouched.
        cat_hdr = QHBoxLayout()
        cat_hdr.setContentsMargins(0, 0, 0, 0)
        cat_hdr.setSpacing(4)
        self._cat_section_label = QLabel(
            tr("sidebar_categories"), objectName="SectionLabel")
        self._cat_section_label.setMinimumWidth(0)
        cat_hdr.addWidget(self._cat_section_label)
        cat_hdr.addStretch()
        # A disclosure-style toggle: a Qt-drawn arrow (not a font glyph, so it
        # can't render as a box/ring) - down = list expanded, right = collapsed
        # to just the active category. Reads like a collapsible section header.
        self.cat_solo_btn = QToolButton(objectName="SectionToggle")
        self.cat_solo_btn.setCheckable(True)
        self.cat_solo_btn.setArrowType(Qt.ArrowType.DownArrow)
        self.cat_solo_btn.setAutoRaise(True)
        self.cat_solo_btn.setFixedSize(22, 18)
        self.cat_solo_btn.setToolTip(tr("tooltip_solo_category"))
        self.cat_solo_btn.toggled.connect(self._on_cat_solo_toggle)
        cat_hdr.addWidget(self.cat_solo_btn)
        sl.addLayout(cat_hdr)
        self.cat_list = QListWidget()
        self.cat_list.setItemDelegate(CategoryColorDelegate(self.cat_list))
        self.cat_list.setMinimumWidth(0)
        self.cat_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cat_list.currentItemChanged.connect(self._category_changed)
        self.cat_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.cat_list.customContextMenuRequested.connect(self._cat_menu)
        sl.addWidget(self.cat_list, 1)

        self._guide_btn = guide_btn = QPushButton(
            tr("btn_epg_guide"), objectName="SideAction")
        guide_btn.setToolTip(tr("btn_epg_guide"))
        guide_btn.setSizePolicy(QSizePolicy.Policy.Ignored,
                                QSizePolicy.Policy.Fixed)
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

        # (Reload lives in the menu bar's "Refresh playlist" and the
        # per-playlist auto-refresh setting; a sidebar button here was just
        # an easy mis-click.)
        self._settings_btn = settings_btn = QPushButton(
            tr("btn_settings"), objectName="SideAction")
        settings_btn.setToolTip(tr("btn_settings"))
        settings_btn.setSizePolicy(QSizePolicy.Policy.Ignored,
                                   QSizePolicy.Policy.Fixed)
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
        self._hide_busy()
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
            [("global", tr("sort_global")), ("default", tr("label_default")),
             ("alpha_asc", "A→Z"), ("alpha_desc", "Z→A"),
             ("recent", tr("label_recent"))],
            "global")
        self.sort_box.setObjectName("InlineCombo")
        self.sort_box.setToolTip(tr("sort_scope_hint"))
        self.sort_box.currentIndexChanged.connect(self._inline_view_changed)
        self.grid_btn = QPushButton(tr("btn_grid"), objectName="InlineToggle")
        self.grid_btn.setCheckable(True)
        self.grid_btn.setChecked(
            self.settings.value("view_grid", "false") == "true")
        self.grid_btn.toggled.connect(self._inline_view_changed)
        self._size_label = QLabel(tr("label_size"))
        self._sort_label = QLabel(tr("label_sort"))
        # Toggle for the left category column - handy for clean screenshots
        # (hide category names) or just more room for the list.
        self.side_btn = QPushButton("☰", objectName="InlineToggle")
        self.side_btn.setCheckable(True)
        self.side_btn.setChecked(True)
        self.side_btn.setToolTip(tr("tooltip_toggle_sidebar"))
        self.side_btn.setFixedWidth(34)
        self.side_btn.toggled.connect(self._on_side_toggle)
        # Focus mode: hide this whole list column to give the player the room,
        # reopened via the arrow strip on the detail pane's edge.
        self.focus_btn = QPushButton("⤢", objectName="InlineToggle")
        self.focus_btn.setToolTip(tr("tooltip_hide_list"))
        self.focus_btn.setFixedWidth(34)
        self.focus_btn.clicked.connect(lambda: self._set_focus_mode(True))
        ctl.addWidget(self.side_btn)
        ctl.addWidget(self.focus_btn)
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

        # "Focus mode" reopen strip: a slim full-height arrow pinned to the
        # detail pane's left edge, shown only when the list is hidden, so
        # there's always an obvious way to bring the middle column back.
        # It floats over the pane (not in the layout) at x=0, so it tracks the
        # pane's left edge as the splitter moves; only its height needs a
        # refresh on window resize (see _position_reopen).
        self._reopen_btn = QToolButton(det, objectName="ReopenStrip")
        self._reopen_btn.setArrowType(Qt.ArrowType.RightArrow)
        self._reopen_btn.setToolTip(tr("tooltip_show_list"))
        self._reopen_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reopen_btn.clicked.connect(lambda: self._set_focus_mode(False))
        self._reopen_btn.hide()

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
            self.player.stalled.connect(self._on_player_stalled)
            self.player.finished.connect(self._on_player_finished)
            self.player.next_episode.connect(self._play_next_episode)
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
        root.setCollapsible(1, False)
        root.setCollapsible(2, False)
        root.setStretchFactor(0, 0)
        root.setStretchFactor(1, 1)
        root.setStretchFactor(2, 0)
        # Save the panel layout every time the user drags a divider, so it
        # persists even if closeEvent doesn't run (Ctrl+C, force quit, sudden
        # kill). The window geometry is saved via moveEvent/resizeEvent below.
        root.splitterMoved.connect(self._schedule_save_layout)
        # Dragging the side divider inward past a threshold collapses the
        # sidebar to the icon rail. Pinning the rail's max width (in
        # _apply_sidebar_collapsed) is what makes this stick mid-drag - a hard
        # width constraint the splitter honours, unlike a setSizes() call.
        root.splitterMoved.connect(self._maybe_collapse_on_drag)
        # When collapsed the rail's width is pinned (so it can't stretch), which
        # freezes its divider handle - so watch the handle for a rightward drag
        # to re-expand it without reaching for the ☰ button.
        self._side_handle = root.handle(1)
        if self._side_handle is not None:
            self._side_handle.installEventFilter(self)
        det.setMinimumWidth(280)
        # Keep the content list from being squeezed away: dragging the sidebar
        # divider far right used to swallow the whole middle column (leaving
        # sidebar + player and no list, which just looks broken). A floor plus
        # non-collapsible keeps it always present.
        mid.setMinimumWidth(240)
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
        # High-water playback progress (percent) of the current title. Kept
        # up to date by the resume timer because mpv stops reporting a
        # position once the file has ended - without this, a title watched
        # to the very end would read as 0% at auto-mark time.
        self._playback_max_pct = 0.0
        self._current_epg = None
        self._player_fs = False

        QShortcut(QKeySequence("Ctrl+Right"), self,
                  activated=lambda: self._zap(1))
        QShortcut(QKeySequence("Ctrl+Left"), self,
                  activated=lambda: self._zap(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self,
                  activated=self._on_escape)
        QShortcut(QKeySequence(Qt.Key.Key_F), self,
                  activated=self._toggle_fullscreen_shortcut)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self,
                  activated=self._delete_pressed)
        QShortcut(QKeySequence(Qt.Key.Key_Space), self,
                  activated=self._toggle_pause_shortcut)
        QShortcut(QKeySequence("Ctrl+B"), self,
                  activated=lambda: self.side_btn.toggle())
        QShortcut(QKeySequence("Ctrl+Shift+M"), self,
                  activated=lambda: self._set_focus_mode(not self._focus_mode))
        # Player controls (single letters, mpv-style). Guarded so they never
        # fire while typing in a text field, and only act with the player up.
        QShortcut(QKeySequence(Qt.Key.Key_M), self,
                  activated=self._shortcut_mute)
        QShortcut(QKeySequence(Qt.Key.Key_P), self,
                  activated=self._shortcut_pip)
        QShortcut(QKeySequence(Qt.Key.Key_R), self,
                  activated=self._shortcut_record)
        QShortcut(QKeySequence(Qt.Key.Key_I), self,
                  activated=self._shortcut_stats)
        QShortcut(QKeySequence("Ctrl+G"), self,
                  activated=self._shortcut_epg_guide)

        self._apply_view_settings()

    def _on_side_toggle(self, checked: bool) -> None:
        """Collapse the left column to a narrow icon rail (or expand it back).
        The rail keeps the TV/Movies/Series nav as icons, so you reclaim the
        width without losing navigation. Remembered across fullscreen."""
        self._sidebar_collapsed = not checked
        if hasattr(self, "_side") and not self._player_fs:
            self._apply_sidebar_collapsed()

    RAIL_W = 60

    def _apply_sidebar_chrome(self, collapsed: bool) -> None:
        """Visual-only rail state: nav glyphs vs labels, and which of the
        expanded-only bits (logo, category area) show. Safe to call mid-drag -
        it touches no width constraints and never calls setSizes()."""
        if not hasattr(self, "nav_btns"):
            return
        for key, b in self.nav_btns.items():
            b.setText(self._rail_glyphs.get(key, "•") if collapsed
                      else self._nav_texts[key])
            self._set_rail(b, collapsed)
        # Everything that only makes sense expanded: logo, the whole
        # category area, and full-text buttons become short labels or hide.
        self._sidebar_logo.setVisible(not collapsed)
        self._cat_section_label.setVisible(not collapsed)
        self.cat_solo_btn.setVisible(not collapsed)
        self.cat_list.setVisible(not collapsed)
        # Short letter labels in the rail so it's obvious what each is
        # (an emoji alone read as an anonymous box for some users).
        self._guide_btn.setText("EPG" if collapsed else tr("btn_epg_guide"))
        self._settings_btn.setText("⚙" if collapsed else tr("btn_settings"))
        self._set_rail(self._guide_btn, collapsed)
        self._set_rail(self._settings_btn, collapsed)

    def _apply_sidebar_collapsed(self) -> None:
        collapsed = getattr(self, "_sidebar_collapsed", False)
        if not hasattr(self, "nav_btns"):
            return
        # Remember the expanded width so we can hand it back on expand.
        if not collapsed and self._side.width() > self.RAIL_W:
            self._sidebar_expanded_w = max(self._side.width(), 180)
        elif collapsed and self._side.maximumWidth() > self.RAIL_W:
            self._sidebar_expanded_w = max(self._side.width(), 180)
        self._apply_sidebar_chrome(collapsed)
        # While the user is actively dragging the divider (see eventFilter) we
        # leave the pane UNPINNED and skip all geometry, so a single continuous
        # drag can collapse, re-expand and collapse again without a pinned width
        # freezing it. The final width is pinned on mouse release.
        if getattr(self, "_side_dragging", False):
            return
        # Pin the pane and reflow the sizes so the panel snaps to its final
        # width immediately. Crucially this runs on the drag-release too: after
        # a collapse the rail must snap to RAIL_W right away, otherwise the pane
        # keeps its wider footprint and leaves an empty gap beside the icons
        # until the next click. When expanding, the target is the current
        # (dragged) width, so setSizes is a no-op reflow that keeps it put.
        if collapsed:
            # Pin to exactly the rail width: a hard constraint the splitter
            # honours immediately (so a drag-in snaps cleanly) and which keeps
            # the rail from stretching when the middle column is hidden in
            # focus mode.
            self._side.setMinimumWidth(self.RAIL_W)
            self._side.setMaximumWidth(self.RAIL_W)
            target = self.RAIL_W
        else:
            self._side.setMinimumWidth(0)
            self._side.setMaximumWidth(16777215)
            target = getattr(self, "_sidebar_expanded_w", 220)
        sizes = self._root.sizes()
        if len(sizes) >= 2:
            sizes[1] = max(240, sizes[1] + (sizes[0] - target))
            sizes[0] = target
            self._root.setSizes(sizes)

    @staticmethod
    def _set_rail(btn, on: bool) -> None:
        btn.setProperty("rail", on)
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def _set_sidebar_collapsed(self, collapsed: bool) -> None:
        """Flip collapsed state and keep the ☰ button in sync, without letting
        its toggle re-run the geometry (we drive that from here / on release)."""
        if getattr(self, "_sidebar_collapsed", False) == collapsed:
            return
        self._sidebar_collapsed = collapsed
        self.side_btn.blockSignals(True)
        self.side_btn.setChecked(not collapsed)   # checked == expanded
        self.side_btn.blockSignals(False)
        self._apply_sidebar_collapsed()

    def _maybe_collapse_on_drag(self, *_a) -> None:
        """Dragging the side divider flips the sidebar between full and the icon
        rail. While the handle is held (see eventFilter) the pane is left
        unpinned, so one continuous drag can collapse, re-expand and collapse
        again - the final width is pinned on release. Two thresholds give a
        hysteresis band so it doesn't flicker right at the boundary."""
        if not hasattr(self, "_side") or not hasattr(self, "side_btn"):
            return
        if getattr(self, "_player_fs", False):
            return
        w = self._side.width()
        if not getattr(self, "_sidebar_collapsed", False):
            # Collapse once pulled to (near) the sidebar's own minimum - keyed
            # off the real minimumSizeHint so it works at any font/DPI.
            floor = self._side.minimumSizeHint().width()
            if w < 150 or w <= floor + 12:
                self._collapse_w = w
                self._set_sidebar_collapsed(True)
        else:
            # Re-expand by dragging back out. Only while the handle is held (a
            # rail pinned at rest can't move) and past a hysteresis gap above
            # where it collapsed, so it won't flip-flop at the threshold.
            if (getattr(self, "_side_dragging", False)
                    and w >= getattr(self, "_collapse_w", 150) + 40):
                self._set_sidebar_collapsed(False)

    def eventFilter(self, obj, event):
        # Track the whole drag gesture on the side divider. On press we free the
        # pane (unpin min/max) so it can move both ways for as long as the button
        # is held; on release we commit the final pinned width. This lets a
        # single continuous drag collapse and re-expand the rail repeatedly
        # without ever letting go.
        if obj is getattr(self, "_side_handle", None):
            t = event.type()
            if t == QEvent.Type.MouseButtonPress:
                self._side_dragging = True
                self._side.setMinimumWidth(0)
                self._side.setMaximumWidth(16777215)
            elif (t == QEvent.Type.MouseButtonRelease
                    and getattr(self, "_side_dragging", False)):
                self._side_dragging = False
                # Commit geometry for whatever state we ended in. On a collapse
                # this snaps the rail to RAIL_W right away (no leftover gap); on
                # an expand the target is the current width, so it stays put.
                self._apply_sidebar_collapsed()
        return super().eventFilter(obj, event)

    def _set_focus_mode(self, on: bool) -> None:
        """Focus mode hides the whole content list so the player pane gets the
        room. The reopen arrow strip on the detail pane's left edge is the way
        back, so the list can never be lost."""
        self._focus_mode = on
        self._mid.setVisible(not on)
        self._reopen_btn.setVisible(on and not self._player_fs)
        if on:
            self._position_reopen()
            self._reopen_btn.raise_()

    def _position_reopen(self) -> None:
        if not hasattr(self, "_reopen_btn"):
            return
        self._reopen_btn.setGeometry(0, 0, 20, self._det.height())

    # -- custom nav colours --------------------------------------------------

    def _apply_nav_color(self, key: str) -> None:
        """Tint one nav entry with the user's chosen text and/or background
        colours. Empty settings -> back to the theme default."""
        b = self.nav_btns.get(key)
        if b is None:
            return
        fg = self.settings.value(f"nav_color_{key}", "") or ""
        bg = self.settings.value(f"nav_bg_{key}", "") or ""
        if not fg and not bg:
            b.setStyleSheet("")
            return
        idle = []
        if fg:
            idle.append(f"color:{fg};")
        if bg:
            idle.append(f"background:{bg};")
        # When selected, a custom background wins as the fill; otherwise keep
        # the accent selection with the custom text colour.
        checked = (f"background:{bg};color:{fg or '#ffffff'};" if bg
                   else f"color:{fg};")
        b.setStyleSheet(
            "QPushButton#NavBtn{%s}QPushButton#NavBtn:checked{%s}"
            % ("".join(idle), checked))

    def _nav_color_menu(self, key: str, global_pos) -> None:
        m = QMenu(self)
        # Provider-backed lists can be reloaded straight from their entry.
        if key in ("live", "vod", "series"):
            m.addAction(tr("menu_refresh_playlist"), self.refresh_playlist)
            m.addSeparator()
        m.addAction(tr("nav_set_text_color"),
                    lambda: self._pick_nav_color(key, "text"))
        m.addAction(tr("nav_set_bg_color"),
                    lambda: self._pick_nav_color(key, "bg"))
        if (self.settings.value(f"nav_color_{key}", "")
                or self.settings.value(f"nav_bg_{key}", "")):
            m.addAction(tr("nav_reset_color"),
                        lambda: self._reset_nav_color(key))
        m.exec(global_pos)

    def _pick_nav_color(self, key: str, which: str) -> None:
        from PyQt6.QtWidgets import QColorDialog
        skey = f"nav_bg_{key}" if which == "bg" else f"nav_color_{key}"
        cur = self.settings.value(skey, "") or ""
        # Force Qt's own dialog: the native colour picker can open behind the
        # window or hang on some Linux setups (same as the file dialog).
        col = QColorDialog.getColor(
            QColor(cur) if cur else QColor("#3b5ba5"), self,
            tr("nav_set_bg_color") if which == "bg"
            else tr("nav_set_text_color"),
            QColorDialog.ColorDialogOption.DontUseNativeDialog)
        if col.isValid():
            self.settings.setValue(skey, col.name())
            self._apply_nav_color(key)

    def _reset_nav_color(self, key: str) -> None:
        self.settings.remove(f"nav_color_{key}")
        self.settings.remove(f"nav_bg_{key}")
        self._apply_nav_color(key)

    def _on_cat_solo_toggle(self, checked: bool) -> None:
        """Collapse the category list to just the active category (hide all
        the other rows), so the provider's category names can be tucked away
        for clean screenshots while the list still shows where you are."""
        self._cat_solo = checked
        self.cat_solo_btn.setArrowType(
            Qt.ArrowType.RightArrow if checked else Qt.ArrowType.DownArrow)
        self._apply_cat_solo()

    def _apply_cat_solo(self) -> None:
        if not hasattr(self, "cat_list"):
            return
        solo = getattr(self, "_cat_solo", False)
        current = self.cat_list.currentRow()
        for i in range(self.cat_list.count()):
            it = self.cat_list.item(i)
            if it is not None:
                it.setHidden(solo and i != current)

    def _toggle_pause_shortcut(self) -> None:
        if self.player and self.player.isVisible():
            self.player.toggle_pause()

    def _typing(self) -> bool:
        """True when a text field has focus, so single-letter player shortcuts
        don't steal the keystroke from the search box etc."""
        return isinstance(self.focusWidget(), QLineEdit)

    def _player_up(self) -> bool:
        return bool(self.player and self.player.isVisible())

    def _shortcut_mute(self) -> None:
        if not self._typing() and self._player_up():
            self.player.toggle_mute()

    def _shortcut_pip(self) -> None:
        if not self._typing() and self._player_up():
            self._toggle_pip()

    def _shortcut_record(self) -> None:
        if not self._typing() and self._player_up():
            self.player.record_menu.emit(self.player.rec_btn)

    def _shortcut_stats(self) -> None:
        if not self._typing() and self._player_up():
            self.player._show_stats()

    def _shortcut_epg_guide(self) -> None:
        if not self._typing():
            self._open_epg_guide()

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
        # Remember the panel widths: hiding the side/middle panes lets the
        # detail pane take the whole window, and without this the splitter
        # wouldn't get its proportions back on the way out of fullscreen.
        self._fs_splitter_sizes = self._root.sizes()
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
        self._update_provider_hint()   # tuck the '+ Add provider' hint away
        self._was_fullscreen = self.isFullScreen()
        self.showFullScreen()

    def _on_escape(self) -> None:
        """Single Escape handler so the key is never ambiguous: dismiss the
        onboarding wizard if it's up, otherwise leave fullscreen."""
        if self._welcome is not None and self._welcome.isVisible():
            self._welcome.dismiss()
            return
        self._exit_player_fullscreen()

    def _exit_player_fullscreen(self) -> None:
        if not self._player_fs:
            if self.isFullScreen():
                self.showNormal()
            return
        self._player_fs = False
        self._side.show()
        self._apply_sidebar_collapsed()   # keep the rail/expanded choice
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
        # Put the panel widths back (deferred so it lands after the window has
        # returned to its normal geometry, otherwise the still-fullscreen-sized
        # window bakes in the wrong proportions).
        saved = getattr(self, "_fs_splitter_sizes", None)
        if saved:
            QTimer.singleShot(0, lambda s=saved: self._root.setSizes(s))
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
        self._update_provider_hint()   # bring the hint back if in explore mode

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
        self._pip_splitter_sizes = self._root.sizes()

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
        # Wayland (GNOME/Mutter) ignores WindowStaysOnTopHint - a client can't
        # pin its own stacking there. So on Wayland we KEEP the title bar, and
        # the user right-clicks it for the compositor's own "Always on Top"
        # (exactly how Firefox PiP does it). On X11 the hint works, so we use
        # the clean frameless floating window.
        flags = Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
        if "wayland" not in QApplication.platformName().lower():
            flags |= Qt.WindowType.FramelessWindowHint
        self.setWindowFlags(flags)
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
        # Put the panel widths back (deferred, after the window geometry has
        # been restored) so the detail pane doesn't keep its PiP-wide size.
        saved = getattr(self, "_pip_splitter_sizes", None)
        if saved:
            QTimer.singleShot(0, lambda s=saved: self._root.setSizes(s))

    def _pip_context_menu(self, global_pos) -> None:
        if self._pip_win is None:
            return
        m = QMenu(self)
        # Right-click "Always on top" only where the client can actually set
        # its stacking (X11/XWayland/Windows/macOS). On native Wayland the
        # compositor ignores it, so there the title-bar menu is the only route
        # and adding a dead toggle here would just mislead.
        if "wayland" not in QApplication.platformName().lower():
            act = m.addAction(tr("pip_always_on_top"))
            act.setCheckable(True)
            act.setChecked(bool(self.windowFlags()
                                & Qt.WindowType.WindowStaysOnTopHint))
            act.toggled.connect(self._set_pip_on_top)
        else:
            # Native Wayland can't pin from the client - point the user at the
            # compositor's own title-bar menu (a disabled, informational row).
            hint = m.addAction(tr("pip_wayland_hint"))
            hint.setEnabled(False)
        m.addSeparator()
        m.addAction(tr("tooltip_exit_pip"), self._exit_pip)
        m.exec(global_pos)

    def _set_pip_on_top(self, on: bool) -> None:
        if self._pip_win is None:
            return
        flags = self.windowFlags()
        if on:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        geo = self.geometry()
        self.setWindowFlags(flags)
        self.setGeometry(geo)      # setWindowFlags can drop the geometry
        self.show()
        self.raise_()

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

    def start_demo(self) -> None:
        """Switch to the built-in demo provider (a few free public test
        streams) so the app can be tried without any credentials. Reuses the
        normal live path - the demo client just answers with a fixed channel
        list."""
        from ..providers.client import DemoClient
        self.client = DemoClient()
        self._base_title = tr("demo_title")
        self.setWindowTitle(self._base_title)
        # Rebuild the (empty) guide against the demo client, then load the
        # Live channels through the usual mode switch.
        self.xmltv = XmltvGuide(self.client, None, cache_path=None,
                                progress_cb=self.epg_progress.emit)
        self.switch_mode("live")
        self._update_provider_hint()   # hides the '+ Add provider' hint
        self._show_toast(tr("demo_notice"), 8000)

    def switch_playlist(self, pid: str) -> None:
        pl = self.playlist_store.get(pid) if self.playlist_store else None
        if not pl:
            return
        self._show_busy()
        self._set_status(tr("status_connecting", name=pl['name']))
        self._show_toast(tr("status_connecting", name=pl['name']))
        candidate = make_client(pl)

        def done(_auth):
            self._hide_busy()
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
            self._update_provider_hint()
            self.refresh_playlist()

        def fail(msg):
            self._hide_busy()
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
            for label, data in [(tr("cat_all"), None),
                                (tr("fav_channels"), "live"),
                                (tr("nav_movies"), "movie"),
                                (tr("nav_series"), "series")]:
                it = QListWidgetItem(label)
                it.setData(Qt.ItemDataRole.UserRole, data)
                self.cat_list.addItem(it)
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
            all_row = QListWidgetItem(tr("cat_all"))
            all_row.setData(Qt.ItemDataRole.UserRole, ("all", None))
            self.cat_list.addItem(all_row)

            def add_fav_section(section: str, label: str, store) -> None:
                # A section header row (shows everything in the section) with
                # the user's folders nested underneath. Locks are a
                # channels-only feature; folders in movies/series just carry a
                # name.
                head = QListWidgetItem(label)
                head.setData(Qt.ItemDataRole.UserRole, (section, None))
                self.cat_list.addItem(head)
                for g in store.custom_groups():
                    locked = (section == "chan" and store.is_locked(g)
                              and not self.parental.session_unlocked)
                    text = f"    {g}  [locked]" if locked else f"    {g}"
                    it = QListWidgetItem(text)
                    it.setData(Qt.ItemDataRole.UserRole, (section, g))
                    self.cat_list.addItem(it)

            add_fav_section("chan", tr("fav_channels"), self.favs)
            add_fav_section("movie", tr("fav_movies"), self.movie_favs)
            add_fav_section("series", tr("fav_series"), self.series_favs)
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
        self._show_busy()
        self._set_status(tr("status_loading_categories"))
        fn = {"live": self.client.live_categories,
              "vod": self.client.vod_categories,
              "series": self.client.series_categories}[self.mode]
        request_mode = self.mode

        def done(cats):
            if gen != self._load_gen or self.mode != request_mode:
                return
            self._hide_busy()
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
                bgcolor = ovr.get("bgcolor", "")
                if bgcolor:
                    it.setBackground(QColor(bgcolor))
                self.cat_list.addItem(it)
            self.cat_list.blockSignals(False)
            # Normally land on the first real category, but if a reload was
            # asked to keep the current one (e.g. after Manage categories),
            # reselect that category so the list doesn't jump to the top.
            keep = self._pending_cat_select
            self._pending_cat_select = _UNSET
            row = 1 if self.cat_list.count() > 1 else 0
            # A pending "jump to now playing" wants every item visible, so land
            # on the "All" row (0) rather than the first category.
            if getattr(self, "_pending_jump_key", None) is not None:
                row = 0
            if keep is not _UNSET:
                for i in range(self.cat_list.count()):
                    if self.cat_list.item(i).data(
                            Qt.ItemDataRole.UserRole) == keep:
                        row = i
                        break
            self.cat_list.setCurrentRow(row)

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
        # In "solo" mode keep only the now-active category visible in the list.
        self._apply_cat_solo()

    def _is_combined_view(self, category_id) -> bool:
        """The combined views that stack several kinds together: grouped under
        headers in list mode, or a flat poster wall in grid mode. They must be
        rebuilt (not just re-filtered) when the grid setting changes."""
        if self.mode == "fav":
            return category_id in (("all", None), ("trakt", None))
        if self.mode == "watchlist":
            return category_id is None
        if self.mode == "watched":
            return True
        if self.mode == "history":
            return category_id is None
        if self.mode == "rec":
            return category_id not in ("__jobs__", "__upcoming__")
        return False

    def _load_items(self, category_id) -> None:
        # Apply the right layout up front and deterministically: grouped
        # overviews are always a headed list, every other view honours the
        # user's grid/list choice. Doing it here (not via a sticky flag) fixes
        # the grid setting being "forgotten" when hopping between categories.
        self._current_cat = category_id
        self._sync_sort_box()            # show THIS category's sort order
        self._apply_list_layout(False)   # honour the user's grid/list choice
        if self.mode == "rec":
            if category_id == "__jobs__":
                self.all_items = [self._job_item(j)
                                  for j in reversed(self.rec.jobs)]
                self._apply_filter()
                return
            if category_id == "__upcoming__":
                self.all_items = [
                    self._job_item(j) for j in reversed(self.rec.jobs)
                    if j["status"] == "scheduled"]
                self._apply_filter()
                return
            self._load_recordings_grouped(self.rec.files(category_id))
            return
        if self.mode == "fav":
            # category_id is a (section, group) tuple (or None as a
            # fallback, meaning all channels).
            section, group = category_id if category_id else ("chan", None)
            self._fav_section = section
            # A folder's colour cascades to all its favourites: resolve it once
            # here (all items in a selected folder share it) for item_tint.
            fstore = {"chan": self.favs, "movie": self.movie_favs,
                      "series": self.series_favs}.get(section)
            gc = fstore.group_color(group) if (fstore and group) else {}
            self._fav_view_tint = (gc.get("color", "") or "",
                                   gc.get("bgcolor", "") or "")
            if section == "all":
                self._load_favorites_all()
                return
            if section == "movie":
                self.all_items = self.movie_favs.items(group)
            elif section == "series":
                self.all_items = self.series_favs.items(group)
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
            self._history_subcat = category_id  # None / live / movie / series
            items = self.history.items()
            if category_id is not None:
                kinds = self._HISTORY_KINDS.get(category_id, set())
                self.all_items = [it for it in items
                                  if it.get("_kind") in kinds]
                self._apply_filter()
                return
            # 'All' - grouped by kind. Keep each row's original _kind for
            # playback/scrobble; only tag _ekind so the delegate paints the
            # right artwork (logo vs poster) per row.
            def sect(cat):
                ks = self._HISTORY_KINDS[cat]
                return self._sorted(self._search_filter(
                    [it for it in items if it.get("_kind") in ks]))
            grouped: list[dict] = []
            for hk, ek, rows in (
                    ("fav_channels", "fav", sect("live")),
                    ("fav_movies", "vod", sect("movie")),
                    ("fav_series", "series", sect("series"))):
                if rows:
                    grouped.append({"_header": tr(hk)})
                    grouped += [{**r, "_ekind": ek} for r in rows]
            self._render_rows(grouped, "history")
            return
        if self.mode == "watchlist":
            self._watchlist_subcat = category_id
            movies = [{**m, "_kind": "vod"} for m in self.watchlist.movies]
            shows = [{**s, "_kind": "series"} for s in self.watchlist.shows]
            if category_id == "movies":
                self.all_items = movies
                self._apply_filter()
                return
            if category_id == "series":
                self.all_items = shows
                self._apply_filter()
                return
            # 'All' - Movies and Series stacked under headers.
            self._show_grouped(
                [("fav_movies", "vod", "vod", self._search_filter(movies)),
                 ("fav_series", "series", "series", self._search_filter(shows))],
                "watchlist")
            return
        if self.mode == "watched":
            self._watched_subcat = category_id
            local = self.watched.local_watched_items()
            if category_id == "local":
                items = local
            elif category_id == "trakt":
                items = self._trakt_watched_items()
            else:
                items = self._merge_watched(
                    local, self._trakt_watched_items())
            movies = self._search_filter(
                [it for it in items
                 if it.get("_kind") not in ("series", "episode")])
            series = self._search_filter(
                [it for it in items
                 if it.get("_kind") in ("series", "episode")])
            self._show_grouped(
                [("fav_movies", "vod", "vod", movies),
                 ("fav_series", "series", "series", series)],
                "watched")
            return
        self._show_busy()
        self._set_status(tr("status_loading_content"))
        fn = {"live": self.client.live_streams,
              "vod": self.client.vod_streams,
              "series": self.client.series_list}[self.mode]
        mode = self.mode
        gen = self._load_gen

        def done(items):
            if gen != self._load_gen or self.mode != mode:
                return
            self._hide_busy()
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

    # -- grouped, headed "All / combined" views ------------------------------

    def _search_filter(self, items: list) -> list:
        """Filter items by the current search text on their display name."""
        text = self.search.text().lower().strip()
        if not text:
            return items
        return [it for it in items
                if text in self.channel_display_name(it).lower()]

    def _grouped(self, sections: list) -> list:
        """Build a headed list from ordered (header_key, ekind, kind, items)
        sections, tagging each row with _ekind (delegate art/badges) and _kind
        (playback routing). Empty sections are skipped. Shared by the combined
        Favorites / Watch Later / Watched / History views."""
        out: list[dict] = []
        for header_key, ekind, kind, items in sections:
            if items:
                out.append({"_header": tr(header_key)})
                out += [{**it, "_ekind": ekind, "_kind": kind}
                        for it in self._sorted(items)]
        return out

    def _grid_on(self) -> bool:
        return self.settings.value("view_grid", "false") == "true"

    def _render_rows(self, rows: list, model_kind: str,
                     empty_msg: str | None = None) -> None:
        """Populate the model from a headed row list.

        In list mode the section headers are kept as full-width rows. In grid
        mode they are dropped: Qt's icon grid gives every cell a uniform size,
        so a header can't cleanly span a row without either wrecking the poster
        column alignment or leaving a tall, ragged gap. Grid mode is therefore
        a clean, uniform poster wall (sections stay grouped in order, just
        without the labels); the section labels live in list mode."""
        n = sum(1 for r in rows if not r.get("_header"))
        if self._grid_on():
            rows = [r for r in rows if not r.get("_header")]
        self.all_items = rows
        self.list_model.set_items(rows, model_kind)
        if self._loading_hint.isVisible():
            self._loading_hint.hide()
        label = self.LABELS.get(model_kind, "")
        self._set_status(f"{n} {label}".strip() if n
                         else (empty_msg or f"0 {label}".strip()))

    def _show_grouped(self, sections: list, model_kind: str,
                      empty_msg: str | None = None) -> None:
        """Render a combined view from ordered sections - grouped under
        headers in list mode, a flat poster wall in grid mode."""
        self._render_rows(self._grouped(sections), model_kind, empty_msg)

    def _load_recordings_grouped(self, files: list) -> None:
        """Recordings grouped by when they were made: Today / Yesterday /
        This week / Earlier, newest first. Rows keep their recording nature
        (no _ekind/_kind retag) so they still play via the rec path."""
        import datetime
        files = sorted(self._search_filter(files),
                       key=lambda f: int(f.get("added") or 0), reverse=True)
        today = datetime.date.today()
        buckets = {"today": [], "yesterday": [], "week": [], "earlier": []}
        for f in files:
            try:
                ts = int(f.get("added") or 0)
            except (TypeError, ValueError):
                ts = 0
            d = datetime.date.fromtimestamp(ts) if ts else today
            delta = (today - d).days
            if delta <= 0:
                buckets["today"].append(f)
            elif delta == 1:
                buckets["yesterday"].append(f)
            elif delta < 7:
                buckets["week"].append(f)
            else:
                buckets["earlier"].append(f)
        grouped: list[dict] = []
        for key, hk in (("today", "rec_today"), ("yesterday", "rec_yesterday"),
                        ("week", "rec_this_week"), ("earlier", "rec_earlier")):
            if buckets[key]:
                grouped.append({"_header": tr(hk)})
                grouped += buckets[key]
        self._render_rows(grouped, "rec")

    def _load_favorites_all(self) -> None:
        """Show every favorite at once - channels, movies and series - grouped
        under section headers, so opening Favorites shows all three kinds
        immediately instead of just Channels."""
        exclude = (() if self.parental.session_unlocked
                   else self.favs.locked_groups())
        chans = self._search_filter(
            self.favs.items(None, exclude_groups=exclude))
        movies = self._search_filter(self.movie_favs.items())
        series = self._search_filter(self.series_favs.items())
        self._show_grouped(
            [("fav_channels", "fav", "live", chans),
             ("fav_movies", "vod", "vod", movies),
             ("fav_series", "series", "series", series)],
            "fav", tr("fav_empty_all"))

    def _apply_list_layout(self, _force_list: bool = False) -> None:
        """Set the middle pane layout from the user's grid/list choice: a plain
        top-to-bottom list, or a uniform, justified poster grid. Combined views
        (favorites, watched, history, ...) use the very same grid - they just
        drop their section headers there (see _render_rows)."""
        from PyQt6.QtWidgets import QListView
        grid = self._grid_on()
        self.delegate.set_grid(grid)
        if not grid:
            self.listw.setViewMode(QListView.ViewMode.ListMode)
            self.listw.setFlow(QListView.Flow.TopToBottom)
            self.listw.setWrapping(False)
            self.listw.set_grid_cell(None)
            self.listw.setGridSize(QSize())
        else:
            self.listw.setViewMode(QListView.ViewMode.IconMode)
            self.listw.setFlow(QListView.Flow.LeftToRight)
            self.listw.setWrapping(True)
            self.listw.setResizeMode(QListView.ResizeMode.Adjust)
            self.listw.set_grid_cell(self.delegate.grid_size())

    def _ensure_xmltv_loaded(self) -> None:
        if self.xmltv._loaded or self.xmltv._failed:
            return
        run_async(self.pool, self.xmltv.ensure_loaded,
                  lambda ok: self.list_model.refresh_all() if ok else None)

    # -- metadata (TMDB artwork) -----------------------------------------------------

    @property
    def tmdb(self):
        """The active TMDB poster resolver (or None). Owned by the
        CoverArtService; exposed here because the detail panel, context
        menu and Trakt sync all reach the TMDB client through it."""
        return self.cover.resolver

    def _flush_poster_refresh(self) -> None:
        self.list_model.refresh_all()

    # -- trakt scrobbling -------------------------------------------------------------


    # -- list and filtering --------------------------------------------------------

    LABELS = {
        "live": "channels", "vod": "movies", "series": "series",
        "episode": "episodes", "fav": "favorites",
        "history": "history items", "rec": "recordings",
        "watchlist": "on your list", "watched": "watched",
    }

    # History left-category -> the stored _kind values it covers, for the
    # grouped view and the per-category delete.
    _HISTORY_KINDS = {
        "live": {"live"},
        "movie": {"movie", "vod"},
        "series": {"series", "episode"},
    }

    @staticmethod
    def _sort_key_name(it):
        return (it.get("name") or it.get("title") or "").lower()

    def _sort_setting_key(self) -> str:
        """Per-category sort key, so each category can keep its own order."""
        return f"sort::{self.mode}::{getattr(self, '_current_cat', None)!r}"

    def _current_sort_raw(self) -> str:
        """The current category's own choice: 'global' (follow the app-wide
        default) unless it has been overridden."""
        return self.settings.value(self._sort_setting_key(), "global")

    def _sync_sort_box(self) -> None:
        """Point the toolbar sort dropdown at the current category's order, so
        it never carries a stale value into _inline_view_changed (which would
        write that order onto whatever category is showing now)."""
        if not hasattr(self, "sort_box"):
            return
        self.sort_box.blockSignals(True)
        i = self.sort_box.findData(self._current_sort_raw())
        if i >= 0:
            self.sort_box.setCurrentIndex(i)
        self.sort_box.blockSignals(False)

    def _current_sort_order(self) -> str:
        """The effective sort order for the current category - its own
        override, or the global default when it is set to follow global."""
        raw = self._current_sort_raw()
        if raw == "global":
            return self.settings.value("sort_order", "default")
        return raw

    @staticmethod
    def _recency_key(it) -> int:
        # Newest-first key that works across views: provider "added", else the
        # history "_watched_at" ISO timestamp.
        a = it.get("added")
        if a:
            try:
                return int(a)
            except (TypeError, ValueError):
                pass
        wa = it.get("_watched_at")
        if wa:
            try:
                return int(datetime.fromisoformat(wa).timestamp())
            except (TypeError, ValueError):
                pass
        return 0

    def _sorted(self, items: list) -> list:
        order = self._current_sort_order()
        if order == "alpha_asc":
            return sorted(items, key=MainWindow._sort_key_name)
        if order == "alpha_desc":
            return sorted(items, key=MainWindow._sort_key_name, reverse=True)
        if order == "recent":
            return sorted(items, key=MainWindow._recency_key, reverse=True)
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

    def item_tint(self, it, kind: str):
        """(text, background) hex colours for a list item. A colour set on a
        category is NOT inherited by its items - that only tints the category
        row itself. Items are tinted only by their own per-item colour, and in
        a favourites folder by the folder's colour."""
        if not it:
            return "", ""
        if self.mode == "fav":
            return getattr(self, "_fav_view_tint", ("", ""))
        mode = {"live": "live", "vod": "vod", "series": "series"}.get(kind)
        if mode is None:
            return "", ""
        key = self._item_key(it)
        if key is not None:
            iov = self.channel_ov.get(mode, key)
            return iov.get("color", "") or "", iov.get("bgcolor", "") or ""
        return "", ""

    def _try_select_playing(self) -> bool:
        """Select + scroll to the playing item if it's in the current list."""
        key = self._playing_key
        if key is None:
            return False
        for row in range(self.list_model.rowCount()):
            item = self.list_model.item_at(row)
            if (item and not item.get("_header")
                    and self._item_key(item) == key):
                idx = self.list_model.index(row)
                self.listw.setCurrentIndex(idx)
                self.listw.scrollTo(
                    idx, QAbstractItemView.ScrollHint.PositionAtCenter)
                return True
        return False

    def _jump_to_now_playing(self) -> None:
        """Clicking the sidebar logo jumps the middle column to whatever's
        playing: select and scroll to its row, switching to its section and
        opening the 'All' category first so the row is actually in the list."""
        if (self._playing_key is None
                or getattr(self, "_playing_item", None) is None):
            self._show_toast(tr("toast_nothing_playing"))
            return
        if self._try_select_playing():
            return
        # Not in the current (possibly category-filtered) list: remember the
        # target and navigate to a view that contains it, then select once it
        # has loaded (see _load_categories / _apply_filter).
        self._pending_jump_key = self._playing_key
        QTimer.singleShot(2500, self._clear_pending_jump)   # safety net
        target = {"live": "live", "vod": "vod",
                  "episode": "series"}.get(self._playing_group, self.mode)
        if self.mode != target:
            self.switch_mode(target)     # done() picks "All" while a jump pends
        elif self.cat_list.count():
            self.cat_list.setCurrentRow(0)   # "All" -> shows every item

    def _clear_pending_jump(self) -> None:
        self._pending_jump_key = None

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
        if getattr(self, "_pending_jump_key", None) is not None:
            if self._try_select_playing():
                self._pending_jump_key = None
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


    # -- series -> episodes --------------------------------------------------------

    def _enter_series(self, series) -> None:
        sid = series.get("series_id")
        if sid is None:
            return
        self._show_busy()
        self._set_status(tr("status_loading_episodes"))

        def done(info):
            self._hide_busy()
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
        # 'fav' meaning live - only the Channels section plays live. In the
        # grouped "All favorites" view each row carries its own _kind, so a
        # channel row (_kind == "live") plays live regardless of section.
        if (self.mode == "live" or it.get("_kind") == "live" or (
                self.mode == "fav" and self._fav_section == "chan")):
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
        if not it or it.get("_header"):
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
        if (self.mode == "fav" and not self.series_ctx
                and (self._fav_section == "series"
                     or it.get("_kind") == "series")):
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
            if not self._confirm_external_while_playing():
                return
            launch_player(player or "mpv", url, title, self)
            if self.mode != "history":
                self.history.add(url, title, icon, key, kind)
            return

        self._start_playback(url, title, icon, key, kind,
                             record=self.mode != "history", item=it)

    def _confirm_external_while_playing(self) -> bool:
        """Opening an external player pulls a SECOND stream from the provider
        (many accounts allow only one). If the mini player is busy, ask first:
        stop it, open anyway, or cancel. Returns False to abort."""
        busy = (self.player is not None and self.player.isVisible()
                and self.playback_mode() == "embedded"
                and self._playing_key is not None)
        if not busy:
            return True
        idx = self._choice_dialog(
            tr("ext_play_title"), tr("ext_play_body"),
            [(tr("ext_play_stop_open"), "primary"),
             (tr("ext_play_keep_open"), "normal"),
             (tr("common_cancel"), "normal")])
        if idx == 0:
            self.player.stop()          # free the connection first
            return True
        return idx == 1                 # 1 = open anyway; else cancel

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
        self.resume.record(self._playing_group, self._playing_key,
                           self.player.playback_position(),
                           self.player.playback_duration())
        self._playback_max_pct = max(self._playback_max_pct,
                                     self.player.progress_percent())

    def _resume_offset(self, key, kind: str) -> float:
        """Ask whether to resume a partly-watched title; return the start
        offset in seconds (0 to start from the beginning)."""
        pos = self.resume.saved_position(key, kind)
        if pos <= 0:
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

    # Watching past this point counts as "seen the whole thing" - credits
    # are routinely skipped, so demanding 100% would miss most real views.
    _AUTO_WATCHED_PCT = 90.0

    def _maybe_auto_mark_watched(self) -> None:
        """Automatically mark the movie/episode that just finished playing as
        watched (local layer) when it was played past _AUTO_WATCHED_PCT.
        Called at every point a playback session ends: Stop, switching to
        another title, and app close. Reuses the same helpers as the
        right-click 'Mark as watched' so the badge appears identically;
        Trakt learns of the mark through the regular local->Trakt sync
        (the live scrobble usually beats it there anyway). Embedded player
        only - external players never report their position."""
        last = getattr(self, "_last_playback", None)
        if not last or last.get("kind") not in ("movie", "episode"):
            return
        if last.get("_auto_marked"):
            return                     # once per playback session
        pct = max(self._playback_max_pct,
                  self.player.progress_percent() if self.player else 0.0)
        if pct < self._AUTO_WATCHED_PCT:
            return
        last["_auto_marked"] = True
        item = last.get("item") or {}
        if last["kind"] == "movie":
            self._mark_movie_watched(item, push_to_trakt=False)
            return
        # Episode marks attribute the episode to the *current* series
        # context; restore the snapshot from when playback started in case
        # the user browsed elsewhere while the episode played.
        saved_ctx = self.series_ctx
        self.series_ctx = last.get("series_ctx") or saved_ctx
        try:
            self._mark_episode_watched(item, push_to_trakt=False)
        finally:
            self.series_ctx = saved_ctx

    def _on_player_stopped(self) -> None:
        """The Stop button was pressed. Save the resume point while the player
        still knows the position, then clear the now-playing highlight/title.
        _last_playback is kept so Play can bring this title back."""
        self._save_resume_position()
        self._maybe_auto_mark_watched()
        self._playing_key = None
        self._playing_group = None
        self._playing_item = None
        self._sync_player_buttons()
        self.listw.viewport().update()
        self.setWindowTitle(self._base_title)

    def _autoplay_next_episode(self) -> bool:
        return self.settings.value("autoplay_next_episode", "true") == "true"

    def _next_episode_item(self):
        """The next episode after the one currently/last playing, as
        (item, queue, next_index, series_ctx) - or None if there isn't one."""
        last = getattr(self, "_last_playback", None)
        if not last or last.get("kind") != "episode":
            return None
        queue = last.get("ep_queue") or []
        idx = last.get("ep_index", -1)
        if idx < 0 or idx + 1 >= len(queue):
            return None
        return queue[idx + 1], queue, idx + 1, last.get("series_ctx")

    def _has_next_episode(self) -> bool:
        return self._next_episode_item() is not None

    def _advance_to_next_episode(self) -> bool:
        """Play the next episode in the current series, carrying the episode
        queue forward so it keeps advancing. Returns False if there's no next
        episode or we're not on the embedded player."""
        nxt = self._next_episode_item()
        if not nxt or self.playback_mode() != "embedded":
            return False
        item, queue, index, ctx = nxt
        saved = self.series_ctx
        self.series_ctx = ctx or saved
        try:
            url = self.client.episode_url(
                item.get("id"), item.get("container_extension"))
            if not url:
                return False
            title = item.get("name") or item.get("title") or "dopeIPTV"
            self._ep_queue_override = queue
            self._ep_index_override = index
            self._start_playback(
                url, title, item.get("stream_icon") or item.get("cover"),
                self._item_key(item), "episode", record=True, item=item)
            return True
        finally:
            self.series_ctx = saved

    def _on_player_finished(self) -> None:
        """An episode played to its natural end. Mark it watched and, if
        enabled, autoplay the next episode in the same series."""
        last = getattr(self, "_last_playback", None)
        if not last or last.get("kind") != "episode":
            return
        # Give the just-finished episode its watched mark (it reached the end).
        self._save_resume_position()
        self._maybe_auto_mark_watched()
        if self._autoplay_next_episode():
            self._advance_to_next_episode()

    def _play_next_episode(self) -> None:
        """The player's 'next episode' button - skip straight to the next one
        without waiting for the current episode to end."""
        self._advance_to_next_episode()

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
        # Remember where we were in whatever was playing before switching,
        # and give the outgoing title its watched mark if it earned one.
        self._save_resume_position()
        self._maybe_auto_mark_watched()
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
                               "key": key, "kind": kind, "item": item,
                               "series_ctx": (self.series_ctx
                                              if kind == "episode" else None)}
        # Snapshot the ordered episode queue + current index so a natural
        # end-of-episode can autoplay the next one, even if the user browses
        # away meanwhile. An override (set by autoplay itself) carries the same
        # queue forward; otherwise take the episode list currently shown.
        if kind == "episode":
            queue = getattr(self, "_ep_queue_override", None)
            idx = getattr(self, "_ep_index_override", -1)
            self._ep_queue_override = None
            self._ep_index_override = -1
            if queue is None and self.series_ctx:
                queue = [e for e in (self.all_items or [])
                         if not e.get("_header")]
                idx = next((i for i, e in enumerate(queue)
                            if e is item or self._item_key(e) == key), -1)
            self._last_playback["ep_queue"] = queue or []
            self._last_playback["ep_index"] = idx
        # Offer the in-player 'next episode' button only when one is queued.
        if self.player is not None:
            self.player.set_next_available(
                kind == "episode" and self._has_next_episode())
        self._playback_max_pct = 0.0
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

    MAX_STREAM_RETRIES = 2

    def _playback_error(self, msg: str) -> None:
        self.rec.finish_all_inplayer("stream error")
        self.wake.release()
        self._trakt_active = None
        # Live streams drop briefly all the time (single-connection accounts,
        # HLS segment hiccups, the window being dragged). Reconnect silently a
        # couple of times before surfacing the error. Reset the counter when
        # the stream had been stable for a while, so a later drop retries too.
        now = time.time()
        if now - getattr(self, "_last_stream_error_ts", 0.0) > 20:
            self._stream_retries = 0
        self._last_stream_error_ts = now
        lp = getattr(self, "_last_playback", None)
        if (lp and lp.get("kind") == "live" and self.player
                and getattr(self, "_stream_retries", 0) < self.MAX_STREAM_RETRIES):
            self._stream_retries = getattr(self, "_stream_retries", 0) + 1
            self.player.current_url = None
            if not self._player_fs:
                self.stream_error.hide()
            self._set_status(tr("status_reconnecting"))
            if self._player_fs and self.player:
                self.player.set_overlay_info(tr("status_reconnecting"))
            QTimer.singleShot(1500, self._retry_last_stream)
            return
        self._stream_retries = 0
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

    def _on_player_stalled(self) -> None:
        """The player reported the live stream frozen (buffer-starved). Reconnect
        silently, respecting the same retry budget as a hard error."""
        lp = getattr(self, "_last_playback", None)
        if not (self.player and self.player.isVisible()):
            return
        if not lp or lp.get("kind") != "live":
            return
        now = time.time()
        if now - getattr(self, "_last_stream_error_ts", 0.0) > 20:
            self._stream_retries = 0
        if getattr(self, "_stream_retries", 0) >= self.MAX_STREAM_RETRIES:
            return   # tried hard already; leave it for the user to zap
        self._last_stream_error_ts = now
        self._stream_retries = getattr(self, "_stream_retries", 0) + 1
        self.player.current_url = None
        self._set_status(tr("status_reconnecting"))
        QTimer.singleShot(300, self._retry_last_stream)

    def _retry_last_stream(self) -> None:
        lp = getattr(self, "_last_playback", None)
        if not lp or lp.get("kind") != "live":
            return
        it = lp.get("item")
        if it is not None:
            self.play_live_channel(it)   # re-derives a fresh live URL
        else:
            self._start_playback(lp["url"], lp["title"], lp.get("icon_url"),
                                 lp.get("key"), "live")

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
        # Skip section-header rows in the grouped views.
        for _ in range(count):
            it = self.list_model.item_at(new_row)
            if not (it and it.get("_header")):
                break
            new_row = (new_row + direction) % count
        idx = self.list_model.index(new_row)
        self.listw.setCurrentIndex(idx)
        self.listw.scrollTo(idx)
        self.play()

    # -- context menu --------------------------------------------------------------


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


    def _show_busy(self) -> None:
        """Show the top 'busy' strip. Always indeterminate (no jumpy 0-100 %),
        and armed with a watchdog so it can never get stuck on screen: every
        call restarts a timer that force-hides it if nothing refreshes it."""
        bar = self.loading_bar
        bar.setRange(0, 0)
        bar.setVisible(True)
        wd = getattr(self, "_busy_watchdog", None)
        if wd is None:
            wd = QTimer(self)
            wd.setSingleShot(True)
            wd.setInterval(25000)
            wd.timeout.connect(self._hide_busy)
            self._busy_watchdog = wd
        wd.start()

    def _hide_busy(self) -> None:
        wd = getattr(self, "_busy_watchdog", None)
        if wd is not None:
            wd.stop()
        self.loading_bar.setVisible(False)

    def _on_epg_progress(self, value: int) -> None:
        # The guide download reports progress erratically - often no total, and
        # nothing at all during the several-second parse after it hits 100 % -
        # so a percentage looked jumpy and got stuck. Drive a calm indeterminate
        # strip + a one-off status line instead.
        self._show_busy()
        if value <= 0:
            self._set_status(tr("status_loading_programme_guide"))

    def _epg_progress_finished(self) -> None:
        self._hide_busy()

    def _error(self, msg: str) -> None:
        self._hide_busy()
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

    def _size_to_screen(self) -> None:
        """First run only: open at a comfortable fraction of the actual
        display instead of a fixed 1240x780, and centre it. Capped so it
        stays sane on very large / multi-monitor desktops. Runs once - after
        this the saved geometry takes over, so nothing resizes 'by itself'."""
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        # Aim for ~90% of the display, capped on huge monitors. The floor is
        # itself clamped to the screen, so a small laptop never gets a window
        # bigger than it can show - it just fills what's there.
        w = min(int(avail.width() * 0.90), 2600)
        h = min(int(avail.height() * 0.90), 1600)
        w = min(max(w, min(1100, avail.width())), avail.width())
        h = min(max(h, min(720, avail.height())), avail.height())
        self.resize(w, h)
        self.move(avail.x() + (avail.width() - w) // 2,
                  avail.y() + (avail.height() - h) // 2)

    def _restore_splitter_state(self) -> None:
        """Restore the panel divider positions from last session. Runs after
        the window is shown at its restored size so the saved proportions land
        exactly instead of being rescaled from the default geometry."""
        from PyQt6.QtCore import QByteArray
        st = self.settings.value("splitter_state")
        if isinstance(st, QByteArray) and st.size() > 0:
            self._root.restoreState(st)
            return
        # First run: give the video (right) column a share of the real width
        # so it's usefully large on a wide screen, instead of a fixed 380 px.
        total = self._root.width()
        if total > 900:
            side = 240
            det = min(max(420, int(total * 0.36)), 1000)
            self._root.setSizes([side, total - side - det, det])

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
        if self._welcome is not None and self._welcome.isVisible():
            self._welcome.cover()
        self._position_provider_hint()
        if getattr(self, "_focus_mode", False):
            self._position_reopen()
        # The justified poster grid re-flows its columns from ChannelListView's
        # own resizeEvent, so nothing else to do here.

    # -- first-run onboarding ------------------------------------------------

    def show_welcome(self) -> None:
        """Show the first-run onboarding wizard (no provider configured)."""
        if self._welcome is None:
            self._welcome = WelcomeOverlay(
                self, settings=self.settings,
                on_connect=self._wizard_connect,
                on_explore=self._wizard_explore,
                on_connect_trakt=lambda: self._trakt_connect_flow(self),
                on_language_change=self._wizard_language,
                on_demo=self.start_demo)
        else:
            self._welcome.reset()
        self._welcome.cover()
        self._update_provider_hint()

    def _wizard_language(self, code: str) -> None:
        from ..i18n import set_language
        set_language(code)
        self.settings.setValue("language", code)
        self.retranslate_ui()

    def _wizard_connect(self, server: str, user: str, pw: str,
                        kind: str = "xtream") -> None:
        name = server.split("//")[-1].split("/")[0] or "My playlist"
        pl = self.playlist_store.add(
            {"name": name, "kind": kind, "server": server, "username": user,
             "password": pw, "epg_url": "", "refresh": "never"})
        self.playlist_store.set_active(pl["id"])
        self.switch_playlist(pl["id"])
        # The wizard stays open for the optional Trakt step and hides itself
        # on Finish; the provider hint should not appear now.
        self._update_provider_hint()

    def _wizard_explore(self) -> None:
        self._set_status(tr("welcome_add_hint"))
        self._update_provider_hint()

    # -- "no provider yet" affordance ----------------------------------------

    def _update_provider_hint(self) -> None:
        """Show a big pulsing '+ Add provider' button in the middle pane
        whenever the app is running without a real provider and the wizard is
        closed. It brings the wizard back and disappears the moment a provider
        is added."""
        # Show the hint until a REAL provider is added: explore mode
        # (OfflineClient) and the demo (DemoClient) both still want it, so the
        # user can graduate from trying channels to entering their own. A real
        # Xtream/M3U client removes it for good. M3UClient also subclasses
        # OfflineClient, so key on the exact types. Never float it over a
        # maximized/fullscreen video.
        no_provider = type(self.client) in (OfflineClient, DemoClient)
        overlay_up = self._welcome is not None and self._welcome.isVisible()
        if no_provider and not overlay_up and not self._player_fs:
            if self._add_provider_btn is None:
                self._build_provider_hint()
            self._add_provider_btn.setText(tr("onb_add_provider"))
            self._add_provider_btn.show()
            self._add_provider_btn.raise_()
            self._position_provider_hint()
            self._add_provider_anim.start()
        elif self._add_provider_btn is not None:
            self._add_provider_anim.stop()
            self._add_provider_btn.hide()

    def _build_provider_hint(self) -> None:
        from PyQt6.QtCore import QPropertyAnimation
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        btn = QPushButton(tr("onb_add_provider"), self)
        btn.setMinimumHeight(46)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # Always red, independent of the app theme, so it clearly reads as
        # a call to action.
        btn.setStyleSheet(
            "QPushButton { background:#e5354b; color:#ffffff; font-weight:700;"
            " font-size:14px; border:none; border-radius:10px; padding:0 22px; }"
            "QPushButton:hover { background:#c8283b; }")
        btn.clicked.connect(self.show_welcome)
        # Slow opacity pulse to draw the eye without being noisy.
        eff = QGraphicsOpacityEffect(btn)
        btn.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", self)
        anim.setDuration(1500)
        anim.setStartValue(1.0)
        anim.setKeyValueAt(0.5, 0.45)
        anim.setEndValue(1.0)
        anim.setLoopCount(-1)
        self._add_provider_btn = btn
        self._add_provider_anim = anim

    def _position_provider_hint(self) -> None:
        btn = self._add_provider_btn
        if btn is None or not btn.isVisible():
            return
        # Lower third of the middle list pane, mapped into window coordinates.
        btn.adjustSize()
        w = max(240, btn.width() + 40)
        h = 46
        tl = self.listw.mapTo(self, self.listw.rect().topLeft())
        x = tl.x() + (self.listw.width() - w) // 2
        y = tl.y() + int(self.listw.height() * 0.90) - h // 2
        btn.setGeometry(x, y, w, h)

    def closeEvent(self, event) -> None:
        # In PiP the main window itself is the little floating player, so its
        # own close button would otherwise quit the app. Treat it as "leave
        # PiP" and drop back to the normal mini-player window instead.
        if self._pip_win is not None:
            event.ignore()
            self._exit_pip()
            return
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
        self._maybe_auto_mark_watched()
        self.wake.release()
        self.cover.flush()
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
