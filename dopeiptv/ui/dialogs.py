"""Dialogs: login, playlist editor, EPG guide, content manager, the update."""

from __future__ import annotations

import re
from typing import Any

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QProgressBar, QPushButton, QScrollArea,
    QSpinBox, QVBoxLayout, QWidget,
)

from ..i18n import tr
from .box_finder import (
    BOX_KINDS, LOGIN_KINDS, MEDIA_KINDS, PIN_KINDS, PLACEHOLDERS, FindOnNetwork, add_box_kinds,
    add_media_kinds,
)
from .media_signin import MediaSignIn
from . import message_box
from .theme import P
# Feedback and the backup's restore have modules of their own; their
# windows stay importable from here.
from .feedback_dialogs import (  # noqa: F401
    PRIVACY_URL, AnswersDialog, FeedbackDialog, ReportDialog, answer_text, answers_html,
    privacy_link,
)
from .backup_dialogs import (  # noqa: F401
    RestoreBackupDialog, UnlockBackupDialog, pick_backup, stage_backup,
)


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

    def forget(self) -> None:
        """What every kind kept goes (the first-run guide, once a source is
        kept: its login is not held for the next one)."""
        self._by_kind.clear()


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
        # A form's fields take the focus, and the area scrolls to each.
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        # Plain http to a provider on the internet: its login travels in
        # every address, readable on the way (docs/SECURITY.md, "A provider
        # on plain http") - said under the address, nothing refused.
        self.http_note = QLabel(tr("playlist_http_note"))
        self.http_note.setWordWrap(True)
        self.http_note.setTextFormat(Qt.TextFormat.PlainText)
        self.http_note.setStyleSheet(f"color:{P.get('warn', P['muted'])}; font-size:12px;")
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

    def plain_http(self) -> bool:
        """Whether the address typed sends a login over plain http to the
        internet: an Xtream server or a portal at http:// - or with no
        scheme, which the Xtream client takes as http - on a host outside
        the home network."""
        if self.kind.currentData() not in ("xtream", "stalker"):
            return False
        text = self.server.text().strip()
        low = text.lower()
        if not text or low.startswith("https://"):
            return False
        if "://" in low and not low.startswith("http://"):
            return False
        from ..core import stream_address
        return stream_address.on_internet(text)

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
            # The server's host, or a playlist file's name - never a login
            # its address carries (core/names.py).
            from ..core.names import source_name
            name = source_name(self.server.text())
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
        # A source's page: whether it plays without its certificate checked
        # (core/nerd.TLS_UNCHECKED) - the quick form has no such switch.
        tls = getattr(self, "tls_box", None)
        if tls is not None:
            options.pop("tls_unchecked", None)
            if tls.isChecked():
                options["tls_unchecked"] = "true"
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
                from ..core.names import source_name
                out["name"] = got["name"] or source_name(got["server"])
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
        form.addRow("", self.http_note)
        self.server.textChanged.connect(lambda _t: self.http_note.setVisible(self.plain_http()))
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
        self.http_note.setVisible(shown["server"] and self.plain_http())
        for w in (self._user_lbl, self.user):
            w.setVisible(shown["login"] and shown["user"])
        for w in (self._pw_lbl, self.pw):
            w.setVisible(shown["login"])
        self._pw_lbl.setText(shown["pw_label"])
        # Each field read by its label (they are QLabels of their own here,
        # so Qt does not tie them as it does a form row's words).
        for lbl, field in ((self._server_lbl, self.server), (self._user_lbl, self.user),
                           (self._pw_lbl, self.pw), (self._mac_lbl, self.mac),
                           (self._satip_lbl, self.satip_m3u)):
            field.setAccessibleName(lbl.text())
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
            message_box.warning(self, bad[0], bad[1])     # a typed or restored line: words
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
        self.list.setAccessibleName(tr("cm_title"))
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
        name, ok = message_box.get_text(
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
        # What was tried may hold what a server answered: words, never markup.
        for lbl in (head, body):
            lbl.setTextFormat(Qt.TextFormat.PlainText)
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
        head.setTextFormat(Qt.TextFormat.PlainText)     # the channel's name
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
            val.setTextFormat(Qt.TextFormat.PlainText)  # the last error, as a server put it
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
        head.setTextFormat(Qt.TextFormat.PlainText)
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
        head.setTextFormat(Qt.TextFormat.PlainText)
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
