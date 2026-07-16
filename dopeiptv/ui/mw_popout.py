"""Detached-player ("pop out") mixin for MainWindow.

Moves the *existing* embedded player widget into its own top-level window and
back, so the video can live on a second screen while the main window stays
fully usable for browsing. Because it reparents the one real player (rather
than spawning a second one), every signal, control and playback-state stays
connected - the playback path is untouched. It reuses the same OpenGL
render-API surface the docked player uses, so it behaves the same on Linux,
macOS and Windows (unlike an mpv-owned window, which fights Qt for the run
loop on macOS).

Right-clicking the detached video offers: keep it above other windows
(replaces the old PiP always-on-top), hide the title bar (then drag the video
itself to move it), or dock it back. Escape leaves the pop-out's fullscreen.

Kept out of main_window.py to keep that file lean.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtWidgets import (
    QApplication, QMenu, QPushButton, QVBoxLayout, QWidget)

from ..i18n import tr


class _PopoutWindow(QWidget):
    """Top-level host for the detached player.

    Closing it (window X) doesn't destroy the shared player - it hands control
    back to the owner so the widget is reparented into the main window first.
    Escape leaves fullscreen (the exit shortcut lives on the main window, which
    isn't focused while this window is up)."""

    def __init__(self, owner) -> None:
        super().__init__()
        self._owner = owner
        self.setObjectName("PopoutWindow")
        self.setStyleSheet("#PopoutWindow { background:#000000; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

    def closeEvent(self, event) -> None:
        event.ignore()
        self._owner._exit_popout()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._owner._popout_escape()
            event.accept()
            return
        super().keyPressEvent(event)


class _PopoutMixin:
    def _toggle_popout(self) -> None:
        """Move the embedded player into its own window, or back if it's already
        detached."""
        if not self.player:
            return
        if self._popout_win is not None:
            self._exit_popout()
            return
        # Pop-out and fullscreen are mutually exclusive takes on the same
        # widget - leave fullscreen first so only one owns the player.
        if self._player_fs:
            self._exit_player_fullscreen()

        win = _PopoutWindow(self)
        win.setWindowTitle(tr("popout_title"))
        self._popout_win = win
        win.setWindowFlags(self._popout_flags())   # restore on-top / frameless

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
        self.player.set_popout_autohide(
            self.settings.value("popout_autohide", "true") == "true")
        self.player.show()

        win.setGeometry(self._saved_popout_geometry())
        win.show()
        win.raise_()

    def _popout_flags(self) -> "Qt.WindowType":
        """Window flags from the saved right-click choices. Wayland can't pin a
        client's stacking, so always-on-top is dropped there."""
        flags = Qt.WindowType.Window
        wayland = "wayland" in QApplication.platformName().lower()
        if (self.settings.value("popout_on_top", "false") == "true"
                and not wayland):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        # Frameless (no title bar) by default - a clean video window. Drag the
        # video to move it, right-click to show the title bar or dock it back.
        if self.settings.value("popout_frameless", "true") == "true":
            flags |= Qt.WindowType.FramelessWindowHint
        return flags

    def _apply_popout_flags(self) -> None:
        win = self._popout_win
        if win is None:
            return
        geo = win.geometry()
        win.setWindowFlags(self._popout_flags())
        win.setGeometry(geo)       # setWindowFlags can drop the geometry
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
        # Reparent back to the top of the detail pane (stretch 1, as built).
        self._det.layout().insertWidget(0, self.player, 1)
        self.player.show()

        ph = getattr(self, "_popout_placeholder", None)
        if ph is not None:
            self._det.layout().removeWidget(ph)
            # Detach from the detail pane *synchronously* (not just deleteLater):
            # fullscreen entry scans self._det.children() and would otherwise
            # grab this not-yet-deleted placeholder, hide it, then crash on
            # w.show() once deleteLater finally removes it.
            ph.hide()
            ph.setParent(None)
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

    def _popout_escape(self) -> None:
        """Escape in the pop-out window leaves its fullscreen (does not dock)."""
        if self._popout_win is not None and self._popout_win.isFullScreen():
            self._toggle_popout_fullscreen()

    def _exit_popout_if_active(self) -> None:
        if self._popout_win is not None:
            self._exit_popout()

    def _popout_context_menu(self, global_pos) -> None:
        """Right-click menu on the detached video: keep it above other windows,
        hide the title bar (then drag the video to move it), or dock it back.
        Parented to the pop-out window, not the main window, so opening it never
        raises or leaks clicks to the window behind."""
        if self._popout_win is None:
            return
        m = QMenu(self._popout_win)
        # "Always on top" only where the client can actually set its stacking
        # (X11/XWayland/Windows/macOS). Native Wayland ignores the hint, so
        # point the user at the compositor's own title-bar menu instead.
        if "wayland" not in QApplication.platformName().lower():
            act = m.addAction(tr("popout_always_on_top"))
            act.setCheckable(True)
            act.setChecked(bool(self._popout_win.windowFlags()
                                & Qt.WindowType.WindowStaysOnTopHint))
            act.toggled.connect(self._set_popout_on_top)
        else:
            hint = m.addAction(tr("popout_wayland_hint"))
            hint.setEnabled(False)
        frameless = bool(self._popout_win.windowFlags()
                         & Qt.WindowType.FramelessWindowHint)
        bar = m.addAction(tr("popout_show_titlebar") if frameless
                          else tr("popout_hide_titlebar"))
        bar.triggered.connect(lambda: self._set_popout_frameless(not frameless))
        auto = m.addAction(tr("popout_autohide_controls"))
        auto.setCheckable(True)
        auto.setChecked(
            self.settings.value("popout_autohide", "true") == "true")
        auto.toggled.connect(self._set_popout_autohide)
        m.addSeparator()
        m.addAction(tr("tooltip_popout_exit"), self._exit_popout)
        m.exec(global_pos)

    def _set_popout_on_top(self, on: bool) -> None:
        self.settings.setValue("popout_on_top", "true" if on else "false")
        self._apply_popout_flags()

    def _set_popout_frameless(self, hidden: bool) -> None:
        self.settings.setValue("popout_frameless", "true" if hidden else "false")
        self._apply_popout_flags()

    def _set_popout_autohide(self, on: bool) -> None:
        self.settings.setValue("popout_autohide", "true" if on else "false")
        if self.player:
            self.player.set_popout_autohide(on)
