"""Multiview: watch up to four live channels at once in a 2x2 grid.

A separate top-level window holding four lightweight video cells, each its own
libmpv render surface (`_MpvGLWidget`) - the *docked* embedded player is never
touched. Cells are muted by default; click one to focus it (its audio plays,
the others stay muted) with a highlighted border. Channels are added from the
main list's right-click "Add to multiview", capturing whatever playlist is
active at that moment - so four cells can come from four different accounts,
sidestepping a single account's connection limit.

Heads-up for the user: each cell is a separate stream = a separate connection
to the provider. On a low connection-limit account the extra cells will be
refused (the same 458 / "all connections in use" the diagnosis reports).

Kept out of main_window.py to keep that file lean.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QGridLayout, QLabel, QVBoxLayout, QWidget)

from ..core.log import log
from ..i18n import tr
from ..media.embedded import _MpvGLWidget


class _MultiviewCell(QWidget):
    """One grid cell: a bare mpv video surface plus a title strip and a
    click-to-focus border. No transport controls - multiview is glanceable."""

    focus_requested = pyqtSignal(object)   # emits self on click
    hovered = pyqtSignal(object)           # emits self on mouse move over it

    def __init__(self, number: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.number = number               # 1..4, its position label
        self.url: str | None = None
        self.title: str = ""
        self._focused = False
        self.setStyleSheet("background:#000000;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.video = _MpvGLWidget(self)
        self.video.setMinimumSize(0, 0)   # free to shrink in the grid
        lay.addWidget(self.video)
        self.video.video_mouse_press.connect(
            lambda _e: self.focus_requested.emit(self))
        self.video.video_mouse_move.connect(
            lambda _e: self.hovered.emit(self))
        self.video.playback_error.connect(self._on_error)
        # Title strip over the top of the video.
        self._title = QLabel("", self.video)
        self._title.setStyleSheet(
            "background:rgba(0,0,0,150); color:#ECECF1; padding:3px 8px;"
            "font-size:12px; font-weight:600;")
        self._title.hide()
        # Empty-state hint, centred.
        self._empty = QLabel(tr("mv_empty_cell"), self.video)
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet("color:#7A7A85; font-size:12px;")
        # Big position number, revealed on hover (so you can pick a target
        # cell from the list's "Add to multiview" submenu), then fades away.
        self._num = QLabel(str(number), self.video)
        self._num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._num.setStyleSheet(
            "background:transparent; color:rgba(255,255,255,140);"
            "font-size:56px; font-weight:800;")
        self._num.hide()
        self.set_focused(False)

    def show_number(self, on: bool) -> None:
        self._num.setVisible(on)
        if on:
            self._num.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._title.adjustSize()
        self._title.move(0, 0)
        self._empty.setGeometry(self.video.rect())
        self._num.setGeometry(self.video.rect())

    def play(self, url: str, title: str) -> bool:
        """Load a stream into this cell. Mirrors the docked player's essential
        mpv setup (title/cache/mute) without any of its chrome."""
        self.url = url
        self.title = title
        self._title.setText(title or "")
        self._title.setVisible(bool(title))
        self._title.adjustSize()
        self._empty.hide()
        if self.video.mpv is None:
            # Force GL init so the render context (and mpv) exists.
            self.video.show()
            QApplication.instance().processEvents()
        m = self.video.mpv
        if m is None:
            return False
        try:
            m["force-media-title"] = title or ""
            m["cache"] = "yes"
            m["mute"] = not self._focused
            m.play(url)
            return True
        except Exception as e:
            log.warning("multiview cell play failed: %s: %s",
                        type(e).__name__, e)
            return False

    def _on_error(self, _msg: str) -> None:
        self._title.setText(tr("mv_cell_error", title=self.title or ""))
        self._title.setVisible(True)
        self._title.adjustSize()

    def set_focused(self, on: bool) -> None:
        self._focused = on
        if self.video.mpv is not None and self.url is not None:
            try:
                self.video.mpv["mute"] = not on
            except Exception:
                pass
        self.setStyleSheet(
            "background:#000000;"
            + ("border:2px solid #e5354b;" if on else "border:2px solid #202028;"))

    def clear(self) -> None:
        if self.video.mpv is not None:
            try:
                self.video.mpv.command("stop")
            except Exception:
                pass
        self.video.set_blank(True)
        self.url = None
        self.title = ""
        self._title.hide()
        self._empty.show()

    def shutdown(self) -> None:
        try:
            self.video.shutdown()
        except Exception:
            pass


class _MultiviewWindow(QWidget):
    """Top-level 2x2 grid of cells. Closing it hands control back to the owner
    so every cell's mpv is torn down cleanly."""

    def __init__(self, owner) -> None:
        super().__init__()
        self._owner = owner
        self.setWindowTitle(tr("mv_title"))
        self.setStyleSheet("background:#000000;")
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        # Cells numbered 1..4 in reading order: 1 top-left, 2 top-right,
        # 3 bottom-left, 4 bottom-right.
        self.cells: list[_MultiviewCell] = []
        for i in range(4):
            c = _MultiviewCell(i + 1, self)
            c.focus_requested.connect(self._focus_cell)
            c.hovered.connect(lambda _c: self._reveal_numbers())
            grid.addWidget(c, i // 2, i % 2)
            self.cells.append(c)
        self._focused: _MultiviewCell | None = None
        # Position numbers flash on hover, then fade after a moment.
        self._num_timer = QTimer(self)
        self._num_timer.setSingleShot(True)
        self._num_timer.setInterval(1400)
        self._num_timer.timeout.connect(self._hide_numbers)
        self._focus_cell(self.cells[0])

    def add_stream(self, url: str, title: str, cell: int | None = None) -> None:
        """Play a stream in cell index *cell* (0..3); if None, the first free
        cell, else the focused one."""
        if cell is not None and 0 <= cell < len(self.cells):
            target = self.cells[cell]
        else:
            target = next((c for c in self.cells if c.url is None),
                          self._focused or self.cells[0])
        target.play(url, title)
        self._focus_cell(target)   # newly added cell takes audio focus

    def _reveal_numbers(self) -> None:
        for c in self.cells:
            c.show_number(True)
        self._num_timer.start()

    def _hide_numbers(self) -> None:
        for c in self.cells:
            c.show_number(False)

    def _focus_cell(self, cell: "_MultiviewCell") -> None:
        for c in self.cells:
            c.set_focused(c is cell)
        self._focused = cell

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

    def add_to_multiview(self, url: str, title: str,
                         cell: int | None = None) -> None:
        """Open the multiview window (if needed) and drop a stream into it."""
        if not url:
            return
        # Free the docked player's connection: otherwise a single-connection
        # account refuses the multiview cell (the same channel then only plays
        # docked), and you'd have two streams and two audio tracks at once.
        self._stop_docked_for_multiview()
        if self._multiview_win is None:
            self._multiview_win = _MultiviewWindow(self)
            self._multiview_win.resize(960, 560)
        self._multiview_win.add_stream(url, title, cell)
        self._multiview_win.show()
        self._multiview_win.raise_()

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
        self._apply_play_icon()   # poster overlay back to a 'play' glyph

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
