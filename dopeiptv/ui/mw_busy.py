"""Busy / loading-indicator mixin for MainWindow.

The top indeterminate loading strip, the centred spinner overlay shown over an
empty list ("Loading movies…"), and the EPG-download progress wiring. Split out
of main_window.py; every method operates on MainWindow state (self.loading_bar,
self.listw, self.list_model, ...) through the mixin, so behaviour is identical.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QLabel

from ..i18n import tr
from .theme import P


class _BusyMixin:
    """MainWindow mixin: the centred busy/loading spinner overlay and status-line hints."""
    _SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def _show_busy(self, message: str | None = None) -> None:
        """Show the top 'busy' strip, and - when the content area is empty and
        a message is given - a centred spinner + label ('Loading movies…') so
        it reads as 'working', not a broken blank list. Always indeterminate,
        and armed with a watchdog so it can never get stuck on screen."""
        bar = self.loading_bar
        bar.setRange(0, 0)
        bar.setVisible(True)
        wd = getattr(self, "_busy_watchdog", None)
        if wd is None:
            wd = QTimer(self)
            wd.setSingleShot(True)
            wd.setInterval(25000)
            wd.timeout.connect(self._hide_busy)
            self._busy_watchdog = wd
        wd.start()
        if hasattr(self, "_busy_label"):
            self._busy_label.setText(message or "")
            self._busy_label.setVisible(bool(message))
        # The message lives in the top loading strip (with the progress bar);
        # don't also write it to the bottom status line - that duplicated it and
        # left it stuck there (the bottom line is the resting count / playing
        # readout, updated by the view, not by a transient load).
        self._update_busy_overlay(message)

    def _hide_busy(self) -> None:
        wd = getattr(self, "_busy_watchdog", None)
        if wd is not None:
            wd.stop()
        self.loading_bar.setVisible(False)
        if hasattr(self, "_busy_label"):
            self._busy_label.setText("")
            self._busy_label.setVisible(False)
        ov = getattr(self, "_busy_overlay", None)
        if ov is not None:
            ov.hide()
            self._busy_spin_timer.stop()

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

    def _on_epg_progress(self, value: int) -> None:
        # The guide download reports progress erratically - often no total, and
        # nothing at all during the several-second parse after it hits 100 % -
        # so a percentage looked jumpy and got stuck. Drive a calm indeterminate
        # strip + a one-off status line instead. During a full playlist refresh
        # the guide reload is the slow part, but "Loading programme guide" reads
        # as unrelated to what the user clicked, so name that context instead.
        self._show_busy(getattr(self, "_busy_epg_msg", None)
                        or tr("status_loading_programme_guide"))

    def _epg_progress_finished(self) -> None:
        self._busy_epg_msg = None
        self._hide_busy()
