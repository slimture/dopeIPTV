"""The cast window's two ways to add a device by hand: "Add Kodi…", a
Kodi with its web server's port and login (``_KodiAddDialog``), and "Add
a device by address…", a Chromecast, a TV or a Kodi the search does not
reach - another part of a network split in VLANs (``_AddressDialog``).
Each asks off the window's thread and keeps the device only once it
answers; ui/mw_cast.py opens them (``add_kodi``, ``add_cast_device``).
"""

from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from ..core.workers import run_async
from ..i18n import tr
from ..providers.kodi import DEFAULT_PORT, parse_address
from .theme import P


class _KodiAddDialog(QDialog):
    """A Kodi by its address: the web server's port (8080 unless changed in
    Kodi) and, when it has one, its login. Kept only once Kodi answers."""

    def __init__(self, parent, kodi, current: str | None, pool) -> None:
        super().__init__(parent)
        self.kodi = kodi
        self.pool = pool
        self.current = current
        self.result_label: str | None = None
        self._checking = False
        dev = kodi.device(current) if current else None
        self.setWindowTitle(tr("kodi_add_title"))
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)
        intro = QLabel(tr("kodi_add_intro"))
        intro.setWordWrap(True)
        intro.setObjectName("DetailMeta")
        lay.addWidget(intro)
        form = QFormLayout()
        self.address = QLineEdit(
            "" if dev is None else (dev.address if dev.port != DEFAULT_PORT
                                    else dev.host))
        self.address.setPlaceholderText(tr("kodi_address_ph"))
        self.user = QLineEdit("" if dev is None else dev.user)
        self.user.setPlaceholderText("kodi")
        self.password = QLineEdit("" if dev is None else dev.password)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(tr("kodi_address"), self.address)
        form.addRow(tr("login_username"), self.user)
        form.addRow(tr("login_password"), self.password)
        lay.addLayout(form)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.hide()
        lay.addWidget(self.status)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_btn.setText(tr("common_save") if dev is not None else tr("btn_connect"))
        self.forget_btn = None
        if dev is not None and getattr(dev, "manual", False):
            self.forget_btn = QPushButton(tr("kodi_forget"))
            self.buttons.addButton(self.forget_btn,
                                   QDialogButtonBox.ButtonRole.DestructiveRole)
            self.forget_btn.clicked.connect(self._forget)
        self.buttons.accepted.connect(self._check)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

    def _say(self, text: str, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(
            f"color:{P['error'] if error else P['muted']}; font-size:12px;")
        if self.status.isHidden():
            self.status.show()
            # Room for the line: the fields are not squeezed to make it.
            self.adjustSize()

    def _forget(self) -> None:
        self.kodi.forget(self.current)
        self.result_label = ""
        self.accept()

    def _check(self) -> None:
        if self._checking:
            return
        where = parse_address(self.address.text())
        if where is None:
            self._say(tr("kodi_bad_address"), error=True)
            return
        host, port = where
        user, password = self.user.text().strip(), self.password.text()
        self._checking = True
        self.ok_btn.setEnabled(False)
        self._say(tr("kodi_checking", address=f"{host}:{port}"))
        run_async(self.pool, self.kodi.add, self._added, self._failed,
                  host, port, user, password)

    def _alive(self) -> bool:
        return not sip.isdeleted(self) and self.isVisible()

    def _added(self, label: str) -> None:
        if not self._alive():
            return
        self._checking = False
        self.result_label = label
        self.accept()

    def _failed(self, msg: str) -> None:
        if not self._alive():
            return
        self._checking = False
        self.ok_btn.setEnabled(True)
        self._say(msg, error=True)


class _AddressDialog(QDialog):
    """A device by its address: what kind it is found out (or the one
    chosen), and it is kept only once it answers as that."""

    KINDS = (("auto", "cast_addr_kind_auto"), ("chromecast", "cast_addr_kind_cc"),
             ("dlna", "cast_addr_kind_dlna"), ("kodi", "cast_addr_kind_kodi"))

    def __init__(self, parent, hub, current: str | None, pool) -> None:
        super().__init__(parent)
        self.hub = hub
        self.pool = pool
        self.current = current
        self.result_label: str | None = None
        self._checking = False
        self.setWindowTitle(tr("cast_addr_title"))
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)
        intro = QLabel(tr("cast_addr_intro"))
        intro.setWordWrap(True)
        intro.setObjectName("DetailMeta")
        lay.addWidget(intro)
        form = QFormLayout()
        self.kind = QComboBox()
        for key, label in self.KINDS:
            self.kind.addItem(tr(label), key)
        self.address = QLineEdit("")
        self.address.setPlaceholderText(tr("cast_addr_ph"))
        form.addRow(tr("cast_addr_kind"), self.kind)
        form.addRow(tr("kodi_address"), self.address)
        lay.addLayout(form)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.hide()
        lay.addWidget(self.status)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_btn.setText(tr("cast_addr_add"))
        self.forget_btn = None
        if current:
            self.forget_btn = QPushButton(tr("cast_addr_forget"))
            self.buttons.addButton(self.forget_btn,
                                   QDialogButtonBox.ButtonRole.DestructiveRole)
            self.forget_btn.clicked.connect(self._forget)
        self.buttons.accepted.connect(self._check)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

    def _say(self, text: str, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(
            f"color:{P['error'] if error else P['muted']}; font-size:12px;")
        if self.status.isHidden():
            self.status.show()
            self.adjustSize()

    def _forget(self) -> None:
        key = self.hub.added_key(self.current)
        if key:
            self.hub.forget_added(key)
        self.result_label = ""
        self.accept()

    def _check(self) -> None:
        if self._checking:
            return
        from ..providers.cast_devices import parse_address
        text = self.address.text().strip()
        if parse_address(text) is None:
            self._say(tr("cast_addr_bad"), error=True)
            return
        self._checking = True
        self.ok_btn.setEnabled(False)
        self._say(tr("cast_addr_checking", address=text))
        run_async(self.pool, self.hub.add_by_address, self._added, self._failed,
                  text, self.kind.currentData() or "auto")

    def _alive(self) -> bool:
        return not sip.isdeleted(self) and self.isVisible()

    def _added(self, label: str) -> None:
        if not self._alive():
            return
        self._checking = False
        self.result_label = label
        self.accept()

    def _failed(self, msg: str) -> None:
        if not self._alive():
            return
        self._checking = False
        self.ok_btn.setEnabled(True)
        self._say(msg, error=True)
