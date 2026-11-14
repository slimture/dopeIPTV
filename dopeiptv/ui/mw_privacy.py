"""Private viewing and Clear recent history in the window (core/privacy.py
holds the rules): the two entries in the app menu, their shortcuts as a
browser has them, the marker in the sidebar while private viewing is on,
and what clearing refreshes."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QPushButton

from ..core import privacy
from ..i18n import tr
from .theme import P

PRIVATE_SHORTCUT = "Ctrl+Shift+N"
CLEAR_SHORTCUT = "Ctrl+Shift+Delete"


class _PrivacyMixin:
    def _build_privacy_actions(self, menu) -> list[QAction]:
        """Private viewing (on/off) and Clear recent history, in *menu*."""
        self._private_action = menu.addAction(tr("private_menu"))
        self._private_action.setCheckable(True)
        self._private_action.setShortcut(QKeySequence(PRIVATE_SHORTCUT))
        self._private_action.toggled.connect(self.set_private_viewing)
        self._clear_action = menu.addAction(tr("clear_menu") + "…")
        self._clear_action.setShortcut(QKeySequence(CLEAR_SHORTCUT))
        self._clear_action.triggered.connect(lambda: self.open_clear_history())
        return [self._private_action, self._clear_action]

    def _build_private_pill(self, row) -> None:
        """The marker beside the logo while private viewing is on; a click
        turns it off."""
        self._private_pill = QPushButton(tr("private_pill"), objectName="PrivatePill")
        self._private_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self._private_pill.setToolTip(tr("private_pill_tip"))
        self._private_pill.setStyleSheet(
            f"QPushButton#PrivatePill {{ background:{P['sel']}; color:{P['text']};"
            f" border:none; border-radius:10px; padding:2px 10px; font-size:11px;"
            f" font-weight:700; }}"
            f"QPushButton#PrivatePill:hover {{ background:{P['btn_hover']}; }}")
        self._private_pill.clicked.connect(lambda: self.set_private_viewing(False))
        self._private_pill.hide()
        row.addWidget(self._private_pill, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_private_viewing(self, on: bool) -> None:
        """Turn private viewing on or off (it ends with the app anyway)."""
        on = bool(on)
        was = privacy.private()
        privacy.set_private(on)
        if on:
            # A Trakt scrobble already under way is let go, not stopped: a
            # stop would report the progress and file it in the history.
            self._trakt_active = None
        action = getattr(self, "_private_action", None)
        if action is not None and action.isChecked() != on:
            action.blockSignals(True)
            action.setChecked(on)
            action.blockSignals(False)
        pill = getattr(self, "_private_pill", None)
        if pill is not None:
            pill.setVisible(on)
        if was != on:
            self._show_toast(tr("private_on_toast") if on else tr("private_off_toast"), 5000)

    def open_clear_history(self, preselect: str | None = None) -> dict | None:
        """The Clear recent history window; clears what it says and says
        what went. Returns the counts, or None when cancelled."""
        from .dialogs import ClearHistoryDialog
        d = ClearHistoryDialog(self, preselect)
        if not d.exec():
            return None
        c = d.choice()
        resume_settings = getattr(self, "_resume_settings", self.settings)
        counts = privacy.clear_recent(self.settings, resume_settings, self.history,
                                      c["seconds"], c["channels"], c["titles"], c["positions"])
        # The resume store holds its positions in memory: read them again.
        if c["positions"] and getattr(self, "resume", None) is not None:
            from ..services.resume import ResumeStore
            pl = self.playlist_store.active() if self.playlist_store else None
            self.resume = ResumeStore(resume_settings, (pl or {}).get("id"))
        total = sum(counts.values())
        self._show_toast(tr("clear_done", n=total) if total else tr("clear_nothing"), 5000)
        self._after_clear()
        return counts

    def _after_clear(self) -> None:
        """Lists that show history or positions, shown again."""
        if getattr(self, "mode", None) == "history":
            try:
                self._load_items(getattr(self, "_history_subcat", None))
            except Exception:
                pass
        refresh = getattr(self, "_apply_home_settings", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:
                pass
