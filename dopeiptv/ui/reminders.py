"""Reminders manager: see every EPG reminder you've set, with a live countdown.

Answers "what have I got queued, and when does it start?" - a single place that
lists all reminders (soonest first), each counting down (hours/minutes, then
seconds as it gets close). Remove the selected one, or double-click to tune the
channel. A 1 s timer keeps the countdowns fresh while it's open. Deliberately
plain one-line rows (no per-item widgets) so nothing clips across platforms.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMenu, QPushButton, QVBoxLayout,
)

from ..i18n import tr
from .theme import P


def fmt_countdown(secs: int) -> str:
    """Human countdown: '2h 14m', '9m', '9m 30s' when close, '45s' under a
    minute, or 'starting now' at/after zero."""
    if secs <= 0:
        return tr("reminder_starting")
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        m, s = divmod(secs, 60)
        return f"{m}m {s}s" if secs < 600 else f"{m}m"   # seconds when close
    h, rem = divmod(secs, 3600)
    return f"{h}h {rem // 60}m"


class RemindersDialog(QDialog):
    """List, count down and manage the EPG reminders for the active playlist."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.setWindowTitle(tr("reminders_title"))
        self.resize(460, 460)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.setWordWrap(True)
        self.list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.itemDoubleClicked.connect(self._tune)
        self.list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.list, 1)

        self.empty = QLabel(tr("reminders_empty"))
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet(f"color:{P['muted2']};")
        lay.addWidget(self.empty)

        btns = QHBoxLayout()
        self.remove_btn = QPushButton(tr("reminders_remove"))
        self.remove_btn.clicked.connect(self._remove_selected)
        btns.addWidget(self.remove_btn)
        btns.addStretch(1)
        close = QPushButton(tr("common_close"))
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        lay.addLayout(btns)

        QShortcut(QKeySequence(Qt.Key.Key_Delete), self.list,
                  activated=self._remove_selected)

        self._rows: list[dict] = []
        self._rebuild()
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _rebuild(self) -> None:
        self.list.clear()
        self._rows = sorted(self.window.reminders.all(),
                            key=lambda r: r.get("start") or 0)
        self.empty.setVisible(not self._rows)
        self.list.setVisible(bool(self._rows))
        self.remove_btn.setEnabled(bool(self._rows))
        for r in self._rows:
            item = QListWidgetItem(self._label(r))
            item.setData(Qt.ItemDataRole.UserRole, r)
            self.list.addItem(item)
        if self._rows:
            self.list.setCurrentRow(0)

    def _label(self, r: dict) -> str:
        ch = r.get("ch") or {}
        title = r.get("title") or ch.get("name") or "?"
        left = int(r.get("start") or 0) - int(time.time())
        cd = tr("reminder_starts_in", t=fmt_countdown(left))
        chan = ch.get("name") or ""
        head = f"{title} · {chan}" if chan else title
        return f"{head}\n{cd}"

    def _tick(self) -> None:
        for i in range(self.list.count()):
            item = self.list.item(i)
            r = item.data(Qt.ItemDataRole.UserRole)
            if r is not None:
                item.setText(self._label(r))

    def _remove_selected(self) -> None:
        items = self.list.selectedItems() or (
            [self.list.currentItem()] if self.list.currentItem() else [])
        for item in items:
            r = item.data(Qt.ItemDataRole.UserRole) or {}
            self.window.reminders.remove(r.get("stream_id"), r.get("start"))
        if items:
            self._rebuild()

    def _menu(self, pos) -> None:
        if not self.list.selectedItems():
            item = self.list.itemAt(pos)
            if item is not None:
                item.setSelected(True)
        if not self.list.selectedItems():
            return
        n = len(self.list.selectedItems())
        m = QMenu(self)
        m.addAction(tr("reminders_remove_n", n=n) if n > 1
                    else tr("reminders_remove"),
                    self._remove_selected)
        m.exec(self.list.mapToGlobal(pos))

    def _tune(self, item: QListWidgetItem) -> None:
        r = item.data(Qt.ItemDataRole.UserRole)
        ch = (r or {}).get("ch") or {}
        if ch.get("stream_id") is not None:
            self.window.tune_from_guide(ch)
            self.accept()
