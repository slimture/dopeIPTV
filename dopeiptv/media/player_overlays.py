"""What floats over the picture, as the player shows it.

The big play/pause disc in the centre, the docked hover scrubber, the
TIMESHIFT pill, which seek bar a stream uses (and a catch-up programme's
virtual window), the "Next episode" card over an episode's last stretch,
and fullscreen's controls and info text with the pointer hidden while
idle. Each is a sibling of the video, never a child of the GL surface,
placed against the surface that has the picture (the docked video, or the
pop-out's mirror).

Mixed into EmbeddedPlayer; it reads the player's attributes.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QSize, Qt, QTimer
from PyQt6.QtWidgets import QApplication

from ..core.log import log
from ..i18n import tr
from .control_icons import _control_icon


class PlayerOverlaysMixin:
    # -- centre play/pause button ---------------------------------------------

    def _position_center_btn(self) -> None:
        b = self.center_btn
        b.move((self.width() - b.width()) // 2,
               (self.height() - b.height()) // 2)

    def _update_center_icon(self) -> None:
        paused = bool(getattr(self, "_paused", False))
        # Single source of truth for "which glyph is on the button" - _reveal_
        # center reads it to know whether the icon needs redrawing at all.
        self._center_icon_paused = paused
        glyph = "play" if paused else "pause"
        self.center_btn.setIcon(_control_icon(glyph, "#FFFFFF", 30))
        self.center_btn.setIconSize(QSize(30, 30))

    def _reveal_center(self) -> None:
        """Show the centre button and, while playing, arm its fade. While
        paused it stays up until the user resumes.

        This runs on EVERY pointer event over the video, so it must do nothing
        when nothing changed. Redrawing the glyph, re-placing and re-raising
        the button on each event dirtied the region under it as fast as the
        mouse reports - and every one of those repaints costs a slice of a
        video blit, which is why dragging the pointer across a maximized
        video felt sluggish. Placement is handled by the resize path."""
        if getattr(self, "current_url", None) is None:
            self.center_btn.hide()
            return
        paused = bool(getattr(self, "_paused", False))
        if not paused and self.loading_shown():
            return      # the loading indicator has the centre
        if paused is not getattr(self, "_center_icon_paused", None):
            self._update_center_icon()
        if not self.center_btn.isVisible():
            self._position_center_btn()
            self.center_btn.show()
            self.center_btn.raise_()
        if paused:
            self._center_hide_timer.stop()
        else:
            self._center_hide_timer.start()

    def _maybe_hide_center(self) -> None:
        # Keep it up while paused; otherwise let it fade.
        if not getattr(self, "_paused", False):
            self.center_btn.hide()

    # -- docked hover scrubber -------------------------------------------------

    def _show_seek_overlay(self) -> None:
        # No VOD seek overlay for plain live (can't seek) or timeshift-edge
        # (the live timeline is the control) - avoids a second, useless bar.
        if self._seek_mode in ("live", "timeline"):
            return
        # Already up - the pointer is simply still moving. Only re-arm the
        # fade: re-placing (a layout pass) and re-raising it on every pointer
        # event repainted the video underneath hundreds of times a second.
        if not self.seek_overlay.isVisible():
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
        vg = self._ov_surface.geometry()
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
        b.setText("⧗ " + tr("player_timeshift"))
        b.setStyleSheet(
            "background: rgba(0,0,0,150); color: #FFFFFF;"
            "border-radius: 9px; padding: 3px 9px;"
            "font-size: 11px; font-weight: 700;")
        b.adjustSize()
        tl = self.video.geometry().topLeft()
        b.move(tl.x() + 12, tl.y() + 12)
        b.show()
        b.raise_()

    def set_seek_mode(self, mode: str) -> None:
        """Pick which seek UI this stream uses (see _seek_mode). Hides the VOD
        seek overlay for live/timeline so only one bar is ever shown."""
        self._seek_mode = mode
        if mode != "timeline":
            self.exit_timeshift()
        if mode != "program":
            self._program_window = 0.0
            self._program_base = 0.0
        if mode in ("live", "timeline"):
            self._hide_seek_ui()
        elif mode == "program" and self._fs_ui:
            # A program-scrub re-load runs play() -> _hide_seek_ui(); re-show the
            # fullscreen scrubber right away so the full-width bar isn't left as
            # an empty gap until the next position poll.
            for w in self._seek_widgets():
                w.setVisible(True)

    def set_program_window(self, secs: float, base: float = 0.0) -> None:
        """In 'program' mode, present the seek bar as the whole picked programme
        (*secs* long), with the currently loaded archive segment beginning
        *base* seconds into it. The provider's archive stream usually runs on
        past the programme to the live edge - and in-stream seeking on it snaps
        to live - so the bar is a *virtual* timeline: scrubbing it re-loads the
        archive at the chosen offset (program_seek) instead of seeking mpv."""
        self._program_window = max(0.0, float(secs or 0.0))
        self._program_base = max(0.0, float(base or 0.0))

    def set_overlay_info(self, text: str) -> None:
        self._overlay_text = text or ""
        if self._fs_ui and self.overlay.isVisible():
            self.overlay.setText(self._overlay_text)
            self._place_overlay()

    # How close to the end the "Next episode" card appears, in seconds.
    # The user's choice, from Settings > Playback; 0 turns the card off.
    #
    # Not computed here any more. It was a fixed minute, then six percent of
    # the runtime capped at three minutes, and the answer both times was
    # "still too late" - because where the credits start is a property of
    # the show, not of its length, and nothing in the file says where.
    NEXTUP_DEFAULT_SECS = 180
    # How far the card sits from the corner of the picture. Hard into the
    # corner reads as a system control rather than an offer.
    NEXTUP_INSET = 28

    def _nextup_window(self, dur: float) -> float:
        """Seconds before the end at which the card appears, or 0 for off."""
        try:
            v = int(self._settings.value(
                "nextup_lead_secs", self.NEXTUP_DEFAULT_SECS)) \
                if self._settings else self.NEXTUP_DEFAULT_SECS
        except (TypeError, ValueError):
            v = self.NEXTUP_DEFAULT_SECS
        # Never past the start of what it is offering to skip.
        return max(0, min(v, dur * 0.5)) if dur > 0 else 0

    def set_next_available(self, available: bool) -> None:
        """Show/hide the 'next episode' button in both control bars. The app
        calls this when it starts a stream, based on whether a next episode is
        queued in the current series."""
        self._next_available = bool(available)
        for b in (getattr(self, "nextep_btn", None),
                  getattr(self, "fs_nextep_btn", None)):
            if b is not None:
                b.setVisible(available)
        if not available:
            self._hide_nextup()

    def _on_nextup_clicked(self) -> None:
        self._hide_nextup()
        self.next_episode.emit()

    def _hide_nextup(self) -> None:
        b = getattr(self, "nextup_btn", None)
        if b is not None and not b.isHidden():
            b.hide()

    def _update_nextup(self, pos: float, dur: float) -> None:
        """Show the card over the closing stretch of an episode.

        Only where there IS a next episode and the length is known, so a
        live channel (no duration) and the last episode of a series never
        show it. Seeking back out of the window takes it away again."""
        b = getattr(self, "nextup_btn", None)
        if b is None:
            return
        left = (dur - pos) if (dur and dur > 0 and pos >= 0) else -1
        if not (self._next_available
                and 0 < left <= self._nextup_window(dur)):
            self._hide_nextup()
            return
        if b.isHidden():
            b.adjustSize()
            b.show()
        b.raise_()
        self._place_nextup()

    def _place_nextup(self) -> None:
        """Bottom-right of whatever surface is showing the picture - the
        docked video, or the mirror while popped out.

        Mapped rather than read off geometry(): the card and the surface do
        not always share a parent (docked the card sits on the player, in
        the pop-out on the pop-out window), and geometry() is relative to
        the SURFACE's parent. Reading it raw put the card adrift in the
        middle of the picture."""
        b = self.nextup_btn
        surf = self._ov_surface
        b.adjustSize()
        par = b.parentWidget()
        try:
            tl = surf.mapTo(par, QPoint(0, 0)) if par is not None \
                else surf.geometry().topLeft()
        except (RuntimeError, TypeError):
            tl = surf.geometry().topLeft()
        # Clear whatever floats over the bottom of the picture, and nothing
        # more. Only fullscreen has anything there - the control bar - and the
        # flat 88 px was sized for that case, so docked and popped out the card
        # rode a bar's height too high over empty picture. The bar's sizeHint
        # rather than its height, because it auto-hides after a few idle
        # seconds and the card must not hop down when it goes.
        gap = self.NEXTUP_INSET
        if self._fs_ui:
            gap += self.fs_controls.sizeHint().height() + 12
        b.move(tl.x() + surf.width() - b.width() - self.NEXTUP_INSET,
               tl.y() + surf.height() - b.height() - gap)

    def set_fullscreen_ui(self, fullscreen: bool) -> None:
        log.info("VID set_fullscreen_ui(%s): video=%dx%d ctx=%s popout=%s",
                 fullscreen, self.video.width(), self.video.height(),
                 self.video._ctx is not None, self._popout_mode)
        self._fs_ui = fullscreen
        self._sync_bar_switch()     # fullscreen has the timeline alone
        self.bar.setVisible(not fullscreen)
        if fullscreen:
            self._hide_seek_overlay(force=True)
            self._set_local_buffer_visible(False)   # fullscreen has its own
            self._lock_video_box()
            # Before the controls: they are raised over it, never under.
            self._keep_stats_up()
            self._show_overlay()
            # Focus the surface that is actually on screen: with a mirror up
            # the docked widget is behind a placeholder in another window,
            # so focusing it sent every key press to the wrong window.
            (getattr(self, "_mirror", None) or self.video).setFocus(
                Qt.FocusReason.OtherFocusReason)
        else:
            self._hide_fs_ui()
            self._overlay_timer.stop()
            for w in self._cursor_surfaces():
                w.unsetCursor()
            self._mac_show_cursor()
            # The docked height is now a constant, not derived from
            # self.height(), so restoring it doesn't depend on the window
            # having finished its async resize back from fullscreen - it
            # can't lock in a stale size. Re-apply once now and once on the
            # next tick to be robust against the transition ordering.
            self._lock_video_box()
            QTimer.singleShot(0, self._lock_video_box)
            self._keep_stats_up()

    def _cursor_surfaces(self) -> list:
        """Every surface the pointer can actually be over.

        In fullscreen on macOS the picture lives in the MIRROR, in its own
        window - so blanking the cursor on the player and the docked video
        left the pointer sitting visibly over the video, which is what it
        was doing there."""
        out = [self, self.video]
        m = getattr(self, "_mirror", None)
        if m is not None:
            out.append(m)
        return out

    def _hide_fs_ui(self) -> None:
        # Never take the controls and cursor away under an open menu or
        # dialog (subtitle/audio pickers, the right-click menu): the user is
        # mid-choice, and blanking the cursor there strands them without a
        # pointer. Re-arm and try again once the popup is gone.
        if self._fs_ui and (QApplication.activePopupWidget() is not None
                            or QApplication.activeModalWidget() is not None):
            self._overlay_timer.start()
            return
        self.overlay.hide()
        self.fs_controls.hide()
        if self._fs_ui:
            for w in self._cursor_surfaces():
                w.setCursor(Qt.CursorShape.BlankCursor)

    def _show_overlay(self) -> None:
        for w in self._cursor_surfaces():
            w.unsetCursor()
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
        # Size the control bar to the stream's seek MODE, not the seek widget's
        # momentary visibility: a program-mode scrub re-loads the archive, which
        # briefly hides the scrubber (play() calls _hide_seek_ui), and keying off
        # that made the bar collapse to compact and re-stretch on every seek.
        # A seek bar belongs to vod/program -> full width; live/timeline -> hug.
        if (self._seek_mode in ("vod", "program")
                or (self._local_buffer_enabled and self._seek_mode == "live"
                    and self._local_fs_window is not None)):
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
        # Sit just above the control bar - and above the timeshift timeline too
        # when it's showing, so the info text never lands on top of the bar.
        bottom = self.height() - self.fs_controls.height() - margin - 8
        if self.ts_timeline.isVisible():
            bottom -= self.ts_timeline.height() + 12
        self.overlay.move(margin, bottom - self.overlay.height())
