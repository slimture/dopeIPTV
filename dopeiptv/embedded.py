"""Embedded in-app video player (libmpv OpenGL render API)."""

from __future__ import annotations

import ctypes
import ctypes.util
import sys

from PyQt6.QtCore import QByteArray, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QOpenGLContext
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMenu, QSizePolicy, QSlider,
    QVBoxLayout, QWidget, QPushButton,
)

from .players import _libmpv, _register_error_callback

# On macOS, Qt's getProcAddress can fail for core GL functions.
# Load the OpenGL framework directly as a reliable fallback.
_opengl_dll = None
if sys.platform == "darwin":
    try:
        _opengl_dll = ctypes.cdll.LoadLibrary(
            ctypes.util.find_library("OpenGL"))
    except Exception:
        pass


def _format_time(seconds: float | None) -> str:
    seconds = max(0, int(seconds or 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class _MpvGLWidget(QOpenGLWidget):
    """Video surface that owns the mpv render context."""

    frame_ready = pyqtSignal()
    playback_error = pyqtSignal(str)

    EXTRA_OPTS: dict = {}

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if sys.platform == "darwin":
            from PyQt6.QtGui import QSurfaceFormat
            fmt = QSurfaceFormat()
            fmt.setVersion(4, 1)
            fmt.setProfile(
                QSurfaceFormat.OpenGLContextProfile.CoreProfile)
            self.setFormat(fmt)
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.mpv = None
        self._ctx = None
        self.frame_ready.connect(self.update)

    def _get_proc_address(self, _, name: bytes) -> int:
        glctx = QOpenGLContext.currentContext()
        if glctx is not None:
            addr = glctx.getProcAddress(QByteArray(name))
            if addr:
                v = int(addr)
                if v:
                    return v
        if _opengl_dll is not None:
            try:
                return ctypes.cast(
                    getattr(_opengl_dll, name.decode("utf-8")),
                    ctypes.c_void_p).value or 0
            except (AttributeError, OSError):
                pass
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
            opts["hwdec"] = "videotoolbox-copy"
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

    OVERLAY_HIDE_MS = 3000
    VIDEO_BOX_HEIGHT = 260

    def __init__(self, parent: QWidget | None = None,
                 settings=None) -> None:
        super().__init__(parent)
        self._settings = settings
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        self.video = _MpvGLWidget(self)
        self.video.installEventFilter(self)
        self.video.setMouseTracking(True)
        self.video.playback_error.connect(self.playback_error)
        lay.addWidget(self.video, 1)

        self.bar = QWidget()
        bl = QHBoxLayout(self.bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(4)
        self.prev_btn = QPushButton("◀", objectName="MiniBtn")
        self.prev_btn.setToolTip("Previous channel (Ctrl+Left)")
        self.prev_btn.clicked.connect(lambda: self.zap.emit(-1))
        self.next_btn = QPushButton("▶", objectName="MiniBtn")
        self.next_btn.setToolTip("Next channel (Ctrl+Right)")
        self.next_btn.clicked.connect(lambda: self.zap.emit(1))
        self.pause_btn = QPushButton("⏸", objectName="MiniBtn")
        self.pause_btn.setToolTip("Pause / resume")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.back_btn = QPushButton("-10", objectName="MiniBtn")
        self.back_btn.setToolTip("Back 10 seconds")
        self.back_btn.clicked.connect(lambda: self._relative_seek(-10))
        self.back_btn.hide()
        self.fwd_btn = QPushButton("+30", objectName="MiniBtn")
        self.fwd_btn.setToolTip("Forward 30 seconds")
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
        self.mute_btn = QPushButton("\U0001f50a", objectName="MiniBtn")
        self.mute_btn.setToolTip("Mute / unmute")
        self.mute_btn.clicked.connect(self.toggle_mute)
        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setFixedWidth(50)
        self.vol.setToolTip("Volume")
        self.vol.valueChanged.connect(self._set_volume)
        self.ts_btn = QPushButton("⏪", objectName="MiniBtn")
        self.ts_btn.setToolTip("Timeshift / catch-up")
        self.ts_btn.clicked.connect(
            lambda: self.timeshift_menu.emit(self.ts_btn))
        self.ts_btn.hide()
        self.rec_btn = QPushButton("REC", objectName="MiniBtn")
        self.rec_btn.setToolTip("Record this channel")
        self.rec_btn.clicked.connect(
            lambda: self.record_menu.emit(self.rec_btn))
        self.rec_btn.hide()
        self.opts_btn = QPushButton("⚙", objectName="MiniBtn")
        self.opts_btn.setToolTip("Audio / subtitles / aspect / buffer")
        self.opts_btn.clicked.connect(
            lambda: self._show_options_menu(self.opts_btn))
        self.stop_btn = QPushButton("■", objectName="MiniBtn")
        self.stop_btn.setToolTip("Stop playback")
        self.stop_btn.clicked.connect(self.stop)
        self.fs_btn = QPushButton("⛶", objectName="MiniBtn")
        self.fs_btn.setToolTip("Fullscreen")
        bl.addWidget(self.prev_btn)
        bl.addWidget(self.next_btn)
        bl.addWidget(self.pause_btn)
        bl.addWidget(self.back_btn)
        bl.addWidget(self.fwd_btn)
        bl.addWidget(self.title_lbl, 1)
        bl.addWidget(self.seek, 2)
        bl.addWidget(self.time_lbl)
        bl.addWidget(self.mute_btn)
        bl.addWidget(self.vol)
        bl.addWidget(self.ts_btn)
        bl.addWidget(self.rec_btn)
        bl.addWidget(self.opts_btn)
        bl.addWidget(self.stop_btn)
        bl.addWidget(self.fs_btn)
        lay.addWidget(self.bar)

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
        self.fs_pause_btn = QPushButton("⏸", objectName="MiniBtn")
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
        self.fs_rec_btn = QPushButton("REC", objectName="MiniBtn")
        self.fs_rec_btn.setToolTip("Record this channel")
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

    # -- event filter ----------------------------------------------------------

    def eventFilter(self, obj, event):
        if obj is self.video:
            if event.type() == event.Type.MouseButtonDblClick:
                self.double_clicked.emit()
                return True
            if event.type() == event.Type.MouseMove and self._fs_ui:
                self._show_overlay()
        elif self._fs_ui and event.type() in (event.Type.Enter,
                                              event.Type.MouseMove):
            self._overlay_timer.start()
        return super().eventFilter(obj, event)

    # -- overlay ---------------------------------------------------------------

    def set_overlay_info(self, text: str) -> None:
        self._overlay_text = text or ""
        if self._fs_ui and self.overlay.isVisible():
            self.overlay.setText(self._overlay_text)
            self._place_overlay()

    def set_fullscreen_ui(self, fullscreen: bool) -> None:
        self._fs_ui = fullscreen
        self.bar.setVisible(not fullscreen)
        self._lock_video_box()
        if fullscreen:
            self._show_overlay()
        else:
            self._hide_fs_ui()
            self._overlay_timer.stop()
            self.unsetCursor()
            self.video.unsetCursor()

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
        if self._fs_ui:
            self.video.setMinimumHeight(190)
            self.video.setMaximumHeight(16777215)
        else:
            self.video.setFixedHeight(self.VIDEO_BOX_HEIGHT)

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
        else:
            set_opt("sid", "auto")
            set_opt("slang", (s.value("sub_lang", "") or "")
                    if sub_mode == "lang" else "")
        aspect = s.value("aspect_mode", "auto")
        if aspect == "stretch":
            set_opt("keepaspect", False)
        else:
            set_opt("keepaspect", True)
            set_opt("video-aspect-override",
                    aspect if aspect != "auto" else "-1")

    def play(self, url: str, title: str) -> bool:
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
        label = "▶" if paused else "⏸"
        self.pause_btn.setText(label)
        self.fs_pause_btn.setText(label)

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

        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

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

    def toggle_mute(self) -> None:
        self._muted = not self._muted
        m = self.video.mpv
        if m is not None:
            try:
                m["mute"] = self._muted
            except Exception:
                pass
        label = "\U0001f507" if self._muted else "\U0001f50a"
        self.mute_btn.setText(label)
        self.fs_mute_btn.setText(label)

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

    def shutdown(self) -> None:
        self.stop_stream_record()
        self._pos_timer.stop()
        self.video.shutdown()
