"""Main application window: sidebar, channel list, detail panel, playback."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta

from PyQt6.QtCore import (
    QEvent, QPoint, QPointF, QSettings, QSize, Qt, QThreadPool,
    QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QAction, QColor, QIcon, QKeySequence, QPainter,
    QPixmap, QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QBoxLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QSplitter, QToolButton, QVBoxLayout, QWidget,
)

from .. import APP_NAME, ORG
from ..core.log import log
from ..i18n import tr
from .channel_list import (
    CategoryColorDelegate, ChannelDelegate, ChannelListModel, ChannelListView,
)
from ..providers.chromecast import CastDialog, ChromecastManager
from ..providers.client import (
    DemoClient, XtreamClient, make_client,
)
from ..media.embedded import EmbeddedPlayer, _SeekSlider
from ..providers.epg import XmltvGuide, epg_cache_path, prune_epg_caches
from ..services.coverart import CoverArtService
from ..services.resume import ResumeStore
from ..services.reminders import ReminderStore
from ..media.players import embedded_playback_supported, launch_player
from ..core.recording import RecordingManager
from ..core.stores import (
    CategoryOverrides, ChannelOverrides, FavoriteStore, HistoryStore,
    ParentalControl, PlaylistStore, WatchedStore, WatchlistStore,
)
from .theme import P
from ..providers.trakt import TraktClient
from ..core.wakelock import WakeLock
from .widgets import (
    FlowRow, _HoverTextButton, _SidebarLogo, _Toast, cast_strip_icon,
)
from .mw_settings import _SettingsMixin
from .mw_trakt import _TraktMixin
from .mw_recording import _RecordingMixin
from .mw_busy import _BusyMixin
from .mw_context import _ContextMenuMixin
from .mw_detail import _DetailMixin
from .mw_nav import _NavMixin
from .mw_multiview import _MultiviewMixin
from .mw_home import _HomeMixin
from .mw_local import _LocalFilesMixin
from .mw_onboarding import _OnboardingMixin
from .mw_popout import _PopoutMixin
from .mw_reminders import _RemindersMixin
from .mw_search import _SearchMixin
from .mw_shortcuts import _ShortcutsMixin
from .mw_sidebar import _SidebarMixin
from .mw_sort import _SortMixin
from .mw_updates import _UpdatesMixin
from ..core.workers import (
    LogoLoader, default_image_cache_dir, run_async)


# Sentinel for "no pending category to reselect" - distinct from None, which
# is a real category id (the "All" row).
_UNSET = object()


class MainWindow(_SettingsMixin, _TraktMixin, _RecordingMixin,
                 _ContextMenuMixin, _DetailMixin, _RemindersMixin,
                 _BusyMixin, _UpdatesMixin, _SearchMixin, _SidebarMixin,
                 _NavMixin, _ShortcutsMixin, _OnboardingMixin, _SortMixin,
                 _LocalFilesMixin,
                 _PopoutMixin, _MultiviewMixin, _HomeMixin, QMainWindow):
    """Primary application window with sidebar, channel list, and detail panel."""

    epg_progress = pyqtSignal(int)

    def __init__(self, client: XtreamClient, settings: QSettings,
                 playlists: PlaylistStore | None = None) -> None:
        super().__init__()
        self._welcome = None  # first-run onboarding overlay; created on demand
        self._local_ctx = None  # current subdirectory in the Local files view
        self._local_series = None  # drilled-into series in the library view
        self._add_provider_btn = None  # "+ Add provider" hint when offline
        self.client = client
        self.settings = settings
        self.playlist_store = playlists
        active_pl = playlists.active() if playlists else None
        # Reclaim EPG guides left behind by playlists that no longer exist -
        # each is hundreds of MB and they were never cleaned up.
        try:
            keep = [p.get("id") for p in playlists.items] if playlists else []
            n = prune_epg_caches(keep)
            log.info("EPG cache prune: kept %d playlist(s) %s, removed %d "
                     "orphaned file(s)", len(keep), keep, n)
        except Exception as e:
            log.warning("EPG cache prune failed: %s", e)
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
        self._resume_settings = self._open_resume_settings(settings)
        self.resume = ResumeStore(self._resume_settings, pid)
        self.reminders = ReminderStore(settings, pid)
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
        # The TMDB poster/person caches and the Trakt watched/watchlist caches
        # are multi-MB blobs. Kept in the shared settings they made every write
        # to that file (a volume nudge, a resume tick) rewrite all of it on
        # sync - a main-thread stall that hitched video. Route every large,
        # frequently-rewritten cache through a dedicated cache file so the
        # shared settings stays small and any write to it syncs in ~1ms.
        self._cache_settings = self._open_cache_settings(settings)
        self.cover = CoverArtService(
            self._cache_settings, self.logos,
            lambda: self._poster_refresh_timer.start(150))
        self.trakt = TraktClient(settings)
        self._trakt_active: dict | None = None
        self.watched = WatchedStore(self._cache_settings)
        self.watchlist = WatchlistStore(self._cache_settings)
        self._watched_sync_running = False
        self._raw_categories: list = []
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
        self._playing_catchup = False   # watching a catch-up archive segment
        self._ts_catchup_program = False  # catch-up is a picked programme (vs scrub)
        self._ts_program_start = None     # picked programme's start (bar origin)
        self._ts_program_stop = None      # picked programme's stop timestamp
        self._ts_program_title = None     # picked programme's title (for reloads)
        self._ts_depth_min = 0            # live-timeline window span (minutes)
        self._ts_live_offset = 0.0        # seconds behind live from buffer pauses
        self._pause_started = None
        self._playing_item = None
        self._focus_mode = False
        self._fav_view_tint = ("", "")
        self._pending_cat_select = _UNSET
        # Last sub-category visited per section, so TV -> Movies -> TV comes
        # back to where you were instead of the top of the list. Per session,
        # and per provider (switch_playlist clears it - another provider's
        # category ids mean nothing here).
        self._last_cat: dict = {}
        self._pending_jump_key = None
        self._pending_jump_cat = None
        self._stream_retries = 0
        self._last_stream_error_ts = 0.0
        self._cast_device: str | None = None   # device a cast is running on
        self._cast_paused_at = None            # when the cast was paused
        self._cast_behind = 0.0                # and how far behind live it is
        # What is on the TV, so the list can mark it exactly as it marks what
        # is playing here. Nothing else should read these - they are not the
        # app's own playback state.
        self._cast_key = None
        self._cast_group = None
        self._cast_ctx: dict = {}              # what is being cast
        self._popout_win = None
        self._popout_placeholder = None
        self._popout_mirror = None   # macOS mirror surface (see mw_popout)
        self._multiview_win = None
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
        self._show_busy(tr("status_loading_channels"))
        QTimer.singleShot(100, self._load_categories)
        # Learn which Browse modes this provider actually has, then hide the
        # empty ones (deferred so the visible category load goes first).
        QTimer.singleShot(150, self._refresh_mode_availability)
        # Cross-device sync of watched movies/episodes from Trakt. Deferred
        # so the initial category/EPG traffic goes first - the sync runs
        # for the full account which can take a couple of seconds.
        QTimer.singleShot(2500, self._maybe_sync_watched)
        # Light the badge from the cached result almost immediately so it isn't
        # missing for the first few seconds, then do the (throttled) network
        # check later so it doesn't compete with the initial load.
        QTimer.singleShot(400, self._apply_cached_update)
        QTimer.singleShot(4000, self._maybe_check_updates)

        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.timeout.connect(self._maybe_auto_refresh)
        self._auto_refresh_timer.start(5 * 60_000)

        # Land on the Home page (if enabled) - the classic view keeps loading
        # underneath, so leaving Home is instant.
        self._maybe_open_home_at_start()

    # -- UI construction -------------------------------------------------------

    def _fill_playlist_menu(self, menu: QMenu) -> None:
        """Fill *menu* with a checkable entry per playlist, the active one
        ticked. Used by the sidebar playlist switcher."""
        menu.clear()
        store = self.playlist_store
        if not store or not store.playlists():
            act = menu.addAction(tr("pl_mgmt_unavailable"))
            act.setEnabled(False)
            return
        active = store.active_id
        for p in store.playlists():
            act = menu.addAction(p.get("name") or p.get("server") or "?")
            act.setCheckable(True)
            act.setChecked(p["id"] == active)
            if p["id"] != active:
                act.triggered.connect(
                    lambda _c=False, pid=p["id"]: self.switch_playlist(pid))

    def _show_playlist_menu(self) -> None:
        """Pop the playlist switcher centered directly under the sidebar's
        playlist button (not left-aligned to its corner)."""
        menu = QMenu(self)
        self._fill_playlist_menu(menu)
        btn = self._playlist_btn
        below = btn.mapToGlobal(btn.rect().bottomLeft())
        menu_w = menu.sizeHint().width()
        x = below.x() + (btn.width() - menu_w) // 2
        menu.exec(QPoint(x, below.y()))

    def _update_playlist_btn(self) -> None:
        """Reflect the active playlist on the sidebar switcher button."""
        if not hasattr(self, "_playlist_btn"):
            return
        store = self.playlist_store
        pl = store.get(store.active_id) if (store and store.active_id) else None
        name = (pl or {}).get("name", "") if pl else ""
        # Icon-only at rest in both sidebar states: the drawn playlist-stack
        # mark under the logo. Hovering it (expanded only) reveals the active
        # playlist's name next to the icon; the name also lives in the
        # tooltip and the window title.
        self._playlist_btn.setText("")
        self._playlist_btn.hover_text = name
        self._playlist_btn.setIcon(
            QIcon(self._action_pixmap("stack", 18, P["text2"])))
        self._playlist_btn.setIconSize(QSize(18, 18))
        tip = tr("menu_playlists") + (f" — {name}" if name else "")
        self._playlist_btn.setToolTip(tip)
        # Only worth showing when there's actually more than one playlist to
        # switch between - a single-playlist user has nothing to pick.
        multiple = bool(store and len(store.playlists()) > 1)
        self._playlist_btn.setVisible(multiple)

    def _build_ui(self) -> None:
        # Dropping a video file anywhere on the window plays it (see
        # dragEnterEvent/dropEvent - the local-files entry points).
        self.setAcceptDrops(True)
        menubar = self.menuBar()
        app_menu = menubar.addMenu(APP_NAME)
        settings_action = app_menu.addAction(tr("btn_settings") + "…")
        settings_action.triggered.connect(self.open_settings)
        refresh_action = app_menu.addAction(tr("menu_refresh_playlist"))
        refresh_action.triggered.connect(self.refresh_playlist)
        open_video_action = app_menu.addAction(tr("menu_open_video"))
        open_video_action.setShortcut("Ctrl+O")
        open_video_action.triggered.connect(self.open_local_video)
        reminders_action = app_menu.addAction(tr("reminders_menu"))
        reminders_action.triggered.connect(self._open_reminders)
        multiview_action = app_menu.addAction(tr("menu_multiview"))
        multiview_action.triggered.connect(self._show_multiview)
        cast_action = app_menu.addAction(tr("ctx_cast_to_chromecast"))
        cast_action.triggered.connect(self.cast_playing)
        # Only while something is playing - there is nothing to send
        # otherwise, and a dead entry is worse than none.
        app_menu.aboutToShow.connect(
            lambda: cast_action.setEnabled(self.can_cast_playing()))
        if sys.platform != "darwin":
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
        if sys.platform == "darwin":
            # Refresh playlist / Reminders have no standard macOS role, so Qt
            # leaves them behind in a second, duplicate "dopeIPTV" menu.
            # ApplicationSpecificRole folds them into the one real app menu, so
            # there's no duplicate. (No-op on Linux/Windows, where the single
            # menu with these items is intentional.)
            refresh_action.setMenuRole(QAction.MenuRole.ApplicationSpecificRole)
            open_video_action.setMenuRole(
                QAction.MenuRole.ApplicationSpecificRole)
            reminders_action.setMenuRole(
                QAction.MenuRole.ApplicationSpecificRole)
            multiview_action.setMenuRole(
                QAction.MenuRole.ApplicationSpecificRole)
            cast_action.setMenuRole(QAction.MenuRole.ApplicationSpecificRole)
        # Kept for live language switching (see retranslate_ui).
        self._i18n_actions = {
            settings_action: lambda: tr("btn_settings") + "…",
            refresh_action: lambda: tr("menu_refresh_playlist"),
            open_video_action: lambda: tr("menu_open_video"),
            reminders_action: lambda: tr("reminders_menu"),
            multiview_action: lambda: tr("menu_multiview"),
            cast_action: lambda: tr("ctx_cast_to_chromecast"),
            about_action: lambda: tr("menu_about"),
            quit_action: lambda: tr("menu_quit"),
        }

        root = QSplitter(Qt.Orientation.Horizontal)
        root.setHandleWidth(6)
        # The central area is a stack: page 0 is the classic three-column
        # view, page 1 (added lazily) is the full-window Home section.
        from PyQt6.QtWidgets import QStackedWidget
        self._center_stack = QStackedWidget()
        self._center_stack.addWidget(root)
        self.setCentralWidget(self._center_stack)

        # Sidebar. Its content lives inside a scroll area so that on a short
        # screen (small laptops) the bottom actions - EPG guide, Settings -
        # stay reachable by scrolling instead of being clipped off. On a tall
        # screen the scroll bar never appears and it looks identical to before.
        # The scroll area sits INSIDE `side` (not in its place), so the
        # collapse-to-rail and splitter logic, which drive `side` and the button
        # widgets directly, are untouched.
        side = QWidget(objectName="Sidebar")
        _side_outer = QVBoxLayout(side)
        _side_outer.setContentsMargins(0, 0, 0, 0)
        _side_outer.setSpacing(0)
        self._side_scroll = QScrollArea()
        self._side_scroll.setWidgetResizable(True)
        self._side_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._side_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._side_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # Transparent so the themed #Sidebar background (on `side`) shows
        # through. IMPORTANT: scope these to the widgets themselves with ID
        # selectors - a bare `background: transparent` cascades to child
        # widgets and wipes their own backgrounds (it silently blanked the
        # Guide/Settings action buttons' fill).
        self._side_scroll.setObjectName("SideScroll")
        self._side_scroll.setStyleSheet(
            "QScrollArea#SideScroll { background: transparent; border: 0; }")
        self._side_scroll.viewport().setObjectName("SideViewport")
        self._side_scroll.viewport().setStyleSheet(
            "QWidget#SideViewport { background: transparent; }")
        _side_outer.addWidget(self._side_scroll)
        _side_content = QWidget(objectName="SideContent")
        _side_content.setStyleSheet(
            "QWidget#SideContent { background: transparent; }")
        self._side_scroll.setWidget(_side_content)
        sl = QVBoxLayout(_side_content)
        sl.setContentsMargins(12, 16, 12, 12)
        # Tight vertical rhythm so the TV..History nav stack stays compact.
        sl.setSpacing(2)

        # Small themed logo at the top of the sidebar (recolours with theme).
        self._sidebar_logo = _SidebarLogo()
        self._sidebar_logo.setMinimumWidth(0)
        self._sidebar_logo.setSizePolicy(QSizePolicy.Policy.Ignored,
                                         QSizePolicy.Policy.Fixed)
        self._sidebar_logo.setToolTip(tr("tooltip_jump_playing"))
        self._sidebar_logo.clicked.connect(self._jump_to_now_playing)
        # The logo draws a small update badge in its top-right corner when a
        # newer release is out; clicking that corner opens About.
        self._sidebar_logo.update_clicked.connect(self.show_about)
        sl.addWidget(self._sidebar_logo)
        sl.addSpacing(6)

        # Visible playlist switcher at the top of the sidebar: one click to hop
        # providers (e.g. to feed multiview cells from different accounts)
        # without a trip through Settings. Shows the active playlist; the menu
        # is built on demand. Hidden when there's nothing to switch.
        self._playlist_btn = _HoverTextButton("", objectName="PlaylistChip")
        # Fit the pill to its label (the active playlist name) instead of
        # stretching the full sidebar width; centred under the logo. On the
        # collapsed rail it goes back to filling the rail (see
        # _apply_sidebar_chrome).
        self._playlist_btn.setSizePolicy(QSizePolicy.Policy.Maximum,
                                         QSizePolicy.Policy.Fixed)
        self._playlist_btn.setToolTip(tr("menu_playlists"))
        self._playlist_btn.clicked.connect(self._show_playlist_menu)
        sl.addWidget(self._playlist_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        sl.addSpacing(6)
        self._update_playlist_btn()

        # Vector-icon kinds for the nav entries (drawn in _action_pixmap).
        # Each must be visually distinct: watch-later is a bookmark,
        # recordings a record dot, history a clock - no two alike. Drawn, not
        # emoji: every OS renders its own emoji font differently.
        self._rail_glyphs = {
            "home": "home",
            "live": "tv", "vod": "movie", "series": "series", "fav": "star",
            "watchlist": "bookmark", "watched": "check", "rec": "rec",
            "history": "clock", "local": "folder",
        }
        self._nav_texts: dict[str, str] = {}
        self.nav_btns: dict[str, QPushButton] = {}
        # Browse-mode availability per active provider (see
        # _refresh_mode_availability): hides TV/Movies/Series a provider has no
        # content for. Empty/unknown = shown, so nothing hides until we KNOW.
        self._avail_gen = 0
        self._mode_avail: dict[str, bool] = {}

        def _make_nav(key: str, text: str, into, primary: bool = False) -> None:
            b = QPushButton(text, objectName="NavBtn")
            b.setCheckable(True)
            b.setFlat(True)
            b.setToolTip(text)
            # Browse (TV/Movies/Series) is the primary tier: a couple of
            # notches larger than the Library rows (see the NavBtn[primary]
            # theme rule; _apply_nav_icons paints its icons larger to match).
            b.setProperty("primary", "true" if primary else "false")
            # A fixed-size icon so every label starts at the SAME x (emoji
            # glyphs have different advance widths, so putting them in the text
            # left the labels ragged). The icon is (re)painted and sized by
            # _apply_nav_icons in the theme's muted tone, white when checked.
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
            self._nav_texts[key] = text
            into.addWidget(b)
            self.nav_btns[key] = b
            self._apply_nav_color(key)

        # Browse: the content-type modes, always visible at the top. Home
        # first - it is not a MODE (it swaps the whole central stack), so its
        # click is rewired from switch_mode to the Home page below.
        for key, text in (("home", tr("nav_home")),
                          ("live", tr("nav_tv")), ("vod", tr("nav_movies")),
                          ("series", tr("nav_series"))):
            _make_nav(key, text, sl, primary=True)
        self.nav_btns["home"].clicked.disconnect()
        self.nav_btns["home"].clicked.connect(self._show_home_page)
        self.nav_btns["home"].setVisible(self._home_enabled())

        # Library: the personal lists, grouped under a collapsible disclosure
        # header (same arrow affordance as Categories) so they don't add to the
        # wall of nav items when you don't need them. The header lives in its
        # own widget (with a little top gap) so the whole thing - gap included -
        # disappears on the icon rail, keeping the library icons tight against
        # the browse icons there.
        self._lib_header = QWidget()
        lib_hdr = QHBoxLayout(self._lib_header)
        lib_hdr.setContentsMargins(0, 8, 0, 0)
        lib_hdr.setSpacing(4)
        self._lib_section_label = QLabel(
            tr("sidebar_library"), objectName="SectionLabel")
        self._lib_section_label.setMinimumWidth(0)
        lib_hdr.addWidget(self._lib_section_label)
        lib_hdr.addStretch()
        self._lib_toggle = QToolButton(objectName="SectionToggle")
        self._lib_toggle.setCheckable(True)
        self._lib_toggle.setArrowType(Qt.ArrowType.DownArrow)
        self._lib_toggle.setAutoRaise(True)
        self._lib_toggle.setFixedSize(22, 18)
        self._lib_toggle.setToolTip(tr("tooltip_toggle_library"))
        self._lib_toggle.toggled.connect(self._on_library_toggle)
        lib_hdr.addWidget(self._lib_toggle)
        sl.addWidget(self._lib_header)

        # Container so the whole group collapses/reveals in one move. No
        # stylesheet on it (a bare 'background' would cascade onto the child
        # buttons and wipe their :checked accent - see the #SideContent note).
        self._library_box = QWidget()
        lib_lay = QVBoxLayout(self._library_box)
        lib_lay.setContentsMargins(0, 0, 0, 0)
        lib_lay.setSpacing(2)
        for key, text in (("fav", tr("nav_favorites")),
                          ("watchlist", tr("nav_watchlist")),
                          ("watched", tr("nav_watched")),
                          ("rec", tr("nav_recordings")),
                          ("local", tr("nav_local")),
                          ("history", tr("nav_history"))):
            _make_nav(key, text, lib_lay)
        # The box must not be stretchable: it holds fixed-height buttons, and a
        # default Preferred policy let the layout balloon it when the rail has
        # spare height, spreading the library icons far apart.
        self._library_box.setSizePolicy(QSizePolicy.Policy.Preferred,
                                        QSizePolicy.Policy.Fixed)
        sl.addWidget(self._library_box)
        # Rail-only filler. Expanded, the CATEGORY LIST is the layout's stretch
        # item and soaks up the spare height - but on the icon rail it's
        # hidden, and a QVBoxLayout with no stretch item spreads its surplus
        # between the remaining rows instead, which blew the icons apart
        # (browse tight, library gaps ~10x). Shown only on the rail, this
        # invisible expanding filler takes over the category list's job so the
        # icons stay packed at the top and the actions stay at the bottom.
        self._rail_filler = QWidget()
        self._rail_filler.setSizePolicy(QSizePolicy.Policy.Ignored,
                                        QSizePolicy.Policy.Expanding)
        self._rail_filler.hide()
        sl.addWidget(self._rail_filler, 1)
        self._apply_nav_icons()
        self.nav_btns["live"].setChecked(True)
        # Restore the remembered collapsed state (setChecked fires the handler).
        if self.settings is not None:
            self._lib_toggle.setChecked(
                self.settings.value("library_collapsed", False, type=bool))

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
        # A search toggle that reveals the category search box only when you
        # want it, so it doesn't take a permanent row in the sidebar.
        self.cat_search_btn = QToolButton(objectName="SectionToggle")
        self.cat_search_btn.setCheckable(True)
        self.cat_search_btn.setAutoRaise(True)
        self.cat_search_btn.setFixedSize(22, 18)
        self.cat_search_btn.setToolTip(tr("cat_search_placeholder"))
        self._apply_cat_search_icon()   # ink-centred 🔍 (not a top-left glyph)
        self.cat_search_btn.toggled.connect(self._toggle_cat_search)
        cat_hdr.addWidget(self.cat_search_btn)
        self.cat_solo_btn.toggled.connect(self._on_cat_solo_toggle)
        cat_hdr.addWidget(self.cat_solo_btn)
        sl.addLayout(cat_hdr)
        # Search that spans category names AND their channels: type "germany"
        # or "bbc" and the matching categories float up (ranked by how many of
        # their channels match), each previewing a few hits. Double-click to
        # enter that category. Hidden until the 🔍 toggle is clicked.
        self.cat_search = QLineEdit(objectName="CatSearch")
        self.cat_search.setPlaceholderText(tr("cat_search_placeholder"))
        self.cat_search.setClearButtonEnabled(True)
        self.cat_search.textChanged.connect(self._on_cat_search)
        self.cat_search.hide()
        sl.addWidget(self.cat_search)
        self._cat_search_timer = QTimer(self)
        self._cat_search_timer.setSingleShot(True)
        self._cat_search_timer.setInterval(220)
        self._cat_search_timer.timeout.connect(self._run_category_search)
        self._search_index_cache: dict = {}
        self.cat_list = QListWidget(objectName="CatList")
        self.cat_list.setItemDelegate(CategoryColorDelegate(self.cat_list))
        self.cat_list.setMinimumWidth(0)
        # Pixel-granular scrolling like the channel list - the default
        # jump-a-whole-row mode reads as laggy on a trackpad, especially with
        # hundreds of provider categories.
        self.cat_list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        # Give the list a real minimum height so, on a short sidebar, the OUTER
        # scroll area shows a scrollbar (keeping the bottom actions - Guide,
        # Settings - reachable) instead of the QVBoxLayout compressing the
        # fixed buttons below their size hint and clipping their text. On a tall
        # sidebar this minimum is never the binding constraint, so nothing
        # changes there.
        self.cat_list.setMinimumHeight(80)
        self.cat_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cat_list.currentItemChanged.connect(self._category_changed)
        self.cat_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.cat_list.customContextMenuRequested.connect(self._cat_menu)
        sl.addWidget(self.cat_list, 1)

        # Contextual "Sync now" - only shown in the Trakt-backed lists
        # (Watched, Watch Later, Favorites -> Trakt) so the user can pull
        # fresh data without digging into Settings. Sits above the action row.
        self._sync_now_btn = QPushButton(tr("btn_sync_now"))
        self._sync_now_btn.clicked.connect(self._sidebar_sync_now)
        # "Ignored" horizontal policy + no text-based minimum, like the nav
        # buttons: otherwise this button's label width pinned the sidebar wider
        # than the icon rail, so it couldn't collapse while a Trakt-backed list
        # (Watched / Trakt favourites) had the button showing.
        self._sync_now_btn.setSizePolicy(QSizePolicy.Policy.Ignored,
                                         QSizePolicy.Policy.Fixed)
        self._sync_now_btn.setMinimumWidth(0)
        # Deliberately loud (red, bold) so it's obvious when it appears -
        # it only shows in the Trakt-backed lists, so it shouldn't blend
        # in with the neutral sidebar buttons.
        self._sync_now_btn.setStyleSheet(
            "QPushButton{background:#e5354b; color:#ffffff; font-weight:700;"
            " border:none; border-radius:6px; padding:8px;}"
            "QPushButton:hover{background:#c8283b;}")
        self._sync_now_btn.hide()
        sl.addWidget(self._sync_now_btn)

        # Guide + Settings + Multiview as a compact row of three glyph icons
        # (tooltips carry their names) rather than wide text pills - the icons
        # are painted by _apply_action_icons. The row is a QBoxLayout whose
        # direction flips to vertical on the collapsed icon rail (60 px is too
        # narrow for three buttons abreast) - see _apply_sidebar_chrome.
        self._guide_btn = guide_btn = QPushButton("", objectName="SideAction")
        guide_btn.setToolTip(tr("btn_epg_guide"))
        guide_btn.setSizePolicy(QSizePolicy.Policy.Ignored,
                                QSizePolicy.Policy.Fixed)
        guide_btn.clicked.connect(self._open_epg_guide)
        # (Reload lives in the menu bar's "Refresh playlist" and the
        # per-playlist auto-refresh setting; a sidebar button here was just
        # an easy mis-click.)
        self._settings_btn = settings_btn = QPushButton(
            "", objectName="SideAction")
        settings_btn.setToolTip(tr("btn_settings"))
        settings_btn.setSizePolicy(QSizePolicy.Policy.Ignored,
                                   QSizePolicy.Policy.Fixed)
        settings_btn.clicked.connect(self.open_settings)
        # Multiview joins the same row (the app-menu entry is easy to miss, and
        # on macOS a hidden multiview window won't Cmd+Tab back).
        self._multiview_btn = multiview_btn = QPushButton(
            "", objectName="SideAction")
        multiview_btn.setToolTip(tr("menu_multiview"))
        multiview_btn.setSizePolicy(QSizePolicy.Policy.Ignored,
                                    QSizePolicy.Policy.Fixed)
        multiview_btn.clicked.connect(self._show_multiview)
        self._actions_box = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._actions_box.setContentsMargins(0, 0, 0, 0)
        self._actions_box.setSpacing(6)
        self._actions_box.addWidget(guide_btn)
        self._actions_box.addWidget(settings_btn)
        self._actions_box.addWidget(multiview_btn)
        sl.addLayout(self._actions_box)
        self._apply_action_icons()

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

        busy_row = QHBoxLayout()
        busy_row.setSpacing(8)
        self.loading_bar = QProgressBar(objectName="LoadBar")
        self.loading_bar.setRange(0, 0)
        self.loading_bar.setTextVisible(False)
        # A label beside the top strip that names what's loading, so a
        # refresh (where the list keeps its old rows and the centred overlay
        # stays hidden) still says e.g. 'Updating TV guide…'.
        self._busy_label = QLabel("")
        self._busy_label.setStyleSheet(
            f"color:{P['accent']}; font-size:11px; font-weight:600;")
        busy_row.addWidget(self.loading_bar, 1)
        busy_row.addWidget(self._busy_label)
        self._hide_busy()
        ml.addLayout(busy_row)

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
        self.grid_btn.setIcon(self._nav_icon("gridview", 14))
        self.grid_btn.setIconSize(QSize(14, 14))
        self.grid_btn.setCheckable(True)
        self.grid_btn.setChecked(
            self.settings.value("view_grid", "false") == "true")
        self.grid_btn.toggled.connect(self._inline_view_changed)
        # Compact stand-ins for the Size/Sort combos, mirroring how the grid
        # toggle shrinks to a glyph: tool buttons that open the same choices as
        # a popup menu (fully readable at any pane width, no clipped text).
        # Shown only when the pane is narrow - see _apply_mid_compact.
        self._size_menu_btn = QToolButton(objectName="InlineToggle")
        self._size_menu_btn.setIcon(
            QIcon(self._action_pixmap("sizepick", 15, P["text2"])))
        self._size_menu_btn.setIconSize(QSize(15, 15))
        self._size_menu_btn.setToolTip(tr("label_size"))
        self._size_menu_btn.setFixedWidth(30)
        self._size_menu_btn.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        self._sort_menu_btn = QToolButton(objectName="InlineToggle")
        self._sort_menu_btn.setIcon(
            QIcon(self._action_pixmap("sort", 15, P["text2"])))
        self._sort_menu_btn.setIconSize(QSize(15, 15))
        self._sort_menu_btn.setToolTip(tr("label_sort"))
        self._sort_menu_btn.setFixedWidth(30)
        self._sort_menu_btn.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        for _btn, _box in ((self._size_menu_btn, self.size_box),
                           (self._sort_menu_btn, self.sort_box)):
            _m = QMenu(_btn)
            _btn.setMenu(_m)
            _m.aboutToShow.connect(
                lambda m=_m, box=_box: self._fill_combo_menu(m, box))
        self._size_menu_btn.hide()
        self._sort_menu_btn.hide()
        self._size_label = QLabel(tr("label_size"))
        self._sort_label = QLabel(tr("label_sort"))
        # Toggle for the left category column - handy for clean screenshots
        # (hide category names) or just more room for the list.
        self.side_btn = QPushButton("", objectName="InlineToggle")
        self.side_btn.setIcon(self._nav_icon("bars", 15))
        self.side_btn.setIconSize(QSize(15, 15))
        self.side_btn.setCheckable(True)
        self.side_btn.setChecked(True)
        self.side_btn.setToolTip(tr("tooltip_toggle_sidebar"))
        self.side_btn.setFixedWidth(34)
        self.side_btn.toggled.connect(self._on_side_toggle)
        # Focus mode: hide this whole list column to give the player the room,
        # reopened via the arrow strip on the detail pane's edge.
        # Vector icon (not a text glyph): ⤢ as raw text sat off-centre,
        # clipped, and rendered differently per OS. _apply_action_icons
        # re-tints it on theme change; this initial paint covers construction
        # (which happens after the first icon pass).
        self.focus_btn = QPushButton("", objectName="InlineToggle")
        self.focus_btn.setIcon(
            QIcon(self._action_pixmap("focus", 16, P["text2"])))
        self.focus_btn.setIconSize(QSize(16, 16))
        self.focus_btn.setToolTip(tr("tooltip_hide_list"))
        self.focus_btn.setFixedWidth(34)
        self.focus_btn.clicked.connect(lambda: self._set_focus_mode(True))
        ctl.addWidget(self.side_btn)
        ctl.addWidget(self.focus_btn)
        ctl.addWidget(self._size_label)
        ctl.addWidget(self.size_box)
        ctl.addWidget(self._size_menu_btn)
        ctl.addWidget(self._sort_label)
        ctl.addWidget(self.sort_box)
        ctl.addWidget(self._sort_menu_btn)
        ctl.addStretch()
        # Folder/library switch for the Local files section - lives up here
        # with the other view controls (it sat as a fake row in the category
        # column first, which read as a folder, not a control).
        self.local_view_btn = QPushButton(objectName="InlineToggle")
        self.local_view_btn.setCheckable(True)
        self.local_view_btn.hide()
        self.local_view_btn.clicked.connect(self._local_toggle_view)
        ctl.addWidget(self.local_view_btn)
        ctl.addWidget(self.grid_btn)
        ml.addLayout(ctl)

        self.back_btn = QPushButton("<-  " + tr("btn_back_to_series"))
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
        # Catch-all: whenever the model is repopulated with rows (from any of
        # the many set_items call sites), retire the "Loading channels..." hint
        # and any busy overlay. Some paths - the startup load when the window is
        # inactive, jumping Home->TV - don't run through _apply_filter, so the
        # hint could otherwise linger under a fully populated list.
        self.list_model.modelReset.connect(self._on_list_populated)
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

        # NB: no bottom "Loading channels..." label here any more. It kept
        # lingering after whichever async load path got discarded (startup on
        # Home, playing from Home, rapid mode hops) - and the busy strip plus
        # the centred spinner overlay already say "loading" while the list is
        # empty, so the label only ever added a way to get stuck.

        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet(f"color:{P['muted3']}; font-size:11px;")
        status_row = QHBoxLayout()
        status_row.addWidget(self.count_lbl, 1)
        # Subtle "Update available" link in the status row - shown only when a
        # newer release is out, clicking opens About. Lives here (not as an
        # overlay toast) so it's quiet and always available while it lasts.
        self.update_status_btn = QPushButton("")
        self.update_status_btn.setFlat(True)
        self.update_status_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_status_btn.setStyleSheet(
            f"color:{P['accent']}; font-size:11px; font-weight:600;"
            "border:none; background:transparent; padding:0 4px;")
        self.update_status_btn.clicked.connect(self.show_about)
        self.update_status_btn.hide()
        status_row.addWidget(self.update_status_btn)
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

        # Casting takes the stream to the TV and stops local playback, which
        # leaves the player pane black with nothing anywhere saying why. This
        # strip sits directly above the video for as long as a cast runs: what
        # is playing, which device it went to, and a way to end it.
        self.cast_bar = QWidget(objectName="CastBar")
        self.cast_bar.setStyleSheet(
            f"QWidget#CastBar {{ background:{P['sel']}; border-radius:10px; }}")
        # Two rows: the controls, and under them the position across the
        # whole width. Sharing a line with the buttons and the volume left the
        # seek bar a few pixels wide, squeezed into what the wrapped labels
        # did not take.
        #
        # Both rows wrap. This strip lives in the right-hand column, which is
        # draggable and is narrow to begin with on a laptop - and a plain row
        # handed less width than its contents need does not stop at their
        # minimum, it goes on until the buttons sit on top of one another.
        self.cast_bar.setSizePolicy(QSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred))
        pol = self.cast_bar.sizePolicy()
        pol.setHeightForWidth(True)      # or the extra lines get no room
        self.cast_bar.setSizePolicy(pol)
        _cast_outer = QVBoxLayout(self.cast_bar)
        _cast_outer.setContentsMargins(12, 8, 8, 8)
        _cast_outer.setSpacing(6)
        _cast_row = FlowRow(spacing=10)
        _cast_outer.addLayout(_cast_row)
        # The labels are a block of their own so they can wrap as one, and so
        # the controls can drop below them rather than into them.
        _cast_names = QWidget()
        _cast_col = QVBoxLayout(_cast_names)
        _cast_col.setContentsMargins(0, 0, 0, 0)
        _cast_col.setSpacing(1)
        self.cast_bar_lbl = QLabel("")
        self.cast_bar_lbl.setWordWrap(True)
        self.cast_bar_lbl.setStyleSheet(
            f"color:{P['accent']}; font-size:12px; font-weight:700;")
        _cast_col.addWidget(self.cast_bar_lbl)
        self.cast_bar_title = QLabel("")
        self.cast_bar_title.setWordWrap(True)
        self.cast_bar_title.setStyleSheet(
            f"color:{P['muted3']}; font-size:11px;")
        _cast_col.addWidget(self.cast_bar_title)
        # It takes the slack, so with room to spare the strip still reads as
        # the row it was: the name on the left, the controls at the far edge.
        _cast_names.setMinimumWidth(90)
        _cast_row.add(_cast_names, grow=True)
        # Drawn, not typed. A gear, a pause bar and a minus sign are all
        # characters a font stack can be missing, and a missing glyph is an
        # empty box - which is what these buttons became.
        def strip_button(kind, tip, slot):
            b = QPushButton()
            b.setIcon(cast_strip_icon(kind, P["text"]))
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedWidth(36)
            b.clicked.connect(slot)
            _cast_row.add(b)
            return b

        self.cast_bar_mute = strip_button(
            "volume", tr("tooltip_mute_unmute"), self._toggle_cast_mute)
        # The player's own bar: a click lands where you clicked, and hovering
        # says what is under the cursor before you commit to it.
        self.cast_bar_vol = _SeekSlider()
        self.cast_bar_vol.setRange(0, 100)
        self.cast_bar_vol.setValue(50)
        # Wide enough to be draggable, narrow enough to wrap as one piece.
        self.cast_bar_vol.setFixedWidth(110)
        self.cast_bar_vol.setToolTip(tr("tooltip_volume"))
        self.cast_bar_vol.set_time_provider(lambda f: f"{round(f * 100)} %")
        # While the handle is being dragged the TV would get a message per
        # pixel; it only needs the one that says where the drag ended.
        self.cast_bar_vol.seek_requested.connect(
            lambda v: self._cast_volume(v / 100))
        self.cast_bar_vol.valueChanged.connect(
            lambda v: (self.cast_bar_vol.dragging or
                       self._cast_volume(v / 100)))
        _cast_row.add(self.cast_bar_vol)
        # The same timeshift the player has, for the same channels. A cast
        # channel with an archive can be paused, wound back and pointed at an
        # earlier programme exactly as it can here - it is the same archive,
        # asked the same way.
        self.cast_bar_ts = strip_button(
            "rewind", tr("tooltip_timeshift"), self._cast_timeshift_menu)
        self.cast_bar_tracks = strip_button(
            "tracks", tr("cast_audio") + " / " + tr("cast_subtitles"),
            self._cast_tracks_menu)
        # Pause is only shown where pausing means something. A Chromecast
        # cannot pause live television - there is nothing buffered ahead to
        # come back to - and the app answers that from the provider's archive.
        # On a channel with no archive there is no answer, and a button that
        # does nothing is worse than no button.
        self.cast_bar_pause = strip_button(
            "pause", tr("tooltip_pause_resume"), self._toggle_cast_pause)
        self.cast_bar_stop = QPushButton(tr("cast_stop"))
        self.cast_bar_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cast_bar_stop.clicked.connect(
            lambda: self._end_cast("stopped from the cast strip"))
        _cast_row.add(self.cast_bar_stop)
        # Where the film has got to, and a way to move it - its own row, the
        # full width of the strip. Only films, episodes and recordings have a
        # length to move within; a broadcast has no end to measure against,
        # and its own way back is the archive.
        _seek_row = FlowRow(spacing=10)
        _seek_row.setContentsMargins(2, 0, 2, 0)
        self.cast_bar_seek = _SeekSlider()
        self.cast_bar_seek.setRange(0, 1000)
        # Narrow enough that the strip can still be pulled in past it: below
        # this the clock and the LIVE button go to a line of their own rather
        # than climbing on top of the bar.
        self.cast_bar_seek.setMinimumWidth(120)
        self.cast_bar_seek.setToolTip(tr("cast_seek"))
        # What the point under the cursor is, before clicking it.
        self.cast_bar_seek.set_time_provider(self._cast_time_at)
        self.cast_bar_seek.seek_requested.connect(
            lambda _v: self._cast_seek_released())
        _seek_row.add(self.cast_bar_seek, grow=True)
        self.cast_bar_time = QLabel("")
        self.cast_bar_time.setStyleSheet(
            "font-size:11px; font-weight:700;")
        _seek_row.add(self.cast_bar_time)
        # The same red button the player has, and it means the same thing:
        # you are not at the live edge, and this is the way back. It is the
        # only thing on the strip that is ever red, so it reads as a state
        # rather than as decoration.
        self.cast_bar_live = QPushButton("⏭ LIVE")
        self.cast_bar_live.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cast_bar_live.setStyleSheet(
            "QPushButton{background:#FF5C5C; color:#fff; border:none;"
            "border-radius:8px; padding:2px 10px; font-size:11px;"
            "font-weight:700;}"
            "QPushButton:hover{background:#e14b4b;}")
        self.cast_bar_live.clicked.connect(self._cast_go_live)
        self.cast_bar_live.hide()
        _seek_row.add(self.cast_bar_live)
        _cast_outer.addLayout(_seek_row)

        # The receiver is the only thing that knows where the film is, and it
        # only says so when asked - so ask, once a second, and only while
        # something with a length is actually playing there.
        self._cast_tick = QTimer(self)
        self._cast_tick.setInterval(1000)
        self._cast_tick.timeout.connect(self._show_cast_progress)
        self.cast_bar.hide()
        dl.addWidget(self.cast_bar)

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
            self.player.popout_requested.connect(self._toggle_popout)
            self.player.popout_context_menu.connect(self._popout_context_menu)
            self.player.docked_context_menu.connect(self._docked_context_menu)
            self.player.stop_btn.clicked.connect(self._exit_popout_if_active)
            self.player.stopped.connect(self._on_player_stopped)
            self.player.resume_requested.connect(self._resume_last)
            self.player.stalled.connect(self._on_player_stalled)
            self.player.finished.connect(self._on_player_finished)
            self.player.next_episode.connect(self._play_next_episode)
            # Keep the poster overlay's play/pause/stop glyph in sync with the
            # player (guarded: the overlay is built later in this constructor).
            self.player.paused_changed.connect(self._on_paused_changed)
            self.player.stopped.connect(self._apply_play_icon)
            self.player.finished.connect(self._apply_play_icon)
            self.player.timeshift_seek.connect(self._on_timeshift_seek)
            self.player.program_seek.connect(self._seek_program)
            self.player.track_selected.connect(self._on_track_selected)
            # Keep the player pane visible on stop - mpv clears to black -
            # instead of hiding it, so the window just goes black.
            # Casting sits in the player's own options menu too, next to the
            # audio and subtitle tracks - the natural place to look for it
            # while watching. The player is handed a label and an action; it
            # does not need to know what a Chromecast is.
            self.player.extra_options = [
                (lambda: tr("ctx_cast_to_chromecast"), self.cast_playing)]
            # The player draws the visualiser and applies the equaliser; the
            # window owns the settings behind both.
            self.player.open_equaliser = self.open_equaliser
            self.player.vis_state = lambda: (
                self.settings.value("vis_on", "true") == "true",
                self.settings.value("vis_style", "bars"))
            self.player.vis_choose = self._vis_choose
            dl.addWidget(self.player, 1)

        # Everything below the video lives in ONE scroll column. This is the
        # structural guarantee that the channel logo / programme info can never
        # end up over the video: the player keeps its fixed height at the top
        # of the pane, and when the window is small this container simply
        # shrinks and scrolls. (Transparent backgrounds are scoped with ID
        # selectors - a bare 'background: transparent' cascades onto child
        # widgets; see the #SideContent note.)
        self._info_scroll = QScrollArea(objectName="InfoScroll")
        self._info_scroll.setWidgetResizable(True)
        self._info_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._info_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._info_scroll.setStyleSheet(
            "QScrollArea#InfoScroll { background: transparent; border: 0; }")
        self._info_scroll.viewport().setObjectName("InfoViewport")
        self._info_scroll.viewport().setStyleSheet(
            "QWidget#InfoViewport { background: transparent; }")
        _info_content = QWidget(objectName="InfoContent")
        _info_content.setStyleSheet(
            "QWidget#InfoContent { background: transparent; }")
        self._info_scroll.setWidget(_info_content)
        il = QVBoxLayout(_info_content)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(12)
        # A small floor keeps at least a strip of info visible; beyond that the
        # column scrolls rather than anything overlapping.
        self._info_scroll.setMinimumHeight(96)
        dl.addWidget(self._info_scroll, 1)

        self.stream_error = QLabel("")
        self.stream_error.setStyleSheet(
            f"color:{P['error']}; font-size:12px;")
        self.stream_error.setWordWrap(True)
        self.stream_error.hide()
        il.addWidget(self.stream_error)

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

        # Icon-only play button, laid over the poster/logo itself (a child of
        # d_logo, centred on it) rather than sitting below it - the familiar
        # "play overlay on the artwork" pattern. The triangle is drawn as an
        # icon (perfectly centred, unlike the off-centre ▶ text glyph) and
        # follows the theme accent; _position_play_over_poster keeps it centred
        # when the poster size changes.
        self.play_mpv = QPushButton(self.d_logo, objectName="PlayGhost")
        self.play_mpv.setToolTip(tr("tooltip_play_in_mpv"))
        self.play_mpv.setFixedSize(30, 30)
        self.play_mpv.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_play_icon(30)
        self.play_mpv.clicked.connect(self._play_overlay_clicked)
        # Start hidden: the poster and its play overlay only appear once a
        # channel/movie/series is selected (see _show_detail), so an empty
        # detail pane has no stray box or button.
        self.d_logo.hide()
        self.play_mpv.hide()
        left_col.addStretch(1)
        header_row.addLayout(left_col)

        # "Now playing" sits beside the logo instead of stacked below it -
        # the channel name is already visible in the middle list, the
        # window title bar, and the mini player's own control bar.
        self.now_card = QFrame(objectName="NowCard")
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
        # Right-click the "now" card to record/remind the current programme.
        self.now_card.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.now_card.customContextMenuRequested.connect(
            lambda pos: self._current_epg and self._epg_programme_menu(
                self._current_epg, self.now_card.mapToGlobal(pos)))
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
        il.addLayout(header_row)

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
        il.addWidget(self.cast_scroll)

        self.epg_scroll = QScrollArea()
        self.epg_scroll.setWidgetResizable(True)
        self.epg_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.epg_holder = QWidget(objectName="EpgHolder")
        self.epg_lay = QVBoxLayout(self.epg_holder)
        self.epg_lay.setContentsMargins(0, 0, 0, 0)
        self.epg_lay.setSpacing(8)
        self.epg_lay.addStretch()
        self.epg_scroll.setWidget(self.epg_holder)
        # Keep a useful strip of the programme list: when the pane is shorter
        # than everything needs, the OUTER info column starts scrolling instead
        # of the EPG being squashed to nothing.
        self.epg_scroll.setMinimumHeight(140)
        il.addWidget(self.epg_scroll, 1)

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
        # The middle pane's width also changes with divider drags, not just
        # window resizes, so keep its control strip's compact mode in sync.
        root.splitterMoved.connect(self._update_mid_compact)
        # The "+ Add provider" hint is absolutely positioned over the middle
        # pane, so it has to follow divider drags too - otherwise it drifts off
        # centre (or off-pane) whenever the columns are resized.
        root.splitterMoved.connect(self._position_provider_hint)
        # When collapsed the rail's width is pinned (so it can't stretch), which
        # freezes its divider handle - so watch the handle for a rightward drag
        # to re-expand it without reaching for the ☰ button.
        self._side_handle = root.handle(1)
        if self._side_handle is not None:
            self._side_handle.installEventFilter(self)
        # Floor wide enough that the docked player's full control row (transport
        # + options + pop-out + fullscreen + mute + volume) always fits, so
        # dragging the divider in never crushes the buttons or drops the slider.
        # The pane's 20px side margins mean the player is 40px narrower than the
        # pane, so the floor is the ~340px bar plus those margins.
        det.setMinimumWidth(380)
        # Keep the content list from being squeezed away: dragging the sidebar
        # divider far right used to swallow the whole middle column (leaving
        # sidebar + player and no list, which just looks broken). A floor plus
        # non-collapsible keeps it always present.
        mid.setMinimumWidth(240)
        self._side, self._mid, self._det = side, mid, det
        self._root = root
        # A base minimum window size: below this the three columns can't hold
        # the docked player plus its info without overlapping (the info would
        # creep up into the video). Width = rail + middle + detail floors;
        # height fits the docked player (which scales with screen width) plus
        # its control bar, the pane chrome and a little info.
        # Height fits the docked player (fixed box + control bar) plus the
        # info column's small floor and chrome. Overlap is impossible at ANY
        # size - everything under the video lives in the info scroll column -
        # so this floor is about usability, not correctness.
        # player(box_h+~52) + pane margins(40) + spacing(12) + info floor(96)
        # + menu bar/chrome ≈ box_h + 240.
        box_h = getattr(self.player, "VIDEO_BOX_HEIGHT", 260) if self.player else 0
        min_h = (box_h + 240) if self.player else 320
        self._base_min = QSize(700, min_h)
        self.setMinimumSize(self._base_min)
        # Parent the toast to the window itself, not the splitter: a QSplitter
        # treats every child widget as a pane and overrides its geometry, so an
        # overlay parented to it gets squeezed/misplaced when the splitter
        # relayouts (e.g. after the playlist loads). As a free child of the
        # main window it floats correctly over the content.
        self._toast = _Toast(self)

        self.tick = QTimer(self)
        self.tick.timeout.connect(self._refresh_progress)
        self.tick.start(60_000)

        # Keep the timeshift live-timeline marker current (no-op unless a
        # timeshift channel is on screen).
        self._ts_segment_start = None
        self._ts_timeline_timer = QTimer(self)
        self._ts_timeline_timer.timeout.connect(self._update_ts_timeline)
        self._ts_timeline_timer.start(1000)

        # Poll EPG reminders so one fires close to its programme's start.
        self._reminder_timer = QTimer(self)
        self._reminder_timer.timeout.connect(self._check_reminders)
        self._reminder_timer.start(30_000)

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
        # macOS: mini-player "maximize" goes fullscreen through the mirror
        # pop-out (a frameless window covers the screen instantly, no native
        # fullscreen Space animation - the reason the pop-out felt smoother).
        self._fs_via_popout = False
        self._fs_exiting = False

        # Escape and Delete are structural/context-sensitive, so they stay
        # fixed; everything else is user-rebindable (see _install_shortcuts).
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self,
                  activated=self._on_escape)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self,
                  activated=self._delete_pressed)
        self._install_shortcuts()

        # Channel-number quick-jump state (digits typed in the list).
        self._prev_live_item = None
        self._chan_buffer = ""
        self._chan_timer = QTimer(self)
        self._chan_timer.setSingleShot(True)
        self._chan_timer.timeout.connect(self._channel_jump)

        self._apply_view_settings()

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

    def _overlay_state(self) -> str:
        """What the poster overlay should do right now:
          'play'  - nothing (or a different item) is playing -> start this one
          'pause' - this item is playing and can be paused (VOD/series/recording
                    or a timeshift channel) -> pause
          'resume'- this item is playing but paused -> resume
          'stop'  - this item is a plain live channel (no timeshift), which
                    can't meaningfully pause -> stop instead."""
        p = self.player
        # str() both keys: the same channel can carry an int stream_id from the
        # provider list but a string one from a JSON-roundtripped source (Home
        # cache, favourites, history), and a type mismatch here left the poster
        # button on 'play' while that channel's catch-up was in fact playing.
        playing_this = (p is not None and p.current_url is not None
                        and self._playing_key is not None
                        and self._current_key is not None
                        and str(self._current_key) == str(self._playing_key))
        if not playing_this:
            return "play"
        # Base the pause/stop decision on what's PLAYING, not the row that
        # happens to be selected - a timeshift channel (or a catch-up segment)
        # can pause/seek, so its button must read "pause", not "stop". The
        # selected row often isn't the playing one, which made it show stop.
        lp = getattr(self, "_last_playback", None)
        play_it = (lp or {}).get("item") or self.list_model.item_at(
            self.listw.currentIndex().row())
        try:
            timeshift = bool(play_it) and self._timeshift_days(play_it) > 0
        except Exception:
            timeshift = False
        pausable = (self._playing_group in ("vod", "episode", "rec")
                    or getattr(self, "_playing_catchup", False)
                    or (self._playing_group == "live" and timeshift))
        if not pausable:
            return "stop"
        return "resume" if getattr(p, "_paused", False) else "pause"

    def _apply_play_icon(self, size: int | None = None) -> None:
        """(Re)draw the poster overlay to match the current playback state
        (play / pause / stop). Called on build, selection change, and whenever
        playback or pause state changes."""
        if not hasattr(self, "play_mpv"):
            return
        size = int(size or self.play_mpv.width() or 28)
        glyph = {"pause": "pause", "stop": "stop"}.get(
            self._overlay_state(), "play")
        self.play_mpv.setIcon(self._overlay_glyph(size, glyph))
        self.play_mpv.setIconSize(QSize(size, size))

    def _on_paused_changed(self, paused: bool) -> None:
        self._apply_play_icon()
        lp = getattr(self, "_last_playback", None)
        on_live = bool(lp) and lp.get("kind") == "live"
        if paused:
            self._pause_started = time.time()
            # Pausing a live stream means you're no longer at the live edge:
            # show the badge and flip the timeline to 'not live' immediately
            # (don't wait for the 1 s timer or the offset to accrue). Only for
            # channels that actually HAVE a provider archive - a plain live
            # channel pauses on mpv's local buffer, and stamping it TIMESHIFT
            # wrongly advertised an archive that doesn't exist.
            it = (lp or {}).get("item")
            try:
                has_archive = it is not None and self._timeshift_days(it) > 0
            except Exception:
                has_archive = False
            if (self.player and on_live and not self._playing_catchup
                    and has_archive):
                self.player.set_live_badge("timeshift")
                self._update_ts_timeline()
            return
        # Resumed. DVR-style pause for timeshift channels: if we paused the live
        # edge for longer than the buffer can hold, re-open the provider archive
        # from the moment we paused, instead of stalling on an exhausted buffer.
        started = getattr(self, "_pause_started", None)
        self._pause_started = None
        if started is None or getattr(self, "_playing_catchup", False):
            return   # not paused by us, or already playing a seekable archive
        it = lp.get("item") if lp else None
        elapsed = time.time() - started
        if log.isEnabledFor(logging.DEBUG):
            tv = self._timeshift_days(it) if it else 0
            log.debug("[ts] resume elapsed=%.1f on_live=%s ts_days=%s "
                      "catchup=%s tl_visible=%s", elapsed, on_live, tv,
                      self._playing_catchup,
                      self.player.ts_timeline.isVisible()
                      if self.player else None)
        if (it and lp.get("kind") == "live"
                and self._timeshift_days(it) > 0 and elapsed >= 120):
            # Only a *long* pause (beyond what mpv's buffer holds) falls to the
            # archive. Shorter pauses resume seamlessly from the buffer, which
            # is the real pause - re-opening a tiny archive segment for them
            # just produced a stuttery 1-minute clip. Include any offset already
            # accrued from earlier short pauses so we land at the right spot.
            total = getattr(self, "_ts_live_offset", 0.0) + elapsed
            self._play_timeshift(it, back_min=total / 60.0)
        elif (it and lp.get("kind") == "live"
              and self._timeshift_days(it) > 0 and elapsed >= 2):
            # Short pause on a timeshift channel: mpv resumes from its buffer,
            # so you're now `elapsed` behind the live edge. Track that gap and
            # keep the 'not live' badge + timeline offset, instead of pretending
            # you're live again.
            self._ts_live_offset = getattr(self, "_ts_live_offset", 0.0) + elapsed
            if self.player:
                self.player.set_live_badge("timeshift")
            self._update_ts_timeline()
        elif self.player and on_live:
            # Buffer resume at ~the live edge: drop the 'not live' badge.
            self.player.set_live_badge(None)

    def _on_timeshift_seek(self, minutes_back: int) -> None:
        """Scrub the live timeline: jump to that point in the provider archive
        (or back to the live edge when dropped at the right)."""
        lp = getattr(self, "_last_playback", None)
        it = lp.get("item") if lp else None
        if not it:
            return
        if minutes_back < 1:
            self.play_live_channel(it)
        else:
            self._play_timeshift(it, back_min=minutes_back)

    def _seek_program(self, disp_secs: int) -> None:
        """Scrub the picked-programme bar: re-load the archive so it starts at
        *disp_secs* into the programme. The archive stream can't be seeked in
        place (it snaps to live), so each scrub re-opens it at the new offset,
        keeping the bar spanning the whole programme (prog_origin)."""
        lp = getattr(self, "_last_playback", None)
        it = lp.get("item") if lp else None
        origin = getattr(self, "_ts_program_start", None)
        stop = getattr(self, "_ts_program_stop", None)
        if not (it and origin and stop and stop > origin):
            return
        disp_secs = max(0, int(disp_secs))
        new_start = origin + disp_secs
        # Don't scrub past the very end of the programme (leave a few seconds so
        # there's something to play).
        new_start = min(new_start, int(stop) - 5)
        if new_start < origin:
            new_start = origin
        prog = {"start_timestamp": new_start, "stop_timestamp": int(stop),
                "title": getattr(self, "_ts_program_title", None) or ""}
        self._play_timeshift(it, prog=prog, prog_origin=origin)

    def _update_ts_timeline(self) -> None:
        if not (self.player and self.player.ts_timeline.isVisible()):
            return
        lp = getattr(self, "_last_playback", None)
        if not lp or lp.get("kind") != "live":
            return
        item = lp.get("item")
        now = time.time()
        if self._playing_catchup and self._ts_segment_start is not None:
            content_time = self._ts_segment_start + self.player.playback_position()
            offset = max(0.0, (now - content_time) / 60.0)
        else:
            # Live edge, but a buffer pause may have left us behind live.
            behind = getattr(self, "_ts_live_offset", 0.0)
            # If we're paused *right now*, the gap is already growing - reflect
            # it immediately (Go-live button + "−Ns" label) instead of waiting
            # for resume to bake it into _ts_live_offset.
            started = getattr(self, "_pause_started", None)
            if started is not None:
                behind += max(0.0, now - started)
            content_time = now - behind
            offset = behind / 60.0
        # Programme-boundary ticks on the timeline, plus the name of the
        # programme at the cursor so the user can see where in the schedule
        # they are (rather than a bare "-1:30").
        depth_min = getattr(self, "_ts_depth_min", 0)
        title = None
        if item is not None and depth_min:
            win_start = now - depth_min * 60
            span = depth_min * 60
            progs = self.xmltv.programmes_in(item, win_start, now)
            segs = []
            for p in progs:
                a = max(0.0, (p["start_timestamp"] - win_start) / span)
                b = min(1.0, (p["stop_timestamp"] - win_start) / span)
                tlabel = "%s–%s" % (
                    time.strftime("%H:%M", time.localtime(p["start_timestamp"])),
                    time.strftime("%H:%M", time.localtime(p["stop_timestamp"])))
                segs.append((a, b, p.get("title") or "", tlabel))
                if p["start_timestamp"] <= content_time < p["stop_timestamp"]:
                    title = p.get("title")
            self.player.set_timeline_segments(segs)
        # A live-edge pause is definitively 'not live' the instant it happens,
        # so flip the label + Go-live button immediately rather than waiting for
        # the offset to creep past the ~5 s live tolerance.
        paused = (getattr(self, "_pause_started", None) is not None
                  and not self._playing_catchup)
        self.player.update_timeshift_position(offset, title, paused=paused)

    def _play_overlay_clicked(self) -> None:
        # WINDOWS ONLY: swallow a click that arrives in the same breath as a
        # pop-out toggle. Reparenting the control bar mid-click makes Windows
        # deliver the release to whatever is under the pointer - this button,
        # whose action on a plain live channel is stop, which killed the stream
        # the moment you popped out. The stamp is only ever set on win32 (see
        # _toggle_popout), so this is inert on macOS and Linux.
        since = time.monotonic() - getattr(self, "_popout_toggled_at", 0.0)
        if getattr(self, "_popout_toggled_at", 0.0) and since < 0.4:
            return
        state = self._overlay_state()
        if state == "play":
            self.play("mpv")
        elif state == "stop":
            if self.player:
                self.player.stop()
        else:                       # pause / resume
            if self.player:
                self.player.toggle_pause()
        self._apply_play_icon()

    @staticmethod
    def _overlay_glyph(size: int, kind: str) -> "QIcon":
        """A white play / pause / stop glyph on a soft dark disc, so it stays
        legible over any artwork - including the many white channel logos it
        used to vanish into."""
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QPolygonF
        scale = 3
        S = max(1, int(size)) * scale
        pm = QPixmap(S, S)
        pm.fill(Qt.GlobalColor.transparent)
        pt = QPainter(pm)
        pt.setRenderHint(QPainter.RenderHint.Antialiasing)
        pt.setPen(Qt.PenStyle.NoPen)

        # Soft dark disc for contrast (no outline/ring, just a scrim).
        pt.setBrush(QColor(0, 0, 0, 165))
        pt.drawEllipse(QRectF(S * 0.03, S * 0.03, S * 0.94, S * 0.94))

        pt.setBrush(QColor("white"))
        if kind == "pause":
            bw, bh, gap = S * 0.11, S * 0.34, S * 0.10
            y = (S - bh) / 2
            for x in (S * 0.5 - gap / 2 - bw, S * 0.5 + gap / 2):
                pt.drawRoundedRect(QRectF(x, y, bw, bh), bw * 0.35, bw * 0.35)
        elif kind == "stop":
            s = S * 0.32
            pt.drawRoundedRect(QRectF((S - s) / 2, (S - s) / 2, s, s),
                               S * 0.05, S * 0.05)
        else:                        # play triangle (optically nudged right)
            cx, cy, w, h = S * 0.54, S * 0.5, S * 0.30, S * 0.36
            pt.drawPolygon(QPolygonF([
                QPointF(cx - w * 0.5, cy - h * 0.5),
                QPointF(cx - w * 0.5, cy + h * 0.5),
                QPointF(cx + w * 0.5, cy)]))
        pt.end()
        return QIcon(pm)

    def _set_det_fs_style(self, on: bool) -> None:
        """Flip the detail pane between its normal chrome and the pure-black
        fullscreen backdrop via a dynamic property (rule in theme.py). This
        repolishes ONE widget; the setStyleSheet() call it replaces forced a
        style recompute of the pane's entire subtree on every fullscreen
        toggle, which grew slower with every widget the pane gained."""
        self._det.setProperty("videofs", on)
        st = self._det.style()
        st.unpolish(self._det)
        st.polish(self._det)
        self._det.update()

    def _toggle_player_fullscreen(self) -> None:
        if not self.player or not self.player.isVisible():
            return
        now = time.time()
        if now - getattr(self, "_fs_toggled_at", 0.0) < 0.4:
            return
        self._fs_toggled_at = now
        if self._popout_win is not None:
            # Already detached. If we opened that pop-out purely to go
            # fullscreen (macOS mini maximize), toggling off docks back;
            # otherwise it's a real pop-out and we just leave its fullscreen.
            if self._fs_via_popout:
                self._exit_player_fullscreen()
            else:
                self._toggle_popout_fullscreen()
            return
        if self._player_fs:
            self._exit_player_fullscreen()
            return
        # macOS: reuse the smooth mirror pop-out for maximize - a frameless
        # window covers the screen at once, skipping the slow native
        # fullscreen transition the decorated main window would trigger.
        if sys.platform == "darwin":
            self._fs_via_popout = True
            self._popout_macos()
            self._toggle_popout_fullscreen()
            return
        self._player_fs = True
        self._fs_return_index = self.listw.currentIndex()
        self._fs_return_scroll = self.listw.verticalScrollBar().value()
        # Remember the panel widths: hiding the side/middle panes lets the
        # detail pane take the whole window, and without this the splitter
        # wouldn't get its proportions back on the way out of fullscreen.
        self._fs_splitter_sizes = self._root.sizes()
        # Suspend painting for the whole transition so the intermediate states
        # (panes vanishing one by one, the pane reflow) aren't drawn - the user
        # sees one clean cut to fullscreen instead of it "building" step by
        # step. This only pauses painting, never mpv, so playback is untouched;
        # re-enabled in finally so it can never get stuck off.
        cw = self.centralWidget()
        if cw is not None:
            cw.setUpdatesEnabled(False)
        try:
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
            self._set_det_fs_style(True)
            self.menuBar().hide()
            self.player.set_fullscreen_ui(True)
            self._update_provider_hint()   # tuck the '+ Add provider' hint away
            self._was_fullscreen = self.isFullScreen()
            # Remember if the window was maximized/zoomed and its exact
            # geometry, so leaving video-fullscreen restores that state instead
            # of shrinking to the pre-maximize size. showNormal() alone drops
            # the maximized state (reported on macOS: a maximized window shrinks
            # after video fullscreen).
            self._was_maximized = self.isMaximized()
            if not self._was_fullscreen:
                self._pre_fs_geo = self.geometry()
            # Go fullscreen INSIDE the paint-suspend: when updates re-enable the
            # window is already fullscreen-sized, so it's one clean cut. Left
            # outside, the re-enabled paint landed first and showed the video
            # filling the still-normal-sized window for a frame - the "half,
            # then max" flash on macOS.
            self.showFullScreen()
        finally:
            if cw is not None:
                cw.setUpdatesEnabled(True)

    def _on_escape(self) -> None:
        """Single Escape handler so the key is never ambiguous: dismiss the
        onboarding wizard if it's up, otherwise leave fullscreen."""
        if self._welcome is not None and self._welcome.isVisible():
            self._welcome.dismiss()
            return
        self._exit_player_fullscreen()

    def _exit_player_fullscreen(self) -> None:
        # When the player is detached, its fullscreen belongs to the pop-out
        # window - route the exit there instead of the main window.
        if self._popout_win is not None:
            if self._fs_via_popout:
                # macOS mini maximize: leaving fullscreen docks straight back
                # to the mini player (never leaves a floating pop-out behind).
                self._fs_via_popout = False
                self._exit_popout()
            elif self._popout_win.isFullScreen():
                self._toggle_popout_fullscreen()
            return
        if not self._player_fs:
            if self.isFullScreen():
                self.showNormal()
            return
        self._player_fs = False
        # Mute the narrow-width auto-collapse for the whole exit transition:
        # the window resizing from fullscreen width back to normal looks like
        # a "just got narrow" edge to _maybe_auto_collapse_sidebar, which then
        # re-collapsed a sidebar the user had deliberately expanded ("after
        # fullscreen it's always icons"). Cleared - and the edge baseline
        # resynced - once the restored geometry has settled (_end_fs_exit).
        self._fs_exiting = True
        # Batch the synchronous reflow (panes reappearing, style + window state
        # restore) into one paint instead of a visible multi-step rebuild -
        # painting only, mpv untouched, re-enabled in finally so it can never
        # stay off. The deferred size restore below repaints once more as the
        # final settle; suspending across that async gap is deliberately
        # avoided so a stuck update state can never freeze the video.
        cw = self.centralWidget()
        if cw is not None:
            cw.setUpdatesEnabled(False)
        try:
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
            self._set_det_fs_style(False)
            self.menuBar().show()
            if not getattr(self, "_was_fullscreen", False):
                if getattr(self, "_was_maximized", False):
                    # Re-zoom to the maximized state it had before fullscreen.
                    self.showMaximized()
                else:
                    self.showNormal()
                    # Deterministically restore the exact windowed
                    # size/position - a manually-enlarged window otherwise
                    # comes back smaller.
                    geo = getattr(self, "_pre_fs_geo", None)
                    if geo is not None:
                        self.setGeometry(geo)
            # Restore the window geometry *before* unlocking the video's
            # fixed height - _lock_video_box() reads the player's current
            # size, and computing it while the window is still fullscreen-
            # sized bakes in a wrong height that a later resize doesn't
            # reliably clear (the same class of bug as the PiP letterboxing).
            self.player.set_fullscreen_ui(False)
        finally:
            if cw is not None:
                cw.setUpdatesEnabled(True)
        # Put the panel widths back (deferred so it lands after the window has
        # returned to its normal geometry, otherwise the still-fullscreen-sized
        # window bakes in the wrong proportions).
        saved = getattr(self, "_fs_splitter_sizes", None)
        if saved:
            QTimer.singleShot(0, lambda s=saved: self._root.setSizes(s))
        # After the deferred size restore has landed: stop muting the
        # auto-collapse and resync its edge baseline to the restored
        # geometry WITHOUT acting on it, so the user's rail/expanded choice
        # survives fullscreen and the next genuine resize still auto-adapts.
        QTimer.singleShot(0, self._end_fs_exit)
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

    def refresh_playlist(self, force: bool = True) -> None:
        """Reload the active playlist. With *force* (the Refresh button / auto-
        refresh timer) the guide is re-fetched from the network; with
        force=False (a playlist switch) the channel list is rebuilt but the
        guide loads from cache when it's still fresh - no forced network reload,
        so switching stays snappy and the auto-refresh time setting still
        governs when a real re-fetch happens."""
        if force:
            self._last_playlist_refresh = time.time()
            # A manual refresh must be a real re-fetch: drop the client's
            # short-TTL list cache so categories/channels come from the
            # provider, not from memory.
            clear = getattr(self.client, "clear_list_cache", None)
            if clear is not None:
                clear()
        self._clear_ts_broken()   # re-trust the provider's tv_archive flags
        pl = self.playlist_store.active() if self.playlist_store else None
        pid = (pl or {}).get("id")
        self.xmltv = XmltvGuide(
            self.client, (pl or {}).get("epg_url") or None,
            cache_path=epg_cache_path(pid) if pid else None,
            progress_cb=self.epg_progress.emit)
        self._info_cache.clear()
        # Name the wait after what the user did (refresh the playlist), not the
        # guide reload that happens to be the slow part of it. Shown in the top
        # loading strip only (the bottom line stays the channel count).
        self._busy_epg_msg = tr("status_refreshing_playlist")
        self._show_busy(tr("status_refreshing_playlist"))
        self._load_categories()
        # New/refreshed provider: re-learn which Browse modes it has and hide
        # the empty ones (Browse nav is shown by default until this resolves).
        self._refresh_mode_availability()
        run_async(
            self.pool, lambda: self.xmltv.ensure_loaded(force=force),
            lambda ok: (self._epg_progress_finished(),
                        self.list_model.refresh_all() if ok else None),
            lambda _: self._epg_progress_finished())

    def _refresh_epg_now(self) -> None:
        """Force a fresh EPG fetch now (Settings button) without reloading the
        channel list."""
        self._flash_status(tr("status_loading_programme_guide"))
        run_async(
            self.pool, lambda: self.xmltv.ensure_loaded(force=True),
            lambda ok: (self._epg_progress_finished(),
                        self.list_model.refresh_all() if ok else None),
            lambda _: self._epg_progress_finished())

    def _clear_epg_cache(self) -> None:
        """Delete the cached guide for the current playlist, sweep away caches
        left by playlists that no longer exist, then re-fetch fresh."""
        self.xmltv.clear_cache()
        try:
            prune_epg_caches(p.get("id") for p in self.playlist_store.items) \
                if self.playlist_store else None
        except Exception:
            pass
        self._flash_status(tr("epg_cache_cleared"))
        self._refresh_epg_now()

    def start_demo(self) -> None:
        """Switch to the built-in demo provider (a few free public test
        streams) so the app can be tried without any credentials. Reuses the
        normal live path - the demo client just answers with a fixed channel
        list."""
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
        self._show_busy(tr("status_connecting", name=pl['name']))
        candidate = make_client(pl)

        def done(_auth):
            self._hide_busy()
            self.playlist_store.set_active(pid)
            self.client = candidate
            # Another provider's category ids say nothing about this one's.
            self._last_cat = {}
            self.favs = FavoriteStore(self.settings, f"favorites_{pid}")
            self.movie_favs = FavoriteStore(
                self.settings, f"movie_favorites_{pid}", id_key="stream_id")
            self.series_favs = FavoriteStore(
                self.settings, f"series_favorites_{pid}", id_key="series_id")
            self.history = HistoryStore(self.settings, f"history_{pid}")
            self.resume = ResumeStore(self._resume_settings, pid)
            self.reminders = ReminderStore(self.settings, pid)
            self._base_title = pl["name"]
            self.setWindowTitle(self._base_title)
            self._update_playlist_btn()
            self._update_provider_hint()
            # Drop any Home media cached from the previous (or empty offline)
            # provider so the new provider's Recently-added / Featured shelves
            # actually populate - otherwise the empty result cached while the
            # welcome overlay was up would linger for MEDIA_CACHE_SECS and Home
            # would look blank right after the first playlist is added.
            self._home_poster_cache = None
            self._home_chan_cache = None
            self._recent_cache = {}
            if self._home_showing():
                self._home_page.refresh()
            # Switching rebuilds the list but loads the guide from cache when
            # fresh - no forced network reload. A manual Refresh or the auto-
            # refresh time setting still drives a real re-fetch.
            self.refresh_playlist(force=False)

        def fail(msg):
            self._hide_busy()
            self._set_status("")
            QMessageBox.warning(
                self, tr("playlist_msg_title"),
                tr("msg_could_not_connect", name=pl['name'], msg=msg))

        run_async(self.pool, candidate.authenticate, done, fail)

    # -- modes and categories ------------------------------------------------------

    def switch_mode(self, mode: str) -> None:
        # Any mode switch drops back from the Home page to the classic view.
        if getattr(self, "_center_stack", None) is not None:
            self._center_stack.setCurrentIndex(0)
        for k, b in self.nav_btns.items():
            b.setChecked(k == mode)
        self.mode = mode
        self.series_ctx = None
        self.back_btn.hide()
        self.clear_history_btn.setVisible(mode == "history")
        if hasattr(self, "local_view_btn"):
            self.local_view_btn.setVisible(mode == "local")
            self._sync_local_view_btn()
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

    def _refresh_mode_availability(self) -> None:
        """Hide a Browse mode (TV/Movies/Series) the active provider has no
        content for, so a live-only (or VOD-only) provider doesn't show empty
        sections. TV/Movies/Series are the Xtream API's three fixed types, so we
        probe each type's category list in the background; an empty list hides
        that mode. Fail-open: a probe that errors (or an all-empty result) leaves
        every mode shown, so a transient hiccup or an uncategorised provider
        never blanks a working section. Purely nav visibility - no effect on
        playback or the mpv/VLC player."""
        if not self.client:
            return
        self._avail_gen += 1
        gen = self._avail_gen
        self._mode_avail = {}
        # Start from all-visible so a mode hidden for the previous provider
        # doesn't linger while the new provider's probes are in flight.
        for m in ("live", "vod", "series"):
            if m in self.nav_btns:
                self.nav_btns[m].setVisible(True)
        fns = {"live": getattr(self.client, "live_categories", None),
               "vod": getattr(self.client, "vod_categories", None),
               "series": getattr(self.client, "series_categories", None)}
        for key, fn in fns.items():
            if fn is None:
                continue   # client doesn't offer this type -> leave it shown
            def done(cats, key=key, gen=gen):
                if gen != self._avail_gen:
                    return   # a newer provider/refresh superseded this probe
                self._mode_avail[key] = bool(cats)
                self._apply_mode_visibility()
            run_async(self.pool, fn, done, lambda _e: None)

    def _apply_mode_visibility(self) -> None:
        modes = ("live", "vod", "series")
        known = {m: self._mode_avail.get(m) for m in modes}
        # Fail open until we know at least one mode has content, so an all-empty
        # result (e.g. an auth glitch) never hides the whole Browse group.
        if not any(v is True for v in known.values()):
            for m in modes:
                if m in self.nav_btns:
                    self.nav_btns[m].setVisible(True)
            return
        for m in modes:
            if m in self.nav_btns:
                # Hide only on a definite empty (False); unknown (None) stays.
                self.nav_btns[m].setVisible(known[m] is not False)
        # If the mode you're on just got hidden, move to the first visible one.
        if self.mode in modes and known[self.mode] is False:
            for m in modes:
                if known[m] is not False:
                    self.switch_mode(m)
                    break

    def _remembered_cat_row(self, default_row: int = 0) -> int:
        """The row holding the sub-category last visited in this section, or
        *default_row* when there is none (first visit) or it is gone (the
        provider dropped the category, a favourites group was renamed, a
        recordings folder was deleted). Ids come back from the provider as
        int or str interchangeably, so compare as strings as a fallback."""
        want = self._last_cat.get(self.mode, _UNSET)
        if want is _UNSET:
            return default_row
        for i in range(self.cat_list.count()):
            d = self.cat_list.item(i).data(Qt.ItemDataRole.UserRole)
            if d == want or (d is not None and want is not None
                             and not isinstance(d, tuple)
                             and str(d) == str(want)):
                return i
        return default_row

    def _select_remembered_cat(self, default_row: int = 0) -> None:
        self.cat_list.setCurrentRow(self._remembered_cat_row(default_row))

    def _load_categories(self) -> None:
        self._load_gen += 1
        gen = self._load_gen
        # Reset the category search: it only applies to the provider sections
        # that actually have categories (live/movies/series). Collapse the box
        # back to just its 🔍 toggle on every section switch.
        if hasattr(self, "cat_search"):
            cat_mode = self.mode in ("live", "vod", "series")
            # Category search where categories exist; a plain list filter in the
            # folder/list sections (Favorites, Watch Later, Watched, Recordings,
            # History) so the same 🔍 works everywhere, adapted to each.
            show = cat_mode or self.mode in (
                "fav", "watchlist", "watched", "rec", "history", "local")
            self._cat_search_supported = show
            self.cat_search.setPlaceholderText(
                tr("cat_search_placeholder") if cat_mode
                else tr("cat_search_items"))
            self.cat_search.blockSignals(True)
            self.cat_search.clear()
            self.cat_search.blockSignals(False)
            self.cat_search.hide()
            self.cat_search_btn.blockSignals(True)
            self.cat_search_btn.setChecked(False)
            self.cat_search_btn.blockSignals(False)
            # Not on the collapsed icon rail (see _apply_sidebar_chrome).
            self.cat_search_btn.setVisible(
                show and not getattr(self, "_sidebar_collapsed", False))
        self.cat_list.clear()
        self.list_model.set_items([], self.mode)
        if self.mode == "local":
            self._load_local_categories()
            return
        if self.mode == "rec":
            self.cat_list.blockSignals(True)
            for label, data in [(tr("rec_all_recordings"), None),
                                (tr("rec_active_scheduled"), "__jobs__"),
                                (tr("rec_upcoming"), "__upcoming__")]:
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, data)
                self.cat_list.addItem(item)
            for rel in self.rec.folders():
                item = QListWidgetItem(rel)
                item.setData(Qt.ItemDataRole.UserRole, rel)
                self.cat_list.addItem(item)
            self.cat_list.blockSignals(False)
            self._select_remembered_cat()
            return
        if self.mode == "history":
            self.cat_list.blockSignals(True)
            for label, data in [(tr("cat_all"), None),
                                (tr("fav_channels"), "live"),
                                (tr("nav_movies"), "movie"),
                                (tr("nav_series"), "series"),
                                (tr("nav_local"), "local")]:
                it = QListWidgetItem(label)
                it.setData(Qt.ItemDataRole.UserRole, data)
                self.cat_list.addItem(it)
            self.cat_list.blockSignals(False)
            self._select_remembered_cat()
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
            self._select_remembered_cat()
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
            self._select_remembered_cat()
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
            self._select_remembered_cat()
            return
        self._show_busy(tr("status_loading_categories"))
        fn = {"live": self.client.live_categories,
              "vod": self.client.vod_categories,
              "series": self.client.series_categories}[self.mode]
        request_mode = self.mode

        def done(cats):
            if gen != self._load_gen or self.mode != request_mode:
                return
            self._hide_busy()
            self._raw_categories = cats or []
            self._search_index_cache.pop(request_mode, None)  # rebuild on search
            self.cat_list.blockSignals(True)
            self.cat_list.clear()
            all_item = QListWidgetItem(tr("cat_all"))
            all_item.setData(Qt.ItemDataRole.UserRole, None)
            self.cat_list.addItem(all_item)
            # A synthetic "Continue watching" category, shown when there are
            # partly-watched titles of this section's kind (movies under
            # Movies, episodes under Series).
            cont_kind = {"vod": "vod", "series": "episode"}.get(self.mode)
            if cont_kind and self._continue_items(cont_kind):
                cw = QListWidgetItem("▶  " + tr("cat_continue"))
                cw.setData(Qt.ItemDataRole.UserRole, "__continue__")
                self.cat_list.addItem(cw)
            # "Recently added" for TV, Movies and Series - the latest the
            # provider has published, newest first.
            if self.mode in ("vod", "series", "live"):
                rc = QListWidgetItem("🆕  " + tr("cat_recent"))
                rc.setData(Qt.ItemDataRole.UserRole, "__recent__")
                self.cat_list.addItem(rc)
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
            # Land on the first *real* category, skipping the synthetic ones
            # ("All" is row 0, then Recently added / Continue watching): those
            # aren't where a user expects to arrive after switching a mode, and
            # landing on "Recently added" for TV looked like the app forgot the
            # previous category.
            row = 0
            for i in range(self.cat_list.count()):
                d = self.cat_list.item(i).data(Qt.ItemDataRole.UserRole)
                if d is None or (isinstance(d, str) and d.startswith("__")):
                    continue
                row = i
                break
            # ...unless this section has been visited before, in which case
            # come back to the category that was open. The explicit overrides
            # below (a jump to what's playing, a reload asked to keep the
            # current category, a series drill-in) still win over it.
            row = self._remembered_cat_row(row)
            # A pending "jump to now playing" wants every item visible, so land
            # on the "All" row (0) rather than the first category - unless we
            # know the target's category (e.g. tuning from the EPG guide), in
            # which case land there so the sidebar reflects where the channel
            # lives (it still contains the channel, so the jump selects it).
            if getattr(self, "_pending_jump_key", None) is not None:
                row = 0
                cat = getattr(self, "_pending_jump_cat", None)
                if cat is not None:
                    for i in range(self.cat_list.count()):
                        d = self.cat_list.item(i).data(
                            Qt.ItemDataRole.UserRole)
                        # str() both sides: int vs str category ids.
                        if d is not None and str(d) == str(cat):
                            row = i
                            break
                    self._pending_jump_cat = None
            if keep is not _UNSET:
                for i in range(self.cat_list.count()):
                    if self.cat_list.item(i).data(
                            Qt.ItemDataRole.UserRole) == keep:
                        row = i
                        break
            # A series drill-in was requested while switching into Series mode
            # (e.g. clicking a show on Home). Honour it now that the categories
            # have loaded, instead of selecting a category - otherwise this
            # late category load would overwrite the episode list and bounce
            # the user back to "all series" (they'd have to click twice).
            pend = getattr(self, "_pending_series_drill", None)
            if pend is not None and request_mode == "series":
                self._pending_series_drill = None
                self._enter_series(pend)
                return
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
        if self.mode == "local" and cat == "__add__":
            # The "+ Add folder" row is a button, not a category: open the
            # picker (it rebuilds the list and selects the new folder).
            self._local_add_folder()
            return
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
        # Remember where we are in this section, so coming back lands here
        # (see _remembered_cat_row).
        self._last_cat[self.mode] = cat
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
        if self.mode == "local":
            # Coming back from another section lands where the user left
            # off - the folder they were browsing, or the series they had
            # open. Only a genuinely new category selection starts at the
            # root of it.
            if self._local_restore_place():
                return
            self._local_ctx = None
            self._local_series = None
            self._local_nav_stack = []
            self.back_btn.hide()
            self._load_local_items(category_id)
            self._local_remember_place()
            return
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
            self._load_recordings_grouped(
                self.rec.files(category_id), show_pending=category_id is None)
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
                    ("fav_series", "series", sect("series")),
                    ("nav_local", "vod", sect("local"))):
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
        if category_id == "__continue__" and self.mode in ("vod", "series"):
            # Synthetic category: partly-watched titles of this section's kind
            # (movies under Movies, episodes under Series), from the resume
            # store (no network fetch).
            kind = "vod" if self.mode == "vod" else "episode"
            self.all_items = self._continue_items(kind)
            self._apply_filter()
            return
        if category_id == "__recent__" and self.mode in ("vod", "series",
                                                          "live"):
            # Synthetic category: every title sorted by the provider's
            # publish/update time, newest first (capped so a huge library
            # doesn't lag the list).
            rmode = self.mode
            rgen = self._load_gen
            skey = "last_modified" if rmode == "series" else "added"
            rfetch = {"vod": self.client.vod_streams,
                      "series": self.client.series_list,
                      "live": self.client.live_streams}[rmode]
            # Short cache: re-opening "Recently added" refetched the entire
            # library every time (slow on a big provider). Serve a recent
            # result straight away instead.
            cache = getattr(self, "_recent_cache", {}).get(rmode)
            if cache and time.time() - cache[0] < 300:
                self.all_items = cache[1]
                self._apply_filter()
                return
            self._show_busy(tr("status_loading_recent"))

            def recent_done(items):
                if rgen != self._load_gen or self.mode != rmode:
                    return
                self._hide_busy()
                items = items or []
                excluded = self.overrides.excluded_ids(
                    rmode, include_locked=not self.parental.session_unlocked)
                if excluded:
                    items = [it for it in items
                             if str(it.get("category_id")) not in excluded]

                def _added(it):
                    try:
                        return int(it.get(skey) or 0)
                    except (TypeError, ValueError):
                        return 0
                self.all_items = sorted(
                    items, key=_added, reverse=True)[:200]
                if not hasattr(self, "_recent_cache"):
                    self._recent_cache = {}
                self._recent_cache[rmode] = (time.time(), self.all_items)
                self._apply_filter()

            def recent_fail(msg):
                if rgen != self._load_gen:
                    return
                self._hide_busy()
                self._error(msg)

            run_async(self.pool, lambda: rfetch(None),
                      recent_done, recent_fail)
            return
        self._show_busy(self._loading_message())
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
        label = self.LABELS.get(model_kind, "")
        self._set_status(f"{n} {label}".strip() if n
                         else (empty_msg or f"0 {label}".strip()))
        # On the very first populated list, select the first playable row (not a
        # section header) so the detail pane shows something straight away -
        # selecting only, never auto-playing.
        if n and not getattr(self, "_did_initial_select", False):
            for row in range(self.list_model.rowCount()):
                item = self.list_model.item_at(row)
                if item and not item.get("_header"):
                    self._did_initial_select = True
                    self.listw.setCurrentIndex(self.list_model.index(row))
                    break

    def _show_grouped(self, sections: list, model_kind: str,
                      empty_msg: str | None = None) -> None:
        """Render a combined view from ordered sections - grouped under
        headers in list mode, a flat poster wall in grid mode."""
        self._render_rows(self._grouped(sections), model_kind, empty_msg)

    def _load_recordings_grouped(self, files: list,
                                 show_pending: bool = False) -> None:
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
        # Surface the pending recordings (recording now + scheduled) at the very
        # top of the default "All recordings" view, soonest first, so upcoming
        # recordings are visible and manageable (right-click → edit / cancel)
        # the moment the Recordings section opens - not hidden behind a
        # sub-category. Only on the All view; a specific folder shows its files.
        if show_pending:
            pending = sorted(
                (j for j in self.rec.jobs
                 if j["status"] in ("recording", "scheduled")),
                key=lambda j: j.get("start") or 0)
            if pending:
                grouped.append({"_header": tr("rec_upcoming")})
                grouped += [self._job_item(j) for j in pending]
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
        # The guide download drives the "Loading programme guide" strip via
        # epg_progress, so this load must END that indicator too - on both
        # completion and failure, like refresh_playlist does. Without it the
        # strip lingered after the download (until the watchdog, or until a
        # category switch happened to hide it).
        run_async(self.pool, self.xmltv.ensure_loaded,
                  lambda ok: (self._epg_progress_finished(),
                              self.list_model.refresh_all() if ok else None),
                  lambda _: self._epg_progress_finished())

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
        "local": "files",
    }

    # History left-category -> the stored _kind values it covers, for the
    # grouped view and the per-category delete.
    _HISTORY_KINDS = {
        "live": {"live"},
        "movie": {"movie", "vod"},
        "series": {"series", "episode"},
        "local": {"local"},
    }

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
        return self._try_select_key(self._playing_key)

    def _try_select_key(self, key) -> bool:
        """Select + scroll to the row with this item key, if it's in the
        current list. The playing-item jump and the back-from-episodes landing
        (which targets the SERIES row, not what's playing) share this."""
        if key is None:
            return False
        for row in range(self.list_model.rowCount()):
            item = self.list_model.item_at(row)
            # str() both sides: providers mix int and str ids, and a key that
            # passed through JSON storage (resume/history) may come back as a
            # string while the freshly fetched list carries ints - an == on
            # raw values then never matches and the row is never selected.
            if (item and not item.get("_header")
                    and self._item_key(item) is not None
                    and str(self._item_key(item)) == str(key)):
                idx = self.list_model.index(row)
                self.listw.setCurrentIndex(idx)
                self.listw.scrollTo(
                    idx, QAbstractItemView.ScrollHint.PositionAtCenter)
                return True
        return False

    def _jump_to_now_playing(self) -> None:
        """Clicking the sidebar logo jumps the middle column to whatever's
        playing: select and scroll to its row, switching to its section and
        opening the 'All' category first so the row is actually in the list.
        Works for every kind - live channels, movies, series episodes and
        recordings (_playing_item is live-only by design, so movies and
        episodes read their snapshot from _last_playback instead)."""
        lp = getattr(self, "_last_playback", None) or {}
        playing = getattr(self, "_playing_item", None) or lp.get("item")
        if self._playing_key is None or playing is None:
            self._show_toast(tr("toast_nothing_playing"))
            return
        if self._try_select_playing():
            return
        # A playing episode lives inside its series' episode list - re-enter
        # the series (snapshot taken at playback start), and the pending-jump
        # hook in _apply_filter selects the episode once the list loads.
        # A local file lives in its own folder - go to the Local files
        # section and browse to the folder the file is in, then select it.
        if self._playing_group == "local":
            path = lp.get("url") or (playing or {}).get("_path")
            if path and os.path.isfile(path):
                self._pending_jump_key = path
                QTimer.singleShot(8000, self._clear_pending_jump)
                if self.mode != "local":
                    self.switch_mode("local")
                self._local_reveal(path)
                return
        if self._playing_group == "episode" and lp.get("series_ctx"):
            self._pending_jump_key = self._playing_key
            QTimer.singleShot(8000, self._clear_pending_jump)
            if self.mode != "series":
                # Switching modes reloads the Series categories asynchronously;
                # an immediate _enter_series would be undone when that load
                # lands and resets the list to "all series" (the same race
                # Home's series cards hit). Hand the drill to the category-load
                # callback instead, which enters the series once the categories
                # are in - the pending-jump key then selects the episode.
                self._pending_series_drill = lp["series_ctx"]
                self.switch_mode("series")
            else:
                self._enter_series(lp["series_ctx"])
            return
        # Not in the current (possibly category-filtered) list: remember the
        # target and navigate to a view that contains it, then select once it
        # has loaded (see _load_categories / _apply_filter). Land on the item's
        # own category so the sidebar reflects what's playing, not just "All".
        self._pending_jump_key = self._playing_key
        self._pending_jump_cat = playing.get("category_id")
        QTimer.singleShot(8000, self._clear_pending_jump)   # safety net
        target = {"live": "live", "vod": "vod", "episode": "series",
                  "rec": "rec"}.get(self._playing_group, self.mode)
        if self.mode != target:
            self.switch_mode(target)     # done() honours _pending_jump_cat
        elif self.cat_list.count():
            row, cat = 0, self._pending_jump_cat
            if cat is not None:
                for i in range(self.cat_list.count()):
                    d = self.cat_list.item(i).data(Qt.ItemDataRole.UserRole)
                    if d is not None and str(d) == str(cat):
                        row = i
                        break
            self._pending_jump_cat = None
            if self.cat_list.currentRow() == row:
                self._apply_filter()   # same category: just (re)select the row
            else:
                self.cat_list.setCurrentRow(row)

    def _clear_pending_jump(self) -> None:
        self._pending_jump_key = None
        self._pending_jump_cat = None

    def _reveal_item_in_list(self, it, target: str) -> None:
        """Navigate the *target* mode's list to *it*'s own category and select
        it - used when playing from Home so the classic list behind the player
        reflects (and highlights) the item, in its category, whether or not we
        were already in that mode. Works for any mode (live channels, movies)."""
        self._pending_jump_key = self._item_key(it)
        self._pending_jump_cat = it.get("category_id")
        # Safety net only - long enough that a slow provider fetch of the
        # category's list still lands inside the window (2.5 s wasn't: the key
        # got cleared mid-load and the row was never selected).
        QTimer.singleShot(8000, self._clear_pending_jump)
        if self.mode != target:
            self.switch_mode(target)   # done() honours the pending jump
            return
        if not self.cat_list.count():
            return
        row, cat = 0, self._pending_jump_cat
        if cat is not None:
            for i in range(self.cat_list.count()):
                d = self.cat_list.item(i).data(Qt.ItemDataRole.UserRole)
                # str() both sides: providers mix int and str category ids.
                if d is not None and str(d) == str(cat):
                    row = i
                    break
        self._pending_jump_cat = None
        if self.cat_list.currentRow() == row:
            self._apply_filter()   # same category: just (re)select the row
        else:
            self.cat_list.setCurrentRow(row)

    def _reveal_channel_in_list(self, it) -> None:
        """Reveal a live channel in the TV list (see _reveal_item_in_list)."""
        self._reveal_item_in_list(it, "live")

    def tune_from_guide(self, ch) -> None:
        """Play a channel picked from the EPG guide, then jump the middle
        column to it and select its category in the sidebar so the guide
        selection is reflected everywhere."""
        self.play_live_channel(ch)
        self._pending_jump_key = self._item_key(ch)
        self._pending_jump_cat = ch.get("category_id")
        QTimer.singleShot(8000, self._clear_pending_jump)
        if self.mode != "live":
            self.switch_mode("live")
        else:
            self._load_categories()

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

    def _on_list_populated(self) -> None:
        """Retire the loading strip/overlay once the model actually has rows.

        Wired to list_model.modelReset so it fires for every set_items path,
        including the ones that skip _apply_filter (startup load while the
        window is inactive, mw_search). Tolerates firing during early
        __init__, before the busy machinery exists."""
        if self.list_model.rowCount() <= 0:
            return
        if hasattr(self, "_hide_busy") and hasattr(self, "loading_bar"):
            self._hide_busy()

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
            # Match the armed key itself (for every playing-item jump it equals
            # the playing key, so this is the same select - but the back-from-
            # episodes landing arms the SERIES row, which isn't what's playing).
            if self._try_select_key(self._pending_jump_key):
                self._pending_jump_key = None
        # The list is populated now, so clear any loading strip/overlay - a
        # gen-discarded async load (e.g. rapid Home<->TV toggling) could leave
        # the "Loading channels…" overlay stuck on until the next selection.
        self._hide_busy()
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

    def _play_kind_for(self, it) -> str:
        """The playback/resume kind for a row. In mixed views (Favorites 'All',
        History, ...) the section-derived _history_kind() would treat a movie as
        'live' and skip its resume prompt, so honour the row's own kind tag when
        it carries one."""
        ek = it.get("_kind") or it.get("_ekind")
        mapped = {"vod": "movie", "movie": "movie", "series": "series",
                  "episode": "episode", "live": "live", "fav": "live"}.get(ek)
        if mapped:
            return mapped
        # Favorites' Movies/Series sections: their rows carry no _kind tag,
        # and the section - not "fav == live" - says what they are (the same
        # routing _stream_for uses). Without this a favorite movie played and
        # resumed as "live": no resume prompt, no saved position.
        if self.mode == "fav" and not self.series_ctx:
            if self._fav_section == "movie":
                return "movie"
            if self._fav_section == "series":
                return "series"
        return self._history_kind()

    # -- selection, EPG and detail panel -------------------------------------------


    # -- series -> episodes --------------------------------------------------------

    def _enter_series(self, series) -> None:
        sid = series.get("series_id")
        if sid is None:
            return
        self._show_busy(tr("status_loading_episodes"))

        def done(info):
            self._hide_busy()
            # Fall back to the show's own poster so an episode row isn't a
            # bare initial: prefer the episode still, then the series art.
            series_cover = (series.get("cover") or series.get("cover_big")
                            or series.get("stream_icon") or "")
            series_title = series.get("name") or series.get("title") or ""
            episodes = []
            for season, eps in (info.get("episodes") or {}).items():
                for ep in eps:
                    ep["season"] = season
                    ep["name"] = (
                        f"S{season} * E{ep.get('episode_num', '?')} - "
                        f"{ep.get('title') or 'Episode'}")
                    still = (ep.get("info") or {}).get("movie_image") or ""
                    ep["stream_icon"] = (ep.get("stream_icon") or still
                                         or series_cover)
                    # Let the cover pipeline resolve the show's TMDB poster for
                    # each episode (its own name never matches TMDB).
                    ep["_series_title"] = series_title
                    episodes.append(ep)
            # A series entered from a slim snapshot (resume ctx) doesn't know
            # its category; series_info usually does. Backfill it so backing
            # out of the episodes can land in the series' own category.
            sctx = series
            if sctx.get("category_id") is None:
                cid = (info.get("info") or {}).get("category_id")
                if cid is not None:
                    sctx = dict(sctx)
                    sctx["category_id"] = cid
            self.series_ctx = sctx
            self.all_items = episodes
            self.back_btn.setText("<-  " + tr("btn_back_to_series"))
            self.back_btn.show()
            self.search.clear()
            self._apply_filter()

        def failed(msg):
            self._hide_busy()
            log.warning("series_info(%s) failed: %s", sid, msg)
            self._set_status(tr("err_series_open"), error=True)

        run_async(self.pool, lambda: self.client.series_info(sid),
                  done, failed)

    def _leave_series(self) -> None:
        # The back button is shared: in the Local files section it walks up
        # one directory instead of leaving a series.
        if self.mode == "local":
            self._local_up()
            return
        ctx = self.series_ctx or {}
        self.series_ctx = None
        self.back_btn.hide()
        # Land in the series' OWN category with the series row selected - not
        # whatever category happened to be selected in the sidebar. After a
        # now-playing jump / Home drill entered the episodes from elsewhere,
        # the sidebar still shows the old selection (e.g. "All"), and backing
        # out to that instead of e.g. "Nordic" read as landing in the wrong
        # place. Unknown category (old snapshot ctx) keeps the old behaviour.
        sid = ctx.get("series_id")
        if sid is not None:
            self._pending_jump_key = sid
            QTimer.singleShot(8000, self._clear_pending_jump)
        cur = self.cat_list.currentItem()
        cur_cat = cur.data(Qt.ItemDataRole.UserRole) if cur else None
        cat = ctx.get("category_id")
        if cat is not None and str(cat) != str(cur_cat):
            for i in range(self.cat_list.count()):
                d = self.cat_list.item(i).data(Qt.ItemDataRole.UserRole)
                # str() both sides: providers mix int and str category ids.
                if d is not None and str(d) == str(cat):
                    self.cat_list.setCurrentRow(i)   # triggers the list load
                    return
        self._load_items(cur_cat)

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
        if eff_mode == "vod" and it.get("stream_id") is not None:
            return self.client.vod_url(
                it.get("stream_id"), it.get("container_extension")), title
        # No provider id: a snapshot-derived row (e.g. Continue watching seeded
        # by a play from History) still carries the URL it was played from, and
        # that one is known-good. Building a URL from a missing id instead gave
        # /movie/user/pass/None.<ext> and a stream error.
        return it.get("_url"), title

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
        # A Continue-watching episode row plays straight from its stored series
        # context (it isn't reachable through the normal series drill-down).
        if it.get("_kind") == "episode" and it.get("_series_ctx") is not None:
            self._play_continue_episode(it, player, external)
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
        if self.mode == "local":
            path = it.get("_path")
            if it.get("_kind") == "localdir":
                self._local_descend(path, it.get("_key"))
                return
            if it.get("_kind") == "localseries":
                self._local_open_series(it.get("_series_title") or "")
                return
            if it.get("_kind") in ("localcollection", "localalbum"):
                self._local_descend(path, it.get("_key"))
                return
            if not path or not os.path.isfile(path):
                return
            title = it.get("name") or os.path.basename(path)
            if external or player == "vlc":
                launch_player(player or "mpv", path, title, self)
                return
            self._start_playback(path, title, it.get("stream_icon"), path,
                                 "local", record=True, item=it)
            return
        if self.mode == "history":
            url = it.get("_url")
            title = it.get("name") or "dopeIPTV"
            icon, key, kind = (it.get("stream_icon"), it.get("_key"),
                               it.get("_kind"))
            if kind == "local" and (not url or not os.path.isfile(url)):
                # A file on an unmounted share is not an error to shrug at:
                # say why it cannot play, and offer to drop the stale row.
                # Cancel keeps it - after mounting the share it plays again.
                idx = self._choice_dialog(
                    tr("nav_local"), tr("local_missing"),
                    [(tr("local_missing_remove"), "normal"),
                     (tr("common_cancel"), "primary")])
                if idx == 0:
                    self.history.remove(key, kind)
                    self._load_items(self._current_cat)
                return
        else:
            url, title = self._stream_for(it)
            icon = it.get("stream_icon") or it.get("cover")
            key, kind = self._item_key(it), self._play_kind_for(it)
        if not url:
            return

        if external or player == "vlc":
            # Which door playback went through - a silent external window
            # left no way to tell a deliberate "open externally" from the
            # embedded player refusing the stream.
            log.info("play: external requested (player=%r external=%r) %s",
                     player, external, title)
            if not self._confirm_external_while_playing():
                return
            launch_player(player or "mpv", url, title, self)
            if self.mode != "history":
                self.history.add(url, title, icon, key, kind)
            return

        self._start_playback(url, title, icon, key, kind,
                             record=self.mode != "history", item=it)

    # -- local files ---------------------------------------------------------------

    VIDEO_EXTS = (".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts",
                  ".m2ts", ".mts", ".mpg", ".mpeg", ".wmv", ".flv", ".ogv",
                  ".3gp", ".vob")
    # Music plays through the very same mpv path - the picture just stays
    # dark. Kept apart from VIDEO_EXTS: frame-grab thumbnails make no sense
    # for audio.
    # A file that is music but not on this list is treated as video all the
    # way through: no visualiser, no tags in the panel, and - the reason
    # this list grew - no place in the play queue, so it played once and
    # went quiet. Everything here is something mpv decodes as audio.
    AUDIO_EXTS = (".mp3", ".flac", ".m4a", ".aac", ".ogg", ".oga", ".opus",
                  ".wav", ".wma", ".aiff", ".aif", ".alac", ".ape", ".wv",
                  ".dsf", ".dff", ".mpc", ".mka", ".m4b", ".mp2", ".ac3",
                  ".dts", ".amr", ".spx", ".tta")
    MEDIA_EXTS = VIDEO_EXTS + AUDIO_EXTS

    def open_local_video(self) -> None:
        """Pick a video off the disk and play it. A mounted SMB/NFS share (or
        a Windows UNC path) is an ordinary path here, so network files come
        along for free - no in-app SMB client needed."""
        from PyQt6.QtWidgets import QFileDialog
        start = (self.settings.value("local_open_dir", "")
                 or os.path.expanduser("~"))
        pattern = " ".join("*" + e for e in self.MEDIA_EXTS)
        path, _ = QFileDialog.getOpenFileName(
            self, tr("open_video_title"), start,
            f"{tr('open_video_filter')} ({pattern});;All files (*)",
            options=QFileDialog.Option.DontUseNativeDialog)
        if not path:
            return
        self.settings.setValue("local_open_dir", os.path.dirname(path))
        self._play_local_path(path)

    def _play_local_path(self, path: str) -> None:
        """Play a file from disk in the embedded player. Rides the recordings
        path: same local-file playback, same resume bookkeeping (keyed on
        the path), no provider connection and nothing to record."""
        if not path or not os.path.isfile(path):
            return
        title = os.path.splitext(os.path.basename(path))[0]
        self._start_playback(path, title, None, path, "local",
                             record=True, item={"name": title, "_path": path,
                                                "_key": path,
                                                "_kind": "local"})

    def dragEnterEvent(self, e) -> None:
        if any(u.isLocalFile()
               and u.toLocalFile().lower().endswith(self.MEDIA_EXTS)
               for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p and p.lower().endswith(self.MEDIA_EXTS):
                self._play_local_path(p)
                e.acceptProposedAction()
                return

    # -- music: the play queue -------------------------------------------------

    def _is_audio(self, path: str) -> bool:
        return str(path or "").lower().endswith(self.AUDIO_EXTS)

    def _is_video(self, path: str) -> bool:
        return str(path or "").lower().endswith(self.VIDEO_EXTS)

    def queue_add(self, items, play_next: bool = False) -> None:
        """Put tracks in the queue - at the front (play next) or the end."""
        rows = [it for it in (items or [])
                if it and self._is_audio(it.get("_path"))]
        if not rows:
            return
        q = list(getattr(self, "_track_queue", []) or [])
        i = getattr(self, "_track_index", -1)
        if play_next and 0 <= i < len(q):
            q[i + 1:i + 1] = rows
        else:
            q += rows
        self._track_queue = q
        # Hand-built: _start_playback must not silently replace it with
        # whatever listing happens to be on screen.
        self._queue_explicit = True
        self._sync_queue_buttons()
        self._set_status(tr("queue_added", n=len(rows)))

    def queue_clear(self) -> None:
        self._track_queue = []
        self._track_index = -1
        self._queue_explicit = False
        self._sync_queue_buttons()

    def _sync_queue_buttons(self) -> None:
        pl = getattr(self, "player", None)
        if pl is None:
            return
        q = getattr(self, "_track_queue", []) or []
        i = getattr(self, "_track_index", -1)
        try:
            pl.set_next_available(self._local_is_playing()
                                  and bool(q) and i + 1 < len(q))
        except Exception:
            pass

    def _queue_step(self, direction: int) -> bool:
        """Play the next/previous queued track. False when music is not
        what is playing, so the caller falls back to its normal zap.

        The queue is only in charge while a track is actually playing:
        with a channel or a film on, prev/next mean the channel list
        again, and a queue left over from earlier must not hijack them."""
        q = getattr(self, "_track_queue", []) or []
        i = getattr(self, "_track_index", -1)
        if not self._local_is_playing():
            log.info("queue: not stepping - no local file is playing "
                     "(key=%r last=%r)", getattr(self, "_playing_key", None),
                     (getattr(self, "_last_playback", None) or {}).get("kind"))
            return False
        if not q or i < 0:
            log.info("queue: nothing to step to (%d tracks, index %d)",
                     len(q), i)
            return False
        step = 1 if direction > 0 else -1
        j = i + step
        # Walk past tracks whose file has gone - an unmounted share, a
        # renamed folder. Stopping dead on the first missing one is the
        # other way an album fell silent halfway through.
        while 0 <= j < len(q):
            if self._play_queued(j):
                return True
            j += step
        log.info("queue: no playable track %s of %d (index %d)",
                 "after" if step > 0 else "before", len(q), i)
        return False

    def _play_queued(self, index: int) -> bool:
        """Play queue entry *index*. False when there is nothing playable
        there, so the caller can move on to the next one."""
        q = getattr(self, "_track_queue", []) or []
        if not (0 <= index < len(q)):
            return False
        it = q[index]
        path = it.get("_path")
        if not path or not os.path.isfile(path):
            log.info("queue: skipping missing track %s", path)
            return False
        self._track_index = index
        self._start_playback(path, it.get("name") or os.path.basename(path),
                             it.get("stream_icon"), path, "local",
                             record=True, item=it)
        self._sync_queue_buttons()
        return True

    def _place_in_queue(self, url: str, title: str, item=None) -> None:
        """Point the play queue at the track that is starting, so that
        end-of-track knows what comes next.

        Three cases. The track is already queued: just position on it. It
        is not, but the user built this queue by hand: slot it in ahead of
        what they queued and play on into it - discarding a hand-built
        queue loses the one thing they asked for. Otherwise the listing on
        screen becomes the queue, which is what makes a folder play
        through without anyone queueing anything.

        The middle case is the bug this fixes: a hand-built queue used to
        be replaced by the listing, and if that listing held no audio at
        all (browsing TV, say) the queue collapsed to the single track
        being played - so it fell silent the moment that track ended."""
        q = getattr(self, "_track_queue", []) or []
        here = next((n for n, t in enumerate(q)
                     if t.get("_path") == url), -1)
        if here < 0 and q and getattr(self, "_queue_explicit", False):
            row = dict(item) if isinstance(item, dict) else {}
            row["_path"] = url
            row.setdefault("name", title)
            i = getattr(self, "_track_index", -1)
            here = i + 1 if 0 <= i < len(q) else 0
            q = list(q)
            q.insert(here, row)
            self._track_queue = q
        elif here < 0:
            # Same media family only: a folder holding both an album and a
            # film must not roll from the last track into the film.
            same = self._is_audio if self._is_audio(url) else self._is_video
            q = [r for r in (self.all_items or [])
                 if not r.get("_header") and same(r.get("_path") or "")]
            here = next((n for n, t in enumerate(q)
                         if t.get("_path") == url), -1)
            if here < 0:
                q, here = [{"_path": url, "name": title}], 0
            self._track_queue = q
            self._queue_explicit = False
        self._track_index = here

    def _local_is_playing(self) -> bool:
        """Is a local file what the player is on right now? The queue is
        only ever in charge then - with a channel or a provider film on,
        prev/next mean the channel list again."""
        if getattr(self, "_playing_key", None) is None:
            return False
        lp = getattr(self, "_last_playback", None) or {}
        return lp.get("kind") == "local"

    def _music_is_playing(self) -> bool:
        """Is a local audio file what the player is on right now?"""
        lp = getattr(self, "_last_playback", None) or {}
        return self._local_is_playing() and self._is_audio(lp.get("url"))

    def _local_autoadvance_ok(self, url: str) -> bool:
        """Should end-of-file roll on to the next local file?

        Music always does - an album is meant to play through. Video only
        when it is an EPISODE: either the file names one (S01E02 / 1x02)
        or we are inside a local series. Nobody wants an unrelated film to
        start because the one before it happened to end."""
        if self._is_audio(url):
            return True
        if getattr(self, "_local_series", None):
            return True
        from .mw_local import episode_info
        stem = os.path.splitext(os.path.basename(str(url or "")))[0]
        return episode_info(stem) is not None

    def _queue_autoplay(self) -> bool:
        """End of a track/episode: roll on to the next queued one."""
        lp = getattr(self, "_last_playback", None) or {}
        if not self._local_autoadvance_ok(lp.get("url") or ""):
            log.info("queue: not auto-advancing past %r (not an episode)",
                     lp.get("url"))
            return False
        return self._queue_step(1)

    def open_queue(self) -> None:
        """The queue, with what is playing marked; double-click to jump."""
        from PyQt6.QtWidgets import (
            QDialog, QDialogButtonBox, QListWidget, QListWidgetItem,
            QVBoxLayout,
        )
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("queue_title"))
        dlg.resize(460, 420)
        v = QVBoxLayout(dlg)
        lst = QListWidget()
        q = getattr(self, "_track_queue", []) or []
        cur = getattr(self, "_track_index", -1)
        for n, it in enumerate(q):
            row = QListWidgetItem(
                ("▶  " if n == cur else "     ") + (it.get("name") or ""))
            row.setData(Qt.ItemDataRole.UserRole, n)
            lst.addItem(row)
        v.addWidget(lst, 1)

        def jump(item):
            self._play_queued(item.data(Qt.ItemDataRole.UserRole))
            dlg.accept()

        lst.itemDoubleClicked.connect(jump)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        clear = bb.addButton(tr("queue_clear"),
                             QDialogButtonBox.ButtonRole.ActionRole)
        clear.clicked.connect(lambda: (self.queue_clear(), dlg.accept()))
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        dlg.exec()

    # -- music: visualiser + equaliser -----------------------------------------

    def _eq_settings(self) -> tuple[list[float], bool]:
        raw = self.settings.value("eq_gains", "")
        try:
            gains = [float(x) for x in str(raw).split(",")] if raw else []
        except ValueError:
            gains = []
        gains = (gains + [0.0] * 10)[:10]
        return gains, self.settings.value("eq_on", "false") == "true"

    def _apply_audio_visuals(self, url: str) -> None:
        """Music gets a visualiser in place of the black video pane, and
        whatever equaliser the user set.

        VIDEO IS NOT TOUCHED - not the filter graph, not the audio chain,
        nothing. Writing mpv's lavfi-complex here (even to clear it) made
        a clicked TV channel fail to open, so it fell back to an external
        player; the auto-preview, which never came through here, played
        fine and that is what gave the bug away. A stale visualiser graph
        from previous music is cleared inside player.play() instead, which
        only writes when one is actually set."""
        pl = getattr(self, "player", None)
        if pl is None:
            return
        if not str(url or "").lower().endswith(self.AUDIO_EXTS):
            # Video. The ONLY thing allowed here is taking down a
            # visualiser we ourselves put up for a previous track - and
            # only when there is one. An ordinary channel play must reach
            # mpv untouched.
            if getattr(self, "_vis_active", False):
                self._vis_active = False
                try:
                    pl.set_visualiser(False)
                except Exception as e:
                    log.debug("visualiser teardown failed: %s", e)
            return
        try:
            style = self.settings.value("vis_style", "bars")
            want = self.settings.value("vis_on", "true") == "true"
            pl.set_visualiser(want, style)
            self._vis_active = want
            gains, on = self._eq_settings()
            pl.set_equaliser(gains, on)
        except Exception as e:
            log.debug("audio visuals failed: %s", e)

    def _vis_choose(self, on: bool, style: str) -> None:
        """Remember the visualiser choice and apply it to what is playing."""
        self.settings.setValue("vis_on", "true" if on else "false")
        self.settings.setValue("vis_style", style)
        pl = getattr(self, "player", None)
        url = (getattr(self, "_last_playback", None) or {}).get("url", "")
        if pl is not None and str(url).lower().endswith(self.AUDIO_EXTS):
            pl.set_visualiser(on, style)

    def open_equaliser(self) -> None:
        """A ten-band graphic equaliser with the usual presets. Applies live
        while playing and is remembered for the next track."""
        from PyQt6.QtWidgets import (
            QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
            QLabel, QSlider, QVBoxLayout,
        )
        pl = getattr(self, "player", None)
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("eq_title"))
        v = QVBoxLayout(dlg)
        gains, on = self._eq_settings()
        top = QHBoxLayout()
        chk = QCheckBox(tr("eq_enable"))
        chk.setChecked(on)
        top.addWidget(chk)
        top.addStretch(1)
        top.addWidget(QLabel(tr("eq_preset")))
        presets = QComboBox()
        for key in ("flat", "bass", "treble", "vocal", "rock"):
            presets.addItem(tr(f"eq_preset_{key}"), key)
        top.addWidget(presets)
        v.addLayout(top)

        row = QHBoxLayout()
        sliders = []
        bands = (pl.EQ_BANDS if pl is not None
                 else (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000))
        for i, f in enumerate(bands):
            col = QVBoxLayout()
            sl = QSlider(Qt.Orientation.Vertical)
            sl.setRange(-12, 12)
            sl.setValue(int(round(gains[i])))
            sl.setMinimumHeight(120)
            lab = QLabel(f"{f // 1000}k" if f >= 1000 else str(f))
            lab.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            col.addWidget(sl, 1, Qt.AlignmentFlag.AlignHCenter)
            col.addWidget(lab)
            row.addLayout(col)
            sliders.append(sl)
        v.addLayout(row)

        def apply_now():
            vals = [s.value() for s in sliders]
            self.settings.setValue("eq_gains",
                                   ",".join(str(x) for x in vals))
            self.settings.setValue("eq_on",
                                   "true" if chk.isChecked() else "false")
            if pl is not None:
                pl.set_equaliser(vals, chk.isChecked())

        def use_preset():
            key = presets.currentData()
            vals = (pl.EQ_PRESETS if pl is not None
                    else {}).get(key, (0,) * 10)
            for sl, g in zip(sliders, vals, strict=False):
                sl.setValue(int(g))
            apply_now()

        for sl in sliders:
            sl.valueChanged.connect(lambda _v: apply_now())
        chk.toggled.connect(lambda _v: apply_now())
        presets.activated.connect(lambda _i: use_preset())
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        bb.accepted.connect(dlg.accept)
        v.addWidget(bb)
        dlg.exec()

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
            # Which of the two it is. Being told to install a package that is
            # already installed is a wild goose chase, and the reason it
            # would not import is the only thing that can end it.
            from ..providers.chromecast import cast_import_error
            why = cast_import_error()
            QMessageBox.information(
                self, "Chromecast",
                tr("msg_cast_package_broken", why=why) if why
                else tr("msg_cast_needs_package"))
            return
        # Already casting this very title? Then this is not a new cast - it is
        # someone coming back to change the device or a track. Reopen the
        # panel on the running session: no question about resuming (that was
        # answered when it started) and nothing touched if it is closed again.
        ctx = self._cast_ctx or {}
        if (getattr(self.cast, "active", None) is not None
                and ctx.get("key") is not None
                and ctx.get("key") == self._item_key(it)):
            self.manage_cast()
            return
        url, title = self._stream_for(it)
        if not url:
            url = it.get("_url")
            title = it.get("name") or it.get("title") or "dopeIPTV"
        # A live channel has to go as HLS: the receiver cannot decode a raw
        # MPEG transport stream at all, whatever we label it. Every section
        # can show a live row - Channels, Favorites, History, Home - so the
        # rule belongs here and not inside one section's branch. History used
        # to hand over its stored .ts address verbatim, which is why casting
        # worked from the channel list and never from History.
        # The ROW decides, and only when it says nothing does the section get
        # a vote. Letting the section decide made a favourite film into a
        # channel: a movie row in Favorites was handed a /live/ address built
        # from its own id, which the panel answers with a 4XX - to the
        # receiver, to the converter, to everything.
        row_kind = it.get("_kind")
        if row_kind:
            live = row_kind == "live"
        else:
            live = (self._content_kind() in ("live", "fav")
                    or (self.mode == "fav" and self._fav_section == "chan"))
        # Two addresses for the same channel. HLS is the one a Chromecast can
        # take directly; the plain stream is the one the player is watching -
        # and some channels are not served as HLS at all, which is a 4XX to
        # everything that asks. The converter reads that one, since ffmpeg has
        # no trouble with a transport stream and the receiver never sees it.
        source = url
        sid = it.get("stream_id")
        if live and sid is not None:
            url = self.client.live_url(sid, "m3u8")
        elif row_kind in ("movie", "vod") and sid is not None:
            # Build the film's own address rather than trust what the section
            # produced. In the Favorites channel folders _stream_for answers
            # for the section, not the row, so a favourite film came back with
            # a /live/ address built from its id - which the panel refuses.
            url = source = self.client.vod_url(
                sid, it.get("container_extension"))
        # A row built from nothing but the address on screen (a play from
        # Home, a resumed title) says so, and that address is used as it is -
        # there is no provider id to build anything else from.
        if it.get("_cast_url"):
            url = source = it["_cast_url"]
        if not url:
            return
        # Remember what is being cast, not just where to. Pausing a live
        # channel is answered from the provider's archive, and that needs the
        # channel's own id long after this dialog is gone.
        # A film or an episode picks up where you left off, exactly as it
        # does here - the same stored point, and the same question about it.
        rkind = self._CAST_RESUME_KIND.get(
            it.get("_kind") or self._content_kind())
        key = self._item_key(it)
        # Where the film actually is, which is the player when the player is
        # the one playing it. The stored point is only written when playback
        # switches or stops, so casting a film you are 22 minutes into asked
        # a store that knew nothing yet and offered to start it over.
        # Only where there is something to resume. A live channel has no
        # point to come back to - what a timeshift channel is a few minutes
        # into is this session, not a place in a title - and being asked
        # whether to carry on from 23 minutes in before casting the news was
        # a question about nothing.
        start = 0.0
        if rkind:
            here = self._playing_position(it)
            start = (self._ask_resume(here) if here > 60
                     else self._resume_offset(key, rkind))
        self._cast_ctx = {
            "sid": it.get("stream_id") if live else None,
            "archive": bool(live and it.get("stream_id") is not None
                            and it.get("tv_archive")),
            "title": title,
            "group": self._RESUME_GROUP.get(rkind or ""),
            "key": key,
            "item": it,
            # For History: the row's own kind and address, kept apart from
            # the address currently on the receiver, which an archive resume
            # or a track change replaces.
            "kind": self._play_kind_for(it),
            "row_url": url,
            "row_source": source,
        }
        CastDialog(self, url, title, self._local_codecs(),
                   self._local_audio_index(), start,
                   self._local_tracks(it),
                   probe=not self._busy_elsewhere(it), source=source,
                   live=self._play_kind_for(it) == "live").exec()

    # The list vocabulary and the resume store's do not match: a movie row is
    # "vod" in one and "movie" in the other, and History rows carry their own.
    _CAST_RESUME_KIND = {"vod": "movie", "movie": "movie",
                         "episode": "episode",
                         "rec": "recording", "recording": "recording",
                         "local": "local"}
    _RESUME_GROUP = {"movie": "vod", "episode": "episode", "recording": "rec",
                     "local": "local"}

    def _busy_elsewhere(self, it) -> bool:
        """Is the app holding a provider connection for something else?

        Listing tracks with ffprobe opens the stream again, and one of these
        accounts allows a single connection at a time. While the player has
        one for another title, that probe cannot succeed and asking anyway
        just spends the seconds the panel then keeps counting.
        """
        p = getattr(self, "player", None)
        if p is None or not getattr(p, "current_url", None):
            return False
        return self._item_key(it) != self._playing_key

    def cast_playing(self) -> None:
        """Send what the player is showing to a Chromecast.

        The same dialog the list's right-click opens, on the row that is
        playing - so you can start watching here and move it to the TV
        without hunting for where the channel was in the list.
        """
        p = getattr(self, "player", None)
        if p is None or not getattr(p, "current_url", None):
            return
        it = getattr(self, "_playing_item", None)
        if not it:
            it = (getattr(self, "_last_playback", None) or {}).get("item")
        if not it:
            # Not every way into the player leaves the row behind - a play
            # from Home, a resumed title. The address on screen is enough to
            # cast, and having the entry disappear for those would be the
            # wrong half of the feature.
            it = {"name": getattr(self, "_detail_name", "") or "dopeIPTV",
                  "_url": p.current_url, "_cast_url": p.current_url}
        self._open_cast_dialog(it)

    def can_cast_playing(self) -> bool:
        p = getattr(self, "player", None)
        return bool(p is not None and getattr(p, "current_url", None))

    def _playing_position(self, it) -> float:
        """How far into *it* the app itself has got, or 0 when it is not the
        thing playing (or has no length, as live has none)."""
        p = getattr(self, "player", None)
        if (p is None or self._playing_key is None
                or self._item_key(it) != self._playing_key):
            return 0.0
        try:
            return p.playback_position() if p.playback_duration() > 1 else 0.0
        except Exception:
            return 0.0

    def _local_tracks(self, it) -> dict:
        """The tracks mpv can already see, in the shape ffprobe would give.

        Asking ffprobe means opening the stream a second time, and these
        accounts are sold with one connection: the probe's session was still
        counted when the cast went for the same stream, and the panel answered
        the converter with a 4XX. mpv is playing the very thing being cast and
        has the whole track list in memory - free, instant, and correct.

        Only when it IS the same thing: this returns nothing for any other row
        and ffprobe answers for those instead.
        """
        if self._playing_key is None or self._item_key(it) != self._playing_key:
            return {}
        m = getattr(getattr(getattr(self, "player", None), "video", None),
                    "mpv", None)
        if m is None:
            return {}
        out: dict = {"audio": [], "subtitle": [], "duration": 0.0,
                     "height": 0, "fps": 0.0}
        try:
            # How long the film is. mpv knows, and without it the cast strip
            # has nothing to measure a position against - so a film handed
            # over from the player, which is the ordinary way of casting one,
            # arrived at the TV with no way to move within it.
            #
            # A broadcast has no length, whatever mpv answers: for a live HLS
            # stream that number is the seekable window, a minute or so of
            # buffer, and putting a position bar on THAT turns the strip into
            # a buffer gauge for a channel whose way back is the archive.
            if self._play_kind_for(it) != "live":
                out["duration"] = float(getattr(m, "duration", 0) or 0.0)
            for t in (m.track_list or []):
                kind = t.get("type")
                if kind == "video" and not out["height"]:
                    out["height"] = int(t.get("demux-h") or 0)
                    out["fps"] = float(t.get("demux-fps") or 0.0)
                    if not out["height"]:
                        # The track list fills demux-h in when the demuxer
                        # gets round to it, and on a live stream that can be
                        # after the cast has already been asked for. mpv's own
                        # properties know as soon as the first frame is
                        # decoded, which is always sooner.
                        out["height"] = int(getattr(m, "height", 0) or 0)
                        out["fps"] = float(
                            getattr(m, "container_fps", 0) or 0.0)
                if kind not in ("audio", "sub"):
                    continue
                key = "audio" if kind == "audio" else "subtitle"
                out[key].append({
                    "index": len(out[key]),
                    "codec": t.get("codec") or "?",
                    "lang": (t.get("lang") or "").strip(),
                    "title": (t.get("title") or "").strip(),
                    "default": bool(t.get("default")),
                })
        except Exception as e:
            log.debug("cast: could not read the local tracks (%s)", e)
            return {}
        log.info("cast: %d audio and %d subtitle track(s), from the player",
                 len(out["audio"]), len(out["subtitle"]))
        return out

    def _local_audio_index(self) -> int:
        """Which audio track the app itself is playing, counted the way
        ffmpeg counts them.

        If you switched to the Swedish track here, that is the one you meant
        to send to the TV - the cast dialog opens on it rather than on
        whatever the stream calls its default. Zero means the default, and
        the default is what keeps a cast native.
        """
        m = getattr(getattr(getattr(self, "player", None), "video", None),
                    "mpv", None)
        if m is None:
            return 0
        try:
            audio = [t for t in (m.track_list or []) if t.get("type") == "audio"]
            for i, t in enumerate(audio):
                if t.get("selected"):
                    return i
        except Exception as e:
            log.debug("cast: could not read the local audio track (%s)", e)
        return 0

    def _local_codecs(self) -> list[str]:
        """Write down what the stream actually is, while we still know.

        A Chromecast refuses a stream it cannot decode without ever saying
        which part it choked on, and an HLS media playlist carries no codec
        information at all - the segments are just paths. The one place the
        answer exists is mpv, which may be decoding the very same stream right
        now, so ask it before handing the channel to a TV. HEVC video or AC-3
        audio on an older receiver is a refusal no address can fix.
        """
        m = getattr(getattr(getattr(self, "player", None), "video", None),
                    "mpv", None)
        if m is None:
            return []
        found = []
        try:
            for t in (m.track_list or []):
                if t.get("selected") and t.get("type") in ("video", "audio"):
                    codec = t.get("codec") or "?"
                    log.info("cast: what is playing here - %s %s",
                             t.get("type"), codec)
                    found.append(codec)
        except Exception as e:
            log.debug("cast: could not read the local codecs (%s)", e)
        return found

    def _stop_cast_for_local_playback(self) -> None:
        """End a running cast when something starts playing in the app.

        Casting is a handover: the stream goes to the TV and local playback
        stops. Starting a video here is the same handover in reverse, so the
        cast has to end - otherwise the account holds two connections at once
        and on a tight limit the new stream is simply refused, which looks
        like the app failing to play anything after a cast.

        """
        self._end_cast("local playback took over")

    def show_cast_strip(self, device: str | None, title: str = "") -> None:
        """Show - or take down - the "Casting to X" strip above the player.

        Casting stops local playback (the receiver pulls the stream itself),
        so the player pane goes black and without this nothing anywhere says
        that anything is happening at all. The cast dialog calls it when a
        cast starts, fails or is stopped.
        """
        self._cast_device = device
        ctx = getattr(self, "_cast_ctx", None) or {}
        self._cast_key = ctx.get("key") if device else None
        self._cast_group = ctx.get("group") if device else None
        if self._cast_group is None and device:
            # A live channel has no resume group, but it still has a row.
            self._cast_group = "live" if ctx.get("sid") else None
            self._cast_key = ctx.get("key")
        for name in ("listw", "grid"):
            view = getattr(self, name, None)
            try:
                view.viewport().update()
            except AttributeError:
                pass
        bar = getattr(self, "cast_bar", None)
        if bar is None:
            return
        self.cast_bar_pause.setIcon(cast_strip_icon("pause", P["text"]))
        # Shown only where pausing works. A film the receiver fetched itself
        # holds its place; a broadcast can be held only while the converter
        # is recording it. A channel that ended up going straight to the
        # receiver has neither, and a button that cannot do what it says is
        # worse than no button.
        self.cast_bar_pause.setVisible(
            bool(device) and (not ctx.get("sid") or self.cast.bridged()))
        self.cast_bar_ts.setVisible(bool(device) and bool(ctx.get("archive")))
        if not device:
            self._cast_tick.stop()
            bar.hide()
            return
        # A different thing is being cast now: how far behind live the last
        # one was is not about this one. Left standing, a channel paused for
        # ten minutes made the next cast open ten minutes behind itself.
        #
        # Marked in the context rather than reset on every call, because this
        # runs again for a track change and for going back to live - and both
        # of those are in the middle of managing this very counter. Only
        # starting something new builds a new context, so the mark is gone.
        if not ctx.get("_counted"):
            ctx["_counted"] = True
            self._cast_behind = 0.0
            self._cast_paused_at = None
        # The player pane goes with it. Casting stops local playback - the
        # receiver pulls the stream itself, and on a one-connection account
        # two readers is one too many - so what is left behind is a black
        # rectangle with a toolbar under it that controls nothing. The strip
        # above it is what is playing now.
        #
        # Not shown again from here: when the cast ends nothing is playing
        # locally either, and every path that starts playback shows the
        # player itself.
        pl = getattr(self, "player", None)
        if pl is not None and pl.isVisible():
            pl.hide()
        self._record_cast_history()
        self._show_cast_progress()
        self.cast_bar_lbl.setText(tr("cast_casting_to", name=device))
        self.cast_bar_title.setText(title or "")
        self.cast_bar_title.setVisible(bool(title))
        self._show_cast_volume()
        bar.show()

    def _history_extra(self, kind: str, item, title: str) -> dict | None:
        """What a History row needs beyond its address, for this kind.

        For a live channel, its stream_id and archive depth, so a later replay
        from History still has timeshift and catch-up available.

        For an episode, its series - so a replay from History (or Home's
        Recently viewed) resumes as an EPISODE, lands in the series' episode
        list and resolves the series' poster. Without it the row degraded to a
        context-less "movie": restarted from zero, duplicated in History (the
        kind mismatch broke the dedup) and posterless. Same slim snapshot the
        resume store keeps.
        """
        if kind == "live" and item is not None:
            return {"stream_id": item.get("stream_id"),
                    "num": item.get("num"),
                    "tv_archive": item.get("tv_archive"),
                    "tv_archive_duration": item.get("tv_archive_duration")}
        if kind == "local" and item is not None:
            return {k: item.get(k)
                    for k in ("_year", "_filename", "_clean_title", "_path")
                    if item.get(k)}
        if kind != "episode":
            return None
        sctx = self.series_ctx or {}
        if sctx.get("series_id") is None:
            return None
        slim = {k: sctx.get(k)
                for k in ("series_id", "name", "title", "cover",
                          "stream_icon", "category_id", "_tmdb_id")
                if sctx.get(k) is not None}
        sname = sctx.get("name") or sctx.get("title")
        extra = {"_series_ctx": slim, "_series_title": sname}
        # Store the row as "Series · S1 * E2 - ..." so History and the Home
        # shelf say WHICH show the episode belongs to - a bare "S01 E01" told
        # the user nothing. Skip when the title already carries it (a
        # continue-watching replay).
        if sname and not title.startswith(sname):
            extra["name"] = f"{sname} · {title}"
        return extra

    def _record_cast_history(self) -> None:
        """A cast is a play, and belongs in History like any other.

        Nothing else recorded it. Every other route into playback goes through
        _start_playback, which a cast deliberately does not - the stream never
        touches this machine - so an evening's television watched on the TV
        left no trace at all, and could not be picked up again from History
        the way anything played here can.
        """
        ctx = getattr(self, "_cast_ctx", None) or {}
        it = ctx.get("item") or {}
        # The row's own address, not the one currently on the receiver: an
        # archive resume replaces that with a timeshift URL good for minutes.
        url, key, kind = ctx.get("row_url"), ctx.get("key"), ctx.get("kind")
        if not url or key is None or not kind:
            return
        self.history.add(url, ctx.get("title") or "",
                         it.get("stream_icon") or it.get("cover"), key, kind,
                         extra=self._history_extra(kind, it,
                                                   ctx.get("title") or ""))

    def manage_cast(self) -> None:
        """Reopen the cast panel on the session that is already running.

        Same dialog, but it starts nothing and changes nothing by itself:
        the device list is the remembered one (discovery disconnects every
        device to start again, which is the last thing a running cast needs),
        the tracks are the ones playing, and casting from here picks up where
        the TV has got to rather than at the beginning.
        """
        ctx = self._cast_ctx or {}
        if not ctx.get("url"):
            # Nothing recorded to reopen on. Better the ordinary panel than
            # one that would cast an empty address.
            log.info("cast: no address recorded for the running cast")
            return
        CastDialog(self, ctx.get("url") or "", ctx.get("title") or "",
                   self._local_codecs(), 0, self.cast.position(),
                   ctx.get("tracks") or {}, probe=False,
                   source=ctx.get("source"), managing=True,
                   chosen=(ctx.get("audio"), ctx.get("subs")),
                   live=ctx.get("kind") == "live").exec()

    def _cast_volume(self, level: float) -> None:
        """Set the TV's volume. Off the UI thread: it is a message to a device
        on the network, and nothing here should wait for it."""
        if getattr(self.cast, "active", None) is None:
            return
        self._cast_level = int(round(level * 100))
        if getattr(self, "_cast_muted", False) and level > 0:
            # Reaching for the volume while it is muted means you want to
            # hear it - leaving the mute on would make the slider do nothing.
            self._toggle_cast_mute()
        threading.Thread(target=self.cast.set_volume, args=(level,),
                         daemon=True).start()

    def _toggle_cast_mute(self) -> None:
        """Silence the TV, and say so on the slider.

        A slider still sitting at half while nothing comes out of the
        television is the control disagreeing with itself. It goes to zero
        with the mute and comes back to where it was when the sound does -
        the level itself is never changed, so unmuting needs no guess.
        """
        if getattr(self.cast, "active", None) is None:
            return
        self._cast_muted = not getattr(self, "_cast_muted", False)
        if self._cast_muted:
            self._cast_level = self.cast_bar_vol.value()
        self.cast_bar_mute.setIcon(cast_strip_icon(
            "muted" if self._cast_muted else "volume", P["text"]))
        # Without the guard the move would be read as someone setting the
        # volume to zero, which is a different thing and would lose the level.
        self.cast_bar_vol.blockSignals(True)
        self.cast_bar_vol.setValue(
            0 if self._cast_muted else getattr(self, "_cast_level", 50))
        self.cast_bar_vol.blockSignals(False)
        threading.Thread(target=self.cast.set_muted,
                         args=(self._cast_muted,), daemon=True).start()

    def _show_cast_volume(self) -> None:
        """Put the TV's own level on the slider, so it starts where the
        television actually is rather than where the app guessed."""
        try:
            level, muted = self.cast.volume()
        except Exception:
            return
        self._cast_muted = muted
        self._cast_level = int(round(level * 100))
        self.cast_bar_vol.blockSignals(True)
        self.cast_bar_vol.setValue(0 if muted else self._cast_level)
        self.cast_bar_vol.blockSignals(False)
        self.cast_bar_mute.setIcon(cast_strip_icon(
            "muted" if muted else "volume", P["text"]))

    def _cast_tracks_menu(self) -> None:
        """Offer another audio track or subtitle while the cast runs.

        There is no switching a track in place - the stream is built again,
        from where the TV has got to, which is the difference between
        changing the subtitles and starting the film over.

        Every subtitle in the stream is offered. There used to be a libass
        question here, because a text subtitle had to be drawn into the
        picture; it now travels beside it as WebVTT, which any ffmpeg
        writes.
        """
        ctx = self._cast_ctx or {}
        tracks = ctx.get("tracks") or {}
        audio = tracks.get("audio") or []
        subs = tracks.get("subtitle") or []
        # Kept for the test that opens this menu: how many subtitles were
        # actually offered, which is the thing that used to be filtered.
        self._last_menu_subs = len(subs)
        menu = QMenu(self)
        menu.addAction(tr("cast_title"), self.manage_cast)
        menu.addSeparator()
        cur_a = (ctx.get("audio") or {}).get("index")
        cur_s = (ctx.get("subs") or {}).get("index")

        def entry(parent, label, checked, chooser):
            act = parent.addAction(label)
            act.setCheckable(True)
            act.setChecked(checked)
            act.triggered.connect(chooser)

        if audio:
            am = menu.addMenu(tr("cast_audio"))
            entry(am, tr("cast_track_default"), cur_a is None,
                  lambda: self._recast_with(None, ctx.get("subs")))
            for t in audio:
                entry(am, self._track_label(t), t["index"] == cur_a,
                      lambda _c=False, t=t: self._recast_with(t,
                                                              ctx.get("subs")))
        if subs:
            sm = menu.addMenu(tr("cast_subtitles"))
            entry(sm, tr("cast_subs_off"), cur_s is None,
                  lambda: self._recast_with(ctx.get("audio"), None))
            for t in subs:
                entry(sm, self._track_label(t), t["index"] == cur_s,
                      lambda _c=False, t=t: self._recast_with(ctx.get("audio"),
                                                              t))
        # The picture setting belongs here too. It is remembered per device,
        # so a channel that did need scaling leaves it set for everything
        # after it - and having to reopen the panel to put it back would be
        # the wrong place to keep it.
        current = self._cast_quality()
        # What the setting is doing to THIS stream. It is a ceiling, so an SD
        # or HD channel passes untouched while the ceiling still reads 720p -
        # and a menu that only shows the setting looks like it is scaling
        # everything.
        older = menu.addAction(
            tr("cast_older_device", name=self._cast_device or ""))
        older.setCheckable(True)
        older.setChecked(current != "original")
        older.triggered.connect(
            lambda on: self._set_cast_quality("older" if on else "original"))
        height = ctx.get("height") or 0
        fps = ctx.get("fps") or 0.0
        if height:
            from ..providers.chromecast import ChromecastManager as _CM
            adapted = _CM._needed_quality(current, height, fps) != "original"
            now = f"{height}p{fps:g}" if fps else f"{height}p"
            shown = menu.addAction(
                f"{now} → {_CM.quality_label(current, height, fps)}"
                if adapted else now)
            shown.setEnabled(False)
        menu.exec(self.cast_bar_tracks.mapToGlobal(
            self.cast_bar_tracks.rect().bottomLeft()))

    def _cast_timeshift_menu(self) -> None:
        """The archive, for what is playing on the TV.

        The player's own timeshift menu, pointed at the receiver instead of at
        mpv: go back to live, start the programme that is on from its
        beginning, browse what has been, or simply wind back a while. The
        archive does not care who is watching it.
        """
        ctx = self._cast_ctx or {}
        it = ctx.get("item")
        if not ctx.get("archive") or not it:
            return
        m = QMenu(self)
        m.addAction(tr("ts_go_live"), self._cast_go_live)
        m.addSeparator()
        # Where the picture is now, so a step back is a step back from THERE
        # and not from live - winding back twice has to go twice as far.
        at = self._cast_moment()
        prog = self.xmltv.current_programme(it)
        if prog:
            m.addAction(
                tr("ts_watch_from_start_named", title=prog["title"]),
                lambda: self._cast_to_moment(
                    datetime.fromtimestamp(prog["start_timestamp"])))
        m.addAction(tr("ts_browse_past"),
                    lambda: self._open_catchup_dialog(
                        it, on_pick=lambda p: self._cast_to_moment(
                            datetime.fromtimestamp(p["start_timestamp"]))))
        m.addSeparator()
        eff_min = self._effective_ts_minutes(it)
        for mins, dur_key in self.TIMESHIFT_STEPS:
            if mins > eff_min:
                break
            m.addAction(tr("ts_go_back", t=tr(dur_key)),
                        lambda mins=mins: self._cast_to_moment(
                            at - timedelta(minutes=mins)))
        note = m.addAction(tr("ts_archive_depth", n=self._timeshift_days(it)))
        note.setEnabled(False)
        m.exec(self.cast_bar_ts.mapToGlobal(
            self.cast_bar_ts.rect().bottomLeft()))

    def _cast_moment(self):
        """The broadcast moment the TV is showing.

        Not the clock. Every pause leaves the picture that much further
        behind the broadcast and it never catches up again - the recording
        simply carries on from where it was held - so the strip said LIVE
        while showing something ten minutes old.
        """
        began = (self._cast_ctx or {}).get("archive_from")
        if began is not None:
            return began + timedelta(seconds=float(self.cast.position() or 0.0))
        behind = getattr(self, "_cast_behind", 0.0)
        if getattr(self, "_cast_paused_at", None) is not None:
            # Still held: the gap is growing as we speak.
            behind += (datetime.now() - self._cast_paused_at).total_seconds()
        return datetime.now() - timedelta(seconds=behind)

    def _cast_to_moment(self, when) -> None:
        """Point the cast at *when*, within what the archive actually holds."""
        ctx = self._cast_ctx or {}
        it = ctx.get("item") or {}
        now = datetime.now()
        floor = now - timedelta(minutes=self._effective_ts_minutes(it))
        # A minute inside the live edge: the archive is written as it goes and
        # the newest minute is not there yet.
        when = max(floor, min(when, now - timedelta(minutes=1)))
        self._cast_paused_at = None
        self.cast_bar_pause.setIcon(cast_strip_icon("pause", P["text"]))
        self._cast_from_archive(when)

    def _cast_go_live(self) -> None:
        """Back to the live edge, off the archive."""
        ctx, device = self._cast_ctx or {}, self._cast_device
        url = ctx.get("row_url")
        if not device or not url:
            return
        log.info("cast: back to the live edge")
        ctx.update(url=url, source=ctx.get("row_source") or url,
                   archive_from=None)
        self._cast_paused_at = None
        self._cast_behind = 0.0
        self.cast_bar_pause.setIcon(cast_strip_icon("pause", P["text"]))
        title = ctx.get("title") or "dopeIPTV"
        run_async(
            self.pool,
            lambda: self.cast.cast(device, url, title, self._local_codecs(),
                                   source=ctx.get("row_source") or url,
                                   quality=self._cast_quality(),
                                   height=ctx.get("height") or 0,
                                   fps=ctx.get("fps") or 0.0,
                                   # Still recorded, so it can still be
                                   # paused. Going back to live used to hand
                                   # the channel straight to the receiver,
                                   # and the pause button quietly vanished.
                                   dvr=bool(ctx.get("archive"))),
            lambda _n: self.show_cast_strip(device, title),
            lambda msg: self._error(tr("cast_failed", msg=msg)))

    def _cast_continue_archive(self) -> None:
        """Keep a timeshifted cast going past the end of its playlist.

        The archive is served as a finite playlist - it stops where it caught
        up with now, and the receiver reports the end of the media. In the
        player that is invisible, because the player asks for more as it
        goes; a cast cannot, so watching an hour behind live used to end
        without warning whenever the requested stretch ran out.

        So notice the end and ask for the next stretch, from exactly where
        the last one stopped.
        """
        ctx = self._cast_ctx or {}
        if not ctx.get("archive_from") or not self._cast_device:
            return
        if getattr(self, "_cast_paused_at", None) is not None:
            return                      # paused on purpose, not run out
        # ONLY a stream the receiver says has ended. Buffering is not an
        # ending, and treating it as one was doing the damage: a stretch
        # that was playing, eleven seconds in and pausing to fill its
        # buffer, got killed and asked for again - and the new one paid the
        # whole start-up cost afresh, seek discard and all, only to buffer
        # again a little later. The restarts were ours.
        #
        # A slow stretch is slow whoever asks for it. There is nothing to
        # rescue it with, and the picture comes back on its own.
        if not str(getattr(self.cast, "state", "")
                   ).startswith("IDLE/FINISHED"):
            return
        # Once. Loading the next stretch takes a few seconds, during which
        # the receiver still reports the end of the last one.
        now = time.monotonic()
        if now - getattr(self, "_cast_continued", 0.0) < 20:
            return
        self._cast_continued = now
        at = self._cast_moment()
        log.info("cast: the archive ran out at %s - asking for the next of it",
                 at.strftime("%H:%M:%S"))
        self._cast_from_archive(at)

    def _show_cast_progress(self) -> None:
        """Put the receiver's own position on the strip.

        Hidden for a broadcast: there is no end to measure against, so a bar
        showing how far through it you are would be showing nothing.
        """
        bar = getattr(self, "cast_bar_seek", None)
        if bar is None:
            return
        span = self._cast_timeline()
        dur = float(getattr(self.cast, "duration", 0.0) or 0.0)
        # A broadcast the converter is recording can be held even with no
        # catch-up behind it, and then there is no timeline to scrub - but
        # how far behind live it is still matters, and while it is paused
        # that counter is the only thing saying so.
        held = bool(self._cast_device) and dur <= 0 and self.cast.bridged()
        on = bool(self._cast_device) and (span is not None or dur > 0)
        bar.setVisible(on)
        self.cast_bar_time.setVisible(on or held)
        # The ticker runs for the whole of a cast, not only where there is a
        # bar to move: a timeshifted channel has no length, and is exactly
        # the thing that needs watching for the end of its playlist.
        if not self._cast_device:
            self._cast_tick.stop()
            bar.set_segments([])
            return
        if not self._cast_tick.isActive():
            self._cast_tick.start()
        self._cast_continue_archive()
        if span is not None:
            self._show_cast_timeline(*span)
            return
        # Not a broadcast, so nothing on the groove. The programme blocks were
        # only ever cleared when the cast ENDED, so casting a film straight
        # after a timeshifted channel left last night's evening drawn along a
        # bar that is now measuring a film.
        bar.set_segments([])
        if held:
            self._show_cast_edge(self._cast_moment().timestamp(), time.time())
            return
        self.cast_bar_live.setVisible(False)
        if not on:
            return
        pos = min(float(self.cast.position() or 0.0), dur)
        # Not while it is being dragged: the handle belongs to the hand
        # holding it until it is let go.
        if not bar.isSliderDown():
            bar.setValue(int(pos / dur * 1000))
        self.cast_bar_time.setText(
            f"{self._fmt_hms(pos)} / {self._fmt_hms(dur)}")

    # How much of a channel's archive the strip's bar spans. The archive
    # itself can be a week deep, and a week across two hundred pixels is a
    # bar where every click is half an hour out. Longer jumps are what the
    # menu's "go back a day" is for; this is for finding your way around the
    # evening.
    CAST_TIMELINE_MIN = 360

    def _cast_timeline(self) -> tuple[float, float] | None:
        """The stretch of broadcast the bar spans, as (start, span) seconds -
        or None when what is playing is not a broadcast."""
        ctx = self._cast_ctx or {}
        if not ctx.get("archive") or not self._cast_device:
            return None
        try:
            depth = self._effective_ts_minutes(ctx.get("item") or {}) or 0
        except Exception:
            return None
        depth = min(depth, self.CAST_TIMELINE_MIN)
        if depth <= 0:
            return None
        now = time.time()
        return now - depth * 60, depth * 60.0

    def _cast_time_at(self, frac: float) -> str:
        """What is at *frac* of the way along the bar.

        A film says how far in it is. A broadcast says the time of day, and
        what was on then - which is the whole point of a timeshift bar, and
        is answered before you click rather than after.
        """
        span = self._cast_timeline()
        if span is not None:
            start, width = span
            when = start + frac * width
            label = time.strftime("%H:%M", time.localtime(when))
            prog = self._cast_programme_at(when)
            return f"{label} · {prog}" if prog else label
        dur = float(getattr(self.cast, "duration", 0.0) or 0.0)
        return self._fmt_hms(frac * dur) if dur > 0 else ""

    def _cast_programme_at(self, when: float) -> str:
        ctx = self._cast_ctx or {}
        item = ctx.get("item")
        if item is None:
            return ""
        for p in self.xmltv.programmes_in(item, when - 1, when + 1) or []:
            if p["start_timestamp"] <= when < p["stop_timestamp"]:
                return p.get("title") or ""
        return ""

    def _show_cast_timeline(self, start: float, span: float) -> None:
        """The archive, drawn as the evening it is.

        A broadcast has no length to run a position bar against, so the strip
        used to show nothing for the very channels that can be moved around
        in most freely. What it spans instead is time itself: programme
        boundaries along the groove, the moment being shown as the handle,
        and how far behind live that is - which is also what says, without a
        word, that a channel is paused.
        """
        at = self._cast_moment().timestamp()
        now = time.time()
        segs = []
        for p in self.xmltv.programmes_in(
                self._cast_ctx.get("item") or {}, start, now) or []:
            a = max(0.0, (p["start_timestamp"] - start) / span)
            b = min(1.0, (p["stop_timestamp"] - start) / span)
            label = "%s–%s" % (
                time.strftime("%H:%M", time.localtime(p["start_timestamp"])),
                time.strftime("%H:%M", time.localtime(p["stop_timestamp"])))
            segs.append((a, b, p.get("title") or "", label))
        self.cast_bar_seek.set_segments(segs)
        if not self.cast_bar_seek.dragging:
            frac = max(0.0, min(1.0, (at - start) / span))
            self.cast_bar_seek.setValue(int(frac * 1000))
        self._show_cast_edge(at, now)

    def _show_cast_edge(self, at: float, now: float) -> None:
        """How far behind the broadcast the picture is, in the words the
        player already uses for it: a white dot at the live edge, a red
        counter behind it, and the red button back.

        Anything else asked the user to learn a second vocabulary for the
        same thing - and a grey "LIVE" that stayed put whether or not it was
        true taught them nothing at all.
        """
        behind = max(0.0, now - at)
        paused = getattr(self, "_cast_paused_at", None) is not None
        live = not paused and behind < 5
        held = "⏸ " if paused else ""
        if live:
            edge = "● LIVE"
        elif paused and behind < 1:
            edge = "⏸ PAUSED"
        elif behind < 60:
            edge = f"{held}−{int(behind)}s"
        else:
            edge = f"{held}−{self._fmt_hms(behind)}"
        prog = self._cast_programme_at(at)
        self.cast_bar_time.setText(f"{edge} · {prog}" if prog else edge)
        self.cast_bar_time.setStyleSheet(
            "font-size:11px; font-weight:700;" if live else
            f"font-size:11px; font-weight:700; color:{P['rec']};")
        self.cast_bar_live.setVisible(not live)

    def _cast_seek_released(self) -> None:
        span = self._cast_timeline()
        if span is not None:
            start, width = span
            at = start + self.cast_bar_seek.value() / 1000 * width
            self._cast_to_moment(datetime.fromtimestamp(at))
            return
        dur = float(getattr(self.cast, "duration", 0.0) or 0.0)
        if dur > 0:
            self._cast_seek(self.cast_bar_seek.value() / 1000 * dur)

    def _cast_seek(self, to: float) -> None:
        """Move to *to* seconds into what is playing on the TV.

        A file the receiver fetched itself it can seek on its own. What the
        converter serves it cannot: that is a pipe with no length and no
        index, so moving inside it means building the stream again from the
        new point - which is what the converter is already good at, and the
        same thing changing a subtitle does.
        """
        log.info("cast: moving to %s", self._fmt_hms(to))
        ctx = self._cast_ctx or {}
        # A playlist can be seeked where a single long response cannot: every
        # segment is still there and named, so the receiver jumps within it
        # by itself. Rebuilding the stream for that started the film over,
        # which is what dragging the bar appeared to do.
        # A playlist can be seeked, but only into what has been made. The
        # converter runs a little ahead of the picture and no further, so a
        # jump forward lands at the end of what exists - which on the
        # television looks like it moved on by a minute and stopped. Going
        # BACK is free, because those segments are all still there.
        playlist = getattr(getattr(self.cast, "bridge", None), "hls", False)
        if playlist and to > self.cast.position() + 5:
            playlist = False            # rebuild from there instead
        if self.cast.bridged() and not playlist:
            self._recast_with(ctx.get("audio"), ctx.get("subs"), start=to)
        else:
            threading.Thread(target=lambda: self.cast.seek(to),
                             daemon=True).start()

    def _cast_quality_key(self) -> str:
        return f"cast_quality_{self._cast_device or ''}"

    def _cast_quality(self) -> str:
        # Through the normaliser: a device set before the question became one
        # checkbox is still remembered as "720p30", which is no longer an
        # answer that exists.
        from ..providers.cast_bridge import normalise_quality
        return normalise_quality(
            str(self.settings.value(self._cast_quality_key(), "original")
                or "original"))

    def _set_cast_quality(self, key: str) -> None:
        """Change what this device is allowed, and show it straight away."""
        if key == self._cast_quality():
            return
        self.settings.setValue(self._cast_quality_key(), key)
        log.info("cast: %s is now set to %s", self._cast_device, key)
        ctx = self._cast_ctx or {}
        self._recast_with(ctx.get("audio"), ctx.get("subs"))

    @staticmethod
    def _track_label(t: dict) -> str:
        bits = [b for b in (t.get("lang"), t.get("title")) if b]
        bits.append(t.get("codec") or "?")
        return " · ".join(bits)

    def _recast_with(self, audio, subs, start: float | None = None) -> None:
        """Cast the same title again with different tracks, from where the TV
        is now - or from *start*, when it is being moved somewhere else."""
        ctx, device = self._cast_ctx or {}, self._cast_device
        url = ctx.get("url")
        if not device or not url:
            return
        at = self.cast.position() if start is None else start
        ctx.update(audio=audio, subs=subs)
        log.info("cast: switching tracks and picking up at %d s", at)
        title = ctx.get("title") or "dopeIPTV"
        run_async(
            self.pool,
            lambda: self.cast.cast(device, url, title, self._local_codecs(),
                                   audio, subs, at,
                                   ctx.get("duration") or 0.0,
                                   False, ctx.get("source"),
                                   self._cast_quality(),
                                   ctx.get("height") or 0,
                                   ctx.get("fps") or 0.0,
                                   # A channel with an archive stays
                                   # recorded here. Changing the audio track
                                   # is no reason to lose the pause button.
                                   dvr=bool(ctx.get("archive"))),
            lambda _n: self.show_cast_strip(device, title),
            lambda msg: self._error(tr("cast_failed", msg=msg)))

    # How close to live the archive can be read from. Only the timeshift
    # menu comes through here now - going back an hour, picking a programme
    # out of the guide - and the panel cannot serve the minute it is still
    # writing.
    ARCHIVE_LAG = timedelta(seconds=75)

    def _toggle_cast_pause(self) -> None:
        """Hold the picture on the TV, and let it go again.

        One mechanism, for a film and for a broadcast alike: the receiver
        stops, and whatever is feeding it waits. A film the receiver fetched
        itself holds its own place. A broadcast is coming through the
        converter, which goes on recording into its spool while the
        television sits still - so play carries on at the very next frame.

        Nothing is asked of the provider either way. Buying the missing
        minutes back from its catch-up afterwards is what eight rounds of
        this foundered on, and the button is not offered at all where that
        would be the only way to answer it.
        """
        if self._cast_paused_at is not None:
            # However long that was, the picture is now that much further
            # behind the broadcast, and stays there.
            self._cast_behind = getattr(self, "_cast_behind", 0.0) + (
                datetime.now() - self._cast_paused_at).total_seconds()
            self._cast_paused_at = None
            self.cast_bar_pause.setIcon(cast_strip_icon("pause", P["text"]))
            threading.Thread(target=self.cast.resume, daemon=True).start()
            return
        self._cast_paused_at = datetime.now()
        self.cast_bar_pause.setIcon(cast_strip_icon("play", P["text"]))
        # However much room the user has allowed for it.
        try:
            gb = float(self.settings.value("cast_pause_gb", 4.5) or 4.5)
        except (TypeError, ValueError):
            gb = 4.5
        self.cast.bridge.cap = int(max(0.1, gb) * 10**9)
        threading.Thread(target=self.cast.pause, daemon=True).start()

    def _cast_from_archive(self, paused_at) -> None:
        """Cast this channel from a moment in the provider's catch-up.

        This is the timeshift menu's doing - going back an hour, picking a
        programme out of the guide. Pausing does not come through here: it is
        held on this machine, which is the only way it ever worked.
        """
        ctx, device = self._cast_ctx or {}, self._cast_device
        sid = ctx.get("sid")
        if sid is None or not device:
            return
        # The TRANSPORT STREAM, through the converter - not the panel's HLS
        # wrapper of the same archive. This is decided by the logs, not by
        # preference: across every attempt, each cast that actually played
        # the right content read the .ts (11:36 played a two-minute-old
        # window correctly), and every freeze-after-one-second was the
        # .m3u8 near live - including a minute over four minutes old, which
        # sank the theory that age was the problem. The panel's HLS archive
        # endpoint is simply broken for short windows; its .ts endpoint is
        # not.
        #
        # The .ts is one stream, not a segment list, so no window arithmetic
        # is needed: ask from the paused minute with generous room ahead,
        # the panel serves what exists and closes at the write head, ffmpeg
        # ends cleanly, and the continuation asks for the next stretch from
        # the exact moment the picture stopped. What ruined this path before
        # is fixed elsewhere: the retry no longer spawns a second ffmpeg,
        # the bridge no longer holds half a megabyte back, and the reconnect
        # flags - which re-request a closed window from its START on panels
        # that ignore Range, replaying television at random - are off for
        # timeshift input.
        paused_at = min(paused_at, datetime.now() - self.ARCHIVE_LAG)
        # Addressed by the minute; the seconds ride along as ffmpeg's own
        # starting offset, so nothing already watched is replayed.
        minute = paused_at.replace(second=0, microsecond=0)
        offset = (paused_at - minute).total_seconds()
        behind = max(1, int((datetime.now() - minute).total_seconds() // 60))
        cands = self.client.timeshift_urls(sid, minute, behind + 240) or []
        url = next((c for c in cands if ".ts" in c), "")
        source = url or next(iter(cands), "")
        url = url or source
        if not url:
            return
        # The moment itself, not how it was worked out - it is the one thing
        # that can be checked against what the picture actually shows.
        log.info("cast: resuming %s from %s (%d min behind live)",
                 ctx.get("title") or "", paused_at.strftime("%H:%M:%S"),
                 behind)
        title = ctx.get("title") or "dopeIPTV"
        # The session now lives at the archive address. Recording it keeps the
        # strip's own panel pointing at what is actually playing - it opened
        # on an empty address otherwise, and cast it. archive_from is what a
        # later pause measures against, so this cast can be paused again
        # without losing the shift it already has.
        ctx.update(url=url, source=source, archive_from=minute)
        # Everything the live cast was doing, the archive cast does too. It is
        # the same channel at the same size, so the device's picture ceiling,
        # the chosen tracks and the known codecs all still apply - dropping
        # them here handed an older receiver the full-size picture it had just
        # been spared, halfway through watching.
        run_async(
            self.pool,
            lambda: self.cast.cast(device, url, title, self._local_codecs(),
                                   ctx.get("audio"), ctx.get("subs"),
                                   start=offset, source=source,
                                   quality=self._cast_quality(),
                                   height=ctx.get("height") or 0,
                                   fps=ctx.get("fps") or 0.0,
                                   # Recorded here, so a programme picked out
                                   # of the archive can be held too.
                                   dvr=True),
            lambda _n: self.show_cast_strip(device, title),
            lambda msg: self._error(tr("cast_failed", msg=msg)))

    def _save_cast_position(self) -> None:
        """Keep the point the TV reached, so it resumes there next time.

        The receiver is the only thing that knows where the film has got to,
        and it stops knowing the moment the cast ends - so this has to happen
        before anything is torn down.
        """
        ctx = getattr(self, "_cast_ctx", None) or {}
        group, key = ctx.get("group"), ctx.get("key")
        if not group or key is None:
            return
        pos, dur = self.cast.position(), self.cast.duration
        # Under a minute in, the store treats a position as "at the start" and
        # DROPS whatever was saved before. A cast that had barely begun would
        # therefore wipe the resume point the film already had - so a position
        # too small to be worth keeping is a reason to leave the store alone,
        # not to write to it.
        if pos <= 60 or dur <= 0:
            return
        log.info("cast: keeping the position, %d s into %d", pos, dur)
        self.resume.record(group, key, pos, dur, item=ctx.get("item"))

    def _end_cast(self, why: str) -> None:
        """Stop a running cast and take the strip down.

        Stopping talks to the receiver over the network, so it runs off the UI
        thread: nothing here - least of all starting playback - may wait for a
        TV to answer.
        """
        cc = getattr(self, "cast", None)
        if cc is not None and getattr(cc, "active", None) is not None:
            self._save_cast_position()
        self.show_cast_strip(None)
        if cc is None or getattr(cc, "active", None) is None:
            return
        log.info("cast: stopping - %s", why)
        threading.Thread(target=cc.stop, daemon=True).start()

    def stop_local_playback_for_cast(self) -> None:
        """Free the local stream when a cast starts. The Chromecast pulls the
        URL itself (one connection from the device), so on a single-connection
        account leaving the embedded player running too would be a second
        connection the provider refuses. Called by the cast dialog on a
        successful cast. Returns whether anything was actually stopped, so
        the caller can let the provider notice before asking for the stream
        again - these panels keep counting a session for a few seconds after
        the socket closes."""
        p = getattr(self, "player", None)
        if p is not None and getattr(p, "current_url", None):
            try:
                p.stop()
                return True
            except Exception:
                pass
        return False

    def _autoplay_preview(self) -> bool:
        # Default off: a live channel plays on double-click (the desktop
        # standard), so single-clicking or arrowing through the list doesn't
        # change channel by accident. Users who want TV-style single-click /
        # arrow-key zapping can turn it back on in Settings.
        return self.settings.value("autoplay_preview", "false") == "true"

    def _play_preview(self) -> None:
        it = self.list_model.item_at(self.listw.currentIndex().row())
        if (not it or self.series_ctx or not self.player
                or self.playback_mode() != "embedded"):
            return
        kind = self._content_kind()
        # Preview live channels: the TV / Favorites channel lists, plus live
        # rows inside History (a movie/episode/recording row there is not a
        # live channel and must not auto-play).
        history_live = kind == "history" and it.get("_kind") == "live"
        # Belt: never preview a history row whose stored URL is VOD/series,
        # whatever its kind tag claims (old entries could be mis-kinded).
        if history_live:
            u = it.get("_url") or ""
            if "/movie/" in u or "/series/" in u:
                return
        if kind not in ("live", "fav") and not history_live:
            return
        # The grouped "All favorites" view shares kind 'fav' for every row.
        # Only actual channel rows may auto-preview: a movie/series row there
        # would otherwise be crammed into a LIVE url built from its vod/series
        # id - playing garbage and filing the movie under TV channels in
        # History.
        row_kind = it.get("_kind") or it.get("_ekind")
        if row_kind not in (None, "live", "fav"):
            return
        if history_live:
            # Rebuild a fresh live URL from the stored stream_id (the snapshot's
            # _url may be stale), falling back to that snapshot when the row
            # predates stream_id enrichment.
            sid = it.get("stream_id")
            if sid is not None:
                fmt = self.settings.value("stream_format", "ts")
                url = self.client.live_url(sid, fmt)
            else:
                url = it.get("_url")
            title = it.get("name") or "dopeIPTV"
        else:
            url, title = self._stream_for(it)
        if not url:
            return
        if self.player.current_url == url:
            return
        if not self._guard_stream_switch(url, title):
            return
        # The preview path never reaches _start_playback, so it must carry the
        # same side effects itself: the multiview-conflict question (close it
        # and free its connections, or keep both) and the History entry -
        # with autoplay-preview on, this IS how channels get played.
        self._stop_cast_for_local_playback()
        self._maybe_close_multiview_for_playback()
        if kind != "history":
            self.history.add(
                url, title, it.get("stream_icon"), self._item_key(it), "live",
                extra={"stream_id": it.get("stream_id"),
                       "num": it.get("num"),
                       "tv_archive": it.get("tv_archive"),
                       "tv_archive_duration": it.get("tv_archive_duration")})
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
        # A preview is always a fresh live edge - clear any catch-up state so
        # the seek mode resolves to the live timeline (or plain live), not a
        # leftover VOD/timeline-scrub state. The behind-live offset is
        # per-channel, so reset it (and any pending pause) here too - otherwise
        # previewing another timeshift channel while paused inherits the old
        # channel's "-Ns behind live" value.
        self._playing_catchup = False
        self._ts_catchup_program = False
        self._ts_program_start = None
        self._ts_program_stop = None
        self._ts_program_title = None
        self._ts_segment_start = None
        self._ts_live_offset = 0.0
        self._pause_started = None
        if self.player.play(url, title):
            self.wake.acquire(f"Playing {title}")
        self._apply_seek_mode(it, "live")
        # Refresh the poster overlay glyph (play -> pause for a timeshift
        # channel, -> stop for plain live). Unlike _start_playback, the preview
        # path doesn't otherwise call this, so the button stayed on 'play'.
        self._apply_play_icon()

    def playback_mode(self) -> str:
        """The in-app embedded player is the only playback surface. Without
        libmpv we fall back to launching an external mpv window per channel."""
        return "embedded" if self.player else "external"

    # Content kinds whose playback position is worth remembering/resuming.
    _RESUMABLE = ("movie", "episode", "recording", "local")

    # How far back the live timeline spans (minutes). A window, not the whole
    # multi-day archive, so a small drag stays fine-grained; matches the 6 h
    # upcoming-programme window. Deeper access stays in the ◀◀ menu.
    _TS_TIMELINE_MAX_MIN = 360

    # Cache keys that are large and/or frequently rewritten - moved out of the
    # shared settings so writing them never rewrites the small settings file
    # (and, conversely, so a small settings write never has to rewrite them).
    _CACHE_KEYS = (
        "tmdb_poster_cache_v3", "tmdb_byid_cache_v1", "tmdb_matcher_version",
        "tmdb_person_cache", "tmdb_person_id_cache",
        "trakt_watched_cache", "trakt_watchlist_cache",
    )

    def _open_cache_settings(self, shared: QSettings) -> QSettings:
        """Dedicated file for the big TMDB/Trakt caches. Keeping multi-MB blobs
        in the shared settings meant QSettings' auto-sync rewrote all of them
        whenever any small value (volume, resume, window state) changed - a
        100-400ms main-thread stall that hitched video. Moving them here keeps
        the shared file tiny so those frequent writes sync in ~1ms, and the
        caches (rewritten on their own worker threads) no longer bloat it.
        Migrate any existing cache keys out of the shared file once."""
        cs = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                       ORG, "cache")
        try:
            if cs.value("_migrated") != "1":
                moved = False
                for k in self._CACHE_KEYS:
                    if shared.contains(k):
                        cs.setValue(k, shared.value(k))
                        shared.remove(k)
                        moved = True
                cs.setValue("_migrated", "1")
                cs.sync()
                if moved:
                    shared.sync()
        except Exception as e:
            log.warning("cache settings migration skipped: %s", e)
        return cs

    def _open_resume_settings(self, shared: QSettings) -> QSettings:
        """Resume positions get their own small settings file. They are written
        every ~12s during playback; keeping them in the shared app settings -
        which also holds the multi-MB TMDB/Trakt caches - meant each write
        dirtied that file, so QSettings' periodic auto-sync rewrote the whole
        thing: a 100-400ms main-thread stall that backed up mpv's render loop
        and dropped a batch of frames every ~12s. A dedicated file syncs in
        ~1ms and never dirties the big one. Migrate any existing
        resume_positions* keys out of the shared file once, so the user's
        continue-watching survives the move."""
        rs = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                       ORG, "resume")
        try:
            if rs.value("_migrated") != "1":
                moved = False
                for k in shared.allKeys():
                    if k.startswith("resume_positions"):
                        rs.setValue(k, shared.value(k))
                        shared.remove(k)
                        moved = True
                rs.setValue("_migrated", "1")
                rs.sync()
                if moved:
                    shared.sync()
        except Exception as e:
            log.warning("resume settings migration skipped: %s", e)
        return rs

    def _save_resume_position(self) -> None:
        """Remember how far into the current title the user got, so it can be
        resumed later. Positions near the very start or end are dropped."""
        if not (self.player and self.player.current_url and self._playing_key
                and self._playing_group in ("vod", "episode", "rec",
                                            "local")):
            return
        lp = self._last_playback or {}
        self.resume.record(self._playing_group, self._playing_key,
                           self.player.playback_position(),
                           self.player.playback_duration(),
                           item=lp.get("item"),
                           series_ctx=lp.get("series_ctx"))
        self._playback_max_pct = max(self._playback_max_pct,
                                     self.player.progress_percent())

    def _play_continue_episode(self, it, player=None,
                               external: bool = False) -> None:
        """Replay a partly-watched episode from the Continue-watching list,
        using its stored series context to build the stream URL."""
        ctx = it.get("_series_ctx") or None
        saved = self.series_ctx
        self.series_ctx = ctx or saved
        try:
            # A History row carries the series context (so it replays AS an
            # episode) but no provider episode id - History stores the URL
            # it was played from, not the catalogue entry. Building a URL
            # from the missing id gave /series/user/pass/None.mp4, which is
            # not empty, so the guard below waved it through and playback
            # failed on a nonsense path. Same reasoning as _stream_for.
            url = ""
            if it.get("id") is not None:
                url = self.client.episode_url(
                    it.get("id"), it.get("container_extension"))
            if not url:
                url = it.get("_url") or ""
            if not url:
                return
            title = it.get("name") or it.get("title") or "dopeIPTV"
            if external or player == "vlc":
                launch_player(player or "mpv", url, title, self)
                return
            # No autoplay-queue from this flat list: pin an empty override so
            # _start_playback doesn't snapshot the Continue list as episodes.
            self._ep_queue_override = []
            self._ep_index_override = -1
            self._start_playback(
                url, title, it.get("stream_icon") or it.get("cover"),
                self._item_key(it), "episode", record=True, item=it)
        finally:
            self.series_ctx = saved

    def _continue_items(self, kind: str) -> list:
        """Continue-watching rows of one kind ('vod' or 'episode')."""
        return [it for it in self.resume.continue_watching()
                if it.get("_kind") == kind]

    def _remove_continue(self, it) -> None:
        """Forget a title's resume point (from the Continue-watching menu),
        then refresh the list - the category disappears once it's empty."""
        group = "episode" if it.get("_kind") == "episode" else "vod"
        self.resume.clear(group, self._item_key(it))
        cur = self.cat_list.currentItem()
        cur_cat = cur.data(Qt.ItemDataRole.UserRole) if cur else None
        if self.mode in ("vod", "series") and cur_cat == "__continue__":
            kind = "vod" if self.mode == "vod" else "episode"
            remaining = self._continue_items(kind)
            if remaining:
                self.all_items = remaining
                self._apply_filter()
            else:
                self._load_categories()

    def _resume_offset(self, key, kind: str) -> float:
        """Ask whether to resume a partly-watched title; return the start
        offset in seconds (0 to start from the beginning)."""
        pos = self.resume.saved_position(key, kind)
        if pos <= 0:
            # Nothing to offer: either never watched >1 min, watched past 95%
            # (counted as finished), or saved under another kind pre-fix.
            log.debug("resume: no saved position for %s (kind=%s)", key, kind)
            return 0.0
        return self._ask_resume(pos)

    def _ask_resume(self, pos: float) -> float:
        """Offer to continue from *pos*; returns it, or 0 to start over."""
        if pos <= 0:
            return 0.0
        idx = self._choice_dialog(
            tr("resume_title"),
            tr("resume_prompt", time=self._fmt_hms(pos)),
            [(tr("resume_continue", time=self._fmt_hms(pos)), "primary"),
             (tr("resume_restart"), "normal")])
        return pos if idx == 0 else 0.0

    # -- per-title track memory ------------------------------------------------
    # Remember the audio/subtitle track a user picked for a movie/episode/
    # recording, so replaying (typically "continue where you left off") comes
    # back with the same subtitles instead of the stream default.

    _TRACK_PREFS_MAX = 200

    def _track_prefs(self) -> dict:
        try:
            d = json.loads(str(self.settings.value("track_prefs", "") or "{}"))
            return d if isinstance(d, dict) else {}
        except (ValueError, TypeError):
            return {}

    def _on_track_selected(self, prop: str, tid) -> None:
        """A track was picked in the player's options menu: file it under the
        currently playing resumable title (live channels are skipped - their
        track layout varies per programme and aid=auto handles them)."""
        lp = getattr(self, "_last_playback", None) or {}
        kind, key = lp.get("kind"), lp.get("key")
        if kind not in self._RESUMABLE or key is None:
            return
        prefs = self._track_prefs()
        # Filed under the episode AND under its series. Picking Swedish
        # subtitles on episode 1 means you want them on episode 2, and a
        # per-episode key alone could never say that - autoplay moved on
        # and the choice was gone.
        for k in [f"{kind}:{key}"] + self._series_pref_keys(kind):
            entry = prefs.pop(k, None)
            entry = entry if isinstance(entry, dict) else {}
            entry[prop] = tid
            prefs[k] = entry        # re-insert = newest, for FIFO capping
        while len(prefs) > self._TRACK_PREFS_MAX:
            prefs.pop(next(iter(prefs)))
        self.settings.setValue("track_prefs", json.dumps(prefs))

    def _series_pref_keys(self, kind: str) -> list[str]:
        """The series-wide track-preference key for what is playing, if it
        belongs to a series at all. One entry per series, so a show is
        remembered without 200 episodes crowding out everything else."""
        if kind != "episode":
            return []
        lp = getattr(self, "_last_playback", None) or {}
        ctx = lp.get("series_ctx") or self.series_ctx or {}
        sid = ctx.get("series_id")
        return [f"series:{sid}"] if sid is not None else []

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
        # Redraw the poster overlay: without this it froze on the last playing
        # state's glyph (a plain live channel leaves it stuck on 'stop').
        self._apply_play_icon()
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
        """End-of-file from the player. For a live stream that means the
        connection dropped (a live channel never really "ends"), so reconnect
        instead of leaving it frozen on the last frame. For an episode, mark it
        watched and optionally autoplay the next one."""
        last = getattr(self, "_last_playback", None)
        if last and last.get("kind") == "live":
            if getattr(self, "_playing_catchup", False):
                # Reached the end of an archive segment. If it actually played,
                # the user has caught up to ~now, so continue at the live edge.
                # If it ended almost immediately, the provider isn't really
                # serving catch-up for this channel - say so instead of
                # silently bouncing to live.
                dur = self.player.playback_duration() if self.player else 0.0
                it = last.get("item")
                if not (dur and dur > 10):
                    self._set_status(tr("ts_archive_unavailable"),
                                     emphasis=True)
                if it is not None:
                    self.play_live_channel(it)
                return
            self._reconnect_live("eof")
            return
        if last and last.get("kind") == "local":
            # Albums play through, and so do local series - a local
            # episode is kind "local", not "episode", so it never reached
            # the episode branch below and simply stopped at every
            # episode's end.
            self._save_resume_position()
            went = self._queue_autoplay()
            log.info("eof: local file, queue autoplay -> %s "
                     "(%d entries, index %d)", went,
                     len(getattr(self, "_track_queue", []) or []),
                     getattr(self, "_track_index", -1))
            return
        if not last or last.get("kind") != "episode":
            return
        # Give the just-finished episode its watched mark (it reached the end).
        self._save_resume_position()
        self._maybe_auto_mark_watched()
        if self._autoplay_next_episode():
            self._advance_to_next_episode()

    def _play_next_episode(self) -> None:
        """The player's 'next' button - the next queued track while music is
        playing, otherwise the next episode."""
        if getattr(self, "_track_queue", None) and self._queue_step(1):
            return
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
                        item=None, catchup: bool = False) -> None:
        if not self._guard_stream_switch(url, title):
            return
        # A click that led here also armed the auto-preview timer; kill it so
        # a pending preview can't fire 350 ms later and stomp this playback.
        if hasattr(self, "_preview_timer"):
            self._preview_timer.stop()
        # Multiview holds provider connections - starting playback here while
        # its streams run would be refused on a tight connection limit. Offer
        # (once per multiview window) to close it; keeping both is fine on
        # accounts with spare connections.
        self._stop_cast_for_local_playback()
        self._maybe_close_multiview_for_playback()
        # Whether this is a catch-up/archive segment. Set here (not by callers)
        # so any normal play - including a live channel opened via play_item /
        # zap, which goes straight through _start_playback - always clears it,
        # instead of staying stuck in 'catch-up' after an archive seek.
        self._playing_catchup = catchup
        if not catchup:
            self._ts_segment_start = None
            self._ts_catchup_program = False
            self._ts_program_start = None
            self._ts_program_stop = None
            self._ts_program_title = None
            self._ts_live_offset = 0.0   # fresh tune = at the live edge
            self._pause_started = None   # per-channel: don't carry a stale pause
        # Remember where we were in whatever was playing before switching,
        # and give the outgoing title its watched mark if it earned one.
        self._save_resume_position()
        self._maybe_auto_mark_watched()
        resume_at = (self._resume_offset(key, kind)
                     if kind in self._RESUMABLE else 0.0)
        self._trakt_stop_current()
        if record and kind:
            self.history.add(url, title, icon_url, key, kind,
                             extra=self._history_extra(kind, item, title))
        if kind in ("movie", "episode"):
            self._trakt_start_for_item(kind, item)
        elif (kind == "local" and item is not None
                and not item.get("_no_scrobble")):
            # A local file scrobbles as a movie when Trakt can match the
            # cleaned title; an unmatched file just scrobbles nothing.
            self._trakt_start_for_item(
                "movie", {"name": item.get("_clean_title")
                          or item.get("name") or ""})
        self.stream_error.hide()
        # Remember the channel we're leaving so the "last channel" key can
        # bounce back to it.
        if (kind == "live" and self._playing_group == "live"
                and self._playing_item
                and self._item_key(self._playing_item) != key):
            self._prev_live_item = self._playing_item
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
        # New stream: reset the failure-diagnosis guard (a definitive-probe or
        # the full diagnosis may show once per playback attempt).
        self._diag_gen = getattr(self, "_diag_gen", 0) + 1
        self._diag_shown = False
        self._sync_player_buttons()
        self._playing_key = key
        self._playing_group = {"live": "live", "movie": "vod",
                               "episode": "episode",
                               "recording": "rec",
                               "local": "local"}.get(kind)
        self.listw.viewport().update()
        self.setWindowTitle(title or self._base_title)
        self._set_status(tr("status_playing", title=title))
        log.info("play: embedded path kind=%s player=%s %s",
                 kind, "yes" if self.player else "NO", title)
        if self.player:
            self.rec.finish_all_inplayer("channel changed")
            self.player.show()
            self.player.set_overlay_info(title)
            self._apply_audio_visuals(url)
            # Music bookkeeping - the detail panel and the play queue. This
            # MUST stay inside the embedded branch: written as a top-level
            # "if kind == local" it closed this block, so everything below
            # (including player.play()) fell into the else and every stream
            # opened in an external mpv window.
            if kind == "local":
                if item is not None and self._is_audio(url):
                    # Show THIS track - a queue rolling on by itself must
                    # not leave the panel on the first one.
                    try:
                        self._show_detail(item)
                    except Exception as e:
                        log.debug("music detail refresh failed: %s", e)
                # Video too, not just music: without a queue position a
                # local episode had no "next" and every series stopped
                # dead at the end of each episode.
                self._place_in_queue(url, title, item)
                self._sync_queue_buttons()
            # Replay with the same audio/subtitle tracks the user picked last
            # time for this title (one-shot; play() consumes them).
            if kind in self._RESUMABLE:
                prefs = self._track_prefs()
                # This exact title first; failing that, whatever was chosen
                # for the series, so the next episode keeps the subtitles.
                pref = prefs.get(f"{kind}:{key}")
                if not isinstance(pref, dict):
                    pref = next((prefs[k] for k in self._series_pref_keys(kind)
                                 if isinstance(prefs.get(k), dict)), None)
                if isinstance(pref, dict):
                    self.player.set_track_prefs(pref.get("aid"),
                                                pref.get("sid"))
            if self.player.play(url, title, start=resume_at):
                self.wake.acquire(f"Playing {title}")
            else:
                log.warning("embedded play() refused %s - falling back to an "
                            "external mpv window", url)
                self.player.hide()
                launch_player("mpv", url, title, self)
        else:
            # No embedded player (libmpv unavailable): open externally.
            launch_player("mpv", url, title, self)
        # Reflect the new playback state on the poster overlay (play -> pause /
        # stop) when the item being played is the one shown in the detail pane.
        self._apply_play_icon()
        self._apply_seek_mode(item, kind)
        # Catch-up sanity check: some providers accept an archive URL but just
        # serve the live feed (the seekbar jumps back yet you're still live).
        # A real archive segment is seekable; verify a few seconds in and, if
        # it isn't, try the next candidate or report it unavailable.
        if catchup:
            token = getattr(self, "_catchup_verify_token", 0) + 1
            self._catchup_verify_token = token
            QTimer.singleShot(5000, lambda t=token: self._verify_catchup(t))

    def _apply_seek_mode(self, item, kind: str) -> None:
        """Pick one seek UI per stream so there's never a second, useless bar:
        VOD -> normal seek bar; plain live -> none; timeshift live edge ->
        the archive timeline; a catch-up segment -> normal seek bar spanning
        it (plus the amber ⧗ TIMESHIFT badge). Called from every play path -
        including the auto-preview, which plays straight through player.play()
        and would otherwise leave the mode stuck at its 'vod' default and show
        a buffer bar on live channels."""
        if not self.player:
            return
        ts_days = self._timeshift_days(item) if item is not None else 0
        if kind != "live":
            self.player.set_seek_mode("vod")
            self.player.set_live_badge(None)
        elif (ts_days > 0 and self._playing_catchup
              and getattr(self, "_ts_catchup_program", False)):
            # A programme picked from the menu/EPG - a seek bar spanning the
            # whole programme. The archive stream starts at the loaded segment
            # but runs on to the live edge and can't be seeked in place, so the
            # bar is virtual: its length is the programme, the playhead sits at
            # (segment offset into the programme), and scrubbing re-loads the
            # archive (see _seek_program) rather than seeking mpv - which would
            # snap to live.
            origin = getattr(self, "_ts_program_start", None)
            stop = getattr(self, "_ts_program_stop", None)
            seg = getattr(self, "_ts_segment_start", None)
            window = (stop - origin) if (origin and stop
                                         and stop > origin) else 0.0
            base = (seg - origin) if (origin and seg
                                      and seg >= origin) else 0.0
            self.player.set_program_window(window, base)
            self.player.set_seek_mode("program")
            self.player.set_live_badge("timeshift")
        elif ts_days > 0 and self._playing_catchup:
            # Scrubbed back on the live timeline (or "go back X") - keep the
            # timeline visible, just positioned behind live, so the user can
            # keep scrubbing across the whole window instead of being locked
            # into the single archive segment.
            self._ts_depth_min = min(ts_days * 1440, self._TS_TIMELINE_MAX_MIN)
            self.player.set_seek_mode("timeline")
            self.player.enter_timeshift(self._ts_depth_min)
            self.player.set_on_archive_segment(True)   # arrows fine-seek here
            self._update_ts_timeline()
            self.player.set_live_badge("timeshift")
        elif ts_days > 0:
            # Span a recent window (see _TS_TIMELINE_MAX_MIN), not the whole
            # multi-day archive: a small drag over days jumped hours/days back.
            self._ts_depth_min = min(ts_days * 1440, self._TS_TIMELINE_MAX_MIN)
            self.player.set_seek_mode("timeline")
            self.player.enter_timeshift(self._ts_depth_min)
            self.player.set_on_archive_segment(False)  # live edge: arrows step
            self._update_ts_timeline()
            self.player.set_live_badge(None)
        else:
            self.player.set_seek_mode("live")
            self.player.set_live_badge(None)


    def _show_toast(self, text: str, duration_ms: int = 0) -> None:
        self._toast.show_message(text, duration_ms)

    def _loading_message(self) -> str:
        return {"live": tr("status_loading_channels"),
                "vod": tr("status_loading_movies"),
                "series": tr("status_loading_series")}.get(
            self.mode, tr("status_loading_content"))

    def _write_status(self, text: str, error: bool = False,
                      emphasis: bool = False) -> None:
        # Errors are red; activity/transient messages use the theme accent and
        # semibold (like the update text) so you actually notice something
        # happened; the resting readout (channel count) stays calm and muted.
        if error:
            style = f"color:{P['error']}; font-size:11px; font-weight:600;"
        elif emphasis:
            style = f"color:{P['accent']}; font-size:11px; font-weight:600;"
        else:
            style = f"color:{P['muted3']}; font-size:11px;"
        self.count_lbl.setStyleSheet(style)
        self.count_lbl.setText(text)

    def _set_status(self, text: str, error: bool = False,
                    emphasis: bool = False) -> None:
        """Set the resting readout of the bottom status line (channel count,
        what's playing, an error, ...). Remembered so a transient flash can
        return to it. Pass ``emphasis`` for activity messages that should stand
        out (accent, semibold) rather than the muted count style."""
        self._rest_status = (text, error, emphasis)
        self._write_status(text, error, emphasis)

    def _flash_status(self, text: str, ms: int = 4000) -> None:
        """Briefly show an activity message in the status line, then return to
        the resting readout. Used for momentary events (guide refresh, cache
        cleared, ...) so they surface in the same place as everything else
        instead of a separate overlay, without lingering afterwards. Emphasised
        so it's easy to notice."""
        self._write_status(text, False, emphasis=True)
        token = getattr(self, "_flash_token", 0) + 1
        self._flash_token = token

        def restore() -> None:
            if getattr(self, "_flash_token", 0) != token:
                return   # a newer status write already took over
            rest = getattr(self, "_rest_status", ("", False, False))
            self._write_status(*rest)

        QTimer.singleShot(ms, restore)

    MAX_STREAM_RETRIES = 2

    def _playback_error(self, msg: str) -> None:
        self.rec.finish_all_inplayer("stream error")
        self.wake.release()
        self._trakt_active = None
        # A catch-up segment that fails right after starting is usually the
        # wrong archive-URL format for this provider - walk to the next
        # candidate. Only while still probing (early failure); once a format has
        # played for a while, later drops are transient and fall through to the
        # normal reconnect below (which replays the same archive url).
        if getattr(self, "_playing_catchup", False):
            pos = self.player.playback_position() if self.player else 0.0
            log.debug("[ts] catchup error: %s (candidate %s, pos=%s)", msg,
                      getattr(self, "_ts_candidate_idx", 0), pos)
            early = ((time.monotonic()
                      - getattr(self, "_ts_candidate_started", 0.0)) < 10
                     and (pos or 0.0) < 2)
            if early:
                if self._try_next_ts_candidate():
                    return
                # No format played. Do NOT learn-and-hide from an mpv-level
                # error: it can be a transient network blip or a single-
                # connection conflict while the live stream releases - the
                # HTTP probe already saw this archive serve real bytes. Only
                # the probe's proven provider response (an error page instead
                # of a stream) may mark a channel broken. Report and settle
                # back on live with timeshift still advertised.
                self._playing_catchup = False
                if self.player:
                    self.player.current_url = None
                self._set_status(tr("ts_archive_unavailable"), error=True)
                lp = getattr(self, "_last_playback", None)
                if lp and lp.get("item"):
                    self.play_live_channel(lp["item"])
                return
        # Live streams drop briefly all the time (single-connection accounts,
        # HLS segment hiccups, the window being dragged). Reconnect silently a
        # couple of times before surfacing the error. Reset the counter when
        # the stream had been stable for a while, so a later drop retries too.
        now = time.time()
        if now - getattr(self, "_last_stream_error_ts", 0.0) > 20:
            self._stream_retries = 0
        self._last_stream_error_ts = now
        lp = getattr(self, "_last_playback", None)
        # A DIFFERENT stream than the one we were retrying is a fresh user play,
        # so give it a fresh retry budget - which is what makes the fast
        # definitive probe (gated on retries == 0) fire on its first failure.
        # The retry loop re-fails the SAME stream, so this never loops. Without
        # it, a 'not started' pinned retries at MAX and the very next channel
        # skipped the fast probe, so the record/reminder prompt only came up via
        # the slower account-aware diagnosis ("it takes too long now").
        err_key = (lp or {}).get("key")
        if err_key != getattr(self, "_err_last_key", None):
            self._stream_retries = 0
            self._err_last_key = err_key
        # On the first failure, probe the URL in parallel with the silent
        # reconnect. A *definitive* status (e.g. 407 = upcoming event, or a
        # forbidden/blocked/not-found) surfaces at once instead of after the
        # whole retry budget - that's what made the "not started yet" dialog
        # take several seconds. Transient results are ignored here so genuine
        # blips still recover silently.
        if (lp and lp.get("kind") == "live" and self.player
                and getattr(self, "_stream_retries", 0) == 0
                and not getattr(self, "_diag_shown", False)):
            self._early_probe_definitive(lp.get("url"), lp.get("item"))
        if (lp and lp.get("kind") == "live" and self.player
                and not getattr(self, "_diag_shown", False)
                and getattr(self, "_stream_retries", 0) < self.MAX_STREAM_RETRIES):
            self._stream_retries = getattr(self, "_stream_retries", 0) + 1
            self.player.current_url = None
            if not self._player_fs:
                self.stream_error.hide()
            self._set_status(tr("status_reconnecting"), emphasis=True)
            if self._player_fs and self.player:
                self.player.set_overlay_info(tr("status_reconnecting"))
            QTimer.singleShot(1500, self._retry_last_stream)
            return
        self._stream_retries = 0
        if self.player:
            self.player.current_url = None
        url = (lp or {}).get("url")
        if url:
            # mpv's "loading failed" is opaque. Probe the account and the stream
            # URL in the background and report the real reason in plain language
            # (expired, connection limit, provider down, HTTP status) - so an
            # end user learns what's wrong without a debug log.
            self._set_status(tr("status_checking_stream"), emphasis=True)
            if self._player_fs and self.player:
                self.player.set_overlay_info(tr("status_checking_stream"))
            self._diagnose_stream_failure(url)
        else:
            self._show_stream_error(msg)

    def _show_stream_error(self, text: str) -> None:
        self._set_status(tr("status_stream_error", msg=text), error=True)
        if self._player_fs and self.player:
            self.player.set_overlay_info(tr("status_stream_error", msg=text))
        else:
            self.stream_error.setText(tr("status_stream_error", msg=text))
            self.stream_error.show()
        if self.player:
            self.player.title_lbl.setText("")

    def _upcoming_prompt_plausible(self, item) -> bool:
        """Whether a 407 really means 'this broadcast hasn't started yet'.

        Overloaded panels also answer 407 when they can't allocate a
        connection, which popped the reminder/record dialog for ordinary
        live channels during provider outages. Treat the 407 as overload -
        not as a scheduled event - when the client's fail-fast cooldown is
        armed (the server just failed at the network level) or when the
        cached guide says the channel has a programme ON AIR right now (a
        mid-programme channel can't be 'not started'). True event channels
        typically carry no guide data, so their prompt still shows."""
        try:
            if time.monotonic() < getattr(self.client, "_net_down_until", 0.0):
                return False
        except Exception:
            pass
        try:
            if item is not None and self.xmltv.current_programme(item):
                return False
        except Exception:
            pass
        return True

    def _early_probe_definitive(self, url, item) -> None:
        """Fast parallel probe on the first live failure: only act on a
        *definitive* HTTP status so the reason (and the upcoming-event prompt)
        appears immediately, while transient failures keep reconnecting
        silently."""
        if not url:
            return
        from ..providers.diagnostics import (
            DEFINITIVE_CODES, reason_for_code, stream_status)
        gen = getattr(self, "_diag_gen", 0)

        def done(code, gen=gen, item=item):
            if (gen != getattr(self, "_diag_gen", -1)
                    or getattr(self, "_diag_shown", False)
                    or (self.player and self.player.current_url)
                    or code not in DEFINITIVE_CODES):
                return
            self._diag_shown = True
            self._stream_retries = self.MAX_STREAM_RETRIES   # stop retrying
            if self.player:
                self.player.current_url = None
            reason = reason_for_code(code)
            if (reason == tr("diag_not_started")
                    and not self._upcoming_prompt_plausible(item)):
                reason = tr("diag_generic")
            self._show_stream_error(reason)
            if reason == tr("diag_not_started"):
                self._offer_upcoming_actions(item)

        run_async(self.pool, lambda: stream_status(url), done, lambda _e: None)

    def _diagnose_stream_failure(self, url: str) -> None:
        from ..providers.diagnostics import diagnose_stream

        lp = getattr(self, "_last_playback", None)

        def show(reason: str) -> None:
            # If a channel started playing meanwhile (the user zapped on), the
            # earlier failure is stale - don't overwrite a working stream. Also
            # skip if the early definitive probe already surfaced this failure.
            if self.player and self.player.current_url:
                return
            if getattr(self, "_diag_shown", False):
                return
            self._diag_shown = True
            # An upcoming/scheduled event (HTTP 407) isn't an error to shrug at
            # - offer to set a reminder or record it. Same-session tr() makes
            # this string compare reliable. But an overloaded panel answers
            # 407 too - only prompt when 'not started' is actually plausible.
            it = (lp or {}).get("item")
            if (reason == tr("diag_not_started")
                    and not self._upcoming_prompt_plausible(it)):
                reason = tr("diag_generic")
            self._show_stream_error(reason)
            if reason == tr("diag_not_started"):
                self._offer_upcoming_actions(it)

        run_async(self.pool, lambda: diagnose_stream(url, self.client),
                  show, lambda _e: show(tr("diag_generic")))

    def _on_player_stalled(self) -> None:
        """The player reported the live stream frozen (buffer-starved)."""
        self._reconnect_live("stall")

    def _auto_reconnect_live(self) -> bool:
        return self.settings.value("auto_reconnect_live", "true") == "true"

    def _reconnect_live(self, reason: str) -> None:
        """Silently recover the current live stream after it froze
        (buffer-starved) or hit EOF (the server dropped the connection or a
        segment gap ended it - with keep-open mpv pauses on the last frame
        instead of stopping, which looks like the app killed it). Guarded by a
        small retry budget so a genuinely dead channel doesn't loop forever; the
        budget resets after 20s of healthy playback.

        Skipped when auto-reconnect is off: on a single-connection account,
        reconnecting steals the connection back from whatever other device just
        took it, so users who share their account can turn this off and let the
        stream simply stop instead of fighting for the slot."""
        lp = getattr(self, "_last_playback", None)
        if not (self.player and self.player.isVisible()):
            return
        if not lp or lp.get("kind") != "live":
            return
        if getattr(self, "_playing_catchup", False):
            # Catch-up/archive playback: seeking makes mpv briefly go idle,
            # which looks like a stall. Never fall back to the live edge here
            # (that yanked the user out of the archive) - the segment is
            # seekable, so let mpv settle after the seek.
            return
        if not self._auto_reconnect_live():
            msg = tr("status_stream_dropped")
            self._set_status(msg, emphasis=True)
            self.player.set_overlay_info(msg)
            return
        now = time.time()
        if now - getattr(self, "_last_stream_error_ts", 0.0) > 20:
            self._stream_retries = 0
        if getattr(self, "_stream_retries", 0) >= self.MAX_STREAM_RETRIES:
            # Quick budget spent. Don't die - keep trying on a slow timer so a
            # channel that comes back (transient drop, provider hiccup) resumes
            # on its own instead of staying frozen. Armed once; re-armed each
            # slow attempt. A successful play resets the counters after 20 s.
            self._arm_slow_reconnect()
            return
        self._last_stream_error_ts = now
        self._stream_retries = getattr(self, "_stream_retries", 0) + 1
        log.info("live reconnect (%s) try %s/%s", reason, self._stream_retries,
                 self.MAX_STREAM_RETRIES)
        self.player.current_url = None
        # Clearing current_url above is what tells the rest of the app "nothing
        # is playing" - but it also removes the guard that stops the failure
        # diagnosis from firing, so the drop we are already handling raced in
        # and set _diag_shown. _retry_last_stream then bailed out on that flag
        # and did nothing at all: the log said "live reconnect try 1/2" and the
        # channel stayed dead, while clicking it by hand started it instantly
        # (a manual play resets the flag). This IS a fresh attempt, so reset it
        # here; the retry budget above is what stops a dead channel looping.
        self._diag_shown = False
        self._set_status(tr("status_reconnecting"), emphasis=True)
        QTimer.singleShot(300, self._retry_last_stream)

    SLOW_RECONNECT_MS = 15000

    def _arm_slow_reconnect(self) -> None:
        if getattr(self, "_slow_reconnect_armed", False):
            return
        self._slow_reconnect_armed = True
        # Remember which channel this is for, so a fire after the user zapped
        # away doesn't yank a working channel offline.
        self._slow_reconnect_key = self._playing_key
        self._set_status(tr("status_reconnecting"), emphasis=True)
        if self.player:
            self.player.set_overlay_info(tr("status_reconnecting"))
        QTimer.singleShot(self.SLOW_RECONNECT_MS, self._slow_reconnect)

    def _slow_reconnect(self) -> None:
        self._slow_reconnect_armed = False
        lp = getattr(self, "_last_playback", None)
        if not (self.player and self.player.isVisible()):
            return
        if not lp or lp.get("kind") != "live":
            return
        if self._playing_key != getattr(self, "_slow_reconnect_key", None):
            return   # user moved on; leave the current channel alone
        if getattr(self, "_playing_catchup", False):
            return
        if not self._auto_reconnect_live():
            return
        self._stream_retries = 0   # a fresh quick budget for this slow attempt
        self._last_stream_error_ts = 0.0
        self._retry_last_stream()

    def _verify_catchup(self, token: int, recheck: bool = False) -> None:
        """A catch-up URL that plays but isn't seekable usually means the
        provider served the live feed instead of the archive - walk to the
        next format or, when exhausted, report catch-up unavailable and
        settle on live.

        mpv's 'seekable' can read False on a REAL archive segment early on
        (the demuxer hasn't resolved a duration yet, especially over a slow
        link), so a single failed check proves nothing: re-check once more
        7 s later and only act when BOTH say not-seekable. And never
        learn-and-hide from here - only the HTTP probe's proven provider
        response may mark a channel's catch-up broken; a wrong verdict here
        silently stripped timeshift off channels that work."""
        if token != getattr(self, "_catchup_verify_token", 0):
            return   # superseded by a newer play
        if not getattr(self, "_playing_catchup", False):
            return
        if not (self.player and self.player.current_url):
            return
        m = self.player.video.mpv
        try:
            seekable = bool(m and m.seekable)
        except Exception:
            return   # can't tell - leave it be
        if seekable:
            return   # genuine archive segment
        if not recheck:
            QTimer.singleShot(
                7000, lambda t=token: self._verify_catchup(t, recheck=True))
            return
        log.debug("[ts] candidate %s played but is live (not seekable twice)",
                  getattr(self, "_ts_candidate_idx", 0))
        if self._try_next_ts_candidate():
            return
        self._playing_catchup = False
        self._set_status(tr("ts_archive_unavailable"), error=True)
        lp = getattr(self, "_last_playback", None)
        if lp and lp.get("item"):
            self.play_live_channel(lp["item"])

    def _try_next_ts_candidate(self) -> bool:
        """Play the next candidate archive-URL format for the current catch-up
        segment. Returns False when none are left. Lets the app auto-pick
        whichever timeshift scheme a provider actually serves."""
        cands = getattr(self, "_ts_candidates", None)
        idx = getattr(self, "_ts_candidate_idx", 0)
        if not cands or idx + 1 >= len(cands):
            return False
        self._ts_candidate_idx = idx + 1
        self._ts_candidate_started = time.monotonic()
        lp = getattr(self, "_last_playback", None) or {}
        self._start_playback(
            cands[idx + 1], lp.get("title", ""), lp.get("icon_url"),
            lp.get("key"), "live", record=False,
            item=lp.get("item"), catchup=True)
        return True

    def _retry_last_stream(self) -> None:
        # The early definitive probe already gave up on this stream (e.g. an
        # upcoming event) - don't replay it just to fail again.
        if getattr(self, "_diag_shown", False):
            log.info("retry skipped: a failure diagnosis was already shown")
            return
        lp = getattr(self, "_last_playback", None)
        if not lp or lp.get("kind") != "live":
            log.info("retry skipped: last playback is %r, not live",
                     (lp or {}).get("kind"))
            return
        # A catch-up/archive segment must be replayed by its own archive URL.
        # Re-deriving a live URL (below) would silently yank the user to the
        # live edge - which is exactly what made a channel whose provider
        # doesn't actually serve catch-up "jump straight to live" on a scrub.
        if getattr(self, "_playing_catchup", False) and lp.get("url"):
            self._start_playback(
                lp["url"], lp["title"], lp.get("icon_url"), lp.get("key"),
                "live", record=False, item=lp.get("item"), catchup=True)
            return
        it = lp.get("item")
        # Re-derive a fresh URL from the channel when we know its id (handles a
        # token/timestamp that expired). Replayed from History we only have the
        # stored URL (no stream_id), so fall back to that - it's the same URL a
        # manual re-open uses, so it reconnects the same way.
        if it is not None and it.get("stream_id") is not None:
            self.play_live_channel(it)
        elif lp.get("url"):
            self._start_playback(lp["url"], lp["title"], lp.get("icon_url"),
                                 lp.get("key"), "live")

    def _last_channel(self) -> None:
        """Jump back to the previously watched live channel (TV 'last' key)."""
        if self._typing():
            return
        prev = getattr(self, "_prev_live_item", None)
        if prev:
            # play_live_channel -> _start_playback records the channel we're
            # leaving as the new 'previous', so pressing it again bounces back.
            self.play_live_channel(prev)

    def _channel_digit(self, digit: str) -> None:
        """Accumulate a typed channel number and jump after a short pause."""
        if self.mode not in ("live", "fav"):
            return
        self._chan_buffer = (self._chan_buffer + digit)[:5]
        self._set_status(tr("chan_entry", num=self._chan_buffer))
        self._chan_timer.start(1200)

    def _channel_jump(self) -> None:
        buf = self._chan_buffer
        self._chan_buffer = ""
        if not buf:
            return
        target = None
        for i in range(self.list_model.rowCount()):
            it = self.list_model.item_at(i)
            if it and not it.get("_header") and str(it.get("num")) == buf:
                target = i
                break
        if target is None:
            self._set_status(tr("chan_not_found", num=buf))
            return
        idx = self.list_model.index(target)
        self.listw.setCurrentIndex(idx)
        self.listw.scrollTo(idx)
        self.play()

    def _zap(self, direction: int) -> None:
        # Music: the buttons step the play queue, not the browsing list -
        # what a listener means by previous/next track.
        if self._queue_step(direction):
            return
        if self.mode not in ("live", "fav", "vod", "series",
                             "history", "rec", "local"):
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


    def _error(self, msg: str) -> None:
        self._hide_busy()
        self._set_status("Error: " + msg, error=True)

    # -- keyboard and close --------------------------------------------------------

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
            # Re-apply the rail/expanded choice the state was saved with -
            # restoreState brings back the 60 px width, and without the
            # matching chrome the full-width content was crammed into it.
            if self.settings.value("sidebar_collapsed", "false") == "true":
                self._set_sidebar_collapsed(True)
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
        while in fullscreen (that size is transient). A detached pop-out is a
        separate window, so the main window geometry stays valid to save."""
        if not hasattr(self, "_root"):
            return
        if self.isFullScreen() or self._player_fs:
            return
        self.settings.setValue("splitter_state", self._root.saveState())
        self.settings.setValue("window_geometry", self.saveGeometry())
        # The rail/expanded choice must persist WITH the splitter state: the
        # saved state restores the 60 px rail width, so without this the next
        # launch drew full-width sidebar content (logo, labels) inside it.
        self.settings.setValue(
            "sidebar_collapsed",
            "true" if getattr(self, "_sidebar_collapsed", False) else "false")
        self.settings.sync()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._schedule_save_layout()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._schedule_save_layout()
        self._maybe_auto_collapse_sidebar()
        self._update_mid_compact()
        if self._welcome is not None and self._welcome.isVisible():
            self._welcome.cover()
        self._position_provider_hint()
        self._reposition_update_banner()
        if getattr(self, "_focus_mode", False):
            self._position_reopen()
        # The justified poster grid re-flows its columns from ChannelListView's
        # own resizeEvent, so nothing else to do here.

    # -- first-run onboarding ------------------------------------------------

    def closeEvent(self, event) -> None:
        # Close the non-modal cast panel first: as a separate top-level
        # window it would otherwise keep the app alive (quitOnLastWindowClosed
        # never fires) and leave the process hanging after the main window
        # closes.
        d = getattr(self, "_cast_dialog", None)
        if d is not None:
            d.close()
        # Bring a detached player home first so mpv teardown acts on the widget
        # in the main window, not one owned by a separate pop-out window.
        self._exit_popout_if_active()
        # Tear down any multiview cells (each owns its own mpv render context).
        self._close_multiview_if_active()
        # All persistence must land BEFORE we skip the interpreter
        # teardown below. Layout, resume position, TMDB cache flush,
        # recording state, mpv teardown, cast disconnect - each of
        # these is itself flush-and-return, no pending threads.
        if not getattr(self, "_resetting", False):
            # Skipped after Settings -> Reset all: these writes would seed the
            # freshly cleared config with layout/resume/watched keys again.
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
        # The app exits via os._exit (here on stuck workers, and always after
        # the event loop - see app.main), which skips QSettings' auto-sync on
        # destruction. Flush every settings file explicitly so this session's
        # layout, resume points and watched/cache writes actually persist.
        for st in (self.settings, self._resume_settings, self._cache_settings):
            try:
                st.sync()
            except Exception:
                pass
        # A cast has to be told to stop before this process is gone, and the
        # thread doing it is a daemon racing os._exit below. Started here so
        # it overlaps with draining the pools, and waited for further down -
        # unwaited it lost that race in the packaged build and the TV simply
        # kept playing after the app had quit.
        casting = getattr(self.cast, "active", None) is not None
        if casting:
            self._save_cast_position()
        cast_stop = threading.Thread(target=self.cast.shutdown, daemon=True)
        cast_stop.start()
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
        # Only wait when something is actually casting: on an idle manager
        # this same call disconnects every discovered device, and nobody
        # should sit through that just to close the window.
        if casting:
            cast_stop.join(2.0)
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
        # macOS: QApplication.quit() enters AppKit's terminate cascade, whose
        # exit() finalizers run while Qt timers are still live - a timer then
        # fires into the half-destroyed QGuiApplication and segfaults (seen
        # via Settings -> Reset all). Teardown above already drained workers
        # and released the player, so the hard exit is safe there too.
        if active_workers or sys.platform == "darwin":
            os._exit(0)
        QApplication.instance().quit()
