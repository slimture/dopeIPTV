"""Embedded in-app video player (libmpv OpenGL render API)."""

from __future__ import annotations

import os
import sys
import time

from PyQt6.QtCore import (
    QByteArray, QEvent, QObject, QPointF, QRect, QRectF, QSize, Qt, QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QCursor, QIcon, QOpenGLContext, QPainter, QPen, QPixmap, QPolygonF,
)
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QMenu, QSizePolicy, QSlider,
    QVBoxLayout, QWidget, QPushButton,
)

from ..i18n import tr
from .players import _libmpv, _register_error_callback
from ..ui.theme import P


def _env_num(name: str, default, cast):
    """Read a numeric override from the environment, falling back to *default*
    when unset or malformed (so a stray value can never abort player start)."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return default


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
    elif name == "options":
        rows = (T, midy, B)
        knobs = (R - w * 0.18, L + w * 0.22, R - w * 0.34)
        rad = S * 0.07
        for y, kx in zip(rows, knobs):
            stroke(S * 0.075)
            p.drawLine(QPointF(L, y), QPointF(R, y))
            fill()
            p.drawEllipse(QPointF(kx, y), rad, rad)
    elif name in ("volume", "mute"):
        fill()
        body_w, body_h = w * 0.28, h * 0.42
        bx = L - w * 0.04
        p.drawRect(QRectF(bx, midy - body_h / 2, body_w, body_h))
        cone_x = bx + body_w + w * 0.26
        p.drawPolygon(QPolygonF([
            QPointF(bx + body_w, midy - body_h / 2), QPointF(cone_x, T),
            QPointF(cone_x, B), QPointF(bx + body_w, midy + body_h / 2)]))
        if name == "volume":
            stroke(S * 0.06)
            for rad in (w * 0.16, w * 0.30):
                p.drawArc(QRectF(cone_x - rad, midy - rad, rad * 2, rad * 2),
                          -55 * 16, 110 * 16)
        else:
            stroke(S * 0.07)
            xx = cone_x + w * 0.12
            dy = h * 0.16
            p.drawLine(QPointF(xx, midy - dy), QPointF(xx + w * 0.22, midy + dy))
            p.drawLine(QPointF(xx + w * 0.22, midy - dy), QPointF(xx, midy + dy))
    p.end()
    pm.setDevicePixelRatio(scale)
    return QIcon(pm)


def _format_time(seconds: float | None) -> str:
    seconds = max(0, int(seconds or 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class _MpvGLWidget(QOpenGLWidget):
    """Video surface that owns the mpv render context."""

    frame_ready = pyqtSignal()
    playback_error = pyqtSignal(str)
    video_mouse_press = pyqtSignal(object)
    video_mouse_move = pyqtSignal(object)
    video_mouse_release = pyqtSignal(object)
    video_dbl_click = pyqtSignal()
    video_key_press = pyqtSignal(object)
    video_key_release = pyqtSignal(object)

    EXTRA_OPTS: dict = {}

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if sys.platform == "darwin":
            from ..core.platform_macos import apply_widget_surface_format
            apply_widget_surface_format(self)
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        # Focusable so it can receive Left/Right for seeking once clicked.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.mpv = None
        self._ctx = None
        self._gl_init_error: str | None = None
        self._blank = False
        self.frame_ready.connect(self.update)

    def set_blank(self, blank: bool) -> None:
        """When blanked, paint solid black instead of mpv's last frame. Used on
        stop so the pane clears instead of freezing on the final image."""
        self._blank = blank
        self.update()

    _SEEK_KEYS = (Qt.Key.Key_Left, Qt.Key.Key_Right)
    _PLAYER_KEYS = (Qt.Key.Key_Left, Qt.Key.Key_Right,
                    Qt.Key.Key_Up, Qt.Key.Key_Down)

    def keyPressEvent(self, event) -> None:
        # Plain (unmodified) Left/Right seek and Up/Down volume when the video
        # has focus (mpv-style). Ctrl+Left/Right stays a channel zap, so only
        # claim the unmodified presses.
        if (event.key() in self._PLAYER_KEYS
                and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            self.video_key_press.emit(event)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if (event.key() in self._SEEK_KEYS
                and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            self.video_key_release.emit(event)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def mousePressEvent(self, event) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.video_mouse_press.emit(event)

    def mouseMoveEvent(self, event) -> None:
        self.video_mouse_move.emit(event)

    def mouseReleaseEvent(self, event) -> None:
        self.video_mouse_release.emit(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.video_dbl_click.emit()

    def _get_proc_address(self, _, name: bytes) -> int:
        glctx = QOpenGLContext.currentContext()
        if glctx is not None:
            addr = glctx.getProcAddress(QByteArray(name))
            if addr:
                v = int(addr)
                if v:
                    return v
        if sys.platform == "darwin":
            from ..core.platform_macos import gl_get_proc_address_fallback
            return gl_get_proc_address_fallback(name)
        return 0

    def initializeGL(self) -> None:
        self.makeCurrent()
        glctx = QOpenGLContext.currentContext()
        if glctx is not None:
            v = glctx.format().version()
            print(f"[dopeIPTV] GL context: {v[0]}.{v[1]} "
                  f"profile={glctx.format().profile().name}",
                  file=sys.stderr)
        else:
            print("[dopeIPTV] WARNING: no GL context in initializeGL",
                  file=sys.stderr)
        # If mpv already exists, this is a *repeat* initializeGL - the window's
        # GL context was recreated (e.g. the compositor reparented the window
        # while dragging it, or it moved to another monitor). Do NOT rebuild:
        # a fresh mpv instance would tear down the stream that's playing (which
        # surfaced as "Stream error: loading failed" on a simple window move).
        # Keep the existing instance; paintGL already tolerates a stale context
        # by drawing a black frame until it settles.
        if self.mpv is not None:
            print("[dopeIPTV] initializeGL re-entered; keeping existing mpv",
                  file=sys.stderr)
            return
        # Building the mpv render context can fail hard on a weak or
        # software-only GL stack (typically a VM with no GPU acceleration).
        # This runs inside a Qt virtual override (initializeGL), and if the
        # exception were allowed to propagate out of it PyQt aborts the whole
        # process with "Fatal Python error: Aborted" - the app never opens.
        # Catch it: leave the video surface blank, keep the app alive, and
        # surface a clear reason instead of a crash. The real machines this
        # targets (with a GPU) never hit this path.
        try:
            self._build_mpv_render()
            print("[dopeIPTV] Render context ready", file=sys.stderr)
        except Exception as e:
            self._ctx = None
            self.mpv = None
            self._gl_init_error = f"{type(e).__name__}: {e}"
            print(f"[dopeIPTV] embedded GL init failed, in-app video "
                  f"disabled: {self._gl_init_error}", file=sys.stderr)
            # Defer the toast so it fires after the window is up, not from
            # inside the GL callback.
            QTimer.singleShot(1500, lambda: self.playback_error.emit(
                tr("embedded_gl_failed")))

    def _build_mpv_render(self) -> None:
        # vo=libmpv is mandatory for the render API and exists in every
        # libmpv build - create the instance with just that (plus a quiet
        # terminal), then apply the rest tolerantly below.
        print("[dopeIPTV] Creating mpv instance...", file=sys.stderr)
        self.mpv = _libmpv.MPV(vo="libmpv", terminal=False)
        # Best-effort options. Some minimal libmpv builds (notably the one
        # compiled inside our Flatpak) don't implement every option - e.g.
        # 'osc'. Passing them all to the constructor makes ONE unknown
        # option abort the whole player ("mpv option does not exist"), so
        # set them one at a time and skip any this build rejects.
        soft = {"user_agent": "dopeIPTV/1.0", "keep_open": "yes",
                "input_default_bindings": False, "input_vo_keyboard": False,
                # Hardware decoding - without this mpv software-decodes, which
                # stutters on 4K (esp. 10-bit HEVC/HDR) even on fast CPUs.
                # 'auto-copy-safe' decodes on the GPU's dedicated engine and
                # copies the frame back to RAM for upload as a normal texture.
                # We deliberately prefer the *copy* path over zero-copy interop:
                # our OpenGL render-API + QOpenGLWidget context can't always
                # negotiate direct GL interop (vaapi-egl / cuda-gl), and when
                # that fails you get a black frame or a software fallback. The
                # copy path works with any GL context and any GPU that has a
                # decoder (AMD vaapi-copy, Intel vaapi-copy, NVIDIA nvdec /
                # vulkan-copy) - the heavy decode still runs on hardware. It
                # also uses noticeably less RAM here than the interop path.
                # Enthusiasts can force zero-copy with DOPEIPTV_HWDEC=nvdec etc.
                "hwdec": (os.environ.get("DOPEIPTV_HWDEC")
                          or "auto-copy-safe"),
                # (No 'osc' option: the on-screen controller is a feature of
                # the standalone mpv GUI and doesn't exist in the libmpv/render
                # build we use - setting it only logged a harmless skip.)
                # Network resilience for live streams: abort a dead connection
                # instead of hanging forever (that then surfaces as an error we
                # auto-reconnect from), and let ffmpeg transparently reconnect
                # dropped HTTP streams. Any option a minimal libmpv build
                # rejects is skipped by the per-key loop below.
                "network-timeout": 30,
                "demuxer-lavf-o": "reconnect=1,reconnect_streamed=1,"
                                  "reconnect_on_network_error=1,"
                                  "reconnect_delay_max=5",
                # Faster channel switching: ffmpeg otherwise probes up to ~5 s /
                # 5 MB of a live MPEG-TS stream before it shows the first frame.
                # A live TS declares its codecs up front, so a shorter probe
                # reaches the picture much sooner with no practical downside.
                # Tunable/disable-able via env (seconds / bytes; 0 = ffmpeg's
                # own default) for streams that need deeper probing.
                "demuxer-lavf-analyzeduration": _env_num(
                    "DOPEIPTV_ANALYZEDURATION", 1.0, float),
                "demuxer-lavf-probesize": _env_num(
                    "DOPEIPTV_PROBESIZE", 2_000_000, int),
                # Never let mpv open its own window, and keep its OSD silent -
                # otherwise it draws the media title centred on black while a
                # stream buffers, which can surface as a stray frame.
                "force-window": "no", "osd-level": 0}
        if sys.platform == "darwin":
            from ..core.platform_macos import extra_mpv_opts
            soft.update(extra_mpv_opts())
        # Demuxer cache: default to mpv's own buffering (what standalone mpv
        # uses). A hard byte cap here bounds RAM but also removes the cushion a
        # high-bitrate VBR 4K stream needs - too small a cap starves the
        # decoder on bitrate spikes and shows up as a periodic ~10s hitch that
        # external mpv (bigger default cache) never has. Only cap when the user
        # explicitly opts in via DOPEIPTV_DEMUX_MAX / DOPEIPTV_DEMUX_MAX_BACK
        # (e.g. "192MiB"), trading a little smoothness headroom for less RAM.
        _dmax = os.environ.get("DOPEIPTV_DEMUX_MAX")
        _dback = os.environ.get("DOPEIPTV_DEMUX_MAX_BACK")
        if _dmax:
            soft["demuxer-max-bytes"] = _dmax
        if _dback:
            soft["demuxer-max-back-bytes"] = _dback
        # Video timing. Default is mpv's own (audio) sync - the most tested,
        # timing-tolerant mode, which suits our QOpenGLWidget surface (Qt
        # composites the frame, so we can't guarantee the exact display timing
        # that video-sync=display-resample needs). Enthusiasts can opt into a
        # display-sync mode with DOPEIPTV_VIDEO_SYNC=display-resample.
        _vsync = os.environ.get("DOPEIPTV_VIDEO_SYNC")
        if _vsync:
            soft["video-sync"] = _vsync
        soft.update(self.EXTRA_OPTS)
        for key, val in soft.items():
            try:
                self.mpv[key.replace("_", "-")] = val
            except Exception as e:
                print(f"[dopeIPTV] mpv option {key!r} skipped: {e}",
                      file=sys.stderr)
        print("[dopeIPTV] mpv created, creating render context...",
              file=sys.stderr)
        self._proc_address_fn = _libmpv.MpvGlGetProcAddressFn(
            self._get_proc_address)
        self._ctx = _libmpv.MpvRenderContext(
            self.mpv, "opengl",
            opengl_init_params={
                "get_proc_address": self._proc_address_fn})
        self._ctx.update_cb = lambda: self.frame_ready.emit()
        _register_error_callback(self.mpv, self.playback_error)

    def paintGL(self) -> None:
        # Blank branch first so a repaint that arrives mid-stop (when mpv has
        # already been told to stop but our _ctx is still around) doesn't try
        # to render an mpv frame with a half-torn-down context - which
        # crashes on Wayland. Also guard the render path against a null ctx,
        # a null current GL context, or an exception bubbling out of libmpv,
        # so any transient race becomes a black frame instead of a segfault.
        if self._blank or self._ctx is None:
            glctx = QOpenGLContext.currentContext()
            if glctx is not None:
                try:
                    f = glctx.functions()
                    f.glClearColor(0.0, 0.0, 0.0, 1.0)
                    f.glClear(0x00004000)  # GL_COLOR_BUFFER_BIT
                except Exception:
                    pass
            return
        try:
            ratio = (self.devicePixelRatioF()
                     if hasattr(self, "devicePixelRatioF") else 1)
            self._ctx.render(flip_y=True, opengl_fbo={
                "w": int(self.width() * ratio),
                "h": int(self.height() * ratio),
                "fbo": self.defaultFramebufferObject(),
            })
            # Tell mpv a frame was just presented. Without this it can't
            # measure the real display refresh, so its frame-timing estimate
            # drifts and high-fps / 4K content judders and drops frames. The
            # buffer swap happens right after paintGL returns, so reporting
            # here (once per painted frame) is the standard approximation.
            try:
                self._ctx.report_swap()
            except Exception:
                pass
        except Exception as e:
            print(f"[dopeIPTV] paintGL render failed: {e}", file=sys.stderr)
            self._blank = True

    def shutdown(self) -> None:
        if self._ctx:
            try:
                self._ctx.free()
            except Exception:
                pass
            self._ctx = None
        if self.mpv:
            try:
                self.mpv.terminate()
            except Exception:
                pass
            self.mpv = None


class _SeekSlider(QSlider):
    """Horizontal seek bar: click jumps to position, drag scrubs, seek on release."""

    seek_requested = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.dragging = False
        self._markers: list[float] = []   # programme boundaries as 0..1 fractions

    def set_markers(self, fractions) -> None:
        """Programme-boundary ticks along the groove, as fractions 0..1 (left
        to right). Empty (the default) draws nothing, so the plain VOD seek bar
        is unaffected."""
        fr = sorted(f for f in fractions if 0.0 < f < 1.0)
        if fr != self._markers:
            self._markers = fr
            self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._markers:
            return
        w, h = self.width(), self.height()
        painter = QPainter(self)
        pen = QPen(QColor(255, 255, 255, 130))
        pen.setWidth(1)
        painter.setPen(pen)
        y0, y1 = int(h * 0.28), int(h * 0.72)
        for f in self._markers:
            x = int(f * (w - 1))
            painter.drawLine(x, y0, x, y1)
        painter.end()

    def _value_for(self, event) -> int:
        ratio = event.position().x() / max(1, self.width())
        span = self.maximum() - self.minimum()
        return int(self.minimum() + max(0.0, min(1.0, ratio)) * span)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.setValue(self._value_for(event))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.dragging:
            self.setValue(self._value_for(event))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.dragging and event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            self.seek_requested.emit(self.value())
            event.accept()
            return
        super().mouseReleaseEvent(event)


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
        if self._over_video():
            set_cursor_hidden(True)

    def eventFilter(self, obj, event):
        et = event.type()
        if et == QEvent.Type.MouseMove and sys.platform == "darwin":
            from ..core.platform_macos import set_cursor_hidden
            set_cursor_hidden(False)
            if self._over_video():
                self._cursor_timer.start()
            else:
                self._cursor_timer.stop()
        elif et in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            if self._handle_seek_key(event, et == QEvent.Type.KeyPress):
                return True
        return False

    def _handle_seek_key(self, event, pressed: bool) -> bool:
        if event.key() not in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            return False
        # Ctrl+arrows stay a channel zap; only claim the bare presses.
        if event.modifiers() != Qt.KeyboardModifier.NoModifier:
            return False
        p = self._player
        if p.current_url is None or not p._is_seekable():
            return False
        # Never steal arrows from a text field (search box, PIN entry, ...).
        if isinstance(QApplication.focusWidget(), QLineEdit):
            return False
        if pressed:
            p._on_seek_key_press(event)
        else:
            p._on_seek_key_release(event)
        return True


class EmbeddedPlayer(QWidget):
    """Video pane with libmpv OpenGL rendering, control bar, and fullscreen overlay."""

    double_clicked = pyqtSignal()
    playback_error = pyqtSignal(str)
    zap = pyqtSignal(int)
    exit_fullscreen = pyqtSignal()
    timeshift_menu = pyqtSignal(object)
    record_menu = pyqtSignal(object)
    pip_requested = pyqtSignal()
    pip_context_menu = pyqtSignal(object)
    stopped = pyqtSignal()
    resume_requested = pyqtSignal()
    stalled = pyqtSignal()
    finished = pyqtSignal()
    next_episode = pyqtSignal()
    paused_changed = pyqtSignal(bool)
    timeshift_seek = pyqtSignal(int)   # minutes back from live (0 = go live)

    OVERLAY_HIDE_MS = 3000
    VIDEO_BOX_HEIGHT = 260
    # How long mpv may sit idle (buffer-starved) while not paused before we
    # treat the stream as frozen and ask for a reconnect.
    STALL_SECS = 12
    MINIBTN = 28
    ICON_PX = 15  # drawn control-icon size inside the 28px buttons

    def __init__(self, parent: QWidget | None = None,
                 settings=None) -> None:
        super().__init__(parent)
        # Initialised up front because eventFilter() reads them, and events
        # can be delivered to the filtered widgets (e.g. font/style changes
        # on the control bar) while the rest of __init__ is still running.
        self._fs_ui = False
        self._pip_mode = False
        self.seek_overlay = None
        self._settings = settings
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
        self.video.installEventFilter(self)
        self.video.setMouseTracking(True)
        # Route mpv errors through a filter so a user-initiated stop doesn't
        # surface the "aborted / loading failed" mpv fires while it winds
        # the stream down as a "Stream error" toast.
        self._stopping = False
        self.video.playback_error.connect(self._on_playback_error)
        self.video.video_dbl_click.connect(self._on_video_dbl_click)
        self.video.video_mouse_press.connect(self._on_video_press)
        self.video.video_mouse_move.connect(self._on_video_move)
        self.video.video_mouse_release.connect(self._on_video_release)
        self.video.video_key_press.connect(self._on_seek_key_press)
        self.video.video_key_release.connect(self._on_seek_key_release)
        # Continuous seek while an arrow key is held down.
        self._seek_hold_dir = 0
        self._seek_hold_timer = QTimer(self)
        self._seek_hold_timer.setInterval(400)
        self._seek_hold_timer.timeout.connect(
            lambda: self._relative_seek(self._seek_hold_dir))
        lay.addWidget(self.video, 1)

        self.bar = QWidget()
        bl = QHBoxLayout(self.bar)
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
        self.pip_btn = QPushButton("PiP", objectName="MiniBtn")
        self.pip_btn.setToolTip(tr("tooltip_pip"))
        self.pip_btn.clicked.connect(self.pip_requested)
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
        bl.addWidget(self.pip_btn)
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

        self.overlay = QLabel("", self)
        self.overlay.setStyleSheet(
            "background: rgba(16,16,20,210); color:#ECECF1;"
            "border-radius:10px; padding:10px 14px; font-size:13px;")
        self.overlay.setWordWrap(True)
        self.overlay.hide()

        self.fs_controls = QWidget(self)
        self.fs_controls.setStyleSheet(
            "background: rgba(16,16,20,210); border-radius: 10px;")
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
        # height. The one text button (PiP) keeps its width but shares the
        # height; the volume sliders match the height too.
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
        self.pip_btn.setFixedHeight(m)
        self.pip_btn.setMinimumWidth(m)
        for s in (self.vol, self.fs_vol):
            s.setFixedHeight(m)

        # Map each symbol button to a drawn-icon name. -10/+30 (seek amounts)
        # and PiP stay as text labels; the pause/mute icons are swapped live
        # in _sync_pause_label / toggle_mute.
        self._icon_names = {
            self.prev_btn: "prev", self.next_btn: "next",
            self.pause_btn: "pause", self.ts_btn: "rewind",
            self.rec_btn: "record", self.opts_btn: "options",
            self.stop_btn: "stop", self.fs_btn: "fullscreen",
            self.mute_btn: "volume", self.nextep_btn: "nextep",
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

        # Sleep timer: stop playback after a chosen number of minutes.
        self._sleep_timer = QTimer(self)
        self._sleep_timer.setSingleShot(True)
        self._sleep_timer.timeout.connect(self._on_sleep_elapsed)

        self._pip_mode = False
        self._pip_bar_timer = QTimer(self)
        self._pip_bar_timer.setSingleShot(True)
        self._pip_bar_timer.timeout.connect(self._hide_pip_bar)

        # Black cover widget that sits over the mpv render surface when we
        # want the pane to be visibly black. Painted with a plain QPalette
        # background so it works even if GL cleaning is unreliable on the
        # user's compositor (Wayland's KDE/Hyprland stack sometimes still
        # shows the last mpv frame or tearing artefacts through paintGL's
        # glClear). setAutoFillBackground guarantees Qt fills it every paint.
        self._blackout = QWidget(self.video)
        self._blackout.setAutoFillBackground(True)
        pal = self._blackout.palette()
        pal.setColor(self._blackout.backgroundRole(), QColor(0, 0, 0))
        self._blackout.setPalette(pal)
        self._blackout.hide()
        self._blackout.installEventFilter(self)  # forward mouse to controls

        self._stats_overlay = QLabel("", self.video)
        self._stats_overlay.setStyleSheet(
            "background: rgba(0,0,0,180); color: #ECECF1;"
            "border-radius: 6px; padding: 8px 10px;"
            "font-family: monospace; font-size: 11px;")
        self._stats_overlay.hide()
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._update_stats_text)

        # Top-left "am I live?" badge: red LIVE at the live edge, a neutral
        # TIMESHIFT pill when watching the catch-up archive. Child of the video
        # so it floats over the picture; top-left anchor needs no reposition.
        self.live_badge = QLabel("", self.video)
        self.live_badge.hide()

        # Live timeline for timeshift channels: a floating bar at the bottom of
        # the video with LIVE at the right edge. Drag left to scrub back into
        # the provider archive (opens a catch-up segment there). Shown only for
        # timeshift channels; VOD keeps the ordinary seek bar.
        self._ts_depth = 1
        self.ts_timeline = QWidget(self)
        self.ts_timeline.setStyleSheet(
            "background: rgba(0,0,0,150); border-radius: 10px;")
        _tl = QHBoxLayout(self.ts_timeline)
        _tl.setContentsMargins(12, 6, 12, 6)
        _tl.setSpacing(10)
        self.ts_slider = _SeekSlider()
        self.ts_slider.seek_requested.connect(self._on_ts_seek)
        self.ts_label = QLabel("● LIVE")
        self.ts_label.setStyleSheet(
            "background: transparent; color: #FFFFFF;"
            "font-size: 11px; font-weight: 700;")
        # Jump straight back to the live edge from anywhere in the archive.
        self.ts_live_btn = QPushButton("⏭ LIVE")
        self.ts_live_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ts_live_btn.setStyleSheet(
            "QPushButton{background: #FF5C5C; color:#fff; border:none;"
            "border-radius: 8px; padding: 2px 10px; font-size: 11px;"
            "font-weight: 700;}"
            "QPushButton:hover{background:#e14b4b;}")
        self.ts_live_btn.clicked.connect(lambda: self.timeshift_seek.emit(0))
        self.ts_live_btn.hide()
        _tl.addWidget(self.ts_slider, 1)
        _tl.addWidget(self.ts_label)
        _tl.addWidget(self.ts_live_btn)
        self.ts_timeline.hide()

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
        self.pip_btn.setToolTip(
            tr("tooltip_exit_pip") if self._pip_mode else tr("tooltip_pip"))
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

    # -- event filter (fullscreen overlay + pip bar on control hover) ---------

    def eventFilter(self, obj, event):
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
        elif obj is self.bar and self._pip_mode:
            if event.type() in (event.Type.Enter, event.Type.MouseMove):
                self._pip_bar_timer.start(self.PIP_BAR_HIDE_MS)
                if event.type() == event.Type.MouseMove:
                    try:
                        pos = event.position().toPoint()
                    except Exception:
                        pos = event.pos()
                    edges = self._resize_edges_for_window(pos, self.bar)
                    cursor = self._EDGE_CURSORS.get(edges)
                    self.bar.setCursor(
                        cursor if cursor else Qt.CursorShape.ArrowCursor)
            elif event.type() == event.Type.MouseButtonPress:
                try:
                    pos = event.position().toPoint()
                except Exception:
                    pos = event.pos()
                edges = self._resize_edges_for_window(pos, self.bar)
                win = self.window().windowHandle()
                if edges and win:
                    win.startSystemResize(edges)
                    return True
        elif self._fs_ui and event.type() in (event.Type.Enter,
                                              event.Type.MouseMove):
            self._overlay_timer.start()
        return super().eventFilter(obj, event)

    # -- video mouse handlers (signals from _MpvGLWidget) ------------------

    RESIZE_MARGIN = 12

    def _on_video_dbl_click(self) -> None:
        self.double_clicked.emit()

    def _on_playback_error(self, msg: str) -> None:
        # Suppress the aborted-playback / "loading failed" mpv fires while a
        # user-initiated stop unwinds the current stream. Any error after
        # play() has cleared the flag is a real one and gets forwarded.
        if self._stopping:
            return
        self.playback_error.emit(msg)

    def _resize_edges_for_window(self, local_pos, source_widget):
        """Map a local mouse position to window-level edge flags for PiP resize."""
        global_pos = source_widget.mapTo(self.window(), local_pos)
        win_size = self.window().size()
        edges = Qt.Edge(0)
        if global_pos.x() <= self.RESIZE_MARGIN:
            edges |= Qt.Edge.LeftEdge
        elif global_pos.x() >= win_size.width() - self.RESIZE_MARGIN:
            edges |= Qt.Edge.RightEdge
        if global_pos.y() <= self.RESIZE_MARGIN:
            edges |= Qt.Edge.TopEdge
        elif global_pos.y() >= win_size.height() - self.RESIZE_MARGIN:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _on_video_press(self, event) -> None:
        if self._pip_mode and event.button() == Qt.MouseButton.RightButton:
            self.pip_context_menu.emit(event.globalPosition().toPoint())
            return
        if event.button() != Qt.MouseButton.LeftButton or not self._pip_mode:
            return
        win = self.window().windowHandle()
        if win is None:
            return
        edges = self._resize_edges_for_window(
            event.position().toPoint(), self.video)
        if edges:
            win.startSystemResize(edges)
        else:
            win.startSystemMove()

    _EDGE_CURSORS = {
        Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor,
        Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
        Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor,
        Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
        Qt.Edge.LeftEdge | Qt.Edge.TopEdge:
            Qt.CursorShape.SizeFDiagCursor,
        Qt.Edge.RightEdge | Qt.Edge.BottomEdge:
            Qt.CursorShape.SizeFDiagCursor,
        Qt.Edge.RightEdge | Qt.Edge.TopEdge:
            Qt.CursorShape.SizeBDiagCursor,
        Qt.Edge.LeftEdge | Qt.Edge.BottomEdge:
            Qt.CursorShape.SizeBDiagCursor,
    }

    def _on_video_move(self, event) -> None:
        if self._pip_mode and not self._fs_ui:
            self._show_pip_bar()
            if self._seekable:
                self._show_seek_overlay()
            if not (event.buttons() & Qt.MouseButton.LeftButton):
                edges = self._resize_edges_for_window(
                    event.position().toPoint(), self.video)
                cursor = self._EDGE_CURSORS.get(edges)
                self.video.setCursor(cursor if cursor else Qt.CursorShape.ArrowCursor)
        elif not self._fs_ui and self._seekable:
            # Docked, seekable content: reveal the floating scrubber.
            self._show_seek_overlay()

    def _on_video_release(self, event) -> None:
        pass

    def _is_seekable(self) -> bool:
        if self._seek_mode in ("live", "timeline"):
            return False   # plain live can't seek; timeline has its own control
        m = self.video.mpv
        try:
            return bool(m is not None and m.duration and m.duration > 1)
        except Exception:
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
        if not self._is_seekable():
            return
        step = -10 if event.key() == Qt.Key.Key_Left else 10
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

    # -- pip bar auto-hide -----------------------------------------------------

    PIP_BAR_HIDE_MS = 2500

    def set_pip_mode(self, enabled: bool) -> None:
        self._pip_mode = enabled
        if enabled:
            self._hide_seek_overlay(force=True)
            self._show_pip_bar()
        else:
            self._pip_bar_timer.stop()
            self.bar.show()
        # Entering PiP must drop the docked fixed height (so the video fills
        # the PiP frame); leaving it must restore that constant height.
        self._lock_video_box()

    def _show_pip_bar(self) -> None:
        self.bar.show()
        self._pip_bar_timer.start(self.PIP_BAR_HIDE_MS)

    def _hide_pip_bar(self) -> None:
        if self._pip_mode:
            self.bar.hide()

    # -- docked hover scrubber -------------------------------------------------

    def _show_seek_overlay(self) -> None:
        # No VOD seek overlay for plain live (can't seek) or timeshift-edge
        # (the live timeline is the control) - avoids a second, useless bar.
        if os.environ.get("DOPEIPTV_SEEK_DEBUG"):
            print(f"[dopeIPTV][seek] show_overlay mode={self._seek_mode} "
                  f"seekable={self._seekable}", file=sys.stderr)
        if self._seek_mode in ("live", "timeline"):
            return
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
        vg = self.video.geometry()
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
        b.setText("⧗ TIMESHIFT")
        b.setStyleSheet(
            "background: rgba(0,0,0,150); color: #FFFFFF;"
            "border-radius: 9px; padding: 3px 9px;"
            "font-size: 11px; font-weight: 700;")
        b.adjustSize()
        b.move(12, 12)
        b.show()
        b.raise_()

    # -- timeshift live timeline ---------------------------------------------

    @staticmethod
    def _fmt_offset(mins: int) -> str:
        mins = max(0, int(mins))
        d, rem = divmod(mins, 1440)
        h, m = divmod(rem, 60)
        if d:
            return f"{d}d {h}h"
        if h:
            return f"{h}:{m:02d}"
        return f"{m} min"

    def enter_timeshift(self, depth_min: int) -> None:
        """Show the live timeline spanning the last *depth_min* minutes."""
        self._ts_depth = max(1, int(depth_min))
        self.ts_slider.setRange(0, self._ts_depth)
        self.ts_slider.setValue(self._ts_depth)
        self.ts_label.setText("● LIVE")
        self.ts_live_btn.hide()
        self._place_ts_timeline()
        self.ts_timeline.show()
        self.ts_timeline.raise_()

    def exit_timeshift(self) -> None:
        self.ts_timeline.hide()

    def set_seek_mode(self, mode: str) -> None:
        """Pick which seek UI this stream uses (see _seek_mode). Hides the VOD
        seek overlay for live/timeline so only one bar is ever shown."""
        if os.environ.get("DOPEIPTV_SEEK_DEBUG"):
            print(f"[dopeIPTV][seek] set_seek_mode({mode})", file=sys.stderr)
        self._seek_mode = mode
        if mode != "timeline":
            self.exit_timeshift()
        if mode in ("live", "timeline"):
            self._hide_seek_ui()

    def set_timeline_markers(self, fractions) -> None:
        """Draw programme-boundary ticks on the live timeline (fractions 0..1,
        oldest to live edge)."""
        self.ts_slider.set_markers(fractions)

    def update_timeshift_position(self, offset_min: float,
                                  title: str | None = None) -> None:
        """Move the marker to *offset_min* behind live, unless the user is
        dragging it right now. *title* names the programme at that point so the
        user can see where in the schedule they are."""
        if not self.ts_timeline.isVisible() or self.ts_slider.dragging:
            return
        val = max(0, self._ts_depth - int(offset_min))
        self.ts_slider.blockSignals(True)
        self.ts_slider.setValue(val)
        self.ts_slider.blockSignals(False)
        live = offset_min < 1
        edge = "● LIVE" if live else f"−{self._fmt_offset(offset_min)}"
        self.ts_label.setText(f"{edge} · {title}" if title else edge)
        self.ts_live_btn.setVisible(not live)

    def _on_ts_seek(self, value: int) -> None:
        self.timeshift_seek.emit(int(self._ts_depth - value))

    def _place_ts_timeline(self) -> None:
        margin = 10
        vg = self.video.geometry()
        self.ts_timeline.setFixedWidth(max(240, vg.width() - 2 * margin))
        self.ts_timeline.adjustSize()
        self.ts_timeline.move(
            vg.x() + margin,
            vg.y() + vg.height() - self.ts_timeline.height() - margin)

    def set_overlay_info(self, text: str) -> None:
        self._overlay_text = text or ""
        if self._fs_ui and self.overlay.isVisible():
            self.overlay.setText(self._overlay_text)
            self._place_overlay()

    def set_next_available(self, available: bool) -> None:
        """Show/hide the 'next episode' button in both control bars. The app
        calls this when it starts a stream, based on whether a next episode is
        queued in the current series."""
        for b in (getattr(self, "nextep_btn", None),
                  getattr(self, "fs_nextep_btn", None)):
            if b is not None:
                b.setVisible(available)

    def set_fullscreen_ui(self, fullscreen: bool) -> None:
        self._fs_ui = fullscreen
        self.bar.setVisible(not fullscreen)
        if fullscreen:
            self._hide_seek_overlay(force=True)
            self._lock_video_box()
            self._show_overlay()
            self.video.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self._hide_fs_ui()
            self._overlay_timer.stop()
            self.unsetCursor()
            self.video.unsetCursor()
            self._mac_show_cursor()
            # The docked height is now a constant, not derived from
            # self.height(), so restoring it doesn't depend on the window
            # having finished its async resize back from fullscreen - it
            # can't lock in a stale size. Re-apply once now and once on the
            # next tick to be robust against the transition ordering.
            self._lock_video_box()
            QTimer.singleShot(0, self._lock_video_box)

    def _hide_fs_ui(self) -> None:
        self.overlay.hide()
        self.fs_controls.hide()
        if self._fs_ui:
            self.setCursor(Qt.CursorShape.BlankCursor)
            self.video.setCursor(Qt.CursorShape.BlankCursor)

    def _show_overlay(self) -> None:
        self.unsetCursor()
        self.video.unsetCursor()
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
        if self.fs_seek.isVisibleTo(self.fs_controls):
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
        self.overlay.move(
            margin,
            self.height() - self.fs_controls.height()
            - margin - 8 - self.overlay.height())

    def _lock_video_box(self) -> None:
        # The video surface always fills whatever the *player* is given;
        # only its minimum differs. What varies is who sizes the player.
        self.video.setMinimumHeight(190 if self._fs_ui else 0)
        self.video.setMaximumHeight(16777215)
        if self._fs_ui or self._pip_mode:
            # Fullscreen and PiP: the window (or the PiP frame) drives the
            # size, so release every constraint on the player and let its
            # layout stretch fill the available space. PiP windows are
            # deliberately small; forcing a docked floor there is what used
            # to wedge black bars after the PiP<->fullscreen round trip.
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
            bar_h = self.bar.sizeHint().height() if self.bar.isVisible() else 0
            spacing = self.layout().spacing() if bar_h else 0
            self.setFixedHeight(self.VIDEO_BOX_HEIGHT + bar_h + spacing)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._lock_video_box()
        if self._blackout.isVisible():
            self._blackout.setGeometry(self.video.rect())
        if self.overlay.isVisible() or self.fs_controls.isVisible():
            self._place_overlay()
        if self.seek_overlay.isVisible():
            self._place_seek_overlay()
        if self.ts_timeline.isVisible():
            self._place_ts_timeline()

    # -- playback defaults -----------------------------------------------------

    def apply_default_options(self) -> None:
        """Apply persistent playback defaults from Settings to the mpv core."""
        m = self.video.mpv
        if m is None or self._settings is None:
            return
        s = self._settings

        def set_opt(prop, value):
            try:
                m[prop] = value
            except Exception:
                pass

        set_opt("alang", s.value("audio_lang", "") or "")
        sub_mode = s.value("sub_mode", "auto")
        if sub_mode == "off":
            set_opt("sid", "no")
            set_opt("sub-forced-only", False)
        else:
            set_opt("sid", "auto")
            if sub_mode == "lang":
                # A primary + optional fallback language: mpv's slang takes
                # a comma-separated priority list, so "swe,eng" means prefer
                # Swedish subs but fall back to English when absent.
                langs = [s.value("sub_lang", "") or "",
                         s.value("sub_lang2", "") or ""]
                set_opt("slang", ",".join(l for l in langs if l))
            else:
                set_opt("slang", "")
            set_opt("sub-forced-only", sub_mode == "forced")
        aspect = s.value("aspect_mode", "auto")
        if aspect == "stretch":
            set_opt("keepaspect", False)
        else:
            set_opt("keepaspect", True)
            set_opt("video-aspect-override",
                    aspect if aspect != "auto" else "-1")

        # Optional video filters (all off/neutral by default). These are plain
        # mpv properties, applied live on the running core.
        #  - deinterlace: smooth interlaced live SD/HD feeds.
        #  - sharpen: unsharp mask strength (0 = off).
        #  - tone-mapping: HDR->SDR curve; harmless on SDR content.
        set_opt("deinterlace",
                s.value("video_deinterlace", "false") == "true")
        try:
            sharpen = float(s.value("video_sharpen", 0.0) or 0.0)
        except (TypeError, ValueError):
            sharpen = 0.0
        set_opt("sharpen", max(0.0, min(sharpen, 3.0)))
        set_opt("tone-mapping", s.value("video_tonemapping", "auto") or "auto")

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

    def play(self, url: str, title: str, start: float = 0.0) -> bool:
        try:
            # Fresh play cycle - drop the stop-in-progress flag so any real
            # error from this new stream (auth failed, 404, ...) is surfaced.
            self._stopping = False
            self._stall_since = 0.0
            self._eof_seen = False
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
            self.apply_default_options()
            try:
                m["volume"] = float(self.vol.value())
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
            m.play(url)
            self.current_url = url
            self._pos_timer.start()
            # Safety net for the "silent until you replay it" case: switching
            # streams while the previous one tears down can land with no audio
            # track selected even though aid=auto. Re-check shortly after load
            # and force the first audio track if none is active.
            for delay in (500, 1500):
                QTimer.singleShot(delay, self._ensure_audio_selected)
            return True
        except Exception as e:
            print(f"[dopeIPTV] Embedded playback failed: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
            self.current_url = None
            return False

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
        m = self.video.mpv
        if m is None:
            return
        try:
            m.command("seek", seconds, "absolute")
        except Exception:
            pass

    def _relative_seek(self, seconds: int) -> None:
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
            self._sync_pause_label(m.pause)
        except Exception:
            pass

    def _sync_pause_label(self, paused: bool) -> None:
        self._paused = paused
        name = "play" if paused else "pause"
        icon = _control_icon(name, self._icon_color, self.ICON_PX)
        self.pause_btn.setIcon(icon)
        self.fs_pause_btn.setIcon(icon)
        self.paused_changed.emit(paused)

    # -- options menu ----------------------------------------------------------

    def _show_options_menu(self, anchor: QWidget) -> None:
        m = self.video.mpv
        menu = QMenu(self)

        def tracks(kind):
            try:
                return [t for t in (m.track_list or [])
                        if t.get("type") == kind]
            except Exception:
                return []

        def track_label(t):
            parts = [t.get("lang") or "", t.get("title") or ""]
            label = " ".join(p for p in parts if p).strip()
            return label or f"Track {t.get('id')}"

        audio = menu.addMenu(tr("opt_audio_track"))
        for t in (tracks("audio") if m else []):
            act = audio.addAction(track_label(t))
            act.setCheckable(True)
            act.setChecked(bool(t.get("selected")))
            act.triggered.connect(
                lambda _c, tid=t.get("id"): self._set_mpv("aid", tid))
        if audio.isEmpty():
            audio.addAction(tr("opt_no_audio_tracks")).setEnabled(False)

        subs = menu.addMenu(tr("opt_subtitles"))
        off = subs.addAction(tr("opt_off"))
        off.setCheckable(True)
        sub_tracks = tracks("sub") if m else []
        off.setChecked(not any(t.get("selected") for t in sub_tracks))
        off.triggered.connect(lambda _c: self._set_mpv("sid", "no"))
        for t in sub_tracks:
            act = subs.addAction(track_label(t))
            act.setCheckable(True)
            act.setChecked(bool(t.get("selected")))
            act.triggered.connect(
                lambda _c, tid=t.get("id"): self._set_mpv("sid", tid))

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
                lambda _c, v=val: self._set_mpv("audio-delay", v))

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
        stats_act = menu.addAction(tr("opt_stats_for_nerds"))
        stats_act.triggered.connect(self._show_stats)

        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _start_sleep_timer(self, minutes: int) -> None:
        """Stop playback after *minutes* (0 cancels a running timer)."""
        if minutes <= 0:
            if self._sleep_timer.isActive():
                self._sleep_timer.stop()
                self.set_overlay_info(tr("sleep_cancelled"))
            return
        self._sleep_timer.start(minutes * 60 * 1000)
        self.set_overlay_info(tr("sleep_set", n=minutes))

    def _ask_sleep_minutes(self) -> None:
        from PyQt6.QtWidgets import QInputDialog
        mins, ok = QInputDialog.getInt(
            self, tr("opt_sleep_timer"), tr("sleep_prompt"),
            60, 1, 1440, 5)
        if ok:
            self._start_sleep_timer(int(mins))

    def _on_sleep_elapsed(self) -> None:
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

        lines = [
            f"Video: {track_info('video')}",
            f"Audio: {track_info('audio')}",
            f"HW dec: {hwdec}",
            f"A/V sync: {prop('avsync', lambda v: f'{v:.3f} s')}",
            f"Dropped: {dropped}",
            f"Timing: {mistimed} mistimed / {delayed} delayed",
            f"FPS: {prop('estimated-vf-fps', lambda v: f'{v:.1f}')}",
            f"Bitrate: {prop('video-bitrate', lambda v: f'{v / 1000:.0f} kbps' if v else '—')}",
            f"Cache: {prop('demuxer-cache-duration', lambda v: f'{v:.1f} s')}",
            f"Net: {prop('cache-speed', lambda v: f'{v / 1024:.0f} KB/s' if v else '0 KB/s')}",
            f"Format: {prop('file-format')}",
            f"Protocol: {prop('stream-path', lambda v: v.split('://')[0] if '://' in str(v) else str(v))}",
        ]
        self._stats_overlay.setText("\n".join(lines))
        self._place_stats()

    def _place_stats(self) -> None:
        self._stats_overlay.adjustSize()
        self._stats_overlay.move(8, 8)

    def _set_mpv(self, prop: str, value) -> None:
        m = self.video.mpv
        if m is None:
            return
        try:
            m[prop] = value
        except Exception as e:
            print(f"[dopeIPTV] set {prop}={value} failed: {e}",
                  file=sys.stderr)

    def _cache_secs(self) -> int:
        try:
            return int(self._settings.value("cache_secs", 10)) \
                if self._settings else 10
        except (TypeError, ValueError):
            return 10

    def _set_cache_secs(self, secs: int) -> None:
        if self._settings:
            self._settings.setValue("cache_secs", str(secs))
        self._set_mpv("cache-secs", float(secs))

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
                self.stalled.emit()
        # Natural end-of-file: with keep-open=yes mpv pauses on the last frame
        # and flags eof-reached. Emit finished() once so the app can autoplay
        # the next episode. (Live streams never end, so this never fires there.)
        try:
            eof = bool(m.eof_reached) and self.current_url is not None
        except Exception:
            eof = False
        if eof:
            if not self._eof_seen:
                self._eof_seen = True
                self.finished.emit()
        else:
            self._eof_seen = False
        seekable = (bool(dur) and dur > 1
                    and self._seek_mode not in ("live", "timeline"))
        self._seekable = seekable
        if not seekable:
            self._hide_seek_ui()
            return
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
            except Exception:
                pass
        self._apply_mute_icon()

    # -- stream recording ------------------------------------------------------

    def start_stream_record(self, path: str) -> bool:
        m = self.video.mpv
        if m is None:
            return False
        try:
            m["stream-record"] = path
            return True
        except Exception as e:
            print(f"[dopeIPTV] stream-record failed: {e}", file=sys.stderr)
            return False

    def stop_stream_record(self) -> None:
        m = self.video.mpv
        if m is None:
            return
        try:
            m["stream-record"] = ""
        except Exception:
            pass

    def stop(self) -> None:
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
        self._blackout.setGeometry(self.video.rect())
        self._blackout.show()
        self._blackout.raise_()

    def shutdown(self) -> None:
        self.stop_stream_record()
        self._pos_timer.stop()
        self._stats_timer.stop()
        self.video.shutdown()
