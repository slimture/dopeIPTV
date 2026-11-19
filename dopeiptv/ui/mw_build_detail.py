"""Building the window's detail column (MainWindow mixin, the base of
ui/mw_build.py's ``_BuildMixin``): the pane and its focus-mode strip
(``_build_detail_pane``), the cast card in the video's place
(``_build_cast_card``), the docked player and what the window hears from it
(``_build_player``), the info column under the video that scrolls
(``_build_info_column``), the poster, Now playing and a film's synopsis
(``_build_detail_header``), and a film's Continue, the cast's photos and the
programme list (``_build_detail_lists``). main_window.py's ``_build_ui``
calls them in the order they were always made; moved out of it as they were.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea,
    QToolButton, QVBoxLayout, QWidget,
)

from ..i18n import tr
from ..media.embedded import EmbeddedPlayer
from ..media.players import embedded_playback_supported
from .cast_card import CastCard
from .player_resize import PlayerResizeHandle
from .theme import P


class _BuildDetailMixin:
    """MainWindow mixin: the detail column's parts of ``_build_ui``."""
    def _build_detail_pane(self) -> tuple[QWidget, QVBoxLayout]:
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
        return det, dl

    def _build_cast_card(self, dl: QVBoxLayout) -> None:
        # Casting takes the stream to the TV and stops local playback, which
        # leaves the player pane black with nothing anywhere saying why. For
        # as long as a cast runs the card stands in the video's place: what
        # plays on the TV, on which TV, how it goes, and every control the
        # cast has (ui/cast_card.py; mw_cast fills it, mw_cast_watch says its
        # state and gets a stuck cast going again).
        card = self.cast_bar = CastCard()
        # Its parts by the names the cast code has always used.
        self.cast_bar_title, self.cast_bar_line = card.title, card.line
        self.cast_bar_lbl = card.device
        self.cast_bar_back, self.cast_bar_pause = card.back, card.pause
        self.cast_bar_fwd, self.cast_bar_stop = card.forward, card.stop
        self.cast_bar_seek, self.cast_bar_time = card.seek, card.time
        self.cast_bar_live = card.live
        self.cast_bar_mute, self.cast_bar_vol = card.mute, card.vol
        self.cast_bar_tracks, self.cast_bar_ts = card.tracks, card.ts
        self.cast_bar_airplay = card.airplay
        card.mute.clicked.connect(self._toggle_cast_mute)
        # While the handle is being dragged the TV would get a message per
        # pixel; it only needs the one that says where the drag ended.
        card.vol.set_time_provider(lambda f: f"{round(f * 100)} %")
        card.vol.seek_requested.connect(lambda v: self._cast_volume(v / 100))
        card.vol.valueChanged.connect(
            lambda v: (self.cast_bar_vol.dragging or self._cast_volume(v / 100)))
        # The same timeshift the player has, for the same channels: a cast
        # channel with an archive can be paused, wound back and pointed at an
        # earlier programme exactly as it can here.
        card.ts.clicked.connect(self._cast_timeshift_menu)
        card.tracks.clicked.connect(self._cast_tracks_menu)
        # AirPlay's own way in: the AirPlay window (Apple's button) back.
        card.airplay.clicked.connect(self._cast_airplay_window)
        # The cast window on the running cast: another TV, a setting.
        self.cast_bar_devices = card.devices
        card.devices.clicked.connect(self.manage_cast)
        # Pause is only shown where pausing means something (show_cast_strip).
        card.pause.clicked.connect(self._toggle_cast_pause)
        card.back.clicked.connect(lambda: self._cast_skip(-self.CAST_SKIP_S))
        card.forward.clicked.connect(lambda: self._cast_skip(self.CAST_SKIP_S))
        card.stop.clicked.connect(lambda: self._end_cast("stopped from the cast card"))
        # What the point under the cursor is, before clicking it.
        card.seek.set_time_provider(self._cast_time_at)
        card.seek.seek_requested.connect(lambda _v: self._cast_seek_released())
        card.live.clicked.connect(self._cast_go_live)
        card.action_clicked.connect(self._cast_card_action)

        # The receiver is the only thing that knows where the film is, and it
        # only says so when asked - so ask, once a second, for the whole of
        # a cast: its position, its state, and whether it is stuck.
        self._cast_tick = QTimer(self)
        self._cast_tick.setInterval(1000)
        self._cast_tick.timeout.connect(self._show_cast_progress)
        self.cast_bar.hide()
        dl.addWidget(self.cast_bar)

    def _build_player(self, dl: QVBoxLayout) -> None:
        self.player: EmbeddedPlayer | None = None
        if embedded_playback_supported():
            self.player = EmbeddedPlayer(settings=self.settings)
            self.player.hide()
            # Whose account a rewind buffer's connection is on (mw_connections).
            self.player.source_of = self._conn_player_source
            self.player.fs_btn.clicked.connect(self._toggle_player_fullscreen)
            self.player.double_clicked.connect(self._toggle_player_fullscreen)
            self.player.exit_fullscreen.connect(self._player_exit_fullscreen)
            self.player.timeshift_menu.connect(self._player_timeshift_menu)
            self.player.record_menu.connect(self._player_record_menu)
            self.rec.stop_inplayer_cb = self.player.stop_stream_record
            self.player.stop_btn.clicked.connect(
                lambda: self.rec.finish_all_inplayer("playback stopped"))
            self.player.stop_btn.clicked.connect(self._release_wake)
            self.player.stop_btn.clicked.connect(self._trakt_stop_current)
            self.player.stop_btn.clicked.connect(self._simkl_stop_current)
            self.player.playback_error.connect(self._playback_error)
            self.player.zap.connect(self._zap)
            self.player.popout_requested.connect(self._toggle_popout)
            # The bar's cast button: the cast window on what plays, the TV
            # used last ready in it (mw_cast).
            self.player.cast_requested.connect(self.cast_playing)
            self.player.popout_context_menu.connect(self._popout_context_menu)
            self.player.docked_context_menu.connect(self._docked_context_menu)
            self.player.stop_btn.clicked.connect(self._exit_popout_if_active)
            self.player.stopped.connect(self._on_player_stopped)
            self.player.stopped.connect(self._events_stopped)
            # Your statistics: the time it plays (mw_stats; it only watches).
            self.player.stopped.connect(self._stats_end)
            self.player.playback_error.connect(self._stats_end)
            self.player.paused_changed.connect(self._stats_paused_changed)
            self.player.stopped.connect(self._discord_stopped)
            self.player.stopped.connect(self._ms_stopped)
            self.player.resume_requested.connect(self._resume_last)
            self.player.stalled.connect(self._on_player_stalled)
            self.player.contested.connect(self._on_stream_contested)
            self.player.reopen_live.connect(self._reopen_live_edge)
            self.player.first_frame.connect(self._health_frame)
            self.player.stream_info_changed.connect(self._remember_stream_info)
            # A box's signal in the stream stats, polled only while they show.
            self.player.stats_shown.connect(self._box_stats_shown)
            # Before _on_player_finished, which may start the next episode.
            self.player.finished.connect(self._events_finished)
            self.player.finished.connect(self._stats_end)
            self.player.finished.connect(self._discord_finished)
            self.player.finished.connect(self._ms_finished)
            self.player.finished.connect(self._on_player_finished)
            self.player.next_episode.connect(self._play_next_episode)
            # Keep the poster overlay's play/pause/stop glyph in sync with the
            # player (guarded: the overlay is built later in this constructor).
            self.player.paused_changed.connect(self._on_paused_changed)
            self.player.paused_changed.connect(self._events_paused)
            self.player.paused_changed.connect(self._discord_paused)
            self.player.paused_changed.connect(self._simkl_paused)
            self.player.vol.valueChanged.connect(self._events_volume)
            self.player.paused_changed.connect(self._ms_paused)
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
            # Nerd: the stream analysis of what plays (only while Nerd is
            # on - an empty label leaves the entry out).
            self.player.extra_options.append((self._tsa_menu_text, self.open_ts_analysis))
            # Subtitles from OpenSubtitles, and a subtitle loaded for a title
            # remembered for the next time it plays (mw_subtitles).
            self.player.find_subtitles = self._open_subtitle_search
            self.player.subtitle_added.connect(self._on_subtitle_added)
            # The player draws the visualiser and applies the equaliser; the
            # window owns the settings behind both.
            self.player.open_equaliser = self.open_equaliser
            self.player.vis_state = lambda: (
                self.settings.value("vis_on", "true") == "true",
                self.settings.value("vis_style", "bars"))
            self.player.vis_choose = self._vis_choose
            # A stream that turns out to be sound alone (an iHeart channel
            # in an M3U) gets the visualiser too, once mpv lists its tracks.
            self.player.audio_only_changed.connect(self._on_audio_only)
            # Night mode's key in the player's menu (the window's keys), and
            # one notice however it was switched (mw_shortcuts).
            self.player.night_key = self._night_key_text
            self.player.night_mode_changed.connect(self._night_mode_told)
            self.player.night_mode_refused.connect(self._night_mode_refused)
            # The teletext viewer, in the player's menu while a channel plays.
            self.player.open_teletext = self.open_teletext
            self.player.teletext_key = self._ttx_key_text
            dl.addWidget(self.player)

    def _build_info_column(self, det: QWidget, dl: QVBoxLayout) -> QVBoxLayout:
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
        if self.player is not None:
            self._player_resize = PlayerResizeHandle(
                self.player, self._info_scroll, self.settings, det)
            dl.insertWidget(dl.indexOf(self.player) + 1, self._player_resize)

        self.stream_error = QLabel("")
        self.stream_error.setStyleSheet(
            f"color:{P['error']}; font-size:12px;")
        self.stream_error.setWordWrap(True)
        self.stream_error.hide()
        il.addWidget(self.stream_error)
        return il

    def _build_detail_header(self, il: QVBoxLayout) -> None:
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
        # The guide's title and description: words, never markup.
        for lbl in (self.now_title, self.now_desc):
            lbl.setTextFormat(Qt.TextFormat.PlainText)
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
        # A plain box, NOT a scroll area. It used to be one, pinned to the
        # poster's height so their bottoms lined up, with a long synopsis
        # scrolling inside - which put a second scrollbar inside the panel's
        # own. Two nested scroll regions are miserable to use: the wheel
        # (and a trackpad especially) gets caught by whichever is under the
        # pointer, and the panel refuses to move. One scroll region for the
        # whole column; this box grows and the column scrolls.
        self.media_info = QFrame(objectName="MediaInfo")
        self._style_media_info()
        mi = QVBoxLayout(self.media_info)
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
        self.media_info.hide()
        # Top-aligned with the poster; its fixed height (set per-selection in
        # _show_detail to the poster height) makes the bottoms line up too.
        header_row.addWidget(self.media_info, 1, Qt.AlignmentFlag.AlignTop)
        header_row.setAlignment(Qt.AlignmentFlag.AlignTop)
        il.addLayout(header_row)

    def _build_detail_lists(self, il: QVBoxLayout) -> None:
        # A film under way: Continue from where it stopped, how far it got
        # and the time left (mw_detail._detail_film_continue).
        self.d_continue = QWidget(objectName="DetailContinue")
        dc = QHBoxLayout(self.d_continue)
        dc.setContentsMargins(0, 0, 0, 0)
        dc.setSpacing(12)
        self.d_continue_btn = QPushButton(objectName="Primary")
        self.d_continue_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.d_continue_btn.clicked.connect(self._detail_film_go_on)
        dc.addWidget(self.d_continue_btn)
        dcol = QVBoxLayout()
        dcol.setSpacing(4)
        self.d_continue_left = QLabel("", objectName="DetailMeta")
        self.d_continue_bar = QProgressBar(objectName="EpgBar")
        self.d_continue_bar.setTextVisible(False)
        self.d_continue_bar.setRange(0, 1000)
        self.d_continue_bar.setFixedHeight(4)
        dcol.addWidget(self.d_continue_left)
        dcol.addWidget(self.d_continue_bar)
        dc.addLayout(dcol, 1)
        self.d_continue.hide()
        il.addWidget(self.d_continue)

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
