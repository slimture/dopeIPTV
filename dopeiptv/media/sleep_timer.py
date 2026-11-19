"""The sleep timer, and the channel number typed, in the picture's corner.

The sleep timer stops what plays after the minutes chosen in the options
menu (the Stop button's own path, so the window ends what goes with it);
a pill in the picture's top-right corner counts it down, fading like the
controls but pinned on for the last SLEEP_PIN_SECS. The number badge
under it shows the digits of the window's number zapping, or why nothing
played. Both ride along into the pop-out with the picture.

Mixed into EmbeddedPlayer; it reads the player's attributes.
"""
from __future__ import annotations

import time

from ..i18n import tr
from ..ui.theme import P


class SleepTimerMixin:
    def _start_sleep_timer(self, minutes: int) -> None:
        """Stop playback after *minutes* (0 cancels a running timer)."""
        if minutes <= 0:
            if self._sleep_timer.isActive():
                self._sleep_timer.stop()
                self._sleep_tick.stop()
                self._sleep_badge_timer.stop()
                self.sleep_badge.hide()
                self.set_overlay_info(tr("sleep_cancelled"))
            return
        self._sleep_timer.start(minutes * 60 * 1000)
        self._sleep_deadline = time.monotonic() + minutes * 60
        self._sleep_tick.start()
        self._reveal_sleep_badge()
        self.set_overlay_info(tr("sleep_set", n=minutes))

    def _reveal_sleep_badge(self) -> None:
        """Flash the countdown pill on activity and re-arm its idle fade. In
        the final SLEEP_PIN_SECS the tick keeps it pinned regardless."""
        if not self._sleep_timer.isActive():
            return
        # Only when it isn't already up: the countdown's own 1 Hz tick keeps
        # the text current, so a pointer event has nothing to add (and this
        # runs on every one of them).
        if not self.sleep_badge.isVisible():
            self._update_sleep_badge(show=True)
        self._sleep_badge_timer.start()

    def _maybe_hide_sleep_badge(self) -> None:
        # Fade like the controls, but never inside the pinned final window.
        if self._sleep_deadline - time.monotonic() > self.SLEEP_PIN_SECS:
            self.sleep_badge.hide()

    def _update_sleep_badge(self, show: bool = False) -> None:
        """Refresh the countdown pill; called every second while armed. Only
        (re)shows it when asked (a reveal), when already visible, or when
        pinned - so an idle fade stays faded between ticks."""
        b = self.sleep_badge
        remaining = int(round(self._sleep_deadline - time.monotonic()))
        if remaining <= 0 or not self._sleep_timer.isActive():
            self._sleep_tick.stop()
            b.hide()
            return
        pinned = remaining <= self.SLEEP_PIN_SECS
        h, rem = divmod(remaining, 3600)
        m, s = divmod(rem, 60)
        txt = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        b.setText(f"\U0001F319 {txt}")
        # A warmer pill in the pinned window makes the imminent stop obvious.
        bg = "rgba(200,60,50,220)" if pinned else "rgba(0,0,0,150)"
        b.setStyleSheet(
            f"background: {bg}; color: #FFFFFF;"
            "border-radius: 9px; padding: 3px 9px;"
            "font-size: 11px; font-weight: 700;")
        b.adjustSize()
        self._place_sleep_badge()
        if show or pinned or b.isVisible():
            b.show()
            b.raise_()

    def _place_sleep_badge(self) -> None:
        # Anchor to the overlay surface (the docked video, or the mirror while
        # popped out) so the badge rides along into the pop-out window.
        g = self._ov_surface.geometry()
        b = self.sleep_badge
        b.move(g.right() - b.width() - 12, g.top() + 12)

    def show_number_entry(self, text: str, ms: int = 4000) -> None:
        """Show *text* (the digits typed so far, or why nothing played) in
        the picture's corner for *ms*; empty hides it. Nothing else changes:
        the window decides what plays."""
        b = self.number_badge
        if not text:
            self._number_badge_timer.stop()
            b.hide()
            return
        b.setText(text)
        b.setStyleSheet(
            "QLabel#NumberEntry { background: rgba(0,0,0,170); color: white;"
            f" border: 2px solid {P['accent']}; border-radius: 10px;"
            " padding: 4px 14px; font-size: 22px; font-weight: 700; }")
        self._place_number_badge()
        b.show()
        b.raise_()
        self._number_badge_timer.start(max(500, ms))

    def _fit_number_badge(self, width: int) -> None:
        """One line as wide as its text, or - a line wider than the picture
        (why nothing played, night mode refused) in a narrow docked player -
        wrapped to the picture's width rather than cut off."""
        b = self.number_badge
        b.setWordWrap(False)
        b.adjustSize()
        cap = max(80, width - 24)
        if b.width() > cap:
            b.setWordWrap(True)
            b.resize(cap, b.heightForWidth(cap))

    def _place_number_badge(self) -> None:
        # Under the sleep-timer pill when that one is up.
        g = self._ov_surface.geometry()
        b = self.number_badge
        self._fit_number_badge(g.width())
        top = g.top() + 12
        if self.sleep_badge.isVisible() and self.sleep_badge.parent() is b.parent():
            top = self.sleep_badge.geometry().bottom() + 8
        b.move(g.right() - b.width() - 12, top)

    def _ask_sleep_minutes(self) -> None:
        from PyQt6.QtWidgets import QInputDialog
        mins, ok = QInputDialog.getInt(
            self, tr("opt_sleep_timer"), tr("sleep_prompt"),
            60, 1, 1440, 5)
        if ok:
            self._start_sleep_timer(int(mins))

    def _on_sleep_elapsed(self) -> None:
        self._sleep_tick.stop()
        self._sleep_badge_timer.stop()
        self.sleep_badge.hide()
        self.set_overlay_info(tr("sleep_stopping"))
        # The Stop button, not stop() alone: the window also ends the
        # recording, the scrobbles and the wake lock on it - so the screen
        # can sleep once the timer has stopped what played.
        self.stop_btn.click()

    def sleep_left(self) -> float:
        """Seconds until the sleep timer stops what plays; 0 when it is off."""
        if not self._sleep_timer.isActive():
            return 0.0
        return max(0.0, self._sleep_deadline - time.monotonic())
