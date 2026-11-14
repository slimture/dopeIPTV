"""Dialogs: login, playlist editor, EPG guide, content manager, the update."""

from __future__ import annotations

import re

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from ..i18n import tr
from .box_finder import (
    BOX_KINDS, LOGIN_KINDS, PLACEHOLDERS, FindOnNetwork, add_box_kinds,
)
from .theme import P




def stalker_available() -> bool:
    """Whether this build has the Stalker / Ministra portal client: the
    choice is offered only then."""
    import importlib.util
    try:
        return importlib.util.find_spec("dopeiptv.providers.stalker") is not None
    except (ImportError, ValueError):
        return False


class FitScroll(QScrollArea):
    """A scroll area as tall as what it holds, up to the screen: a dialog
    around it grows with its content and scrolls once that would not fit
    (the source form with its Nerd options open is taller than a laptop's
    screen)."""

    def __init__(self, content: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidget(content)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Room the dialog needs around this: its margins, title, buttons.
        self.reserve = 160

    def _cap(self) -> int:
        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry().height() if screen is not None else 800
        return max(200, int(avail * 0.9) - self.reserve)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        w = self.widget()
        hint = w.sizeHint() if w is not None else QSize(400, 200)
        bar = self.verticalScrollBar().sizeHint().width()
        return QSize(hint.width() + bar, min(hint.height(), self._cap()))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        hint = self.sizeHint()
        return QSize(hint.width(), min(hint.height(), 200))


class PlaylistDialog(QDialog):
    """Add or edit a playlist (Xtream provider)."""

    # (value, i18n key) - the label is translated at construction time.
    REFRESH_OPTIONS = [
        ("never", "refresh_never"), ("startup", "refresh_at_startup"),
        ("2h", "refresh_every_2h"), ("6h", "refresh_every_6h"),
        ("12h", "refresh_every_12h"), ("24h", "refresh_daily"),
        ("1w", "refresh_weekly"),
    ]

    def __init__(self, parent=None, playlist=None,
                 prefill: dict | None = None) -> None:
        """*playlist* edits a stored playlist; *prefill* starts a new one
        with fields already filled in (a playlist file dropped on the
        window)."""
        super().__init__(parent)
        editing = bool(playlist)
        playlist = playlist or prefill or {}
        self.setWindowTitle(tr("playlist_edit_title") if editing
                            else tr("playlist_add_title"))
        self.setMinimumWidth(520)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 22, 22, 22)
        # The form scrolls, the buttons stay: with Nerd on it can be taller
        # than the screen.
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        self._scroll = FitScroll(body, self)
        outer.addWidget(self._scroll, 1)
        form = QFormLayout()
        form.setSpacing(10)
        self.name = QLineEdit(playlist.get("name", ""))
        self.name.setPlaceholderText(tr("playlist_name_placeholder"))
        self.kind = QComboBox()
        self.kind.addItem(tr("playlist_kind_xtream"), "xtream")
        self.kind.addItem(tr("playlist_kind_m3u"), "m3u")
        if stalker_available():
            self.kind.addItem(tr("playlist_kind_stalker"), "stalker")
        add_box_kinds(self.kind)
        # A box's own options (stream port, profile...) ride along unchanged.
        self._options = dict(playlist.get("options") or {})
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
        # A Stalker / Ministra portal logs in with the box's MAC address.
        self.mac = QLineEdit(playlist.get("mac", ""))
        self.mac.setPlaceholderText("00:1A:79:12:34:56")
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
        self._mac_lbl = QLabel(tr("playlist_mac"))
        form.addRow(tr("playlist_name"), self.name)
        form.addRow(tr("playlist_kind"), self.kind)
        # Nerd: devices and servers with known playlist/guide addresses.
        from ..core import nerd
        if nerd.current().on:
            from .source_presets import PresetButton
            self.presets = PresetButton(self._preset_picked, self)
            form.addRow("", self.presets)
        # A box on the home network can be found instead of typed.
        self.finder = FindOnNetwork(self)
        self.finder.picked.connect(self._box_found)
        form.addRow(self._server_lbl, self.server)
        form.addRow("", self.finder.row)
        form.addRow(self._user_lbl, self.user)
        form.addRow(self._pw_lbl, self.pw)
        form.addRow(self._mac_lbl, self.mac)
        form.addRow(tr("playlist_custom_epg_url"), self.epg_url)
        form.addRow(tr("playlist_auto_refresh"), self.refresh)
        lay.addLayout(form)
        # The source's own Nerd options: only while Nerd is on, folded.
        self.nerd_panel = None
        from ..core import nerd
        if nerd.current().on:
            from .source_nerd import SourceNerdPanel
            self.nerd_panel = SourceNerdPanel(self.kind.currentData(), self._options, self,
                                              source=self._source_now)
            lay.addWidget(self.nerd_panel)
            self.nerd_panel.toggle.toggled.connect(lambda _on: self._fit())
            for field in (self.server, self.user, self.pw):
                field.textChanged.connect(self.nerd_panel.source_changed)
        lay.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.kind.currentIndexChanged.connect(self._update_kind)
        self._update_kind()

    def _source_now(self) -> dict:
        """The source as the form has it now, without its Nerd options (what
        the catch-up Test asks)."""
        return {"kind": self.kind.currentData(), "server": self.server.text().strip(),
                "username": self.user.text().strip(), "password": self.pw.text().strip(),
                "mac": self.normal_mac(self.mac.text()) or ""}

    def _fit(self) -> None:
        """Grow or shrink with the form (rows shown for the kind, the Nerd
        options folded open or shut) - after the layouts have taken the
        change in."""
        def fit() -> None:
            self._scroll.widget().adjustSize()
            self._scroll.updateGeometry()
            self.adjustSize()
        QTimer.singleShot(0, fit)

    def _update_kind(self) -> None:
        kind = self.kind.currentData()
        m3u, stalker = kind == "m3u", kind == "stalker"
        box = kind in BOX_KINDS
        # M3U needs only a URL, a portal its URL and a MAC address - hide
        # the username/password rows and relabel the address field. A box
        # on the home network takes its address and, if it asks, a login.
        self._server_lbl.setText(
            tr("playlist_m3u_url") if m3u
            else tr("playlist_portal_url") if stalker
            else tr("playlist_box_address") if box else tr("login_server"))
        self.server.setPlaceholderText(
            "https://example.com/playlist.m3u" if m3u
            else "http://portal.example.com/c/" if stalker
            else PLACEHOLDERS.get(kind, "http://server:port"))
        login = not (m3u or stalker or box) or kind in LOGIN_KINDS
        for w in (self._user_lbl, self.user, self._pw_lbl, self.pw):
            w.setVisible(login)
        hint = tr("playlist_login_optional") if box else ""
        self.user.setPlaceholderText(hint)
        self.pw.setPlaceholderText(hint)
        for w in (self._mac_lbl, self.mac):
            w.setVisible(stalker)
        self.finder.row.setVisible(box)
        if not box:
            self.finder.status.hide()
        if getattr(self, "nerd_panel", None) is not None:
            self.nerd_panel.set_kind(kind)
        self._fit()

    def _preset_picked(self, src: dict) -> None:
        idx = self.kind.findData(src["kind"])
        if idx >= 0:
            self.kind.setCurrentIndex(idx)
        self.server.setText(src["server"])
        if src.get("epg_url"):
            self.epg_url.setText(src["epg_url"])
        if not self.name.text().strip():
            self.name.setText(src["name"])

    def _box_found(self, found: dict) -> None:
        """A box picked from what answered on the network fills the form."""
        idx = self.kind.findData(found.get("kind"))
        if idx >= 0:
            self.kind.setCurrentIndex(idx)
        self.server.setText(found.get("server", ""))
        if not self.name.text().strip():
            self.name.setText(str(found.get("name", "")).split(" - ")[0])
        if found.get("login"):
            self.user.setFocus()

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

    @staticmethod
    def normal_mac(text: str) -> str | None:
        """00:1A:79:12:34:56 from what was typed (dashes, no separators,
        lower case) - or None when it is no MAC address."""
        hexd = re.sub(r"[^0-9A-Fa-f]", "", text or "")
        if len(hexd) != 12 or re.sub(r"[0-9A-Fa-f:\-\s.]", "", text or ""):
            return None
        return ":".join(hexd[i:i + 2] for i in range(0, 12, 2)).upper()

    def _validate(self) -> None:
        if self.kind.currentData() == "stalker":
            if not (self.server.text().strip()
                    and self.normal_mac(self.mac.text())):
                QMessageBox.warning(self, tr("playlist_msg_title"),
                                    tr("playlist_portal_required"))
                return
            self.accept()
            return
        if self.kind.currentData() in BOX_KINDS:
            if not self.server.text().strip():
                QMessageBox.warning(self, tr("playlist_msg_title"),
                                    tr("playlist_box_required"))
                return
            self.accept()
            return
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
        out = {
            "name": name,
            "kind": self.kind.currentData(),
            "server": self.server.text().strip(),
            "username": self.user.text().strip(),
            "password": self.pw.text().strip(),
            "epg_url": self.epg_url.text().strip(),
            "refresh": self.refresh.currentData(),
            "mac": self.normal_mac(self.mac.text()) or "",
        }
        options = (self.nerd_panel.values() if self.nerd_panel is not None
                   else dict(self._options))
        if options:
            out["options"] = options
        return out




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
        if not locked and not self.window._request_unlock():
            return
        if locked:
            self.window.parental.lock_session()
        self.overrides.update(self.mode, cid, locked=locked)
        self._populate()


class UpdateDialog(QDialog):
    """The app updating itself, from the download to the restart, in one
    window: the app's icon, what is happening, a thin bar and the buttons.

    working() while it looks, downloads and checks (Cancel stops it and
    emits cancelled); ready() offers Restart now - Install now on Windows -
    or Later (accepted / rejected); failed() and up_to_date() end with
    Close. Qt's stock progress and question boxes it replaces read like any
    program's - a question mark and No / Yes."""

    cancelled = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("update_now"))
        self.setModal(True)
        self.setMinimumWidth(460)
        self._mode = ""
        row = QHBoxLayout(self)
        row.setContentsMargins(24, 22, 24, 20)
        row.setSpacing(18)
        icon = QLabel()
        app = QApplication.instance()
        if isinstance(app, QApplication) and not app.windowIcon().isNull():
            icon.setPixmap(app.windowIcon().pixmap(56, 56))
        icon.setFixedSize(56, 56)
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(6)
        self._title = QLabel()
        self._title.setWordWrap(True)
        self._title.setStyleSheet(f"font-size:16px; font-weight:700; color:{P['text']};")
        self._body = QLabel()
        self._body.setWordWrap(True)
        self._body.setStyleSheet(f"font-size:13px; color:{P['muted']};")
        self._bar = QProgressBar(objectName="EpgBar")
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(4)
        self._status = QLabel()
        self._status.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        col.addWidget(self._title)
        col.addWidget(self._body)
        col.addSpacing(6)
        col.addWidget(self._bar)
        col.addWidget(self._status)
        col.addSpacing(12)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self._second = QPushButton()
        self._first = QPushButton()
        self._second.clicked.connect(self.reject)
        self._first.clicked.connect(self._first_clicked)
        buttons.addWidget(self._second)
        buttons.addWidget(self._first)
        col.addLayout(buttons)
        row.addLayout(col, 1)

    def _set(self, mode: str, title: str, body: str = "", first: str = "",
             second: str = "", primary: bool = False) -> None:
        self._mode = mode
        self._title.setText(title)
        self._body.setText(body)
        self._body.setVisible(bool(body))
        working = mode == "working"
        self._bar.setVisible(working)
        self._status.setVisible(working)
        self._first.setText(first)
        self._first.setObjectName("Primary" if primary else "")
        self._first.style().unpolish(self._first)
        self._first.style().polish(self._first)
        self._second.setText(second)
        self._second.setVisible(bool(second))
        self._first.setDefault(True)
        self._first.setFocus()

    def working(self, title: str, status: str = "", fraction: float | None = None) -> None:
        """Busy until *fraction* (0-1) is known; *status* under the bar."""
        if self._mode != "working" or self._title.text() != title:
            self._set("working", title, first=tr("common_cancel"))
        self._status.setText(status)
        self._status.setVisible(bool(status))
        if fraction is None:
            self._bar.setRange(0, 0)
        else:
            self._bar.setRange(0, 1000)
            self._bar.setValue(int(max(0.0, min(1.0, fraction)) * 1000))

    def ready(self, version: str, install: bool) -> None:
        self._set("ready", tr("update_ready_title", version=version),
                  tr("update_ready_install" if install else "update_ready_restart"),
                  tr("update_install_now" if install else "update_restart_now"),
                  tr("update_later"), primary=True)

    def failed(self, message: str) -> None:
        self._set("done", tr("update_failed_title"), message, tr("common_close"))

    def up_to_date(self) -> None:
        self._set("done", "\u2713 " + tr("about_up_to_date"), first=tr("common_close"))

    def mode(self) -> str:
        return self._mode

    def _first_clicked(self) -> None:
        if self._mode == "ready":
            self.accept()
        else:
            self.reject()

    def reject(self) -> None:
        # Closed or Esc while it works is a cancel, as the button is.
        if self._mode == "working":
            self._mode = "cancelled"
            self.cancelled.emit()
        super().reject()



class SupporterDialog(QDialog):
    """The supporter key: paste it (it is checked here, offline), or - for
    a supporter - the thank-you, with a way to donate again."""

    def __init__(self, parent, settings, donate=None) -> None:
        super().__init__(parent)
        from PyQt6.QtWidgets import QLineEdit

        from ..core import supporter
        from .widgets import HeartButton, _SidebarLogo
        self._settings = settings
        self.setWindowTitle(tr("supporter_title"))
        self.setMinimumWidth(480)
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 22, 24, 20)
        col.setSpacing(10)
        top = QHBoxLayout()
        heart = HeartButton(size=22)
        heart.setFixedSize(46, 46)
        heart.setFlat(True)
        self.heart = heart
        top.addWidget(heart, 0, Qt.AlignmentFlag.AlignTop)
        # A supporter sees their mark: the logo as the sidebar shows it now.
        mark = _SidebarLogo()
        mark.set_supporter(True)
        mark.setCursor(Qt.CursorShape.ArrowCursor)
        mark.setFixedWidth(mark.LOGO_W + 8)
        self.mark = mark
        top.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        words = QVBoxLayout()
        self.head = QLabel()
        self.head.setStyleSheet(f"font-size:16px; font-weight:700; color:{P['text']};")
        self.sub = QLabel()
        self.sub.setWordWrap(True)
        self.sub.setStyleSheet(f"font-size:13px; color:{P['text2']};")
        words.addWidget(self.head)
        words.addWidget(self.sub)
        top.addLayout(words, 1)
        col.addLayout(top)
        self.key = QLineEdit(str(settings.value("supporter_key", "") or ""))
        self.key.setPlaceholderText(tr("supporter_paste"))
        self.key.setAccessibleName(tr("supporter_key"))
        col.addWidget(self.key)
        self.state = QLabel()
        self.state.setWordWrap(True)
        col.addWidget(self.state)
        where = QLabel(tr("supporter_where"))
        where.setWordWrap(True)
        where.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        self.where = where
        col.addWidget(where)
        buttons = QHBoxLayout()
        self.remove = QPushButton(tr("supporter_remove"))
        buttons.addWidget(self.remove)
        buttons.addStretch(1)
        self.donate_btn = QPushButton(tr("donate_support") + "…")
        if donate is not None:
            self.donate_btn.clicked.connect(lambda _c=False: donate())
        else:
            self.donate_btn.hide()
        close = QPushButton(tr("common_close"), objectName="Primary")
        close.clicked.connect(self.accept)
        buttons.addWidget(self.donate_btn)
        buttons.addWidget(close)
        col.addLayout(buttons)
        self._supporter = supporter
        self.key.textChanged.connect(self._check)
        self.remove.clicked.connect(lambda _c=False: self.key.setText(""))
        self._check()

    def _check(self, *_a) -> None:
        text = self.key.text().strip()
        found = self._supporter.parse(text) if text else None
        if found is not None:
            self._settings.setValue("supporter_key", text)
            from .qt_i18n import format_month
            date = format_month(found["since"])
            self.head.setText(tr("supporter_thanks"))
            self.sub.setText(tr("supporter_since", date=date))
            self.state.setText("✓ " + tr("supporter_ok", date=date))
            self.state.setStyleSheet(f"font-size:12px; color:{P.get('ok', P['text2'])};")
            self.donate_btn.setText(tr("supporter_again") + "…")
        else:
            if not text:
                self._settings.remove("supporter_key")
            self.head.setText(tr("supporter_title"))
            self.sub.setText(tr("supporter_intro"))
            self.state.setText(tr("supporter_bad") if text else "")
            self.state.setStyleSheet(f"font-size:12px; color:{P['error']};")
            self.donate_btn.setText(tr("donate_support") + "…")
        self.heart.setVisible(found is None)
        self.mark.setVisible(found is not None)
        self.mark.setToolTip(tr("supporter_tip", date=date) if found is not None else "")
        self.where.setVisible(found is None)
        self.state.setVisible(bool(text))
        self.remove.setVisible(bool(text))
        self.supporter = found


def answer_text(a: dict) -> str:
    """An admin's answer in the app's own words: its fixed part in this
    language, their own words as written."""
    code, version = str(a.get("code") or ""), str(a.get("version") or "")
    fixed = {"thanks": "fbr_thanks", "known": "fbr_known", "needinfo": "fbr_needinfo"}
    if code == "fixed":
        said = tr("fbr_fixed", version=version) if version else tr("fbr_fixed_next")
    else:
        said = tr(fixed[code]) if code in fixed else ""
    return " ".join(p for p in (said, str(a.get("text") or "").strip()) if p)


def answers_html(tickets: list) -> str:
    """The answered reports, newest first: what the person wrote (kept on
    this computer only) and each answer with its day."""
    import datetime as _dt
    import html as _html
    rows = []
    ordered = sorted(tickets, key=lambda t: -max((a.get("t", 0) for a in t.get("r") or [{}]), default=0))
    for n, t in enumerate(ordered):
        said = "".join(
            f"<p style='margin:4px 0 0'><span style='color:{P['muted']}'>"
            f"{_dt.date.fromtimestamp(int(a.get('t') or 0)).isoformat()}</span>&nbsp; "
            f"{_html.escape(answer_text(a))}</p>" for a in t.get("r") or [])
        rows.append(f"<p style='margin:{12 if n else 0}px 0 0 0; color:{P['text2']}'>"
                    f"{_html.escape(tr('fbr_you', text=str(t.get('w') or '…')))}</p>{said}")
    return "".join(rows)


class FeedbackDialog(QDialog):
    """Feedback, asked the way the answers are useful: what it is about,
    for something that does not work where and how often, the person's
    words, and what to attach - each on its own, versions only by default.
    What is sent can be shown in full and changed before Send."""

    TOPICS = (("bug", "fb_t_bug"), ("idea", "fb_t_idea"), ("like", "fb_t_like"), ("question", "fb_t_question"))
    AREAS = (("playback", "fb_a_play"), ("guide", "fb_a_guide"), ("recording", "fb_a_rec"),
             ("lists", "fb_a_lists"), ("settings", "fb_a_settings"), ("cast", "fb_a_cast"),
             ("updates", "fb_a_updates"), ("other", "fb_a_other"))
    OFTEN = (("always", "fb_o_always"), ("sometimes", "fb_o_sometimes"), ("once", "fb_o_once"))

    def __init__(self, parent, details, has_log_file: bool = False, answers: list | None = None) -> None:
        super().__init__(parent)
        from PyQt6.QtGui import QFont
        from PyQt6.QtWidgets import QButtonGroup, QCheckBox, QComboBox, QGridLayout, QPlainTextEdit, QScrollArea

        from .settings_ui import Chip, _Flow
        self._details = details
        self._edited = False
        self.setWindowTitle(tr("feedback_title"))
        self.setMinimumWidth(600)
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 20, 24, 18)
        col.setSpacing(8)

        def label(text: str, size: int = 12, colour: str = "", bold: bool = True) -> QLabel:
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"font-size:{size}px; font-weight:{700 if bold else 400};"
                              f" color:{colour or P['muted']};")
            return lbl
        col.addWidget(label(tr("feedback_title"), 17, P["text"]))
        col.addWidget(label(tr("feedback_intro"), 13, P["text2"], bold=False))
        # Answers to earlier feedback (asked for with its code), first.
        self.answers_box: QWidget | None = None
        if answers:
            col.addSpacing(4)
            col.addWidget(label(tr("fbr_title")))
            inner = QLabel(answers_html(answers))
            inner.setWordWrap(True)
            inner.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            inner.setTextFormat(Qt.TextFormat.RichText)
            inner.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            inner.setStyleSheet(f"font-size:13px; color:{P['text']}; padding:10px 12px;")
            scroll = QScrollArea()
            scroll.setWidget(inner)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setStyleSheet(f"QScrollArea {{ background:{P['input']}; border-radius:10px; }}"
                                 f" QScrollArea > QWidget > QWidget {{ background:transparent; }}")
            # As tall as the answers at the window's width, up to a few of them.
            scroll.setFixedHeight(min(180, inner.heightForWidth(552) + 4))
            self.answers_box = scroll
            col.addWidget(scroll)
        col.addSpacing(4)
        col.addWidget(label(tr("fb_topic")))
        chips = _Flow(6, False)
        self._topics = QButtonGroup(self)
        self._topics.setExclusive(True)
        self.topic_chips: dict[str, Chip] = {}
        for key, text in self.TOPICS:
            chip = Chip(tr(text), False)
            self._topics.addButton(chip)
            self.topic_chips[key] = chip
            chips.flow.addWidget(chip)
        col.addWidget(chips)
        self.bug_row = QWidget()
        bug = QGridLayout(self.bug_row)
        bug.setContentsMargins(0, 2, 0, 0)
        bug.setHorizontalSpacing(10)
        self.area = QComboBox()
        for key, text in self.AREAS:
            self.area.addItem(tr(text), key)
        self.often = QComboBox()
        for key, text in self.OFTEN:
            self.often.addItem(tr(text), key)
        bug.addWidget(label(tr("fb_where")), 0, 0)
        bug.addWidget(label(tr("fb_often")), 0, 1)
        bug.addWidget(self.area, 1, 0)
        bug.addWidget(self.often, 1, 1)
        self.bug_row.hide()
        col.addWidget(self.bug_row)
        box_style = (f"QPlainTextEdit {{ background: {P['input']}; color: {P['text']};"
                     f" border: none; border-radius: 10px; padding: 8px 10px;"
                     f" selection-background-color: {P['sel']}; }}")
        self.message = QPlainTextEdit()
        self.message.setPlaceholderText(tr("feedback_placeholder"))
        self.message.setStyleSheet(box_style)
        self.message.setFixedHeight(104)
        col.addWidget(self.message)
        col.addWidget(label(tr("fb_attach")))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        self.attach = {"versions": QCheckBox(tr("fb_att_versions")), "log": QCheckBox(tr("fb_att_log")),
                       "file": QCheckBox(tr("fb_att_file")), "source": QCheckBox(tr("fb_att_source"))}
        self.attach["versions"].setChecked(True)
        self.attach["file"].setVisible(has_log_file)
        for n, key in enumerate(("versions", "log", "source", "file")):
            grid.addWidget(self.attach[key], n // 2, n % 2)
        col.addLayout(grid)
        self.show_btn = QPushButton(tr("fb_show") + "  \u25be")
        self.show_btn.setFlat(True)
        self.show_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.show_btn.setStyleSheet(f"QPushButton {{ border:none; background:transparent; padding:2px 0;"
                                    f" text-align:left; color:{P['text2']}; font-size:12px; }}"
                                    f" QPushButton:hover {{ color:{P['text']}; }}")
        col.addWidget(self.show_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.preview = QPlainTextEdit()
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.TypeWriter)
        self.preview.setFont(mono)
        self.preview.setStyleSheet(box_style)
        self.preview.setFixedHeight(190)
        self.preview.hide()
        col.addWidget(self.preview)
        # An answer in the app: a code kept here, its hash with the report.
        self.answers = QCheckBox(tr("fb_answers_opt"))
        self.answers.setChecked(True)
        self.answers.setToolTip(tr("fb_answers_note"))
        col.addWidget(self.answers)
        col.addWidget(label(tr("fb_answers_note") + " " + tr("report_privacy"), 12, bold=False))
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(tr("common_cancel"))
        self.send_btn = QPushButton(tr("report_send"), objectName="Primary")
        cancel.clicked.connect(self.reject)
        self.send_btn.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.send_btn)
        col.addLayout(buttons)
        self._topics.buttonToggled.connect(lambda *_a: self._topic_changed())
        for box in self.attach.values():
            box.toggled.connect(lambda *_a: self._refresh())
        # The log's part that matters follows "Where?".
        self.area.currentIndexChanged.connect(lambda *_a: self._refresh())
        self.message.textChanged.connect(self._ready)
        self.preview.textChanged.connect(self._preview_edited)
        self.show_btn.clicked.connect(self._toggle_preview)
        self._refresh()
        self._ready()
        self.message.setFocus()

    def topic(self) -> str:
        return next((k for k, chip in self.topic_chips.items() if chip.isChecked()), "")

    def _topic_changed(self) -> None:
        t = self.topic()
        self.bug_row.setVisible(t == "bug")
        self.message.setPlaceholderText(tr({"bug": "fb_ph_bug", "idea": "fb_ph_idea", "like": "fb_ph_like",
                                            "question": "fb_ph_question"}.get(t, "feedback_placeholder")))
        if t == "bug" and not self._edited:
            self.attach["log"].setChecked(True)
            self.attach["source"].setChecked(True)
        self._refresh()
        self._ready()
        self._fit()

    def _fit(self) -> None:
        """As tall as what it holds at its width: the questions and the
        preview come and go, and wrapped text (the topics, the notes) is
        taller in some languages - a plain size hint leaves it overlapping."""
        lay = self.layout()
        if lay is None:
            return
        lay.invalidate()
        lay.activate()
        w = max(self.width(), self.minimumWidth())
        h = lay.totalHeightForWidth(w) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        h = max(h, lay.totalSizeHint().height())
        self.setMinimumHeight(h)
        self.resize(w, h)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit()

    def options(self) -> dict:
        out: dict = {k: box.isChecked() and box.isVisibleTo(self) for k, box in self.attach.items()}
        out["area"] = str(self.area.currentData()) if self.topic() == "bug" else ""
        return out

    def _refresh(self) -> None:
        if self._edited:
            return
        self._setting = True
        self.preview.setPlainText(self._details(self.options()))
        self._setting = False

    def _preview_edited(self) -> None:
        if not getattr(self, "_setting", False):
            self._edited = True

    def _toggle_preview(self) -> None:
        on = not self.preview.isVisible()
        self.preview.setVisible(on)
        self.show_btn.setText(tr("fb_hide" if on else "fb_show") + ("  \u25b4" if on else "  \u25be"))
        self._fit()

    def _ready(self) -> None:
        self.send_btn.setEnabled(bool(self.topic()) and bool(self.message.toPlainText().strip()))

    def fields(self) -> dict[str, str]:
        out = {"topic": self.topic()}
        if out["topic"] == "bug":
            out["area"] = str(self.area.currentData())
            out["often"] = str(self.often.currentData())
        return out

    def payload(self) -> str:
        parts = [self.message.toPlainText().strip()]
        extra = self.preview.toPlainText().strip()
        if extra:
            parts.append(extra)
        return "\n\n".join(p for p in parts if p)


class ReportDialog(QDialog):
    """An error report or feedback, shown in full before anything is sent:
    what it holds, that nothing in it identifies a stream or a person, and
    a box where any of it can be changed or deleted. Sent only on Send."""

    def __init__(self, parent, kind: str, text: str, intro: str = "") -> None:
        super().__init__(parent)
        from PyQt6.QtGui import QFont
        from PyQt6.QtWidgets import QCheckBox, QPlainTextEdit
        self.kind = kind
        feedback = kind == "feedback"
        self.setWindowTitle(tr("feedback_title") if feedback else tr("report_title"))
        self.setMinimumWidth(600)
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 22, 24, 20)
        col.setSpacing(8)
        head = QLabel(tr("feedback_title") if feedback else tr("report_title"))
        head.setStyleSheet(f"font-size:16px; font-weight:700; color:{P['text']};")
        col.addWidget(head)
        about = QLabel(intro or (tr("feedback_intro") if feedback else tr("report_intro")))
        about.setWordWrap(True)
        about.setStyleSheet(f"font-size:13px; color:{P['text2']};")
        col.addWidget(about)
        box_style = (f"QPlainTextEdit {{ background: {P['input']}; color: {P['text']};"
                     f" border: none; border-radius: 8px; padding: 6px 8px;"
                     f" selection-background-color: {P['sel']}; }}")
        self.message: QPlainTextEdit | None = None
        if feedback:
            self.message = QPlainTextEdit()
            self.message.setPlaceholderText(tr("feedback_placeholder"))
            self.message.setStyleSheet(box_style)
            self.message.setFixedHeight(110)
            col.addWidget(self.message)
        self.include = QCheckBox(tr("report_include"))
        self.include.setChecked(not feedback)
        col.addWidget(self.include)
        what = QLabel(tr("report_what_is_sent"))
        what.setStyleSheet(f"font-size:12px; font-weight:700; color:{P['muted']};")
        col.addWidget(what)
        self.text = QPlainTextEdit(text)
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.TypeWriter)
        self.text.setFont(mono)
        self.text.setStyleSheet(box_style)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.text.setMinimumHeight(self.text.fontMetrics().lineSpacing() * 12 + 18)
        col.addWidget(self.text, 1)
        self.include.toggled.connect(self.text.setVisible)
        self.include.toggled.connect(what.setVisible)
        self.text.setVisible(self.include.isChecked())
        what.setVisible(self.include.isChecked())
        privacy = QLabel(tr("report_privacy"))
        privacy.setWordWrap(True)
        privacy.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        col.addWidget(privacy)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(tr("common_cancel"))
        self.send_btn = QPushButton(tr("report_send"), objectName="Primary")
        cancel.clicked.connect(self.reject)
        self.send_btn.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.send_btn)
        col.addLayout(buttons)
        if self.message is not None:
            self.message.setFocus()

    def payload(self) -> str:
        """What is sent: the person's words, then the report as they left it."""
        parts = []
        if self.message is not None and self.message.toPlainText().strip():
            parts.append(self.message.toPlainText().strip())
        if self.include.isChecked() and self.text.toPlainText().strip():
            parts.append(self.text.toPlainText().strip())
        return "\n\n".join(parts)


class ProblemDialog(QDialog):
    """Something did not work, said plainly: what, in a sentence, then the
    details - what was tried and the exact error or code - in a box that can
    be selected and copied (a forum post, a bug report). In the app's own
    look, not a system message box."""

    def __init__(self, parent, title: str, summary: str, detail: str,
                 state: str = "fail") -> None:
        super().__init__(parent)
        from PyQt6.QtGui import QFont, QGuiApplication
        from PyQt6.QtWidgets import QPlainTextEdit
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(520)
        row = QHBoxLayout(self)
        row.setContentsMargins(24, 22, 24, 20)
        row.setSpacing(16)
        # "fail" (✕), "warn" (!) or "ok" (✓: what a check that worked did).
        colour = {"warn": P.get("warn", P["muted"]), "ok": P.get("ok", P["muted"]),
                  "info": P["muted"]}.get(state, P["error"])
        mark = QLabel({"warn": "!", "ok": "✓", "info": "i"}.get(state, "✕"))
        mark.setFixedSize(40, 40)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setStyleSheet(f"border-radius:20px; border:2px solid {colour}; color:{colour};"
                           " font-size:18px; font-weight:800;")
        row.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(6)
        head = QLabel(title)
        head.setWordWrap(True)
        head.setStyleSheet(f"font-size:16px; font-weight:700; color:{P['text']};")
        body = QLabel(summary)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet(f"font-size:13px; color:{P['text2']};")
        col.addWidget(head)
        col.addWidget(body)
        col.addSpacing(6)
        what = QLabel(tr("problem_details"))
        what.setStyleSheet(f"font-size:12px; font-weight:700; color:{P['muted']};")
        col.addWidget(what)
        self.details = QPlainTextEdit(detail)
        self.details.setReadOnly(True)
        self.details.setObjectName("NerdText")
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                             | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.TypeWriter)
        self.details.setFont(mono)
        # Its own colours: the box's style elsewhere belongs to Settings,
        # and a system default here was light text on white.
        self.details.setStyleSheet(
            f"QPlainTextEdit {{ background: {P['input']}; color: {P['text']};"
            f" border: none; border-radius: 8px; padding: 6px 8px;"
            f" selection-background-color: {P['sel']}; }}")
        self.details.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        lines = max(6, min(16, detail.count("\n") + 4))
        self.details.setFixedHeight(self.details.fontMetrics().lineSpacing() * lines + 18)
        col.addWidget(self.details)
        col.addSpacing(10)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.copied = QLabel("")
        self.copied.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        buttons.addWidget(self.copied)
        buttons.addStretch(1)
        self.copy_btn = QPushButton(tr("problem_copy"))
        close = QPushButton(tr("common_close"), objectName="Primary")

        def copy() -> None:
            QGuiApplication.clipboard().setText(f"{title}\n{summary}\n\n{detail}")
            self.copied.setText("✓ " + tr("problem_copied"))
        self.copy_btn.clicked.connect(copy)
        close.clicked.connect(self.accept)
        buttons.addWidget(self.copy_btn)
        buttons.addWidget(close)
        col.addLayout(buttons)
        row.addLayout(col, 1)
        close.setDefault(True)
        close.setFocus()


def health_secs(value: float | None) -> str:
    """Seconds as the app's language writes them ("2,4 s"); a dash for none."""
    if value is None:
        return "–"
    from .qt_i18n import app_locale
    return tr("health_secs", s=app_locale().toString(float(value), "f", 1))


class StreamHealthDialog(QDialog):
    """A channel's stream health (Nerd, core/health.py) in the look of the
    problem window: a mark in the colour of how it is doing, a sentence,
    then its numbers - which can be copied."""

    STATES = {"ok": ("ok", "✓"), "warn": ("warn", "!"), "error": ("error", "✕")}

    def __init__(self, parent, name: str, entry: dict | None) -> None:
        super().__init__(parent)
        from PyQt6.QtGui import QGuiApplication
        from PyQt6.QtWidgets import QGridLayout

        from ..core import health
        from .qt_i18n import format_when
        e = entry or {}
        grade = health.grade(e)
        self.setWindowTitle(tr("nerd_card_health"))
        self.setModal(True)
        self.setMinimumWidth(460)
        row = QHBoxLayout(self)
        row.setContentsMargins(24, 22, 24, 20)
        row.setSpacing(16)
        key, glyph = self.STATES.get(grade, ("muted", "i"))
        colour = P.get(key, P["muted"])
        mark = QLabel(glyph)
        mark.setFixedSize(40, 40)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setStyleSheet(f"border-radius:20px; border:2px solid {colour}; color:{colour};"
                           " font-size:18px; font-weight:800;")
        row.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(6)
        head = QLabel(name)
        head.setWordWrap(True)
        head.setStyleSheet(f"font-size:16px; font-weight:700; color:{P['text']};")
        self.summary = QLabel(tr({"ok": "health_sum_ok", "warn": "health_sum_warn",
                                  "error": "health_sum_error"}.get(grade, "health_sum_none")))
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(f"font-size:13px; color:{P['text2']};")
        col.addWidget(head)
        col.addWidget(self.summary)
        col.addSpacing(8)
        starts, fails, drops = health.counts(e)
        played, err_at = health.when(e, "at"), health.when(e, "err_at")
        self.lines: list[tuple[str, str]] = [
            (tr("health_starts"), str(starts)),
            (tr("health_fails"), str(fails)),
            (tr("health_ttff_last"), health_secs(health.last_start(e))),
            (tr("health_ttff_avg"), health_secs(health.average(e))),
            (tr("health_drops"), str(drops)),
            (tr("health_played"), format_when(played) if played else "–"),
        ]
        if e.get("err"):
            when = f" ({format_when(err_at)})" if err_at else ""
            self.lines.append((tr("health_error"), f"{e['err']}{when}"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        for i, (label, value) in enumerate(self.lines):
            lbl = QLabel(label)
            lbl.setStyleSheet(f"font-size:12px; color:{P['muted']};")
            val = QLabel(value)
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            val.setStyleSheet(f"font-size:13px; color:{P['text']};")
            grid.addWidget(lbl, i, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(val, i, 1)
        grid.setColumnStretch(1, 1)
        col.addLayout(grid)
        col.addSpacing(10)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.copied = QLabel("")
        self.copied.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        buttons.addWidget(self.copied)
        buttons.addStretch(1)
        self.copy_btn = QPushButton(tr("problem_copy"))
        close = QPushButton(tr("common_close"), objectName="Primary")
        text = "\n".join([name, self.summary.text(), ""] + [f"{a}: {b}" for a, b in self.lines])

        def copy() -> None:
            QGuiApplication.clipboard().setText(text)
            self.copied.setText("✓ " + tr("problem_copied"))
        self.copy_btn.clicked.connect(copy)
        close.clicked.connect(self.accept)
        buttons.addWidget(self.copy_btn)
        buttons.addWidget(close)
        col.addLayout(buttons)
        row.addLayout(col, 1)
        close.setDefault(True)
        close.setFocus()


class ClearHistoryDialog(QDialog):
    """Clear what was watched, as a browser clears its history: a time range
    and what to clear. *preselect*: "channels" or "titles" ticks only that
    (the Recently viewed list it was opened from)."""

    def __init__(self, parent=None, preselect: str | None = None) -> None:
        super().__init__(parent)
        from PyQt6.QtWidgets import QCheckBox

        from ..core.privacy import RANGES
        self.setWindowTitle(tr("clear_title"))
        self.setModal(True)
        self.setMinimumWidth(460)
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 22, 24, 20)
        col.setSpacing(10)
        head = QLabel(tr("clear_title"))
        head.setStyleSheet(f"font-size:16px; font-weight:700; color:{P['text']};")
        sub = QLabel(tr("clear_sub"))
        sub.setWordWrap(True)
        sub.setStyleSheet(f"font-size:13px; color:{P['muted']};")
        col.addWidget(head)
        col.addWidget(sub)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("clear_range")))
        self.range = QComboBox()
        for rid, secs in RANGES:
            self.range.addItem(tr(f"clear_range_{rid}"), secs)
        self.range.setCurrentIndex(0)
        row.addWidget(self.range, 1)
        col.addLayout(row)
        self.channels = QCheckBox(tr("clear_channels"))
        self.titles = QCheckBox(tr("clear_titles"))
        self.positions = QCheckBox(tr("clear_positions"))
        for box in (self.channels, self.titles, self.positions):
            box.setChecked(True)
            col.addWidget(box)
        if preselect == "channels":
            self.titles.setChecked(False)
            self.positions.setChecked(False)
        elif preselect == "titles":
            self.channels.setChecked(False)
        note = QLabel(tr("clear_note"))
        note.setWordWrap(True)
        note.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        col.addWidget(note)
        col.addSpacing(6)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(tr("common_cancel"))
        self.go = QPushButton(tr("clear_go"), objectName="Danger")
        cancel.clicked.connect(self.reject)
        self.go.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.go)
        col.addLayout(buttons)
        for box in (self.channels, self.titles, self.positions):
            box.toggled.connect(self._sync)
        self._sync()

    def _sync(self, *_a) -> None:
        self.go.setEnabled(self.channels.isChecked() or self.titles.isChecked()
                           or self.positions.isChecked())

    def choice(self) -> dict:
        return {"seconds": int(self.range.currentData() or 0),
                "channels": self.channels.isChecked(), "titles": self.titles.isChecked(),
                "positions": self.positions.isChecked()}


class ChannelNumberDialog(QDialog):
    """Nerd: a channel's own number, typed or stepped. *taken(n)* says
    whether another channel has *n* by hand, and its name if known: that
    one gives the number up."""

    def __init__(self, parent, name: str, number: int, taken) -> None:
        super().__init__(parent)
        from ..core.channel_numbers import MAX_NUMBER
        from .settings_ui import Stepper, settings_style
        self._taken = taken
        self.setWindowTitle(tr("chno_title"))
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setStyleSheet(settings_style())
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 22, 24, 20)
        col.setSpacing(10)
        head = QLabel(name or tr("chno_title"))
        head.setWordWrap(True)
        head.setStyleSheet(f"font-size:16px; font-weight:700; color:{P['text']};")
        sub = QLabel(tr("chno_sub"))
        sub.setWordWrap(True)
        sub.setStyleSheet(f"font-size:13px; color:{P['muted']};")
        col.addWidget(head)
        col.addWidget(sub)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("chno_label")))
        row.addStretch(1)
        self.stepper = Stepper(lo=1, hi=MAX_NUMBER, value=max(1, min(MAX_NUMBER, int(number or 1))),
                               width=150)
        self.stepper.setAccessibleName(tr("chno_label"))
        row.addWidget(self.stepper)
        col.addLayout(row)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"font-size:12px; color:{P.get('warn', P['muted'])};")
        col.addWidget(self.note)
        col.addSpacing(6)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(tr("common_cancel"))
        ok = QPushButton(tr("common_save"), objectName="Primary")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        ok.setDefault(True)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        col.addLayout(buttons)
        self.stepper.valueChanged.connect(self._said)
        self._said()
        self.stepper.spin.setFocus()
        self.stepper.spin.selectAll()

    def _said(self, *_a) -> None:
        other, name = self._taken(self.value())
        self.note.setText((tr("chno_taken_by", name=name) if name else tr("chno_taken"))
                          if other else "")
        self.note.setVisible(bool(other))

    def value(self) -> int:
        return self.stepper.value()


# The languages Settings names in words of the app's own; the rest of a
# channel's list is named in its own language (QLocale).
_LANG_WORDS = {
    "swe": "lang_swe", "eng": "lang_eng", "nor": "lang_nor", "dan": "lang_dan",
    "fin": "lang_fin", "ger": "lang_ger", "fre": "lang_fre", "spa": "lang_spa",
    "ita": "lang_ita", "por": "lang_por", "pol": "lang_pol", "ara": "lang_ara",
    "tur": "lang_tur",
}


def language_choices() -> list[tuple[str, str]]:
    """(ISO 639-2 code, name) for a channel's language lists: those
    Settings offers first, in its order, then the others by name."""
    from PyQt6.QtCore import QLocale

    from ..core.channel_prefs import LANGUAGES
    out = [(code, tr(key)) for code, key in _LANG_WORDS.items()]
    rest = []
    for code, other in LANGUAGES.items():
        if code in _LANG_WORDS:
            continue
        name = QLocale(other[0]).nativeLanguageName() if other else ""
        rest.append((code, name[:1].upper() + name[1:] if name else code))
    rest.sort(key=lambda kv: kv[1].casefold())
    return out + rest


class ChannelSettingsDialog(QDialog):
    """Nerd: the settings of a channel's own (core/channel_prefs.py) - its
    audio and subtitle language, deinterlacing, aspect ratio and audio
    delay, each "Default" (the app's setting) until chosen. A line under
    the name says whether the channel has its own; Reset puts every row
    back to Default, Save keeps what is chosen. *delay*: the audio delay
    per channel is on (Nerd's switch), so its row is offered."""

    DELAY_MS = 3000

    def __init__(self, parent, name: str, prefs: dict | None, delay: bool = True) -> None:
        super().__init__(parent)
        from PyQt6.QtWidgets import QGridLayout

        from ..core import channel_prefs
        from .settings_ui import GridSpin, Stepper, settings_style
        own = channel_prefs.clean(prefs)
        self.setWindowTitle(tr("chanprefs_title"))
        self.setModal(True)
        self.setMinimumWidth(480)
        self.setStyleSheet(settings_style())
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 22, 24, 20)
        col.setSpacing(10)
        head = QLabel(name or tr("chanprefs_title"))
        head.setWordWrap(True)
        head.setStyleSheet(f"font-size:16px; font-weight:700; color:{P['text']};")
        self.marker = QLabel("")
        self.marker.setWordWrap(True)
        sub = QLabel(tr("chanprefs_sub"))
        sub.setWordWrap(True)
        sub.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        col.addWidget(head)
        col.addWidget(self.marker)
        col.addWidget(sub)
        col.addSpacing(4)
        default = ("", tr("label_default"))
        langs = language_choices()
        self.alang = self._combo([default, *langs], own.get("alang", ""))
        self.slang = self._combo([default, (channel_prefs.SUB_OFF, tr("option_sub_off")), *langs],
                                 own.get("slang", ""))
        self.deint = self._combo([default, ("on", tr("option_on")), ("off", tr("option_off"))],
                                 own.get("deint", ""))
        self.aspect = self._combo(
            [default, ("auto", tr("option_aspect_auto")), ("16:9", "16:9"), ("4:3", "4:3"),
             ("21:9", "21:9"), ("2.35:1", "2.35:1"), ("stretch", tr("option_aspect_stretch"))],
            own.get("aspect", ""))
        rows: list[tuple[str, QWidget]] = [
            (tr("setting_audio_lang"), self.alang), (tr("setting_sub_lang"), self.slang),
            (tr("setting_deinterlace"), self.deint), (tr("setting_aspect_ratio"), self.aspect)]
        self.delay: Stepper | None = None
        self._delay_kept = channel_prefs.delay(own.get("adelay"))
        if delay:
            ms = int(round(self._delay_kept * 1000))
            grid = list(range(-self.DELAY_MS, self.DELAY_MS + 1, 50))
            spin = GridSpin(grid, self._delay_text, min(grid[0], ms), max(grid[-1], ms))
            spin.setValue(ms)
            self.delay = Stepper(spin, width=max(170, spin.widest() + 80))
            self.delay.setAccessibleName(tr("opt_audio_delay"))
            rows.append((tr("opt_audio_delay"), self.delay))
        form = QGridLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(8)
        for i, (label, widget) in enumerate(rows):
            lbl = QLabel(label)
            lbl.setStyleSheet(f"font-size:13px; color:{P['text2']};")
            form.addWidget(lbl, i, 0)
            form.addWidget(widget, i, 1)
        form.setColumnStretch(1, 1)
        col.addLayout(form)
        col.addSpacing(8)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.reset_btn = QPushButton(tr("common_reset"))
        cancel = QPushButton(tr("common_cancel"))
        save = QPushButton(tr("common_save"), objectName="Primary")
        self.reset_btn.clicked.connect(self.reset)
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.accept)
        save.setDefault(True)
        buttons.addWidget(self.reset_btn)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        col.addLayout(buttons)
        for box in (self.alang, self.slang, self.deint, self.aspect):
            box.currentIndexChanged.connect(self._sync)
        if self.delay is not None:
            self.delay.valueChanged.connect(self._sync)
        self._sync()

    @staticmethod
    def _combo(items: list[tuple[str, str]], current: str) -> QComboBox:
        box = QComboBox()
        for value, label in items:
            box.addItem(label, value)
        idx = box.findData(current)
        if idx < 0 and current:
            box.addItem(current, current)       # kept, though no longer offered
            idx = box.count() - 1
        box.setCurrentIndex(max(0, idx))
        box.setMaxVisibleItems(14)
        return box

    @staticmethod
    def _delay_text(ms: int) -> str:
        return tr("opt_delay_default") if ms == 0 else f"{ms / 1000:+.2f} s"

    def choice(self) -> dict:
        """What is chosen, as the channel keeps it ({}: none of its own)."""
        from ..core import channel_prefs
        return channel_prefs.clean({
            "alang": self.alang.currentData(), "slang": self.slang.currentData(),
            "deint": self.deint.currentData(), "aspect": self.aspect.currentData(),
            "adelay": (self.delay.value() / 1000 if self.delay is not None
                       else self._delay_kept)})

    def reset(self) -> None:
        """Every row back to Default."""
        for box in (self.alang, self.slang, self.deint, self.aspect):
            box.setCurrentIndex(0)
        self._delay_kept = 0.0
        if self.delay is not None:
            self.delay.setValue(0)
        self._sync()

    def has_own(self) -> bool:
        return bool(self.choice())

    def _sync(self, *_a) -> None:
        from . import theme
        own = self.has_own()
        self.marker.setText(("● " + tr("chanprefs_own")) if own else tr("chanprefs_none"))
        self.marker.setStyleSheet(
            f"font-size:13px; color:{theme.ACCENT if own else P['muted']};"
            f" font-weight:{600 if own else 400};")
        self.reset_btn.setEnabled(own)
