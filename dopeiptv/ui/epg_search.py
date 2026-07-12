"""EPG search: find a programme by name across the whole guide and tune in.

Answers questions like "what's on with Formula 1 this week?" - it searches
every live channel's schedule (title + description) over the next several days
and lists the hits with their channel and time, so you can play the channel or
set a reminder straight from the results. The heavy scan runs off the UI thread
(see ``XmltvGuide.search``); this module is just the dialog around it.
"""

from __future__ import annotations

import time
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout,
)

from ..core.workers import run_async
from ..i18n import tr
from .theme import P


class EpgSearchDialog(QDialog):
    """Search the loaded guide across all live channels and act on a hit."""

    SEARCH_DAYS = 7
    MIN_QUERY = 2

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self._channels: list = []     # all live channel items (loaded async)
        self._results: list = []      # current result entries
        self.setWindowTitle(tr("epg_search_title"))
        self.resize(660, 560)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        self.box = QLineEdit(placeholderText=tr("epg_search_placeholder"))
        self.box.setClearButtonEnabled(True)
        self.box.textChanged.connect(lambda _t: self._debounce.start(250))
        self.box.returnPressed.connect(self._run_search)
        lay.addWidget(self.box)

        self.results = QListWidget()
        self.results.itemActivated.connect(lambda _i: self._play_current())
        self.results.currentRowChanged.connect(self._on_row)
        lay.addWidget(self.results, 1)

        bar = QHBoxLayout()
        self.info = QLabel(tr("epg_search_hint"))
        self.info.setStyleSheet(f"color:{P['muted']}; font-size:12px;")
        self.info.setWordWrap(True)
        bar.addWidget(self.info, 1)
        self.remind_btn = QPushButton(tr("reminder_add"))
        self.remind_btn.setEnabled(False)
        self.remind_btn.clicked.connect(self._remind_current)
        bar.addWidget(self.remind_btn)
        self.play_btn = QPushButton(tr("epg_play_channel"), objectName="Primary")
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self._play_current)
        bar.addWidget(self.play_btn)
        lay.addLayout(bar)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._run_search)

        self._load_channels()

    # -- data ----------------------------------------------------------------

    def _load_channels(self) -> None:
        self.info.setText(tr("status_loading_channels"))

        def done(chans) -> None:
            self._channels = chans or []
            self.info.setText(tr("epg_search_hint"))
            self.box.setFocus()
            if self.box.text().strip():
                self._run_search()

        run_async(self.window.pool,
                  lambda: self.window.client.live_streams(None),
                  done, lambda _e: self.info.setText(tr("epg_search_hint")))

    def _run_search(self) -> None:
        self._debounce.stop()
        q = self.box.text().strip()
        self.results.clear()
        self._results = []
        self.play_btn.setEnabled(False)
        self.remind_btn.setEnabled(False)
        if len(q) < self.MIN_QUERY or not self._channels:
            self.info.setText(tr("epg_search_hint"))
            return
        now = time.time()
        win_start = now - 3600
        win_stop = now + self.SEARCH_DAYS * 86400
        chans = self._channels
        self.info.setText(tr("epg_searching"))

        def done(res) -> None:
            self._results = res or []
            self._populate()

        run_async(self.window.pool,
                  lambda: self.window.xmltv.search(chans, q, win_start,
                                                   win_stop),
                  done, lambda _e: self._populate())

    def _populate(self) -> None:
        self.results.clear()
        now = time.time()
        for e in self._results:
            ch = e["_channel"]
            when = datetime.fromtimestamp(e["start_timestamp"])
            label = (f"{when.strftime('%a %d %b  %H:%M')}   ·   "
                     f"{e['title']}   —   {ch.get('name', '')}")
            item = QListWidgetItem(label)
            if e["stop_timestamp"] < now:
                item.setForeground(QColor(P["muted"]))   # already aired
            self.results.addItem(item)
        n = len(self._results)
        self.info.setText(tr("epg_search_count", n=n) if n
                          else tr("epg_search_none"))
        if n:
            self.results.setCurrentRow(0)

    # -- selection / actions -------------------------------------------------

    def _current(self):
        row = self.results.currentRow()
        return self._results[row] if 0 <= row < len(self._results) else None

    def _on_row(self, row: int) -> None:
        e = self._results[row] if 0 <= row < len(self._results) else None
        self.play_btn.setEnabled(e is not None)
        if e is None:
            self.remind_btn.setEnabled(False)
            return
        ch = e["_channel"]
        sid = ch.get("stream_id")
        future = e["start_timestamp"] > time.time()
        rem = getattr(self.window, "reminders", None)
        can_remind = future and sid is not None and rem is not None
        self.remind_btn.setEnabled(can_remind)
        self.remind_btn.setText(
            tr("reminder_remove") if can_remind and rem.has(sid,
                                                            e["start_timestamp"])
            else tr("reminder_add"))

    def _play_current(self) -> None:
        e = self._current()
        if not e:
            return
        ch = e["_channel"]
        # A finished programme on a timeshift channel plays as catch-up;
        # everything else tunes the channel live (mirrors the guide grid).
        if e["stop_timestamp"] < time.time() and self.window._timeshift_days(ch):
            self.window._play_timeshift(ch, prog=e)
        else:
            self.window.play_live_channel(ch)
        self.accept()

    def _remind_current(self) -> None:
        e = self._current()
        if not e:
            return
        ch = e["_channel"]
        sid = ch.get("stream_id")
        rem = getattr(self.window, "reminders", None)
        if sid is None or rem is None:
            return
        if rem.has(sid, e["start_timestamp"]):
            rem.remove(sid, e["start_timestamp"])
        else:
            self.window._add_reminder(ch, e)
        self._on_row(self.results.currentRow())
