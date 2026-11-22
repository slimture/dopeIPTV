"""Embedded in-app video player (libmpv OpenGL render API)."""

from __future__ import annotations

import os
import sys
import time

from PyQt6.QtCore import (
    QEvent, QObject, QRect, QSize,
    Qt, QTimer, pyqtSignal,
)
from PyQt6.QtGui import QColor, QCursor, QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QMenu, QSizePolicy, QSlider,
    QVBoxLayout, QWidget, QPushButton,
)

from ..core import channel_prefs, night_mode, stream_address
from ..core.log import log
from ..core.nerd import CertificateUnchecked
from ..core.privacy import for_log, url_for_log
from ..core.local_buffer import rewind_bytes
from ..i18n import tr
from .audio_effects import AudioEffectsMixin
from .audio_only import AudioOnlyMixin
from .control_icons import _control_icon
from .dvr_player import DvrPlayerMixin
from .loading_overlay import LoadingMixin
from .player_overlays import PlayerOverlaysMixin
from .popout_mirror import PopoutMirrorMixin
from .rewind_bar import RewindBarMixin
from .seek_slider import _SeekSlider, _format_time
from .sleep_timer import SleepTimerMixin
# aspect_name and on_off stay importable from here, as the tests import them.
from .stats_overlay import StatsOverlayMixin, _StatsLabel, aspect_name, on_off  # noqa: F401
from .stream_info import StreamInfoMixin
from .teletext_subs import TeletextSubsMixin
from .tracks import TracksMixin, _delay_key, _set_forced_only, _subtitle_to_load
# The window-thread surface the pop-out's mirror builds on stays importable
# from here, as the tests import it.
from .video_surface import _MpvGLWidget  # noqa: F401
from .video_thread import make_video_surface, swap_for_fallback
from ..ui.theme import P


def _is_bare_arrow(modifiers) -> bool:
    """True when no real modifier is held. macOS tags arrow keys with
    KeypadModifier, which must be ignored or no arrow ever reads as 'bare'."""
    return (modifiers & ~Qt.KeyboardModifier.KeypadModifier) == \
        Qt.KeyboardModifier.NoModifier


def _is_shift_arrow(modifiers) -> bool:
    """True when only Shift is held (again ignoring the macOS KeypadModifier).
    Shift+arrow steps the timeshift timeline; a bare arrow fine-seeks."""
    return (modifiers & ~Qt.KeyboardModifier.KeypadModifier) == \
        Qt.KeyboardModifier.ShiftModifier


def live_subtitles(settings) -> bool:
    """Subtitles on live channels as on films and series (the default), or
    live channels open without them (Settings > Picture and sound)."""
    return settings is None or str(settings.value("sub_live", "true")) != "false"


class _MacInputFilter(QObject):
    """macOS-only application-level input filter for the embedded player.

    On macOS a QOpenGLWidget does not reliably get keyboard focus or drive its
    own cursor rect, so two things that work through plain widget events on
    Linux don't on macOS:
      * plain Left/Right seek - the keys fall through to the channel list (mini
        player) or the window (fullscreen) and change channel instead of
        seeking ±10 s;
      * the idle cursor-hide over the video surface.
    Filtering at the application level sidesteps the focus problem. It is
    installed only on darwin, so Linux keeps its existing widget-based paths
    completely untouched.
    """

    def __init__(self, player: "EmbeddedPlayer") -> None:
        super().__init__(player)
        self._player = player
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setSingleShot(True)
        self._cursor_timer.setInterval(2000)
        self._cursor_timer.timeout.connect(self._maybe_hide_cursor)
        QApplication.instance().installEventFilter(self)

    def _over_video(self) -> bool:
        p = self._player
        if p.current_url is None or p.video.mpv is None or not p.video.isVisible():
            return False
        top_left = p.video.mapToGlobal(p.video.rect().topLeft())
        return QRect(top_left, p.video.size()).contains(QCursor.pos())

    def _maybe_hide_cursor(self) -> None:
        from ..core.platform_macos import set_cursor_hidden
        # Never hide while a menu or dialog is up. The subtitle/audio menus
        # and the right-click menu overlap the video, so the geometric
        # over-video test held true while the user was mid-choice - the
        # cursor vanished inside the open menu and (native menus swallow
        # the mouse-move events that un-hide it) only came back on a click.
        if (QApplication.activePopupWidget() is not None
                or QApplication.activeModalWidget() is not None):
            return
        if self._over_video():
            set_cursor_hidden(True)

    # Application-level filter = called for EVERY event in the app, so the
    # dispatch must be as close to free as possible. Module-level frozensets
    # beat rebuilding enum tuples per call (profiled: the enum lookups alone
    # showed up in the fullscreen-toggle trace). PyQt hands a key or mouse
    # event over as its own class, so an isinstance test lets every other
    # event through before event.type() builds its enum: building the
    # Settings window alone sends some forty thousand events through here.
    _MOUSE_EVS = frozenset({
        QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonRelease,
    })
    _KEY_EVS = frozenset({QEvent.Type.KeyPress, QEvent.Type.KeyRelease})
    _IS_MAC = sys.platform == "darwin"

    def eventFilter(self, obj, event):
        if isinstance(event, QKeyEvent):
            et = event.type()
            if et in self._KEY_EVS and self._handle_seek_key(
                    event, et == QEvent.Type.KeyPress):
                return True
        elif (self._IS_MAC and isinstance(event, QMouseEvent)
              and event.type() in self._MOUSE_EVS):
            # Any mouse activity un-hides - including the click that opens
            # a context menu, so the menu never comes up with an invisible
            # pointer. Only idle time over the bare video re-hides - and a
            # move Qt sends while the pointer rests is no move.
            from ..core.platform_macos import cursor_moved, set_cursor_hidden
            if event.type() == QEvent.Type.MouseMove:
                at = event.globalPosition().toPoint()
                if not cursor_moved(at.x(), at.y()):
                    return False
            set_cursor_hidden(False)
            if self._over_video():
                self._cursor_timer.start()
                # A QOpenGLWidget on macOS delivers its own mouseMoveEvent
                # erratically, so the centre play/pause button revealed from
                # the widget's move signal came up only sometimes. Reveal it
                # from this app-level move filter too - it fires reliably
                # whenever the pointer is over the video.
                try:
                    self._player._reveal_center()
                except Exception:
                    pass
            else:
                self._cursor_timer.stop()
        return False

    def _handle_seek_key(self, event, pressed: bool) -> bool:
        if event.key() not in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            return False
        # Ctrl/Cmd+arrows stay a channel zap; claim the bare presses (fine-seek)
        # and Shift+arrow (timeline step). On macOS the arrow keys carry
        # KeypadModifier, so ignore that one bit - otherwise no arrow ever
        # counts as "bare" and every one falls through to the channel list.
        bare = _is_bare_arrow(event.modifiers())
        shift = _is_shift_arrow(event.modifiers())
        if not (bare or shift):
            return False
        p = self._player
        if p.current_url is None:
            return False
        # Never steal arrows from a text field (search box, PIN entry, ...).
        if isinstance(QApplication.focusWidget(), QLineEdit):
            return False
        back = event.key() == Qt.Key.Key_Left
        # Gate on the MODE, not the bar's visibility - the bar now auto-hides,
        # but a timeshift channel is still steppable while it's tucked away.
        timeline = p._seek_mode == "timeline"
        # Shift+arrow: step the timeline into the archive - one jump per press
        # (no auto-repeat) since each reloads a segment.
        if shift and timeline:
            if pressed and not event.isAutoRepeat():
                p.arrow_scrub(back, step=True)
            return True
        # Bare arrow on a seekable stream (movie, catch-up segment, live
        # buffer): fine-seek, with hold-to-seek while the key is down.
        if p._mpv_seekable():
            if pressed:
                p._on_seek_key_press(event)
            else:
                p._on_seek_key_release(event)
            return True
        # Non-seekable timeshift live edge: swallow the bare arrow so it doesn't
        # swap the channel (use Shift+arrow to step into the archive).
        if timeline:
            return True
        return False


# How subtitles look (Settings -> Audio & subtitles): size in percent and
# the colour of text subtitles.
SUB_SCALES = ("75", "100", "125", "150", "200")
SUB_COLORS = {"white": "#FFFFFF", "yellow": "#FFE14D", "grey": "#D0D0D0"}


def _sub_look(s, set_opt) -> None:
    """Style text subtitles (SRT, WebVTT...) from Settings. libass styling,
    applied to the playing stream at once - nothing is reinitialised.
    sub-scale sizes styled (ASS) subtitles too; their colours are theirs."""
    scale = str(s.value("sub_scale", "100") or "100")
    set_opt("sub-scale", int(scale) / 100 if scale in SUB_SCALES else 1.0)
    set_opt("sub-color",
            SUB_COLORS.get(s.value("sub_color", "white"), "#FFFFFF"))
    box = s.value("sub_background", "false") == "true"
    # mpv 0.38+ draws a box from sub-border-style; older builds from a
    # sub-back-color that is not transparent. Both are set: the one a build
    # does not have just fails (set_opt swallows it).
    set_opt("sub-border-style",
            "background-box" if box else "outline-and-shadow")
    set_opt("sub-back-color", "#B3000000" if box else "#00000000")


#: A web page through yt-dlp (YouTube, Twitch...) opens as mpv itself opens
#: it. The reconnect, short probe and buffer tuned for IPTV providers broke
#: YouTube's HLS - blocks and smears on its live streams, which played
#: cleanly in mpv on the same computer. Set for that file only: the next
#: stream has the app's own again. (probesize cannot be mpv's 0; 5 MB is
#: FFmpeg's own default.)
WEB_FILE_OPTIONS = {"demuxer-lavf-o": "", "demuxer-lavf-analyzeduration": "0",
                    "demuxer-lavf-probesize": "5000000", "cache-secs": "3600000",
                    "network-timeout": "60"}


def rtsp_file_options(url: str | None) -> dict[str, str]:
    """A SAT>IP source's RTSP transport for *url* alone (core/rtsp.py): TCP
    or UDP as the playing source says. {} for every other address, which
    opens exactly as before."""
    if not url:
        return {}
    try:
        from ..core import nerd, rtsp
        return rtsp.mpv_options(nerd.source(), url)
    except Exception as e:
        log.debug("rtsp transport not set: %s", e)
        return {}


def load_with_options(m, url: str, options: dict[str, str]) -> bool:
    """loadfile with options for this file alone. mpv 0.38 put an index
    before the options, and python-mpv's loadfile follows the libmpv only in
    its newer releases - so both forms are tried, then a plain play. Whether
    the options were taken."""
    opts = ",".join(f"{k}={v}" for k, v in options.items())
    for args in ((url, "replace", "-1", opts), (url, "replace", opts)):
        try:
            m.command("loadfile", *args)
            return True
        except Exception as e:
            log.debug("loadfile with options (%d args): %s", len(args), e)
    m.play(url)
    return False


#: mpv's output that plays nothing and keeps the time: what a muted player
#: opens instead of a stream to the system's sound server.
QUIET_AO = "null"


def ao_text(value) -> str:
    """mpv's ``ao`` option as text to set it back: "" for its own choice,
    else the outputs' names in order (what a user's mpv options asked)."""
    if isinstance(value, str):
        return "" if value == QUIET_AO else value
    try:
        names = [str(e.get("name")) for e in value or () if e.get("name")]
    except Exception:
        return ""
    names = [n for n in names if n != QUIET_AO]
    return ",".join(names)


#: How long play() waits for a web page's file to be gone (some milliseconds).
FILE_END_WAIT = 2.0


def end_file_options(m, wait: float = FILE_END_WAIT) -> bool:
    """End the file load_with_options loaded, and wait until its options
    are back, before the next stream's are set. mpv puts back what such a
    file replaced when it ends - with loadfile replace that is after the
    next stream had written its own, which were lost: the first stream
    after a web page played without Nerd's low latency, or with the buffer
    from before a change. A stop alone is not enough either: a write right
    after it can still come before the core ends the file. The file's path
    is gone once the options are back. Whether that was within *wait*
    seconds (if not, the next stream plays as it did before this)."""
    deadline = time.monotonic() + wait
    try:
        if m.path is None:
            return True
        m.command("stop")
        while m.path is not None:
            if time.monotonic() > deadline:
                log.info("web page: its file was not ended within %.1f s", wait)
                return False
            time.sleep(0.005)
    except Exception as e:
        log.debug("web page: its file not ended first: %s", e)
        return False
    return True


class EmbeddedPlayer(TeletextSubsMixin, StreamInfoMixin, LoadingMixin,
                     AudioOnlyMixin, RewindBarMixin, DvrPlayerMixin,
                     PopoutMirrorMixin, PlayerOverlaysMixin, AudioEffectsMixin,
                     SleepTimerMixin, StatsOverlayMixin, TracksMixin, QWidget):
    """Video pane with libmpv OpenGL rendering, control bar, and fullscreen overlay."""

    _info_loaded = pyqtSignal(str)
    _info_reconfig = pyqtSignal()
    # (a thread) the PMT's teletext pages of stream number n: teletext_subs.
    _ttx_streams_ready = pyqtSignal(int, object)
    # (mpv's event thread) what the loading indicator follows, and its value:
    # media/loading_overlay.py.
    _ld_event = pyqtSignal(str, object)
    # (mpv's event thread) file-loaded with its path, or the track list
    # changed: media/audio_only.py.
    _ao_event = pyqtSignal(str, str)
    # The stream play() opened turned out to be sound with no picture
    # (True), or to have one after all (False): once per change.
    audio_only_changed = pyqtSignal(bool)
    stream_info_changed = pyqtSignal(str, object)
    double_clicked = pyqtSignal()
    playback_error = pyqtSignal(str)
    zap = pyqtSignal(int)
    exit_fullscreen = pyqtSignal()
    timeshift_menu = pyqtSignal(object)
    record_menu = pyqtSignal(object)
    popout_requested = pyqtSignal()
    #: The bar's cast button: what plays, to a TV (the window opens its cast
    #: window).
    cast_requested = pyqtSignal()
    popout_context_menu = pyqtSignal(object)
    docked_context_menu = pyqtSignal(object)   # right-click on the docked video
    stopped = pyqtSignal()
    resume_requested = pyqtSignal()
    stalled = pyqtSignal()
    # The app's own buffer keeps losing its provider connection: another
    # device or app on the account, most likely. Once per stream.
    contested = pyqtSignal()
    # LIVE in the DVR row with the buffer far behind the channel: open it again.
    reopen_live = pyqtSignal()
    # The channel watched privately has its buffer on the disk and the
    # recording or cast that kept it there is done (core/dvr_pool's
    # on_private_disk, from any thread): the window opens it again.
    private_on_disk = pyqtSignal()
    # The stream play() opened is playing, once per play(): how many seconds
    # it had already played when the poll saw it (stream health, Nerd).
    first_frame = pyqtSignal(float)
    #: Why the last play() left its stream unopened on purpose, in the
    #: person's words ("" for none): its certificate cannot be checked.
    refusal = ""
    # The stream stats (I) came up or went: what the window polls for them
    # (a box's signal) runs only while they show.
    stats_shown = pyqtSignal(bool)
    finished = pyqtSignal()
    next_episode = pyqtSignal()
    paused_changed = pyqtSignal(bool)
    timeshift_seek = pyqtSignal(int)   # minutes back from live (0 = go live)
    program_seek = pyqtSignal(int)     # seconds from the picked programme start
    track_selected = pyqtSignal(str, object)  # "aid"/"sid", track id or "no"
    subtitle_added = pyqtSignal(str)          # a subtitle file now showing
    # Night mode switched on or off - the options menu, the window's key or
    # Settings: the window says so (night_mode_failed: the core refused it).
    night_mode_changed = pyqtSignal(bool)
    # A stream's sound did not start through the night filter: it plays
    # without it now, and the window says so.
    night_mode_refused = pyqtSignal()

    OVERLAY_HIDE_MS = 3000
    VIDEO_BOX_HEIGHT = 260
    # How long mpv may sit idle (buffer-starved) while not paused before we
    # treat the stream as frozen and ask for a reconnect.
    STALL_SECS = 12
    # Minutes a Left/Right arrow press moves the timeshift live timeline (at the
    # live edge); once inside a seekable archive segment, arrows fine-seek by
    # these seconds instead - matching the on-screen ◀ 10s / 30s ▶ buttons.
    TIMELINE_STEP_MIN = 5
    SEEK_BACK = 10
    SEEK_FWD = 30
    MINIBTN = 28
    # Sleep-timer countdown pill: pinned on (never fades) once this little time
    # is left, so the imminent stop is unmissable.
    SLEEP_PIN_SECS = 30
    ICON_PX = 15  # drawn control-icon size inside the 28px buttons

    def __init__(self, parent: QWidget | None = None,
                 settings=None) -> None:
        super().__init__(parent)
        # Initialised up front because eventFilter() reads them, and events
        # can be delivered to the filtered widgets (e.g. font/style changes
        # on the control bar) while the rest of __init__ is still running.
        self._fs_ui = False
        self._popout_mode = False
        self._mirror = None          # macOS pop-out mirror surface (if any)
        self._dock_ph = None         # 'playing in pop-out' cover, docked side
        # The surface the floating overlays (seek bar, timeshift timeline,
        # stats) anchor to: the docked video normally, the pop-out mirror while
        # popped out on macOS. Its .geometry() is in its parent's coords, and
        # the overlays are reparented alongside, so placement is identical in
        # either window.
        self._ov_surface = None      # set to self.video once it's built
        self._popout_drag_from = None
        self._popout_autohide = False
        # Menu entries the app adds to the options menu (see
        # populate_options_menu). Kept as (label_callable, action) so a
        # language change is picked up when the menu is next opened.
        self.extra_options: list = []
        # The window's "Find subtitles online…" (None: not offered), the
        # same way: the player shows the entry, the window knows the site.
        self.find_subtitles = None
        self._popout_bar_timer = QTimer(self)
        self._popout_bar_timer.setSingleShot(True)
        self._popout_bar_timer.setInterval(2500)
        self._popout_bar_timer.timeout.connect(self._hide_popout_bar)
        self.seek_overlay = None
        self._settings = settings
        # One-shot audio/subtitle track ids for the NEXT play() - set via
        # set_track_prefs() to restore a title's remembered track choices.
        self._pref_aid = None
        self._pref_sid = None
        # (mode, languages) until the tracks are known: _apply_sub_rule.
        self._sub_rule: tuple | None = None
        # One-shot subtitle files for the NEXT play() (set_sub_files).
        self._pref_sub_files: list[str] = []
        # Nerd: the channel the NEXT play() is (set_channel, one-shot), the
        # one playing and the settings of its own it plays with ({}: none).
        self._chan_armed: str | None = None
        self._chan_key: str | None = None
        self._chan_own: dict = {}
        self._chan_store = None
        # Teletext and DVB subtitle tracks (media/teletext_subs.py).
        self._ttx_streams_ready.connect(self._ttx_streams_got)
        # Sound with no picture (media/audio_only.py).
        self._ao_init()
        # Where the playing stream's audio delay is kept, if it is: its
        # channel's settings or (anything else) a key of its own.
        self._delay_chan: str | None = None
        self._delay_key: str | None = None
        self._delay_set = False
        # The file on the core was loaded with options of its own (a web
        # page): play() ends it before it sets the next stream's.
        self._own_file_options = False
        # Night mode for the sound (core/night_mode.py): whether it is
        # wanted, and what the core was last given (its weakref, on/off) -
        # so a play() that changes nothing writes nothing to mpv, and a core
        # never given the filter is never touched while it is off.
        self._night_on = night_mode.enabled(settings)
        self._night_core = None
        self._night_now = False
        # That core refused the filter: not asked again until night mode
        # is switched (a new core is asked afresh).
        self._night_refused = False
        # The window's key for it, shown in the options menu ("" for none).
        self.night_key = None
        # Docked video-box height scales with the display so the mini player
        # is usefully large on a 27"/4K screen and modest on a laptop -
        # computed once here, then constant (nothing resizes on its own).
        from PyQt6.QtWidgets import QApplication
        scr = QApplication.primaryScreen()
        if scr is not None:
            # Scale the docked box with the screen WIDTH, not its height: the
            # right column's width is what a 16:9 video has to fit into, so a
            # width-based height stays roughly 16:9 for the typical column and
            # avoids tall letterbox bars on narrow laptop screens - while
            # still growing on a wide 27"/4K display. Computed once, so it
            # never drifts when the splitter is dragged.
            self.VIDEO_BOX_HEIGHT = min(
                max(230, int(scr.availableGeometry().width() * 0.17)), 620)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        # Drawn on a thread of its own unless switched off or impossible
        # here (media/video_thread.py); else the window-thread surface.
        self.video = make_video_surface(self, settings)
        # This player switches adaptive streams to the variant Nerd's cap
        # allows (_watch_variants): its core may open them on the smallest.
        self.video.variant_picker = True
        # Hardware-decode preference from Settings, staged on the widget before
        # its lazy GL init builds the mpv core (env DOPEIPTV_HWDEC still wins).
        if settings is not None:
            self.video.hwdec_pref = str(
                settings.value("hwdec_mode", "") or "") or None
        # Route mpv errors through a filter so a user-initiated stop doesn't
        # surface the "aborted / loading failed" mpv fires while it winds
        # the stream down as a "Stream error" toast.
        self._stopping = False
        self._play_args: tuple | None = None
        self._video_swapped = False
        self._wire_video(self.video)
        self._ov_surface = self.video   # overlays anchor here (see __init__)
        # Continuous seek while an arrow key is held down.
        self._seek_hold_dir = 0
        self._seek_hold_timer = QTimer(self)
        self._seek_hold_timer.setInterval(400)
        self._seek_hold_timer.timeout.connect(
            lambda: self._relative_seek(self._seek_hold_dir))
        lay.addWidget(self.video, 1)

        self.bar = QWidget()
        bar_layout = QVBoxLayout(self.bar)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(2)
        self._build_dvr_row()
        controls = QWidget(self.bar)
        bar_layout.addWidget(controls)
        bl = QHBoxLayout(controls)
        bl.setContentsMargins(4, 2, 4, 2)
        bl.setSpacing(8)
        bl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.prev_btn = QPushButton("◀", objectName="MiniBtn")
        self.prev_btn.setToolTip(tr("tooltip_previous_channel") + " (Ctrl+Left)")
        self.prev_btn.clicked.connect(lambda: self.zap.emit(-1))
        self.next_btn = QPushButton("▶", objectName="MiniBtn")
        self.next_btn.setToolTip(tr("tooltip_next_channel") + " (Ctrl+Right)")
        self.next_btn.clicked.connect(lambda: self.zap.emit(1))
        self.pause_btn = QPushButton("‖", objectName="MiniBtn")
        self.pause_btn.setToolTip(tr("tooltip_pause_resume"))
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.back_btn = QPushButton("−10", objectName="MiniBtn")
        self.back_btn.setToolTip(tr("tooltip_back_10s"))
        self.back_btn.clicked.connect(lambda: self._relative_seek(-10))
        self.back_btn.hide()
        self.fwd_btn = QPushButton("+30", objectName="MiniBtn")
        self.fwd_btn.setToolTip(tr("tooltip_forward_30s"))
        self.fwd_btn.clicked.connect(lambda: self._relative_seek(30))
        self.fwd_btn.hide()
        self.title_lbl = QLabel("", objectName="DetailMeta")
        self.title_lbl.setSizePolicy(QSizePolicy.Policy.Ignored,
                                     QSizePolicy.Policy.Preferred)
        self.seek = _SeekSlider()
        self.seek.seek_requested.connect(self._do_seek)
        self.seek.hide()
        self.time_lbl = QLabel("", objectName="DetailMeta")
        self.time_lbl.hide()
        self.mute_btn = QPushButton("🔊", objectName="MiniBtn")
        self.mute_btn.setToolTip(tr("tooltip_mute_unmute"))
        self.mute_btn.clicked.connect(self.toggle_mute)
        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setFixedWidth(40)
        self.vol.setToolTip(tr("tooltip_volume"))
        self.vol.valueChanged.connect(self._set_volume)
        # '◀◀' (geometric triangles) rather than the '⏪' emoji, which ignores
        # the colour and rendered grey; amber flags a timeshift channel.
        self.ts_btn = QPushButton("◀◀", objectName="MiniBtn")
        self.ts_btn.setToolTip(tr("tooltip_timeshift"))
        self.ts_btn.setStyleSheet("color:#F2B01E; font-size:11px;")
        self.ts_btn.clicked.connect(
            lambda: self.timeshift_menu.emit(self.ts_btn))
        self.ts_btn.hide()
        self.rec_btn = QPushButton("●", objectName="MiniBtn")
        self.rec_btn.setToolTip(tr("tooltip_record"))
        self.rec_btn.setStyleSheet("color:#FF5C5C;")
        self.rec_btn.clicked.connect(
            lambda: self.record_menu.emit(self.rec_btn))
        self.rec_btn.hide()
        self.opts_btn = QPushButton("⚙", objectName="MiniBtn")
        self.opts_btn.setToolTip(tr("tooltip_audio_subs_aspect"))
        self.opts_btn.clicked.connect(
            lambda: self._show_options_menu(self.opts_btn))
        self.stop_btn = QPushButton("■", objectName="MiniBtn")
        self.stop_btn.setToolTip(tr("tooltip_stop_playback"))
        self.stop_btn.clicked.connect(self.stop)
        # Skip to the next episode (series only). Hidden unless the app tells us
        # a next episode is queued (set_next_available).
        self.nextep_btn = QPushButton("⏭", objectName="MiniBtn")
        self.nextep_btn.setToolTip(tr("tooltip_next_episode"))
        self.nextep_btn.clicked.connect(self.next_episode)
        self.nextep_btn.hide()
        self.pop_btn = QPushButton("⧉", objectName="MiniBtn")
        self.pop_btn.setToolTip(tr("tooltip_popout"))
        self.pop_btn.clicked.connect(self.popout_requested)
        # What plays, to a TV: the cast window, the TV used last ready in it
        # (no right click needed - the options menu keeps its entry too).
        self.cast_btn = QPushButton("", objectName="MiniBtn")
        self.cast_btn.setToolTip(tr("tooltip_cast"))
        self.cast_btn.setAccessibleName(tr("tooltip_cast"))
        self.cast_btn.clicked.connect(self.cast_requested)
        self.fs_btn = QPushButton("⛶", objectName="MiniBtn")
        self.fs_btn.setToolTip(tr("tooltip_fullscreen"))
        # Group by function: prev/next are the "channel zap" pair (same
        # gesture, opposite direction); play-pause/stop are the "playback
        # state" pair. A small gap between the groups keeps them visually
        # distinct so you don't hit Play when reaching for Next.
        bl.addWidget(self.prev_btn)
        bl.addWidget(self.next_btn)
        bl.addSpacing(10)
        bl.addWidget(self.pause_btn)
        bl.addWidget(self.stop_btn)
        bl.addWidget(self.nextep_btn)
        bl.addStretch(1)
        bl.addWidget(self.ts_btn)
        bl.addWidget(self.rec_btn)
        bl.addWidget(self.opts_btn)
        bl.addWidget(self.cast_btn)
        bl.addWidget(self.pop_btn)
        bl.addWidget(self.fs_btn)
        bl.addSpacing(6)
        bl.addWidget(self.mute_btn)
        bl.addSpacing(2)
        bl.addWidget(self.vol)
        lay.addWidget(self.bar)
        self.bar.setMouseTracking(True)
        self.bar.installEventFilter(self)

        # Floating scrubber shown over the bottom of the docked video on
        # hover, so the seek bar doesn't permanently occupy the control row.
        self._seekable = False
        # What seek UI this stream uses:
        #  'vod'      - normal seek bar (movies/series/recordings)
        #  'program'  - a catch-up segment: normal seek bar spanning it
        #  'timeline' - a timeshift channel at the live edge: the live timeline
        #  'live'     - a plain live channel: no seek bar at all
        self._seek_mode = "vod"
        self._program_window = 0.0        # 'program' mode: clamp bar to N secs
        self._program_base = 0.0          # secs of the programme before this segment
        self.seek_overlay = QWidget(self)
        self.seek_overlay.setStyleSheet(
            "background: rgba(16,16,20,215); border-radius: 8px;")
        so = QHBoxLayout(self.seek_overlay)
        so.setContentsMargins(8, 5, 8, 5)
        so.setSpacing(8)
        so.addWidget(self.back_btn)
        so.addWidget(self.time_lbl)
        so.addWidget(self.seek, 1)
        so.addWidget(self.fwd_btn)
        self.seek_overlay.hide()
        self.seek_overlay.setMouseTracking(True)
        self.seek_overlay.installEventFilter(self)
        self.seek.installEventFilter(self)
        self._seek_overlay_timer = QTimer(self)
        self._seek_overlay_timer.setSingleShot(True)
        self._seek_overlay_timer.setInterval(2500)
        self._seek_overlay_timer.timeout.connect(self._hide_seek_overlay)
        # The timeshift live timeline auto-hides like the scrubber: it reappears
        # on mouse-move over the video (and on a keyboard step) and fades after
        # a few idle seconds, so it doesn't sit over the picture forever.
        self._ts_hide_timer = QTimer(self)
        self._ts_hide_timer.setSingleShot(True)
        self._ts_hide_timer.setInterval(self.TS_BAR_HIDE_MS)
        self._ts_hide_timer.timeout.connect(self._hide_ts_timeline)

        self.overlay = QLabel("", self)
        # A station's ICY title, a programme's or a channel's name: words,
        # never markup (AutoText read a tag in them as rich text).
        self.overlay.setTextFormat(Qt.TextFormat.PlainText)
        self.overlay.setStyleSheet(
            "background: rgba(16,16,20,210); color:#ECECF1;"
            "border-radius:10px; padding:10px 14px; font-size:13px;")
        self.overlay.setWordWrap(True)
        self.overlay.hide()

        # A big white play/pause disc in the centre of the video. Shared by the
        # docked, fullscreen and pop-out views (all one widget). It stays up
        # while paused and appears briefly on mouse activity while playing; a
        # click toggles pause. Clicking the video itself does the same (see
        # _on_video_release), with a double-click still reserved for fullscreen.
        self.center_btn = QPushButton(self)
        self.center_btn.setObjectName("CenterPlay")
        self.center_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.center_btn.setFixedSize(72, 72)
        self.center_btn.setStyleSheet(
            "#CenterPlay { background: rgba(16,16,20,140); border:none;"
            " border-radius:36px; }"
            "#CenterPlay:hover { background: rgba(16,16,20,200); }")
        self.center_btn.clicked.connect(self.toggle_pause)
        self.center_btn.hide()
        self._center_hide_timer = QTimer(self)
        self._center_hide_timer.setSingleShot(True)
        self._center_hide_timer.setInterval(2000)
        self._center_hide_timer.timeout.connect(self._maybe_hide_center)
        # Single-click vs double-click: a click waits one double-click interval
        # before toggling pause, so a double-click (fullscreen) can cancel it.
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(QApplication.doubleClickInterval())
        self._click_timer.timeout.connect(self._video_single_click)
        self._ignore_next_release = False

        self.fs_controls = QWidget(self, objectName="FsControls")
        # Scope the pill background to the container: a selectorless rule
        # cascades onto every child, pinning the buttons' background so their
        # hover state never showed. The buttons keep the flat look but get a
        # clear hover highlight.
        self.fs_controls.setStyleSheet(
            "#FsControls { background: rgba(16,16,20,210);"
            " border-radius: 10px; }"
            "#FsControls QPushButton { background: transparent;"
            " border: none; border-radius: 6px; }"
            "#FsControls QPushButton:hover {"
            " background: rgba(255,255,255,0.16); }")
        fc = QHBoxLayout(self.fs_controls)
        fc.setContentsMargins(8, 6, 8, 6)
        fc.setSpacing(8)
        self.fs_prev_btn = QPushButton("◀", objectName="MiniBtn")
        self.fs_prev_btn.setToolTip(tr("tooltip_previous_channel"))
        self.fs_prev_btn.clicked.connect(lambda: self.zap.emit(-1))
        self.fs_next_btn = QPushButton("▶", objectName="MiniBtn")
        self.fs_next_btn.setToolTip(tr("tooltip_next_channel"))
        self.fs_next_btn.clicked.connect(lambda: self.zap.emit(1))
        self.fs_pause_btn = QPushButton("‖", objectName="MiniBtn")
        self.fs_pause_btn.setToolTip(tr("tooltip_pause_resume"))
        self.fs_pause_btn.clicked.connect(self.toggle_pause)
        self.fs_nextep_btn = QPushButton("⏭", objectName="MiniBtn")
        self.fs_nextep_btn.setToolTip(tr("tooltip_next_episode"))
        self.fs_nextep_btn.clicked.connect(self.next_episode)
        self.fs_nextep_btn.hide()
        self.fs_back_btn = QPushButton("-10", objectName="MiniBtn")
        self.fs_back_btn.clicked.connect(lambda: self._relative_seek(-10))
        self.fs_back_btn.hide()
        self.fs_fwd_btn = QPushButton("+30", objectName="MiniBtn")
        self.fs_fwd_btn.clicked.connect(lambda: self._relative_seek(30))
        self.fs_fwd_btn.hide()
        self.fs_seek = _SeekSlider()
        self.fs_seek.seek_requested.connect(self._do_seek)
        self.fs_seek.hide()
        self.fs_time_lbl = QLabel("", objectName="DetailMeta")
        self.fs_time_lbl.hide()
        self.fs_mute_btn = QPushButton("\U0001f50a", objectName="MiniBtn")
        self.fs_mute_btn.setToolTip(tr("tooltip_mute_unmute"))
        self.fs_mute_btn.clicked.connect(self.toggle_mute)
        self.fs_vol = QSlider(Qt.Orientation.Horizontal)
        self.fs_vol.setRange(0, 100)
        self.fs_vol.setFixedWidth(80)
        self.fs_vol.setToolTip(tr("tooltip_volume"))
        self.fs_vol.valueChanged.connect(self._set_volume)
        self.fs_ts_btn = QPushButton("◀◀", objectName="MiniBtn")
        self.fs_ts_btn.setToolTip(tr("tooltip_timeshift"))
        self.fs_ts_btn.setStyleSheet("color:#F2B01E; font-size:11px;")
        self.fs_ts_btn.clicked.connect(
            lambda: self.timeshift_menu.emit(self.fs_ts_btn))
        self.fs_ts_btn.hide()
        self.fs_rec_btn = QPushButton("●", objectName="MiniBtn")
        self.fs_rec_btn.setToolTip(tr("tooltip_record"))
        self.fs_rec_btn.setStyleSheet("color:#FF5C5C;")
        self.fs_rec_btn.clicked.connect(
            lambda: self.record_menu.emit(self.fs_rec_btn))
        self.fs_rec_btn.hide()
        self.fs_opts_btn = QPushButton("⚙", objectName="MiniBtn")
        self.fs_opts_btn.setToolTip(tr("tooltip_audio_subs_aspect"))
        self.fs_opts_btn.clicked.connect(
            lambda: self._show_options_menu(self.fs_opts_btn))
        self.fs_exit_btn = QPushButton("✕", objectName="MiniBtn")
        self.fs_exit_btn.setToolTip(tr("tooltip_exit_fullscreen"))
        self.fs_exit_btn.clicked.connect(self.exit_fullscreen.emit)
        fc.addWidget(self.fs_prev_btn)
        fc.addWidget(self.fs_next_btn)
        fc.addWidget(self.fs_pause_btn)
        fc.addWidget(self.fs_nextep_btn)
        fc.addWidget(self.fs_back_btn)
        fc.addWidget(self.fs_fwd_btn)
        fc.addWidget(self.fs_seek, 1)
        fc.addWidget(self.fs_time_lbl)
        fc.addWidget(self.fs_mute_btn)
        fc.addWidget(self.fs_vol)
        fc.addWidget(self.fs_ts_btn)
        fc.addWidget(self.fs_rec_btn)
        fc.addWidget(self.fs_opts_btn)
        fc.addWidget(self.fs_exit_btn)
        self.fs_controls.hide()
        for wdg in (self.fs_controls, self.fs_prev_btn, self.fs_next_btn,
                    self.fs_pause_btn, self.fs_back_btn, self.fs_fwd_btn,
                    self.fs_seek, self.fs_time_lbl, self.fs_mute_btn,
                    self.fs_vol, self.fs_ts_btn,
                    self.fs_rec_btn, self.fs_opts_btn,
                    self.fs_exit_btn):
            wdg.setMouseTracking(True)
            wdg.installEventFilter(self)

        # Give every control-bar button the exact same square size, then
        # replace its Unicode glyph with a hand-drawn, perfectly centred icon
        # (see _control_icon) so the symbols line up at identical size and
        # height. The volume sliders match the height too.
        m = self.MINIBTN
        all_icon_btns = (
            self.prev_btn, self.next_btn, self.pause_btn, self.back_btn,
            self.fwd_btn, self.ts_btn, self.rec_btn, self.opts_btn,
            self.stop_btn, self.nextep_btn, self.fs_btn, self.mute_btn,
            self.fs_prev_btn, self.fs_next_btn, self.fs_pause_btn,
            self.fs_nextep_btn, self.fs_back_btn, self.fs_fwd_btn,
            self.fs_ts_btn, self.fs_rec_btn, self.fs_opts_btn,
            self.fs_exit_btn, self.fs_mute_btn,
        )
        for b in all_icon_btns:
            b.setFixedSize(m, m)
        self.pop_btn.setFixedSize(m, m)
        self.cast_btn.setFixedSize(m, m)
        for s in (self.vol, self.fs_vol):
            s.setFixedHeight(m)

        # Map each symbol button to a drawn-icon name. -10/+30 (seek amounts)
        # and the pop-out glyph stay as text labels; the pause/mute icons are
        # swapped live in _sync_pause_label / toggle_mute.
        self._icon_names = {
            self.prev_btn: "prev", self.next_btn: "next",
            self.pause_btn: "pause", self.ts_btn: "rewind",
            self.rec_btn: "record", self.opts_btn: "options",
            self.stop_btn: "stop", self.fs_btn: "fullscreen",
            self.mute_btn: "volume", self.nextep_btn: "nextep",
            self.pop_btn: "popout", self.cast_btn: "cast",
            self.fs_prev_btn: "prev", self.fs_next_btn: "next",
            self.fs_pause_btn: "pause", self.fs_ts_btn: "rewind",
            self.fs_rec_btn: "record", self.fs_opts_btn: "options",
            self.fs_exit_btn: "exit", self.fs_mute_btn: "volume",
            self.fs_nextep_btn: "nextep",
        }
        self._icon_color = P.get("text2", "#ECECF1")
        self.refresh_icons()

        self._pos_timer = QTimer(self)
        self._pos_timer.setInterval(500)
        self._pos_timer.timeout.connect(self._poll_position)

        self._fs_ui = False
        self.current_url: str | None = None
        self._muted = False
        self._stall_since = 0.0
        self._eof_seen = False
        try:
            vol = int(self._settings.value("volume", 100)) \
                if self._settings else 100
        except (TypeError, ValueError):
            vol = 100
        for s in (self.vol, self.fs_vol):
            s.blockSignals(True)
            s.setValue(vol)
            s.blockSignals(False)
        self._overlay_text = ""
        self._overlay_timer = QTimer(self)
        self._overlay_timer.setSingleShot(True)
        self._overlay_timer.setInterval(self.OVERLAY_HIDE_MS)
        self._overlay_timer.timeout.connect(self._hide_fs_ui)

        # Sleep timer: stop playback after a chosen number of minutes. A
        # second timer ticks once a second to count the badge down.
        self._sleep_timer = QTimer(self)
        self._sleep_timer.setSingleShot(True)
        self._sleep_timer.timeout.connect(self._on_sleep_elapsed)
        self._sleep_deadline = 0.0
        self._sleep_tick = QTimer(self)
        self._sleep_tick.setInterval(1000)
        self._sleep_tick.timeout.connect(self._update_sleep_badge)
        # The countdown pill fades like the controls, but is pinned on for the
        # final SLEEP_PIN_SECS as a "about to stop" warning.
        self._sleep_badge_timer = QTimer(self)
        self._sleep_badge_timer.setSingleShot(True)
        self._sleep_badge_timer.setInterval(2500)
        self._sleep_badge_timer.timeout.connect(self._maybe_hide_sleep_badge)

        # Black cover widget that sits over the mpv render surface when we
        # want the pane to be visibly black. Painted with a plain QPalette
        # background so it works even if GL cleaning is unreliable on the
        # user's compositor (Wayland's KDE/Hyprland stack sometimes still
        # shows the last mpv frame or tearing artefacts through paintGL's
        # glClear). setAutoFillBackground guarantees Qt fills it every paint.
        #
        # Parented to the PLAYER, not the video widget: a child widget on top
        # of the QOpenGLWidget forces Qt/macOS onto the render-to-texture
        # composition path instead of presenting the GL layer directly, and
        # THAT composited path is what goes stale (frozen frame) when the
        # player is reparented into the pop-out window. Keeping the GL widget
        # child-free lets it present directly and survive the reparent. The
        # overlay sits over the video as a sibling (like the centre button and
        # seek bar already do), positioned to the video's geometry.
        self._blackout = QWidget(self)
        self._blackout.setAutoFillBackground(True)
        pal = self._blackout.palette()
        pal.setColor(self._blackout.backgroundRole(), QColor(0, 0, 0))
        self._blackout.setPalette(pal)
        self._blackout.hide()
        self._blackout.installEventFilter(self)  # forward mouse to controls

        # Also a sibling of the video (see _blackout): "stats for nerds" was
        # THE reproducible pop-out freeze trigger - a translucent QLabel child
        # over the GL widget kept the composited path alive, so a reparent
        # froze the picture every time. As the player's child it no longer
        # touches the GL widget's child list.
        self._stats_overlay = _StatsLabel(self)
        # Lines the window adds under the stats (a box's signal), as
        # (name, value); kept up to date by it while stats_shown says so.
        self.stats_extra: list[tuple[str, str]] = []
        self._stats_overlay.toggled.connect(lambda: self._update_stats_text())
        self._stats_overlay.hide()
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._update_stats_text)
        self._init_stream_info(P)
        # The loading indicator over the picture (media/loading_overlay.py):
        # a sibling of the video like the overlays above, never its child.
        self._init_loading()
        # Night mode: did a stream's sound start through its filter?
        self._ld_event.connect(self._night_on_event)

        # Top-left "am I live?" badge: red LIVE at the live edge, a neutral
        # TIMESHIFT pill when watching the catch-up archive. A sibling of the
        # video (the player's child), NOT its child - see _blackout: any child
        # on top of the QOpenGLWidget forces the composited path that freezes
        # on a pop-out reparent. Anchored to the video's top-left corner.
        self.live_badge = QLabel("", self)
        self.live_badge.hide()

        # Sleep-timer badge: a top-right pill counting down the time left until
        # playback stops, so an armed sleep timer is always visible. Like the
        # live badge it's the player's child (never a child of the GL surface),
        # anchored to the video's top-right corner.
        self.sleep_badge = QLabel("", self)
        self.sleep_badge.hide()

        # The channel number being typed (the window's number zapping), in
        # the picture's top-right corner like a television's. A sibling of
        # the video like the badges above; it only shows text.
        self.number_badge = QLabel("", self)
        self.number_badge.setObjectName("NumberEntry")
        # TV mode shows a track's own name here: words, never markup.
        self.number_badge.setTextFormat(Qt.TextFormat.PlainText)
        self.number_badge.hide()
        self._number_badge_timer = QTimer(self)
        self._number_badge_timer.setSingleShot(True)
        self._number_badge_timer.timeout.connect(self.number_badge.hide)

        # "Next episode" card, over the picture for the last stretch of an
        # episode, so the credits can be skipped with one click instead of
        # being sat through. Deliberately NOT automatic: where the credits
        # start varies per show, and cutting away from a scene nobody asked
        # to leave is worse than a button.
        #
        # A sibling of the video, like the other overlays (see _blackout):
        # a child of the GL widget is what froze the picture on reparent.
        self.nextup_btn = QPushButton("⏭  " + tr("tooltip_next_episode"),
                                      self, objectName="NextUp")
        self.nextup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.nextup_btn.setStyleSheet(
            "QPushButton#NextUp { background: rgba(20,20,26,220);"
            " color: #FFFFFF; border: 1px solid rgba(255,255,255,60);"
            " border-radius: 8px; padding: 10px 16px; font-size: 13px;"
            " font-weight: 600; }"
            "QPushButton#NextUp:hover { background: rgba(40,40,50,235); }")
        self.nextup_btn.clicked.connect(self._on_nextup_clicked)
        self.nextup_btn.hide()
        self._next_available = False

        self._build_ts_timeline()

        # App-level filter for bare Left/Right seeking of a seekable video, so
        # it works even in the docked mini-player where focus is on the list
        # (a QOpenGLWidget can't reliably drive it itself). Live/non-seekable
        # streams and text fields are left alone, so channel zapping and typing
        # still work. On macOS it also handles cursor-hide over the video.
        self._mac_input_filter = _MacInputFilter(self)

        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        """(Re)apply every control tooltip from the active language. Called
        once at construction and again live when the language changes."""
        self.prev_btn.setToolTip(tr("tooltip_previous_channel") + " (Ctrl+Left)")
        self.next_btn.setToolTip(tr("tooltip_next_channel") + " (Ctrl+Right)")
        self.pause_btn.setToolTip(tr("tooltip_pause_resume"))
        self.back_btn.setToolTip(tr("tooltip_back_10s"))
        self.fwd_btn.setToolTip(tr("tooltip_forward_30s"))
        self.mute_btn.setToolTip(tr("tooltip_mute_unmute"))
        self.vol.setToolTip(tr("tooltip_volume"))
        self.ts_btn.setToolTip(tr("tooltip_timeshift"))
        self.rec_btn.setToolTip(tr("tooltip_record"))
        self.opts_btn.setToolTip(tr("tooltip_audio_subs_aspect"))
        self.stop_btn.setToolTip(tr("tooltip_stop_playback"))
        self.pop_btn.setToolTip(
            tr("tooltip_popout_exit") if self._popout_mode
            else tr("tooltip_popout"))
        self.fs_btn.setToolTip(tr("tooltip_fullscreen"))
        self.fs_prev_btn.setToolTip(tr("tooltip_previous_channel") + " (Left)")
        self.fs_next_btn.setToolTip(tr("tooltip_next_channel") + " (Right)")
        self.fs_pause_btn.setToolTip(tr("tooltip_pause_resume"))
        self.fs_back_btn.setToolTip(tr("tooltip_back_10s"))
        self.fs_fwd_btn.setToolTip(tr("tooltip_forward_30s"))
        self.fs_mute_btn.setToolTip(tr("tooltip_mute_unmute"))
        self.fs_vol.setToolTip(tr("tooltip_volume"))
        self.fs_ts_btn.setToolTip(tr("tooltip_timeshift"))
        self.fs_rec_btn.setToolTip(tr("tooltip_record"))
        self.fs_opts_btn.setToolTip(tr("tooltip_audio_subs_aspect"))
        self.fs_exit_btn.setToolTip(tr("tooltip_exit_fullscreen") + " (Esc)")
        self._label_live_button(force=True)
        self.local_buffer_seek.setToolTip(tr("tooltip_timeshift"))
        self._dvr_switch.retranslate()
        self._ts_switch.retranslate()
        self._ld_refresh()

    # -- event filter (fullscreen overlay reveal on hover) --------------------

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.Type.Resize
                and obj is getattr(self, "_ov_surface", None)):
            # The video the floating bars sit on changed size while the
            # player did not: in the pop-out, the control bar coming back
            # shrinks it, and a bar placed against the taller video lay over
            # the control bar until it was next shown.
            self._place_floating_bars()
        if obj is not self.video and obj is getattr(self, "_mirror", None):
            return super().eventFilter(obj, event)
        if obj in getattr(self, "_local_buffer_controls", ()):
            if event.type() in (QEvent.Type.Enter, QEvent.Type.MouseMove,
                                 QEvent.Type.MouseButtonPress,
                                 QEvent.Type.MouseButtonRelease):
                self._reveal_local_buffer()
                if self._popout_autohide:
                    self._popout_bar_timer.start()
            return super().eventFilter(obj, event)
        if self.seek_overlay is not None and obj in (self.seek_overlay,
                                                     self.seek):
            # Keep the hover scrubber alive while the pointer is on it.
            if event.type() in (event.Type.Enter, event.Type.MouseMove,
                                 event.Type.MouseButtonPress):
                self._seek_overlay_timer.start()
            return super().eventFilter(obj, event)
        if obj is self.video:
            if event.type() == event.Type.MouseMove:
                if self._fs_ui:
                    self._show_overlay()
        elif obj is self.bar and self._popout_autohide:
            # Keep the auto-hiding pop-out bar up while the pointer is on it,
            # so it never fades out from under a button you're reaching for.
            if event.type() in (event.Type.Enter, event.Type.MouseMove):
                self._popout_bar_timer.start()
        elif self._fs_ui and event.type() in (event.Type.Enter,
                                              event.Type.MouseMove):
            self._overlay_timer.start()
        return super().eventFilter(obj, event)

    # -- video mouse handlers (signals from _MpvGLWidget) ------------------

    def _on_video_dbl_click(self) -> None:
        # A double-click toggles fullscreen - cancel the pending single-click
        # pause it started, and swallow the trailing release so it doesn't
        # schedule a fresh one.
        self._click_timer.stop()
        self._ignore_next_release = True
        self.double_clicked.emit()

    def _video_single_click(self) -> None:
        """A confirmed single click on the video (no double-click followed):
        the controls come up - it pauses nothing. Pausing is the centre
        button's, the play/pause button's and the keys' (Space, K, the
        media keys): a click meant to bring the controls back, or to give
        the window focus, stopped the picture."""
        if self.current_url is None:
            return
        self._reveal_center()

    def _on_playback_error(self, msg: str) -> None:
        # Suppress the aborted-playback / "loading failed" mpv fires while a
        # user-initiated stop unwinds the current stream. Any error after
        # play() has cleared the flag is a real one and gets forwarded.
        if self._stopping:
            return
        # The window says what went wrong; nothing is loading any more.
        self._ld_clear()
        self.playback_error.emit(msg)

    def _on_video_press(self, event) -> None:
        # Remember where a left-press started so the release can tell a click
        # (toggle pause) from a drag (move the pop-out window) - in every mode.
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
        if not self._popout_mode or self._fs_ui:
            # Docked or fullscreen: right-click opens the full context menu
            # (pause/stop, fullscreen toggle, audio/subtitle/aspect options,
            # send to multiview). No drag in these modes.
            if event.button() == Qt.MouseButton.RightButton:
                self.docked_context_menu.emit(event.globalPosition().toPoint())
            return
        if event.button() == Qt.MouseButton.RightButton:
            # Detached window: right-click opens its context menu.
            self.popout_context_menu.emit(event.globalPosition().toPoint())
            return
        if event.button() == Qt.MouseButton.LeftButton:
            # Drag the video to move the pop-out window - works with or without
            # a title bar. The move only starts once the pointer has actually
            # moved (see _on_video_move), so a plain click / double-click still
            # toggles fullscreen instead of dragging.
            self._popout_drag_from = event.position().toPoint()

    def _on_video_move(self, event) -> None:
        # Only a pointer that actually moved: Qt sends a synthetic move when
        # what is under a resting pointer changes, which is no one's hover.
        at = event.globalPosition()
        last = getattr(self, "_dvr_pointer", None)
        self._dvr_pointer = at
        if last is None or (at - last).manhattanLength() > 2:
            self._reveal_local_buffer()
        # A timeshift channel's timeline reappears on any mouse activity over
        # the video (docked or fullscreen) and re-arms its idle fade.
        if self._seek_mode == "timeline":
            self._show_ts_timeline()
        if self._popout_mode:
            self._reveal_popout_bar()   # no-op unless auto-hide is on
        drag_from = getattr(self, "_popout_drag_from", None)
        if (drag_from is not None
                and event.buttons() & Qt.MouseButton.LeftButton):
            if (event.position().toPoint() - drag_from).manhattanLength() > 6:
                self._popout_drag_from = None
                handle = self.window().windowHandle()
                if handle is not None:
                    handle.startSystemMove()
            return
        # A hover flashes the centre play/pause button in every mode (docked,
        # fullscreen and pop-out) so the affordance is consistent everywhere:
        # while playing it fades after a short idle, while paused it stays up.
        self._reveal_center()
        self._reveal_sleep_badge()      # no-op unless a sleep timer is armed
        if not self._fs_ui and self._seekable:
            # Docked / pop-out, seekable content: reveal the floating scrubber.
            self._show_seek_overlay()

    def _on_video_release(self, event) -> None:
        self._popout_drag_from = None
        if event.button() != Qt.MouseButton.LeftButton:
            return
        press = getattr(self, "_press_pos", None)
        self._press_pos = None
        # Swallow the release that trails a double-click (it already toggled
        # fullscreen).
        if self._ignore_next_release:
            self._ignore_next_release = False
            return
        # A real click (pointer didn't travel) toggles pause - after a short
        # wait so a double-click can cancel it (that's fullscreen). A press that
        # moved the pointer (a pop-out drag, a scrub) is not a click.
        moved = (press is None
                 or (event.position().toPoint() - press).manhattanLength() > 6)
        if moved:
            return
        self._click_timer.start()

    def _mpv_seekable(self) -> bool:
        """Whether the underlying mpv stream can seek (has a real duration),
        independent of _seek_mode - a catch-up archive segment is seekable even
        in 'timeline' mode, so arrows can fine-seek inside it."""
        if self.local_buffer_window() is not None:
            return True
        m = self.video.mpv
        try:
            return bool(m is not None and m.duration and m.duration > 1)
        except Exception:
            return False

    def arrow_scrub(self, back: bool, step: bool = False) -> bool:
        """Single arrow action. A bare arrow fine-seeks by SEEK_BACK/SEEK_FWD
        (within a movie, a catch-up segment, or the live buffer); Shift+arrow
        (*step*) jumps the live timeline TIMELINE_STEP_MIN into the archive.
        Returns True when it consumed the key."""
        if self.current_url is None:
            return False
        # Mode, not bar visibility - the timeline auto-hides but stays steppable.
        timeline = self._seek_mode == "timeline"
        if step and timeline:
            # Shift+arrow: explicit timeline step into the archive.
            self._timeline_step(-self.TIMELINE_STEP_MIN if back
                                else self.TIMELINE_STEP_MIN)
            return True
        if self._mpv_seekable():
            # Bare arrow (or Shift on a non-timeline stream): fine-seek within a
            # movie, a catch-up segment, or a seekable live buffer.
            self._relative_seek(-self.SEEK_BACK if back else self.SEEK_FWD)
            return True
        if timeline:
            # Timeshift live edge mpv won't fine-seek: swallow the bare arrow so
            # it doesn't swap the channel, but don't do a jarring multi-minute
            # jump - Shift+arrow steps into the archive when you want that.
            return True
        return False

    def _mac_show_cursor(self) -> None:
        """Un-hide the macOS override cursor (safety for when playback ends
        while it was hidden and no mouse move follows). No-op off macOS."""
        if sys.platform == "darwin":
            from ..core.platform_macos import set_cursor_hidden
            set_cursor_hidden(False)

    def _on_seek_key_press(self, event) -> None:
        # Up/Down = volume, and it works on live too (no seeking needed).
        # Auto-repeat is fine here - holding the key ramps the volume.
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._nudge_volume(5 if event.key() == Qt.Key.Key_Up else -5)
            return
        if not self._mpv_seekable():
            return
        step = -self.SEEK_BACK if event.key() == Qt.Key.Key_Left else self.SEEK_FWD
        if event.isAutoRepeat():
            return  # the hold timer drives the continuous seek
        self._relative_seek(step)
        self._seek_hold_dir = step
        self._seek_hold_timer.start()

    def _on_seek_key_release(self, event) -> None:
        if not event.isAutoRepeat():
            self._seek_hold_timer.stop()

    def _nudge_volume(self, delta: int) -> None:
        """Bump the volume by *delta* (the slider's valueChanged applies it to
        mpv and unmutes if needed)."""
        self.vol.setValue(max(0, min(100, self.vol.value() + delta)))

    def _on_mpv_eof(self) -> None:
        """mpv says the file ended normally. Raise finished() once, sharing
        the guard with the eof-reached poll so whichever notices first wins
        and the other is a no-op."""
        try:
            if self._stopping or self.current_url is None:
                return      # a user-initiated stop is not an end of file
            if self._eof_seen:
                return
            if self._dvr is not None and self._dvr_on_eof():
                return      # the app's own buffer goes on from the store
            self._eof_seen = True
            log.info("eof event: %s", url_for_log(self.current_url))
            self.finished.emit()
        except RuntimeError:
            # This arrives from mpv's event thread via a queued connection,
            # so it can land after the player's C++ object is gone (quit
            # while a file happens to end). Nothing left to tell.
            pass

    def _wire_video(self, w) -> None:
        """Connect a video widget's signals/filters to the player."""
        w.installEventFilter(self)
        w.setMouseTracking(True)
        fallback = getattr(w, "fallback", None)
        if fallback is not None:
            fallback.connect(self._video_fallback)
        w.playback_error.connect(self._on_playback_error)
        w.eof_event.connect(self._on_mpv_eof)
        w.video_dbl_click.connect(self._on_video_dbl_click)
        w.video_mouse_press.connect(self._on_video_press)
        w.video_mouse_move.connect(self._on_video_move)
        w.video_mouse_release.connect(self._on_video_release)
        w.video_key_press.connect(self._on_seek_key_press)
        w.video_key_release.connect(self._on_seek_key_release)

    def _video_fallback(self, reason: str) -> None:
        """The picture's own thread could not draw (media/video_thread.py):
        the window-thread surface takes its place, and a stream that was
        starting starts again on it."""
        old = self.video
        if getattr(old, "threaded", False) is not True:
            return
        if self._mirror is not None:
            self.popout_requested.emit()     # back to docked first
        again = self._play_args if self.current_url is not None else None
        self.video = swap_for_fallback(old, self._wire_video)
        self._video_swapped = True
        self._ov_surface = self.video
        self._lock_video_box()
        for ov in (self.seek_overlay, self.ts_timeline, self._stats_overlay, self.nextup_btn,
                   self.local_buffer_bar, self.stream_info_badge, self._ld_overlay,
                   self.live_badge, self.sleep_badge, self.number_badge, self._blackout):
            if ov is not None and ov.parentWidget() is self:
                ov.raise_()
        log.warning("video: the window's thread draws the picture from now on (%s)", reason)
        if again is not None:
            url, title, start, kw = again
            QTimer.singleShot(0, lambda: self.play(url, title, start, **kw))

    def _lock_video_box(self) -> None:
        # The video surface always fills whatever the *player* is given;
        # only its minimum differs. What varies is who sizes the player.
        self.video.setMinimumHeight(190 if self._fs_ui else 0)
        self.video.setMaximumHeight(16777215)
        if self._fs_ui or self._popout_mode:
            # Fullscreen and the detached pop-out window both let the host
            # window drive the size, so release every constraint on the player
            # and let its layout stretch to fill the available space. (Forcing
            # a docked floor in a free-resizing window is what used to wedge
            # black bars after a fullscreen round trip.)
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
        else:
            # Docked mini player: pin the *player itself* to one constant
            # height and let the video fill it through the normal layout.
            #
            # The old code sized the video from self.height() instead. That
            # is a feedback loop - a fixed-height video inflates the
            # player's own height, so self.height() reads back the value we
            # just wrote. A wrong height computed during the fullscreen ->
            # normal transition (when self.height() still reported the
            # fullscreen size) therefore reinforced itself on every later
            # resize and showed as unclearable letterbox bars until the app
            # was restarted. A constant player height has no such feedback,
            # so the bars can't get stuck.
            # Count the bar only while it actually lives IN this player: in
            # the mirror pop-out the bar is reparented into the pop-out
            # window, and still reserving its height here left a dead strip
            # under the docked placeholder (the "broken control panel" in the
            # main window while popped out).
            bar_here = self.bar.isVisible() and self.bar.parent() is self
            bar_h = self.bar.sizeHint().height() if bar_here else 0
            spacing = self.layout().spacing() if bar_h else 0
            box_h = getattr(self, "_docked_video_height", self.VIDEO_BOX_HEIGHT)
            if hasattr(self, "_docked_video_height"):
                self.setMinimumHeight(120 + bar_h + spacing)
                self.setMaximumHeight(box_h + bar_h + spacing)
            else:
                self.setFixedHeight(box_h + bar_h + spacing)

    def sizeHint(self) -> QSize:
        size = super().sizeHint()
        if (hasattr(self, "_docked_video_height")
                and not self._fs_ui and not self._popout_mode):
            bar_here = self.bar.isVisible() and self.bar.parent() is self
            bar_h = self.bar.sizeHint().height() if bar_here else 0
            spacing = self.layout().spacing() if bar_h else 0
            size.setHeight(self._docked_video_height + bar_h + spacing)
        return size

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._lock_video_box()
        self._relayout_controls()
        if self._blackout.isVisible():
            self._blackout.setGeometry(self.video.geometry())
        if self._dock_ph is not None and not self._dock_ph.isHidden():
            self._dock_ph.setGeometry(self.rect())   # see _show_dock_placeholder
            self._dock_ph.raise_()
        if self._stats_overlay.isVisible():
            self._place_stats()
        if self.live_badge.isVisible():
            tl = self.video.geometry().topLeft()
            self.live_badge.move(tl.x() + 12, tl.y() + 12)
        if self.sleep_badge.isVisible():
            self._place_sleep_badge()
        if self.number_badge.isVisible():
            self._place_number_badge()
        if self.center_btn.isVisible():
            self._position_center_btn()
        if self.overlay.isVisible() or self.fs_controls.isVisible():
            self._place_overlay()
        self._place_floating_bars()

    def _place_floating_bars(self) -> None:
        """Re-place the bars that float over the bottom of the video - the
        seek bar, the timeshift timeline and the DVR row - that are showing.
        The timeline first: the DVR row goes above it when both show."""
        if self.stream_info_badge.isVisible():
            self._place_stream_info()
        # Not isVisible(): the loading indicator is shown before the pop-out
        # window is, and the picture's resize there (the mirror laid out, or
        # on Windows the player moved in) comes before the indicator is
        # visible - it stayed where the picture had been.
        if self._ld_overlay is not None and not self._ld_overlay.isHidden():
            self._place_loading()
        if self.seek_overlay is not None and self.seek_overlay.isVisible():
            self._place_seek_overlay()
        if self.ts_timeline.isVisible():
            self._place_ts_timeline()
        if self.local_buffer_bar.isVisible():
            self._place_local_buffer()

    def _relayout_controls(self) -> None:
        """Responsive docked control bar: when the right column is dragged
        narrow, drop the lowest-priority always-on controls - the volume
        slider first, then the pop-out button - so the transport buttons keep their size and
        nothing spills outside the column. The mute button stays (volume is
        still reachable by scrolling the video or Up/Down). Leaves the
        state-driven buttons (timeshift / record / next-episode) alone."""
        # Use the player's own width (reliable during resizeEvent; the bar's
        # child width can still lag the layout pass). The detail pane's minimum
        # width (340) is sized so everything fits, so these only ever fire if
        # the player is used somewhere narrower than that - a safety net, not
        # the normal path. When the bar lives in the POP-OUT window (mirror
        # mode) size against that window instead - keying off the narrow
        # docked pane hid the volume slider inside a wide pop-out.
        if self.bar.parent() is self:
            w = self.width()
        else:
            host = self.bar.parentWidget()
            w = host.width() if host is not None else self.width()
        self.vol.setVisible(w >= 330)
        self.pop_btn.setVisible(w >= 315)

    # -- playback defaults -----------------------------------------------------

    # -- hardware decoding -----------------------------------------------------

    def _hwdec_base(self) -> str:
        """The decode mode to use (env > setting > software default).

        Default is 'no' (software), like standalone mpv: modern CPUs decode
        even 4K 10-bit HEVC/HDR fine, and it sidesteps the driver hazards of the
        libmpv OpenGL render API entirely. Users opt into hardware decoding from
        Settings > Playback (or the DOPEIPTV_HWDEC env override)."""
        return (os.environ.get("DOPEIPTV_HWDEC")
                or (str(self._settings.value("hwdec_mode", "") or "")
                    if self._settings else "")
                or "no")

    def apply_default_options(self, load: bool = False,
                              live: bool = True) -> None:
        """Apply persistent playback defaults from Settings to the mpv core.
        *load*: a stream is about to open (play()), and which subtitle track
        it opens with is decided too - *live* says whether it is a channel.
        Otherwise (a Settings save) the track playing is left as it is."""
        m = self.video.mpv
        if m is None or self._settings is None:
            return
        s = self._settings

        def set_opt(prop, value):
            try:
                m[prop] = value
            except Exception:
                pass

        # Nerd: the settings of its own the channel playing has win over
        # these ({} - none, anything but a channel, Nerd off - changes no
        # value). Every start sets them all again, so a channel's own are
        # never left behind for the next stream.
        own = getattr(self, "_chan_own", None) or {}
        set_opt("alang", channel_prefs.audio_langs(own, s.value("audio_lang", "") or ""))
        sub_mode = s.value("sub_mode", "auto")
        if load:
            # Subtitles for films and series alone, if so chosen: a live
            # channel opens without them - its DVB or teletext subtitles
            # can still be turned on in the player.
            self._sub_defaults("off" if live and not live_subtitles(s) else sub_mode,
                               live, set_opt)
            if channel_prefs.own_subtitles(own):
                self._own_subtitles(own, live, set_opt)
            self._ttx_before_load(set_opt)
        elif channel_prefs.own_subtitles(own):
            pass            # the channel's own choice stays while it plays
        elif sub_mode == "off":
            # Hidden, not deselected: choosing a track mid-stream is what
            # freezes a film (see _sub_defaults).
            set_opt("sub-visibility", False)
        else:
            _set_forced_only(m, sub_mode == "forced")
        _sub_look(s, set_opt)
        aspect = channel_prefs.aspect_mode(own, s.value("aspect_mode", "auto"))
        for prop, value in channel_prefs.aspect_props(aspect):
            set_opt(prop, value)

        # Optional video filters (all off/neutral by default). These are plain
        # mpv properties, applied live on the running core.
        #  - deinterlace: smooth interlaced live SD/HD feeds.
        #  - sharpen: unsharp mask strength (0 = off).
        #  - tone-mapping: HDR->SDR curve; harmless on SDR content.
        set_opt("deinterlace", channel_prefs.deinterlace(
            own, s.value("video_deinterlace", "false") == "true"))
        try:
            sharpen = float(s.value("video_sharpen", 0.0) or 0.0)
        except (TypeError, ValueError):
            sharpen = 0.0
        set_opt("sharpen", max(0.0, min(sharpen, 3.0)))
        set_opt("tone-mapping", s.value("video_tonemapping", "auto") or "auto")
        # The network buffer is deliberately NOT touched here.
        #
        # 1.2.2 started pushing cache-secs (and a scaled demuxer-max-bytes)
        # onto the stream already playing, so a changed setting took effect at
        # once. It read as harmless - "just a readahead target, no decoder
        # reinit" - and it was wrong. cache-secs is a FLOOR, not a ceiling:
        # mpv's docs say it overrides demuxer-readahead-secs when larger, i.e.
        # "prefetch at least this much". Nothing bounded the readahead, so the
        # demuxer pulled as hard as the server would serve. A debug log with
        # the buffer set to 10 s showed the cache 52 s deep and still climbing,
        # on an account with a single connection - and live streams started
        # dropping constantly.
        #
        # The buffer is applied where it always was: once per stream, in
        # play(). Changing it in Settings takes effect on the next stream.
        # That is the behaviour that was stable for months, and playback
        # stability outranks the convenience of a live-applied setting.

    def playback_position(self) -> float:
        m = self.video.mpv
        try:
            return float(m.playback_time or 0.0)
        except Exception:
            return 0.0

    def playback_duration(self) -> float:
        m = self.video.mpv
        try:
            return float(m.duration or 0.0)
        except Exception:
            return 0.0

    # -- web pages through yt-dlp (YouTube, Twitch: ui/mw_webstreams.py) ------

    def _core(self):
        """The mpv core, made now if the surface has not made it yet (as
        play() does)."""
        if self.video.mpv is None:
            self.video.show()
            QApplication.instance().processEvents()
        return self.video.mpv

    def has_ytdl_hook(self) -> bool:
        """Whether this libmpv runs yt-dlp by itself for a web page - its
        ytdl hook, a Lua script: a build without Lua (the Flatpak's) has
        none, and the window then asks yt-dlp itself (core/ytdlp.resolve)."""
        if getattr(self, "_ytdl_hook", False):
            return True
        m = self._core()
        if m is None:
            return False
        try:
            m.command("script-message-to", "ytdl_hook", "dopeiptv-probe")
        except Exception:
            return False
        self._ytdl_hook = True
        return True

    def set_ytdl_path(self, path: str) -> None:
        """The yt-dlp the hook runs (the app's own copy, or the system's).
        mpv looks for it again when this changes, so a copy downloaded
        while the app runs is used by the next page opened."""
        m = self.video.mpv
        if m is None or not path or getattr(self, "_ytdl_path", None) == path:
            return
        try:
            m.command("change-list", "script-opts", "append",
                      f"ytdl_hook-ytdl_path={path}")
            self._ytdl_path = path
        except Exception as e:
            log.debug("ytdl_path not set: %s", e)

    def icy_title(self) -> str:
        """What an internet radio station says is on now (its ICY title),
        or ""."""
        m = self.video.mpv
        if m is None:
            return ""
        try:
            # A property, not an option: m["..."] reads options/<name>.
            meta = m.metadata or {}
        except Exception:
            return ""
        if not isinstance(meta, dict):
            return ""
        title = meta.get("icy-title") or meta.get("title") or ""
        return " ".join(str(title).split())[:200]

    def play(self, url: str, title: str, start: float = 0.0,
             *, local_buffer: bool = False,
             buffer_key: str | None = None, web: bool = False,
             own_buffer: bool = True, outside: bool = False) -> bool:
        # local_buffer: a live channel, which gets the rewind buffer unless
        # Settings turned it off. own_buffer False: never dopeIPTV's own
        # (an address opened by hand, which need not be a stream at all) -
        # mpv opens it, with mpv's own rewind. outside: an address from
        # outside the source's lists, not on its server - none of its Nerd
        # headers, user agent, proxy or route (core/nerd.same_server).
        # An address that is no stream, whoever asks for it - a filter graph
        # or an edit list in the address itself (core/stream_address.py) -
        # is never handed to mpv. A source's addresses are looked at where
        # they are handed out; this is the last look.
        # Why the last play() did not open its stream on purpose, in the
        # person's words: the window says it on the video, and never hands
        # such a stream to another player (main_window._start_playback).
        self.refusal = ""
        why = stream_address.refusal(url, remote=False)
        if why:
            log.warning("play: an address that is no stream (%s) is not opened", why)
            return False
        # What a fallback of the video surface plays again (_video_fallback).
        self._play_args = (url, title, start, {"local_buffer": local_buffer,
                                               "buffer_key": buffer_key, "web": web,
                                               "own_buffer": own_buffer,
                                               "outside": outside})
        live = local_buffer
        # Nerd: the channel this is (set_channel, one-shot) and the settings
        # of its own it plays with - none for anything else.
        chan, self._chan_armed = self._chan_armed, None
        # The loading indicator's logo or poster, for this stream alone.
        loading_art = self._ld_take_armed()
        self._reset_stream_info()
        self._chan_key = chan
        self._ttx_reset(live)
        self._chan_own = self._channel_own(chan, buffer_key or url)
        local_buffer = local_buffer and self._live_rewind_minutes() > 0
        # A session of the app's own buffer ends here: its provider
        # connection goes before the next stream opens one. Then, where
        # Settings asks for it, a live channel gets one: mpv plays its local
        # server, and the session reads the provider.
        self._end_dvr()
        # Server mode streams this channel to other devices already: its
        # connection is shared, not opened twice (and for anything else
        # the server lets go of what the player needs first).
        source = None
        hook = self.live_hook if not web else None
        if hook is not None:
            try:
                shared = hook(url, buffer_key if live else None)
                source = self._dvr_adopt(shared) if live and shared is not None else None
            except Exception as e:
                log.debug("server mode's hook: %s", e)
        # buffer_key: the channel, whatever its address - a portal hands
        # out a new one each time - so its history is found again.
        if source is None and local_buffer and own_buffer:
            source = self._dvr_start(url, buffer_key)
        self._local_buffer_enabled = local_buffer
        self._local_buffer_ready = False
        self._bar_view = self._chosen_bar()
        self._local_buffer_hide_timer.stop()
        self._local_slider_window = self._local_fs_window = None
        self._live_lag.reset()
        self._dvr_live, self._live_ahead, self._dvr_span = True, 0.0, 0.0
        self._label_live_button()
        self.local_buffer_bar.hide()
        try:
            # Fresh play cycle - drop the stop-in-progress flag so any real
            # error from this new stream (auth failed, 404, ...) is surfaced.
            self._stopping = False
            self._stall_since = 0.0
            self._eof_seen = False
            self._frame_told = False
            self._contest_told = False
            self._hide_nextup()     # new title: no stale card
            self._blackout.hide()
            self.video.set_blank(False)
            self.title_lbl.setText(title or "")
            self._hide_seek_ui()
            if self.video.mpv is None:
                self.video.show()
                QApplication.instance().processEvents()
                ensure = getattr(self.video, "ensure_started", None)
                if ensure is not None:
                    ensure()
                if self.video.mpv is None and self._video_swapped:
                    # The threaded surface could not start: the window-thread
                    # one took its place (_video_fallback) and starts now.
                    self._video_swapped = False
                    self.video.show()
                    QApplication.instance().processEvents()
            if self.video.mpv is None:
                raise RuntimeError("OpenGL context not ready")
            m = self.video.mpv
            # A web page's file had options of its own: it ends first, so
            # that mpv does not put them back over what is set below.
            if self._own_file_options:
                self._own_file_options = False
                end_file_options(m)
            # Re-enable mpv's video output in case a previous stop() set it
            # to "no" - without this the stream would play with audio only.
            # Also reset the audio track to auto: picking a specific track
            # earlier pins aid to that track *index*, and that index leaks into
            # the next stream. Switching e.g. a movie -> a TV channel that has
            # fewer/other audio tracks then selects a non-existent track and
            # plays silent until a manual reload. Auto lets each new stream pick
            # its own default (apply_default_options re-applies any language
            # preference right after).
            try:
                m["vid"] = "auto"
                m["aid"] = "auto"
            except Exception:
                pass
            try:
                m["force-media-title"] = title or "dopeIPTV"
            except Exception:
                pass
            try:
                m["cache"] = "yes"
                m["cache-secs"] = float(self._cache_secs())
            except Exception:
                pass
            try:
                # The rewind buffer is in memory unless Settings put it on
                # disk. mpv's disk cache is append-only - its hidden file grows
                # 1-4 GB an hour for as long as a channel plays - so that is
                # the user's choice, for a long rewind without the memory.
                mpv_rewind = local_buffer and self._dvr is None
                # Never on a disk for a private playback (core/privacy): its
                # rewind stays in mpv's memory, at the memory's size.
                on_disk = bool(mpv_rewind and self._live_rewind_disk()
                               and not self._dvr_playing_private())
                m["cache-on-disk"] = "yes" if on_disk else "no"
                self._mpv_cache_on_disk = on_disk
                m["demuxer-max-back-bytes"] = self._back_buffer(mpv_rewind, on_disk)
            except Exception as e:
                log.warning("live rewind buffer unavailable: %s", e)
            # Decode mode, set per stream (not in apply_default_options - that
            # also runs on every Settings save, and re-assigning hwdec
            # mid-playback reinitialises the decoder and glitches the video). A
            # changed setting takes effect on the next opened stream. The
            # default is software ('no'); hardware is opt-in for those who want
            # it, and DOPEIPTV_HWDEC still wins over the setting.
            try:
                m["hwdec"] = self._hwdec_base()
            except Exception:
                pass
            self.apply_default_options(load=True, live=live)
            # Night mode: on a core that has not had it yet (nothing at all
            # is written while it is off and always was).
            self._sync_night()
            # Nerd: user agent, headers and proxy for this source (nothing
            # while Nerd is off and never was; the defaults back once it was).
            # For everyone: an internet source's stream through the app's
            # bridge, and its certificate checked - or not opened at all
            # (CertificateUnchecked, below).
            try:
                from ..core import live_latency, nerd
                # A web page is no source's: not its proxy or headers; nor
                # an address from outside its lists. Nor the bridge: a page
                # of the person's own, which yt-dlp opens and resolves
                # itself - played as it always was.
                self._nerd_applied = nerd.apply_stream(
                    m, None if web or outside else nerd.source(),
                    getattr(self, "_nerd_applied", False),
                    url=url, opened=source or url, guard=not web)
                # Nerd: low latency for a live channel the player takes
                # from the provider - not from the rewind buffer; anything
                # else gets back what the last one changed.
                live_latency.apply(m, live and source is None)
                # Nerd: adaptive streams up to a picture size - the variant
                # is chosen once mpv has opened the stream (core/hls_cap.py).
                self._hls_cap = nerd.current().hls_cap()
                if self._hls_cap.picks:
                    self._watch_variants(m)
                # Nerd: each channel's own audio delay (lip sync), as set
                # for it last - in its settings, or for anything that is no
                # channel under a key of its own; untouched while Nerd is
                # off, but for the last stream's own delay, which is not
                # this one's.
                self._delay_key = self._delay_chan = None
                if channel_prefs.delay_enabled():
                    if chan:
                        self._delay_chan = chan
                        value = channel_prefs.delay(self._chan_own.get("adelay"))
                    else:
                        self._delay_key = _delay_key(buffer_key or url)
                        value = float(self._settings.value(self._delay_key, 0)
                                      or 0) if self._settings else 0.0
                    m["audio-delay"] = value
                    self._delay_set = bool(value)
                elif self._delay_set:
                    m["audio-delay"] = 0.0
                    self._delay_set = False
            except CertificateUnchecked:
                raise
            except Exception as e:
                log.debug("nerd stream options: %s", e)
            # Remembered per-title track choices (resume): set before load,
            # exactly like the multiview cells do - mpv keeps a sid/aid set
            # while loading and selects that track once the file's tracks are
            # known. One-shot: consumed here so the next stream starts clean.
            pref_aid, pref_sid = self._pref_aid, self._pref_sid
            self._pref_aid = self._pref_sid = None
            try:
                if pref_aid is not None:
                    m["aid"] = int(pref_aid)
                if pref_sid == "no":
                    # Off for this title: on a film the track chosen at
                    # the start stays, hidden (_sub_defaults).
                    self._sub_rule = None
                    if live:
                        m["sid"] = "no"
                    else:
                        m["sub-visibility"] = False
                elif pref_sid is not None:
                    self._sub_rule = None
                    m["sid"] = int(pref_sid)
                    m["sub-visibility"] = True
            except Exception as e:
                log.debug("track prefs (aid=%r sid=%r) failed: %s",
                          pref_aid, pref_sid, e)
            # A subtitle fetched or picked for this title before. Through
            # change-list: a path is one item whatever characters it holds,
            # where the option's own string form splits on the separator.
            sub_files, self._pref_sub_files = self._pref_sub_files, []
            try:
                m.command("change-list", "sub-files", "clr", "")
                for path in sub_files:
                    m.command("change-list", "sub-files", "append",
                              _subtitle_to_load(path))
                if sub_files:
                    # Chosen for this title on purpose: shown, whatever the
                    # default subtitle mode says (mpv prefers an external
                    # track when it picks one).
                    self._sub_rule = None
                    m["sid"] = "auto"
                    _set_forced_only(m, False)
                    m["sub-visibility"] = True
            except Exception as e:
                log.debug("sub-files %r failed: %s", for_log(sub_files), e)
            try:
                # While muted the slider reads 0. Sending that as the volume
                # would leave the stream silent even after unmuting, since mute
                # is a separate flag and clearing it can't restore a zero
                # volume - so start from the real, pre-mute level.
                level = (getattr(self, "_vol_before_mute", None)
                         if self._muted else None)
                m["volume"] = float(self.vol.value() if level is None
                                    else level)
                m["mute"] = self._muted
            except Exception:
                pass
            self._quiet_output(m, reload=False)
            self._sync_pause_label(False)
            try:
                m.pause = False
            except Exception:
                pass
            # Resume playback at a saved offset (in seconds) via mpv's start
            # option; reset it otherwise so it doesn't leak to the next file.
            try:
                m["start"] = str(float(start)) if start and start > 1 else "none"
            except Exception:
                pass
            try:
                self._arm_stream_info(m, source or url)
            except Exception as e:
                log.debug("Stream info unavailable: %s", e)
            # Whether it is sound alone - known once mpv lists its tracks.
            # Only read here: the window draws the visualiser for it.
            try:
                self._ao_arm(m, source or url)
            except Exception as e:
                log.debug("audio-only check unavailable: %s", e)
            # The loading indicator: from this load's start-file to its
            # playback-restart (media/loading_overlay.py). Never raises.
            self._ld_hook(m)
            self._ld_begin(title, loading_art)
            rtsp_opts = {} if web or source else rtsp_file_options(url)
            if web:
                self._own_file_options = load_with_options(m, url, WEB_FILE_OPTIONS)
            elif rtsp_opts:
                self._own_file_options = load_with_options(m, url, rtsp_opts)
            else:
                m.play(source or url)
            self.current_url = url
            # Unpause AGAIN, after the load. keep-open=yes pauses mpv when a
            # file ends, and that pause is PLAYER state, not file state: it
            # survives loading the next one. Clearing it before the load (as
            # above) is not enough, because mpv is still sitting at the old
            # file's end and re-asserts it - which is why autoplay put the
            # next episode on screen and left it standing still.
            self._user_paused = False
            try:
                m.pause = False
            except Exception:
                pass
            self._play_gen = getattr(self, "_play_gen", 0) + 1
            gen = self._play_gen
            for delay in (250, 900):
                QTimer.singleShot(
                    delay, lambda g=gen: self._ensure_unpaused(g))
            self._pos_timer.start()
            # Safety net for the "silent until you replay it" case: switching
            # streams while the previous one tears down can land with no audio
            # track selected even though aid=auto. Re-check shortly after load
            # and force the first audio track if none is active.
            for delay in (500, 1500):
                QTimer.singleShot(delay, self._ensure_audio_selected)
            return True
        except CertificateUnchecked as e:
            # The stream's certificate is to be checked and this core will
            # not: not opened, and the stream before it ends too - never a
            # stream played unchecked in its place.
            log.warning("play: %s - its certificate cannot be checked here, not opened (%s)",
                        url_for_log(url), e)
            self.refusal = tr("player_tls_unverifiable")
            try:
                if self.video.mpv is not None:
                    self.video.mpv.command("stop")
            except Exception as stop_err:
                log.debug("play: the stream before not stopped: %s", stop_err)
            self.current_url = None
            self._reset_stream_info()
            self._ao_reset()
            self._ld_clear()
            self._end_dvr()
            return False
        except Exception as e:
            log.error("Embedded playback failed: %s: %s", type(e).__name__, e)
            self.current_url = None
            self._reset_stream_info()
            self._ao_reset()
            self._ld_clear()
            self._end_dvr()
            return False

    def nerd_reapply(self) -> dict[str, list[str]] | None:
        """Nerd's settings changed while this player's core plays: onto the
        core at once (nerd.reapply_core), and the quality cap's variant
        switch armed for the new cap - a reconnect that reopens the stream
        before the next play() would otherwise open it on the smallest
        variant the new cap puts on the core, with no switch to leave it.
        None without a core."""
        from ..core import nerd
        m = getattr(self.video, "mpv", None)
        if m is None:
            return None
        done = nerd.reapply_core(m, picker=True)
        self._hls_cap = nerd.current().hls_cap()
        if self._hls_cap.picks:
            self._watch_variants(m)
        return done

    def _watch_variants(self, m) -> None:
        """Nerd's quality cap: each stream core *m* opens goes to the
        variant the cap allows. Hooked once per core, and only once a cap
        has asked for it; it does nothing while none does."""
        watched = getattr(self, "_variants_watched", None)
        if watched is not None and watched() is m:
            return
        import weakref

        from ..core import hls_cap
        try:
            me, core = weakref.ref(self), weakref.ref(m)
        except TypeError:
            return

        def loaded(_evt) -> None:        # mpv's event thread
            try:
                player, mpv_core = me(), core()
                cap = getattr(player, "_hls_cap", None) if player is not None else None
                if mpv_core is not None and cap is not None and cap.picks:
                    hls_cap.apply_pick(mpv_core, cap)
            except Exception as e:
                log.debug("nerd: adaptive stream variant: %s", e)
        try:
            m.event_callback("file-loaded")(loaded)
        except Exception as e:
            log.info("nerd: adaptive streams not capped by size: %s", e)
            return
        self._variants_watched = core

    def _ensure_unpaused(self, gen: int) -> None:
        """Clear a pause the user never asked for, shortly after a load.

        The one that matters is keep-open's end-of-file pause carried into
        the next file. *gen* pins this to the play() call that armed it, so
        a later stream is never touched, and a pause the user pressed in
        the meantime is left alone."""
        try:
            if gen != getattr(self, "_play_gen", 0):
                return
            m = self.video.mpv
            if m is None or self.current_url is None:
                return
            if getattr(self, "_user_paused", False):
                return
            if m.pause:
                log.info("clearing a pause nobody asked for (keep-open eof)")
                m.pause = False
                self._sync_pause_label(False)
        except Exception:
            # Fires up to 900 ms after the load, so it can outlive the
            # player on a quick quit. A missed unpause is not worth a crash.
            pass

    def _ensure_audio_selected(self) -> None:
        """If playback has audio tracks but none is active (a stream-switch
        race that otherwise plays silent until you replay it), select the
        first one. A no-op when audio is already playing."""
        m = self.video.mpv
        if m is None or self.current_url is None:
            return
        try:
            if m.aid:               # already on a track (truthy id)
                return
            tracks = [t for t in (m.track_list or [])
                      if t.get("type") == "audio"]
        except Exception:
            return
        if tracks:
            try:
                m.aid = tracks[0].get("id")
            except Exception:
                pass

    # -- seeking ---------------------------------------------------------------

    def _seek_widgets(self):
        # Only the fullscreen scrubber set; the docked scrubber lives in the
        # hover overlay, whose whole container is shown/hidden instead.
        return (self.fs_seek, self.fs_time_lbl, self.fs_back_btn,
                self.fs_fwd_btn)

    def _hide_seek_ui(self) -> None:
        for wdg in self._seek_widgets():
            wdg.hide()
        self._hide_seek_overlay(force=True)

    def _do_seek(self, seconds: int) -> None:
        if self._local_buffer_enabled and self._seek_mode == "live":
            self._seek_local_buffer(seconds, self._local_fs_window)
            return
        # In 'program' mode the seek bar is virtual: the archive stream can't be
        # seeked in place (it snaps to live), so ask the app to re-load the
        # archive at the chosen programme offset instead.
        if self._seek_mode == "program" and self._program_window > 1:
            self.program_seek.emit(int(max(0, seconds)))
            return
        m = self.video.mpv
        if m is None:
            return
        try:
            m.command("seek", seconds, "absolute")
        except Exception:
            pass

    def _relative_seek(self, seconds: int) -> None:
        window = self.local_buffer_window()
        if window is not None:
            self._bar_view = "dvr"      # the seek is in the buffer: show it
            self._reveal_local_buffer()
            self._seek_local_target(self._local_position() + seconds, window)
            return
        if getattr(self, "_local_buffer_enabled", False):
            return
        # 'program' mode: a relative nudge is a re-load at the new offset (see
        # _do_seek) - the loaded archive segment isn't seekable in place.
        if self._seek_mode == "program" and self._program_window > 1:
            cur = self._program_base + self.playback_position()
            target = max(0, min(self._program_window, cur + seconds))
            self.program_seek.emit(int(target))
            return
        m = self.video.mpv
        if m is None:
            return
        try:
            m.command("seek", seconds)
        except Exception:
            pass

    def toggle_pause(self) -> None:
        # Pressing play after a Stop replays the last item (the main window
        # remembers what it was and resumes where it left off) rather than
        # doing nothing on an empty player.
        if self.current_url is None:
            self.resume_requested.emit()
            return
        m = self.video.mpv
        if m is None:
            return
        try:
            m.pause = not m.pause
            # Remember a DELIBERATE pause, so the post-load unpause watchdog
            # never yanks playback back out of it.
            self._user_paused = bool(m.pause)
            self._sync_pause_label(m.pause)
        except Exception:
            pass

    def _sync_pause_label(self, paused: bool) -> None:
        # Called every position poll - only act on an actual pause<->play
        # transition. Re-emitting paused_changed each poll reset the app's
        # pause-start timestamp, so a long pause measured as ~0 s (the DVR
        # timeshift offset never kicked in).
        if paused == getattr(self, "_paused", None):
            return
        self._paused = paused
        name = "play" if paused else "pause"
        icon = _control_icon(name, self._icon_color, self.ICON_PX)
        self.pause_btn.setIcon(icon)
        self.fs_pause_btn.setIcon(icon)
        # Paused, the loading indicator gives way to the play button; going
        # on, it comes back while the stream is still on its way.
        self._ld_refresh()
        # The big centre button stays up while paused and fades once playing.
        self._reveal_center()
        self.paused_changed.emit(paused)

    # -- options menu ----------------------------------------------------------

    def _show_options_menu(self, anchor: QWidget) -> None:
        # Parent the menu to the ANCHOR's window, not the player: in the
        # mirror pop-out the bar lives in another top-level window, and on
        # Wayland a popup can't be placed over a window it doesn't belong to
        # (the subtitles/options menu simply never appeared there).
        menu = QMenu(anchor.window())
        self.populate_options_menu(menu)
        # On macOS a menu shown over the layer-backed GL video bleeds through
        # (the video keeps repainting behind the non-native popup); force it to
        # its own native window so it composites above the GL surface - alone,
        # never its siblings with it (video_thread.native_menu).
        from .video_thread import native_menu
        native_menu(menu)
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def populate_options_menu(self, menu, extras: bool = True) -> None:
        """Fill *menu* with the full playback options (audio/subtitle tracks,
        delay, aspect, video filters, buffer, sleep timer, stats). Shared by the
        options button and the video right-click menu. *extras*: the window's
        own entries (casting, Watch together) at the end - left out by a menu
        that puts them at its own top level."""
        m = self.video.mpv
        audio = menu.addMenu(tr("opt_audio_track"))
        self.fill_audio_tracks(audio)
        if audio.isEmpty():
            audio.addAction(tr("opt_no_audio_tracks")).setEnabled(False)
        # Night mode next to the tracks: loud and quiet evened out, on the
        # fly. The key the window gives it, after a tab, is only shown.
        key = self.night_key() if callable(self.night_key) else ""
        night = menu.addAction(tr("night_mode") + (f"\t{key}" if key else ""))
        night.setCheckable(True)
        night.setChecked(self._night_on)
        night.setToolTip(tr("night_mode_hint"))
        night.triggered.connect(self._night_from_menu)
        if self.open_teletext is not None and self._ttx_live and self.current_url:
            key = self.teletext_key() if callable(self.teletext_key) else ""
            ttx_act = menu.addAction(tr("ttx_menu") + (f"\t{key}" if key else ""))
            ttx_act.triggered.connect(self.open_teletext)

        subs = menu.addMenu(tr("opt_subtitles"))
        sub_tracks = self.fill_sub_tracks(subs)
        if m and sub_tracks:
            self._ttx_menu_tail(subs)
        if m and self.current_url:
            subs.addSeparator()
            subs.addAction(tr("opt_sub_load_file"), self._load_subtitle_file)
            # Films, episodes and files - a live channel has nothing to find.
            if (self.find_subtitles is not None
                    and getattr(self, "_seek_mode", "vod") == "vod"):
                subs.addAction(tr("opt_sub_find_online"), self.find_subtitles)
        if self._settings is not None:
            # How they look - the same keys as Settings -> Audio & subtitles,
            # applied live and kept as the default.
            subs.addSeparator()
            look = subs.addMenu(tr("set_card_sub_look"))
            st = self._settings

            def look_choice(title, key, default, items) -> None:
                sm = look.addMenu(title)
                cur = str(st.value(key, default))
                for label, val in items:
                    a = sm.addAction(label)
                    a.setCheckable(True)
                    a.setChecked(val == cur)
                    a.triggered.connect(
                        lambda _c, k=key, v=val: self._set_sub_look(k, v))

            look_choice(tr("setting_sub_size"), "sub_scale", "100",
                        [(tr("opt_percent", n=v), v) for v in SUB_SCALES])
            look_choice(tr("setting_sub_color"), "sub_color", "white",
                        [(tr(f"option_sub_color_{k}"), k) for k in SUB_COLORS])
            box = look.addAction(tr("setting_sub_box"))
            box.setCheckable(True)
            box.setChecked(st.value("sub_background", "false") == "true")
            box.toggled.connect(lambda on: self._set_sub_look(
                "sub_background", "true" if on else "false"))

        delay = menu.addMenu(tr("opt_audio_delay"))
        current_delay = 0.0
        try:
            current_delay = float(m["audio-delay"]) if m else 0.0
        except Exception:
            pass
        for val in (-1.0, -0.5, -0.25, 0.0, 0.25, 0.5, 1.0):
            act = delay.addAction(
                f"{val:+.2f} s" if val else tr("opt_delay_default"))
            act.setCheckable(True)
            act.setChecked(abs(current_delay - val) < 0.01)
            act.triggered.connect(
                lambda _c, v=val: (self._set_mpv("audio-delay", v),
                                   self._remember_delay(v)))

        aspect = menu.addMenu(tr("opt_aspect_ratio"))
        for label, val in ((tr("opt_aspect_auto"), "-1"), ("16:9", "16:9"),
                           ("4:3", "4:3"), ("2.35:1", "2.35:1")):
            act = aspect.addAction(label)
            act.triggered.connect(
                lambda _c, v=val: self._set_mpv("video-aspect-override", v))
        stretch = aspect.addAction(tr("opt_aspect_stretch"))
        stretch.triggered.connect(
            lambda _c: self._set_mpv("keepaspect", False))

        # Video filters - same options as Settings > Playback > Video, kept in
        # one place. Each choice is applied live and saved as the default.
        s = self._settings
        video = menu.addMenu(tr("opt_video"))

        def _sub(parent, title, key, default, choices):
            sm = parent.addMenu(title)
            cur = str(s.value(key, default)) if s else default
            for label, val in choices:
                a = sm.addAction(label)
                a.setCheckable(True)
                a.setChecked(val == cur)
                a.triggered.connect(
                    lambda _c, k=key, v=val: self._set_video_opt(k, v))

        _sub(video, tr("setting_deinterlace"), "video_deinterlace", "false",
             ((tr("option_off"), "false"), (tr("option_on"), "true")))
        _sub(video, tr("setting_sharpen"), "video_sharpen", "0.0",
             ((tr("option_off"), "0.0"), (tr("option_low"), "0.5"),
              (tr("option_medium"), "1.0"), (tr("option_high"), "2.0")))
        _sub(video, tr("setting_tonemapping"), "video_tonemapping", "auto",
             ((tr("option_tonemap_auto"), "auto"), ("Hable", "hable"),
              ("Mobius", "mobius"), ("Reinhard", "reinhard"),
              ("BT.2390", "bt.2390"), (tr("option_tonemap_clip"), "clip")))

        buf = menu.addMenu(tr("opt_network_buffer"))
        current_buf = self._cache_secs()
        for secs in (1, 3, 5, 10, 30):
            act = buf.addAction(f"{secs} s")
            act.setCheckable(True)
            act.setChecked(secs == current_buf)
            act.triggered.connect(
                lambda _c, s=secs: self._set_cache_secs(s))

        sleep = menu.addMenu(tr("opt_sleep_timer"))
        off_s = sleep.addAction(tr("opt_off"))
        off_s.setCheckable(True)
        off_s.setChecked(not self._sleep_timer.isActive())
        off_s.triggered.connect(lambda _c: self._start_sleep_timer(0))
        for mins in (15, 30, 45, 60, 90):
            act = sleep.addAction(tr("opt_minutes", n=mins))
            act.triggered.connect(
                lambda _c, mn=mins: self._start_sleep_timer(mn))
        sleep.addSeparator()
        custom = sleep.addAction(tr("opt_sleep_custom"))
        custom.triggered.connect(self._ask_sleep_minutes)

        menu.addSeparator()
        # Audio shaping: the equaliser, and (for music, where the video pane
        # is otherwise a black rectangle) which visualiser to draw.
        eq_act = menu.addAction(tr("eq_title"))
        eq_act.triggered.connect(
            lambda: self.open_equaliser and self.open_equaliser())
        vis = menu.addMenu(tr("vis_title"))
        cur_style = self._vis_style_get()
        off = vis.addAction(tr("vis_off"))
        off.setCheckable(True)
        off.setChecked(not self._vis_on_get())
        off.triggered.connect(lambda: self._vis_pick(None))
        vis.addSeparator()
        for style in self.VIS_STYLES:
            a = vis.addAction(tr(f"vis_style_{style}"))
            a.setCheckable(True)
            a.setChecked(self._vis_on_get() and cur_style == style)
            a.triggered.connect(lambda _c=False, st=style: self._vis_pick(st))

        stats_act = menu.addAction(tr("opt_stats_for_nerds"))
        stats_act.triggered.connect(self._show_stats)

        # Anything the app wants on this menu that the player has no business
        # knowing about - casting, today. A list of (label, callback) the
        # window fills in, so the options button and the video right-click
        # both get it without this module learning what a Chromecast is.
        for label, action in (self.extra_options or []) if extras else []:
            text = label()
            if text:            # "": not offered now (a Nerd entry, Nerd off)
                menu.addAction(text, action)

    def _set_mpv(self, prop: str, value) -> None:
        m = self.video.mpv
        if m is None:
            return
        try:
            m[prop] = value
        except Exception as e:
            log.debug("set %s=%s failed: %s", prop, value, e)

    def _cache_secs(self) -> int:
        try:
            return int(self._settings.value("cache_secs", 10)) \
                if self._settings else 10
        except (TypeError, ValueError):
            return 10

    def _live_rewind_disk(self) -> bool:
        return bool(self._settings) and \
            self._settings.value("live_rewind_disk", "false") == "true"

    def _live_rewind_minutes(self) -> int:
        try:
            return max(0, int(self._settings.value("live_rewind_minutes", 2))) \
                if self._settings else 2
        except (TypeError, ValueError):
            return 2

    def _back_buffer(self, local_buffer: bool, on_disk: bool | None = None) -> int | str:
        """demuxer-max-back-bytes for the stream about to play: the live
        rewind buffer (on the disk or in memory, *on_disk*: as Settings say
        when not given), or what every other stream had before it."""
        if local_buffer:
            return rewind_bytes(self._live_rewind_minutes(),
                                self._live_rewind_disk() if on_disk is None else on_disk)
        return os.environ.get("DOPEIPTV_DEMUX_MAX_BACK") or "50MiB"

    def _set_cache_secs(self, secs: int) -> None:
        if self._settings:
            self._settings.setValue("cache_secs", str(secs))
        self._set_mpv("cache-secs", float(secs))

    def _set_sub_look(self, key: str, value: str) -> None:
        """Keep a subtitle-look choice and apply just the look, live. Not
        apply_default_options: its sid=auto would drop a track picked by
        hand, right while the user is looking at it."""
        if self._settings is None:
            return
        self._settings.setValue(key, value)
        m = self.video.mpv
        if m is None:
            return

        def set_opt(prop, v) -> None:
            try:
                m[prop] = v
            except Exception:
                pass

        _sub_look(self._settings, set_opt)

    def _set_video_opt(self, key: str, value: str) -> None:
        """Persist a video-filter choice and apply it live. Re-uses
        apply_default_options so the running core and the saved default stay in
        one place (the in-player Video menu and Settings write the same keys)."""
        if self._settings:
            self._settings.setValue(key, value)
        self.apply_default_options()

    # -- position polling ------------------------------------------------------

    def _poll_position(self) -> None:
        m = self.video.mpv
        if m is None:
            return
        try:
            dur = m.duration
            pos = m.playback_time
            paused = bool(m.pause)
        except Exception:
            return
        self._sync_pause_label(paused)
        if getattr(self, "_sub_rule", None) is not None:
            self._apply_sub_rule(m)
        if self._ttx_pending:
            self._ttx_poll(m)
        played = pos
        if self._dvr is not None:
            pos = self._dvr_pos(pos)    # the app's own buffer keeps its time
        local_window = self._update_local_buffer(pos)
        self._update_nextup(float(pos or 0.0), float(dur or 0.0))
        # Freeze watchdog: mpv reports core-idle while it has nothing to render
        # (buffer starved / stream stalled). If that persists while we're not
        # paused, the stream has frozen - ask for a reconnect. Covers live too,
        # which returns below as non-seekable.
        try:
            idle = bool(m.core_idle) and self.current_url is not None
        except Exception:
            idle = False
        # Playing: whatever mpv event did not come, nothing is loading.
        if self._ld_phase is not None or self._ld_buffering:
            self._ld_poll(played is not None and self.current_url is not None
                          and not paused and not idle)
        if paused or not idle:
            self._stall_since = 0.0
        else:
            if not self._stall_since:
                self._stall_since = time.time()
            elif time.time() - self._stall_since > self.STALL_SECS:
                self._stall_since = 0.0
                if not (self._dvr is not None and self._dvr_on_stall()):
                    self.stalled.emit()
        if (not getattr(self, "_frame_told", True) and played is not None
                and self.current_url is not None and not paused and not idle):
            self._frame_told = True
            # A stream starts at 0: what it played before this poll is
            # taken off; a time far from 0 says nothing about that.
            try:
                ahead = float(played)
            except (TypeError, ValueError):
                ahead = 0.0
            self.first_frame.emit(ahead if 0.0 <= ahead <= 1.0 else 0.0)
        if self._dvr is not None and not getattr(self, "_contest_told", False):
            try:
                hit = bool(self._dvr.contested)
            except Exception:
                hit = False
            if hit:
                self._contest_told = True
                self.contested.emit()
        # Natural end-of-file: with keep-open=yes mpv pauses on the last frame
        # and flags eof-reached. Emit finished() once so the app can autoplay
        # the next episode. (Live streams never end, so this never fires there.)
        try:
            eof = bool(m.eof_reached) and self.current_url is not None
        except Exception:
            eof = False
        if eof and self._dvr is not None and self._dvr_on_eof():
            eof = False     # the app's own buffer goes on from the store
        if eof:
            if not self._eof_seen:
                self._eof_seen = True
                # The decisive line for "the queue stopped after one track":
                # if this never appears, mpv never reported end-of-file and
                # nothing downstream was ever asked to play the next one.
                log.info("eof reached: %s", url_for_log(self.current_url))
                self.finished.emit()
        else:
            self._eof_seen = False
        if local_window is not None and self._seek_mode == "live":
            self._seekable = False
            self._hide_seek_overlay(force=True)
            if not self.fs_seek.dragging:
                self._local_fs_window = local_window
                self.fs_seek.setRange(0, max(1, int(self._dvr_span)))
                self.fs_seek.setValue(int(min(
                    self._dvr_span, max(0, (pos or 0) - local_window.start))))
            self.fs_time_lbl.setText(self.local_buffer_time.text())
            for widget in self._seek_widgets():
                widget.show()
            self._sync_fs_controls_width(True)
            return
        seekable = (bool(dur) and dur > 1
                    and self._seek_mode not in ("live", "timeline"))
        # In 'program' mode the bar is virtual and always present: scrubbing it
        # re-loads the archive, which briefly zeroes mpv's duration. Keep the
        # bar shown (with its known window) across that blip so it doesn't
        # collapse and re-stretch on every seek.
        program_bar = self._seek_mode == "program" and self._program_window > 1
        self._seekable = seekable or program_bar
        if not self._seekable:
            self._hide_seek_ui()
            self._sync_fs_controls_width(False)
            return
        # A picked catch-up programme: present the bar as the whole programme,
        # with the playhead at (base into the programme + position in the loaded
        # segment), even though the underlying archive stream keeps running to
        # live. Scrubbing this bar re-loads the archive (program_seek).
        if self._seek_mode == "program" and self._program_window > 1:
            dur = self._program_window
            pos = max(0.0, min(self._program_base + (pos or 0.0), dur))
        text = f"{_format_time(pos)} / {_format_time(dur)}"
        # Keep both scrubbers' values current; the docked one lives in the
        # hover overlay (shown on mouse-over), the other in the fullscreen
        # overlay. Only the fullscreen widgets are force-shown here.
        for slider, label in ((self.seek, self.time_lbl),
                              (self.fs_seek, self.fs_time_lbl)):
            label.setText(text)
            if not slider.dragging:
                slider.setMaximum(int(dur))
                slider.setValue(int(pos or 0))
        for w in (self.fs_seek, self.fs_time_lbl,
                  self.fs_back_btn, self.fs_fwd_btn):
            w.setVisible(True)
        self._sync_fs_controls_width(True)

    def _sync_fs_controls_width(self, seek_shown: bool) -> None:
        """Keep the fullscreen control-bar width matched to whether the seek bar
        is showing. Without this the bar can stay full-width after the scrubber
        is hidden (a picked catch-up programme re-loads on each scrub, blanking
        the bar for a moment) - leaving a big empty stretch gap with the buttons
        flung to the edges."""
        if getattr(self, "_fs_seek_shown", None) == seek_shown:
            return
        self._fs_seek_shown = seek_shown
        if self._fs_ui and self.fs_controls.isVisible():
            self._place_overlay()

    def progress_percent(self) -> float:
        m = self.video.mpv
        if m is None:
            return 0.0
        try:
            dur = m.duration
            pos = m.playback_time
        except Exception:
            return 0.0
        if not dur:
            return 0.0
        return max(0.0, min(100.0, 100.0 * (pos or 0) / dur))

    # -- volume ----------------------------------------------------------------

    def _set_volume(self, value: int) -> None:
        # Dragging the slider up while muted implicitly unmutes, so the slider
        # never lies about what you'll hear.
        if value > 0 and getattr(self, "_muted", False):
            self._muted = False
            self._apply_mute_icon()
        m = self.video.mpv
        if m is not None:
            try:
                m["volume"] = float(value)
                m["mute"] = getattr(self, "_muted", False)
            except Exception:
                pass
            self._quiet_output(m, reload=True)
        for s in (self.vol, self.fs_vol):
            if s.value() != value:
                s.blockSignals(True)
                s.setValue(value)
                s.blockSignals(False)
        if self._settings is not None:
            self._settings.setValue("volume", int(value))

    def refresh_icons(self) -> None:
        """(Re)draw every control-button icon in the current theme's text
        colour. Called at construction and again when the theme changes so
        the icons stay legible in light and dark themes."""
        self._icon_color = P.get("text2", "#ECECF1")
        size = QSize(self.ICON_PX, self.ICON_PX)
        for btn, name in self._icon_names.items():
            colour = ("#FF5C5C" if name == "record"
                      else "#F2B01E" if name == "rewind"   # amber timeshift
                      else self._icon_color)
            btn.setText("")
            btn.setIcon(_control_icon(name, colour, self.ICON_PX))
            btn.setIconSize(size)
        # Reassert the pause/mute icons to match current state.
        self._sync_pause_label(bool(getattr(self, "_paused", False)))
        self._apply_mute_icon()
        if self._ld_overlay is not None:
            self._ld_overlay.update()  # it reads the palette as it draws

    def _apply_mute_icon(self) -> None:
        icon = _control_icon(
            "mute" if getattr(self, "_muted", False) else "volume",
            self._icon_color, self.ICON_PX)
        self.mute_btn.setIcon(icon)
        self.fs_mute_btn.setIcon(icon)

    def toggle_mute(self) -> None:
        self._muted = not self._muted
        # Make the volume sliders follow the mute state: drop to 0 while muted,
        # spring back to the previous level when unmuted. We move them without
        # emitting valueChanged so the real (pre-mute) volume isn't overwritten
        # in settings.
        if self._muted:
            self._vol_before_mute = self.vol.value()
            shown = 0
        else:
            shown = getattr(self, "_vol_before_mute", self.vol.value()) or 0
        for s in (self.vol, self.fs_vol):
            s.blockSignals(True)
            s.setValue(shown)
            s.blockSignals(False)
        m = self.video.mpv
        if m is not None:
            try:
                m["mute"] = self._muted
                # The sliders moved with their signals blocked, so _set_volume
                # never ran and mpv was never told the new level. Push it here:
                # a stream that STARTED while muted was handed the muted
                # slider's 0 as its volume, and clearing the mute flag does not
                # undo a zero volume - it stayed silent until the film was
                # restarted.
                m["volume"] = float(shown)
            except Exception:
                pass
            self._quiet_output(m, reload=True)
        self._apply_mute_icon()

    def _quiet_output(self, m, reload: bool) -> None:
        """While muted the player opens no sound output of its own (mpv's
        null output keeps the time): a stream to the system's sound server
        for every channel, even muted, made it renegotiate, and another
        program's sound (Spotify) crackled at each change. Unmuted, the
        system's own output again - *reload*: for the stream playing now."""
        want = QUIET_AO if getattr(self, "_muted", False) else ""
        if getattr(self, "_ao_now", "") == want:
            return
        try:
            if want == QUIET_AO:
                self._ao_user = ao_text(m["ao"])
            else:
                want = getattr(self, "_ao_user", "")
            aid = m.aid if reload else None
            m["ao"] = want
            self._ao_now = QUIET_AO if want == QUIET_AO else ""
            if reload and m.path is not None:
                m.command("ao-reload")
                # A device that could not be opened drops the sound track;
                # the one picked comes back with the next output.
                if aid not in (None, False) and m.aid is False:
                    m.aid = aid
        except Exception as e:
            log.debug("sound output not switched: %s", e)

    # -- stream recording ------------------------------------------------------

    def start_stream_record(self, path: str) -> bool:
        # With the app's own buffer the recording reads the buffer: no
        # connection of its own, and a pause or a rewind leaves it whole.
        if self._dvr is not None and self._dvr_record(path):
            return True
        m = self.video.mpv
        if m is None:
            return False
        try:
            m["stream-record"] = path
            return True
        except Exception as e:
            log.error("stream-record failed: %s", e)
            return False

    def stop_stream_record(self) -> None:
        self._stop_buffer_record()
        m = self.video.mpv
        if m is None:
            return
        try:
            m["stream-record"] = ""
        except Exception:
            pass

    def stop(self) -> None:
        self._reset_stream_info()
        self._ao_reset()
        self._ld_clear()
        self._ld_armed = (None, False)
        self._ttx_reset()
        self._ttx_pending = False
        self._end_dvr()
        self._local_buffer_enabled = False
        self._local_buffer_ready = False
        self._local_buffer_hide_timer.stop()
        self._local_slider_window = self._local_fs_window = None
        self._live_lag.reset()
        self._dvr_live, self._live_ahead, self._dvr_span = True, 0.0, 0.0
        self._label_live_button()
        self.local_buffer_bar.hide()
        # Tell the main window first (while position/duration are still valid)
        # so it can remember this item as last-active and save its resume point.
        self.stopped.emit()
        # Flip the video pane to blank BEFORE telling mpv to stop. On Wayland
        # mpv's update_cb can fire once more after command("stop") is issued;
        # if we hadn't blanked yet, that callback would repaint with an mpv
        # context that libmpv has already begun tearing down and segfault the
        # whole app. Blanking first makes any late repaint a harmless clear.
        self.video.set_blank(True)
        # Flag "we are stopping" so the playback-error handler ignores the
        # aborted-playback messages libmpv fires while it winds the current
        # stream down - without this, a user-initiated stop shows a scary
        # "Stream error: loading failed" toast.
        self._stopping = True
        m = self.video.mpv
        if m is not None:
            # Disable mpv's video output too, so nothing at all renders back
            # through the render context - defeats the "stale last frame" and
            # "torn artefacts" the pane shows when only Qt clears the FBO.
            try:
                m["vid"] = "no"
            except Exception:
                pass
            # Clear the playlist and issue stop. (Feeding mpv a null: source
            # to force a black frame turned out to be wrong: mpv rejects it
            # and fires a "loading failed" error that the UI showed to the
            # user on every manual stop.)
            for cmd in (("playlist-clear",), ("stop",)):
                try:
                    m.command(*cmd)
                except Exception:
                    pass
        self.stop_stream_record()
        self.current_url = None
        self.title_lbl.setText("")
        self._center_hide_timer.stop()
        self.center_btn.hide()
        self._pos_timer.stop()
        self._hide_seek_ui()
        self._stats_overlay.hide()
        if self._stats_timer.isActive():
            self._stats_timer.stop()
            self.stats_shown.emit(False)
        self.live_badge.hide()
        self.ts_timeline.hide()
        self._sync_pause_label(True)
        self._mac_show_cursor()
        # Force several deferred repaints - the compositor sometimes ignores a
        # single update() while it's still animating the last mpv frame, and
        # any late mpv update_cb also just re-hits the blank branch.
        QTimer.singleShot(0, self.video.update)
        QTimer.singleShot(80, self.video.update)
        QTimer.singleShot(240, self.video.update)
        # Show the raster-painted black cover on top of the GL surface. This
        # is what actually makes the pane genuinely black on compositors
        # where paintGL's glClear alone isn't enough.
        self._blackout.setGeometry(self.video.geometry())
        self._blackout.show()
        self._blackout.raise_()

    def shutdown(self) -> None:
        self._ld_clear()
        self.stop_stream_record()
        self.dvr_shutdown()
        self._pos_timer.stop()
        self._stats_timer.stop()
        self.video.shutdown()
