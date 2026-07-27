"""Dialogs: login, playlist editor, EPG guide, content manager."""

from __future__ import annotations


from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from ..i18n import tr
from .theme import P




class PlaylistDialog(QDialog):
    """Add or edit a playlist (Xtream provider)."""

    # (value, i18n key) - the label is translated at construction time.
    REFRESH_OPTIONS = [
        ("never", "refresh_never"), ("startup", "refresh_at_startup"),
        ("2h", "refresh_every_2h"), ("6h", "refresh_every_6h"),
        ("12h", "refresh_every_12h"), ("24h", "refresh_daily"),
        ("1w", "refresh_weekly"),
    ]

    def __init__(self, parent=None, playlist=None) -> None:
        super().__init__(parent)
        playlist = playlist or {}
        self.setWindowTitle(tr("playlist_edit_title") if playlist
                            else tr("playlist_add_title"))
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 22, 22, 22)
        form = QFormLayout()
        form.setSpacing(10)
        self.name = QLineEdit(playlist.get("name", ""))
        self.name.setPlaceholderText(tr("playlist_name_placeholder"))
        self.kind = QComboBox()
        self.kind.addItem(tr("playlist_kind_xtream"), "xtream")
        self.kind.addItem(tr("playlist_kind_m3u"), "m3u")
        kidx = self.kind.findData(playlist.get("kind", "xtream"))
        if kidx >= 0:
            self.kind.setCurrentIndex(kidx)
        self.server = QLineEdit(playlist.get("server", ""))
        # A pasted link is recognised as Xtream (fans out into the three
        # fields) or M3U (a plain playlist URL) and the mode follows; manual
        # entry is unaffected. textEdited fires on paste (but not on our own
        # setText), so the credentials fill the moment the link is pasted.
        self.server.textEdited.connect(lambda _t: self._maybe_autodetect_link())
        self.user = QLineEdit(playlist.get("username", ""))
        self.pw = QLineEdit(playlist.get("password", ""))
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.epg_url = QLineEdit(playlist.get("epg_url", ""))
        self.epg_url.setPlaceholderText(tr("playlist_epg_placeholder"))
        self.refresh = QComboBox()
        for value, key in self.REFRESH_OPTIONS:
            self.refresh.addItem(tr(key), value)
        idx = self.refresh.findData(playlist.get("refresh", "never"))
        if idx >= 0:
            self.refresh.setCurrentIndex(idx)
        # Explicit labels so the login rows can be hidden for M3U playlists.
        self._server_lbl = QLabel()
        self._user_lbl = QLabel(tr("login_username"))
        self._pw_lbl = QLabel(tr("login_password"))
        form.addRow(tr("playlist_name"), self.name)
        form.addRow(tr("playlist_kind"), self.kind)
        form.addRow(self._server_lbl, self.server)
        form.addRow(self._user_lbl, self.user)
        form.addRow(self._pw_lbl, self.pw)
        form.addRow(tr("playlist_custom_epg_url"), self.epg_url)
        form.addRow(tr("playlist_auto_refresh"), self.refresh)
        lay.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.kind.currentIndexChanged.connect(self._update_kind)
        self._update_kind()

    def _update_kind(self) -> None:
        m3u = self.kind.currentData() == "m3u"
        # M3U needs only a URL - hide the username/password rows and relabel
        # the address field.
        self._server_lbl.setText(
            tr("playlist_m3u_url") if m3u else tr("login_server"))
        self.server.setPlaceholderText(
            "https://example.com/playlist.m3u" if m3u else "http://server:port")
        for w in (self._user_lbl, self.user, self._pw_lbl, self.pw):
            w.setVisible(not m3u)

    def _maybe_autodetect_link(self) -> None:
        """Recognise a pasted provider link and configure the dialog: Xtream
        (split into server/username/password, preferred) or M3U (a plain
        playlist URL). A bare host is left alone so manual entry keeps working."""
        from ..providers.client import detect_provider_link
        detected = detect_provider_link(self.server.text())
        if not detected:
            return
        kind, server, user, pw = detected
        idx = self.kind.findData(kind)
        if idx >= 0:
            self.kind.setCurrentIndex(idx)   # fires _update_kind
        self.server.setText(server)
        if kind == "xtream":
            self.user.setText(user)
            self.pw.setText(pw)

    def _validate(self) -> None:
        if self.kind.currentData() == "m3u":
            ok = bool(self.server.text().strip())
        else:
            ok = bool(self.server.text().strip() and self.user.text().strip()
                      and self.pw.text().strip())
        if not ok:
            QMessageBox.warning(self, tr("playlist_msg_title"),
                                tr("playlist_required_fields"))
            return
        self.accept()

    def values(self) -> dict:
        name = self.name.text().strip()
        if not name:
            name = self.server.text().strip().split("//")[-1].split("/")[0]
        return {
            "name": name,
            "kind": self.kind.currentData(),
            "server": self.server.text().strip(),
            "username": self.user.text().strip(),
            "password": self.pw.text().strip(),
            "epg_url": self.epg_url.text().strip(),
            "refresh": self.refresh.currentData(),
        }




class ContentManagerDialog(QDialog):
    """Hide, rename, or lock categories across all lists."""

    def __init__(self, window, mode, categories, overrides) -> None:
        super().__init__(window)
        self.window = window
        self.mode = mode
        self.categories = categories
        self.overrides = overrides
        self.setWindowTitle(tr("cm_title"))
        self.resize(460, 520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)

        hint = QLabel(tr("cm_hint"))
        hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.list = QListWidget()
        lay.addWidget(self.list, 1)

        btns = QHBoxLayout()
        rename_btn = QPushButton(tr("cm_rename"))
        self.hide_btn = QPushButton(tr("cm_hide"))
        self.lock_btn = QPushButton(tr("cm_lock"))
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
        # Sort by the (possibly renamed) display name, case-insensitively, so
        # the manager list is alphabetical and easy to scan - the provider's
        # own order is arbitrary. This is display only; it doesn't change the
        # sidebar's category order.
        def _disp(c):
            return self.overrides.display_name(
                self.mode, c.get("category_id"),
                c.get("category_name", "?"))
        for c in sorted(self.categories, key=lambda c: _disp(c).lower()):
            cid = c.get("category_id")
            name = self.overrides.display_name(
                self.mode, cid, c.get("category_name", "?"))
            flags = []
            if self.overrides.is_hidden(self.mode, cid):
                flags.append(tr("cm_flag_hidden"))
            if self.overrides.is_locked(self.mode, cid):
                flags.append(tr("cm_flag_locked"))
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
        self.hide_btn.setText(tr("cm_unhide") if hidden else tr("cm_hide"))
        self.lock_btn.setText(tr("cm_unlock") if locked else tr("cm_lock"))

    def _rename(self) -> None:
        cid = self._selected_cid()
        if cid is None:
            return
        current = self.overrides.display_name(
            self.mode, cid,
            next((c.get("category_name", "") for c in self.categories
                  if c.get("category_id") == cid), ""))
        name, ok = QInputDialog.getText(
            self, tr("cm_rename_title"), tr("cm_new_name"), text=current)
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
