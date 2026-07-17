"""Main application window: sidebar, channel list, detail panel, playback."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time

from PyQt6.QtCore import (
    QEvent, QPointF, QSettings, QSize, Qt, QThreadPool,
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
from ..media.embedded import EmbeddedPlayer
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
from .widgets import _SidebarLogo, _Toast
from .mw_settings import _SettingsMixin
from .mw_trakt import _TraktMixin
from .mw_recording import _RecordingMixin
from .mw_busy import _BusyMixin
from .mw_context import _ContextMenuMixin
from .mw_detail import _DetailMixin
from .mw_nav import _NavMixin
from .mw_multiview import _MultiviewMixin
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
                 _PopoutMixin, _MultiviewMixin, QMainWindow):
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
        self._pending_jump_key = None
        self._pending_jump_cat = None
        self._stream_retries = 0
        self._last_stream_error_ts = 0.0
        self._popout_win = None
        self._popout_placeholder = None
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

    # -- UI construction -------------------------------------------------------

    def _build_ui(self) -> None:
        menubar = self.menuBar()
        app_menu = menubar.addMenu(APP_NAME)
        settings_action = app_menu.addAction(tr("btn_settings") + "…")
        settings_action.triggered.connect(self.open_settings)
        refresh_action = app_menu.addAction(tr("menu_refresh_playlist"))
        refresh_action.triggered.connect(self.refresh_playlist)
        reminders_action = app_menu.addAction(tr("reminders_menu"))
        reminders_action.triggered.connect(self._open_reminders)
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
            reminders_action.setMenuRole(
                QAction.MenuRole.ApplicationSpecificRole)
        # Kept for live language switching (see retranslate_ui).
        self._i18n_actions = {
            settings_action: lambda: tr("btn_settings") + "…",
            refresh_action: lambda: tr("menu_refresh_playlist"),
            reminders_action: lambda: tr("reminders_menu"),
            about_action: lambda: tr("menu_about"),
            quit_action: lambda: tr("menu_quit"),
        }

        root = QSplitter(Qt.Orientation.Horizontal)
        root.setHandleWidth(6)
        self.setCentralWidget(root)

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

        # Source glyphs for the nav icons (rendered to fixed-size monochrome
        # pixmaps below). Each must be visually distinct: watch-later is a
        # bookmark, recordings a record dot, history a clock - no two alike.
        self._rail_glyphs = {
            "live": "📺", "vod": "🎬", "series": "🎞", "fav": "★",
            "watchlist": "🔖", "watched": "✓", "rec": "⏺", "history": "🕘",
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

        # Browse: the content-type modes, always visible at the top.
        for key, text in (("live", tr("nav_tv")), ("vod", tr("nav_movies")),
                          ("series", tr("nav_series"))):
            _make_nav(key, text, sl, primary=True)

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

        # EPG Guide + Settings sit side by side in one compact row rather than
        # two stretched full-width pills. The row is a QBoxLayout whose
        # direction flips to vertical on the collapsed icon rail (60 px is too
        # narrow for two buttons abreast) - see _apply_sidebar_chrome.
        self._guide_btn = guide_btn = QPushButton(
            tr("btn_epg_guide"), objectName="SideAction")
        guide_btn.setToolTip(tr("btn_epg_guide"))
        guide_btn.setSizePolicy(QSizePolicy.Policy.Ignored,
                                QSizePolicy.Policy.Fixed)
        guide_btn.clicked.connect(self._open_epg_guide)
        # (Reload lives in the menu bar's "Refresh playlist" and the
        # per-playlist auto-refresh setting; a sidebar button here was just
        # an easy mis-click.)
        self._settings_btn = settings_btn = QPushButton(
            tr("btn_settings"), objectName="SideAction")
        settings_btn.setToolTip(tr("btn_settings"))
        settings_btn.setSizePolicy(QSizePolicy.Policy.Ignored,
                                   QSizePolicy.Policy.Fixed)
        settings_btn.clicked.connect(self.open_settings)
        self._actions_box = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._actions_box.setContentsMargins(0, 0, 0, 0)
        self._actions_box.setSpacing(6)
        self._actions_box.addWidget(guide_btn)
        self._actions_box.addWidget(settings_btn)
        sl.addLayout(self._actions_box)

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
        self.grid_btn.setCheckable(True)
        self.grid_btn.setChecked(
            self.settings.value("view_grid", "false") == "true")
        self.grid_btn.toggled.connect(self._inline_view_changed)
        # Compact stand-ins for the Size/Sort combos, mirroring how the grid
        # toggle shrinks to a glyph: tool buttons that open the same choices as
        # a popup menu (fully readable at any pane width, no clipped text).
        # Shown only when the pane is narrow - see _apply_mid_compact.
        self._size_menu_btn = QToolButton(objectName="InlineToggle")
        self._size_menu_btn.setText("⊞")
        self._size_menu_btn.setToolTip(tr("label_size"))
        self._size_menu_btn.setFixedWidth(30)
        self._size_menu_btn.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        self._sort_menu_btn = QToolButton(objectName="InlineToggle")
        self._sort_menu_btn.setText("⇅")
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
        ctl.addWidget(self._size_menu_btn)
        ctl.addWidget(self._sort_label)
        ctl.addWidget(self.sort_box)
        ctl.addWidget(self._sort_menu_btn)
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
            # Keep the player pane visible on stop - mpv clears to black -
            # instead of hiding it, so the window just goes black.
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
        self.epg_holder = QWidget()
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
        playing_this = (p is not None and p.current_url is not None
                        and self._playing_key is not None
                        and self._current_key == self._playing_key)
        if not playing_this:
            return "play"
        it = self.list_model.item_at(self.listw.currentIndex().row())
        try:
            timeshift = bool(it) and self._timeshift_days(it) > 0
        except Exception:
            timeshift = False
        pausable = (self._playing_group in ("vod", "episode", "rec")
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
            # (don't wait for the 1 s timer or the offset to accrue).
            if self.player and on_live and not self._playing_catchup:
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

    def _toggle_player_fullscreen(self) -> None:
        if not self.player or not self.player.isVisible():
            return
        now = time.time()
        if now - getattr(self, "_fs_toggled_at", 0.0) < 0.4:
            return
        self._fs_toggled_at = now
        if self._popout_win is not None:
            self._toggle_popout_fullscreen()
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
        # Remember if the window was maximized/zoomed and its exact geometry, so
        # leaving video-fullscreen restores that state instead of shrinking to
        # the pre-maximize size. showNormal() alone drops the maximized state
        # (reported on macOS: a maximized window shrinks after video fullscreen).
        self._was_maximized = self.isMaximized()
        if not self._was_fullscreen:
            self._pre_fs_geo = self.geometry()
        self.showFullScreen()

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
            if self._popout_win.isFullScreen():
                self._toggle_popout_fullscreen()
            return
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
            if getattr(self, "_was_maximized", False):
                # Re-zoom to the maximized state it had before going fullscreen.
                self.showMaximized()
            else:
                self.showNormal()
                # Deterministically restore the exact windowed size/position -
                # a manually-enlarged window otherwise comes back smaller.
                geo = getattr(self, "_pre_fs_geo", None)
                if geo is not None:
                    self.setGeometry(geo)
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
            self.pool, lambda: self.xmltv.ensure_loaded(force=True),
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
                "fav", "watchlist", "watched", "rec", "history")
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
            # "Recently added" for Movies and Series - the latest titles the
            # provider has published, newest first.
            if self.mode in ("vod", "series"):
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
            row = 1 if self.cat_list.count() > 1 else 0
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
                        if self.cat_list.item(i).data(
                                Qt.ItemDataRole.UserRole) == cat:
                            row = i
                            break
                    self._pending_jump_cat = None
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
        if category_id == "__continue__" and self.mode in ("vod", "series"):
            # Synthetic category: partly-watched titles of this section's kind
            # (movies under Movies, episodes under Series), from the resume
            # store (no network fetch).
            kind = "vod" if self.mode == "vod" else "episode"
            self.all_items = self._continue_items(kind)
            self._apply_filter()
            return
        if category_id == "__recent__" and self.mode in ("vod", "series"):
            # Synthetic category: every title sorted by the provider's
            # publish/update time, newest first (capped so a huge library
            # doesn't lag the list).
            self._show_busy(tr("status_loading_recent"))
            rmode = self.mode
            rgen = self._load_gen
            skey = "added" if rmode == "vod" else "last_modified"
            rfetch = (self.client.vod_streams if rmode == "vod"
                      else self.client.series_list)

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
        if self._loading_hint.isVisible():
            self._loading_hint.hide()
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
        # has loaded (see _load_categories / _apply_filter). Land on the item's
        # own category so the sidebar reflects what's playing, not just "All".
        self._pending_jump_key = self._playing_key
        self._pending_jump_cat = (self._playing_item or {}).get("category_id")
        QTimer.singleShot(2500, self._clear_pending_jump)   # safety net
        target = {"live": "live", "vod": "vod",
                  "episode": "series"}.get(self._playing_group, self.mode)
        if self.mode != target:
            self.switch_mode(target)     # done() honours _pending_jump_cat
        elif self.cat_list.count():
            row, cat = 0, self._pending_jump_cat
            if cat is not None:
                for i in range(self.cat_list.count()):
                    if self.cat_list.item(i).data(
                            Qt.ItemDataRole.UserRole) == cat:
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

    def tune_from_guide(self, ch) -> None:
        """Play a channel picked from the EPG guide, then jump the middle
        column to it and select its category in the sidebar so the guide
        selection is reflected everywhere."""
        self.play_live_channel(ch)
        self._pending_jump_key = self._item_key(ch)
        self._pending_jump_cat = ch.get("category_id")
        QTimer.singleShot(3000, self._clear_pending_jump)
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

    def _play_kind_for(self, it) -> str:
        """The playback/resume kind for a row. In mixed views (Favorites 'All',
        History, ...) the section-derived _history_kind() would treat a movie as
        'live' and skip its resume prompt, so honour the row's own kind tag when
        it carries one."""
        ek = it.get("_kind") or it.get("_ekind")
        mapped = {"vod": "movie", "movie": "movie", "series": "series",
                  "episode": "episode", "live": "live", "fav": "live"}.get(ek)
        return mapped or self._history_kind()

    # -- selection, EPG and detail panel -------------------------------------------


    # -- series -> episodes --------------------------------------------------------

    def _enter_series(self, series) -> None:
        sid = series.get("series_id")
        if sid is None:
            return
        self._show_busy(tr("status_loading_episodes"))

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
        if self.mode == "history":
            url = it.get("_url")
            title = it.get("name") or "dopeIPTV"
            icon, key, kind = (it.get("stream_icon"), it.get("_key"),
                               it.get("_kind"))
        else:
            url, title = self._stream_for(it)
            icon = it.get("stream_icon") or it.get("cover")
            key, kind = self._item_key(it), self._play_kind_for(it)
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

    def stop_local_playback_for_cast(self) -> None:
        """Free the local stream when a cast starts. The Chromecast pulls the
        URL itself (one connection from the device), so on a single-connection
        account leaving the embedded player running too would be a second
        connection the provider refuses. Called by the cast dialog on a
        successful cast."""
        p = getattr(self, "player", None)
        if p is not None and getattr(p, "current_url", None):
            try:
                p.stop()
            except Exception:
                pass

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
        if kind not in ("live", "fav") and not history_live:
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
    _RESUMABLE = ("movie", "episode", "recording")

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
                and self._playing_group in ("vod", "episode", "rec")):
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
            url = self.client.episode_url(
                it.get("id"), it.get("container_extension"))
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
                        item=None, catchup: bool = False) -> None:
        if not self._guard_stream_switch(url, title):
            return
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
            # For a live channel, remember its stream_id + archive depth so a
            # later replay from History still has timeshift/catch-up available.
            extra = None
            if kind == "live" and item is not None:
                extra = {"stream_id": item.get("stream_id"),
                         "num": item.get("num"),
                         "tv_archive": item.get("tv_archive"),
                         "tv_archive_duration": item.get("tv_archive_duration")}
            self.history.add(url, title, icon_url, key, kind, extra=extra)
        if kind in ("movie", "episode"):
            self._trakt_start_for_item(kind, item)
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
        self._sync_player_buttons()
        self._playing_key = key
        self._playing_group = {"live": "live", "movie": "vod",
                               "episode": "episode",
                               "recording": "rec"}.get(kind)
        self.listw.viewport().update()
        self.setWindowTitle(title or self._base_title)
        self._set_status(tr("status_playing", title=title))
        if self.player:
            self.rec.finish_all_inplayer("channel changed")
            self.player.show()
            self.player.set_overlay_info(title)
            if self.player.play(url, title, start=resume_at):
                self.wake.acquire(f"Playing {title}")
            else:
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

    def _player_missing(self, name: str) -> None:
        QMessageBox.warning(
            self, tr("status_player_not_found"),
            tr("status_player_not_found_msg", name=name))

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
                # No format worked: the provider isn't serving catch-up here.
                # Learn it so this channel stops advertising timeshift.
                self._playing_catchup = False
                if self.player:
                    self.player.current_url = None
                self._set_status(tr("ts_archive_unavailable"), error=True)
                lp = getattr(self, "_last_playback", None)
                if lp and lp.get("item"):
                    self._mark_ts_broken(lp["item"])
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
        if (lp and lp.get("kind") == "live" and self.player
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

    def _diagnose_stream_failure(self, url: str) -> None:
        from ..providers.diagnostics import diagnose_stream

        def show(reason: str) -> None:
            # If a channel started playing meanwhile (the user zapped on), the
            # earlier failure is stale - don't overwrite a working stream.
            if self.player and self.player.current_url:
                return
            self._show_stream_error(reason)

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

    def _verify_catchup(self, token: int) -> None:
        """A catch-up URL that plays but isn't seekable means the provider
        served the live feed instead of the archive - walk to the next format
        or, when exhausted, report catch-up unavailable and settle on live."""
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
        log.debug("[ts] candidate %s played but is live (not seekable)",
                  getattr(self, "_ts_candidate_idx", 0))
        if self._try_next_ts_candidate():
            return
        self._playing_catchup = False
        self._set_status(tr("ts_archive_unavailable"), error=True)
        lp = getattr(self, "_last_playback", None)
        if lp and lp.get("item"):
            # This URL played but is the live feed, not a seekable archive. Hide
            # the channel's catch-up unless the request sat right at the depth
            # limit (clamped) - that only means the oldest edge isn't kept, not
            # that the archive is fake. A within-depth request served live is a
            # fake archive at a point a real one would serve, so mark it broken.
            # (A picked programme never unmarks: its URL can fail on a channel
            # whose plain archive is fine.)
            if (not getattr(self, "_ts_last_clamped", False)
                    and not getattr(self, "_ts_catchup_program", False)):
                self._mark_ts_broken(lp["item"])
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
        lp = getattr(self, "_last_playback", None)
        if not lp or lp.get("kind") != "live":
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
