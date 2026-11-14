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
            self.nerd_panel = SourceNerdPanel(self.kind.currentData(), self._options, self)
            lay.addWidget(self.nerd_panel)
            self.nerd_panel.toggle.toggled.connect(lambda _on: self._fit())
        lay.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.kind.currentIndexChanged.connect(self._update_kind)
        self._update_kind()

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
        from .widgets import HeartButton
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
            date = found["since"].isoformat()
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
        self.heart.set_supporter(found is not None)
        self.remove.setVisible(bool(text))
        self.supporter = found


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
