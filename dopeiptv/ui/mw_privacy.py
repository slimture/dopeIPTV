"""Private viewing and Clear recent history in the window (core/privacy.py
holds the rules): the two entries in the app menu, their shortcuts as a
browser has them, the marker in the sidebar while private viewing is on,
and what clearing refreshes."""

from __future__ import annotations

import sys

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QPushButton

from ..core import privacy
from ..i18n import tr
from .theme import P

PRIVATE_SHORTCUT = "Ctrl+Shift+N"
# A Mac's delete key is Backspace (⇧⌘⌫, as Safari has it); few have the other.
CLEAR_SHORTCUT = "Ctrl+Shift+Backspace" if sys.platform == "darwin" else "Ctrl+Shift+Delete"


class _Follow(QObject):
    """Calls *place* whenever the watched widget moves, resizes or shows."""

    def __init__(self, parent: QObject, place) -> None:
        super().__init__(parent)
        self._place = place

    def eventFilter(self, obj, event) -> bool:        # noqa: N802 - Qt hook
        if event.type() in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show,
                            QEvent.Type.LayoutRequest):
            self._place()
        return False


class _PrivacyMixin:
    def _build_privacy_actions(self, menu=None) -> list[QAction]:
        """Private viewing (on/off) and Clear recent history - in *menu*
        when given (the app menu lays them out itself otherwise)."""
        self._private_action = QAction(tr("private_menu"), self)
        self._private_action.setCheckable(True)
        self._private_action.setShortcut(QKeySequence(PRIVATE_SHORTCUT))
        self._private_action.toggled.connect(self.set_private_viewing)
        self._clear_action = QAction(tr("clear_menu") + "…", self)
        self._clear_action.setShortcut(QKeySequence(CLEAR_SHORTCUT))
        self._clear_action.triggered.connect(lambda: self.open_clear_history())
        if menu is not None:
            menu.addAction(self._private_action)
            menu.addAction(self._clear_action)
        return [self._private_action, self._clear_action]

    def _build_private_pill(self) -> None:
        """The marker while private viewing is on: a small label laid over
        the logo's lower edge, centred on it - in no layout, so the logo,
        the heart beside it and the row under it stay exactly where they
        are. A click turns private viewing off."""
        logo = self._sidebar_logo
        pill = QPushButton(tr("private_pill"), logo.parentWidget(), objectName="PrivatePill")
        pill.setCursor(Qt.CursorShape.PointingHandCursor)
        pill.setToolTip(tr("private_pill_tip"))
        pill.setAccessibleName(tr("private_pill_tip"))
        # Ringed in the sidebar's colour, so it reads as lying on the mark.
        pill.setStyleSheet(
            f"QPushButton#PrivatePill {{ background:{P['sel']}; color:{P['text']};"
            f" border:2px solid {P['side']}; border-radius:10px; padding:1px 9px;"
            f" font-size:10px; font-weight:700; }}"
            f"QPushButton#PrivatePill:hover {{ background:{P['btn_hover']}; }}")
        pill.clicked.connect(lambda: self.set_private_viewing(False))
        pill.hide()
        self._private_pill = pill
        follow = _Follow(pill, self._place_private_pill)
        logo.installEventFilter(follow)
        logo.parentWidget().installEventFilter(follow)

    def _place_private_pill(self) -> None:
        pill = getattr(self, "_private_pill", None)
        logo = getattr(self, "_sidebar_logo", None)
        if pill is None or logo is None or not pill.isVisible():
            return
        pill.adjustSize()
        g = logo.geometry()
        mark_top = g.top() + (g.height() - logo.LOGO_H) // 2
        # On the icon rail the heart sits under the logo: the top edge there.
        edge = mark_top if getattr(self, "_sidebar_collapsed", False) else mark_top + logo.LOGO_H
        pill.move(g.center().x() + 1 - pill.width() // 2, edge - pill.height() // 2)
        pill.raise_()

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
            self._place_private_pill()
        if hasattr(self, "_donate_heart_refresh"):   # the supporter's label gives way
            self._donate_heart_refresh()
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
