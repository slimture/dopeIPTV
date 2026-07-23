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

import sys
import time

from PyQt6.QtCore import QRect, QSize, Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication, QMenu, QPushButton, QVBoxLayout, QWidget)

from ..i18n import tr
from .widgets import exec_menu_over_video


def _use_mirror_popout() -> bool:
    """Whether to render the pop-out via a MIRROR surface instead of
    reparenting the real GL widget.

    macOS and Windows both share GL contexts across top-levels
    (AA_ShareOpenGLContexts, required by the mpv render API). On macOS this
    makes reparenting a QOpenGLWidget between windows present a stale layer
    (frozen picture); Windows shares contexts the same way and is expected to
    behave the same, so both take the mirror path. Linux destroys and rebuilds
    the context on reparent, so the plain reparent path stays there.
    """
    return sys.platform in ("darwin", "win32")


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

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Keep the centre disc centred and the floating overlays anchored to
        # the mirror as the window is resized or maximised.
        self._owner._reposition_popout_center()
        self._owner._reposition_popout_overlays()


class _PopoutMixin:
    """MainWindow mixin: detaching the player into a separate always-on-top window."""
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

        if _use_mirror_popout():
            # macOS/Windows cannot reparent a QOpenGLWidget between windows
            # without the picture freezing (a stale shared-context layer).
            # Mirror the stream into the pop-out window instead of moving the
            # player. (Named _popout_macos for its origin; also used on win32.)
            self._popout_macos()
            return

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

    def _popout_macos(self) -> None:
        """macOS pop-out via a mirror surface (no reparent). The pop-out window
        hosts a second GL surface that renders the same live mpv stream; the
        real player stays docked, its video covered by a placeholder. Controls
        come from the video itself: click = pause, double-click = fullscreen,
        drag = move the window, right-click = the full options menu."""
        win = _PopoutWindow(self)
        win.setWindowTitle(tr("popout_title"))
        self._popout_win = win
        win.setWindowFlags(self._popout_flags())
        mirror = self.player.start_mirror(win)
        self._popout_mirror = mirror
        win.layout().addWidget(mirror, 1)
        # Bring the player's real control bar into the pop-out below the video
        # (it's a plain widget row - safe to reparent, unlike the GL surface).
        # Its buttons act on the shared stream; pop-out ⧉ docks back and ⛶
        # already routes to the pop-out's own fullscreen when _popout_win is
        # set, so nothing needs rewiring.
        win.layout().addWidget(self.player.bar)
        self.player.bar.show()
        # Auto-hide the bar after a few idle seconds (revealed again on pointer
        # movement over the mirror), per the same setting the docked pop-out
        # uses. The mirror path now counts as a pop-out context, so the timer
        # actually runs.
        self.player.set_popout_autohide(
            self.settings.value("popout_autohide", "true") == "true")
        mirror.video_dbl_click.connect(self._on_mirror_dbl_click)
        mirror.video_mouse_press.connect(self._on_mirror_press)
        mirror.video_mouse_move.connect(self._on_mirror_move)
        mirror.video_mouse_release.connect(self._on_mirror_release)
        # A click toggles pause, but only after the double-click interval so a
        # double-click (fullscreen) can cancel it - otherwise a double-click
        # both paused AND maximised.
        self._mirror_click_timer = QTimer(self)
        self._mirror_click_timer.setSingleShot(True)
        self._mirror_click_timer.setInterval(QApplication.doubleClickInterval())
        self._mirror_click_timer.timeout.connect(self._popout_toggle_pause)
        # Visible control: a centre play/pause disc revealed on pointer
        # movement (matches the docked player); the right-click menu carries
        # the rest. A child of the pop-out window - the mirror surface is never
        # reparented, so an overlay here is safe.
        btn = QPushButton(win)
        btn.setObjectName("CenterPlay")
        btn.setFixedSize(72, 72)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            "#CenterPlay { background: rgba(16,16,20,140); border:none;"
            " border-radius:36px; }"
            "#CenterPlay:hover { background: rgba(16,16,20,200); }")
        btn.clicked.connect(self._popout_toggle_pause)
        btn.hide()
        self._popout_center = btn
        self._popout_center_timer = QTimer(self)
        self._popout_center_timer.setSingleShot(True)
        self._popout_center_timer.setInterval(2500)
        self._popout_center_timer.timeout.connect(self._maybe_hide_popout_center)
        # Blank the mouse cursor after a short idle over the video (windowed or
        # fullscreen), like a real player. Re-armed on every mirror move.
        self._popout_cursor_hidden = False
        self._popout_cursor_timer = QTimer(self)
        self._popout_cursor_timer.setSingleShot(True)
        self._popout_cursor_timer.setInterval(2000)
        self._popout_cursor_timer.timeout.connect(self._hide_popout_cursor)
        win.setGeometry(self._saved_popout_geometry())
        win.show()
        win.raise_()
        mirror.show()
        self._reveal_popout_center()   # a first hint that it's interactive
        self._popout_cursor_timer.start()

    def _popout_toggle_pause(self) -> None:
        self.player.toggle_pause()
        self._reveal_popout_center()

    def _reveal_popout_center(self) -> None:
        btn = getattr(self, "_popout_center", None)
        win = self._popout_win
        if btn is None or win is None:
            return
        from ..media.embedded import _control_icon
        paused = getattr(self.player, "_paused", False)
        btn.setIcon(_control_icon("play" if paused else "pause", "#FFFFFF", 30))
        btn.setIconSize(QSize(30, 30))
        btn.move((win.width() - btn.width()) // 2,
                 (win.height() - btn.height()) // 2)
        btn.show()
        btn.raise_()
        # While paused it stays up; while playing it fades after a short idle.
        if paused:
            self._popout_center_timer.stop()
        else:
            self._popout_center_timer.start()

    def _maybe_hide_popout_center(self) -> None:
        btn = getattr(self, "_popout_center", None)
        if btn is not None and not getattr(self.player, "_paused", False):
            btn.hide()

    def _popout_cursor_activity(self) -> None:
        """A mirror move: restore the cursor and re-arm the idle-hide."""
        self._show_popout_cursor()
        t = getattr(self, "_popout_cursor_timer", None)
        if t is not None:
            t.start()

    def _hide_popout_cursor(self) -> None:
        """Blank the cursor after the idle timeout - but only while it rests
        over the video, so it never vanishes over the control bar."""
        m = getattr(self, "_popout_mirror", None)
        win = self._popout_win
        if m is None or win is None or not m.underMouse():
            return
        if sys.platform == "darwin":
            from ..core.platform_macos import set_cursor_hidden
            set_cursor_hidden(True)
        else:
            win.setCursor(Qt.CursorShape.BlankCursor)
            m.setCursor(Qt.CursorShape.BlankCursor)
        self._popout_cursor_hidden = True

    def _show_popout_cursor(self) -> None:
        if not getattr(self, "_popout_cursor_hidden", False):
            return
        if sys.platform == "darwin":
            from ..core.platform_macos import set_cursor_hidden
            set_cursor_hidden(False)
        else:
            win = self._popout_win
            m = getattr(self, "_popout_mirror", None)
            if win is not None:
                win.unsetCursor()
            if m is not None:
                m.unsetCursor()
        self._popout_cursor_hidden = False

    def _reposition_popout_center(self) -> None:
        btn = getattr(self, "_popout_center", None)
        win = self._popout_win
        if btn is None or win is None or btn.isHidden():
            return
        btn.move((win.width() - btn.width()) // 2,
                 (win.height() - btn.height()) // 2)
        btn.raise_()

    def _reposition_popout_overlays(self) -> None:
        """Re-anchor the floating overlays to the mirror after a pop-out window
        resize (they're reparented into this window while popped out)."""
        if getattr(self, "_popout_mirror", None) is None:
            return
        p = self.player
        if p.seek_overlay is not None and p.seek_overlay.isVisible():
            p._place_seek_overlay()
        if p.ts_timeline.isVisible():
            p._place_ts_timeline()
        if p._stats_overlay.isVisible():
            p._place_stats()

    def _on_mirror_press(self, event) -> None:
        # Accept the event so a right-click can't leak through the frameless
        # window to whatever sits behind it (the "bleed-through").
        event.accept()
        if event.button() == Qt.MouseButton.RightButton:
            self._popout_context_menu(event.globalPosition().toPoint())
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._mirror_press_pos = event.position().toPoint()

    def _on_mirror_move(self, event) -> None:
        frm = getattr(self, "_mirror_press_pos", None)
        if frm is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            if (event.position().toPoint() - frm).manhattanLength() > 6:
                self._mirror_press_pos = None   # became a drag, not a click
                win = self._popout_win
                handle = win.windowHandle() if win is not None else None
                if handle is not None:
                    handle.startSystemMove()
            return
        # Idle pointer over the video flashes the centre play/pause disc and
        # the seek bar / timeshift timeline (whichever applies), and brings the
        # cursor back.
        self._popout_cursor_activity()
        self._reveal_popout_center()
        self.player.reveal_pop_overlays()

    def _on_mirror_release(self, event) -> None:
        # A plain click (no drag) toggles pause - but deferred by the
        # double-click interval so a double-click (fullscreen) cancels it.
        if (event.button() == Qt.MouseButton.LeftButton
                and getattr(self, "_mirror_press_pos", None) is not None):
            self._mirror_press_pos = None
            self._mirror_click_timer.start()

    def _on_mirror_dbl_click(self) -> None:
        # Cancel the pending single-click pause, then toggle fullscreen. When
        # this window IS the mini-player's maximize (fs-via-popout), a
        # double-click leaves fullscreen by docking straight back.
        t = getattr(self, "_mirror_click_timer", None)
        if t is not None:
            t.stop()
        if getattr(self, "_fs_via_popout", False):
            self._exit_player_fullscreen()
        else:
            self._toggle_popout_fullscreen()

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
        # Any dock-back clears the mini-maximize flag (dock via context menu /
        # placeholder click too), so a later maximize starts clean.
        self._fs_via_popout = False

        if getattr(self, "_popout_mirror", None) is not None:
            # macOS mirror path: no reparent to undo - just tear down the
            # mirror surface and hand the picture back to the docked player.
            g = (getattr(self, "_popout_fs_geo", None) or win.geometry()) \
                if win.isFullScreen() else win.geometry()
            self.settings.setValue(
                "popout_geometry", f"{g.x()},{g.y()},{g.width()},{g.height()}")
            for tname in ("_popout_center_timer", "_mirror_click_timer",
                          "_popout_cursor_timer"):
                t = getattr(self, tname, None)
                if t is not None:
                    t.stop()
            self._show_popout_cursor()   # never leave the cursor hidden
            self._popout_center = None       # deleted with the window below
            # Return the control bar to the docked player (bottom of its vbox,
            # after the video) before the pop-out window is destroyed.
            self.player.layout().addWidget(self.player.bar)
            self.player.set_popout_autohide(False)   # docked: always visible
            self.player.bar.show()
            self.player.stop_mirror()
            self._popout_mirror = None
            win.deleteLater()
            return

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
        # macOS mirror pop-out: the mirror just fills the window, so fullscreen
        # is only a window-state change - don't touch the DOCKED player's
        # fullscreen UI (that hides its bar and would fight the docked view).
        mac_mirror = getattr(self, "_popout_mirror", None) is not None
        if win.isFullScreen():
            if not mac_mirror:
                self.player.set_fullscreen_ui(False)
            win.showNormal()
            geo = getattr(self, "_popout_fs_geo", None)
            if geo:
                win.setGeometry(geo)
        else:
            self._popout_fs_geo = win.geometry()
            if not mac_mirror:
                self.player.set_fullscreen_ui(True)
            win.showFullScreen()
        # Reset the cursor visible and re-arm the idle-hide across the
        # transition, so it neither sticks hidden nor stays up forever.
        if mac_mirror:
            self._popout_cursor_activity()

    def _popout_escape(self) -> None:
        """Escape in the pop-out window leaves its fullscreen. For a real
        pop-out that just un-fullscreens (stays floating); for the macOS mini
        maximize (fs-via-popout) it docks straight back to the mini player."""
        if self._popout_win is None:
            return
        if getattr(self, "_fs_via_popout", False):
            self._exit_player_fullscreen()
        elif self._popout_win.isFullScreen():
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
        # Full playback options (pause/stop + audio/subtitles/aspect/…), same
        # as the docked right-click and the options button.
        p = getattr(self, "player", None)
        if p is not None and getattr(p, "current_url", None):
            m.addSeparator()
            paused = getattr(p, "_paused", False)
            m.addAction(tr("btn_play") if paused else tr("btn_pause"),
                        p.toggle_pause)
            m.addAction(tr("btn_stop"), p.stop)
            opts = m.addMenu(tr("tooltip_audio_subs_aspect"))
            p.populate_options_menu(opts)
        m.addSeparator()
        m.addAction(tr("tooltip_popout_exit"), self._exit_popout)
        exec_menu_over_video(m, global_pos)

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
