"""Multiview: watch up to four live channels at once in a 2x2 grid.

A separate top-level window holding four lightweight video cells, each its own
libmpv render surface (`_MpvGLWidget`) - the *docked* embedded player is never
touched. Click a cell to give it audio focus (the others stay muted); right-
click for mute, swap/move, and remove. Channels are added from the main list's
right-click "Add to multiview", capturing whatever playlist is active at that
moment - so four cells can come from four different accounts, sidestepping a
single account's connection limit.

The window mirrors the pop-out player's chrome options: title-bar-less by
default (drag a cell to move it), with right-click "Always on top" and
"Show title bar". Cell titles/numbers auto-hide and reappear on mouse
movement; a seek bar shows position/pause for seekable (timeshift/catch-up)
cells. Space pauses the focused cell; Left/Right seek it.

Heads-up for the user: each cell is a separate stream = a separate connection
to the provider. On a low connection-limit account the extra cells will be
refused (the same 458 / "all connections in use" the diagnosis reports).

Kept out of main_window.py to keep that file lean.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialogButtonBox, QGridLayout, QLabel, QMenu,
    QSlider, QVBoxLayout, QWidget)

from ..core.log import log
from ..i18n import tr
from ..media.embedded import _MpvGLWidget


def _fmt(secs: float) -> str:
    secs = int(max(0, secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class _MultiviewCell(QWidget):
    """One grid cell: a bare mpv video surface with a position number, an
    auto-hiding title strip and seek bar, click-to-focus, and per-cell
    mute/pause/seek."""

    focus_requested = pyqtSignal(object)
    context_requested = pyqtSignal(object, object)   # (cell, global pos)
    hovered = pyqtSignal(object)

    def __init__(self, number: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.number = number
        self.url: str | None = None
        self.title: str = ""
        self._focused = False
        self._muted = True
        self._drag_from = None
        self._overlays_on = False
        self._seeking = False
        self._dur = 0.0
        self.setStyleSheet("background:#000000;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.video = _MpvGLWidget(self)
        self.video.setMinimumSize(0, 0)
        lay.addWidget(self.video)
        self.video.video_mouse_press.connect(self._on_press)
        self.video.video_mouse_move.connect(self._on_move)
        self.video.video_mouse_release.connect(self._on_release)
        self.video.playback_error.connect(self._on_error)

        self._title = QLabel("", self.video)
        self._title.setStyleSheet(
            "background:rgba(0,0,0,150); color:#ECECF1; padding:3px 8px;"
            "font-size:12px; font-weight:600;")
        self._title.hide()
        self._empty = QLabel(tr("mv_empty_cell"), self.video)
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet("color:#7A7A85; font-size:12px;")
        self._num = QLabel(str(number), self.video)
        self._num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._num.setStyleSheet(
            "background:transparent; color:rgba(255,255,255,140);"
            "font-size:56px; font-weight:800;")
        self._num.hide()
        self._pause = QLabel("⏸", self.video)
        self._pause.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pause.setStyleSheet(
            "background:transparent; color:rgba(255,255,255,200);"
            "font-size:44px;")
        self._pause.hide()
        # Seek bar + time, shown for seekable (timeshift/catch-up) content when
        # the overlays are revealed.
        self._seek = QSlider(Qt.Orientation.Horizontal, self.video)
        self._seek.setRange(0, 1000)
        self._seek.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self._seek.sliderReleased.connect(self._on_seek_released)
        self._seek.hide()
        self._time = QLabel("", self.video)
        self._time.setStyleSheet(
            "background:rgba(0,0,0,150); color:#ECECF1; padding:1px 6px;"
            "font-size:11px;")
        self._time.hide()
        self.set_focused(False)

    # -- overlays ------------------------------------------------------------

    def show_overlays(self, on: bool) -> None:
        self._overlays_on = on
        self._num.setVisible(on)
        self._title.setVisible(on and bool(self.title))
        if on:
            for w in (self._num, self._title, self._seek, self._time):
                w.raise_()
        else:
            self._seek.hide()
            self._time.hide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        r = self.video.rect()
        self._title.adjustSize()
        self._title.move(0, 0)
        self._empty.setGeometry(r)
        self._num.setGeometry(r)
        self._pause.setGeometry(r)
        self._seek.setGeometry(10, r.height() - 26, r.width() - 90, 16)
        self._time.adjustSize()
        self._time.move(r.width() - self._time.width() - 8, r.height() - 26)

    # -- playback ------------------------------------------------------------

    def play(self, url: str, title: str) -> bool:
        self.url = url
        self.title = title
        self._title.setText(title or "")
        self._title.adjustSize()
        self._empty.hide()
        if self.video.mpv is None:
            self.video.show()
            QApplication.instance().processEvents()
        m = self.video.mpv
        if m is None:
            return False
        try:
            m["force-media-title"] = title or ""
            m["cache"] = "yes"
            m["mute"] = self._muted
            m.play(url)
            return True
        except Exception as e:
            log.warning("multiview cell play failed: %s: %s",
                        type(e).__name__, e)
            return False

    def _on_error(self, _msg: str) -> None:
        self._title.setText(tr("mv_cell_error", title=self.title or ""))
        self.title = self._title.text()
        self._title.setVisible(True)
        self._title.adjustSize()

    def set_focused(self, on: bool) -> None:
        self._focused = on
        self.setStyleSheet(
            "background:#000000;"
            + ("border:2px solid #e5354b;" if on else "border:2px solid #202028;"))

    def set_muted(self, muted: bool) -> None:
        self._muted = muted
        if self.video.mpv is not None and self.url is not None:
            try:
                self.video.mpv["mute"] = muted
            except Exception:
                pass

    def is_muted(self) -> bool:
        return self._muted

    def toggle_pause(self) -> None:
        m = self.video.mpv
        if m is None or self.url is None:
            return
        try:
            m.pause = not m.pause
        except Exception:
            pass

    def seek(self, secs: int) -> None:
        m = self.video.mpv
        if m is None or self.url is None:
            return
        try:
            m.command("seek", secs, "relative")
        except Exception:
            pass

    def _on_seek_released(self) -> None:
        self._seeking = False
        m = self.video.mpv
        if m is None or self.url is None or self._dur < 1:
            return
        try:
            m.command("seek", self._seek.value() / 1000.0 * self._dur,
                      "absolute")
        except Exception:
            pass

    def refresh_state(self) -> None:
        """Poll mpv for position/duration/pause and update the seek bar + pause
        glyph. Called on a timer by the window; robust to a not-yet-ready mpv."""
        m = self.video.mpv
        if m is None or self.url is None:
            return
        try:
            paused = bool(m.pause)
        except Exception:
            paused = False
        self._pause.setVisible(paused)
        if paused:
            self._pause.raise_()
        try:
            dur = float(m.duration or 0)
            pos = float(m.time_pos or 0)
            seekable = bool(m.seekable)
        except Exception:
            self._seek.hide()
            self._time.hide()
            return
        self._dur = dur
        show = self._overlays_on and seekable and dur > 1
        self._seek.setVisible(show)
        self._time.setVisible(show)
        if show and not self._seeking:
            self._seek.blockSignals(True)
            self._seek.setValue(int(pos / dur * 1000))
            self._seek.blockSignals(False)
            self._time.setText(f"{_fmt(pos)} / {_fmt(dur)}")
            self._time.adjustSize()

    def clear(self) -> None:
        if self.video.mpv is not None:
            try:
                self.video.mpv.command("stop")
            except Exception:
                pass
        self.video.set_blank(True)
        self.url = None
        self.title = ""
        self._muted = True
        self._title.hide()
        self._pause.hide()
        self._seek.hide()
        self._time.hide()
        self._empty.show()

    def shutdown(self) -> None:
        try:
            self.video.shutdown()
        except Exception:
            pass

    # -- mouse ---------------------------------------------------------------

    def _window_frameless(self) -> bool:
        return bool(self.window().windowFlags()
                    & Qt.WindowType.FramelessWindowHint)

    def _on_press(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.context_requested.emit(
                self, event.globalPosition().toPoint())
            return
        if event.button() == Qt.MouseButton.LeftButton:
            # Focus only (audio). Mute lives on the right-click menu, so a
            # left-click - e.g. to grab and drag the frameless window - never
            # mutes by accident.
            self.focus_requested.emit(self)
            if self._window_frameless():
                self._drag_from = event.position().toPoint()

    def _on_move(self, event) -> None:
        self.hovered.emit(self)
        d = self._drag_from
        if d is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            if (event.position().toPoint() - d).manhattanLength() > 6:
                self._drag_from = None
                handle = self.window().windowHandle()
                if handle is not None:
                    handle.startSystemMove()

    def _on_release(self, event) -> None:
        self._drag_from = None


class _MultiviewWindow(QWidget):
    """Top-level 2x2 grid. Flags (frameless / always-on-top) come from the
    owner's settings; closing hands control back so every cell's mpv is torn
    down cleanly."""

    def __init__(self, owner) -> None:
        super().__init__()
        self._owner = owner
        self.setWindowTitle(tr("mv_title"))
        self.setObjectName("MvWin")
        self.setStyleSheet("#MvWin { background:#000000; }")
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        self.cells: list[_MultiviewCell] = []
        for i in range(4):
            c = _MultiviewCell(i + 1, self)
            c.focus_requested.connect(self._focus_cell)
            c.context_requested.connect(self._cell_context_menu)
            c.hovered.connect(lambda _c: self._reveal_overlays())
            c.video.video_key_press.connect(
                lambda ev, cell=c: self._cell_key(cell, ev))
            grid.addWidget(c, i // 2, i % 2)
            self.cells.append(c)
        self._focused: _MultiviewCell | None = None
        self._overlay_timer = QTimer(self)
        self._overlay_timer.setSingleShot(True)
        self._overlay_timer.setInterval(2000)
        self._overlay_timer.timeout.connect(self._hide_overlays)
        # Poll cell state (position / pause) for the seek bars.
        self._state_timer = QTimer(self)
        self._state_timer.setInterval(500)
        self._state_timer.timeout.connect(self._refresh_states)
        self._state_timer.start()
        self.setWindowFlags(self._flags())
        self._focus_cell(self.cells[0])

    # -- window flags --------------------------------------------------------

    def _flags(self) -> "Qt.WindowType":
        flags = Qt.WindowType.Window
        s = self._owner.settings
        wayland = "wayland" in QApplication.platformName().lower()
        if s.value("mv_on_top", "false") == "true" and not wayland:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        if s.value("mv_frameless", "true") == "true":
            flags |= Qt.WindowType.FramelessWindowHint
        return flags

    def _apply_flags(self) -> None:
        geo = self.geometry()
        self.setWindowFlags(self._flags())
        self.setGeometry(geo)
        self.show()
        self.raise_()

    def _set_on_top(self, on: bool) -> None:
        self._owner.settings.setValue("mv_on_top", "true" if on else "false")
        self._apply_flags()

    def _set_frameless(self, hidden: bool) -> None:
        self._owner.settings.setValue(
            "mv_frameless", "true" if hidden else "false")
        self._apply_flags()

    # -- streams / focus -----------------------------------------------------

    def add_stream(self, url: str, title: str, cell: int | None = None) -> None:
        if cell is not None and 0 <= cell < len(self.cells):
            target = self.cells[cell]
        else:
            target = next((c for c in self.cells if c.url is None),
                          self._focused or self.cells[0])
        target.play(url, title)
        self._focus_cell(target)
        self._reveal_overlays()

    def _focus_cell(self, cell: "_MultiviewCell") -> None:
        for c in self.cells:
            c.set_focused(c is cell)
            c.set_muted(c is not cell)
        self._focused = cell

    def _swap_cells(self, a: "_MultiviewCell", b: "_MultiviewCell") -> None:
        """Exchange the two cells' videos (swap places / move into an empty
        cell), then re-apply audio focus."""
        ua, ta = a.url, a.title
        ub, tb = b.url, b.title
        a.play(ub, tb) if ub else a.clear()
        b.play(ua, ta) if ua else b.clear()
        self._focus_cell(self._focused if self._focused in self.cells else a)

    def _cell_context_menu(self, cell: "_MultiviewCell", pos) -> None:
        m = QMenu(self)
        if cell.url is not None:
            m.addAction(tr("mv_unmute") if cell.is_muted() else tr("mv_mute"),
                        lambda: cell.set_muted(not cell.is_muted()))
            move = m.addMenu(tr("mv_move"))
            for other in self.cells:
                if other is cell:
                    continue
                move.addAction(
                    tr("mv_cell", n=other.number),
                    lambda _c=False, o=other: self._swap_cells(cell, o))
            m.addAction(tr("mv_remove_cell"), cell.clear)
            m.addSeparator()
        if "wayland" not in QApplication.platformName().lower():
            act = m.addAction(tr("popout_always_on_top"))
            act.setCheckable(True)
            act.setChecked(bool(self.windowFlags()
                                & Qt.WindowType.WindowStaysOnTopHint))
            act.toggled.connect(self._set_on_top)
        frameless = bool(self.windowFlags()
                         & Qt.WindowType.FramelessWindowHint)
        bar = m.addAction(tr("popout_show_titlebar") if frameless
                          else tr("popout_hide_titlebar"))
        bar.triggered.connect(lambda: self._set_frameless(not frameless))
        m.addSeparator()
        m.addAction(tr("mv_close"), self.close)
        m.exec(pos)

    # -- overlays + polling + keyboard --------------------------------------

    def _reveal_overlays(self) -> None:
        for c in self.cells:
            c.show_overlays(True)
        self._overlay_timer.start()

    def _hide_overlays(self) -> None:
        for c in self.cells:
            c.show_overlays(False)

    def _refresh_states(self) -> None:
        for c in self.cells:
            c.refresh_state()

    def _cell_key(self, cell: "_MultiviewCell", event) -> None:
        if event.key() == Qt.Key.Key_Left:
            cell.seek(-10)
        elif event.key() == Qt.Key.Key_Right:
            cell.seek(10)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space and self._focused is not None:
            self._focused.toggle_pause()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.close()   # no title-bar X when frameless
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        event.ignore()
        self._owner._close_multiview()


class _MultiviewMixin:
    def _add_channel_to_multiview(self, it, cell: int | None = None) -> None:
        """Build the live stream URL for a channel item (using the currently
        active playlist's client) and drop it into multiview cell *cell*
        (0..3), or the first free cell when None."""
        sid = it.get("stream_id")
        if sid is None:
            return
        fmt = self.settings.value("stream_format", "ts")
        try:
            url = self.client.live_url(sid, fmt)
        except Exception:
            url = it.get("_url")
        self.add_to_multiview(
            url, it.get("name") or it.get("title") or "", cell)

    def _ensure_multiview_window(self):
        """Create the multiview window if needed, then bring it to the front.
        raise_ + activateWindow is the reliable cross-platform way back to it -
        on macOS separate app windows don't cycle with Cmd+Tab."""
        self._maybe_show_multiview_info()
        if self._multiview_win is None:
            self._multiview_win = _MultiviewWindow(self)
            self._multiview_win.resize(960, 560)
        w = self._multiview_win
        w.show()
        w.raise_()
        w.activateWindow()
        return w

    def _show_multiview(self) -> None:
        self._ensure_multiview_window()

    def add_to_multiview(self, url: str, title: str,
                         cell: int | None = None) -> None:
        if not url:
            return
        # Free the docked player's connection: otherwise a single-connection
        # account refuses the multiview cell (the same channel then only plays
        # docked), and you'd have two streams and two audio tracks at once.
        self._stop_docked_for_multiview()
        self._ensure_multiview_window().add_stream(url, title, cell)

    def _maybe_show_multiview_info(self) -> None:
        """One-time notice that multiview needs enough provider connections,
        with a 'don't show again' opt-out. A themed QDialog (not QMessageBox,
        whose checkbox rendered invisible against the dark message box)."""
        if self.settings.value("mv_info_seen", "false") == "true":
            return
        from PyQt6.QtWidgets import QDialog
        d = QDialog(self)
        d.setWindowTitle(tr("mv_info_title"))
        lay = QVBoxLayout(d)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(14)
        d.setStyleSheet("QDialog { background:#1b1b20; }")
        msg = QLabel(tr("mv_info_body"))
        msg.setWordWrap(True)
        msg.setMinimumWidth(420)
        msg.setStyleSheet("color:#ECECF1; font-size:13px;")
        lay.addWidget(msg)
        cb = QCheckBox(tr("dont_show_again"))
        # Force a fully self-drawn indicator: on macOS the native check box
        # renders as an invisible same-colour square against the dark dialog.
        # An explicit bordered box that fills with the accent when checked is
        # visible regardless of platform/native styling.
        cb.setStyleSheet(
            "QCheckBox { color:#ECECF1; font-size:13px; spacing:8px; }"
            "QCheckBox::indicator { width:18px; height:18px;"
            " border:1px solid #6A6A75; border-radius:3px; background:#2A2A32; }"
            "QCheckBox::indicator:checked {"
            " background:#e5354b; border:1px solid #e5354b; }")
        lay.addWidget(cb)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(d.accept)
        lay.addWidget(bb)
        d.exec()
        if cb.isChecked():
            self.settings.setValue("mv_info_seen", "true")

    def _stop_docked_for_multiview(self) -> None:
        p = getattr(self, "player", None)
        if p is None or not getattr(p, "current_url", None):
            return
        try:
            self.rec.finish_all_inplayer("multiview")
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass
        try:
            self.wake.release()
        except Exception:
            pass
        self._apply_play_icon()

    def _close_multiview(self) -> None:
        win = self._multiview_win
        if win is None:
            return
        self._multiview_win = None
        for c in win.cells:
            c.shutdown()
        win.deleteLater()

    def _close_multiview_if_active(self) -> None:
        if self._multiview_win is not None:
            self._close_multiview()
