"""Dialogs: login, playlist editor, EPG guide, content manager."""

from __future__ import annotations

import time
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QMessageBox, QPushButton, QSplitter,
    QVBoxLayout, QWidget,
)

from .i18n import tr
from .theme import P


class LoginDialog(QDialog):
    """Initial login: Xtream Codes server, username, password."""

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connect to an Xtream server")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(14)

        heading = QLabel("dopeIPTV")
        heading.setStyleSheet("font-size:20px; font-weight:700;")
        subtitle = QLabel("Sign in with your Xtream Codes credentials.")
        subtitle.setStyleSheet(f"color:{P['muted']};")
        lay.addWidget(heading)
        lay.addWidget(subtitle)

        form = QFormLayout()
        form.setSpacing(10)
        self.server = QLineEdit(settings.value("server", ""))
        self.server.setPlaceholderText("http://server:port")
        self.user = QLineEdit(settings.value("username", ""))
        self.user.setPlaceholderText("username")
        self.pw = QLineEdit(settings.value("password", ""))
        self.pw.setPlaceholderText("password")
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Server", self.server)
        form.addRow("Username", self.user)
        form.addRow("Password", self.pw)
        lay.addLayout(form)

        self.status = QLabel("")
        self.status.setStyleSheet(f"color:{P['error']}; font-size:12px;")
        lay.addWidget(self.status)

        buttons = QDialogButtonBox()
        self.ok = buttons.addButton(
            "Connect", QDialogButtonBox.ButtonRole.AcceptRole)
        self.ok.setObjectName("Primary")
        buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def values(self) -> tuple[str, str, str]:
        return (self.server.text().strip(),
                self.user.text().strip(),
                self.pw.text().strip())


class PlaylistDialog(QDialog):
    """Add or edit a playlist (Xtream provider)."""

    REFRESH_OPTIONS = [
        ("never", "Never"), ("startup", "At startup"),
        ("2h", "Every 2 hours"), ("6h", "Every 6 hours"),
        ("12h", "Every 12 hours"), ("24h", "Daily"),
        ("1w", "Weekly"),
    ]

    def __init__(self, parent=None, playlist=None) -> None:
        super().__init__(parent)
        playlist = playlist or {}
        self.setWindowTitle("Edit playlist" if playlist else "Add playlist")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 22, 22, 22)
        form = QFormLayout()
        form.setSpacing(10)
        self.name = QLineEdit(playlist.get("name", ""))
        self.name.setPlaceholderText("e.g. My provider")
        self.server = QLineEdit(playlist.get("server", ""))
        self.server.setPlaceholderText("http://server:port")
        self.user = QLineEdit(playlist.get("username", ""))
        self.pw = QLineEdit(playlist.get("password", ""))
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.epg_url = QLineEdit(playlist.get("epg_url", ""))
        self.epg_url.setPlaceholderText(
            "optional - overrides the provider's xmltv.php")
        self.refresh = QComboBox()
        for value, label in self.REFRESH_OPTIONS:
            self.refresh.addItem(label, value)
        idx = self.refresh.findData(playlist.get("refresh", "never"))
        if idx >= 0:
            self.refresh.setCurrentIndex(idx)
        form.addRow("Name", self.name)
        form.addRow("Server", self.server)
        form.addRow("Username", self.user)
        form.addRow("Password", self.pw)
        form.addRow("Custom TV guide URL", self.epg_url)
        form.addRow("Auto-refresh", self.refresh)
        lay.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _validate(self) -> None:
        if not (self.server.text().strip() and self.user.text().strip()
                and self.pw.text().strip()):
            QMessageBox.warning(self, "Playlist",
                                "Server, username and password are required.")
            return
        self.accept()

    def values(self) -> dict:
        name = self.name.text().strip()
        if not name:
            name = self.server.text().strip().split("//")[-1].split("/")[0]
        return {
            "name": name,
            "server": self.server.text().strip(),
            "username": self.user.text().strip(),
            "password": self.pw.text().strip(),
            "epg_url": self.epg_url.text().strip(),
            "refresh": self.refresh.currentData(),
        }


class EpgGuideDialog(QDialog):
    """Channel schedule with timeline: past, current, and upcoming."""

    MAX_ROWS = 2000

    def __init__(self, window, channels, category_name=None) -> None:
        super().__init__(window)
        self.window = window
        self.channels = channels
        title = tr("btn_epg_guide")
        if category_name:
            title += f" — {category_name}"
        self.setWindowTitle(title)
        self.resize(780, 600)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        self.search = QLineEdit(placeholderText=tr("epg_filter_channels"))
        self.search.textChanged.connect(self._populate)
        lay.addWidget(self.search)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)
        self.info_lbl = QLabel("")
        self.info_lbl.setStyleSheet(
            f"color:{P['muted2']}; font-size:11px;")
        ll.addWidget(self.info_lbl)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._channel_selected)
        self.list.itemDoubleClicked.connect(self._play_selected)
        ll.addWidget(self.list, 1)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        self.ch_title = QLabel(tr("epg_select_channel"))
        self.ch_title.setStyleSheet("font-size:14px; font-weight:700;")
        rl.addWidget(self.ch_title)
        self.now_lbl = QLabel("")
        self.now_lbl.setStyleSheet(
            f"color:{P['accent']}; font-size:12px; font-weight:600;")
        self.now_lbl.setWordWrap(True)
        rl.addWidget(self.now_lbl)
        self.schedule_list = QListWidget()
        self.schedule_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.schedule_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.schedule_list.customContextMenuRequested.connect(
            self._schedule_context_menu)
        self.schedule_list.itemDoubleClicked.connect(
            lambda _it: self._play_current())
        rl.addWidget(self.schedule_list, 1)
        rec_hint = QLabel(tr("epg_record_hint"))
        rec_hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        rec_hint.setWordWrap(True)
        rl.addWidget(rec_hint)
        self.play_btn = QPushButton(tr("epg_play_channel"), objectName="Primary")
        self.play_btn.clicked.connect(self._play_current)
        self.play_btn.setEnabled(False)
        rl.addWidget(self.play_btn)
        self.window.rec.jobs_changed.connect(self._refresh_schedule_marks)

        splitter.addWidget(left)
        splitter.addWidget(right)
        # Size the channel pane to actually fit the channel names, with a
        # floor and a cap so a stray very long title doesn't make it dominate.
        fm = self.list.fontMetrics()
        widest = 0
        for it in self.channels[:800]:
            widest = max(widest, fm.horizontalAdvance(it.get("name") or "?"))
        left_w = max(240, min(widest + 52, 400))
        splitter.setSizes([left_w, 540])
        splitter.setStretchFactor(1, 1)
        lay.addWidget(splitter, 1)
        self.resize(left_w + 580, 620)

        self._populate()

    def _populate(self, _text=None) -> None:
        self.list.clear()
        text = self.search.text().lower().strip()
        shown = 0
        for it in self.channels:
            name = it.get("name") or "?"
            if text and text not in name.lower():
                continue
            shown += 1
            if shown > self.MAX_ROWS:
                break
            now = self.window.xmltv.now_for(it)
            suffix = f" — {now[0]}" if now else ""
            item = QListWidgetItem(name + suffix)
            item.setToolTip(name + suffix)
            item.setData(Qt.ItemDataRole.UserRole, it)
            self.list.addItem(item)
        note = tr("epg_channels_count", n=shown)
        if shown > self.MAX_ROWS:
            note += " " + tr("epg_channels_first", n=self.MAX_ROWS)
        self.info_lbl.setText(note)

    def _channel_selected(self, cur, _prev=None) -> None:
        self.schedule_list.clear()
        self._current_channel = None
        if not cur:
            self.ch_title.setText(tr("epg_select_channel"))
            self.now_lbl.setText("")
            self.play_btn.setEnabled(False)
            return
        it = cur.data(Qt.ItemDataRole.UserRole)
        self._current_channel = it
        name = it.get("name") or "?"
        self.ch_title.setText(name)
        self.play_btn.setEnabled(True)

        now_prog = self.window.xmltv.current_programme(it)
        if now_prog:
            start = datetime.fromtimestamp(now_prog["start_timestamp"])
            stop = datetime.fromtimestamp(now_prog["stop_timestamp"])
            self.now_lbl.setText(
                f"{tr('epg_now_prefix')}: {now_prog['title']}  "
                f"({start.strftime('%H:%M')}–{stop.strftime('%H:%M')})")
        else:
            self.now_lbl.setText(tr("epg_no_programme"))

        now_ts = datetime.now().astimezone().timestamp()
        entries = self.window.xmltv._entries_for(it)
        past = [p for p in entries
                if p["stop_timestamp"] <= now_ts
                and p["start_timestamp"] >= now_ts - 86400]
        past.reverse()
        upcoming = [p for p in entries if p["start_timestamp"] > now_ts][:20]

        if now_prog:
            self._add_section("Now")
            self._add_programme(it, now_prog, live=True)
        if upcoming:
            self._add_section("Upcoming")
            for p in upcoming:
                self._add_programme(it, p, live=False)
        if past:
            self._add_section("Earlier today")
            for p in past[:20]:
                self._add_programme(it, p, live=False, schedulable=False)

    def _add_section(self, text: str) -> None:
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setForeground(QColor(P["muted2"]))
        f = item.font()
        f.setBold(True)
        f.setPointSize(max(8, f.pointSize() - 1))
        item.setFont(f)
        self.schedule_list.addItem(item)

    def _matching_job(self, channel_it, prog: dict):
        url = self.window.client.live_url(
            channel_it.get("stream_id"), "ts")
        for j in self.window.rec.jobs:
            if (j.get("status") in ("scheduled", "recording")
                    and j.get("url") == url
                    and abs((j.get("start") or 0)
                            - prog["start_timestamp"]) < 60):
                return j
        return None

    def _add_programme(self, channel_it, prog: dict, live: bool,
                       schedulable: bool = True) -> None:
        start = datetime.fromtimestamp(prog["start_timestamp"])
        stop = datetime.fromtimestamp(prog["stop_timestamp"])
        time_str = f"{start.strftime('%H:%M')}–{stop.strftime('%H:%M')}"
        title = prog.get("title") or "?"
        job = self._matching_job(channel_it, prog) if schedulable else None
        text = f"{time_str}  {title}"
        if job:
            text = "● REC  " + text
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole,
                    {"prog": prog, "channel": channel_it, "live": live,
                     "schedulable": schedulable})
        if job:
            item.setForeground(QColor(P["rec"]))
        elif not schedulable:
            item.setForeground(QColor(P["muted"]))
        self.schedule_list.addItem(item)

    def _refresh_schedule_marks(self) -> None:
        cur = self.list.currentItem()
        if cur:
            self._channel_selected(cur)

    def _schedule_context_menu(self, pos) -> None:
        items = self.schedule_list.selectedItems()
        entries = [it.data(Qt.ItemDataRole.UserRole) for it in items]
        entries = [e for e in entries if e and e.get("schedulable")]
        if not entries:
            return
        to_record = [e for e in entries
                    if not self._matching_job(e["channel"], e["prog"])]
        to_cancel = [e for e in entries
                    if self._matching_job(e["channel"], e["prog"])]
        m = QMenu(self)
        if to_record:
            label = ("Record" if len(to_record) == 1
                     else f"Record {len(to_record)} programmes")
            m.addAction(label, lambda: self._record_entries(to_record))
        if to_cancel:
            label = ("Cancel recording" if len(to_cancel) == 1
                     else f"Cancel {len(to_cancel)} recordings")
            m.addAction(label, lambda: self._cancel_entries(to_cancel))
        if not m.isEmpty():
            m.exec(self.schedule_list.viewport().mapToGlobal(pos))

    def _record_entries(self, entries: list[dict]) -> None:
        scheduled, skipped = 0, 0
        for e in entries:
            channel_it, prog, live = e["channel"], e["prog"], e["live"]
            sid = channel_it.get("stream_id")
            if sid is None:
                skipped += 1
                continue
            url = self.window.client.live_url(sid, "ts")
            title = (f"{self.window.channel_display_name(channel_it)} - "
                    f"{prog.get('title') or '?'}")
            start_ts = time.time() if live else prog["start_timestamp"]
            self.window.rec.add_job(url, title, start_ts,
                                    prog["stop_timestamp"])
            scheduled += 1
        self._refresh_schedule_marks()
        if scheduled:
            self.window._set_status(
                f"Scheduled {scheduled} recording"
                f"{'s' if scheduled > 1 else ''} - see Recordings → Upcoming")
        if skipped:
            QMessageBox.warning(
                self, "Record",
                f"{skipped} programme{'s' if skipped > 1 else ''} could "
                "not be scheduled: missing channel stream id.")

    def _cancel_entries(self, entries: list[dict]) -> None:
        for e in entries:
            job = self._matching_job(e["channel"], e["prog"])
            if job:
                self.window.rec.cancel(job["id"])
        self._refresh_schedule_marks()

    def _play_selected(self, item) -> None:
        it = item.data(Qt.ItemDataRole.UserRole)
        self.window.play_live_channel(it)
        self.accept()

    def _play_current(self) -> None:
        cur = self.list.currentItem()
        if cur:
            self._play_selected(cur)


class ContentManagerDialog(QDialog):
    """Hide, rename, or lock categories across all lists."""

    def __init__(self, window, mode, categories, overrides) -> None:
        super().__init__(window)
        self.window = window
        self.mode = mode
        self.categories = categories
        self.overrides = overrides
        self.setWindowTitle("Manage categories")
        self.resize(460, 520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)

        hint = QLabel(
            "Hidden categories disappear from the sidebar and their "
            "channels are left out of 'All'. Locked categories need "
            "the parental PIN to open.")
        hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.list = QListWidget()
        lay.addWidget(self.list, 1)

        btns = QHBoxLayout()
        rename_btn = QPushButton("Rename...")
        self.hide_btn = QPushButton("Hide")
        self.lock_btn = QPushButton("Lock")
        for b in (rename_btn, self.hide_btn, self.lock_btn):
            btns.addWidget(b)
        lay.addLayout(btns)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)

        rename_btn.clicked.connect(self._rename)
        self.hide_btn.clicked.connect(self._toggle_hidden)
        self.lock_btn.clicked.connect(self._toggle_locked)
        self.list.currentItemChanged.connect(
            lambda *_: self._update_buttons())
        self._populate()

    def _selected_cid(self):
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _populate(self) -> None:
        selected = self._selected_cid()
        self.list.clear()
        for c in self.categories:
            cid = c.get("category_id")
            name = self.overrides.display_name(
                self.mode, cid, c.get("category_name", "?"))
            flags = []
            if self.overrides.is_hidden(self.mode, cid):
                flags.append("hidden")
            if self.overrides.is_locked(self.mode, cid):
                flags.append("locked")
            label = name + (f"   [{', '.join(flags)}]" if flags else "")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, cid)
            self.list.addItem(item)
            if cid == selected:
                self.list.setCurrentItem(item)
        self._update_buttons()

    def _update_buttons(self) -> None:
        cid = self._selected_cid()
        hidden = (cid is not None
                  and self.overrides.is_hidden(self.mode, cid))
        locked = (cid is not None
                  and self.overrides.is_locked(self.mode, cid))
        self.hide_btn.setText("Unhide" if hidden else "Hide")
        self.lock_btn.setText("Unlock" if locked else "Lock")

    def _rename(self) -> None:
        cid = self._selected_cid()
        if cid is None:
            return
        current = self.overrides.display_name(
            self.mode, cid,
            next((c.get("category_name", "") for c in self.categories
                  if c.get("category_id") == cid), ""))
        name, ok = QInputDialog.getText(
            self, "Rename category", "New name:", text=current)
        if ok:
            self.overrides.update(self.mode, cid, name=name.strip())
            self._populate()

    def _toggle_hidden(self) -> None:
        cid = self._selected_cid()
        if cid is None:
            return
        hidden = not self.overrides.is_hidden(self.mode, cid)
        self.overrides.update(self.mode, cid, hidden=hidden)
        self._populate()

    def _toggle_locked(self) -> None:
        cid = self._selected_cid()
        if cid is None:
            return
        locked = not self.overrides.is_locked(self.mode, cid)
        if locked and not self.window._ensure_pin_configured():
            return
        if locked:
            self.window.parental.lock_session()
        self.overrides.update(self.mode, cid, locked=locked)
        self._populate()
