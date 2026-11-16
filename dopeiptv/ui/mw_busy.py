"""Busy / loading-indicator mixin for MainWindow.

The top indeterminate loading strip, the centred spinner overlay shown over an
empty list ("Loading movies…"), and the programme guide's own share of the
strip. Split out of main_window.py; every method operates on MainWindow state
(self.loading_bar, self.listw, self.list_model, ...) through the mixin.

Two things ask for the strip, and each ends only its own:

- the list and the other places (``_show_busy`` / ``_hide_busy``): one at a
  time, the newest message wins, a watchdog ends it if nothing does;
- the programme guide (``_guide_busy_begin`` / ``_on_epg_progress`` /
  ``_guide_busy_end``): one owner, a token per load. Only the load the
  window started last may show it or end it - a late word from an older
  load (a source switched away from, a refresh replaced) changes nothing,
  and a list loaded meanwhile does not end it. A load that says nothing for
  ``GUIDE_QUIET_MS`` is let go with a calm note in the status line.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QLabel

from ..core.log import log
from ..i18n import tr
from .theme import P


class _BusyMixin:
    """MainWindow mixin: the busy strip, its centred spinner overlay and the
    programme guide's share of the strip."""
    _SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    #: A guide load that says nothing for this long (no progress, no end) is
    #: let go: its strip ends with a calm note, the load itself goes on.
    #: A big guide's parse says nothing for 10-25 s on a usual computer.
    GUIDE_QUIET_MS = 60_000

    # The list's own (None: not busy, "" busy without words) and the guide's:
    # the load that owns it (0: none), what it shows (None: nothing yet - a
    # guide from the disk cache comes in without a word) and in whose words.
    _busy_main: str | None = None
    _guide_busy_seq = 0
    _guide_busy_token = 0
    _guide_busy_text: str | None = None
    _guide_busy_msg: str | None = None

    def _show_busy(self, message: str | None = None) -> None:
        """Show the top 'busy' strip, and - when the content area is empty and
        a message is given - a centred spinner + label ('Loading movies…') so
        it reads as 'working', not a broken blank list. Always indeterminate,
        and armed with a watchdog so it can never get stuck on screen."""
        self._busy_main = message or ""
        wd = getattr(self, "_busy_watchdog", None)
        if wd is None:
            wd = QTimer(self)
            wd.setSingleShot(True)
            wd.setInterval(25000)
            wd.timeout.connect(self._hide_busy)
            self._busy_watchdog = wd
        wd.start()
        # The message lives in the top loading strip (with the progress bar);
        # don't also write it to the bottom status line - that duplicated it and
        # left it stuck there (the bottom line is the resting count / playing
        # readout, updated by the view, not by a transient load).
        self._busy_render()
        self._update_busy_overlay(message)

    def _hide_busy(self) -> None:
        """End the list's (and the other places') busy strip. A guide load
        still on its way keeps its own share of it until it ends."""
        wd = getattr(self, "_busy_watchdog", None)
        if wd is not None:
            wd.stop()
        self._busy_main = None
        self._busy_render()
        ov = getattr(self, "_busy_overlay", None)
        if ov is not None:
            ov.hide()
            self._busy_spin_timer.stop()

    def _busy_render(self) -> None:
        """The strip as its two owners say: the list's words first, else the
        guide's; hidden when neither is busy."""
        main = self._busy_main
        guide = self._guide_busy_text
        on = main is not None or guide is not None
        text = (main or guide or "") if on else ""
        bar = getattr(self, "loading_bar", None)
        if bar is None:
            return          # asked before the window is built
        bar.setRange(0, 0)
        bar.setVisible(on)
        if hasattr(self, "_busy_label"):
            self._busy_label.setText(text)
            self._busy_label.setVisible(bool(text))

    def _ensure_busy_overlay(self):
        ov = getattr(self, "_busy_overlay", None)
        if ov is None:
            ov = QLabel(self.listw)
            ov.setObjectName("BusyOverlay")
            ov.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ov.setStyleSheet(
                "#BusyOverlay {"
                f" background: {P['hover']}; color: {P['text']};"
                f" border: 1px solid {P['border_in']}; border-radius: 10px;"
                " padding: 12px 20px; font-size: 13px; font-weight: 600; }")
            ov.hide()
            self._busy_overlay = ov
            self._busy_spin_i = 0
            self._busy_text = ""
            t = QTimer(self)
            t.setInterval(110)
            t.timeout.connect(self._tick_busy_overlay)
            self._busy_spin_timer = t
        return self._busy_overlay

    def _tick_busy_overlay(self) -> None:
        ov = self._busy_overlay
        self._busy_spin_i = (self._busy_spin_i + 1) % len(self._SPIN)
        ov.setText(f"{self._SPIN[self._busy_spin_i]}   {self._busy_text}")
        ov.adjustSize()
        ov.move((self.listw.width() - ov.width()) // 2,
                max(24, (self.listw.height() - ov.height()) // 3))

    def _update_busy_overlay(self, message: str | None) -> None:
        if not hasattr(self, "listw") or not hasattr(self, "list_model"):
            return
        ov = self._ensure_busy_overlay()
        if message and self.list_model.rowCount() == 0:
            self._busy_text = message
            self._tick_busy_overlay()
            ov.show()
            ov.raise_()
            self._busy_spin_timer.start()
        else:
            ov.hide()
            self._busy_spin_timer.stop()

    # -- the programme guide's share ------------------------------------------

    def _guide_busy_begin(self, message: str | None = None) -> int:
        """A guide load the window starts: it owns the guide's share of the
        strip from now on, and its token is what its progress and its end
        carry. Shown only once it reports progress (a download); *message*
        names the wait after what the person did ("Refreshing playlist…")."""
        token = self._guide_busy_seq + 1
        self._guide_busy_seq = token
        self._guide_busy_token = token
        self._guide_busy_msg = message
        # What an older load showed is not this one's to keep.
        self._guide_busy_text = None
        self._guide_quiet_stop()
        self._busy_render()
        return token

    def _on_epg_progress(self, token: int, value: int) -> None:
        """A guide download's progress (delivered on the window's thread).
        Shown only for the load that owns the strip; percentages are erratic
        (often no total, then nothing through the parse), so the strip stays
        a calm indeterminate one."""
        if not token:
            # A load nobody here started (a lookup on a worker found no guide
            # and loads it itself): the window owns one beside it, which
            # waits for that load and ends the strip with it.
            adopt = getattr(self, "_ensure_xmltv_loaded", None)
            if callable(adopt):
                adopt()
            return
        if token != self._guide_busy_token:
            return          # an older load's late word
        self._guide_busy_text = (self._guide_busy_msg
                                 or tr("status_loading_programme_guide"))
        self._guide_quiet_timer().start()
        self._busy_render()

    def _guide_busy_end(self, token: int, note: str | None = None) -> bool:
        """The load with *token* ended: its share of the strip goes, and
        *note* is said in the status line when the strip was showing. An
        older load's end changes nothing (False)."""
        if not token or token != self._guide_busy_token:
            return False
        shown = self._guide_busy_text is not None
        self._guide_busy_token = 0
        self._guide_busy_msg = None
        self._guide_busy_text = None
        self._guide_quiet_stop()
        self._busy_render()
        if note and shown and hasattr(self, "_flash_status"):
            self._flash_status(note, 6000)
        return True

    def _guide_busy_quiet(self) -> None:
        """The safety net: the load that owns the strip said nothing for
        GUIDE_QUIET_MS. Its strip ends with a calm note; the load goes on,
        and when it is in the lists show it as usual."""
        token = self._guide_busy_token
        if not token or self._guide_busy_text is None:
            return
        log.info("EPG: the guide load said nothing for %d s - its loading "
                 "strip ends, the load goes on", self.GUIDE_QUIET_MS // 1000)
        self._guide_busy_end(token, tr("status_guide_still_loading"))

    def _guide_quiet_timer(self) -> QTimer:
        t = getattr(self, "_guide_quiet", None)
        if t is None:
            t = QTimer(self)
            t.setSingleShot(True)
            t.setInterval(self.GUIDE_QUIET_MS)
            t.timeout.connect(self._guide_busy_quiet)
            self._guide_quiet = t
        return t

    def _guide_quiet_stop(self) -> None:
        t = getattr(self, "_guide_quiet", None)
        if t is not None:
            t.stop()
