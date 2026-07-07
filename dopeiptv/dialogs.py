"""Dialogs: login, playlist editor, EPG guide, content manager."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QVBoxLayout,
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
    """Channel schedule overview from the EPG."""

    MAX_ROWS = 2000

    def __init__(self, window, channels) -> None:
        super().__init__(window)
        self.window = window
        self.channels = channels
        self.setWindowTitle("EPG Guide")
        self.resize(560, 640)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)

        self.search = QLineEdit(placeholderText="Filter channels...")
        self.search.textChanged.connect(self._populate)
        lay.addWidget(self.search)

        self.info_lbl = QLabel("")
        self.info_lbl.setStyleSheet(
            f"color:{P['muted2']}; font-size:11px;")
        lay.addWidget(self.info_lbl)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._play_selected)
        lay.addWidget(self.list, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)

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
            if now:
                label = f"{name}\nNow: {now[0]}  ({int(now[1])}%)"
            else:
                label = f"{name}\nNo programme data"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, it)
            self.list.addItem(item)
        note = f"{shown} channels"
        if shown > self.MAX_ROWS:
            note += (f" (showing first {self.MAX_ROWS}"
                     " - narrow your search)")
        self.info_lbl.setText(note)

    def _play_selected(self, item) -> None:
        it = item.data(Qt.ItemDataRole.UserRole)
        self.window.play_live_channel(it)
        self.accept()


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
