"""Detached-player ("pop out") mixin for MainWindow.

Moves the *existing* embedded player widget into its own top-level window and
back, so the video can live on a second screen while the main window stays
fully usable for browsing. Because it reparents the one real player (rather
than spawning a second one), every signal, control and playback-state stays
connected - the playback path is untouched. It reuses the same OpenGL
render-API surface the docked player uses, so it behaves the same on Linux,
macOS and Windows (unlike an mpv-owned window, which fights Qt for the run
loop on macOS).

Kept out of main_window.py to keep that file lean.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from ..i18n import tr


class _PopoutWindow(QWidget):
    """Top-level host for the detached player. Closing it (window X) doesn't
    destroy the player - it hands control back so the widget is reparented into
    the main window first."""

    def __init__(self, on_close) -> None:
        super().__init__()
        self._on_close = on_close
        self.setObjectName("PopoutWindow")
        self.setStyleSheet("#PopoutWindow { background:#000000; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

    def closeEvent(self, event) -> None:
        # Never let the OS close tear down the shared player widget; bounce it
        # back into the main window first, then this window is deleted.
        event.ignore()
        self._on_close()


class _PopoutMixin:
    def _toggle_popout(self) -> None:
        """Move the embedded player into its own window, or back if it's already
        detached."""
        if not self.player:
            return
        if self._popout_win is not None:
            self._exit_popout()
            return
        # Pop-out and PiP/fullscreen are mutually exclusive takes on the same
        # widget - leave those first so only one owns the player at a time.
        if self._pip_win is not None:
            self._exit_pip()
        if self._player_fs:
            self._exit_player_fullscreen()

        win = _PopoutWindow(self._exit_popout)
        win.setWindowTitle(tr("popout_title"))

        # Where the video used to sit in the detail pane, drop a clickable
        # placeholder the same height so the pane doesn't jump, and clicking it
        # brings the player home.
        ph = QPushButton(tr("popout_placeholder"), self._det)
        ph.setCursor(Qt.CursorShape.PointingHandCursor)
        box_h = getattr(self.player, "VIDEO_BOX_HEIGHT", 260)
        ph.setFixedHeight(box_h + 44)
        ph.setStyleSheet(
            "QPushButton { background:#000000; color:#B8B8C0; border:none;"
            " font-size:13px; font-weight:600; }"
            "QPushButton:hover { color:#FFFFFF; }")
        ph.clicked.connect(self._exit_popout)
        self._popout_placeholder = ph
        self._det.layout().insertWidget(0, ph)

        # Reparent the real player into the pop-out window (this removes it from
        # the detail-pane layout). set_popout_mode releases the docked fixed
        # height so the video fills the window.
        win.layout().addWidget(self.player)
        self.player.set_popout_mode(True)
        self.player.pip_btn.hide()   # no PiP while already detached
        self.player.show()

        self._popout_win = win
        win.resize(self._saved_popout_geometry().size())
        win.setGeometry(self._saved_popout_geometry())
        win.show()
        win.raise_()

    def _saved_popout_geometry(self) -> "QRect":
        raw = self.settings.value("popout_geometry", "")
        try:
            x, y, w, h = (int(v) for v in str(raw).split(","))
            if w >= 320 and h >= 200:
                return QRect(x, y, w, h)
        except (ValueError, TypeError):
            pass
        screen_geo = self.screen().availableGeometry()
        w, h = 720, 420
        return QRect(screen_geo.center().x() - w // 2,
                     screen_geo.center().y() - h // 2, w, h)

    def _exit_popout(self) -> None:
        """Bring the detached player back into the main window's detail pane."""
        win = self._popout_win
        if win is None:
            return
        self._popout_win = None
        if win.isFullScreen():
            # Leave the pop-out's own fullscreen first, then measure geometry.
            self.player.set_fullscreen_ui(False)
            g = getattr(self, "_popout_fs_geo", None) or win.geometry()
        else:
            g = win.geometry()
        self.settings.setValue(
            "popout_geometry", f"{g.x()},{g.y()},{g.width()},{g.height()}")

        self.player.set_popout_mode(False)
        self.player.pip_btn.show()
        # Reparent back to the top of the detail pane (stretch 1, as built).
        self._det.layout().insertWidget(0, self.player, 1)
        self.player.show()

        ph = getattr(self, "_popout_placeholder", None)
        if ph is not None:
            self._det.layout().removeWidget(ph)
            ph.deleteLater()
            self._popout_placeholder = None
        win.deleteLater()

    def _toggle_popout_fullscreen(self) -> None:
        """Fullscreen the pop-out window itself (it holds only the player, so
        this is simpler than the main-window fullscreen - no panes to hide)."""
        win = self._popout_win
        if win is None:
            return
        now = time.time()
        if now - getattr(self, "_popout_fs_toggled_at", 0.0) < 0.4:
            return
        self._popout_fs_toggled_at = now
        if win.isFullScreen():
            self.player.set_fullscreen_ui(False)
            win.showNormal()
            geo = getattr(self, "_popout_fs_geo", None)
            if geo:
                win.setGeometry(geo)
        else:
            self._popout_fs_geo = win.geometry()
            self.player.set_fullscreen_ui(True)
            win.showFullScreen()

    def _exit_popout_if_active(self) -> None:
        if self._popout_win is not None:
            self._exit_popout()
