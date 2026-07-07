"""Dialogs: login, playlist editor, EPG guide, content manager."""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QScrollArea, QSplitter, QVBoxLayout,
    QWidget,
)

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
        title = "EPG Guide"
        if category_name:
            title += f" — {category_name}"
        self.setWindowTitle(title)
        self.resize(780, 600)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        self.search = QLineEdit(placeholderText="Filter channels...")
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
        self.ch_title = QLabel("Select a channel")
        self.ch_title.setStyleSheet("font-size:14px; font-weight:700;")
        rl.addWidget(self.ch_title)
        self.now_lbl = QLabel("")
        self.now_lbl.setStyleSheet(
            f"color:{P['accent']}; font-size:12px; font-weight:600;")
        self.now_lbl.setWordWrap(True)
        rl.addWidget(self.now_lbl)
        self.schedule_scroll = QScrollArea()
        self.schedule_scroll.setWidgetResizable(True)
        self.schedule_holder = QWidget()
        self.schedule_lay = QVBoxLayout(self.schedule_holder)
        self.schedule_lay.setContentsMargins(0, 0, 0, 0)
        self.schedule_lay.setSpacing(2)
        self.schedule_lay.addStretch()
        self.schedule_scroll.setWidget(self.schedule_holder)
        rl.addWidget(self.schedule_scroll, 1)
        self.play_btn = QPushButton("Play channel", objectName="Primary")
        self.play_btn.clicked.connect(self._play_current)
        self.play_btn.setEnabled(False)
        rl.addWidget(self.play_btn)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([280, 500])
        splitter.setStretchFactor(1, 1)
        lay.addWidget(splitter, 1)

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
            item.setData(Qt.ItemDataRole.UserRole, it)
            self.list.addItem(item)
        note = f"{shown} channels"
        if shown > self.MAX_ROWS:
            note += f" (first {self.MAX_ROWS})"
        self.info_lbl.setText(note)

    def _channel_selected(self, cur, _prev=None) -> None:
        while self.schedule_lay.count() > 1:
            w = self.schedule_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        if not cur:
            self.ch_title.setText("Select a channel")
            self.now_lbl.setText("")
            self.play_btn.setEnabled(False)
            return
        it = cur.data(Qt.ItemDataRole.UserRole)
        name = it.get("name") or "?"
        self.ch_title.setText(name)
        self.play_btn.setEnabled(True)

        now_prog = self.window.xmltv.current_programme(it)
        if now_prog:
            start = datetime.fromtimestamp(now_prog["start_timestamp"])
            stop = datetime.fromtimestamp(now_prog["stop_timestamp"])
            self.now_lbl.setText(
                f"Now: {now_prog['title']}  "
                f"({start.strftime('%H:%M')}–{stop.strftime('%H:%M')})")
        else:
            self.now_lbl.setText("No current programme data")

        now_ts = datetime.now().astimezone().timestamp()
        entries = self.window.xmltv._entries_for(it)
        past = [p for p in entries
                if p["stop_timestamp"] <= now_ts
                and p["start_timestamp"] >= now_ts - 86400]
        past.reverse()
        upcoming = [p for p in entries if p["start_timestamp"] > now_ts][:20]

        if upcoming:
            self._add_section("Upcoming")
            for p in upcoming:
                self._add_programme(p, upcoming=True)
        if past:
            self._add_section("Earlier today")
            for p in past[:20]:
                self._add_programme(p, upcoming=False)

    def _add_section(self, text: str) -> None:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color:{P['muted2']}; font-size:10px; font-weight:700;"
            "letter-spacing:1px; padding:6px 0 2px 0;")
        idx = self.schedule_lay.count() - 1
        self.schedule_lay.insertWidget(idx, lbl)

    def _add_programme(self, prog: dict, upcoming: bool) -> None:
        start = datetime.fromtimestamp(prog["start_timestamp"])
        stop = datetime.fromtimestamp(prog["stop_timestamp"])
        time_str = f"{start.strftime('%H:%M')}–{stop.strftime('%H:%M')}"
        title = prog.get("title") or "?"
        row = QLabel(f"<b>{time_str}</b>  {title}")
        row.setWordWrap(True)
        color = P["text2"] if upcoming else P["muted"]
        row.setStyleSheet(f"color:{color}; font-size:12px; padding:2px 0;")
        idx = self.schedule_lay.count() - 1
        self.schedule_lay.insertWidget(idx, row)

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
