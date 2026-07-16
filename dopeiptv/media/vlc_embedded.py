"""Experimental libVLC embedded-player backend.

This is an **opt-in spike**, not a replacement for the mpv player. It exists so
you can try the embedded picture with libVLC instead of libmpv (Settings ->
Playback -> Player engine) and compare. The default engine stays **mpv**, and
importing this module never touches the mpv path.

How it embeds: libVLC (stable 3.x) has no OpenGL-render-into-a-widget API like
mpv's, so it draws into a *native window handle* - `set_xwindow` (X11),
`set_hwnd` (Windows) or `set_nsobject` (macOS). That means:

  * On a pure **Wayland** session there is no X11 window id, so this needs
    XWayland (the app's "Force X11" setting) or it won't paint. mpv's render API
    doesn't have this limitation - which is exactly why mpv is the default.
  * The mpv-only features (single-connection `stream-record`, the archive
    timeline/timeshift, "stats for nerds", precise programme scrubbing) are
    **not** implemented here - the methods exist so the app never crashes, but
    they are safe no-ops. Recording a timeshift channel still works through the
    normal (second-connection) recorder.

`VlcEmbeddedPlayer` implements the same public interface (signals + methods +
button attributes) that `main_window` calls on `self.player`, so it can be
swapped in at construction with no other changes.
"""
from __future__ import annotations

import sys

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget,
)

# python-vlc is an OPTIONAL dependency: import-guard it so the app (and the test
# suite) load fine without it. vlc_playback_reason() reports why it's off.
try:
    import vlc as _vlc            # python-vlc (pip install python-vlc)
    _vlc_error: str | None = None
except Exception as e:            # ImportError, or libvlc not found at import
    _vlc = None
    _vlc_error = str(e)


def vlc_playback_reason() -> str | None:
    """None if the libVLC backend is usable, else a short explanation."""
    if _vlc is None:
        return (f"python-vlc / libVLC not available ({_vlc_error}). "
                "Install VLC and `pip install python-vlc`.")
    try:
        inst = _vlc.Instance()
        if inst is None:
            return "libVLC could not create an instance (no VLC runtime?)."
    except Exception as e:
        return f"libVLC failed to initialise ({e})."
    return None


def vlc_playback_supported() -> bool:
    return vlc_playback_reason() is None


class _VlcSurface(QWidget):
    """The native widget libVLC paints into. A plain, opaque widget with a
    stable native window id; mouse/double-click are surfaced so the app's
    fullscreen-on-double-click still works. `mpv = None` is a defensive stub so
    the mpv-only catch-up verifier (which never runs under VLC) can't AttributeError."""

    double_clicked = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.mpv = None
        # A real native window is required for set_xwindow/hwnd/nsobject.
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setStyleSheet("background:#000;")
        self.setMinimumHeight(190)

    def mouseDoubleClickEvent(self, e) -> None:
        self.double_clicked.emit()


class VlcEmbeddedPlayer(QWidget):
    """A libVLC-backed drop-in for `EmbeddedPlayer` (experimental).

    Only the playback-critical surface (play/stop/pause/volume/position/
    fullscreen) is real; everything mpv-specific is a safe no-op so the host UI
    keeps working. Signal names/types mirror EmbeddedPlayer exactly."""

    # --- the exact signal contract main_window connects to ------------------
    double_clicked = pyqtSignal()
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
    timeshift_seek = pyqtSignal(int)
    program_seek = pyqtSignal(int)
    playback_error = pyqtSignal(str)

    VIDEO_BOX_HEIGHT = 190   # mirror EmbeddedPlayer so min-size math is unchanged

    def __init__(self, settings=None, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.current_url: str | None = None
        self._pending_seek = 0.0
        self._last_state = None
        self._muted = False

        self._inst = _vlc.Instance("--no-xlib") if sys.platform != "win32" \
            else _vlc.Instance()
        self._mp = self._inst.media_player_new()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.video = _VlcSurface(self)
        self.video.double_clicked.connect(self.double_clicked)
        root.addWidget(self.video, 1)

        # A minimal control bar. objectName "MiniBtn" borrows the theme's button
        # styling. rec/ts buttons exist (the host reads them) but stay hidden -
        # recording/timeshift aren't wired to VLC in this spike.
        bar = QWidget(self)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(8, 4, 8, 4)
        bl.setSpacing(6)

        self.pause_btn = QPushButton("⏸", objectName="MiniBtn")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.stop_btn = QPushButton("⏹", objectName="MiniBtn")
        self.stop_btn.clicked.connect(self.stop)

        self.title_lbl = QLabel("", bar)
        self.title_lbl.setStyleSheet("color:#ddd;")

        self.mute_btn = QPushButton("🔊", objectName="MiniBtn")
        self.mute_btn.clicked.connect(self.toggle_mute)
        self.vol = QSlider(Qt.Orientation.Horizontal, bar)
        self.vol.setRange(0, 130)
        self.vol.setValue(100)
        self.vol.setFixedWidth(90)
        self.vol.valueChanged.connect(self._on_vol)

        self.pip_btn = QPushButton("⧉", objectName="MiniBtn")
        self.pip_btn.clicked.connect(self.pip_requested)
        self.fs_btn = QPushButton("⛶", objectName="MiniBtn")

        # Present but hidden: the host reads these attributes (both the normal
        # and the fullscreen-overlay variants of the record / timeshift buttons).
        self.rec_btn = QPushButton("●", objectName="MiniBtn")
        self.ts_btn = QPushButton("◀◀", objectName="MiniBtn")
        self.fs_rec_btn = QPushButton("●", objectName="MiniBtn")
        self.fs_ts_btn = QPushButton("◀◀", objectName="MiniBtn")
        for b in (self.rec_btn, self.ts_btn, self.fs_rec_btn, self.fs_ts_btn):
            b.hide()
        # The host repositions/reads .ts_timeline; a hidden stub is enough.
        self.ts_timeline = QWidget(self)
        self.ts_timeline.hide()

        for w in (self.pause_btn, self.stop_btn):
            bl.addWidget(w)
        bl.addWidget(self.title_lbl, 1)
        bl.addWidget(self.mute_btn)
        bl.addWidget(self.vol)
        bl.addWidget(self.pip_btn)
        bl.addWidget(self.fs_btn)
        bar.setStyleSheet("background:#141414;")
        root.addWidget(bar)

        # Poll VLC state on the Qt thread (libVLC events fire on a foreign
        # thread; polling avoids cross-thread signal hazards). Detects
        # play/pause/end/error transitions and applies a pending start-seek.
        self._poll = QTimer(self)
        self._poll.setInterval(300)
        self._poll.timeout.connect(self._tick)
        self._poll.start()

    # -- playback ------------------------------------------------------------
    def play(self, url: str, title: str, start: float = 0.0) -> bool:
        try:
            self.current_url = url
            self.title_lbl.setText(title or "")
            self._pending_seek = float(start or 0.0)
            media = self._inst.media_new(url)
            self._mp.set_media(media)
            self._bind_surface()
            self._mp.play()
            return True
        except Exception as e:
            self.playback_error.emit(str(e))
            return False

    def _bind_surface(self) -> None:
        """Hand libVLC the native window id of our video widget. Re-bound on
        each play and on fullscreen toggles because reparenting can mint a new
        native handle on some platforms."""
        try:
            wid = int(self.video.winId())
            if sys.platform.startswith("linux"):
                self._mp.set_xwindow(wid)
            elif sys.platform == "win32":
                self._mp.set_hwnd(wid)
            elif sys.platform == "darwin":
                self._mp.set_nsobject(wid)
        except Exception:
            pass

    def _tick(self) -> None:
        try:
            st = self._mp.get_state()
        except Exception:
            return
        if st != self._last_state:
            if st == _vlc.State.Playing:
                self.paused_changed.emit(False)
                if self._pending_seek > 0:
                    QTimer.singleShot(
                        300, lambda: self._apply_seek(self._pending_seek))
                    self._pending_seek = 0.0
            elif st == _vlc.State.Paused:
                self.paused_changed.emit(True)
            elif st == _vlc.State.Ended:
                self.finished.emit()
            elif st == _vlc.State.Error:
                self.playback_error.emit("libVLC could not play this stream")
            self._last_state = st

    def _apply_seek(self, secs: float) -> None:
        try:
            self._mp.set_time(int(secs * 1000))
        except Exception:
            pass

    def toggle_pause(self) -> None:
        try:
            self._mp.pause()   # libVLC toggles; the poll emits paused_changed
        except Exception:
            pass

    def stop(self) -> None:
        try:
            self._mp.stop()
        except Exception:
            pass
        self.stopped.emit()

    def toggle_mute(self) -> None:
        self._muted = not self._muted
        try:
            self._mp.audio_set_mute(self._muted)
        except Exception:
            pass
        self.mute_btn.setText("🔇" if self._muted else "🔊")

    def _on_vol(self, v: int) -> None:
        try:
            self._mp.audio_set_volume(int(v))
        except Exception:
            pass

    def playback_position(self) -> float:
        try:
            return max(0.0, self._mp.get_time() / 1000.0)
        except Exception:
            return 0.0

    def playback_duration(self) -> float:
        try:
            return max(0.0, self._mp.get_length() / 1000.0)
        except Exception:
            return 0.0

    def progress_percent(self) -> float:
        try:
            return max(0.0, min(100.0, self._mp.get_position() * 100.0))
        except Exception:
            return 0.0

    def shutdown(self) -> None:
        try:
            self._poll.stop()
            self._mp.stop()
            self._mp.release()
            self._inst.release()
        except Exception:
            pass

    # -- fullscreen / pip ----------------------------------------------------
    def set_fullscreen_ui(self, fullscreen: bool) -> None:
        # Reparenting for fullscreen can change the native handle; re-bind so
        # libVLC keeps painting into the (possibly new) surface.
        QTimer.singleShot(0, self._bind_surface)

    def set_pip_mode(self, enabled: bool) -> None:
        QTimer.singleShot(0, self._bind_surface)

    # -- no-ops for the mpv-only feature surface (kept so the host can't crash)
    def apply_default_options(self) -> None: ...
    def refresh_icons(self) -> None: ...
    def retranslate_ui(self) -> None: ...
    def _show_stats(self) -> None: ...
    def set_overlay_info(self, text: str) -> None: ...
    def set_next_available(self, available: bool) -> None: ...
    def set_seek_mode(self, mode: str) -> None: ...
    def set_live_badge(self, kind) -> None: ...
    def enter_timeshift(self, depth_min: int) -> None: ...
    def update_timeshift_position(self, offset_min: float, title=None,
                                  paused: bool = False) -> None: ...
    def set_program_window(self, secs: float, base: float = 0.0) -> None: ...
    def set_on_archive_segment(self, on: bool) -> None: ...
    def set_timeline_segments(self, segments) -> None: ...

    def start_stream_record(self, path: str) -> bool:
        # No single-connection record with the VLC spike; the normal recorder
        # (its own connection) still works from the channel's context menu.
        return False

    def stop_stream_record(self) -> None: ...
