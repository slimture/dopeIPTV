"""Main application window: sidebar, channel list, detail panel, playback."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time

from PyQt6.QtCore import (
    QEvent, QPoint, QPointF, QSettings, QSize, Qt, QThreadPool,
    QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QGuiApplication, QIcon, QPainter, QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QMainWindow, QMenu, QMessageBox, QWidget,
)

from .. import ORG
from ..core.log import log
from ..i18n import tr
from ..providers.casting import CastHub
from ..providers.kodi import KodiManager
from ..providers.client import (
    DemoClient, XtreamClient, make_client,
)
from ..media.video_thread import WindowChanges
from ..providers.epg import XmltvGuide, epg_cache_path, prune_epg_caches
from ..services.coverart import CoverArtService
from ..core.series_rules import SeriesRuleStore
from ..media.players import launch_player
from ..core.contest import ContestWatch
from ..core import privacy
from ..core.privacy import for_log, url_for_log
from ..core.health import StreamHealth
from ..core.recording import RecordingManager
from ..core.stores import (
    CategoryOverrides, ChannelOverrides, ParentalControl, PlaylistStore,
)
from .theme import P, rounded
from ..core.wakelock import WakeLock
from .widgets import (
    file_dialog_options, plain_text,
)
from .mw_settings import _SettingsMixin
from .mw_trakt import _TraktMixin
from .mw_recording import _RecordingMixin
from .mw_busy import _BusyMixin
from .notices import hub_of
from .mw_airplay import _AirPlayMixin
from .mw_cast import _CastMixin
from .mw_cast_watch import _CastWatchMixin
from .mw_chanprefs import _ChannelPrefsMixin
from .mw_failover import _FailoverMixin
from .mw_connections import _ConnectionsMixin
from .mw_mirrors import _MirrorsMixin
from .mw_portal_archive import _PortalArchiveMixin
from .mw_health import _HealthMixin
from .mw_ts_analysis import _TsAnalysisMixin
from .mw_stream_info import _StreamInfoMixin
from .mw_remote import _RemoteMixin
from .mw_phone import _PhoneMixin
from .mw_server import _ServerMixin
from .mw_calendar import _CalendarMixin
from .mw_events import _EventsMixin
from .mw_media import _MediaServerMixin
from .mw_report import _ReportMixin
from .mw_sync import _SyncMixin
from .mw_together import _TogetherMixin
from .mw_simkl import _SimklMixin
from .mw_discord import _DiscordMixin
from .mw_privacy import _PrivacyMixin, note_titles, wake_reason
from .mw_profiles import _ProfilesMixin
from .mw_commands import _CommandsMixin
from .mw_box import _BoxMixin
from .mw_donate import _DonateMixin
from .mw_context import _ContextMenuMixin
from .mw_favorites import _FavoritesMixin
from .mw_follow import _FollowMixin
from .mw_pane_follow import _PaneFollowMixin, from_pane
from .mw_place_tv import _PlaceTvMixin
from .mw_detail import _DetailMixin
from .mw_nav import _NavMixin
from .mw_source_label import _SourceLabelMixin
from .mw_mpris import _MprisMixin
from .mw_multiview import _MultiviewMixin
from .mw_guide import _GuideMixin
from .mw_places import _PlacesMixin
from .mw_place_look import _PlaceLookMixin
from .mw_mitt import _MittMixin
from .mw_popular import _PopularMixin
from .mw_local import _LocalFilesMixin
from .mw_local_watch import _LocalWatchMixin
from .mw_onboarding import _OnboardingMixin
from .mw_popout import _PopoutMixin
from .mw_reminders import _RemindersMixin
from .mw_series import _SeriesMixin
from .mw_series_page import _SeriesPageMixin
from .mw_place_films import _PlaceFilmsMixin
from .mw_place_local import _PlaceLocalMixin
from .mw_place_rec import _PlaceRecMixin
from .mw_place_radio import _PlaceRadioMixin
from .mw_place_series import _PlaceSeriesMixin
from .mw_search_all import _SearchAllMixin
from .mw_every_source import _EverySourceMixin
from .mw_stats import _StatsMixin
from .mw_search import _SearchMixin
from .mw_shortcuts import _ShortcutsMixin
from .mw_palette import _PaletteMixin
from .mw_sidebar import _SidebarMixin
from .mw_sort import _SortMixin
from .mw_subtitles import _SubtitlesMixin
from .mw_updates import _UpdatesMixin
from .mw_radio import _RadioMixin
from .mw_webstreams import _WebStreamsMixin
from .mw_tv import _TvMixin
from .mw_teletext import _TeletextMixin
from .mw_lists import _UNSET, _ListsMixin
from .mw_queue import _QueueMixin
from .mw_build import _BuildMixin
from ..core.workers import (
    LogoLoader, default_image_cache_dir, run_async)


class MainWindow(WindowChanges, _SettingsMixin, _TraktMixin, _RecordingMixin,
                 _ContextMenuMixin, _FavoritesMixin, _DetailMixin, _RemindersMixin,
                 _BusyMixin, _UpdatesMixin, _SearchMixin, _SidebarMixin,
                 _NavMixin, _SourceLabelMixin, _ShortcutsMixin, _PaletteMixin, _OnboardingMixin, _SortMixin,
                 _ListsMixin,
                 _LocalFilesMixin, _PlaceLocalMixin, _LocalWatchMixin, _RadioMixin, _WebStreamsMixin,
                 _QueueMixin,
                 _PlaceRecMixin, _PlaceRadioMixin,
                 _PopoutMixin, _MultiviewMixin, _PlacesMixin, _PlaceLookMixin, _PopularMixin,
                 _GuideMixin,
                 _SeriesPageMixin, _PlaceFilmsMixin, _PlaceSeriesMixin, _SearchAllMixin,
                 _EverySourceMixin, _MittMixin,
                 _MprisMixin,
                 _FollowMixin, _PaneFollowMixin, _PlaceTvMixin, _SubtitlesMixin, _CastMixin, _CastWatchMixin, _AirPlayMixin, _DonateMixin,
                 _BoxMixin, _CommandsMixin, _PrivacyMixin, _FailoverMixin,
                 _HealthMixin, _TsAnalysisMixin, _StreamInfoMixin, _ChannelPrefsMixin, _RemoteMixin, _PhoneMixin, _ServerMixin, _CalendarMixin,
                 _EventsMixin, _ReportMixin,
                 _PortalArchiveMixin, _SeriesMixin, _SyncMixin,
                 _ConnectionsMixin, _MirrorsMixin, _TogetherMixin, _StatsMixin,
                 _SimklMixin, _DiscordMixin, _MediaServerMixin, _TvMixin,
                 _TeletextMixin, _ProfilesMixin, _BuildMixin, QMainWindow):
    """Primary application window with sidebar, channel list, and detail panel."""

    # A guide download's progress: (the load's token, percent). Token 0 is a
    # load the window did not start (mw_busy._on_epg_progress).
    epg_progress = pyqtSignal(int, int)

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
        # The Nerd page's profile (nothing while Nerd is off) and the source
        # it applies to.
        from ..core import nerd
        nerd.install(settings)
        nerd.set_source(active_pl)
        # This install's id at Jellyfin, Emby and Plex lives in the settings.
        from ..providers import mediaserver
        mediaserver.install_device_store(settings)
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
        # Where covers of the person's own are, for the loaders above.
        self._local_trust_pictures()
        self.epg_progress.connect(self._on_epg_progress)
        pid = (active_pl or {}).get("id")
        self.xmltv = XmltvGuide(
            client, (active_pl or {}).get("epg_url") or None,
            cache_path=epg_cache_path(pid) if pid else None,
            progress_cb=self._guide_progress(0))
        self.xmltv.delay_minutes = self._epg_delay_minutes()
        # The household's profiles (core/profiles.py); each store below is
        # the profile's in use, pinned to it.
        from ..core.profiles import ProfileStore
        self.profiles = ProfileStore(settings)
        self._resume_settings = self._open_resume_settings(settings)
        self._follow_settings = self._open_follow_settings(settings)
        self._build_source_stores(pid)
        # Radio stations and web streams belong to no source: one list each,
        # whatever source is in use (mw_radio, mw_webstreams).
        self._build_radio_web_favs()
        self.series_rules = SeriesRuleStore(settings, pid)
        self.overrides = CategoryOverrides(
            settings, f"category_overrides_{pid}" if pid else "category_overrides")
        self.channel_ov = ChannelOverrides(
            settings, f"channel_overrides_{pid}" if pid else "channel_overrides")
        self.parental = ParentalControl(settings)
        # Chromecasts, DLNA TVs and Kodis alike, behind one object
        # (casting.py); a Kodi added by hand is remembered in the settings.
        self.cast = CastHub(kodi=KodiManager(settings), settings=settings)
        # AirPlay to an Apple TV joins it on macOS (mw_airplay).
        self._init_airplay()
        self.rec = RecordingManager(settings, self)
        # A portal's recording asks for its address as it starts.
        self.rec.address_for = self._rec_address_for
        # A scheduled one, as it comes due: counted on its account as one
        # started by hand - recorded from the player when it plays that
        # channel, held (never a second connection silently) when no room.
        self.rec.may_open = self._rec_may_open
        self.rec.jobs_changed.connect(self._recordings_changed)
        self.rec.recording_stopped.connect(self._on_recording_stopped)
        # A box's channel it cannot decrypt, said as the recording's reason.
        self.rec.recording_stopped.connect(self._box_rec_stopped)
        # Events pushed out (Nerd): they only watch.
        self.rec.recording_stopped.connect(self._events_rec_stopped)
        self.rec.jobs_changed.connect(self._events_jobs)
        self.wake = WakeLock()
        self._init_donate()
        # Settings sync between a person's computers (off until started).
        self._init_sync()
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
        self._trakt_active: dict | None = None
        self._simkl_active: dict | None = None
        # The profile's accounts (Trakt; Simkl beside it, offered only when
        # the build carries its client id - mw_simkl), watched marks and
        # Watch later.
        self._build_profile_accounts()
        self._watched_sync_running = False
        self._raw_categories: list = []
        self.mode: str = "live"
        # The place the app opens on (mw_places): where the person left off,
        # or Mine - its list mode set before the first list is fetched.
        self._init_places()
        # No category yet: the build's first filter asks it (a start on
        # Radio asked for its Search row and crashed - 1.6.9's report).
        self._current_cat = None
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
        self._contest = ContestWatch()
        # Nerd's stream health per channel: it only watches (mw_health).
        self.health = StreamHealth(settings)
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
        self._last_playlist_refresh = time.time()
        self._load_gen = 0

        self._base_title = (active_pl or {}).get("name", "")
        self.setWindowTitle(self._base_title)
        self.resize(1240, 780)
        self._build_ui()
        # Watch together (off until a room is started or joined).
        self._init_together()
        self._watch_color_scheme()
        # The guide is loaded at start; an app left open - overnight, or a
        # Mac that slept - kept yesterday's. Look again now and then, and
        # whenever the app comes back to the front.
        self._guide_check = QTimer(self)
        self._guide_check.setInterval(20 * 60 * 1000)
        self._guide_check.timeout.connect(self._check_guide_age)
        # The series rules look through the guide on the same beat.
        self._guide_check.timeout.connect(self._check_series_rules)
        self._guide_check.start()
        QApplication.instance().applicationStateChanged.connect(
            lambda state: state == Qt.ApplicationState.ApplicationActive
            and self._check_guide_age())
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
        load_first = self._start_loader()
        QTimer.singleShot(100, lambda: (load_first(), self._open_start_place()))
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
        QTimer.singleShot(5000, self._finish_self_update)
        # Offered once the window is up and doing something; a link in the
        # status row, never a dialog over the window.
        QTimer.singleShot(6000, self._maybe_offer_desktop_entry)
        # A settings file this start could not keep to this user (a drive
        # without permissions): said once, calmly (mw_settings).
        QTimer.singleShot(3000, self._tell_settings_not_private)

        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.timeout.connect(self._maybe_auto_refresh)
        self._auto_refresh_timer.start(5 * 60_000)

        # "Start in TV mode" (mw_tv), once the window is up.
        QTimer.singleShot(300, self._maybe_start_tv_mode)
        # "Who's watching?" over it all when there are several profiles
        # (mw_profiles) - TV mode shows it as a screen of its own.
        self._maybe_pick_profile_at_start()

    # -- UI construction -------------------------------------------------------

    def _fill_playlist_menu(self, menu: QMenu) -> None:
        """Fill *menu* with a checkable entry per playlist, the active one
        ticked. Used by the source avatar at the sidebar's foot."""
        menu.clear()
        # The household's profiles first (two or more; mw_profiles).
        self._fill_profile_menu(menu)
        store = self.playlist_store
        if not store or not store.playlists():
            act = menu.addAction(tr("pl_mgmt_unavailable"))
            act.setEnabled(False)
            self._profile_manage_action(menu)
            return
        active = store.active_id
        for p in store.playlists():
            act = menu.addAction(p.get("name") or p.get("server") or "?")
            act.setCheckable(True)
            act.setChecked(p["id"] == active)
            if p["id"] != active:
                act.triggered.connect(
                    lambda _c=False, pid=p["id"]: self.switch_playlist(pid))
        if active and store.get(active):
            menu.addSeparator()
            act = menu.addAction(tr("src_settings_menu"))
            act.triggered.connect(lambda _c=False, pid=active: self.open_source_settings(pid))
        self._profile_manage_action(menu)

    def _show_playlist_menu(self) -> None:
        """Pop the source switcher just above the avatar at the sidebar's
        foot, centred on it (Qt keeps it on the screen)."""
        menu = QMenu(self)
        self._fill_playlist_menu(menu)
        btn = self._playlist_btn
        top = btn.mapToGlobal(btn.rect().topLeft())
        hint = menu.sizeHint()
        x = top.x() + (btn.width() - hint.width()) // 2
        menu.exec(QPoint(x, top.y() - hint.height() - 4))
        menu.deleteLater()

    def _update_playlist_btn(self) -> None:
        """Put the active source on the avatar at the sidebar's foot: its
        initials, its full name in the tooltip. Shown whenever there is a
        source of one's own (demo and explore have none to name)."""
        btn = getattr(self, "_playlist_btn", None)
        if btn is None:
            return
        # Its name over the categories, TV mode's header and the status
        # line follow every change the avatar does (mw_source_label).
        self._update_source_label()
        # ...and the Search page lets go of what it read of a source removed
        # or edited since; Every source is offered with two of them or more.
        self._search_sources_changed()
        self._xs_sources_changed()
        store = self.playlist_store
        pl = store.get(store.active_id) if (store and store.active_id) else None
        name = ((pl or {}).get("name") or (pl or {}).get("server") or "")
        prof = self._profile() if self._profile_many() else None
        if prof is not None:
            # Two people or more: the avatar is the one watching (mw_profiles).
            from .profiles_ui import avatar_text, profile_fill
            btn.set_profile(prof.name, profile_fill(prof), avatar_text(prof), name,
                            tr("profile_menu_hint"))
            btn.setVisible(True)
            return
        btn.set_source(name, tr("menu_playlists"))
        btn.setVisible(pl is not None)

    def _build_ui(self) -> None:
        # Dropping a video file anywhere on the window plays it (see
        # dragEnterEvent/dropEvent - the local-files entry points).
        self.setAcceptDrops(True)

        # The window's parts, made in the order they always were: that
        # order is the focus chain, the stacking of overlapping siblings
        # and the order signals' handlers run in (ui/mw_build.py,
        # ui/mw_build_detail.py).
        self._build_menus()
        root = self._build_center()
        side, sl = self._build_sidebar_shell()
        self._build_places_nav(sl)
        self._build_category_column(sl)
        self._build_sidebar_foot(sl)
        mid, lp, ml = self._build_middle_pane()
        self._build_list_controls(ml)
        self._build_list_view(ml)
        self._build_status_row(lp)
        det, dl = self._build_detail_pane()
        self._build_cast_card(dl)
        self._build_player(dl)
        il = self._build_info_column(det, dl)
        self._build_detail_header(il)
        self._build_detail_lists(il)
        self._build_columns(root, side, mid, det)
        self._build_timers()
        self._build_shortcuts()
        # The desktop's media controls and the keyboard's media keys.
        self._start_media_session()
        self._report_init()
        # New episodes of the series the user follows.
        self._start_follow_checks()

        # Channel-number quick-jump state (digits typed in the list).
        self._prev_live_item = None
        self._chan_buffer = ""
        self._chan_timer = QTimer(self)
        self._chan_timer.setSingleShot(True)
        self._chan_timer.timeout.connect(self._channel_jump)

        # The TV place's header and Recently watched over the list.
        self._place_tv_init()
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
        from_page = self._sp_detail_playing()
        playing_this = (p is not None and p.current_url is not None
                        and self._playing_key is not None
                        and self._current_key is not None
                        and str(self._current_key) == str(self._playing_key))
        if from_page is not None:
            # The column shows a series' page's pick: the button is that
            # episode's (or the series' while no card is picked).
            playing_this = from_page
        elif not playing_this and self._series_page_playing():
            # A series' page: the row selected in the list is the series,
            # never the episode that plays - the poster read "play" while an
            # episode of this very series played, and a click started it over.
            playing_this = True
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
            self._warn_pause_while_recording()
            # Pausing a live stream means you're no longer at the live edge:
            # show the badge and flip the timeline to 'not live' immediately
            # (don't wait for the 1 s timer or the offset to accrue). Only for
            # channels that actually HAVE a provider archive - a plain live
            # channel pauses on mpv's local buffer, and stamping it TIMESHIFT
            # wrongly advertised an archive that doesn't exist.
            # Nor for a channel paused in its rewind buffer (the DVR row):
            # the pause is the buffer's, the archive plays no part in it.
            it = (lp or {}).get("item")
            try:
                has_archive = it is not None and self._timeshift_days(it) > 0
            except Exception:
                has_archive = False
            if (self.player and on_live and not self._playing_catchup
                    and has_archive and not self._dvr_buffered()):
                self.player.set_live_badge("timeshift")
                self._update_ts_timeline()
            elif self.player and on_live and not self._playing_catchup:
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
                and self._timeshift_days(it) > 0 and elapsed >= 120
                and (not self.player or self.player.local_buffer_window() is None)):
            # Only a *long* pause (beyond what mpv's buffer holds) falls to the
            # archive. Shorter pauses resume seamlessly from the buffer, which
            # is the real pause - re-opening a tiny archive segment for them
            # just produced a stuttery 1-minute clip. Include any offset already
            # accrued from earlier short pauses so we land at the right spot.
            total = getattr(self, "_ts_live_offset", 0.0) + elapsed
            self._play_timeshift(it, back_min=total / 60.0)
        elif self.player and on_live and self._dvr_buffered():
            # Resumed in the rewind buffer: the DVR row says how far behind
            # live it is, and LIVE takes it there.
            self.player.set_live_badge(None)
            self._update_ts_timeline()
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

    def _dvr_buffered(self) -> bool:
        """The live channel on screen has a rewind buffer (the DVR row)."""
        try:
            return (self.player is not None
                    and self.player.local_buffer_window() is not None)
        except Exception:
            return False

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
        elif self.player.local_behind() is not None:
            # In the rewind buffer: it knows how far behind live it is.
            behind = self.player.local_behind() or 0.0
            content_time = now - behind
            offset = behind / 60.0
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
            if not self._detail_play():
                self.play("mpv")
        elif state == "stop":
            if self.player:
                # The button, not player.stop(): it is what also ends the
                # recording, the Trakt / Simkl scrobble and the wake lock.
                self.player.stop_btn.click()
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

    def _player_exit_fullscreen(self) -> None:
        """The player's own Exit fullscreen button: in TV mode that full
        screen is TV mode's, so TV mode ends (mw_tv) - never the window's
        lists with TV mode still on behind them."""
        if getattr(self, "_tv_on", False):
            self._tv_player_left_fullscreen()
            return
        self._exit_player_fullscreen()

    def _toggle_player_fullscreen(self) -> None:
        if not self.player or not self.player.isVisible():
            return
        if getattr(self, "_tv_on", False):
            # A double click on TV mode's picture, its fullscreen button:
            # out of TV mode's full screen, so out of TV mode.
            self._tv_player_left_fullscreen()
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
        self._enter_docked_fullscreen()

    def _enter_docked_fullscreen(self) -> None:
        """The docked player fills the window, the window the screen. TV
        mode (mw_tv) comes here straight: its window already fills the
        screen, so macOS needs no pop-out for it."""
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
        onboarding wizard if it's up, else leave the Guide page if it shows,
        otherwise leave fullscreen."""
        if self._welcome is not None and self._welcome.isVisible():
            self._welcome.dismiss()
            return
        # The Guide page on screen goes back where you were. (In fullscreen
        # the pane is hidden and this falls through to leaving it.)
        if self._guide_page_visible():
            self._leave_guide_page()
            return
        # ...and a series' page goes back to the place it came from.
        if self._series_page_visible():
            self._leave_series_page()
            return
        # The Search page: its field emptied first, then back where you were.
        if self._search_page_visible():
            self._search_escape()
            return
        # Mine the same: its search emptied first, then back where you were.
        if getattr(self, "_mitt_page_visible", lambda: False)():
            self._mitt_escape()
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
                    self._restore_normal_geometry(getattr(self, "_pre_fs_geo", None))
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

    def _restore_normal_geometry(self, geo, then=None) -> None:
        """showNormal() was asked: *geo* (the window's own rectangle) put
        back, then *then* run. On X11 and Wayland only once the window
        manager has taken the window out of full screen: set at once, it
        raced that and the window crept down and shrank by its title bar
        each time (mutter); on Wayland it shrank by Qt's own decorations
        even when left alone (weston). Elsewhere at once, as it always was."""
        platform = QGuiApplication.platformName()
        if platform != "xcb" and not platform.startswith("wayland"):
            if geo is not None:
                self.setGeometry(geo)
            if then is not None:
                self._after_fullscreen_left(then)
            return
        self._after_fullscreen_left(then, geo)

    def _after_fullscreen_left(self, then=None, geo=None) -> None:
        """*then* once the window is out of full screen - the window
        manager's size, not the screen's, twice in a row - or after a second
        and a half at most; *geo* set first if the window has another. A
        newer call's geometry wins, and what an earlier one was to run still
        runs (TV mode's put-back is never lost to a quick full screen)."""
        t = getattr(self, "_settle_timer", None)
        if t is None:
            t = self._settle_timer = QTimer(self)
            t.setInterval(20)
            t.timeout.connect(self._settle_tick)
        job = getattr(self, "_settle_job", None)
        thens = list(job["then"]) if job else []
        if then is not None:
            thens.append(then)
        self._settle_job = {"geo": geo, "then": thens, "until": time.monotonic() + 1.5, "seen": 0}
        t.start()

    def _settle_tick(self) -> None:
        job = getattr(self, "_settle_job", None)
        if job is None:
            self._settle_cancel()
            return
        scr = self.screen() or QApplication.primaryScreen()
        full = scr.geometry().size() if scr is not None else None
        if full is None or self.size() != full:
            job["seen"] += 1
        if job["seen"] < 2 and time.monotonic() < job["until"]:
            return
        self._settle_cancel()
        geo = job["geo"]
        if geo is not None and not (self.isFullScreen() or self.isMaximized()) \
                and self.geometry() != geo:
            self.setGeometry(geo)
        for fn in job["then"]:
            try:
                fn()
            except Exception:
                log.exception("the window after full screen")

    def _settle_cancel(self) -> None:
        self._settle_job = None
        t = getattr(self, "_settle_timer", None)
        if t is not None:
            t.stop()

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
        # Whole-catalogue copies built from the previous fetch (or provider).
        self._full_catalog = None
        self._search_index_cache = {}
        if force:
            self._search_source_refreshed()   # the Search page reads it anew
        self._clear_ts_broken()   # re-trust the provider's tv_archive flags
        pl = self.playlist_store.active() if self.playlist_store else None
        pid = (pl or {}).get("id")
        self.xmltv = XmltvGuide(
            self.client, (pl or {}).get("epg_url") or None,
            cache_path=epg_cache_path(pid) if pid else None,
            progress_cb=self._guide_progress(0))
        # The new guide keeps the guide offset (Settings, and a source's own
        # with Nerd); a refresh used to drop it until the app restarted.
        self.xmltv.delay_minutes = self._epg_delay_minutes()
        self._info_cache.clear()
        # Name the wait after what the user did (refresh the playlist), not the
        # guide reload that happens to be the slow part of it. Shown in the top
        # loading strip only (the bottom line stays the channel count).
        self._show_busy(tr("status_refreshing_playlist"))
        self._load_categories()
        # New/refreshed provider: re-learn which Browse modes it has and hide
        # the empty ones (Browse nav is shown by default until this resolves).
        self._refresh_mode_availability()
        self._load_guide(force=force, label=tr("status_refreshing_playlist"))

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
                                progress_cb=self._guide_progress(0))
        self.switch_mode("live")
        self._update_provider_hint()   # hides the '+ Add provider' hint
        self._show_toast(tr("demo_notice"), 8000)

    def switch_playlist(self, pid: str, then=None) -> None:
        """Use source *pid* from now on, once it answers; *then* runs after
        (the Search page plays a film of it, or opens a series)."""
        pl = self.playlist_store.get(pid) if self.playlist_store else None
        if not pl:
            return
        self._show_busy(tr("status_connecting", name=pl['name']))
        candidate = make_client(pl)

        def done(_auth):
            self._hide_busy()
            self.playlist_store.set_active(pid)
            self.client = candidate
            from ..core import nerd
            nerd.set_source(pl)
            # Another provider's category ids say nothing about this one's.
            self._last_cat = {}
            # The profile's own for this source: favorites, history, resume
            # points, reminders, followed series.
            self._build_source_stores(pid)
            self._watch_favorites()
            self.series_rules = SeriesRuleStore(self.settings, pid)
            # Hide/rename/lock rules are per playlist too: kept, the old
            # provider's locks applied here and edits saved under its key.
            self.overrides = CategoryOverrides(
                self.settings, f"category_overrides_{pid}")
            self.channel_ov = ChannelOverrides(
                self.settings, f"channel_overrides_{pid}")
            self._base_title = pl["name"]
            self.setWindowTitle(self._base_title)
            self._update_playlist_btn()
            self._update_provider_hint()
            self._refresh_nav_status()      # this playlist's followed series
            # Drop what was read of the previous (or empty offline) provider -
            # it means nothing for this one.
            self._recent_cache = {}
            self._guide_source_changed()    # the old source's guide goes
            self._sp_source_changed()       # and a series' page of it
            self._pf_source_changed()       # the Movies place and a film's page
            self._place_series_source_changed()   # the Series place's rows
            self._popular_changed()         # popular right now: this source's
            self._refresh_places("source")  # the places' own pages
            # Switching rebuilds the list but loads the guide from cache when
            # fresh - no forced network reload. A manual Refresh or the auto-
            # refresh time setting still drives a real re-fetch.
            self.refresh_playlist(force=False)
            if then is not None:
                then()

        def fail(msg):
            self._hide_busy()
            self._set_status("")
            # Nerd: its pinned server showed another certificate - said
            # plainly, with the new one to look at (ui/cert_trust.py).
            from .cert_trust import offer_review
            if offer_review(self, self.playlist_store, pl, self.switch_playlist):
                return
            QMessageBox.warning(
                self, tr("playlist_msg_title"),
                plain_text(tr("msg_could_not_connect", name=pl['name'], msg=msg)))

        run_async(self.pool, candidate.authenticate, done, fail)

    # -- metadata (TMDB artwork) -----------------------------------------------------

    @property
    def tmdb(self):
        """The active TMDB poster resolver (or None). Owned by the
        CoverArtService; exposed here because the detail panel, context
        menu and Trakt sync all reach the TMDB client through it."""
        return self.cover.resolver

    def _flush_poster_refresh(self) -> None:
        self.list_model.refresh_all()

    # -- a channel tuned from the guide -----------------------------------------------

    @from_pane
    def tune_from_guide(self, ch) -> None:
        """Play a channel picked from the EPG guide, then jump the middle
        column to it and select its category in the sidebar so the guide
        selection is reflected everywhere."""
        self.play_live_channel(ch)
        self._pending_jump_key = self._item_key(ch)
        self._pending_jump_cat = ch.get("category_id")
        QTimer.singleShot(8000, self._clear_pending_jump)
        # Tuned from the Guide page: the page stays, beside the player, and
        # the list follows the channel underneath it.
        keep = self._guide_page_showing()
        self._guide_hold = keep
        try:
            if self.mode != "live":
                self.switch_mode("live")
            else:
                self._load_categories()
        finally:
            self._guide_hold = False
        if keep:
            self._guide_followed_play()

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

    @staticmethod
    def _episode_rows(info: dict, series: dict) -> list:
        """A series_info answer's episodes as list rows, season by season
        (the list here and TV mode's)."""
        # Fall back to the show's own poster so an episode row isn't a
        # bare initial: prefer the episode still, then the series art.
        series_cover = (series.get("cover") or series.get("cover_big")
                        or series.get("stream_icon") or "")
        series_title = series.get("name") or series.get("title") or ""
        raw = info.get("episodes") if isinstance(info, dict) else None
        groups: list[tuple] = []
        if isinstance(raw, dict):
            groups = list(raw.items())
        elif isinstance(raw, list):
            # Seasons keyed 0..n come from a PHP panel as a list of lists
            # (the index is the season); a flat list says it on each row.
            groups = [(str(i), eps) if isinstance(eps, list) else (None, [eps])
                      for i, eps in enumerate(raw)]
        episodes = []
        for key, eps in groups:
            for ep in eps or []:
                if not isinstance(ep, dict):
                    continue
                season = key if key is not None else ep.get("season")
                ep["season"] = season
                ep["name"] = (
                    f"S{'?' if season is None else season} * E{ep.get('episode_num', '?')} - "
                    f"{ep.get('title') or tr('misc_episode')}")
                still = (ep.get("info") or {}).get("movie_image") or ""
                ep["stream_icon"] = (ep.get("stream_icon") or still
                                     or series_cover)
                # Let the cover pipeline resolve the show's TMDB poster for
                # each episode (its own name never matches TMDB).
                ep["_series_title"] = series_title
                episodes.append(ep)
        return episodes

    def _enter_series(self, series) -> None:
        """A series opened (from any list, Home, a search): its own page
        in the middle pane, over the list it came from (mw_series_page)."""
        if series.get("series_id") is None:
            return
        self._open_series_page(series)

    def _leave_series(self) -> None:
        # The back button is shared: in the Local files section it walks up
        # one directory instead of leaving a series.
        if self.mode == "local":
            self._local_up()
            return
        if self.mode == "radio":
            self._radio_up()
            return
        ctx = self.series_ctx or {}
        log.info("_leave_series: back to the series list (series_id=%s)",
                 for_log(ctx.get("series_id")))
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

    def _link_waits(self, plan) -> tuple | None:
        """(method, args, kwargs) of the address *plan* (the code that would
        ask the client for one) wants, when getting it would wait on the
        network - a portal's create_link, a media server opening a live
        channel (StalkerClient / BoxClient slow_links); None when it answers
        at once. *plan* runs against a stand-in that has the client's own
        methods and nothing else, so its getattr(client, ..., None) tests
        come out as they would on the client."""
        client = self.client
        if not getattr(client, "slow_links", False):
            return None
        asked: list = []

        class _StandIn:
            def __getattr__(self, name):
                if not hasattr(client, name):
                    raise AttributeError(name)

                def call(*a, **k):
                    asked.append((name, a, k))
                    return ""
                return call

        self.client = _StandIn()
        try:
            plan()
        except Exception:
            pass
        finally:
            self.client = client
        if not asked:
            return None
        name, a, k = asked[0]
        ready = getattr(client, "link_ready", None)
        if ready is None or not a or ready(name, a[0]):
            return None
        return name, a, k

    def _links_later(self, plan, again, key: str = "play") -> bool:
        """When the address *plan* wants is slow to get (_link_waits), fetch
        it off the window's thread and call *again* once it is in - the
        client then has it at once. Only the newest ask of each *key* goes
        on ("play": the player's; a multiview cell, a cast, a recording have
        their own), and only on the same provider. False when there is
        nothing to wait for: the caller goes on."""
        waits = MainWindow._link_waits(self, plan)
        if waits is None:
            return False
        client = self.client
        name, a, k = waits
        # A play from the middle pane stays one once its address is in
        # (mw_pane_follow).
        keep = getattr(self, "_keep_origin", None)
        if keep is not None:
            again = keep(again)
        # ...and a phone remote's request stays the phone's: its questions
        # are asked there, never left on this screen (mw_phone_rec).
        keep = getattr(self, "_phone_keep_asking", None)
        if keep is not None:
            again = keep(again)
        gens = getattr(self, "_link_gens", None)
        if gens is None:
            gens = self._link_gens = {}
        gens[key] = gen = gens.get(key, 0) + 1
        self._set_status(tr("status_loading_content"))

        def work():
            return getattr(client, name)(*a, **k)

        def done(_url) -> None:
            if gen == self._link_gens.get(key) and self.client is client:
                again()

        run_async(self.pool, work, done, lambda _e: done(""))
        return True

    def _stream_for(self, it) -> tuple[str | None, str]:
        title = it.get("name") or it.get("title") or "dopeIPTV"
        # Only with an episode id. Without one this produced
        # ".../series/user/pass/None.mp4" - not empty, so every "no url"
        # guard downstream waved it through and playback died on a path
        # that never existed. Same trap as the History replay had.
        if self.series_ctx and it.get("id") is not None:
            return self.client.episode_url(
                it.get("id"), it.get("container_extension")), title
        # Favorite movies/series route by the selected section, not by
        # 'fav' meaning live - only the Channels section plays live. In the
        # grouped "All favorites" view each row carries its own _kind, so a
        # channel row (_kind == "live") plays live regardless of section.
        if (self.mode == "live" or it.get("_kind") == "live" or (
                self.mode == "fav" and self._fav_section == "chan")):
            fmt = self.settings.value("stream_format", "ts")
            return self._live_stream_address(it.get("stream_id"), fmt), title
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

    def _live_stream_address(self, stream_id, fmt: str) -> str:
        """A live channel's address for the player: what a transcoding box
        gives (ui/mw_box.py), else the source's live_url."""
        play = getattr(self, "_live_play_url", None)
        if play is not None:
            return play(stream_id, fmt)
        return self.client.live_url(stream_id, fmt)

    def play_live_channel(self, it) -> None:
        fmt = self.settings.value("stream_format", "ts")
        if self._links_later(
                lambda: self.client.live_url(it.get("stream_id"), fmt),
                lambda: self.play_live_channel(it)):
            return
        url = self._live_stream_address(it.get("stream_id"), fmt)
        if not url and self._box_no_live(it):
            return
        title = it.get("name") or "dopeIPTV"
        self._start_playback(url, title, it.get("stream_icon"),
                             self._item_key(it), "live", item=it)

    def play(self, player=None, external: bool = False) -> None:
        it = self.list_model.item_at(self.listw.currentIndex().row())
        self.play_item(it, player, external)

    def play_item(self, it, player=None, external: bool = False) -> None:
        if not it or it.get("_header"):
            return
        # Another source's channel in Every source: from that source, the
        # source in use left as it is (mw_every_source) - never an address
        # of the source in use made from it.
        if it.get("_xs"):
            self._xs_play(it)
            return
        # A Continue-watching episode row plays straight from its stored series
        # context (it isn't reachable through the normal series drill-down).
        if it.get("_kind") == "episode" and it.get("_series_ctx") is not None:
            self._play_continue_episode(it, player, external)
            return
        # A radio station or a web stream (YouTube, Twitch), wherever its row
        # is: Radio, Favourites, History.
        row_kind = it.get("_kind") or it.get("_ekind")
        if self.mode == "radio" or row_kind in ("radio", "radiogroup"):
            self._radio_activate(it, player, external)
            return
        if row_kind == "web":
            self._web_activate(it, player, external)
            return
        # A SERIES row is never playable, whatever the drill state believes.
        # series_ctx alone used to decide that a row was an episode, so if
        # the list and that flag ever disagreed - a grid toggle that rebuilt
        # the list while series_ctx stayed set - a double-click took the
        # series row for an episode, built .../series/u/p/None.<ext> out of
        # an id it does not have, STOPPED whatever was playing and put an
        # error on screen. Trust the row, not the flag: drill in.
        # Any view, not just the Series section: a series row turns up in
        # Favorites, Watch Later, Watched and Continue watching too, and the
        # disagreement was REPORTED from Favorites - where the old
        # mode == "series" test would not have fired at all.
        if (it.get("series_id") is not None and it.get("id") is None
                and it.get("_kind") in (None, "series")
                and self.mode in ("series", "fav", "watchlist", "watched")):
            if self.series_ctx:
                log.info("play_item: series row while series_ctx is set "
                         "(mode=%s) - list and drill state disagree; entering "
                         "the series instead of playing a nonexistent "
                         "episode", self.mode)
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
            title = it.get("name") or tr("rec_status_recording")
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
                self._local_open_series(it.get("_series_title") or "", it)
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
            if self._links_later(
                    lambda: self._stream_for(it),
                    lambda: self.play_item(it, player, external)):
                return
            url, title = self._stream_for(it)
            icon = it.get("stream_icon") or it.get("cover")
            key, kind = self._item_key(it), self._play_kind_for(it)
        if not url:
            self._box_no_live(it)
            return

        if external or player == "vlc":
            # Which door playback went through - a silent external window
            # left no way to tell a deliberate "open externally" from the
            # embedded player refusing the stream.
            log.info("play: external requested (player=%r external=%r) %s",
                     player, external, for_log(title))
            if not self._confirm_external_while_playing():
                return
            if kind not in ("local", "recording") and self._vpn_guard_blocks(url):
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
            f"{tr('open_video_filter')} ({pattern});;"
            + tr("file_filter_all"),
            options=file_dialog_options(
                QFileDialog.Option.HideNameFilterDetails))
        if not path:
            return
        if not privacy.private():       # where a file watched privately lies
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

    def _is_audio(self, path: str) -> bool:
        return str(path or "").lower().endswith(self.AUDIO_EXTS)

    def _is_video(self, path: str) -> bool:
        return str(path or "").lower().endswith(self.VIDEO_EXTS)

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
            # Free the connection first - by the button, which also ends the
            # recording, the scrobbles and the wake lock.
            self.player.stop_btn.click()
            return True
        return idx == 1                 # 1 = open anyway; else cancel

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
        if it.get("_xs"):
            # Another source's channel (Every source): as it plays when
            # picked - from its own source, through the usual path.
            self._xs_play(it)
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
        sid = it.get("stream_id")
        fmt = self.settings.value("stream_format", "ts")
        if self._links_later(
                (lambda: self.client.live_url(sid, fmt)) if history_live
                and sid is not None else (lambda: self._stream_for(it)),
                self._play_preview):
            return
        if history_live:
            # Rebuild a fresh live URL from the stored stream_id (the snapshot's
            # _url may be stale), falling back to that snapshot when the row
            # predates stream_id enrichment.
            if sid is not None:
                fmt = self.settings.value("stream_format", "ts")
                url = self._live_stream_address(sid, fmt)
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
        if self._vpn_guard_blocks(url):
            return
        if self._cast_zap(url, title, it.get("stream_icon"),
                          self._item_key(it), "live", it, False):
            return
        self._failover_leave(url, self._item_key(it))
        self._reconnect_off = False
        # The preview path never reaches _start_playback, so it must carry the
        # same side effects itself: the multiview-conflict question (close it
        # and free its connections, or keep both) and the History entry -
        # with autoplay-preview on, this IS how channels get played.
        self._stop_cast_for_local_playback()
        self._maybe_close_multiview_for_playback()
        # What played before - a film, an episode - keeps its place, its
        # watched mark, and its Trakt / Simkl scrobble ends.
        self._save_resume_position()
        self._maybe_auto_mark_watched()
        self._trakt_stop_current()
        self._simkl_stop_current()
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
        self._note_playing_source(url, self._item_key(it), "live")
        self._last_playback = {"url": url, "title": title,
                               "icon_url": it.get("stream_icon"),
                               "key": self._item_key(it),
                               "kind": "live", "item": it}
        privacy.playback_started("live", self._item_key(it))
        note_titles(self._last_playback)
        self._sync_player_buttons()
        self.listw.viewport().update()
        self.setWindowTitle(title or self._base_title)
        self._set_status(self._status_playing(title))
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
        self._ts_from_portal = False
        self._ts_live_offset = 0.0
        self._pause_started = None
        # Nerd: the channel it is, for the settings of its own.
        self._chanprefs_arm(self._item_key(it), it, "live")
        self._ms_leave()
        # Music or a radio station before it put the visualiser up: taken
        # down, as _start_playback does - and only then (_vis_active), so an
        # ordinary preview still reaches mpv untouched.
        self._apply_audio_visuals(url)
        self._arm_loading(it.get("stream_icon"))
        self.player.refusal = ""
        if self.player.play(url, title, local_buffer=True,
                            buffer_key=f"live:{self._item_key(it)}"):
            self.wake.acquire(wake_reason(title))
            self._health_begin(self._item_key(it), title, it, "live", False)
            self._events_started(self._item_key(it), title, it, "live", False)
            self._together_started(self._item_key(it), title, it, "live", False)
            self._stats_begin(self._item_key(it), title, it, "live", False)
            self._discord_started(self._item_key(it), title, it, "live", False)
            self._ms_started(url, self._item_key(it), "live", it)
        elif getattr(self.player, "refusal", ""):
            self._show_stream_error(self.player.refusal)
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
        cs = self._side_settings(shared, "cache")
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

    @staticmethod
    def _side_settings(shared: QSettings, name: str) -> QSettings:
        """One of the files beside the app's settings - as the profile in
        use sees it when the app's settings are a profile's view
        (core/profiles.py)."""
        from ..core.profiles import ProfileSettings
        kind = ProfileSettings if isinstance(shared, ProfileSettings) else QSettings
        return kind(QSettings.Format.IniFormat, QSettings.Scope.UserScope, ORG, name)

    @classmethod
    def _open_follow_settings(cls, shared: QSettings | None = None) -> QSettings:
        """Followed series in a file of their own, for the same reason as the
        resume positions: each remembers every episode id it has seen, which
        grows with every series followed - kept in the shared settings, each
        check would have rewritten all of it on the main thread."""
        if shared is None:
            return QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                             ORG, "follow")
        return cls._side_settings(shared, "follow")

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
        rs = self._side_settings(shared, "resume")
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
        # A title watched privately keeps no resume point to the end of that
        # playback, though private viewing ended before it did.
        if not privacy.playback_private(lp.get("kind") or "", self._playing_key):
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
                if not self._vpn_guard_blocks(url):
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
        """Continue-watching rows of one kind ('vod' or 'episode'), a media
        server's own among them (mw_media)."""
        return [it for it in self._continue_rows()
                if it.get("_kind") == kind]

    def _remove_continue(self, it) -> None:
        """Forget a title's resume point (from the Continue-watching menu),
        then refresh the list - the category disappears once it's empty."""
        group = "episode" if it.get("_kind") == "episode" else "vod"
        self.resume.clear(group, self._item_key(it))
        self._ms_forget(it)
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

    def _resume_offset(self, key, kind: str, item=None) -> float:
        """Ask whether to resume a partly-watched title; return the start
        offset in seconds (0 to start from the beginning). A media server's
        own resume point counts when it is the newer (mw_media)."""
        pos = self._ms_resume_at(key, kind, item,
                                 self.resume.saved_position(key, kind))
        if pos <= 0:
            # Nothing to offer: either never watched >1 min, watched past 95%
            # (counted as finished), or saved under another kind pre-fix.
            log.debug("resume: no saved position for %s (kind=%s)", for_log(key), kind)
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
        # Something else picked: the phone remote's sheet is read again.
        self._track_ver = getattr(self, "_track_ver", 0) + 1
        lp = getattr(self, "_last_playback", None) or {}
        kind, key = lp.get("kind"), lp.get("key")
        if kind not in self._RESUMABLE or key is None:
            return
        if privacy.playback_private(kind, key):
            return      # a title's track choice says it was watched
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
        if last and (last.get("_private") or privacy.playback_private(
                last.get("kind") or "", last.get("key"))):
            # Watched privately - though private viewing ended before it
            # did: the mark would reach the media server and Trakt / Simkl.
            # Kept on the playback itself: this is asked again when the
            # next title starts, after Stop has ended the private playback.
            last["_private"] = True
            return
        if privacy.private():
            return                     # private viewing marks nothing
        if not last or last.get("kind") not in ("movie", "episode", "recording"):
            return
        if last.get("_auto_marked"):
            return                     # once per playback session
        pct = max(self._playback_max_pct,
                  self.player.progress_percent() if self.player else 0.0)
        if pct < self._AUTO_WATCHED_PCT:
            return
        last["_auto_marked"] = True
        if last["kind"] == "recording":
            # A recording watched to its end: Watched on its card
            # (mw_place_rec) - this computer's file, nothing sent anywhere.
            getattr(self, "_rec_auto_watched", lambda _k: None)(last.get("key"))
            return
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

    def _stoppable(self) -> bool:
        """Whether Stop has something to stop: a stream in the player, or a
        channel waiting for the app's reconnect - no address in the player
        then (its 0.3 s / 1.5 s timer, a backup address asked for), yet it
        would start again by itself. Stop pressed then is the Stop button's
        click all the same, and _on_player_stopped puts the reconnect off
        (the profile switch stops on the same)."""
        p = self.player
        return p is not None and bool(getattr(p, "current_url", None)
                                      or getattr(self, "_playing_group", None))

    def _on_player_stopped(self) -> None:
        """The Stop button was pressed. Save the resume point while the player
        still knows the position, then clear the now-playing highlight/title.
        _last_playback is kept so Play can bring this title back."""
        self._save_resume_position()
        self._maybe_auto_mark_watched()
        self._failover_stopped()
        # A reconnect under way (its timer, a backup address asked for) is
        # no longer wanted: Stop is Stop.
        self._reconnect_off = True
        # Nor a channel whose address is still being asked for (a portal's
        # link, _links_later): it would start after the Stop.
        gens = getattr(self, "_link_gens", None)
        if gens is not None and "play" in gens:
            gens["play"] += 1
        privacy.playback_stopped()
        self._playing_key = None
        self._playing_group = None
        self._playing_item = None
        self._sync_player_buttons()
        # Redraw the poster overlay: without this it froze on the last playing
        # state's glyph (a plain live channel leaves it stuck on 'stop').
        self._apply_play_icon()
        self.listw.viewport().update()
        self.setWindowTitle(self._base_title)
        self._source_status_stopped()     # the list's line again, its source with it
        self._sp_refresh_soon()        # where it stopped, on a series' page
        pf = getattr(self, "_pf_refresh_soon", None)
        if pf is not None:
            pf()                       # ...and on the Movies place, a film's page
        place = getattr(self, "_place_series_playing", None)
        if place is not None:
            place()                    # ...and on the Series place's Continue
        local = getattr(self, "_place_local_playing", None)
        if local is not None:
            local()                    # ...and Local files' Continue and mark
        rec = getattr(self, "_place_rec_playing", None)
        if rec is not None:
            rec()                      # ...and the Recordings place's mark
        radio = getattr(self, "_place_radio_playing", None)
        if radio is not None:
            radio()                    # ...and the Radio place's

    def _autoplay_next_episode(self) -> bool:
        return self.settings.value("autoplay_next_episode", "true") == "true"

    def _episode_step_item(self, direction: int = 1):
        """The episode after (1) or before (-1) the one playing or played
        last, in its queue, as (item, queue, index, series_ctx) - or None."""
        last = getattr(self, "_last_playback", None)
        if not last or last.get("kind") != "episode":
            return None
        queue = last.get("ep_queue") or []
        idx = last.get("ep_index", -1)
        to = idx + (1 if direction > 0 else -1)
        if idx < 0 or not 0 <= to < len(queue):
            return None
        return queue[to], queue, to, last.get("series_ctx")

    def _next_episode_item(self):
        """The next episode after the one currently/last playing, as
        (item, queue, next_index, series_ctx) - or None if there isn't one."""
        return self._episode_step_item(1)

    def _has_next_episode(self) -> bool:
        return self._next_episode_item() is not None

    def _has_previous_episode(self) -> bool:
        return self._episode_step_item(-1) is not None

    def _advance_to_next_episode(self, ask: bool = True) -> bool:
        """Play the next episode in the current series, carrying the episode
        queue forward so it keeps advancing. Returns False if there's no next
        episode or we're not on the embedded player."""
        return self._advance_episode(1, ask)

    def _advance_episode(self, direction: int, ask: bool = True) -> bool:
        """The next (1) or previous (-1) episode in the queue of the one
        playing - autoplay's, the player's button, the media keys, the phone.
        *ask* False: where it got to without the resume question (a remote
        has no one at the computer to answer it)."""
        nxt = self._episode_step_item(direction)
        # On a running cast the next one goes to the TV (mw_cast), whatever
        # this computer plays with.
        casting = getattr(self, "_cast_runs", None)
        if not nxt or (self.playback_mode() != "embedded"
                       and not (callable(casting) and casting())):
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
            key = self._item_key(item)
            if not ask:
                self._start_at_once(key, "episode", item)
            self._ep_queue_override = queue
            self._ep_index_override = index
            self._start_playback(
                url, title, item.get("stream_icon") or item.get("cover"),
                key, "episode", record=True, item=item)
            return True
        finally:
            self.series_ctx = saved
            self._sp_start_once = None

    def _start_at_once(self, key, kind: str, item=None) -> None:
        """The next _start_playback of *key* starts where it got to, without
        the resume question (once; the caller clears _sp_start_once after)."""
        pos = self._ms_resume_at(key, kind, item, self.resume.saved_position(key, kind))
        self._sp_start_once = (key, pos) if pos > 0 else None

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
                # A portal's archive link is one programme: a go-back goes
                # on with the next (mw_portal_archive).
                if it is not None and self._portal_archive_next(it):
                    return
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
        if last and last.get("kind") == "recording":
            # A recording played to its end: where it stopped dropped, and
            # Watched on its card.
            self._save_resume_position()
            self._maybe_auto_mark_watched()
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
        # Play after a Stop plays here, as it always has - not on a cast.
        self._start_playback(last["url"], last["title"], last["icon_url"],
                             last["key"], last["kind"], record=False,
                             item=last["item"], to_cast=False,
                             own_buffer=last.get("own_buffer", True))

    def _start_playback(self, url: str, title: str, icon_url,
                        key, kind: str, record: bool = True,
                        item=None, catchup: bool = False,
                        start_at: float = 0.0, to_cast: bool = True,
                        own_buffer: bool = True) -> None:
        # start_at: how far into a catch-up link to start (a portal's
        # archive link plays a programme from its own start).
        # to_cast: a live channel may go to a running cast (False: here).
        # own_buffer False: an address opened by hand - mpv opens it, never
        # dopeIPTV's own rewind buffer (it need not be a stream at all).
        # The app's reconnect (_retry_last_stream) of this very stream says
        # "Reconnecting…" over the picture while it opens; anything else is
        # a fresh start.
        reconnect = key is not None and getattr(self, "_reconnect_key", None) == key
        self._reconnect_key = None
        # The same title again (a reconnect, a backup address, the archive):
        # the middle pane is left where it is (mw_pane_follow).
        restart = reconnect or catchup or (
            key is not None and str(key) == str(getattr(self, "_playing_key", None))
            and getattr(self, "_playing_group", None) is not None)
        # A kids profile plays only what its lists may show, however it was
        # asked (mw_profiles).
        if not self._kids_may_play(kind, item, key):
            return
        if not self._guard_stream_switch(url, title):
            return
        # The guard is about streams: the user's own files always play.
        if kind not in ("local", "recording") and self._vpn_guard_blocks(url):
            return
        # A cast runs: a live channel goes to the same receiver and the
        # player stays as it is (mw_cast). Stopping the cast is explicit.
        if to_cast and self._cast_zap(url, title, icon_url, key, kind, item,
                                      catchup):
            return
        self._failover_leave(url, key)
        self._reconnect_off = False
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
            self._ts_from_portal = False
            self._ts_live_offset = 0.0   # fresh tune = at the live edge
            self._pause_started = None   # per-channel: don't carry a stale pause
        # Remember where we were in whatever was playing before switching,
        # and give the outgoing title its watched mark if it earned one.
        self._save_resume_position()
        self._maybe_auto_mark_watched()
        self._ms_leave()          # a media server hears where it got to
        # Watch together: what the room plays starts where the room is,
        # without the resume question.
        together_at = self._together_take_start()
        if together_at is None:
            # A series page's Continue: where it got to, without the question.
            together_at = self._sp_take_start(key)
        if together_at is not None:
            resume_at = together_at
        else:
            resume_at = (self._resume_offset(key, kind, item)
                         if kind in self._RESUMABLE else 0.0)
        if catchup and start_at > 0:
            resume_at = float(start_at)
        self._trakt_stop_current()
        self._simkl_stop_current()
        if record and kind:
            self.history.add(url, title, icon_url, key, kind,
                             extra=self._history_extra(kind, item, title))
        self._watch_accounts_start(kind, key, item)
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
        self._note_playing_source(url, key, kind)
        self._last_playback = {"url": url, "title": title, "icon_url": icon_url,
                               "key": key, "kind": kind, "item": item,
                               "series_ctx": (self.series_ctx
                                              if kind == "episode" else None),
                               "own_buffer": own_buffer}
        privacy.playback_started(kind, key)
        note_titles(self._last_playback)
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
        self._sync_mark_play()     # settings sync waits out a stream's start
        # New stream: reset the failure-diagnosis guard (a definitive-probe or
        # the full diagnosis may show once per playback attempt).
        self._diag_gen = getattr(self, "_diag_gen", 0) + 1
        self._diag_shown = False
        self._sync_player_buttons()
        self._playing_key = key
        self._playing_group = {"live": "live", "movie": "vod",
                               "episode": "episode",
                               "recording": "rec",
                               "local": "local", "radio": "radio",
                               "web": "web"}.get(kind)
        # A series' page marks what plays and goes to it (autoplay's next).
        self._sp_playback_started(key, kind)
        # ...and the column under the video shows the episode's series.
        if kind == "episode":
            try:
                self._detail_follow_episode(item, key)
            except Exception as e:
                log.debug("episode detail refresh failed: %s", e)
        # ...and the Movies place and a film's page mark it (mw_place_films).
        pf = getattr(self, "_pf_playback_started", None)
        if pf is not None:
            pf(key, kind)
        # ...the Series place its series (and Continue, a moment later).
        place = getattr(self, "_place_series_playing", None)
        if place is not None:
            place()
        # ...and Local files its file, Recordings its recording, Radio its
        # station.
        local = getattr(self, "_place_local_playing", None)
        if local is not None:
            local()
        rec = getattr(self, "_place_rec_playing", None)
        if rec is not None:
            rec()
        radio = getattr(self, "_place_radio_playing", None)
        if radio is not None:
            radio()
        # The middle pane goes to what plays, when it came from elsewhere.
        follow = getattr(self, "_pane_follow_soon", None)
        if follow is not None:
            follow(kind, key, restart)
        # The TV place's Recently watched: History's new title, its mark.
        played = getattr(self, "_place_tv_played", None)
        if played is not None:
            played()
        self.listw.viewport().update()
        self.setWindowTitle(title or self._base_title)
        self._set_status(self._status_playing(title))
        log.info("play: embedded path kind=%s player=%s %s",
                 kind, "yes" if self.player else "NO", for_log(title))
        if self.player:
            self.rec.finish_all_inplayer("channel changed")
            self.player.show()
            self.player.set_overlay_info(title)
            # A radio station is music too: the visualiser and equaliser.
            self._apply_audio_visuals(url, audio=kind == "radio")
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
                # A subtitle fetched or picked for this title before.
                self.player.set_sub_files(
                    self._remembered_subtitles(kind, key))
            # Nerd: a live channel (its catch-up too) plays with the
            # settings of its own; anything else with the app's.
            self._chanprefs_arm(key, item, kind)
            before = getattr(self, "_tv_before_play", None)
            if before is not None:
                before()
            self._arm_loading(icon_url, reconnect)
            # own_buffer False is an address opened by hand (a dopeiptv://
            # link, --play, the remote API, Open address…): the source's
            # Nerd headers only on the source's own server. A radio station
            # is no source's either: none of the source's headers go to it,
            # and where it is decides its way out (core/net_bridge.py).
            outside = kind == "radio" or (not own_buffer and self._from_outside(url))
            self.player.refusal = ""
            if self.player.play(url, title, start=resume_at,
                                local_buffer=kind == "live" and not catchup,
                                buffer_key=f"{kind}:{key}" if key else None,
                                web=kind == "web", own_buffer=own_buffer,
                                outside=outside):
                self.wake.acquire(wake_reason(title))
                self._health_begin(key, title, item, kind, catchup)
                self._events_started(key, title, item, kind, catchup)
                self._together_started(key, title, item, kind, catchup)
                self._stats_begin(key, title, item, kind, catchup)
                self._discord_started(key, title, item, kind, catchup)
                if not catchup:
                    self._ms_started(url, key, kind, item, resume_at)
                # TV mode: the picture fills the screen, whoever asked.
                self._tv_started(kind)
            elif getattr(self.player, "refusal", ""):
                # Not opened on purpose (its certificate cannot be checked
                # here): said on the video - never another player instead.
                self._show_stream_error(self.player.refusal)
                if getattr(self, "_tv_on", False):
                    self._tv_refused()
            elif getattr(self, "_tv_on", False):
                # TV mode fills the screen: another window would open
                # behind it, out of reach of the remote.
                log.warning("embedded play() refused %s in TV mode",
                            url_for_log(url))
                self._show_stream_error(tr("tv_play_refused"))
                self._tv_refused()      # said on its page, back on it
            else:
                log.warning("embedded play() refused %s - falling back to an "
                            "external mpv window", url_for_log(url))
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

    def _arm_loading(self, icon_url, reconnect: bool = False) -> None:
        """What the player's loading indicator shows while the next stream
        opens: the channel's logo or the title's poster when the window
        holds it in memory already - never a download for it, the name
        alone otherwise - and whether it opens again what dropped."""
        # Right before player.play(): nothing here may keep a stream from
        # playing - an icon that is no address (a provider's odd JSON) is
        # the name alone.
        pm = None
        if icon_url and isinstance(icon_url, str):
            try:
                for loader in (getattr(self, "logos", None), getattr(self, "poster_art", None)):
                    hit = getattr(loader, "cache", {}).get(icon_url)
                    if hit is not None and not hit.isNull():
                        pm = hit
                        break
            except Exception as e:
                log.debug("loading indicator: no picture: %s", e)
                pm = None
        try:
            self.player.set_loading_art(pm, reconnect=reconnect)
        except Exception as e:
            log.debug("loading indicator not armed: %s", e)

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
            self._update_ts_timeline()
            self.player.set_live_badge("timeshift")
        elif ts_days > 0:
            # Span a recent window (see _TS_TIMELINE_MAX_MIN), not the whole
            # multi-day archive: a small drag over days jumped hours/days back.
            self._ts_depth_min = min(ts_days * 1440, self._TS_TIMELINE_MAX_MIN)
            self.player.set_seek_mode("timeline")
            self.player.enter_timeshift(self._ts_depth_min)
            self._update_ts_timeline()
            self.player.set_live_badge(None)
        else:
            self.player.set_seek_mode("live")
            self.player.set_live_badge(None)


    def _vpn_guard_blocks(self, url: str = "") -> bool:
        """Nerd's VPN guard: with it on and no VPN up, nothing streams -
        said once, plainly, with where to change it. The rule recordings
        and multiview ask too (nerd.blocks_stream): the user's own files
        always play, and a box's source or a stream at a home address is on
        the home network."""
        from ..core import nerd
        src = nerd.source() or {}
        if not nerd.blocks_stream(url, src.get("_kind")):
            return False
        self._show_toast(tr("nerd_vpn_blocked"), 6000)
        note = getattr(self, "_phone_note", None)   # a play the phone asked for
        if note is not None:
            note(tr("nerd_vpn_blocked"))
        return True

    def _loading_message(self) -> str:
        return {"live": tr("status_loading_channels"),
                "vod": tr("status_loading_movies"),
                "series": tr("status_loading_series")}.get(
            self.mode, tr("status_loading_content"))

    MAX_STREAM_RETRIES = 2

    def _release_wake(self) -> None:
        """Let the screen sleep again - unless multiview is still showing
        video, which has no wake lock of its own."""
        win = getattr(self, "_multiview_win", None)
        if win is not None and any(c.url for c in win.cells):
            return
        self.wake.release()

    def _playback_error(self, msg: str) -> None:
        self.rec.finish_all_inplayer("stream error")
        self._release_wake()
        self._trakt_active = None
        self._simkl_active = None
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
            self._health_drop()
            QTimer.singleShot(1500, self._retry_last_stream)
            return
        self._stream_retries = 0
        if self.player:
            self.player.current_url = None
        # A station or a web page is no source's: nothing to diagnose or
        # fail over to, just why it did not play.
        if (lp or {}).get("kind") in ("radio", "web"):
            self._events_failed()
            self._discord_stopped()
            self._show_stream_error(tr("radio_play_failed")
                                    if lp.get("kind") == "radio"
                                    else tr("web_play_failed"))
            return
        # The reconnects are spent: for stream health, it did not play.
        self._health_failed(msg)
        self._events_failed()
        self._discord_stopped()
        self._ms_leave()
        self._together_stopped()
        url = (lp or {}).get("url")
        # Nerd: the same channel from another source, before giving up.
        if url and self._try_failover(lambda u=url: self._diagnose_stream_failure(u)):
            return
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
        # Said here, on the video - never repeated as a message.
        self._stream_failed_quietly()
        if self._player_fs and self.player:
            self.player.set_overlay_info(tr("status_stream_error", msg=text))
        else:
            self.stream_error.setText(tr("status_stream_error", msg=text))
            self.stream_error.show()
        if self.player:
            self.player.title_lbl.setText("")
            self.player.hide_loading()

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
        hkey = self.health.current

        def done(code, gen=gen, item=item):
            # Nerd's backup addresses: a server that is down, asked again.
            self._mirror_probe_said(url, code)
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
            self._health_failed(reason, hkey)
            self._show_stream_error(reason)
            if reason == tr("diag_not_started"):
                self._offer_upcoming_actions(item)

        run_async(self.pool, lambda: stream_status(url), done, lambda _e: None)

    def _diagnose_stream_failure(self, url: str) -> None:
        from ..providers.diagnostics import diagnose_stream

        lp = getattr(self, "_last_playback", None)
        hkey = self.health.current

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
            self._health_failed(reason, hkey)
            self._show_stream_error(reason)
            if reason == tr("diag_not_started"):
                self._offer_upcoming_actions(it)
            elif reason == tr("diag_certificate"):
                self._tls_offer(lp)

        run_async(self.pool, lambda: diagnose_stream(url, self.client),
                  show, lambda _e: show(tr("diag_generic")))

    #: What plays from a source - a station, a web page, a file or a
    #: recording is none's, and has nothing to let play unchecked.
    _TLS_NO_SOURCE = frozenset({"radio", "web", "local", "recording"})

    def _tls_offer(self, lp: dict | None) -> None:
        """A stream of the source in use whose certificate did not check out
        (the player checks every internet stream's: core/nerd.
        checks_certificate): asked once, on the decision card, whether the
        source may play without it - stored as the source's own option
        (nerd.TLS_UNCHECKED, taken back on its page), and the stream tried
        again. Nothing is played unchecked without that answer."""
        from ..core import nerd
        pl = self.playlist_store.active() if self.playlist_store else None
        if (not pl or not lp or lp.get("kind") in self._TLS_NO_SOURCE
                or nerd.tls_unchecked(pl.get("options"))):
            return
        pid = str(pl.get("id") or "")
        name = str(pl.get("name") or pl.get("server") or "")

        def go() -> None:
            now = self.playlist_store.get(pid) if self.playlist_store else None
            if not now:
                return
            options = dict(now.get("options") or {}, **{nerd.TLS_UNCHECKED: "true"})
            self.playlist_store.update(pid, options=options)
            nerd.set_source(dict(now, options=options))
            log.info("tls: the person let a source play without its certificate checked")
            self._diag_shown = False
            self._stream_retries = 0
            self._retry_last_stream()

        self._show_action_toast(tr("tls_offer", name=name), 0, go,
                                label=tr("tls_offer_go"), decide=True,
                                key=f"tls-offer-{pid}")

    def _style_media_info(self) -> None:
        """The detail panel's info box, in the theme's colours and corners."""
        self.media_info.setStyleSheet(rounded(
            f"QFrame#MediaInfo {{ background:{P['input']}; "
            f"border:1px solid {P['border_in']}; border-radius:12px; }}"))

    def _on_player_stalled(self) -> None:
        """The player reported the live stream frozen (buffer-starved)."""
        self._reconnect_live("stall")

    def _on_stream_contested(self) -> None:
        """The rewind buffer keeps losing its provider connection - a tug of
        war with another device or app on the account, which a player that
        reconnects at once never ends by itself."""
        key = (getattr(self, "_last_playback", None) or {}).get("key")
        if self._contest.claim(key):
            self._tell_contested()

    def _tell_contested(self) -> None:
        """Say the account is being fought over: its figures when the
        provider gives them ("all 1/1 connections are in use"), else what it
        looks like."""
        from ..providers.diagnostics import account_reason
        client = self.client

        def show(reason) -> None:
            self._show_toast(reason or tr("stream_contested"), 15000)

        run_async(self.pool, lambda: account_reason(client), show,
                  lambda _e: show(None))

    def _reopen_live_edge(self) -> None:
        """LIVE in the DVR row with the buffer far behind the channel: open
        the channel again, the way a reconnect does. Not while anything
        records - a new stream ends a recording made in the player, and
        another may hold the account's only connection - nor in an archive:
        then LIVE goes to the end of the buffer, as it did before."""
        lp = getattr(self, "_last_playback", None)
        recording = any(j.get("status") == "recording" for j in self.rec.jobs)
        if (not lp or lp.get("kind") != "live" or recording
                or getattr(self, "_playing_catchup", False)):
            self.player.go_to_buffer_end()
            return
        # A diagnosis shown for an earlier failure would make the retry a
        # no-op, and the channel plays: this is a fresh start, as a click is.
        self._diag_shown = False
        self._retry_last_stream()

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
            if reason == "eof":
                # Ended, and nothing opens it again: it holds no connection.
                self._conn_gave_up()
            return
        if not lp or lp.get("kind") != "live":
            return
        if getattr(self, "_playing_catchup", False):
            # Catch-up/archive playback: seeking makes mpv briefly go idle,
            # which looks like a stall. Never fall back to the live edge here
            # (that yanked the user out of the archive) - the segment is
            # seekable, so let mpv settle after the seek.
            return
        self._health_drop()
        if not self._auto_reconnect_live():
            msg = tr("status_stream_dropped")
            self._set_status(msg, emphasis=True)
            self.player.set_overlay_info(msg)
            self.player.hide_loading()
            if reason == "eof":
                # Ended, and not opened again: the picture stays until Stop,
                # but it holds no connection (a stall may still be read).
                self._conn_gave_up()
            return
        if reason == "eof":
            # The provider ended a stream that was playing. Drops like that,
            # over and over, are another device taking the account's
            # connection - the rewind buffer has its own watch for the same
            # thing. Only counted here; the reconnect below is unchanged.
            try:
                if self._contest.drop(lp.get("key"),
                                      self.player.playback_position()):
                    log.info("live stream keeps being dropped after playing: "
                             "another device on the account?")
                    self._tell_contested()
            except Exception:
                log.debug("contest watch failed", exc_info=True)
        now = time.time()
        if now - getattr(self, "_last_stream_error_ts", 0.0) > 20:
            self._stream_retries = 0
        if getattr(self, "_stream_retries", 0) >= self.MAX_STREAM_RETRIES:
            # Quick budget spent. Don't die - keep trying on a slow timer so a
            # channel that comes back (transient drop, provider hiccup) resumes
            # on its own instead of staying frozen. Armed once; re-armed each
            # slow attempt. A successful play resets the counters after 20 s.
            self._health_stuck()
            self._arm_slow_reconnect()
            # A box's channel it cannot decrypt is said so (ui/mw_box.py).
            told = getattr(self, "_box_no_picture", None)
            if told is not None:
                told()
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
        if getattr(self, "_ts_from_portal", False):
            # The portal's own link for the programme, not a guessed
            # address format - and a programme's file need not be seekable.
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
        # Stop was pressed (or a profile switched, a cast took over) while
        # the reconnect waited: the stream must not come back by itself.
        if getattr(self, "_reconnect_off", False):
            log.info("retry skipped: playback was stopped meanwhile")
            return
        lp = getattr(self, "_last_playback", None)
        if not lp or lp.get("kind") != "live":
            log.info("retry skipped: last playback is %r, not live",
                     (lp or {}).get("kind"))
            return
        # Its start says "Reconnecting…" - also when a portal's new link
        # comes later (_links_later); _start_playback takes it.
        self._reconnect_key = lp.get("key")
        self._health_retry()
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
            # A portal's link may be good once: ask it for a new one.
            forget = getattr(self.client, "forget_link", None)
            if forget is not None:
                forget(it["stream_id"])
            self.play_live_channel(it)
        elif lp.get("url") and not lp.get("own_buffer", True):
            # An address opened by hand, again as it was opened: as it is,
            # and out of Recently viewed.
            self._start_playback(lp["url"], lp["title"], lp.get("icon_url"),
                                 lp.get("key"), "live", record=False,
                                 own_buffer=False)
        elif lp.get("url"):
            # Another source's channel (failover, the Search page) was kept
            # out of Recently viewed when it started: its reconnect too.
            other = getattr(self, "_failover_row_for", None)
            kw = ({"record": False}
                  if other is not None and other(lp.get("key")) is not None else {})
            self._start_playback(lp["url"], lp["title"], lp.get("icon_url"),
                                 lp.get("key"), "live", **kw)

    def _last_channel(self) -> None:
        """Jump back to the previously watched live channel (TV 'last' key)."""
        if self._typing():
            return
        prev = getattr(self, "_prev_live_item", None)
        if prev:
            # play_live_channel -> _start_playback records the channel we're
            # leaving as the new 'previous', so pressing it again bounces back.
            self.play_live_channel(prev)

    # How long a typed channel number waits for its next digit.
    CHAN_ENTRY_MS = 1500

    def _channel_digit(self, digit: str, from_player: bool = False) -> None:
        """Accumulate a typed channel number and jump after a short pause:
        typed in the channel list (TV, Favorites), or - *from_player* - over
        the picture while a live channel plays, whatever the list shows."""
        if not (len(digit) == 1 and digit.isdecimal()):
            return
        if self.mode not in ("live", "fav") and not (from_player and self._live_playing()):
            return
        self._chan_buffer = (self._chan_buffer + str(int(digit)))[:5]
        self._chan_entry_show(self._chan_buffer, self.CHAN_ENTRY_MS + 1000)
        self._chan_timer.start(self.CHAN_ENTRY_MS)

    def _live_playing(self) -> bool:
        if self._player_up() and getattr(self, "_playing_item", None) is not None:
            return True
        # A channel on a cast: a typed number zaps it too (mw_cast).
        return self._cast_live_item() is not None

    def _chan_entry_show(self, text: str, ms: int) -> None:
        """The digits typed (or that no channel has them) on screen: over
        the picture while the player is up, else as a note in the window."""
        p = self.player
        if p is not None and p.isVisible() and p.current_url is not None:
            p.show_number_entry(text, ms)
        elif text:
            # One card that follows the digits as they are typed.
            hub_of(self).post(tr("chan_entry", num=text) if text.isdigit() else text,
                              ms=ms, key="chan_entry")
        else:
            hub_of(self).dismiss_key("chan_entry")

    def _chan_not_found(self, number) -> None:
        self._chan_entry_show(tr("chan_not_found", num=number), 2500)

    def _channel_jump(self) -> None:
        from ..core import channel_numbers
        self._chan_timer.stop()
        buf = self._chan_buffer
        self._chan_buffer = ""
        if not buf:
            return
        self._chan_entry_show("", 0)
        if self.mode in ("live", "fav") and not self.series_ctx:
            # The list on screen first: the channel is selected there too.
            rows = [self.list_model.item_at(i) for i in range(self.list_model.rowCount())]
            found = channel_numbers.find([r for r in rows if r], buf,
                                         channel_numbers.active(self._chno_pid()))
            if found is not None:
                target = next(i for i, r in enumerate(rows) if r is found)
                idx = self.list_model.index(target)
                self.listw.setCurrentIndex(idx)
                self.listw.scrollTo(idx)
                self.play()
                return
        # The whole source next - from a channel list, or over a channel
        # that plays; never instead of a film in Favorites' Movies.
        if self._content_kind() in ("live", "fav") or self._live_playing():
            self._play_channel_number(int(buf))
        else:
            self._chan_not_found(buf)

    def _zap(self, direction: int) -> None:
        # Music: the buttons step the play queue, not the browsing list -
        # what a listener means by previous/next track.
        if self._queue_step(direction):
            return
        # A channel plays while the list shows no channels (Series, Movies):
        # channel up and down go through the source's channels as a remote's
        # do (mw_commands), and the middle pane follows (mw_pane_follow).
        lp = getattr(self, "_last_playback", None) or {}
        if (lp.get("kind") == "live" and self._playing_group == "live"
                and (self.mode not in ("live", "fav") or self.series_ctx)
                and self._play_channel_step(direction)):
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

    def _remove_history_selected(self, clicked_item=None) -> None:
        items = [self.list_model.item_at(ix.row())
                 for ix in self.listw.selectionModel().selectedRows()]
        items = [it for it in items if it]
        if not items and clicked_item:
            items = [clicked_item]
        for it in items:
            self.history.remove(it.get("_key"), it.get("_kind"))
        if items:
            # The category on screen, not "All": the sidebar still shows
            # e.g. Movies, and the list jumped to everything under it.
            self._load_items(getattr(self, "_history_subcat", None))

    def _delete_pressed(self) -> None:
        if self._profile_locked():
            return          # who is watching first: nothing of theirs goes
        if self.mode == "history":
            self._remove_history_selected()
        elif self.mode == "rec":
            self._delete_recordings_selected()

    # -- recordings ----------------------------------------------------------------


    def _error(self, msg: str) -> None:
        self._hide_busy()
        self._set_status(tr("status_error", msg=msg), error=True)

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
        # Nor in TV mode, nor while the window comes back from it: that
        # size is the screen's, and the panes' widths are not the person's.
        if self.isFullScreen() or self._player_fs or getattr(self, "_tv_on", False) \
                or getattr(self, "_tv_restoring", None) is not None:
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
        self._profile_picker_fit()
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
            self._ms_close()
            self._sync_on_close()
            self._stats_close()
        self.wake.release()
        self._stop_media_session()
        self._together_close()
        self._tsa_quit()
        self._events_quit()
        self._discord_quit()
        self._remote_stop()
        self.phone_remote_stop(keep=True)
        self._server_stop(wait=True)
        self.cover.flush()
        if self._trakt_active:
            active = self._trakt_active
            progress = self.player.progress_percent() if self.player else 0.0
            threading.Thread(
                target=lambda: self.trakt.scrobble_stop(
                    active["payload"], progress),
                daemon=True).start()
        self._simkl_stop_current()
        self.rec.shutdown()
        if self.player:
            self.player.shutdown()
        # Now that the player has let go of an in-player recording's file.
        self.rec.after_shutdown()
        # The app exits via os._exit (here on stuck workers, and always after
        # the event loop - see app.main), which skips QSettings' auto-sync on
        # destruction. Flush every settings file explicitly so this session's
        # layout, resume points and watched/cache writes actually persist.
        for st in (self.settings, self._resume_settings, self._cache_settings,
                   self._follow_settings):
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
        # A Local files search ends its folder and stops (mw_local); what it
        # watched on disk is let go, the watcher's thread ended.
        walk = getattr(self, "_local_walk", None)
        if walk is not None:
            walk.cancel()
        getattr(self, "_lw_shutdown", lambda: None)()
        for pool in (self.pool, self._logo_pool, self._art_pool,
                     getattr(self, "_local_walk_threads", None)):
            if pool is None:
                continue
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
        self._events_join(1.0)
        # Settings -> Reset all: the app's folders emptied now that the
        # player and the workers have let go of them (core/reset.py).
        keep = getattr(self, "_reset_wipe", None)
        if keep is not None:
            try:
                from ..core import reset as app_reset
                app_reset.wipe(keep=keep)
            except Exception as e:
                log.warning("reset: folders not emptied: %s", e)
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
            p is not None and p.activeThreadCount() > 0
            for p in (self.pool, self._logo_pool, self._art_pool,
                      getattr(self, "_sync_pool", None),
                      getattr(self, "_local_walk_threads", None)))
        # macOS: QApplication.quit() enters AppKit's terminate cascade, whose
        # exit() finalizers run while Qt timers are still live - a timer then
        # fires into the half-destroyed QGuiApplication and segfaults (seen
        # via Settings -> Reset all). Teardown above already drained workers
        # and released the player, so the hard exit is safe there too.
        if active_workers or sys.platform == "darwin":
            os._exit(0)
        QApplication.instance().quit()
