"""Dialogs: login, playlist editor, EPG guide, content manager, the update."""

from __future__ import annotations

import re
from typing import Any

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QSpinBox, QVBoxLayout, QWidget,
)

from ..i18n import tr
from .box_finder import (
    BOX_KINDS, LOGIN_KINDS, MEDIA_KINDS, PIN_KINDS, PLACEHOLDERS, FindOnNetwork, add_box_kinds,
    add_media_kinds,
)
from .media_signin import MediaSignIn
from .theme import P


class KindOwnFields:
    """Each kind of source keeps its own address and login (and a portal's
    MAC): another kind picked shows that kind's own - empty for one not
    filled in - never what was typed for the kind before. An Xtream login
    stayed in the fields of every kind picked after it: sent to a Tvheadend
    or a box found on the network, and on a source's page stored at once
    as the new kind. Now a kind changed is stored only once its own are
    there, and the kind picked back brings back what it had.

    *fields*: the line edits by name; *signin*, the form's media server
    sign-in, which keeps its sign-in while the form fills its own fields."""

    def __init__(self, kind: QComboBox, fields: dict[str, QLineEdit],
                 signin: MediaSignIn | None = None) -> None:
        self._kind, self._fields, self._signin = kind, fields, signin
        self._shown = kind.currentData()
        self._by_kind: dict[Any, dict[str, str]] = {}
        # Before the form's own handlers of the kind: they see the new
        # kind's fields.
        kind.currentIndexChanged.connect(self._swapped)

    def _swapped(self, _index: int = -1) -> None:
        new = self._kind.currentData()
        old = self._shown
        if new == old:
            return
        self._by_kind[old] = {k: f.text() for k, f in self._fields.items()}
        own = self._by_kind.get(new, {})
        self._shown = new
        if self._signin is None:
            self._fill(own)
            return
        with self._signin.filling():
            self._fill(own)

    def _fill(self, own: dict[str, str]) -> None:
        for k, f in self._fields.items():
            f.setText(own.get(k, ""))


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


#: A SAT>IP source's own options (providers/satip.py), which its fields
#: hold for everyone: its channel lists, how its streams travel, the HTTP
#: port, how many of its tuners the app may use, where its description was
#: found.
SATIP_KEYS = ("m3u", "transport", "http_port", "tuners", "description")
SATIP_HTTP_PORT = 8875


class SourceFields:
    """A source's connection fields and what they mean - name, kind,
    address, login, MAC, guide address, refresh, a media server's sign-in,
    Find on my network, a SAT>IP server's channel list, transport and
    tuners - without a layout of their own: the Add dialog puts them in a
    form, a source's page in Settings (ui/source_page.py) on its tabs. Mixed
    into the QWidget that holds them (it parents the sign-in and the
    finder); *nerd_panel*, the source's own Nerd options, is the page's
    while Nerd is on."""

    # (value, i18n key) - the label is translated at construction time.
    REFRESH_OPTIONS = [
        ("never", "refresh_never"), ("startup", "refresh_at_startup"),
        ("2h", "refresh_every_2h"), ("6h", "refresh_every_6h"),
        ("12h", "refresh_every_12h"), ("24h", "refresh_daily"),
        ("1w", "refresh_weekly"),
    ]

    nerd_panel: Any = None

    def _build_fields(self, playlist: dict, editing: bool) -> None:
        # The stored source's id: its history and the backup address in use
        # (what a Nerd test asks of it).
        self._pid = str(playlist.get("id") or "") if editing else ""
        self.name = QLineEdit(playlist.get("name", ""))
        self.name.setPlaceholderText(tr("playlist_name_placeholder"))
        self.kind = QComboBox()
        self.kind.addItem(tr("playlist_kind_xtream"), "xtream")
        self.kind.addItem(tr("playlist_kind_m3u"), "m3u")
        if stalker_available():
            self.kind.addItem(tr("playlist_kind_stalker"), "stalker")
        add_box_kinds(self.kind)
        add_media_kinds(self.kind)
        # A box's own options (stream port, profile...) ride along unchanged.
        self._options = dict(playlist.get("options") or {})
        # A media server's sign-in: a token, never kept for another kind.
        self._had_token = bool(playlist.get("token"))
        self._accept_signed = False
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
        # A box on the home network can be found instead of typed.
        self.finder = FindOnNetwork(self)
        self.finder.picked.connect(self._box_found)
        # A media server is signed in to here (ui/media_signin.py).
        self.signin = MediaSignIn(self, self.server, self.user, self.pw,
                                  playlist if editing else None)
        self.server.textChanged.connect(self.signin.form_changed)
        self.user.textChanged.connect(self.signin.form_changed)
        self._build_satip(self._options)
        self._kind_own = KindOwnFields(
            self.kind, {"server": self.server, "user": self.user, "pw": self.pw,
                        "mac": self.mac}, self.signin)

    def _build_satip(self, options: dict) -> None:
        """A SAT>IP server's fields: its channel lists (M3U, a file or an
        address), how its streams travel, the HTTP port, its tuners."""
        self.satip_m3u = QLineEdit(str(options.get("m3u") or ""))
        self.satip_m3u.setPlaceholderText(tr("satip_m3u_placeholder"))
        self.satip_transport = QComboBox()
        for value, key in (("tcp", "satip_tr_tcp"), ("udp", "satip_tr_udp"), ("http", "satip_tr_http")):
            self.satip_transport.addItem(tr(key), value)
        idx = self.satip_transport.findData(str(options.get("transport") or "tcp").lower())
        self.satip_transport.setCurrentIndex(max(0, idx))
        self.satip_http_port = QSpinBox()
        self.satip_http_port.setRange(1, 65535)
        self.satip_tuners = QSpinBox()
        self.satip_tuners.setRange(0, 64)
        self.satip_tuners.setSpecialValueText(tr("satip_tuners_auto"))
        for box, key, default in ((self.satip_http_port, "http_port", SATIP_HTTP_PORT),
                                  (self.satip_tuners, "tuners", 0)):
            try:
                box.setValue(int(options.get(key) or default))
            except (TypeError, ValueError):
                box.setValue(default)
        # Where Find on my network found the server's description.
        self._satip_desc = str(options.get("description") or "")

    def _satip_options(self, options: dict) -> dict:
        """*options* with a SAT>IP source's own as its fields say (each at
        its default left out); a description found for another address is
        not kept."""
        out = {k: v for k, v in options.items() if k not in SATIP_KEYS}
        lists = self.satip_m3u.text().strip()
        if lists:
            out["m3u"] = lists
        transport = self.satip_transport.currentData()
        if transport != "tcp":
            out["transport"] = transport
        if transport == "http" and self.satip_http_port.value() != SATIP_HTTP_PORT:
            out["http_port"] = str(self.satip_http_port.value())
        if self.satip_tuners.value():
            out["tuners"] = str(self.satip_tuners.value())
        desc = self._satip_desc
        if desc:
            from urllib.parse import urlsplit
            typed = self.server.text().strip()
            try:
                there = (urlsplit(desc).hostname or "").lower()
                here = (urlsplit(typed if "://" in typed else "http://" + typed).hostname or "").lower()
            except ValueError:
                there, here = "", "-"
            if there and there == here:
                out["description"] = desc
        return out

    def _kind_fields(self) -> dict[str, Any]:
        """What the kind shows: the address's label, which rows (address,
        login, MAC, Find on my network) - and tells the sign-in, the finder
        and the Nerd options which kind it is."""
        kind = self.kind.currentData()
        m3u, stalker = kind == "m3u", kind == "stalker"
        box = kind in BOX_KINDS
        media = kind in MEDIA_KINDS
        # M3U needs only a URL, a portal its URL and a MAC address - hide
        # the username/password rows and relabel the address field. A box
        # on the home network takes its address and, if it asks, a login;
        # a media server is signed in to (Plex: in the browser, its address
        # then comes from the account).
        self.server.setPlaceholderText(
            "https://example.com/playlist.m3u" if m3u
            else "http://portal.example.com/c/" if stalker
            else PLACEHOLDERS.get(kind, "http://server:port"))
        hint = tr("playlist_login_optional") if box else ""
        pin = kind in PIN_KINDS
        self.user.setPlaceholderText(hint)
        self.pw.setPlaceholderText(tr("playlist_pin_hint") if pin else hint)
        self.finder.wanted = MEDIA_KINDS if media else BOX_KINDS
        if not (box or media):
            self.finder.status.hide()
        self.signin.set_kind(kind)
        if self.nerd_panel is not None:
            self.nerd_panel.set_kind(kind)
        return {
            "server_label": (tr("playlist_m3u_url") if m3u
                             else tr("playlist_portal_url") if stalker
                             else tr("playlist_box_address") if box or media
                             else tr("login_server")),
            "server": kind != "plex",
            "login": (not (m3u or stalker or box or media) or kind in LOGIN_KINDS
                      or kind in ("jellyfin", "emby")),
            # A PIN alone (NextPVR): the password field, named so.
            "user": not pin,
            "pw_label": tr("login_pin") if pin else tr("login_password"),
            "mac": stalker,
            "finder": box or media,
            "satip": kind == "satip",
        }

    def _source_now(self) -> dict:
        """The source as the form has it now, without its Nerd options (what
        the Nerd tests ask)."""
        out = {"kind": self.kind.currentData(), "server": self.server.text().strip(),
               "username": self._username(), "password": self.pw.text().strip(),
               "mac": self.normal_mac(self.mac.text()) or "",
               "name": self.name.text().strip()}
        if out["kind"] in MEDIA_KINDS and self.signin.signed_in():
            # Its ids beside the options, which the panel brings its own of.
            got = self.signin.values()
            out.update(server=got["server"], username=got["username"], password="",
                       token=got["token"], **got["options"])
        if self._pid:
            out["id"] = self._pid
        return out

    def _username(self) -> str:
        """The user name, none for a kind that logs in with a PIN alone."""
        return "" if self.kind.currentData() in PIN_KINDS else self.user.text().strip()

    def _preset_picked(self, src: dict) -> None:
        idx = self.kind.findData(src["kind"])
        if idx >= 0:
            self.kind.setCurrentIndex(idx)
        self.server.setText(src["server"])
        if src.get("epg_url"):
            self.epg_url.setText(src["epg_url"])
        if src.get("m3u"):
            self.satip_m3u.setText(src["m3u"])
        if not self.name.text().strip():
            self.name.setText(src["name"])

    def _box_found(self, found: dict) -> None:
        """A box picked from what answered on the network fills the form."""
        idx = self.kind.findData(found.get("kind"))
        if idx >= 0:
            self.kind.setCurrentIndex(idx)
        self.server.setText(found.get("server", ""))
        if found.get("kind") == "satip":
            self._satip_desc = str(found.get("description") or "")
        if not self.name.text().strip():
            self.name.setText(str(found.get("name", "")).split(" - ")[0])
        if found.get("login") or found.get("kind") in ("jellyfin", "emby"):
            (self.pw if found.get("kind") in PIN_KINDS else self.user).setFocus()
        if found.get("kind") in MEDIA_KINDS:
            self.signin.prefer(found)

    def _maybe_autodetect_link(self) -> None:
        """Recognise a pasted provider link and configure the form: Xtream
        (split into server/username/password, preferred) or M3U (a plain
        playlist URL). A bare host is left alone so manual entry keeps working."""
        from ..providers.client import detect_provider_link
        detected = detect_provider_link(self.server.text())
        if not detected:
            return
        kind, server, user, pw = detected
        idx = self.kind.findData(kind)
        if idx >= 0:
            self.kind.setCurrentIndex(idx)   # fires the kind's update
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

    def problem(self) -> tuple[str, str, str] | None:
        """Why the form cannot be stored as it is - (title, what to say,
        which part: "rewrite", "mirrors" or "") - or None when it can. A
        media server not signed in to cannot."""
        # Nerd: a rewrite rule that cannot work is refused here, not
        # stored to be skipped at every channel ...
        p = self.nerd_panel
        bad = p.rules_ok() if p is not None else None
        if bad is not None:
            return tr("nerd_ck_title_rewrite"), tr(bad.key, **bad.params), "rewrite"
        # ... and so is a backup address that is no address.
        bad = p.mirrors_ok() if p is not None else None
        if bad is not None:
            return tr("nerd_ck_title_mirrors"), tr(bad.key, **bad.params), "mirrors"
        kind = self.kind.currentData()
        server = self.server.text().strip()
        if kind == "stalker":
            if server and self.normal_mac(self.mac.text()):
                return None
            return tr("playlist_msg_title"), tr("playlist_portal_required"), ""
        if kind in BOX_KINDS:
            return None if server else (tr("playlist_msg_title"), tr("playlist_box_required"), "")
        if kind in MEDIA_KINDS:
            return None if self.signin.signed_in() else (
                tr("playlist_msg_title"), tr("ms_sign_in_needed"), "")
        if kind == "m3u":
            ok = bool(server)
        else:
            ok = bool(server and self.user.text().strip() and self.pw.text().strip())
        return None if ok else (tr("playlist_msg_title"), tr("playlist_required_fields"), "")

    def values(self) -> dict:
        name = self.name.text().strip()
        if not name:
            name = self.server.text().strip().split("//")[-1].split("/")[0]
        out = {
            "name": name,
            "kind": self.kind.currentData(),
            "server": self.server.text().strip(),
            "username": self._username(),
            "password": self.pw.text().strip(),
            "epg_url": self.epg_url.text().strip(),
            "refresh": self.refresh.currentData(),
            "mac": self.normal_mac(self.mac.text()) or "",
        }
        options = (self.nerd_panel.values() if self.nerd_panel is not None
                   else dict(self._options))
        if out["kind"] == "satip":
            options = self._satip_options(options)
        if out["kind"] in MEDIA_KINDS:
            # What the sign-in left: the token and the ids, never a password.
            got = self.signin.values()
            out.update(server=got["server"], username=got["username"], password="",
                       token=got["token"])
            for key in ("user_id", "server_id", "connections"):
                options.pop(key, None)
            options.update(got["options"])
            if not self.name.text().strip():
                out["name"] = got["name"] or got["server"].split("//")[-1].split("/")[0]
        elif self._had_token:
            out["token"] = ""
            for key in ("user_id", "server_id", "connections"):
                options.pop(key, None)
        if options:
            out["options"] = options
        return out


class PlaylistDialog(SourceFields, QDialog):
    """Add a source: the quick form - its kind, address and login, the
    presets (Nerd), Find on my network, a media server's sign-in. Its own
    options are on its page in Settings, which opens once it is added."""

    def __init__(self, parent=None, playlist=None,
                 prefill: dict | None = None) -> None:
        """*playlist* edits a stored playlist; *prefill* starts a new one
        with fields already filled in (a playlist file dropped on the
        window)."""
        super().__init__(parent)
        editing = bool(playlist)
        playlist = playlist or prefill or {}
        self._build_fields(playlist, editing)
        self.setWindowTitle(tr("playlist_edit_title") if editing
                            else tr("playlist_add_title"))
        self.setMinimumWidth(520)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 22, 22, 22)
        # The form scrolls, the buttons stay: a media server's sign-in can
        # make it taller than a small screen.
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        self._scroll = FitScroll(body, self)
        outer.addWidget(self._scroll, 1)
        form = QFormLayout()
        form.setSpacing(10)
        # Explicit labels so the login rows can be hidden for M3U playlists.
        self._server_lbl = QLabel()
        self._user_lbl = QLabel(tr("login_username"))
        self._pw_lbl = QLabel(tr("login_password"))
        self._mac_lbl = QLabel(tr("playlist_mac"))
        self._satip_lbl = QLabel(tr("satip_m3u"))
        form.addRow(tr("playlist_name"), self.name)
        form.addRow(tr("playlist_kind"), self.kind)
        # Nerd: devices and servers with known playlist/guide addresses.
        from ..core import nerd
        if nerd.current().on:
            from .source_presets import PresetButton
            self.presets = PresetButton(self._preset_picked, self)
            form.addRow("", self.presets)
        form.addRow(self._server_lbl, self.server)
        form.addRow("", self.finder.row)
        form.addRow(self._user_lbl, self.user)
        form.addRow(self._pw_lbl, self.pw)
        self.signin.changed.connect(self._fit)
        self.signin.finished.connect(self._signin_finished)
        form.addRow("", self.signin)
        form.addRow(self._mac_lbl, self.mac)
        form.addRow(self._satip_lbl, self.satip_m3u)
        form.addRow(tr("playlist_custom_epg_url"), self.epg_url)
        form.addRow(tr("playlist_auto_refresh"), self.refresh)
        lay.addLayout(form)
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
        """Grow or shrink with the form (rows shown for the kind, the
        sign-in's) - after the layouts have taken the change in."""
        def fit() -> None:
            self._scroll.widget().adjustSize()
            self._scroll.updateGeometry()
            self.adjustSize()
        QTimer.singleShot(0, fit)

    def _update_kind(self) -> None:
        shown = self._kind_fields()
        self._server_lbl.setText(shown["server_label"])
        for w in (self._server_lbl, self.server):
            w.setVisible(shown["server"])
        for w in (self._user_lbl, self.user):
            w.setVisible(shown["login"] and shown["user"])
        for w in (self._pw_lbl, self.pw):
            w.setVisible(shown["login"])
        self._pw_lbl.setText(shown["pw_label"])
        for w in (self._mac_lbl, self.mac):
            w.setVisible(shown["mac"])
        for w in (self._satip_lbl, self.satip_m3u):
            w.setVisible(shown["satip"])
        self.finder.row.setVisible(shown["finder"])
        self._fit()

    def _signin_finished(self, ok: bool) -> None:
        """A sign-in OK asked for has ended: the source is added if it worked."""
        if self._accept_signed and ok and self.signin.signed_in():
            self._accept_signed = False
            self.accept()
            return
        self._accept_signed = False
        self._fit()

    def _validate(self) -> None:
        if self.kind.currentData() in MEDIA_KINDS and not self.signin.signed_in():
            if self.signin.busy:
                return
            # Name and password typed, Sign in not pressed: OK does it.
            if self.signin.sign_in():
                self._accept_signed = True
                return
        bad = self.problem()
        if bad is not None:
            QMessageBox.warning(self, bad[0], bad[1])
            return
        self.accept()


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
            # What the key opens, too: someone who picked a supporter theme
            # lands here and should read why.
            self.sub.setText(tr("supporter_intro") + "\n\n" + tr("supporter_perks"))
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
        if self.isVisible():
            self._fit()

    def _fit(self) -> None:
        """As tall as its words at its width: the intro and what the key
        opens wrap beside the heart, and a plain size hint does not count
        the wrapping - they ran past the top and bottom of the row."""
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
    this computer only), each answer with its day and their replies."""
    import datetime as _dt
    import html as _html

    from ..core import report

    def line(when, text: str, colour: str = "") -> str:
        words = f"<span style='color:{colour}'>{_html.escape(text)}</span>" if colour else _html.escape(text)
        return (f"<p style='margin:4px 0 0'><span style='color:{P['muted']}'>"
                f"{_dt.date.fromtimestamp(int(when or 0)).isoformat()}</span>&nbsp; {words}</p>")
    rows = []
    ordered = sorted(tickets, key=lambda t: -max((a.get("t", 0) for a in t.get("r") or [{}]), default=0))
    for n, t in enumerate(ordered):
        # Their own replies in their own colour, the answers as they were.
        said = "".join(line(e.get("t"), answer_text(e)) if kind == "answer"
                       else line(e.get("t"), tr("fbr_you_replied", text=e["w"]), P["text2"])
                       for kind, e in report.conversation(t))
        rows.append(f"<p style='margin:{12 if n else 0}px 0 0 0; color:{P['text2']}'>"
                    f"{_html.escape(tr('fbr_you', text=str(t.get('w') or '…')))}</p>{said}")
    return "".join(rows)


def _count(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


class AnswersDialog(QDialog):
    """Answers to feedback, in a window of their own: a card for each report
    that asked for one, newest first - the day it went, its first words
    (kept on this computer only), each answer with its day and the person's
    replies between them, the new ones marked and the waiting ones said -
    with Check now, and how it stays anonymous. Under an answer that lets
    them reply, a box and Send reply (*reply(code, text)*); what is typed
    there stays while the cards are drawn again. With no report yet, a way
    to send one."""

    #: The first words are kept to this many characters (mw_report).
    WORDS = 90
    #: The cards' room before they scroll.
    TALLEST = 460

    def __init__(self, parent, tickets: list, check=None, send=None, reply=None) -> None:
        super().__init__(parent)
        self._check, self._send, self._reply = check, send, reply
        #: (code, answer's index) new while this window has been open.
        self._new: set[tuple[str, int]] = set()
        #: What is typed in each card's reply box, and the replies on their way.
        self._drafts: dict[str, str] = {}
        self._sending: set[str] = set()
        self.cards: list[QFrame] = []
        self.reply_boxes: dict[str, Any] = {}
        self.reply_buttons: dict[str, QPushButton] = {}
        self.reply_notes: dict[str, QLabel] = {}
        self.send_btn: QPushButton | None = None
        self.setWindowTitle(tr("fbr_title"))
        self.setMinimumWidth(540)
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 20, 24, 18)
        col.setSpacing(10)
        head = QLabel(tr("fbr_title"))
        head.setStyleSheet(f"font-size:17px; font-weight:700; color:{P['text']};")
        col.addWidget(head)
        self.list = QWidget(objectName="AnswersList")
        self.rows = QVBoxLayout(self.list)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(10)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.list)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea, QWidget#AnswersList { background: transparent; }")
        col.addWidget(self.scroll, 1)
        self.note = QLabel(tr("fb_answers_note"))
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        col.addWidget(self.note)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        buttons.addWidget(self.status, 1)
        self.check_btn = QPushButton(tr("fba_check"))
        self.check_btn.clicked.connect(self._check_clicked)
        self.close_btn = QPushButton(tr("common_close"), objectName="Primary")
        self.close_btn.clicked.connect(self.accept)
        buttons.addWidget(self.check_btn)
        buttons.addWidget(self.close_btn)
        col.addLayout(buttons)
        self.set_tickets(tickets)

    # -- what it shows ------------------------------------------------------------

    def set_tickets(self, tickets: list) -> None:
        """The reports as they are kept now; an answer not shown before
        (past its report's "seen") stays marked new while this is open."""
        import time

        from ..core import report
        for t in tickets:
            for i in range(_count(t.get("seen")), len(t.get("r") or [])):
                self._new.add((str(t.get("c")), i))
        # Typing a reply: the same box has the cursor where it was once the
        # cards are drawn again.
        typing = next(((c, b.textCursor().position()) for c, b in self.reply_boxes.items() if b.hasFocus()), None)
        while self.rows.count():
            item = self.rows.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.hide()
                w.deleteLater()
        self.cards = []
        self.reply_boxes, self.reply_buttons, self.reply_notes = {}, {}, {}
        self.send_btn = None
        ordered = sorted(tickets, key=lambda t: -float(t.get("t") or 0))
        for t in ordered:
            card = self._card(t)
            self.cards.append(card)
            self.rows.addWidget(card)
        if not ordered:
            self.rows.addWidget(self._empty())
        self.rows.addStretch(1)
        if typing is not None and typing[0] in self.reply_boxes:
            box = self.reply_boxes[typing[0]]
            box.setFocus()
            cursor = box.textCursor()
            cursor.setPosition(min(typing[1], len(box.toPlainText())))
            box.setTextCursor(cursor)
        asked = bool(report.open_codes(ordered, time.time()))
        self.check_btn.setVisible(asked)
        if not asked:
            self.status.clear()
        # One button stands out: Send feedback… while there is nothing yet.
        primary = "Primary" if ordered else ""
        if self.close_btn.objectName() != primary:
            self.close_btn.setObjectName(primary)
            self.close_btn.style().unpolish(self.close_btn)
            self.close_btn.style().polish(self.close_btn)
        if self.isVisible():
            self._fit()

    def _card(self, t: dict) -> QFrame:
        import html as _html

        from ..core import report
        from .qt_i18n import format_when
        card = QFrame(objectName="AnswerCard")
        card.setStyleSheet(f"QFrame#AnswerCard {{ background:{P['input']}; border-radius:12px; }}"
                           " QFrame#AnswerCard QLabel { background: transparent; }")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(4)
        code, answers = str(t.get("c")), [a for a in t.get("r") or [] if isinstance(a, dict)]
        top = QHBoxLayout()
        top.setSpacing(8)
        sent = QLabel(tr("fba_sent", date=format_when(float(t.get("t") or 0), "d MMM yyyy")))
        sent.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        top.addWidget(sent, 1)
        card.new = sum((code, i) in self._new for i in range(len(answers)))
        if card.new:
            pill = QLabel(tr("fba_new"))
            pill.setStyleSheet(f"QLabel {{ background:{P['accent']}; color:{P['on_accent']};"
                               f" border-radius:8px; padding:1px 8px; font-size:11px; font-weight:700; }}")
            top.addWidget(pill, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(top)
        words = " ".join(str(t.get("w") or "").split())
        if len(words) >= self.WORDS:
            words = words.rstrip(" .,;:-") + "…"
        you = QLabel(tr("fba_you", text=words or "…"))
        you.setTextFormat(Qt.TextFormat.PlainText)       # "<private>" is words, not a tag
        you.setWordWrap(True)
        you.setStyleSheet(f"font-size:13px; color:{P['text2']};")
        lay.addWidget(you)
        if not answers:
            wait = QLabel(tr("fba_waiting"))
            wait.setStyleSheet(f"font-size:13px; color:{P['muted']}; font-style:italic;")
            lay.addSpacing(2)
            lay.addWidget(wait)

        def words(said: str, colour: str) -> None:
            text = QLabel(said)
            text.setTextFormat(Qt.TextFormat.PlainText)
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            text.setStyleSheet(f"font-size:13px; color:{colour};")
            lay.addWidget(text)
        i = -1
        for kind, a in report.conversation(t):
            lay.addSpacing(8)
            if kind == "reply":
                # Their own reply, as they sent it (kept on this computer only).
                when = QLabel(tr("fba_you_replied", date=format_when(float(a["t"]), "d MMM yyyy")))
                when.setStyleSheet(f"font-size:12px; font-weight:600; color:{P['muted']};")
                lay.addWidget(when)
                words(a["w"], P["text2"])
                continue
            i += 1
            fresh = (code, i) in self._new
            dot = f"<span style='color:{P['accent']}'>●</span>&nbsp; " if fresh else ""
            when = QLabel(dot + _html.escape(tr("fba_answer", date=format_when(float(a.get("t") or 0),
                                                                              "d MMM yyyy"))))
            when.setTextFormat(Qt.TextFormat.RichText)
            when.setStyleSheet(f"font-size:12px; font-weight:600;"
                               f" color:{P['text'] if fresh else P['muted']};")
            lay.addWidget(when)
            said = answer_text(a)
            if said:
                words(said, P["text"])
        if self._reply is not None and report.can_reply(t):
            self._reply_box(lay, code)
        return card

    def _reply_box(self, lay: QVBoxLayout, code: str) -> None:
        """Under the answer that lets them reply: a word on what it is, the
        box (as long as the site takes), Send reply and how it went."""
        from functools import partial

        from PyQt6.QtWidgets import QPlainTextEdit
        lay.addSpacing(10)
        hint = QLabel(tr("fba_reply_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        lay.addWidget(hint)
        box = QPlainTextEdit()
        box.setPlaceholderText(tr("fba_reply_placeholder"))
        box.setAccessibleName(tr("fba_reply_placeholder"))
        box.setTabChangesFocus(True)
        box.setFixedHeight(84)
        box.setStyleSheet(f"QPlainTextEdit {{ background: {P['bg']}; color: {P['text']}; border: none;"
                          f" border-radius: 8px; padding: 6px 8px; selection-background-color: {P['sel']}; }}")
        box.setPlainText(self._drafts.get(code, ""))
        sending = code in self._sending
        box.setReadOnly(sending)
        lay.addWidget(box)
        row = QHBoxLayout()
        row.setSpacing(8)
        note = QLabel(tr("fba_reply_sending") if sending else "")
        note.setWordWrap(True)
        note.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        row.addWidget(note, 1)
        send = QPushButton(tr("fba_reply_send"))
        send.setEnabled(not sending and bool(box.toPlainText().strip()))
        send.clicked.connect(partial(self._reply_clicked, code))
        row.addWidget(send, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(row)
        self.reply_boxes[code], self.reply_buttons[code], self.reply_notes[code] = box, send, note
        box.textChanged.connect(partial(self._draft_changed, code))

    def _draft_changed(self, code: str) -> None:
        """Kept as typed (a card drawn again has it back), never longer than
        the site takes; Send reply only with something in it."""
        from ..core import report
        box, send = self.reply_boxes.get(code), self.reply_buttons.get(code)
        if box is None or send is None:
            return
        text = box.toPlainText()
        if len(text) > report.REPLY_MAX:
            at = min(box.textCursor().position(), report.REPLY_MAX)
            text = text[:report.REPLY_MAX]
            box.blockSignals(True)
            box.setPlainText(text)
            box.blockSignals(False)
            cursor = box.textCursor()
            cursor.setPosition(at)
            box.setTextCursor(cursor)
        self._drafts[code] = text
        send.setEnabled(code not in self._sending and bool(text.strip()))

    def _reply_clicked(self, code: str, *_a) -> None:
        text = self._drafts.get(code, "").strip()
        if text and self._reply is not None and code not in self._sending:
            self._reply(code, text)

    # -- a reply on its way -----------------------------------------------------

    def reply_sending(self, code: str) -> None:
        self._sending.add(code)
        box, send, note = (self.reply_boxes.get(code), self.reply_buttons.get(code),
                           self.reply_notes.get(code))
        if box is not None:
            box.setReadOnly(True)
        if send is not None:
            send.setEnabled(False)
        if note is not None:
            note.setStyleSheet(f"font-size:12px; color:{P['muted']};")
            note.setText(tr("fba_reply_sending"))

    def reply_sent(self, code: str, tickets: list) -> None:
        """It arrived: the box goes (until a newer answer lets them reply),
        their reply shows in its place."""
        self._sending.discard(code)
        self._drafts.pop(code, None)
        self.set_tickets(tickets)

    def reply_failed(self, code: str, why: str, tickets: list | None = None) -> None:
        """Not sent: *why* "wait" or "day" (feedback's own limits), "closed"
        (the site is not waiting for a reply to that answer - the box goes,
        what was typed is kept should a newer answer let them), else it did
        not reach the site; the words stay in the box."""
        self._sending.discard(code)
        if why == "closed":
            if tickets is not None:
                self.set_tickets(tickets)
            self.status.setStyleSheet(f"font-size:12px; color:{P['error']};")
            self.status.setText(tr("fba_reply_closed"))
            return
        box, send, note = (self.reply_boxes.get(code), self.reply_buttons.get(code),
                           self.reply_notes.get(code))
        if box is not None:
            box.setReadOnly(False)
        if send is not None:
            send.setEnabled(bool(self._drafts.get(code, "").strip()))
        if note is not None:
            note.setStyleSheet(f"font-size:12px; color:{P['error']};")
            note.setText(tr({"wait": "fb_wait", "day": "fb_enough"}.get(why, "fba_reply_failed")))

    def _empty(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 28, 12, 20)
        lay.setSpacing(8)
        title = QLabel(tr("fba_empty_title"))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"font-size:15px; font-weight:700; color:{P['text']};")
        text = QLabel(tr("fba_empty", option=tr("fb_answers_opt")))
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text.setStyleSheet(f"font-size:13px; color:{P['text2']};")
        self.send_btn = QPushButton(tr("feedback_menu") + "…", objectName="Primary")
        self.send_btn.clicked.connect(self._send_clicked)
        lay.addWidget(title)
        lay.addWidget(text)
        lay.addSpacing(6)
        lay.addWidget(self.send_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        return box

    # -- asking -------------------------------------------------------------------

    def show_checking(self) -> None:
        self.check_btn.setEnabled(False)
        self.status.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        self.status.setText(tr("fba_checking"))

    def show_result(self, ok: bool, fresh: int, when: float) -> None:
        from .qt_i18n import format_when
        self.check_btn.setEnabled(True)
        if not ok:
            self.status.setStyleSheet(f"font-size:12px; color:{P['error']};")
            self.status.setText(tr("fba_failed"))
            return
        self.status.setStyleSheet(f"font-size:12px; color:{P['muted']};")
        self.status.setText(tr("fba_checked_new" if fresh else "fba_checked_none",
                               time=format_when(when, "HH:mm")))

    def _check_clicked(self, *_a) -> None:
        if self._check is not None:
            self._check()

    def _send_clicked(self, *_a) -> None:
        if self._send is not None:
            self._send()
        else:
            self.accept()

    # -- size ---------------------------------------------------------------------

    def _fit(self) -> None:
        """As tall as the cards at this width, up to a screenful's share -
        then they scroll."""
        width = max(self.width(), self.minimumWidth())
        self.rows.activate()

        def tall(inner: int) -> int:
            h = self.list.heightForWidth(inner) if self.rows.hasHeightForWidth() else -1
            return h if h > 0 else self.list.sizeHint().height()
        h = tall(width - 48)
        if h + 2 <= self.TALLEST:
            # Exactly as tall as the cards: the scroll area's own size hint
            # (its content at no particular width) is taller and left a gap.
            self.scroll.setFixedHeight(h + 2)
        else:
            # It scrolls, and grows with the window.
            self.scroll.setMinimumHeight(self.TALLEST)
            self.scroll.setMaximumHeight(16_777_215)
        lay = self.layout()
        if lay is not None:
            lay.activate()
            self.resize(width, lay.totalHeightForWidth(width) if lay.hasHeightForWidth()
                        else lay.totalSizeHint().height())

    def showEvent(self, event) -> None:  # noqa: N802 - Qt hook
        super().showEvent(event)
        self._fit()


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

    def __init__(self, parent, details, has_log_file: bool = False, answers: list | None = None,
                 all_answers=None, copy_only: bool = False) -> None:
        super().__init__(parent)
        self._all_answers = all_answers
        self.copy_only = copy_only
        from PyQt6.QtGui import QFont
        from PyQt6.QtWidgets import QButtonGroup, QCheckBox, QComboBox, QGridLayout, QPlainTextEdit, QScrollArea

        from .settings_ui import Chip, _Flow
        self._details = details
        self._edited = False
        self.setWindowTitle(tr("feedback_title"))
        self.setMinimumWidth(600)
        # Everything but Cancel and Send scrolls: the answers, the questions
        # and the preview together are taller than a laptop's screen, and a
        # window as tall as all of it went off the screen with Send under it.
        self._body = QWidget()
        self._body.setObjectName("FeedbackBody")
        col = QVBoxLayout(self._body)
        col.setContentsMargins(24, 20, 24, 8)
        col.setSpacing(8)

        def label(text: str, size: int = 12, colour: str = "", bold: bool = True) -> QLabel:
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"font-size:{size}px; font-weight:{700 if bold else 400};"
                              f" color:{colour or P['muted']};")
            return lbl
        col.addWidget(label(tr("feedback_title"), 17, P["text"]))
        col.addWidget(label(tr("feedback_intro"), 13, P["text2"], bold=False))
        # A modified build: nothing goes to dopeiptv.org, the text can be copied.
        self.not_official: QLabel | None = None
        if copy_only:
            self.not_official = label(tr("build_reports_elsewhere"), 13, P["warn"], bold=False)
            col.addWidget(self.not_official)
        # Answers to earlier feedback (asked for with its code), first.
        self.answers_box: QWidget | None = None
        self.all_btn: QPushButton | None = None
        if answers:
            col.addSpacing(4)
            title_row = QHBoxLayout()
            title_row.addWidget(label(tr("fbr_title")), 1)
            if all_answers is not None:
                # Every report and its answers, in their own window.
                self.all_btn = QPushButton(tr("fba_all") + "…")
                self.all_btn.setFlat(True)
                self.all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                self.all_btn.setStyleSheet(f"QPushButton {{ border:none; background:transparent; padding:0;"
                                           f" color:{P['accent']}; font-size:12px; font-weight:600; }}"
                                           f" QPushButton:hover {{ text-decoration:underline; }}")
                self.all_btn.clicked.connect(self._open_all_answers)
                title_row.addWidget(self.all_btn)
            col.addLayout(title_row)
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
        self.answers.setChecked(not copy_only)
        self.answers.setToolTip(tr("fb_answers_note"))
        col.addWidget(self.answers)
        note = label(tr("fb_answers_note") + " " + tr("report_privacy"), 12, bold=False)
        col.addWidget(note)
        self.answers.setVisible(not copy_only)
        note.setVisible(not copy_only)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("FeedbackScroll")
        self._scroll.setWidget(self._body)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self._scroll, 1)
        self._button_bar = QWidget()
        buttons = QHBoxLayout(self._button_bar)
        buttons.setContentsMargins(24, 10, 24, 18)
        buttons.addStretch(1)
        cancel = QPushButton(tr("common_cancel"))
        self.send_btn = QPushButton(tr("build_copy_report" if copy_only else "report_send"),
                                    objectName="Primary")
        cancel.clicked.connect(self.reject)
        self.send_btn.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.send_btn)
        outer.addWidget(self._button_bar)
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

    def _open_all_answers(self, *_a) -> None:
        if self._all_answers is not None:
            self._all_answers(self)

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

    #: How short the window may be made: the rest scrolls.
    SHORTEST = 360
    #: Its share of the screen's height at most.
    SCREEN_SHARE = 0.9

    def _fit(self) -> None:
        """As tall as what it holds at its width, up to the screen's room:
        the questions and the preview come and go, and wrapped text (the
        topics, the notes) is taller in some languages - a plain size hint
        leaves it overlapping. Past that room it scrolls, Cancel and Send
        always in sight, and the window can be made shorter still."""
        lay = self._body.layout()
        if lay is None:
            return
        lay.invalidate()
        lay.activate()
        w = max(self.width(), self.minimumWidth())
        h = lay.totalHeightForWidth(w) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        h = max(h, lay.totalSizeHint().height())
        want = h + self._button_bar.sizeHint().height()
        room = self._room()
        cap = int(room.height() * self.SCREEN_SHARE) if room is not None else want
        self.setMinimumHeight(min(want, self.SHORTEST, cap))
        self.resize(w, max(self.minimumHeight(), min(want, cap)))
        if self.isVisible():
            # Once the window manager has the new size and its frame.
            QTimer.singleShot(0, self._keep_on_screen)

    def _room(self):
        """The screen's room for windows (the Dock and menu bar, the panel
        left out), on the screen the window or its parent is on."""
        parent = self.parentWidget()
        screen = self.screen() or (parent.screen() if parent is not None else None) \
            or QApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else None

    def _keep_on_screen(self) -> None:
        """Moved up (or in) so none of it is under the Dock or off the
        screen - its title bar first."""
        room = self._room()
        if room is None or not self.isVisible():
            return
        frame = self.frameGeometry()
        x = min(frame.x(), room.right() - frame.width() + 1)
        y = min(frame.y(), room.bottom() - frame.height() + 1)
        x, y = max(x, room.left()), max(y, room.top())
        if (x, y) != (frame.x(), frame.y()):
            self.move(x, y)

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
        if on:
            # What opened is what they asked to see: scrolled into sight.
            QTimer.singleShot(0, lambda: self._scroll.ensureWidgetVisible(self.preview, 0, 12))

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

    def __init__(self, parent, kind: str, text: str, intro: str = "", copy_only: bool = False) -> None:
        super().__init__(parent)
        from PyQt6.QtGui import QFont
        from PyQt6.QtWidgets import QCheckBox, QPlainTextEdit
        self.kind = kind
        self.copy_only = copy_only
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
        # A modified build: nothing goes to dopeiptv.org, the text can be copied.
        self.not_official: QLabel | None = None
        if copy_only:
            self.not_official = QLabel(tr("build_reports_elsewhere"))
            self.not_official.setWordWrap(True)
            self.not_official.setStyleSheet(f"font-size:13px; color:{P['warn']};")
            col.addWidget(self.not_official)
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
        self.send_btn = QPushButton(tr("build_copy_report" if copy_only else "report_send"),
                                    objectName="Primary")
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
class RestoreBackupDialog(QDialog):
    """What a backup holds, and how to take it: over everything here, or its
    playlists beside the ones here. *counts*: backup.summary(); *missing*:
    how many folder settings name folders this computer does not have;
    *risky*: backup.risky() - with backup.risky_sources(), what runs
    something, lets others in or sends the connections elsewhere, shown
    before anything is taken. Replacing everything is offered only for a
    backup that is backup.replaceable(); the files it does not have are
    named as kept."""

    WIDTH = 560

    _RISKY_LABELS = {"nerd_rec_post": ("nerd_card_rec_after", "nerd_rec_post"),
                     "nerd_mpv_options": ("nerd_mpv_options",),
                     "nerd_mpv_config_dir": ("nerd_mpv_dir",),
                     "nerd_proxy": ("nerd_proxy",),
                     "nerd_headers": ("nerd_headers",),
                     "nerd_udpxy": ("nerd_card_udpxy",),
                     "nerd_hooks_urls": ("nerd_card_hooks", "nerd_hooks_urls"),
                     "nerd_hooks_header": ("nerd_card_hooks", "nerd_hooks_header"),
                     "nerd_mqtt_host": ("nerd_card_mqtt", "nerd_mqtt_host"),
                     "nerd_remote": ("nerd_remote",),
                     "nerd_share": ("nerd_share",),
                     "nerd_mqtt_commands": ("nerd_card_mqtt", "nerd_mqtt_commands"),
                     # A playlist's own options.
                     "proxy": ("nerd_proxy",),
                     "headers": ("nerd_headers",),
                     "rewrite": ("nerd_src_rewrite",),
                     "udpxy": ("nerd_card_udpxy",),
                     "catchup_template": ("nerd_src_catchup",),
                     "backup_servers": ("nerd_src_mirrors",),
                     "nerd_dns_url": ("nerd_card_dns", "nerd_dns_url"),
                     "dns_url": ("nerd_card_dns", "nerd_dns_url"),
                     "cert_pin": ("cert_row",)}

    @classmethod
    def _label(cls, key: str) -> str:
        return " · ".join(tr(k) for k in cls._RISKY_LABELS.get(key, (key,)))

    @classmethod
    def risky_lines(cls, risky: list, sources: list) -> list[str]:
        """The lines that name what *risky* (backup.risky()) and *sources*
        (backup.risky_sources()) set: a setting with its value, a switch
        that lets others in with who, a playlist with its options."""
        lines = []
        for key, value in risky:
            if key in ("nerd_remote", "nerd_share", "nerd_mqtt_commands"):
                lines.append(cls._label(key) + (" · " + tr("nerd_remote_home")
                                                if value else ""))
            else:
                lines.append(tr("backup_count", what=cls._label(key), n=cls._short(value)))
        for name, keys in sources:
            lines.append(tr("restore_risky_source", name=cls._short(name, 40),
                            what=", ".join(cls._label(k) for k in keys)))
        return lines

    def __init__(self, parent, backup, counts: dict, missing: int,
                 risky: list, can_add: bool = True, syncing: bool = False) -> None:
        super().__init__(parent)
        from PyQt6.QtWidgets import QButtonGroup, QRadioButton

        from ..core import backup as bk
        replaceable = bk.replaceable(backup)
        self.setWindowTitle(tr("restore_title"))
        self.setModal(True)
        self.setMinimumWidth(self.WIDTH)
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 22, 24, 20)
        col.setSpacing(10)
        head = QLabel(tr("restore_title"))
        head.setStyleSheet(f"font-size:16px; font-weight:700; color:{P['text']};")
        col.addWidget(head)
        sub = QLabel(tr("restore_made", version=backup.app_version or "?",
                        date=self._when(backup.created)) if backup.full
                     else tr("restore_old_file"))
        sub.setWordWrap(True)
        sub.setTextFormat(Qt.TextFormat.PlainText)   # the file's own words
        sub.setStyleSheet(f"font-size:13px; color:{P['muted']};")
        col.addWidget(sub)
        lines = [tr("backup_count", what=tr(f"backup_kind_{kind}"), n=n)
                 for kind, n in counts.items() if n or kind == "sources"]
        self.holds = QLabel(tr("restore_holds") + "\n" + "\n".join(
            "  •  " + line for line in lines))
        self.holds.setWordWrap(True)
        self.holds.setTextFormat(Qt.TextFormat.PlainText)
        self.holds.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.holds.setStyleSheet(f"font-size:13px; color:{P['text']};")
        col.addWidget(self.holds)
        notes = []
        if backup.full and not replaceable:
            notes.append(tr("restore_cannot_replace"))
        elif backup.full and bk.kept(backup):
            notes.append(tr("restore_kept", what=", ".join(
                tr(f"backup_kind_{kind}") for kind in bk.kept(backup))))
        if backup.full and missing:
            notes.append(tr("restore_missing_paths", n=missing))
        shown = self.risky_lines(risky, bk.risky_sources(backup))
        if shown:
            notes.append(tr("restore_risky") + "\n" + "\n".join(
                "  •  " + line for line in shown))
        if backup.full and bk.new_token_here(backup):
            notes.append(tr("restore_token_note"))
        if replaceable and syncing:
            notes.append(tr("restore_sync_note"))
        self.notes = QLabel("\n\n".join(notes))
        self.notes.setWordWrap(True)
        self.notes.setTextFormat(Qt.TextFormat.PlainText)
        self.notes.setStyleSheet(
            f"font-size:12px; color:{P.get('warn', P['muted'])};")
        self.notes.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        # A backup with many playlists of their own proxy can name dozens:
        # they scroll, the choice below stays on the screen.
        notes_box = QScrollArea()
        notes_box.setWidget(self.notes)
        notes_box.setWidgetResizable(True)
        notes_box.setFrameShape(QScrollArea.Shape.NoFrame)
        notes_box.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget"
                                " { background:transparent; }")
        notes_box.setVisible(bool(notes))
        col.addWidget(notes_box)
        col.addSpacing(4)
        self.replace = QRadioButton(tr("restore_replace"))
        self.add = QRadioButton(tr("restore_add"))
        group = QButtonGroup(self)
        for i, (radio, hint) in enumerate(
                ((self.replace, "restore_replace_hint"),
                 (self.add, "restore_add_hint"))):
            group.addButton(radio, i)
            col.addWidget(radio)
            text = QLabel(tr(hint))
            text.setWordWrap(True)
            text.setContentsMargins(26, 0, 0, 4)
            text.setStyleSheet(f"font-size:12px; color:{P['muted']};")
            col.addWidget(text)
        self.replace.setEnabled(replaceable)
        self.add.setEnabled(can_add)
        (self.replace if replaceable else self.add).setChecked(True)
        col.addSpacing(6)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(tr("common_cancel"))
        self.go = QPushButton(tr("restore_go"), objectName="Primary")
        cancel.clicked.connect(self.reject)
        self.go.clicked.connect(self.accept)
        self.go.setEnabled(self.replace.isEnabled() or self.add.isEnabled())
        buttons.addWidget(cancel)
        buttons.addWidget(self.go)
        col.addLayout(buttons)
        cancel.setFocus()
        # A dialog does not ask its wrapped lines how tall they are at its
        # width: told here, or they were cut.
        inner = self.WIDTH - 48
        for label in self.findChildren(QLabel):
            if label.wordWrap() and label is not self.notes:
                label.setMinimumHeight(label.heightForWidth(inner - label.contentsMargins().left()))
        notes_box.setFixedHeight(min(260, self.notes.heightForWidth(inner - 16) + 4))

    @staticmethod
    def _when(created: str) -> str:
        from datetime import datetime

        from .qt_i18n import format_when
        try:
            return format_when(datetime.fromisoformat(created),
                               "d MMM yyyy HH:mm")
        except (TypeError, ValueError, OSError, OverflowError):
            # OSError, OverflowError: a date the system clock cannot hold
            # (Windows before 1970, year 1 or 9999).
            return created or "?"

    @staticmethod
    def _short(value: str, most: int = 90) -> str:
        value = " ".join(value.split())
        return value if len(value) <= most else value[:most - 1] + "…"

    def choice(self) -> str:
        """"replace" (everything) or "add" (its playlists only)."""
        if self.replace.isChecked() and self.replace.isEnabled():
            return "replace"
        return "add"


def pick_backup(parent) -> tuple[Any, str]:
    """A backup file picked and read (core/backup.py): (it, "") - or
    (None, why) for a file that is not one, (None, "") for none picked.
    Settings' restore and the welcome's both start here."""
    from PyQt6.QtWidgets import QFileDialog

    from ..core import backup
    from ..core.log import log
    from .widgets import file_dialog_options
    path, _f = QFileDialog.getOpenFileName(
        parent, tr("restore_title"), "",
        tr("file_filter_json") + ";;" + tr("file_filter_all"),
        options=file_dialog_options())
    if not path:
        return None, ""
    try:
        return backup.read(path), ""
    except backup.BackupError as exc:
        log.warning("restore: %s", exc)
        return None, (tr("pl_import_unreadable", error=exc.detail)
                      if exc.reason == "unreadable"
                      else tr("restore_newer", version=exc.detail or "?")
                      if exc.reason == "newer" else tr("restore_invalid"))


def stage_backup(b: Any) -> str:
    """*b* left to be put back over everything at the next start
    (core/backup.stage): "" - or why it could not be."""
    from ..core import backup
    from ..core.log import log
    try:
        backup.stage(b)
    except (OSError, ValueError) as exc:
        log.warning("restore: not staged: %s", exc)
        return tr("pl_export_failed", error=exc)
    return ""
