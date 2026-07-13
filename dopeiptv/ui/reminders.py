"""Reminders manager: see every EPG reminder you've set, with a live countdown.

Answers "what have I got queued, and when does it start?" - a single place that
lists all reminders (soonest first), each counting down (hours/minutes, then
seconds as it gets close), with a remove button and a double-click to tune the
channel. A 1 s timer keeps the countdowns fresh while it's open.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout, QWidget,
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
        self.resize(460, 480)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._tune_selected)
        lay.addWidget(self.list, 1)

        self.empty = QLabel(tr("reminders_empty"))
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet(f"color:{P['muted2']};")
        lay.addWidget(self.empty)

        btns = QHBoxLayout()
        btns.addStretch(1)
        close = QPushButton(tr("common_close"))
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        lay.addLayout(btns)

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
        for r in self._rows:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8, 6, 8, 6)
            rl.setSpacing(10)
            ch = r.get("ch") or {}
            title = r.get("title") or ch.get("name") or "?"
            text = QLabel(f"<b>{title}</b><br>"
                          f"<span style='color:{P['muted2']}'>"
                          f"{ch.get('name') or ''}</span>")
            text.setTextFormat(Qt.TextFormat.RichText)
            rl.addWidget(text, 1)
            countdown = QLabel()
            countdown.setStyleSheet(f"color:{P['accent']}; font-weight:600;")
            r["_countdown_lbl"] = countdown
            rl.addWidget(countdown)
            remove = QPushButton(tr("reminders_remove"))
            remove.clicked.connect(
                lambda _c=False, rr=r: self._remove(rr))
            rl.addWidget(remove)
            item = QListWidgetItem(self.list)
            item.setSizeHint(row.sizeHint())
            item.setData(Qt.ItemDataRole.UserRole, r)
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
        self._tick()

    def _tick(self) -> None:
        now = int(time.time())
        for r in self._rows:
            lbl = r.get("_countdown_lbl")
            if lbl is not None:
                left = int(r.get("start") or 0) - now
                lbl.setText(tr("reminder_starts_in", t=fmt_countdown(left)))

    def _remove(self, r: dict) -> None:
        self.window.reminders.remove(r.get("stream_id"), r.get("start"))
        self._rebuild()

    def _tune_selected(self, item: QListWidgetItem) -> None:
        r = item.data(Qt.ItemDataRole.UserRole)
        ch = (r or {}).get("ch") or {}
        if ch.get("stream_id") is not None:
            self.window.tune_from_guide(ch)
            self.accept()
