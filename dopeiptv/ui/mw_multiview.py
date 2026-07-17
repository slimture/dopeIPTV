"""Multiview: watch up to four live channels at once in a 2x2 grid.

A separate top-level window holding four lightweight video cells, each its own
libmpv render surface (`_MpvGLWidget`) - the *docked* embedded player is never
touched. Click a cell to give it audio focus (the others stay muted); click the
focused one again, or right-click, to mute it. Channels are added from the
main list's right-click "Add to multiview", capturing whatever playlist is
active at that moment - so four cells can come from four different accounts,
sidestepping a single account's connection limit.

The window mirrors the pop-out player's chrome options: title-bar-less by
default (drag a cell to move it), with right-click "Always on top" and
"Show title bar". Cell titles and position numbers auto-hide and reappear on
mouse movement. Space pauses the focused cell; Left/Right seek it.

Heads-up for the user: each cell is a separate stream = a separate connection
to the provider. On a low connection-limit account the extra cells will be
refused (the same 458 / "all connections in use" the diagnosis reports).

Kept out of main_window.py to keep that file lean.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialogButtonBox, QGridLayout, QLabel, QMenu,
    QVBoxLayout, QWidget)

from ..core.log import log
from ..i18n import tr
from ..media.embedded import _MpvGLWidget


class _MultiviewCell(QWidget):
    """One grid cell: a bare mpv video surface with a position number, an
    auto-hiding title strip, click-to-focus, and per-cell mute/pause/seek."""

    focus_requested = pyqtSignal(object)         # left-click: focus/mute toggle
    context_requested = pyqtSignal(object, object)   # right-click: (cell, pos)
    hovered = pyqtSignal(object)                 # mouse move: reveal overlays

    def __init__(self, number: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.number = number
        self.url: str | None = None
        self.title: str = ""
        self._focused = False
        self._muted = True
        self._drag_from = None
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
        self.set_focused(False)

    # -- overlays (title + number auto-hide together) ------------------------

    def show_overlays(self, on: bool) -> None:
        self._num.setVisible(on)
        self._title.setVisible(on and bool(self.title))
        if on:
            self._num.raise_()
            self._title.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._title.adjustSize()
        self._title.move(0, 0)
        self._empty.setGeometry(self.video.rect())
        self._num.setGeometry(self.video.rect())

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
        self._empty.show()

    def shutdown(self) -> None:
        try:
            self.video.shutdown()
        except Exception:
            pass

    # -- mouse: left = focus, right = menu, drag = move (frameless) ----------

    def _window_frameless(self) -> bool:
        return bool(self.window().windowFlags()
                    & Qt.WindowType.FramelessWindowHint)

    def _on_press(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.context_requested.emit(
                self, event.globalPosition().toPoint())
            return
        if event.button() == Qt.MouseButton.LeftButton:
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
        self.setStyleSheet("#MvWin { background:#000000; }")
        self.setObjectName("MvWin")
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        # Cells numbered 1..4 in reading order: 1 top-left, 2 top-right,
        # 3 bottom-left, 4 bottom-right.
        self.cells: list[_MultiviewCell] = []
        for i in range(4):
            c = _MultiviewCell(i + 1, self)
            c.focus_requested.connect(self._on_cell_clicked)
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
        self.setWindowFlags(self._flags())
        self._focus_cell(self.cells[0])

    # -- window flags (mirrors the pop-out chrome options) -------------------

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

    # -- streams / focus / mute ---------------------------------------------

    def add_stream(self, url: str, title: str, cell: int | None = None) -> None:
        if cell is not None and 0 <= cell < len(self.cells):
            target = self.cells[cell]
        else:
            target = next((c for c in self.cells if c.url is None),
                          self._focused or self.cells[0])
        target.play(url, title)
        self._focus_cell(target)
        self._reveal_overlays()

    def _on_cell_clicked(self, cell: "_MultiviewCell") -> None:
        # Click the already-focused cell again to mute/unmute it; click another
        # to move audio focus there.
        if cell is self._focused and cell.url is not None:
            cell.set_muted(not cell.is_muted())
        else:
            self._focus_cell(cell)

    def _focus_cell(self, cell: "_MultiviewCell") -> None:
        for c in self.cells:
            c.set_focused(c is cell)
            c.set_muted(c is not cell)
        self._focused = cell

    def _cell_context_menu(self, cell: "_MultiviewCell", pos) -> None:
        m = QMenu(self)
        if cell.url is not None:
            m.addAction(tr("mv_unmute") if cell.is_muted() else tr("mv_mute"),
                        lambda: cell.set_muted(not cell.is_muted()))
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

    # -- overlays + keyboard -------------------------------------------------

    def _reveal_overlays(self) -> None:
        for c in self.cells:
            c.show_overlays(True)
        self._overlay_timer.start()

    def _hide_overlays(self) -> None:
        for c in self.cells:
            c.show_overlays(False)

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
            # There's no title-bar X when frameless, so give Escape as a close.
            self.close()
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
        msg = QLabel(tr("mv_info_body"))
        msg.setWordWrap(True)
        msg.setMinimumWidth(420)
        lay.addWidget(msg)
        cb = QCheckBox(tr("dont_show_again"))
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
