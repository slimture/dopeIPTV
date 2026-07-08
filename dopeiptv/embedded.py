"""Embedded in-app video player (libmpv OpenGL render API)."""

from __future__ import annotations

import sys

from PyQt6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor, QIcon, QOpenGLContext, QPainter, QPen, QPixmap, QPolygonF,
)
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMenu, QSizePolicy, QSlider,
    QVBoxLayout, QWidget, QPushButton,
)

from .i18n import tr
from .players import _libmpv, _register_error_callback
from .theme import P


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

    EXTRA_OPTS: dict = {}

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if sys.platform == "darwin":
            from .platform_macos import apply_widget_surface_format
            apply_widget_surface_format(self)
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.mpv = None
        self._ctx = None
        self.frame_ready.connect(self.update)

    def mousePressEvent(self, event) -> None:
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
            from .platform_macos import gl_get_proc_address_fallback
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
        opts = {"vo": "libmpv", "user_agent": "dopeIPTV/1.0",
                "keep_open": "yes", "input_default_bindings": False,
                "input_vo_keyboard": False, "osc": False,
                "terminal": False}
        if sys.platform == "darwin":
            from .platform_macos import extra_mpv_opts
            opts.update(extra_mpv_opts())
        opts.update(self.EXTRA_OPTS)
        print("[dopeIPTV] Creating mpv instance...", file=sys.stderr)
        self.mpv = _libmpv.MPV(**opts)
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
        print("[dopeIPTV] Render context ready", file=sys.stderr)

    def paintGL(self) -> None:
        if not self._ctx:
            return
        ratio = (self.devicePixelRatioF()
                 if hasattr(self, "devicePixelRatioF") else 1)
        self._ctx.render(flip_y=True, opengl_fbo={
            "w": int(self.width() * ratio),
            "h": int(self.height() * ratio),
            "fbo": self.defaultFramebufferObject(),
        })

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


class EmbeddedPlayer(QWidget):
    """Video pane with libmpv OpenGL rendering, control bar, and fullscreen overlay."""

    double_clicked = pyqtSignal()
    playback_error = pyqtSignal(str)
    zap = pyqtSignal(int)
    exit_fullscreen = pyqtSignal()
    timeshift_menu = pyqtSignal(object)
    record_menu = pyqtSignal(object)
    pip_requested = pyqtSignal()

    OVERLAY_HIDE_MS = 3000
    VIDEO_BOX_HEIGHT = 260
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
        self._settings = settings
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        self.video = _MpvGLWidget(self)
        self.video.installEventFilter(self)
        self.video.setMouseTracking(True)
        self.video.playback_error.connect(self.playback_error)
        self.video.video_dbl_click.connect(self._on_video_dbl_click)
        self.video.video_mouse_press.connect(self._on_video_press)
        self.video.video_mouse_move.connect(self._on_video_move)
        self.video.video_mouse_release.connect(self._on_video_release)
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
        self.ts_btn = QPushButton("⏪", objectName="MiniBtn")
        self.ts_btn.setToolTip(tr("tooltip_timeshift"))
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
        self.pip_btn = QPushButton("PiP", objectName="MiniBtn")
        self.pip_btn.setToolTip(tr("tooltip_pip"))
        self.pip_btn.clicked.connect(self.pip_requested)
        self.fs_btn = QPushButton("⛶", objectName="MiniBtn")
        self.fs_btn.setToolTip(tr("tooltip_fullscreen"))
        bl.addWidget(self.prev_btn)
        bl.addWidget(self.next_btn)
        bl.addWidget(self.pause_btn)
        bl.addWidget(self.back_btn)
        bl.addWidget(self.fwd_btn)
        bl.addWidget(self.title_lbl, 1)
        bl.addWidget(self.seek, 2)
        bl.addWidget(self.time_lbl)
        bl.addWidget(self.ts_btn)
        bl.addWidget(self.rec_btn)
        bl.addWidget(self.opts_btn)
        bl.addWidget(self.pip_btn)
        bl.addWidget(self.stop_btn)
        bl.addWidget(self.fs_btn)
        bl.addSpacing(6)
        bl.addWidget(self.mute_btn)
        bl.addSpacing(2)
        bl.addWidget(self.vol)
        lay.addWidget(self.bar)
        self.bar.setMouseTracking(True)
        self.bar.installEventFilter(self)

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
        self.fs_prev_btn.setToolTip("Previous channel (Left)")
        self.fs_prev_btn.clicked.connect(lambda: self.zap.emit(-1))
        self.fs_next_btn = QPushButton("▶", objectName="MiniBtn")
        self.fs_next_btn.setToolTip("Next channel (Right)")
        self.fs_next_btn.clicked.connect(lambda: self.zap.emit(1))
        self.fs_pause_btn = QPushButton("‖", objectName="MiniBtn")
        self.fs_pause_btn.setToolTip("Pause / resume")
        self.fs_pause_btn.clicked.connect(self.toggle_pause)
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
        self.fs_mute_btn.setToolTip("Mute / unmute")
        self.fs_mute_btn.clicked.connect(self.toggle_mute)
        self.fs_vol = QSlider(Qt.Orientation.Horizontal)
        self.fs_vol.setRange(0, 100)
        self.fs_vol.setFixedWidth(80)
        self.fs_vol.setToolTip("Volume")
        self.fs_vol.valueChanged.connect(self._set_volume)
        self.fs_ts_btn = QPushButton("⏪", objectName="MiniBtn")
        self.fs_ts_btn.setToolTip("Timeshift / catch-up")
        self.fs_ts_btn.clicked.connect(
            lambda: self.timeshift_menu.emit(self.fs_ts_btn))
        self.fs_ts_btn.hide()
        self.fs_rec_btn = QPushButton("●", objectName="MiniBtn")
        self.fs_rec_btn.setToolTip("Record")
        self.fs_rec_btn.setStyleSheet("color:#FF5C5C;")
        self.fs_rec_btn.clicked.connect(
            lambda: self.record_menu.emit(self.fs_rec_btn))
        self.fs_rec_btn.hide()
        self.fs_opts_btn = QPushButton("⚙", objectName="MiniBtn")
        self.fs_opts_btn.setToolTip("Audio / subtitles / aspect / buffer")
        self.fs_opts_btn.clicked.connect(
            lambda: self._show_options_menu(self.fs_opts_btn))
        self.fs_exit_btn = QPushButton("✕", objectName="MiniBtn")
        self.fs_exit_btn.setToolTip("Exit fullscreen (Esc)")
        self.fs_exit_btn.clicked.connect(self.exit_fullscreen.emit)
        fc.addWidget(self.fs_prev_btn)
        fc.addWidget(self.fs_next_btn)
        fc.addWidget(self.fs_pause_btn)
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
            self.stop_btn, self.fs_btn, self.mute_btn,
            self.fs_prev_btn, self.fs_next_btn, self.fs_pause_btn,
            self.fs_back_btn, self.fs_fwd_btn, self.fs_ts_btn,
            self.fs_rec_btn, self.fs_opts_btn, self.fs_exit_btn,
            self.fs_mute_btn,
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
            self.mute_btn: "volume",
            self.fs_prev_btn: "prev", self.fs_next_btn: "next",
            self.fs_pause_btn: "pause", self.fs_ts_btn: "rewind",
            self.fs_rec_btn: "record", self.fs_opts_btn: "options",
            self.fs_exit_btn: "exit", self.fs_mute_btn: "volume",
        }
        self._icon_color = P.get("text2", "#ECECF1")
        self.refresh_icons()

        self._pos_timer = QTimer(self)
        self._pos_timer.setInterval(500)
        self._pos_timer.timeout.connect(self._poll_position)

        self._fs_ui = False
        self.current_url: str | None = None
        self._muted = False
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

        self._pip_mode = False
        self._pip_bar_timer = QTimer(self)
        self._pip_bar_timer.setSingleShot(True)
        self._pip_bar_timer.timeout.connect(self._hide_pip_bar)

        self._stats_overlay = QLabel("", self.video)
        self._stats_overlay.setStyleSheet(
            "background: rgba(0,0,0,180); color: #ECECF1;"
            "border-radius: 6px; padding: 8px 10px;"
            "font-family: monospace; font-size: 11px;")
        self._stats_overlay.hide()
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._update_stats_text)

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
            if not (event.buttons() & Qt.MouseButton.LeftButton):
                edges = self._resize_edges_for_window(
                    event.position().toPoint(), self.video)
                cursor = self._EDGE_CURSORS.get(edges)
                self.video.setCursor(cursor if cursor else Qt.CursorShape.ArrowCursor)

    def _on_video_release(self, event) -> None:
        pass

    # -- pip bar auto-hide -----------------------------------------------------

    PIP_BAR_HIDE_MS = 2500

    def set_pip_mode(self, enabled: bool) -> None:
        self._pip_mode = enabled
        if enabled:
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

    # -- overlay ---------------------------------------------------------------

    def set_overlay_info(self, text: str) -> None:
        self._overlay_text = text or ""
        if self._fs_ui and self.overlay.isVisible():
            self.overlay.setText(self._overlay_text)
            self._place_overlay()

    def set_fullscreen_ui(self, fullscreen: bool) -> None:
        self._fs_ui = fullscreen
        self.bar.setVisible(not fullscreen)
        if fullscreen:
            self._lock_video_box()
            self._show_overlay()
        else:
            self._hide_fs_ui()
            self._overlay_timer.stop()
            self.unsetCursor()
            self.video.unsetCursor()
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
        self.overlay.setFixedWidth(
            max(120, min(self.width() - 2 * margin, 640)))
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
        if self.overlay.isVisible() or self.fs_controls.isVisible():
            self._place_overlay()

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
            self.title_lbl.setText(title or "")
            self._hide_seek_ui()
            if self.video.mpv is None:
                self.video.show()
                QApplication.instance().processEvents()
            if self.video.mpv is None:
                raise RuntimeError("OpenGL context not ready")
            m = self.video.mpv
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
            return True
        except Exception as e:
            print(f"[dopeIPTV] Embedded playback failed: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
            self.current_url = None
            return False

    # -- seeking ---------------------------------------------------------------

    def _seek_widgets(self):
        return (self.seek, self.time_lbl, self.back_btn, self.fwd_btn,
                self.fs_seek, self.fs_time_lbl, self.fs_back_btn,
                self.fs_fwd_btn)

    def _hide_seek_ui(self) -> None:
        for wdg in self._seek_widgets():
            wdg.hide()

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

        audio = menu.addMenu("Audio track")
        for t in (tracks("audio") if m else []):
            act = audio.addAction(track_label(t))
            act.setCheckable(True)
            act.setChecked(bool(t.get("selected")))
            act.triggered.connect(
                lambda _c, tid=t.get("id"): self._set_mpv("aid", tid))
        if audio.isEmpty():
            audio.addAction("(no audio tracks)").setEnabled(False)

        subs = menu.addMenu("Subtitles")
        off = subs.addAction("Off")
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

        delay = menu.addMenu("Audio delay")
        current_delay = 0.0
        try:
            current_delay = float(m["audio-delay"]) if m else 0.0
        except Exception:
            pass
        for val in (-1.0, -0.5, -0.25, 0.0, 0.25, 0.5, 1.0):
            act = delay.addAction(
                f"{val:+.2f} s" if val else "0 s (default)")
            act.setCheckable(True)
            act.setChecked(abs(current_delay - val) < 0.01)
            act.triggered.connect(
                lambda _c, v=val: self._set_mpv("audio-delay", v))

        aspect = menu.addMenu("Aspect ratio")
        for label, val in (("Auto", "-1"), ("16:9", "16:9"),
                           ("4:3", "4:3"), ("2.35:1", "2.35:1")):
            act = aspect.addAction(label)
            act.triggered.connect(
                lambda _c, v=val: self._set_mpv("video-aspect-override", v))
        stretch = aspect.addAction("Stretch to window")
        stretch.triggered.connect(
            lambda _c: self._set_mpv("keepaspect", False))

        buf = menu.addMenu("Network buffer")
        current_buf = self._cache_secs()
        for secs in (1, 3, 5, 10, 30):
            act = buf.addAction(f"{secs} s")
            act.setCheckable(True)
            act.setChecked(secs == current_buf)
            act.triggered.connect(
                lambda _c, s=secs: self._set_cache_secs(s))

        menu.addSeparator()
        stats_act = menu.addAction("Stats for nerds")
        stats_act.triggered.connect(self._show_stats)

        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

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
            try:
                v = m[name]
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

        lines = [
            f"Video: {track_info('video')}",
            f"Audio: {track_info('audio')}",
            f"HW dec: {hwdec}",
            f"A/V sync: {prop('avsync', lambda v: f'{v:.3f} s')}",
            f"Dropped: {dropped}",
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
        seekable = bool(dur) and dur > 1
        if not seekable:
            self._hide_seek_ui()
            return
        text = f"{_format_time(pos)} / {_format_time(dur)}"
        for slider, label in ((self.fs_seek, self.fs_time_lbl),):
            label.setText(text)
            slider.setVisible(True)
            label.setVisible(True)
            if not slider.dragging:
                slider.setMaximum(int(dur))
                slider.setValue(int(pos or 0))
        for btn in (self.fs_back_btn, self.fs_fwd_btn):
            btn.setVisible(True)

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
        m = self.video.mpv
        if m is not None:
            try:
                m["volume"] = float(value)
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
            colour = "#FF5C5C" if name == "record" else self._icon_color
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
        self.stop_stream_record()
        self.current_url = None
        if self.video.mpv:
            try:
                self.video.mpv.command("stop")
            except Exception:
                pass
        self.title_lbl.setText("")
        self._hide_seek_ui()
        self._stats_overlay.hide()
        self._stats_timer.stop()

    def shutdown(self) -> None:
        self.stop_stream_record()
        self._pos_timer.stop()
        self._stats_timer.stop()
        self.video.shutdown()
