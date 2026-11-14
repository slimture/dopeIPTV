"""Embedded in-app video player (libmpv OpenGL render API)."""

from __future__ import annotations

import hashlib
import os
import sys
import time

from PyQt6.QtCore import (
    QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize,
    QStandardPaths, Qt, QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QCursor, QIcon, QPainter, QPen, QPixmap, QPolygonF,
)
from PyQt6.QtOpenGL import QOpenGLFramebufferObject
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QMenu, QSizePolicy, QSlider,
    QVBoxLayout, QWidget, QPushButton,
)

from ..core import channel_prefs
from ..core.log import log, redact_url
from ..core.local_buffer import rewind_bytes
from ..i18n import current_language, tr
from .dvr_player import DvrPlayerMixin
from .rewind_bar import RewindBarMixin
from .seek_slider import _SeekSlider, _format_time
from .subtitle_text import fixed_bytes
from .video_surface import _MpvGLWidget, _RasterMirror
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


def _control_icon(name: str, color: str, px: int = 28) -> QIcon:
    """Draw a media-control glyph as a crisp, perfectly centred monochrome
    icon. Hand-drawing (instead of relying on Unicode glyphs, whose ink sits
    at glyph-specific offsets inside the em box) is what makes every button's
    symbol line up at the exact same height and size."""
    scale = 3
    S = px * scale
    pm = QPixmap(S, S)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    col = QColor(color)
    m = S * 0.30
    L, R, T, B = m, S - m, m, S - m
    w, h = R - L, B - T
    midx, midy = S / 2, S / 2
    solid = Qt.PenStyle.SolidLine
    round_cap = Qt.PenCapStyle.RoundCap
    round_join = Qt.PenJoinStyle.RoundJoin

    def fill():
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)

    def stroke(width):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(col, width, solid, round_cap, round_join))

    if name in ("play", "next"):
        fill()
        p.drawPolygon(QPolygonF([QPointF(L, T), QPointF(L, B), QPointF(R, midy)]))
    elif name == "prev":
        fill()
        p.drawPolygon(QPolygonF([QPointF(R, T), QPointF(R, B), QPointF(L, midy)]))
    elif name == "pause":
        fill()
        bw = w * 0.30
        p.drawRoundedRect(QRectF(L, T, bw, h), bw * 0.25, bw * 0.25)
        p.drawRoundedRect(QRectF(R - bw, T, bw, h), bw * 0.25, bw * 0.25)
    elif name == "stop":
        fill()
        p.drawRoundedRect(QRectF(L, T, w, h), w * 0.14, w * 0.14)
    elif name == "record":
        fill()
        p.drawEllipse(QRectF(L, T, w, h))
    elif name == "rewind":
        fill()
        p.drawPolygon(QPolygonF([QPointF(midx, T), QPointF(midx, B),
                                 QPointF(L, midy)]))
        p.drawPolygon(QPolygonF([QPointF(R, T), QPointF(R, B),
                                 QPointF(midx, midy)]))
    elif name == "nextep":
        # Skip-to-next: a right-pointing triangle plus a vertical bar (⏭),
        # drawn to the same box as the others so it lines up perfectly.
        fill()
        tri_r = L + w * 0.66
        p.drawPolygon(QPolygonF([QPointF(L, T), QPointF(L, B),
                                 QPointF(tri_r, midy)]))
        bar_w = w * 0.16
        p.drawRoundedRect(QRectF(R - bar_w, T, bar_w, h),
                          bar_w * 0.25, bar_w * 0.25)
    elif name == "exit":
        stroke(S * 0.11)
        p.drawLine(QPointF(L, T), QPointF(R, B))
        p.drawLine(QPointF(R, T), QPointF(L, B))
    elif name == "fullscreen":
        stroke(S * 0.09)
        seg = w * 0.34
        p.drawPolyline(QPolygonF([QPointF(L, T + seg), QPointF(L, T),
                                  QPointF(L + seg, T)]))
        p.drawPolyline(QPolygonF([QPointF(R - seg, T), QPointF(R, T),
                                  QPointF(R, T + seg)]))
        p.drawPolyline(QPolygonF([QPointF(L, B - seg), QPointF(L, B),
                                  QPointF(L + seg, B)]))
        p.drawPolyline(QPolygonF([QPointF(R - seg, B), QPointF(R, B),
                                  QPointF(R, B - seg)]))
    elif name == "popout":
        # The ⧉ glyph, drawn: two equal squares offset diagonally. Drawn rather
        # than typed because the Unicode character came out at whatever size
        # the platform font happened to give it (fine on macOS, tiny on Linux).
        # Uses a WIDER box than the other icons on purpose - the glyph it
        # replaces fills the button, so the shared inner box looked shrunken.
        pl, pt = S * 0.22, S * 0.22
        span = S - 2 * pl                     # full drawing extent
        off = span * 0.26                     # diagonal offset between squares
        side = span - off                     # each square's size
        stroke(S * 0.065)
        rad = S * 0.045
        # Back square (lower-left), then the detached one (upper-right).
        p.drawRoundedRect(QRectF(pl, pt + off, side, side), rad, rad)
        p.drawRoundedRect(QRectF(pl + off, pt, side, side), rad, rad)
    elif name == "options":
        rows = (T, midy, B)
        knobs = (R - w * 0.18, L + w * 0.22, R - w * 0.34)
        rad = S * 0.07
        for y, kx in zip(rows, knobs, strict=True):
            stroke(S * 0.075)
            p.drawLine(QPointF(L, y), QPointF(R, y))
            fill()
            p.drawEllipse(QPointF(kx, y), rad, rad)
    elif name in ("volume", "mute"):
        # Like "popout", drawn in a WIDER box than the solid glyphs. A speaker
        # is a slim shape - a small body, a cone and thin waves - so inside the
        # box the filled icons use it covered barely a third of the button and
        # looked like a different, much smaller icon set next to play/pause.
        # These constants give it roughly the same optical weight as they have.
        vl, vt = S * 0.20, S * 0.20
        span = S - 2 * vl
        vmid = S / 2
        fill()
        body_w, body_h = span * 0.24, span * 0.34
        p.drawRect(QRectF(vl, vmid - body_h / 2, body_w, body_h))
        cone_x = vl + span * 0.46
        p.drawPolygon(QPolygonF([
            QPointF(vl + body_w, vmid - body_h / 2), QPointF(cone_x, vt),
            QPointF(cone_x, vt + span), QPointF(vl + body_w, vmid + body_h / 2)]))
        if name == "volume":
            stroke(S * 0.062)
            for rad in (span * 0.20, span * 0.38):
                p.drawArc(QRectF(cone_x - rad, vmid - rad, rad * 2, rad * 2),
                          -55 * 16, 110 * 16)
        else:
            stroke(S * 0.072)
            xx, dy = cone_x + span * 0.10, span * 0.15
            p.drawLine(QPointF(xx, vmid - dy),
                       QPointF(xx + span * 0.30, vmid + dy))
            p.drawLine(QPointF(xx + span * 0.30, vmid - dy),
                       QPointF(xx, vmid + dy))
    p.end()
    pm.setDevicePixelRatio(scale)
    return QIcon(pm)


# The tags a subtitle track's language comes as, per app language: ISO
# 639-2 (both forms where they differ) and 639-1.
_SUB_LANG_TAGS = {
    "en": "eng,en", "sv": "swe,sv", "es": "spa,es", "de": "ger,deu,de",
    "fr": "fre,fra,fr", "zh": "chi,zho,zh", "ru": "rus,ru", "th": "tha,th",
    "pt": "por,pt", "it": "ita,it", "nl": "dut,nld,nl", "pl": "pol,pl",
    "hr": "hrv,hr", "sr": "srp,sr", "el": "gre,ell,el", "tr": "tur,tr",
    "uk": "ukr,uk", "id": "ind,id", "vi": "vie,vi", "hi": "hin,hi",
    "ja": "jpn,ja", "ko": "kor,ko", "sw": "swa,sw", "ar": "ara,ar",
    "fa": "per,fas,fa", "he": "heb,he", "ur": "urd,ur",
}


def _set_forced_only(m, on: bool) -> None:
    """Show only the forced subtitle events, or everything. mpv 0.37 renamed
    sub-forced-only to sub-forced-events-only, and the libmpv in use may be
    either side of that (Homebrew's and the macOS, Windows and Flatpak
    builds are newer, the AppImage's older): the new name, else the old.
    Setting the old name on a new mpv raised - and a subtitle that had
    loaded fine was then reported as unusable."""
    for name in ("sub-forced-events-only", "sub-forced-only"):
        try:
            m[name] = on
            return
        except Exception:
            continue


_SUB_FIX_MAX_BYTES = 20 * 1024 * 1024


def _subtitle_to_load(path: str) -> str:
    """*path*, or a copy of it without the right-to-left marks a file
    translated from Arabic or Hebrew can keep around every line
    (subtitle_text.py). The copy goes to the cache under the same file
    name, so mpv still reads a language from it."""
    try:
        if os.path.getsize(path) > _SUB_FIX_MAX_BYTES:
            return path
        with open(path, "rb") as fh:
            fixed = fixed_bytes(fh.read())
    except OSError:
        return path
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.CacheLocation)
    if fixed is None or not base:
        return path
    key = hashlib.sha1(os.path.abspath(path).encode(
        "utf-8", "surrogateescape")).hexdigest()[:12]
    folder = os.path.join(base, "subtitles", key)
    copy = os.path.join(folder, os.path.basename(path))
    try:
        os.makedirs(folder, exist_ok=True)
        with open(copy, "wb") as fh:
            fh.write(fixed)
    except OSError as e:
        log.info("subtitle: no fixed copy of %s: %s", path, e)
        return path
    log.info("subtitle: right-to-left marks taken out of %s",
             os.path.basename(path))
    return copy


_ASPECTS = ((16 / 9, "16:9"), (4 / 3, "4:3"), (21 / 9, "21:9"), (2.39, "2.39:1"),
            (1.85, "1.85:1"), (5 / 4, "5:4"), (1.0, "1:1"), (3 / 2, "3:2"))


def aspect_name(value) -> str:
    """mpv's aspect as people write it: 1.7777777777777777 is 16:9."""
    try:
        a = float(value)
    except (TypeError, ValueError):
        return str(value)
    for ratio, name in _ASPECTS:
        if abs(a - ratio) < 0.01:
            return name
    return f"{a:.2f}:1"


def on_off(value) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    text = str(value).strip().lower()
    return {"yes": "on", "true": "on", "no": "off", "false": "off"}.get(text, str(value))


class _StatsLabel(QLabel):
    """Stats for nerds over the picture: names quiet, values bright and in
    fixed-width digits (they change every second without the text
    jumping). A click switches between all of it and the few lines most
    looked at."""

    toggled = pyqtSignal()
    #: The lines of the compact view.
    COMPACT = ("Video", "Audio", "HW dec", "Dropped", "Bitrate", "Cache", "Net")

    def __init__(self, parent) -> None:
        super().__init__("", parent)
        self.compact = False
        self.columns = 1
        self.font_px = 11
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click: fewer or more lines")

    def mousePressEvent(self, event) -> None:      # noqa: N802 - Qt hook
        self.compact = not self.compact
        self.toggled.emit()
        event.accept()

    def _html(self, groups, columns: int) -> str:
        from html import escape

        def table(rows) -> str:
            return ("<table cellspacing='0' cellpadding='0'>" + "".join(
                f"<tr><td style='color:#9A9AA8; padding:1px 12px 1px 0'>{escape(k)}</td>"
                f"<td style='color:#F2F2F6; font-family:monospace; padding:1px 0'>{escape(str(v))}</td></tr>"
                for k, v in rows) + "</table>")
        shown = [[r for r in g if not self.compact or r[0] in self.COMPACT] for g in groups]
        shown = [g for g in shown if g]
        if columns > 1 and len(shown) > 1:
            cells = "<td width='22'></td>".join(f"<td valign='top'>{table(g)}</td>" for g in shown)
            return f"<table cellspacing='0' cellpadding='0'><tr>{cells}</tr></table>"
        return "<div style='margin:0'></div>".join(table(g) + ("<div style='font-size:6px'>&nbsp;</div>"
                                                                if i < len(shown) - 1 else "")
                                                     for i, g in enumerate(shown))

    def _style(self, px: int) -> None:
        self.setStyleSheet(
            "background: rgba(0,0,0,185); border-radius: 8px;"
            f"padding: {max(6, px // 2 + 2)}px {max(8, px - 1)}px; font-size: {px}px;")

    def lay_out(self, groups, max_w: int, max_h: int) -> None:
        """The largest type, one column or two, that fits *max_w* x *max_h*."""
        top = max(10, min(15, round(max_h / 62)))
        tries = [(px, cols) for px in range(top, 8, -1) for cols in (1, 2)]
        for px, cols in tries:
            self._style(px)
            self.setText(self._html(groups, cols))
            self.adjustSize()
            if self.height() <= max_h and self.width() <= max_w:
                self.font_px, self.columns = px, cols
                return
        self.font_px, self.columns = 9, 2


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
    # showed up in the fullscreen-toggle trace).
    _MOUSE_EVS = frozenset({
        QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonRelease,
    })
    _KEY_EVS = frozenset({QEvent.Type.KeyPress, QEvent.Type.KeyRelease})
    _IS_MAC = sys.platform == "darwin"

    def eventFilter(self, obj, event):
        et = event.type()
        if et in self._KEY_EVS:
            if self._handle_seek_key(event, et == QEvent.Type.KeyPress):
                return True
        elif self._IS_MAC and et in self._MOUSE_EVS:
            # Any mouse activity un-hides - including the click that opens
            # a context menu, so the menu never comes up with an invisible
            # pointer. Only idle time over the bare video re-hides.
            from ..core.platform_macos import set_cursor_hidden
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


def _delay_key(channel: str) -> str:
    """Where the audio delay of a stream that is no channel (a film, a
    file) is kept (Nerd) - and where a channel's was, before it moved into
    its own settings (core/channel_prefs.py)."""
    return channel_prefs.legacy_key(channel)


class EmbeddedPlayer(RewindBarMixin, DvrPlayerMixin, QWidget):
    """Video pane with libmpv OpenGL rendering, control bar, and fullscreen overlay."""

    double_clicked = pyqtSignal()
    playback_error = pyqtSignal(str)
    zap = pyqtSignal(int)
    exit_fullscreen = pyqtSignal()
    timeshift_menu = pyqtSignal(object)
    record_menu = pyqtSignal(object)
    popout_requested = pyqtSignal()
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
    # The stream play() opened is playing, once per play(): how many seconds
    # it had already played when the poll saw it (stream health, Nerd).
    first_frame = pyqtSignal(float)
    finished = pyqtSignal()
    next_episode = pyqtSignal()
    paused_changed = pyqtSignal(bool)
    timeshift_seek = pyqtSignal(int)   # minutes back from live (0 = go live)
    program_seek = pyqtSignal(int)     # seconds from the picked programme start
    track_selected = pyqtSignal(str, object)  # "aid"/"sid", track id or "no"
    subtitle_added = pyqtSignal(str)          # a subtitle file now showing

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
        # Where the playing stream's audio delay is kept, if it is: its
        # channel's settings or (anything else) a key of its own.
        self._delay_chan: str | None = None
        self._delay_key: str | None = None
        self._delay_set = False
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

        self.video = _MpvGLWidget(self)
        # Hardware-decode preference from Settings, staged on the widget before
        # its lazy GL init builds the mpv core (env DOPEIPTV_HWDEC still wins).
        if settings is not None:
            self.video.hwdec_pref = str(
                settings.value("hwdec_mode", "") or "") or None
        # Route mpv errors through a filter so a user-initiated stop doesn't
        # surface the "aborted / loading failed" mpv fires while it winds
        # the stream down as a "Stream error" toast.
        self._stopping = False
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
            self.pop_btn: "popout",
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
        self._stats_overlay.toggled.connect(lambda: self._update_stats_text())
        self._stats_overlay.hide()
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._update_stats_text)

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
        toggle pause, like the centre button."""
        if self.current_url is None:
            return
        self.toggle_pause()
        self._reveal_center()

    # -- centre play/pause button ---------------------------------------------

    def _position_center_btn(self) -> None:
        b = self.center_btn
        b.move((self.width() - b.width()) // 2,
               (self.height() - b.height()) // 2)

    def _update_center_icon(self) -> None:
        paused = bool(getattr(self, "_paused", False))
        # Single source of truth for "which glyph is on the button" - _reveal_
        # center reads it to know whether the icon needs redrawing at all.
        self._center_icon_paused = paused
        glyph = "play" if paused else "pause"
        self.center_btn.setIcon(_control_icon(glyph, "#FFFFFF", 30))
        self.center_btn.setIconSize(QSize(30, 30))

    def _reveal_center(self) -> None:
        """Show the centre button and, while playing, arm its fade. While
        paused it stays up until the user resumes.

        This runs on EVERY pointer event over the video, so it must do nothing
        when nothing changed. Redrawing the glyph, re-placing and re-raising
        the button on each event dirtied the region under it as fast as the
        mouse reports - and every one of those repaints costs a slice of a
        video blit, which is why dragging the pointer across a maximized
        video felt sluggish. Placement is handled by the resize path."""
        if getattr(self, "current_url", None) is None:
            self.center_btn.hide()
            return
        paused = bool(getattr(self, "_paused", False))
        if paused is not getattr(self, "_center_icon_paused", None):
            self._update_center_icon()
        if not self.center_btn.isVisible():
            self._position_center_btn()
            self.center_btn.show()
            self.center_btn.raise_()
        if paused:
            self._center_hide_timer.stop()
        else:
            self._center_hide_timer.start()

    def _maybe_hide_center(self) -> None:
        # Keep it up while paused; otherwise let it fade.
        if not getattr(self, "_paused", False):
            self.center_btn.hide()

    def _on_playback_error(self, msg: str) -> None:
        # Suppress the aborted-playback / "loading failed" mpv fires while a
        # user-initiated stop unwinds the current stream. Any error after
        # play() has cleared the flag is a real one and gets forwarded.
        if self._stopping:
            return
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

    # -- detached pop-out window -----------------------------------------------

    # -- macOS mirror pop-out (no reparent) ----------------------------------

    def start_mirror(self, parent: QWidget) -> "_MpvGLWidget":
        """macOS pop-out: build a MIRROR GL surface in *parent* (the pop-out
        window) that renders this player's live mpv stream. The real video
        widget is never moved - reparenting a QOpenGLWidget between windows on
        macOS leaves cocoa presenting a stale (frozen) layer. Instead the mpv
        render context is drawn into a second surface that shares GL resources,
        and the docked video (still rendering, behind a placeholder) keeps
        driving mpv's frame timing. Returns the mirror widget for the caller to
        place in the window."""
        _t_start = time.monotonic()
        # macOS can opt into the raster path with DOPEIPTV_MAC_RASTER=1.
        #
        # It is the same symptom here as there: a GL surface in a SECOND
        # top-level window keeps presenting the frame it had, while
        # rendering reports success the whole way. Linux and Windows both
        # left the GL mirror behind for exactly that, and the raster path
        # has been solid on both since. macOS kept the GL mirror because it
        # was "proven there" - and the fullscreen freeze says it no longer
        # is. This is opt-in rather than the default so the two can be
        # compared in ONE build: same binary, one environment variable.
        #
        # It also brings the stall watchdog, which lives in the raster
        # tick and has therefore never run on macOS.
        _mac_raster = (sys.platform == "darwin"
                       and os.environ.get("DOPEIPTV_MAC_RASTER") == "1")
        if (sys.platform.startswith("linux") or sys.platform == "win32"
                or _mac_raster):
            # DEFAULT Linux and Windows path: raster mirror (see
            # _RasterMirror). Every GL presentation route in a second window
            # shows black on modern Qt/Mesa (X11 and Wayland alike), so all GL
            # stays in the docked widget's proven context and the pop-out gets
            # plain images.
            #
            # Windows joined this path after the GL mirror there rendered the
            # picture upside down and then black - the same symptoms Linux had,
            # and the same cause: a second top-level window's GL surface. macOS
            # keeps the GL mirror, which is proven there.
            # Pacing: a PRECISE 60 Hz poll whose tick renders ONLY when mpv
            # reports a new frame pending - so readbacks run at the stream's
            # real fps with <=16 ms cadence error. (A 30 fps coarse poll beat
            # against 25 fps streams - judder; driving ticks from mpv's update
            # callback flooded the main thread with readbacks - far worse.)
            m = _RasterMirror(parent, mirror_of=self.video)
            self._raster_state = ""
            # New-frame gate: mpv's update callback sets a cheap flag (the
            # slot must NOT render - the callback fires more often than the
            # video fps, and rendering from it flooded the main thread); the
            # 60 Hz tick consumes it. This replaces polling
            # mpv_render_context_update, which silently gated nothing when
            # the binding lacks update() (the try/except defaulted to
            # "always render" = 60 readbacks/s for a 25 fps stream).
            self._raster_pending_frame = True
            self.video.frame_ready.connect(self._raster_mark_frame)
            tick, interval = self._tick_raster_mirror, 16
        else:
            m = _MpvGLWidget(parent, mirror_of=self.video)
            m.setMouseTracking(True)
            tick, interval = m.update, 16
        self._mirror = m
        # Raster mirror: double-buffered offscreen targets. The tick reads
        # LAST tick's buffer (whose GPU work finished long ago - no stall)
        # and submits this tick's render into the other one.
        self._mirror_fbos = [None, None]
        self._mirror_fbo_i = 0
        self._mirror_pending = None
        # Drive the mirror with a steady poll timer, NOT mpv's frame callback:
        # a timer can never stall on a window resize/maximize (the freeze
        # reported when the pop-out was maximized), and while popped out the
        # mirror is the only render loop, so it also paces mpv. Suspend the
        # docked surface so the GPU renders the stream once, not twice.
        self.video._render_suspended = True
        # One forced repaint so the suspended surface CLEARS TO BLACK now.
        # Without it the frame-driven repaints stop while the last video frame
        # is still on the surface, and any gap around the placeholder shows it
        # frozen - intermittently, depending on how the reflow interleaved.
        self.video.update()
        self._mirror_timer = QTimer(self)
        # Precise, not coarse: Qt's default timer slack (~5%) is visible as
        # frame-pacing jitter at video rates.
        self._mirror_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._mirror_timer.timeout.connect(tick)
        self._mirror_timer.start(interval)
        # Float the seek bar, timeshift timeline and stats over the mirror:
        # reparent them into the pop-out window and anchor placement to the
        # mirror. They're plain widgets, so moving them is safe.
        self._ov_surface = m
        m.installEventFilter(self)      # its resizes re-place the bars
        for ov in (self.seek_overlay, self.ts_timeline, self._stats_overlay,
                   self.nextup_btn, self.local_buffer_bar):
            if ov is not None:
                ov.setParent(parent)
                ov.hide()
        # The sleep-timer badge follows into the pop-out too; flash it there if
        # a timer is armed (then it fades / stays pinned per the countdown).
        self.sleep_badge.setParent(parent)
        self.sleep_badge.hide()
        self.number_badge.setParent(parent)
        self.number_badge.hide()
        self._show_dock_placeholder(True)
        # Re-place once the reflow has settled: the caller still has to move
        # the control bar out and re-lock the height after this returns, and a
        # placeholder sized against the pre-reflow rect left a strip uncovered
        # (the intermittent "video over the control bar").
        QTimer.singleShot(0, lambda: self._show_dock_placeholder(True))
        self._reveal_sleep_badge()
        # Every fullscreen toggle tears this down and builds it again - a new
        # GL context and a fresh mpv render binding each time. Timed so a
        # stall at the transition can be attributed to the rebuild rather
        # than guessed at.
        log.info("VID mirror start took %.0fms (path=%s)",
                 (time.monotonic() - _t_start) * 1000,
                 "raster" if isinstance(m, _RasterMirror) else "gl")
        return m

    def _raster_mark_frame(self) -> None:
        """mpv produced a new frame: set the flag the 60 Hz tick consumes.
        This slot must stay this cheap - rendering directly from the update
        callback flooded the main thread (it fires more often than the video
        fps)."""
        self._raster_pending_frame = True

    def _tick_raster_mirror(self) -> None:
        """One raster-mirror frame: render mpv into an offscreen FBO in the
        DOCKED widget's GL context (the one that provably presents) and hand
        the readback to the pop-out's raster widget. The docked mpv/render
        context are never moved or freed."""
        m = self._mirror
        v = self.video
        if m is None or v.mpv is None or v._ctx is None:
            return
        try:
            ratio = float(m.devicePixelRatioF() or 1.0)
            w = max(2, int(m.width() * ratio))
            h = max(2, int(m.height() * ratio))
            # Cap the offscreen render (and so the readback) at 1080p: mpv
            # downscales on the GPU for free, while an uncapped maximized
            # pop-out meant a ~14 MB synchronous glReadPixels per frame - the
            # "lags deluxe when maximized". The raster widget scales the frame
            # up smoothly; for TV content the difference is invisible.
            scale = min(1.0, 1920 / w, 1080 / h)
            w = max(2, int(w * scale))
            h = max(2, int(h * scale))
            # A resize drops the whole pipeline (both buffers + the pending
            # frame) so no stale-size frame is ever shown.
            fbos = self._mirror_fbos
            if fbos[0] is not None and (fbos[0].width() != w
                                        or fbos[0].height() != h):
                self._mirror_pending = None
                self._mirror_fbos = fbos = [None, None]
                self._mirror_fbo_i = 0
            # Render only when mpv actually produced a new frame (live TV is
            # 25 fps against the 60 Hz tick; the frame_ready flag is set by
            # mpv's update callback) - but always collect a finished pending
            # frame.
            upd = getattr(self, "_raster_pending_frame", True)
            # Watchdog: the gate only opens when mpv's update callback fires.
            # If that signal is lost across a window transition - which is
            # what a fullscreen switch is - the mirror keeps ticking but
            # never renders again: the picture freezes on its last frame
            # while the audio plays on, until some window event forces a
            # repaint (the "tab out and back and it works" report). Force a
            # frame through after a quarter second of silence so it can heal
            # itself instead of waiting for the user.
            now_m = time.monotonic()
            if upd:
                self._raster_frame_at = now_m
            elif now_m - getattr(self, "_raster_frame_at", now_m) > 0.25:
                self._raster_frame_at = now_m
                self._raster_stalls = getattr(self, "_raster_stalls", 0) + 1
                if self._raster_stalls in (1, 10, 100):
                    log.info("VID raster mirror: no new frame for 250 ms - "
                             "forcing one (stall #%d)", self._raster_stalls)
                upd = True
            pend = self._mirror_pending
            # Heartbeat: this path only logged on a state CHANGE, so a mirror
            # that ticks but never produces a frame looked identical to one
            # that had stopped ticking altogether - which is exactly the
            # question a frozen pop-out asks. Report the gate itself: how often
            # we tick, how many of those ticks mpv had a new frame for, and how
            # many images actually reached the widget.
            self._raster_n = getattr(self, "_raster_n", 0) + 1
            if upd:
                self._raster_upd_n = getattr(self, "_raster_upd_n", 0) + 1
            if self._raster_n % 120 == 0:
                now_t = time.monotonic()
                since_t = now_t - getattr(self, "_raster_hb_at", now_t)
                self._raster_hb_at = now_t
                log.debug("VID raster tick #%d %.1f/s new-frame=%d shown=%d "
                          "size=%dx%d pending=%s",
                          self._raster_n,
                          (120 / since_t if since_t > 0 else 0.0),
                          getattr(self, "_raster_upd_n", 0),
                          getattr(self, "_raster_shown_n", 0),
                          w, h, pend is not None)
            if not upd and pend is None:
                return
            img = None
            v.makeCurrent()
            try:
                # 1) Read LAST tick's frame first: its GPU work finished
                #    during the ~40 ms since, so glReadPixels returns without
                #    stalling. (Reading AFTER submitting this tick's render
                #    would wait for that too - GL is in-order.) flipped=False
                #    skips toImage's mirror pass - which is why the render
                #    below drops mpv's flip_y too: upright needs the two
                #    toggles to MATCH (flip_y=False+flipped=True was inverted
                #    on hardware; True+True was upright).
                if pend is not None:
                    img = pend.toImage(False)
                    self._mirror_pending = None
                # 2) Submit this tick's render into the OTHER buffer; it is
                #    read on the next tick.
                if upd:
                    self._raster_pending_frame = False
                    i = self._mirror_fbo_i
                    if fbos[i] is None:
                        fbos[i] = QOpenGLFramebufferObject(w, h)
                    # block_for_target_time=False is THE fix for the pop-out
                    # lag: mpv_render_context_render() defaults to BLOCKING
                    # until the frame's target display time, so every tick
                    # stalled the GUI thread for most of a frame interval -
                    # independent of the pop-out's size (which is why capping
                    # the resolution never helped), and worst on 23.976 fps
                    # film against a 60 Hz tick. We present via a readback,
                    # not a vsync'd swap, so mpv must never pace us.
                    v._ctx.render(flip_y=False, block_for_target_time=False,
                                  opengl_fbo={
                                      "fbo": int(fbos[i].handle()),
                                      "w": w, "h": h})
                    # Keep reporting the present: dropping it degraded mpv's
                    # timing estimate and made the judder worse.
                    try:
                        v._ctx.report_swap()
                    except Exception:
                        pass
                    self._mirror_pending = fbos[i]
                    self._mirror_fbo_i = 1 - i
            finally:
                v.doneCurrent()
            if img is not None:
                self._raster_shown_n = getattr(self, "_raster_shown_n", 0) + 1
                m.set_image(img)   # painted stretched to the widget rect
            if getattr(self, "_raster_state", "") != "render":
                self._raster_state = "render"
                log.info("VID raster-mirror -> render (%dx%d dpr=%.1f)",
                         w, h, ratio)
        except Exception as e:
            if getattr(self, "_raster_state", "") != "fail":
                self._raster_state = "fail"
                log.info("VID raster-mirror -> FAIL: %s", e)

    def stop_mirror(self) -> None:
        """Tear the mirror down and hand the picture back to the docked
        surface. Safe to call when no mirror is active."""
        _t_stop = time.monotonic()
        t = getattr(self, "_mirror_timer", None)
        if t is not None:
            t.stop()
            self._mirror_timer = None
        # Raster mirror: stop feeding the new-frame flag.
        try:
            self.video.frame_ready.disconnect(self._raster_mark_frame)
        except (TypeError, RuntimeError):
            pass
        # Raster mirror: release the offscreen FBOs while the docked GL context
        # (their owner) is current. The render context itself was never moved.
        if any(getattr(self, "_mirror_fbos", [None, None])):
            try:
                self.video.makeCurrent()
                self._mirror_fbos = [None, None]
                self.video.doneCurrent()
            except Exception:
                self._mirror_fbos = [None, None]
        self._mirror_pending = None
        self.video._render_suspended = False
        # Overlays back onto the docked player.
        self._ov_surface = self.video
        for ov in (self.seek_overlay, self.ts_timeline, self._stats_overlay,
                   self.nextup_btn, self.local_buffer_bar):
            if ov is not None:
                ov.setParent(self)
                ov.hide()
        self.sleep_badge.setParent(self)
        self.sleep_badge.hide()
        self.number_badge.setParent(self)
        self.number_badge.hide()
        self._mirror = None
        self._show_dock_placeholder(False)
        # The control bar is back in the player by now (the caller re-adds it
        # before stop_mirror) - re-pin the docked height WITH the bar again.
        self._lock_video_box()
        self._relayout_controls()
        self.video.update()
        self._reveal_sleep_badge()
        log.info("VID mirror stop took %.0fms",
                 (time.monotonic() - _t_stop) * 1000)

    def reveal_pop_overlays(self) -> None:
        """macOS/Windows pop-out hover: bring back the auto-hidden control bar
        and flash the seek bar (VOD / catch-up) or the timeshift timeline,
        whichever applies. The mirror forwards its pointer movement here; the
        overlays are already reparented over it."""
        self._reveal_local_buffer()
        self._reveal_popout_bar()       # no-op unless auto-hide is on
        self._reveal_sleep_badge()      # no-op unless a sleep timer is armed
        if self._seek_mode == "timeline":
            self._show_ts_timeline()
        else:
            self._show_seek_overlay()   # a no-op on plain live

    def _show_dock_placeholder(self, on: bool) -> None:
        """Cover (not hide) the docked video with a 'playing in pop-out' panel
        while mirrored, so the detail pane doesn't show the same picture twice.
        Covering rather than hiding keeps the docked surface rendering, which
        is what keeps mpv's timing and the shared render context healthy."""
        ph = getattr(self, "_dock_ph", None)
        if on:
            if ph is None:
                ph = self._dock_ph = QPushButton(tr("popout_placeholder"), self)
                ph.setCursor(Qt.CursorShape.PointingHandCursor)
                ph.setStyleSheet(
                    "QPushButton { background:#000000; color:#B8B8C0;"
                    " border:none; font-size:13px; font-weight:600; }"
                    "QPushButton:hover { color:#FFFFFF; }")
                ph.clicked.connect(lambda: self.popout_requested.emit())
            # Cover the WHOLE player, not just the video rect: while mirrored
            # the control bar has moved to the pop-out, so the player shrinks
            # and the video stretches into the freed strip. Sizing to a rect
            # measured before that reflow left the (suspended, so no longer
            # repainting) GL surface visible there as a frozen frame - "video
            # rendered over the control bar".
            ph.setGeometry(self.rect())
            ph.show()
            ph.raise_()
        elif ph is not None:
            ph.hide()

    def set_popout_mode(self, enabled: bool) -> None:
        """Detached-window mode: the pop-out window drives the size, so release
        the docked fixed height and let the video fill the window."""
        log.info("VID set_popout_mode(%s): video=%dx%d ctx=%s mpv=%s",
                 enabled, self.video.width(), self.video.height(),
                 self.video._ctx is not None, self.video.mpv is not None)
        self._popout_mode = enabled
        if not enabled:
            # Back to docked: the bar is always visible there.
            self._popout_autohide = False
            self._popout_bar_timer.stop()
        self.bar.show()
        self.pop_btn.setToolTip(
            tr("tooltip_popout_exit") if enabled else tr("tooltip_popout"))
        self._lock_video_box()
        # Re-settle once the layout is final. A pop-out toggle reparents the
        # player, so right after it the video geometry isn't settled: the fixed
        # video box measured a not-yet-laid-out control bar (seek bar sat over
        # the toolbar on dock-back), and the timeshift timeline / seek bar,
        # placed against the stale geometry, sat in the middle of the video on
        # pop-out until a stray event nudged them. The deferred pass re-measures
        # and re-places against the settled geometry - both directions.
        QTimer.singleShot(0, self._settle_after_reparent)

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
            log.info("eof event: %s", redact_url(self.current_url))
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
        w.playback_error.connect(self._on_playback_error)
        w.eof_event.connect(self._on_mpv_eof)
        w.video_dbl_click.connect(self._on_video_dbl_click)
        w.video_mouse_press.connect(self._on_video_press)
        w.video_mouse_move.connect(self._on_video_move)
        w.video_mouse_release.connect(self._on_video_release)
        w.video_key_press.connect(self._on_seek_key_press)
        w.video_key_release.connect(self._on_seek_key_release)

    def _settle_after_reparent(self) -> None:
        """Re-lock the video box and re-place any visible overlay against the
        geometry once it has settled after a pop-out dock/undock reparent."""
        self._lock_video_box()
        if self.ts_timeline.isVisible():
            self._place_ts_timeline()
        if (self.seek_overlay is not None
                and self.seek_overlay.isVisible()):
            self._place_seek_overlay()
        if self.local_buffer_bar.isVisible():
            self._place_local_buffer()
        # macOS keeps the GL context across the reparent, so the widget's
        # backing framebuffer can stay bound to the old window and show a
        # frozen frame. A 1 px resize and back rebuilds it. Pure widget
        # geometry - mpv and the render context are never touched.
        v = self.video
        sz = v.size()
        if sz.isValid() and sz.height() > 1:
            v.resize(sz.width(), sz.height() - 1)
            v.resize(sz)
        v.update()
        # The reparent leaves a STALE snapshot of the whole player composition
        # on the new window (proven by "double stats for nerds" in the pop-out:
        # the stale copy plus the live overlay, and the picture frozen even
        # with no overlay at all). An async update()/resize can lose the race
        # with that stale layer. Force the host window to rebuild its entire
        # backing store SYNCHRONOUSLY, now - the one presentation-level lever
        # not yet tried. macOS only; mpv is untouched.
        if sys.platform == "darwin":
            win = self.window()
            if win is not None:
                win.repaint()
                log.info("VID host-window repaint (rebuild stale backing store)")

    def _in_popout(self) -> bool:
        """True whenever the control bar lives in a pop-out window - either the
        reparent path (Linux, `_popout_mode`) or the mirror path (macOS/Windows,
        a live `_mirror`). Docked, the bar is always visible."""
        return self._popout_mode or getattr(self, "_mirror", None) is not None

    def set_popout_autohide(self, enabled: bool) -> None:
        """When on, the pop-out control bar fades after a few seconds of no
        pointer activity and reappears on mouse movement over the video (like a
        video player). Off keeps it permanently visible."""
        self._popout_autohide = enabled
        self.bar.show()
        if enabled and self._in_popout():
            self._popout_bar_timer.start()
        else:
            self._popout_bar_timer.stop()

    def _hide_popout_bar(self) -> None:
        if self._in_popout() and self._popout_autohide and not self._fs_ui:
            if self.bar.underMouse() or self.local_buffer_seek.dragging:
                self._popout_bar_timer.start()
                return
            self.bar.hide()

    def _reveal_popout_bar(self) -> None:
        if self._in_popout() and self._popout_autohide:
            self.bar.show()
            self._popout_bar_timer.start()

    # -- docked hover scrubber -------------------------------------------------

    def _show_seek_overlay(self) -> None:
        # No VOD seek overlay for plain live (can't seek) or timeshift-edge
        # (the live timeline is the control) - avoids a second, useless bar.
        if self._seek_mode in ("live", "timeline"):
            return
        # Already up - the pointer is simply still moving. Only re-arm the
        # fade: re-placing (a layout pass) and re-raising it on every pointer
        # event repainted the video underneath hundreds of times a second.
        if not self.seek_overlay.isVisible():
            for w in (self.back_btn, self.fwd_btn, self.seek, self.time_lbl):
                w.show()
            self._place_seek_overlay()
            self.seek_overlay.show()
            self.seek_overlay.raise_()
        self._seek_overlay_timer.start()

    def _hide_seek_overlay(self, force: bool = False) -> None:
        if not force and self.seek.dragging:
            self._seek_overlay_timer.start()
            return
        self.seek_overlay.hide()

    def _place_seek_overlay(self) -> None:
        margin = 10
        vg = self._ov_surface.geometry()
        self.seek_overlay.setFixedWidth(max(180, vg.width() - 2 * margin))
        self.seek_overlay.adjustSize()
        self.seek_overlay.move(
            vg.x() + margin,
            vg.y() + vg.height() - self.seek_overlay.height() - margin)

    # -- overlay ---------------------------------------------------------------

    def set_live_badge(self, kind: str | None) -> None:
        """Show a top-left 'not live' pill when watching the catch-up archive or
        while a live stream is paused (behind the live edge). Anything else -
        including the live edge itself - hides it (no permanent LIVE tag)."""
        b = self.live_badge
        if kind != "timeshift":
            b.hide()
            return
        b.setText("⧗ " + tr("player_timeshift"))
        b.setStyleSheet(
            "background: rgba(0,0,0,150); color: #FFFFFF;"
            "border-radius: 9px; padding: 3px 9px;"
            "font-size: 11px; font-weight: 700;")
        b.adjustSize()
        tl = self.video.geometry().topLeft()
        b.move(tl.x() + 12, tl.y() + 12)
        b.show()
        b.raise_()

    def set_seek_mode(self, mode: str) -> None:
        """Pick which seek UI this stream uses (see _seek_mode). Hides the VOD
        seek overlay for live/timeline so only one bar is ever shown."""
        self._seek_mode = mode
        if mode != "timeline":
            self.exit_timeshift()
        if mode != "program":
            self._program_window = 0.0
            self._program_base = 0.0
        if mode in ("live", "timeline"):
            self._hide_seek_ui()
        elif mode == "program" and self._fs_ui:
            # A program-scrub re-load runs play() -> _hide_seek_ui(); re-show the
            # fullscreen scrubber right away so the full-width bar isn't left as
            # an empty gap until the next position poll.
            for w in self._seek_widgets():
                w.setVisible(True)

    def set_program_window(self, secs: float, base: float = 0.0) -> None:
        """In 'program' mode, present the seek bar as the whole picked programme
        (*secs* long), with the currently loaded archive segment beginning
        *base* seconds into it. The provider's archive stream usually runs on
        past the programme to the live edge - and in-stream seeking on it snaps
        to live - so the bar is a *virtual* timeline: scrubbing it re-loads the
        archive at the chosen offset (program_seek) instead of seeking mpv."""
        self._program_window = max(0.0, float(secs or 0.0))
        self._program_base = max(0.0, float(base or 0.0))

    def set_overlay_info(self, text: str) -> None:
        self._overlay_text = text or ""
        if self._fs_ui and self.overlay.isVisible():
            self.overlay.setText(self._overlay_text)
            self._place_overlay()

    # How close to the end the "Next episode" card appears, in seconds.
    # The user's choice, from Settings > Playback; 0 turns the card off.
    #
    # Not computed here any more. It was a fixed minute, then six percent of
    # the runtime capped at three minutes, and the answer both times was
    # "still too late" - because where the credits start is a property of
    # the show, not of its length, and nothing in the file says where.
    NEXTUP_DEFAULT_SECS = 180
    # How far the card sits from the corner of the picture. Hard into the
    # corner reads as a system control rather than an offer.
    NEXTUP_INSET = 28

    def _nextup_window(self, dur: float) -> float:
        """Seconds before the end at which the card appears, or 0 for off."""
        try:
            v = int(self._settings.value(
                "nextup_lead_secs", self.NEXTUP_DEFAULT_SECS)) \
                if self._settings else self.NEXTUP_DEFAULT_SECS
        except (TypeError, ValueError):
            v = self.NEXTUP_DEFAULT_SECS
        # Never past the start of what it is offering to skip.
        return max(0, min(v, dur * 0.5)) if dur > 0 else 0

    def set_next_available(self, available: bool) -> None:
        """Show/hide the 'next episode' button in both control bars. The app
        calls this when it starts a stream, based on whether a next episode is
        queued in the current series."""
        self._next_available = bool(available)
        for b in (getattr(self, "nextep_btn", None),
                  getattr(self, "fs_nextep_btn", None)):
            if b is not None:
                b.setVisible(available)
        if not available:
            self._hide_nextup()

    def _on_nextup_clicked(self) -> None:
        self._hide_nextup()
        self.next_episode.emit()

    def _hide_nextup(self) -> None:
        b = getattr(self, "nextup_btn", None)
        if b is not None and not b.isHidden():
            b.hide()

    def _update_nextup(self, pos: float, dur: float) -> None:
        """Show the card over the closing stretch of an episode.

        Only where there IS a next episode and the length is known, so a
        live channel (no duration) and the last episode of a series never
        show it. Seeking back out of the window takes it away again."""
        b = getattr(self, "nextup_btn", None)
        if b is None:
            return
        left = (dur - pos) if (dur and dur > 0 and pos >= 0) else -1
        if not (self._next_available
                and 0 < left <= self._nextup_window(dur)):
            self._hide_nextup()
            return
        if b.isHidden():
            b.adjustSize()
            b.show()
        b.raise_()
        self._place_nextup()

    def _place_nextup(self) -> None:
        """Bottom-right of whatever surface is showing the picture - the
        docked video, or the mirror while popped out.

        Mapped rather than read off geometry(): the card and the surface do
        not always share a parent (docked the card sits on the player, in
        the pop-out on the pop-out window), and geometry() is relative to
        the SURFACE's parent. Reading it raw put the card adrift in the
        middle of the picture."""
        b = self.nextup_btn
        surf = self._ov_surface
        b.adjustSize()
        par = b.parentWidget()
        try:
            tl = surf.mapTo(par, QPoint(0, 0)) if par is not None \
                else surf.geometry().topLeft()
        except (RuntimeError, TypeError):
            tl = surf.geometry().topLeft()
        # Clear whatever floats over the bottom of the picture, and nothing
        # more. Only fullscreen has anything there - the control bar - and the
        # flat 88 px was sized for that case, so docked and popped out the card
        # rode a bar's height too high over empty picture. The bar's sizeHint
        # rather than its height, because it auto-hides after a few idle
        # seconds and the card must not hop down when it goes.
        gap = self.NEXTUP_INSET
        if self._fs_ui:
            gap += self.fs_controls.sizeHint().height() + 12
        b.move(tl.x() + surf.width() - b.width() - self.NEXTUP_INSET,
               tl.y() + surf.height() - b.height() - gap)

    def set_fullscreen_ui(self, fullscreen: bool) -> None:
        log.info("VID set_fullscreen_ui(%s): video=%dx%d ctx=%s popout=%s",
                 fullscreen, self.video.width(), self.video.height(),
                 self.video._ctx is not None, self._popout_mode)
        self._fs_ui = fullscreen
        self._sync_bar_switch()     # fullscreen has the timeline alone
        self.bar.setVisible(not fullscreen)
        if fullscreen:
            self._hide_seek_overlay(force=True)
            self._set_local_buffer_visible(False)   # fullscreen has its own
            self._lock_video_box()
            self._show_overlay()
            # Focus the surface that is actually on screen: with a mirror up
            # the docked widget is behind a placeholder in another window,
            # so focusing it sent every key press to the wrong window.
            (getattr(self, "_mirror", None) or self.video).setFocus(
                Qt.FocusReason.OtherFocusReason)
        else:
            self._hide_fs_ui()
            self._overlay_timer.stop()
            for w in self._cursor_surfaces():
                w.unsetCursor()
            self._mac_show_cursor()
            # The docked height is now a constant, not derived from
            # self.height(), so restoring it doesn't depend on the window
            # having finished its async resize back from fullscreen - it
            # can't lock in a stale size. Re-apply once now and once on the
            # next tick to be robust against the transition ordering.
            self._lock_video_box()
            QTimer.singleShot(0, self._lock_video_box)

    def _cursor_surfaces(self) -> list:
        """Every surface the pointer can actually be over.

        In fullscreen on macOS the picture lives in the MIRROR, in its own
        window - so blanking the cursor on the player and the docked video
        left the pointer sitting visibly over the video, which is what it
        was doing there."""
        out = [self, self.video]
        m = getattr(self, "_mirror", None)
        if m is not None:
            out.append(m)
        return out

    def _hide_fs_ui(self) -> None:
        # Never take the controls and cursor away under an open menu or
        # dialog (subtitle/audio pickers, the right-click menu): the user is
        # mid-choice, and blanking the cursor there strands them without a
        # pointer. Re-arm and try again once the popup is gone.
        if self._fs_ui and (QApplication.activePopupWidget() is not None
                            or QApplication.activeModalWidget() is not None):
            self._overlay_timer.start()
            return
        self.overlay.hide()
        self.fs_controls.hide()
        if self._fs_ui:
            for w in self._cursor_surfaces():
                w.setCursor(Qt.CursorShape.BlankCursor)

    def _show_overlay(self) -> None:
        for w in self._cursor_surfaces():
            w.unsetCursor()
        if self._overlay_text:
            self.overlay.setText(self._overlay_text)
            self.overlay.show()
        self.fs_controls.show()
        self._place_overlay()
        self.overlay.raise_()
        self.fs_controls.raise_()
        self._overlay_timer.start()

    def _place_overlay(self) -> None:
        margin = 24
        # Size the control bar to the stream's seek MODE, not the seek widget's
        # momentary visibility: a program-mode scrub re-loads the archive, which
        # briefly hides the scrubber (play() calls _hide_seek_ui), and keying off
        # that made the bar collapse to compact and re-stretch on every seek.
        # A seek bar belongs to vod/program -> full width; live/timeline -> hug.
        if (self._seek_mode in ("vod", "program")
                or (self._local_buffer_enabled and self._seek_mode == "live"
                    and self._local_fs_window is not None)):
            controls_w = self.width() - 2 * margin
        else:
            controls_w = self.fs_controls.sizeHint().width()
        self.fs_controls.setFixedWidth(max(80, controls_w))
        self.fs_controls.adjustSize()
        self.fs_controls.move(
            self.width() - self.fs_controls.width() - margin,
            self.height() - self.fs_controls.height() - margin)
        # Hug the text instead of always reserving a wide box: measure the
        # widest line and only wrap (up to the cap) when it's genuinely long.
        fm = self.overlay.fontMetrics()
        lines = (self.overlay.text() or "").split("\n")
        text_w = max((fm.horizontalAdvance(ln) for ln in lines), default=0)
        cap = min(self.width() - 2 * margin, 640)
        self.overlay.setFixedWidth(max(120, min(text_w + 34, cap)))
        self.overlay.adjustSize()
        # Sit just above the control bar - and above the timeshift timeline too
        # when it's showing, so the info text never lands on top of the bar.
        bottom = self.height() - self.fs_controls.height() - margin - 8
        if self.ts_timeline.isVisible():
            bottom -= self.ts_timeline.height() + 12
        self.overlay.move(margin, bottom - self.overlay.height())

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
            self._sub_defaults(sub_mode, live, set_opt)
            if channel_prefs.own_subtitles(own):
                self._own_subtitles(own, live, set_opt)
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

    def _sub_defaults(self, mode: str, live: bool, set_opt) -> None:
        """The subtitle track a stream opens with, and whether it shows.

        mpv answers a subtitle track chosen during playback with a refresh
        seek: it drops what it has buffered and asks for the stream again
        from a little earlier - on a film from a slow panel, five seconds or
        more of frozen picture each time subtitles were turned on. So a film
        or an episode opens with a track chosen even when none is to show,
        hidden: it is read along with the rest, and turning subtitles on is
        only showing it. Whether the setting shows it is decided once the
        tracks are known (_apply_sub_rule). A live channel cannot be sought
        - choosing a track later costs nothing there - and opens as before.
        """
        m = self.video.mpv
        s = self._settings
        langs = [x for x in (s.value("sub_lang", "") or "",
                             s.value("sub_lang2", "") or "") if x]
        self._sub_rule = None
        if live or mode == "forced":
            set_opt("subs-fallback", "default")      # mpv's own default
            set_opt("sub-visibility", True)
            if mode == "off":
                set_opt("sid", "no")
                _set_forced_only(m, False)
                return
            set_opt("sid", "auto")
            # A primary + optional fallback language: mpv's slang takes a
            # comma-separated priority list, so "swe,eng" means prefer
            # Swedish subs but fall back to English when absent.
            set_opt("slang", ",".join(langs) if mode == "lang" else "")
            _set_forced_only(m, mode == "forced")
            return
        set_opt("sid", "auto")
        # The track picked is the one most likely to be turned on: the
        # languages asked for, then the one turned on last, then the app's
        # own. Picking another later is the slow part. In "auto" a track
        # the file flags still shows (_apply_sub_rule).
        set_opt("slang", ",".join(self._preferred_sub_langs(langs)))
        set_opt("subs-fallback", "yes")              # some track, always
        _set_forced_only(m, False)
        set_opt("sub-visibility", False)
        if mode != "off":
            self._sub_rule = (mode, [x.lower() for x in langs])

    def _own_subtitles(self, own: dict, live: bool, set_opt) -> None:
        """Nerd: a channel's own subtitle choice, over what _sub_defaults
        just chose - "off", or a language first in line (the app's own
        after it). Its catch-up opens like a film: a track chosen, hidden,
        shown once the tracks say it is in that language (_apply_sub_rule)."""
        s = self._settings
        langs = [x for x in (s.value("sub_lang", "") or "",
                             s.value("sub_lang2", "") or "") if x]
        if live:
            also = langs if s.value("sub_mode", "auto") == "lang" else []
        else:
            also = self._preferred_sub_langs(langs)
        for prop, value in channel_prefs.subtitle_props(own, live, also):
            set_opt(prop, value)
        _set_forced_only(self.video.mpv, False)
        self._sub_rule = channel_prefs.subtitle_rule(own, live)

    def _preferred_sub_langs(self, langs: list[str]) -> list[str]:
        """The languages a film's hidden track is chosen by: those asked
        for in Settings, the one turned on last, the app's language."""
        s = self._settings
        last = (s.value("sub_last_lang", "") or "") if s else ""
        out: list[str] = []
        for value in (*langs, last,
                      _SUB_LANG_TAGS.get(current_language(), "")):
            for tag in str(value).split(","):
                tag = tag.strip().lower()
                if tag and tag not in out:
                    out.append(tag)
        return out

    def _apply_sub_rule(self, m) -> None:
        """The stream's tracks are known: show the track chosen hidden at
        the start if the setting would have chosen it itself - one the file
        flags default or forced, or (by language) one in a language asked
        for. The same tracks mpv showed before tracks were chosen hidden.

        In "auto" the hidden track is picked by language, so a track the
        file flags may be another one: that one is taken, once, at the very
        start - what mpv itself picked before."""
        try:
            tracks = m.track_list or []
        except Exception:
            return
        if not tracks:
            return                        # not loaded yet: the next poll
        mode, langs = self._sub_rule
        self._sub_rule = None
        subs = [t for t in tracks if t.get("type") == "sub"]
        sel = next((t for t in subs if t.get("selected")), None)
        if mode == "auto":
            flagged = (next((t for t in subs if t.get("default")), None)
                       or next((t for t in subs if t.get("forced")), None))
            if flagged is None:
                return
            try:
                if sel is None or sel.get("id") != flagged.get("id"):
                    m["sid"] = flagged.get("id")
                m["sub-visibility"] = True
            except Exception:
                pass
            return
        if sel is None:
            return
        lang = (sel.get("lang") or "").lower()
        if (sel.get("default") or sel.get("forced")
                or (mode == "lang" and lang in langs)):
            try:
                m["sub-visibility"] = True
            except Exception:
                pass

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

    # -- music: visualiser + equaliser ---------------------------------------

    # Ten ISO bands, the shape every graphic equaliser uses.
    EQ_BANDS = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
    EQ_PRESETS = {
        "flat":   (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        "bass":   (6, 5, 4, 2, 0, 0, 0, 0, 0, 0),
        "treble": (0, 0, 0, 0, 0, 1, 3, 4, 5, 6),
        "vocal":  (-2, -1, 0, 2, 4, 4, 3, 1, 0, -1),
        "rock":   (4, 3, 1, -1, -2, 0, 2, 3, 4, 4),
    }
    VIS_STYLES = ("bars", "spectrum", "wave")

    def _vis_filter(self, style: str, w: int = 640, h: int = 360) -> str:
        """An mpv lavfi-complex graph that turns the audio into a picture -
        what a music player shows instead of a black rectangle."""
        if style == "spectrum":
            draw = (f"showspectrum=s={w}x{h}:slide=scroll:mode=combined:"
                    "color=intensity:scale=cbrt:legend=0")
        elif style == "wave":
            draw = (f"showwaves=s={w}x{h}:mode=cline:rate=30:colors=white")
        else:
            draw = (f"showcqt=s={w}x{h}:fps=30:count=2:bar_g=4:sono_g=4:"
                    "axis=0:sono_h=0")
        return f"[aid1]asplit=2[ao][a];[a]{draw}[vo]"

    # Filled in by the window: reading/writing the user's choices lives
    # there, the drawing lives here.
    open_equaliser = None
    vis_state = None          # () -> (on, style)
    vis_choose = None         # (on, style) -> None

    def _vis_on_get(self) -> bool:
        try:
            return bool(self.vis_state and self.vis_state()[0])
        except Exception:
            return False

    def _vis_style_get(self) -> str:
        try:
            return (self.vis_state and self.vis_state()[1]) or "bars"
        except Exception:
            return "bars"

    def _vis_pick(self, style: str | None) -> None:
        if self.vis_choose:
            self.vis_choose(style is not None, style or self._vis_style_get())

    def set_visualiser(self, on: bool, style: str = "bars") -> None:
        """Show/hide the audio visualiser. A file with real video never gets
        one; mpv builds the graph from the audio track itself.

        The filter graph is only ever touched when it actually has to
        change: rewriting lavfi-complex on every ordinary video play is a
        needless poke at mpv's pipeline, and the video path must stay
        exactly as it was before any of this existed."""
        m = self.video.mpv
        if m is None:
            return
        want = (bool(on), style if on else "")
        if getattr(self, "_vis_now", (False, "")) == want:
            return
        try:
            if on:
                m["lavfi-complex"] = self._vis_filter(style)
            else:
                m["lavfi-complex"] = ""
            self._vis_now = want
        except Exception as e:
            log.debug("visualiser %s failed: %s", "on" if on else "off", e)

    def set_equaliser(self, gains, enabled: bool = True) -> None:
        """Apply a ten-band equaliser (gains in dB, -12..+12). Disabled or
        all-flat means no filter at all, so the untouched path stays
        bit-exact."""
        m = self.video.mpv
        if m is None:
            return
        try:
            gains = [max(-12.0, min(12.0, float(g))) for g in gains][:10]
            gains += [0.0] * (10 - len(gains))
        except (TypeError, ValueError):
            return
        try:
            want = tuple(gains) if enabled else ()
            if getattr(self, "_eq_now", None) == want:
                return                 # already applied: leave mpv alone
            self._eq_now = want
            if not enabled or not any(abs(g) > 0.05 for g in gains):
                m["af"] = ""
                return
            parts = [f"equalizer=f={f}:width_type=o:width=1:g={g:.1f}"
                     for f, g in zip(self.EQ_BANDS, gains, strict=True)]
            m["af"] = "lavfi=[" + ",".join(parts) + "]"
        except Exception as e:
            log.debug("equaliser failed: %s", e)

    def set_track_prefs(self, aid=None, sid=None) -> None:
        """Arm one-shot audio/subtitle track ids for the next play() - the
        window passes a title's remembered choices here right before
        replaying/resuming it. Cleared by play() whether applied or not."""
        self._pref_aid = aid
        self._pref_sid = sid

    def set_channel(self, ckey: str | None) -> None:
        """Arm which channel the next play() is - its source's id and its
        stream's id (core/channel_prefs.channel_key); None for anything else.
        Nerd plays it with the settings of its own and keeps its audio delay
        there. Consumed by play(), whether it is a channel or not."""
        self._chan_armed = ckey or None

    def _prefs_store(self):
        if self._chan_store is None:
            self._chan_store = channel_prefs.ChannelPrefs(self._settings)
        return self._chan_store

    def _channel_own(self, chan: str | None, stream: str | None = None) -> dict:
        """The settings of its own channel *chan* plays with: {} for none,
        for anything that is no channel, and while Nerd is off (kept, not
        applied). A delay kept the old way for *stream* moves in. Never
        raises: a bad stored value must not stop a channel from playing."""
        if not chan or self._settings is None:
            return {}
        try:
            if not channel_prefs.enabled():
                return {}
            return self._prefs_store().get(
                chan, legacy=_delay_key(stream) if stream else None)
        except Exception as e:
            log.debug("channel settings of %s not read: %s", chan, e)
            return {}

    def channel_prefs_changed(self, chan: str | None) -> None:
        """The channel playing now got new settings of its own (its dialog):
        put in force as a Settings change is - the picture and the delay at
        once; the tracks it plays may wait for its next start."""
        m = self.video.mpv
        if (not chan or chan != self._chan_key or m is None
                or self.current_url is None):
            return
        self._chan_own = self._channel_own(chan)
        self.apply_default_options()
        if self._delay_chan == chan:
            try:
                value = channel_prefs.delay(self._chan_own.get("adelay"))
                m["audio-delay"] = value
                self._delay_set = bool(value)
            except Exception as e:
                log.debug("audio delay of %s not set: %s", chan, e)

    def set_sub_files(self, paths: list[str]) -> None:
        """Arm subtitle files for the next play() - one fetched or picked
        for this title before comes back with it. One-shot, like the track
        choices."""
        self._pref_sub_files = [str(p) for p in paths if p]

    def add_subtitle(self, path: str, title: str = "", lang: str = "") -> bool:
        """Load a subtitle file into what is playing and show it."""
        m = self.video.mpv
        if m is None or not self.current_url:
            return False
        try:
            m.command("sub-add", _subtitle_to_load(str(path)), "select",
                      title or "", lang or "")
        except Exception as e:
            log.warning("sub-add %s failed: %s", path, e)
            return False
        # Loaded: from here on it is only about showing it, which must not
        # turn a loaded subtitle into a failure.
        _set_forced_only(m, False)
        try:
            m["sub-visibility"] = True
        except Exception as e:
            log.debug("sub-visibility after sub-add failed: %s", e)
        self.subtitle_added.emit(str(path))
        return True

    def _load_subtitle_file(self) -> None:
        from PyQt6.QtWidgets import QFileDialog
        path, _flt = QFileDialog.getOpenFileName(
            self.window(), tr("subs_pick_title"), "",
            tr("opt_subtitles") + " (*.srt *.ass *.ssa *.vtt *.sub)")
        if path:
            self.add_subtitle(path)

    def play(self, url: str, title: str, start: float = 0.0,
             *, local_buffer: bool = False,
             buffer_key: str | None = None) -> bool:
        # local_buffer: a live channel, which gets the rewind buffer unless
        # Settings turned it off.
        live = local_buffer
        # Nerd: the channel this is (set_channel, one-shot) and the settings
        # of its own it plays with - none for anything else.
        chan, self._chan_armed = self._chan_armed, None
        self._chan_key = chan
        self._chan_own = self._channel_own(chan, buffer_key or url)
        local_buffer = local_buffer and self._live_rewind_minutes() > 0
        # A session of the app's own buffer ends here: its provider
        # connection goes before the next stream opens one. Then, where
        # Settings asks for it, a live channel gets one: mpv plays its local
        # server, and the session reads the provider.
        self._end_dvr()
        # buffer_key: the channel, whatever its address - a portal hands
        # out a new one each time - so its history is found again.
        source = self._dvr_start(url, buffer_key) if local_buffer else None
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
            if self.video.mpv is None:
                raise RuntimeError("OpenGL context not ready")
            m = self.video.mpv
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
                m["cache-on-disk"] = ("yes" if mpv_rewind
                                      and self._live_rewind_disk() else "no")
                m["demuxer-max-back-bytes"] = self._back_buffer(mpv_rewind)
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
            # Nerd: user agent, headers and proxy for this source (nothing
            # while Nerd is off and never was; the defaults back once it was).
            try:
                from ..core import nerd
                self._nerd_applied = nerd.apply_stream(
                    m, nerd.source(), getattr(self, "_nerd_applied", False),
                    url=url)
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
                log.debug("sub-files %r failed: %s", sub_files, e)
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
        except Exception as e:
            log.error("Embedded playback failed: %s: %s", type(e).__name__, e)
            self.current_url = None
            self._end_dvr()
            return False

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
        # its own native window so it composites above the GL surface.
        if sys.platform == "darwin":
            menu.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
            menu.winId()
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def populate_options_menu(self, menu) -> None:
        """Fill *menu* with the full playback options (audio/subtitle tracks,
        delay, aspect, video filters, buffer, sleep timer, stats). Shared by the
        options button and the video right-click menu."""
        m = self.video.mpv

        def tracks(kind):
            try:
                return [t for t in (m.track_list or [])
                        if t.get("type") == kind]
            except Exception:
                return []

        def track_label(t):
            parts = [t.get("lang") or "", t.get("title") or ""]
            label = " ".join(p for p in parts if p).strip()
            return label or tr("player_track_n", n=t.get("id"))

        audio = menu.addMenu(tr("opt_audio_track"))
        for t in (tracks("audio") if m else []):
            act = audio.addAction(track_label(t))
            act.setCheckable(True)
            act.setChecked(bool(t.get("selected")))
            act.triggered.connect(
                lambda _c, tid=t.get("id"): self._pick_track("aid", tid))
        if audio.isEmpty():
            audio.addAction(tr("opt_no_audio_tracks")).setEnabled(False)

        subs = menu.addMenu(tr("opt_subtitles"))
        off = subs.addAction(tr("opt_off"))
        off.setCheckable(True)
        sub_tracks = tracks("sub") if m else []
        # A track chosen hidden (_sub_defaults) is off, as far as anyone
        # watching can tell.
        try:
            shown = bool(m["sub-visibility"]) if m else False
        except Exception:
            shown = True
        off.setChecked(not (shown and any(t.get("selected")
                                          for t in sub_tracks)))
        off.triggered.connect(lambda _c: self._pick_track("sid", "no"))
        for t in sub_tracks:
            act = subs.addAction(track_label(t))
            act.setCheckable(True)
            act.setChecked(bool(t.get("selected")) and shown)
            act.triggered.connect(
                lambda _c, tid=t.get("id"): self._pick_track("sid", tid))
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
        for label, action in (self.extra_options or []):
            menu.addAction(label(), action)

    def _start_sleep_timer(self, minutes: int) -> None:
        """Stop playback after *minutes* (0 cancels a running timer)."""
        if minutes <= 0:
            if self._sleep_timer.isActive():
                self._sleep_timer.stop()
                self._sleep_tick.stop()
                self._sleep_badge_timer.stop()
                self.sleep_badge.hide()
                self.set_overlay_info(tr("sleep_cancelled"))
            return
        self._sleep_timer.start(minutes * 60 * 1000)
        self._sleep_deadline = time.monotonic() + minutes * 60
        self._sleep_tick.start()
        self._reveal_sleep_badge()
        self.set_overlay_info(tr("sleep_set", n=minutes))

    def _reveal_sleep_badge(self) -> None:
        """Flash the countdown pill on activity and re-arm its idle fade. In
        the final SLEEP_PIN_SECS the tick keeps it pinned regardless."""
        if not self._sleep_timer.isActive():
            return
        # Only when it isn't already up: the countdown's own 1 Hz tick keeps
        # the text current, so a pointer event has nothing to add (and this
        # runs on every one of them).
        if not self.sleep_badge.isVisible():
            self._update_sleep_badge(show=True)
        self._sleep_badge_timer.start()

    def _maybe_hide_sleep_badge(self) -> None:
        # Fade like the controls, but never inside the pinned final window.
        if self._sleep_deadline - time.monotonic() > self.SLEEP_PIN_SECS:
            self.sleep_badge.hide()

    def _update_sleep_badge(self, show: bool = False) -> None:
        """Refresh the countdown pill; called every second while armed. Only
        (re)shows it when asked (a reveal), when already visible, or when
        pinned - so an idle fade stays faded between ticks."""
        b = self.sleep_badge
        remaining = int(round(self._sleep_deadline - time.monotonic()))
        if remaining <= 0 or not self._sleep_timer.isActive():
            self._sleep_tick.stop()
            b.hide()
            return
        pinned = remaining <= self.SLEEP_PIN_SECS
        h, rem = divmod(remaining, 3600)
        m, s = divmod(rem, 60)
        txt = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        b.setText(f"\U0001F319 {txt}")
        # A warmer pill in the pinned window makes the imminent stop obvious.
        bg = "rgba(200,60,50,220)" if pinned else "rgba(0,0,0,150)"
        b.setStyleSheet(
            f"background: {bg}; color: #FFFFFF;"
            "border-radius: 9px; padding: 3px 9px;"
            "font-size: 11px; font-weight: 700;")
        b.adjustSize()
        self._place_sleep_badge()
        if show or pinned or b.isVisible():
            b.show()
            b.raise_()

    def _place_sleep_badge(self) -> None:
        # Anchor to the overlay surface (the docked video, or the mirror while
        # popped out) so the badge rides along into the pop-out window.
        g = self._ov_surface.geometry()
        b = self.sleep_badge
        b.move(g.right() - b.width() - 12, g.top() + 12)

    def show_number_entry(self, text: str, ms: int = 4000) -> None:
        """Show *text* (the digits typed so far, or why nothing played) in
        the picture's corner for *ms*; empty hides it. Nothing else changes:
        the window decides what plays."""
        b = self.number_badge
        if not text:
            self._number_badge_timer.stop()
            b.hide()
            return
        b.setText(text)
        b.setStyleSheet(
            "QLabel#NumberEntry { background: rgba(0,0,0,170); color: white;"
            f" border: 2px solid {P['accent']}; border-radius: 10px;"
            " padding: 4px 14px; font-size: 22px; font-weight: 700; }")
        b.adjustSize()
        self._place_number_badge()
        b.show()
        b.raise_()
        self._number_badge_timer.start(max(500, ms))

    def _place_number_badge(self) -> None:
        # Under the sleep-timer pill when that one is up.
        g = self._ov_surface.geometry()
        b = self.number_badge
        top = g.top() + 12
        if self.sleep_badge.isVisible() and self.sleep_badge.parent() is b.parent():
            top = self.sleep_badge.geometry().bottom() + 8
        b.move(g.right() - b.width() - 12, top)

    def _ask_sleep_minutes(self) -> None:
        from PyQt6.QtWidgets import QInputDialog
        mins, ok = QInputDialog.getInt(
            self, tr("opt_sleep_timer"), tr("sleep_prompt"),
            60, 1, 1440, 5)
        if ok:
            self._start_sleep_timer(int(mins))

    def _on_sleep_elapsed(self) -> None:
        self._sleep_tick.stop()
        self._sleep_badge_timer.stop()
        self.sleep_badge.hide()
        self.set_overlay_info(tr("sleep_stopping"))
        self.stop()

    def _show_stats(self) -> None:
        if self._stats_overlay.isVisible():
            self._stats_overlay.hide()
            self._stats_timer.stop()
            return
        self._update_stats_text()
        self._stats_overlay.show()
        self._stats_overlay.raise_()
        self._place_stats()
        self._stats_timer.start()

    def _update_stats_text(self) -> None:
        m = self.video.mpv
        if m is None:
            self._stats_overlay.hide()
            self._stats_timer.stop()
            return

        def prop(name, fmt=str):
            # Read via attribute access (m.some_property) - the same form the
            # rest of the player uses successfully. Some libmpv builds don't
            # resolve the subscript getter for these hyphenated names, which
            # made every stat below Video/Audio read as "—".
            try:
                v = getattr(m, name.replace("-", "_"))
                return fmt(v) if v is not None else "—"
            except Exception:
                return "—"

        def track_info(kind):
            try:
                for t in (m.track_list or []):
                    if t.get("type") == kind and t.get("selected"):
                        codec = t.get("codec") or "?"
                        parts = [codec]
                        if kind == "video":
                            w = t.get("demux-w") or t.get("width")
                            h = t.get("demux-h") or t.get("height")
                            if w and h:
                                parts.append(f"{w}×{h}")
                            fps = t.get("demux-fps")
                            if fps:
                                parts.append(f"{fps:.1f} fps")
                        elif kind == "audio":
                            sr = t.get("demux-samplerate")
                            ch = t.get("demux-channel-count") or \
                                t.get("audio-channels")
                            lang = t.get("lang")
                            if sr:
                                parts.append(f"{sr} Hz")
                            if ch:
                                parts.append(f"{ch}ch")
                            if lang:
                                parts.append(lang)
                        return " / ".join(parts)
            except Exception:
                pass
            return "—"

        hwdec = prop("hwdec-current")
        if not hwdec or hwdec == "—":
            hwdec = prop("hwdec")
        if not hwdec or hwdec in ("—", "no"):
            hwdec = "software"
        dropped = prop("frame-drop-count", lambda v: str(int(v)))
        vo_drops = prop("vo-drop-frame-count", lambda v: str(int(v)))
        if dropped != "—" and vo_drops != "—":
            dropped = f"{dropped} / {vo_drops} vo"
        # Display-sync timing faults: with video-sync=display-resample a
        # periodic present-timing correction shows up here (not as a "drop"),
        # so these two pinpoint a render/vsync-side hitch vs a decode-side one.
        mistimed = prop("mistimed-frame-count", lambda v: str(int(v)))
        delayed = prop("vo-delayed-frame-count", lambda v: str(int(v)))

        stream = [
            ("Video", track_info('video')),
            ("Audio", track_info('audio')),
            ("HW dec", hwdec),
            ("A/V sync", prop('avsync', lambda v: f'{v:.3f} s')),
            ("Dropped", dropped),
            ("Timing", f"{mistimed} mistimed / {delayed} delayed"),
            ("FPS", prop('estimated-vf-fps', lambda v: f'{v:.1f}')),
            ("Bitrate", prop('video-bitrate', lambda v: f'{v / 1000:.0f} kbps' if v else '—')),
            ("Cache", prop('demuxer-cache-duration', lambda v: f'{v:.1f} s')),
            ("Net", prop('cache-speed', lambda v: f'{v / 1024:.0f} KB/s' if v else '0 KB/s')),
            ("Format", prop('file-format')),
            ("Protocol", prop('stream-path', lambda v: v.split('://')[0] if '://' in str(v) else str(v))),
        ]
        self._stats_groups = [stream, self._nerd_stats(prop)]
        self._place_stats()

    def _remember_delay(self, value: float) -> None:
        """Nerd: the audio delay chosen, kept for the channel playing."""
        chan = getattr(self, "_delay_chan", None)
        if chan and self._settings is not None:
            try:
                self._chan_own = self._prefs_store().set_delay(chan, value)
                self._delay_set = bool(channel_prefs.delay(value))
            except Exception as e:
                log.debug("audio delay of %s not kept: %s", chan, e)
            return
        key = getattr(self, "_delay_key", None)
        if key and self._settings is not None:
            self._delay_set = abs(value) >= 0.001
            if abs(value) < 0.001:
                self._settings.remove(key)
            else:
                self._settings.setValue(key, f"{value:g}")

    def _nerd_stats(self, prop) -> list[str]:
        """More for a nerd, with Nerd on: the picture's colour and scan,
        what the audio goes out as, the source and what the network says
        it is."""
        from ..core import nerd
        n = nerd.current()
        if not (n.on and n.flag("nerd_overlay_more", True)):
            return []

        def vp(key: str) -> str:
            try:
                return str((self.video.mpv.video_params or {}).get(key) or "—")
            except Exception:
                return "—"
        src = nerd.source() or {}
        out = [
            ("Colour", f"{vp('colormatrix')} / {vp('primaries')} / {vp('gamma')}"),
            ("Pixels", f"{vp('pixelformat')} · {vp('w')}×{vp('h')} · {aspect_name(vp('aspect'))}"),
            ("Deinterlace", prop('deinterlace', on_off)),
            ("Audio out", f"{prop('audio-codec-name')} · "
                          f"{prop('audio-out-params', lambda v: (v or {}).get('format', '—'))}"),
            ("Passthrough", n.get('nerd_spdif') or 'off'),
            ("Container", f"{prop('file-format')} · demuxer {prop('current-demuxer')}"),
            ("Source", src.get('_kind') or '—'),
            ("User agent", n.user_agent(src)),
            ("Proxy", n.proxy(src) and 'on' or 'off'),
        ]
        if n.flag("nerd_vpn_guard"):
            up = nerd.vpn_interfaces()
            out.append(("VPN", ', '.join(up) if up else 'none'))
        return out

    def _place_stats(self) -> None:
        """Lay the stats out for the room the picture has: the type grows
        with it (fullscreen reads from the sofa), a second column when one
        is taller than the picture, smaller type when even that is not
        enough. 8 px inside the surface's top-left (the docked video, or the
        pop-out mirror while popped out)."""
        g = self._ov_surface.geometry()
        groups = getattr(self, "_stats_groups", None)
        if groups:
            self._stats_overlay.lay_out(groups, g.width() - 16, g.height() - 16)
        else:
            self._stats_overlay.adjustSize()
        self._stats_overlay.move(g.x() + 8, g.y() + 8)

    def _pick_track(self, prop: str, tid) -> None:
        """User picked an audio/subtitle track from the options menu: apply
        it and announce the choice so the window can remember it per title.

        Subtitles go off by hiding the track, not by letting it go, and on by
        showing it: choosing a track is what makes mpv drop its buffer and
        ask the provider again (_sub_defaults), so it happens only when the
        track asked for is another one."""
        if prop != "sid":
            self._set_mpv(prop, tid)
        elif tid == "no":
            self._set_mpv("sub-visibility", False)
        else:
            m = self.video.mpv
            try:
                current = m.sid if m is not None else None
            except Exception:
                current = None
            if current != tid:
                self._set_mpv("sid", tid)
            self._remember_sub_lang(m, tid)
            # Picked on purpose: all of it, whatever "forced only" says.
            if m is not None:
                _set_forced_only(m, False)
            # A stream opened with subtitles off leaves them hidden.
            self._set_mpv("sub-visibility", True)
        self.track_selected.emit(prop, tid)

    def _remember_sub_lang(self, m, tid) -> None:
        """The language of a subtitle turned on by hand: the next film opens
        with a track in it chosen, hidden, so turning it on is instant."""
        if m is None or not self._settings:
            return
        try:
            track = next((t for t in (m.track_list or [])
                          if t.get("type") == "sub" and t.get("id") == tid),
                         None)
        except Exception:
            return
        lang = str((track or {}).get("lang") or "").strip().lower()
        if lang and lang not in ("und", "unknown"):
            self._settings.setValue("sub_last_lang", lang)

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

    def _back_buffer(self, local_buffer: bool) -> int | str:
        """demuxer-max-back-bytes for the stream about to play: the live
        rewind buffer, or what every other stream had before it."""
        if local_buffer:
            return rewind_bytes(self._live_rewind_minutes(),
                                self._live_rewind_disk())
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
                log.info("eof reached: %s", redact_url(self.current_url))
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
        self._apply_mute_icon()

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
        self._stats_timer.stop()
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
        self.stop_stream_record()
        self.dvr_shutdown()
        self._pos_timer.stop()
        self._stats_timer.stop()
        self.video.shutdown()
