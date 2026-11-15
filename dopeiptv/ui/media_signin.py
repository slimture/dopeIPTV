"""Signing in to a media server in the add-a-source forms (the source dialog
and the welcome's connect step): Jellyfin and Emby with the user's name and
password (sent once, never kept: the form keeps the token and the user's id)
or Jellyfin's Quick Connect; Plex with its PIN in the browser, then the
person's server picked from their account. The network is asked off the
window's thread (providers/jellyfin.py, providers/plex.py); what comes back
lands only while the form is still there."""

from __future__ import annotations

from typing import Any, Callable

from PyQt6 import sip
from PyQt6.QtCore import QThreadPool, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from ..core.log import log
from ..core.workers import run_async
from ..i18n import LocalizedError, tr
from .theme import P

MEDIA_KINDS = ("jellyfin", "emby", "plex")
NAMES = {"jellyfin": "Jellyfin", "emby": "Emby", "plex": "Plex"}
QC_EVERY_MS = 3000
QC_TRIES = 100            # five minutes
PIN_EVERY_MS = 2000
PIN_TRIES = 450           # fifteen minutes
#: A wait's polls that may fail in a row (the network, a server's 5xx)
#: before the sign-in gives up; each waits twice as long as the one before.
POLL_HICCUPS = 4
#: What a wait's poll may say that is the end of it: the code is gone.
_FINAL = frozenset(("err_ms_qc_expired", "err_ms_pin_expired"))


def _tagged(gen: int, fn: Callable[..., Any], *args: Any) -> tuple[int, Any, str]:
    """(gen, what *fn* gave, "") or (gen, None, why it failed) - a failure
    comes back the same way as an answer, with the ask it belongs to."""
    try:
        return gen, fn(*args), ""
    except LocalizedError as e:
        return gen, None, e.localized()
    except Exception as e:
        log.info("media sign-in: %s", type(e).__name__)
        return gen, None, tr("err_unexpected_response")


class Hiccup:
    """A wait's poll that did not get through (the network, the server):
    one more try, not the end of the sign-in. *why*, in the user's language,
    is said if it was the last."""

    def __init__(self, why: str) -> None:
        self.why = why


def _patient(fn: Callable[..., Any], *args: Any) -> Any:
    """(off the window's thread) *fn*'s answer - or a Hiccup when it failed
    for a reason another try may not meet; the code being gone (_FINAL)
    ends the wait as it did."""
    try:
        return fn(*args)
    except LocalizedError as e:
        if e.key in _FINAL:
            raise
        return Hiccup(e.localized())
    except Exception as e:
        log.info("media sign-in: a poll failed - %s", type(e).__name__)
        return Hiccup(tr("err_unexpected_response"))


def _server_of(text: str, kind: str) -> str:
    from ..providers import jellyfin
    from ..providers.boxes import normalize_server
    if kind == "plex":
        return normalize_server(text, 32400)
    return jellyfin.server_url(text, kind)


class MediaSignIn(QWidget):
    """The sign-in row of a media server's form. *server*, *user* and *pw*
    are the form's own fields; *playlist* a stored source being edited."""

    changed = pyqtSignal()          # signed in, out, or another server picked
    finished = pyqtSignal(bool)     # a sign-in ended (whether it worked)

    def __init__(self, parent: QWidget | None, server: QLineEdit, user: QLineEdit, pw: QLineEdit,
                 playlist: dict | None = None) -> None:
        super().__init__(parent)
        self._server_field, self._user_field, self._pw_field = server, user, pw
        self._kind = ""
        self._gen = 0
        self._busy = False
        self._state: dict[str, Any] = {}
        self._for: tuple[str, str, str] = ("", "", "")
        self._qc: dict[str, Any] = {}
        self._pin: dict[str, Any] = {}
        self._servers: list[dict] = []
        self._prefer = ""
        self._plex_token = ""
        self._plex_user = ""
        self._picking: dict[str, Any] = {}
        self._filling = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.sign_btn = QPushButton(tr("ms_sign_in"))
        self.sign_btn.setObjectName("Primary")
        self.sign_btn.clicked.connect(self._sign_clicked)
        self.qc_btn = QPushButton(tr("ms_quick_connect"))
        self.qc_btn.clicked.connect(self._qc_clicked)
        self.plex_btn = QPushButton(tr("ms_plex_sign_in"))
        self.plex_btn.setObjectName("Primary")
        self.plex_btn.clicked.connect(self._plex_clicked)
        for b in (self.sign_btn, self.qc_btn, self.plex_btn):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.server_row = QWidget()
        srow = QHBoxLayout(self.server_row)
        srow.setContentsMargins(0, 0, 0, 0)
        self.server_lbl = QLabel(tr("ms_plex_server"))
        self.servers = QComboBox()
        self.servers.activated.connect(self._server_picked)
        srow.addWidget(self.server_lbl)
        srow.addWidget(self.servers, 1)
        lay.addWidget(self.server_row)
        self.server_row.hide()
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.status.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._poll)
        pl = playlist or {}
        if pl.get("kind") in MEDIA_KINDS and pl.get("token"):
            opts = dict(pl.get("options") or {})
            self._state = {"token": str(pl["token"]), "user": str(pl.get("username") or ""),
                           "server": str(pl.get("server") or ""),
                           "user_id": str(opts.get("user_id") or ""),
                           "server_id": str(opts.get("server_id") or ""),
                           "connections": str(opts.get("connections") or "")}
            self._for = (str(pl["kind"]), self._typed_server(str(pl["kind"])),
                         str(pl.get("username") or "").strip())
            self._kind = str(pl["kind"])
            self._say(tr("ms_signed_in", user=self._state["user"] or "?"))

    # -- what the form asks -------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._busy

    def signed_in(self) -> bool:
        return bool(self._state.get("token")) and self._for[0] == self._kind

    def values(self) -> dict:
        """What the source keeps of the sign-in: its address, the user's
        name, the token and the ids (under "options")."""
        s = self._state
        opts = {k: s[k] for k in ("user_id", "server_id", "connections") if s.get(k)}
        return {"server": s.get("server") or "", "username": s.get("user") or "",
                "token": s.get("token") or "", "options": opts,
                "name": s.get("server_name") or ""}

    def set_kind(self, kind: str) -> None:
        if kind != self._kind:
            self._kind = kind
            self._stop()
            if kind in MEDIA_KINDS and self.signed_in():
                self._say(tr("ms_signed_in", user=self._state.get("user") or "?"))
            else:
                self._say("")
        self._buttons()
        self.setVisible(kind in MEDIA_KINDS)

    def prefer(self, found: dict) -> None:
        """A server found on the network: the one picked once signed in."""
        self._prefer = str(found.get("id") or "")
        if self._servers and self._prefer:
            i = next((n for n, s in enumerate(self._servers) if s["id"] == self._prefer), -1)
            if i >= 0:
                self.servers.setCurrentIndex(i)
                self._server_picked(i)

    def form_changed(self, *_a: Any) -> None:
        """The address or the user changed: a token for another one is no
        good any more."""
        if self._filling or self._kind == "plex" or not self._state.get("token"):
            return
        now = (self._kind, self._typed_server(self._kind), self._user_field.text().strip())
        if now != self._for:
            self._state = {}
            self._for = ("", "", "")
            self._say(tr("ms_sign_in_again"))
            self._placeholders()
            self.changed.emit()

    def sign_in(self) -> bool:
        """Sign in with what the form has (OK pressed before Sign in);
        whether it started."""
        if self._kind not in ("jellyfin", "emby") or self._busy:
            return False
        if not (self._server_field.text().strip() and self._user_field.text().strip()):
            return False
        self._sign_clicked()
        return True

    def retranslate(self) -> None:
        self.sign_btn.setText(tr("ms_sign_in"))
        self.qc_btn.setText(tr("ms_quick_connect"))
        self.plex_btn.setText(tr("ms_plex_sign_in"))
        self.server_lbl.setText(tr("ms_plex_server"))
        if self.signed_in() and not self._busy:
            self._say(tr("ms_signed_in", user=self._state.get("user") or "?"))
        self._placeholders()

    # -- the look -------------------------------------------------------------

    def _typed_server(self, kind: str) -> str:
        return _server_of(self._server_field.text(), kind) if kind != "plex" else ""

    def _buttons(self) -> None:
        jelly = self._kind in ("jellyfin", "emby")
        self.sign_btn.setVisible(jelly)
        self.qc_btn.setVisible(self._kind == "jellyfin")
        self.plex_btn.setVisible(self._kind == "plex")
        self.server_row.setVisible(self._kind == "plex" and len(self._servers) > 1)
        for b in (self.sign_btn, self.qc_btn, self.plex_btn):
            b.setEnabled(not self._busy)
        self._placeholders()

    def _placeholders(self) -> None:
        if self._kind in ("jellyfin", "emby"):
            self._pw_field.setPlaceholderText(tr("ms_password_again") if self.signed_in() else "")

    def _say(self, text: str, error: bool = False) -> None:
        self.status.setStyleSheet(f"color:{P['error'] if error else P['muted']}; font-size:11px;")
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def _start(self, text: str) -> int:
        self._gen += 1
        self._busy = True
        self._buttons()
        self._say(text)
        return self._gen

    def _end(self) -> None:
        self._busy = False
        self._buttons()

    def _stop(self) -> None:
        """Whatever was going on is no longer wanted."""
        self._gen += 1
        self._timer.stop()
        self._qc = {}
        self._pin = {}
        if self._busy:
            self._busy = False

    def _run(self, fn: Callable[..., Any], done: Callable[[tuple], None], *args: Any) -> None:
        run_async(QThreadPool.globalInstance(), _tagged, done, None, self._gen, fn, *args)

    def _alive(self, got: Any) -> bool:
        return (not sip.isdeleted(self) and isinstance(got, tuple) and len(got) == 3
                and got[0] == self._gen)

    def _failed(self, why: str) -> None:
        self._stop()
        self._end()
        self._say(why, error=True)
        self.finished.emit(False)

    def _signed(self, state: dict) -> None:
        self._stop()
        self._state = state
        self._for = (self._kind, "", str(state.get("user") or "").strip())
        if self._kind != "plex":
            # The form shows what is kept: the address as stored, the user,
            # and no password.
            self._filling = True
            try:
                self._server_field.setText(state.get("server", ""))
                if state.get("user"):
                    self._user_field.setText(state["user"])
                self._pw_field.clear()
            finally:
                self._filling = False
            self._for = (self._kind, self._typed_server(self._kind), self._user_field.text().strip())
        self._end()
        self._say(tr("ms_signed_in", user=state.get("user") or "?"))
        self.changed.emit()
        self.finished.emit(True)

    # -- Jellyfin and Emby ----------------------------------------------------------

    def _sign_clicked(self) -> None:
        kind = self._kind
        server = self._server_field.text().strip()
        user = self._user_field.text().strip()
        if not server:
            self._say(tr("playlist_box_required"), error=True)
            return
        if not user:
            self._say(tr("playlist_required_fields"), error=True)
            self._user_field.setFocus()
            return
        from ..providers import jellyfin
        self._start(tr("ms_signing_in"))
        self._run(jellyfin.sign_in, self._sign_done, server, user, self._pw_field.text(), kind)

    def _sign_done(self, got: Any) -> None:
        if not self._alive(got):
            return
        _gen, result, why = got
        if result is None:
            self._failed(why)
            return
        self._signed(dict(result))

    def _qc_clicked(self) -> None:
        server = self._server_field.text().strip()
        if not server:
            self._say(tr("playlist_box_required"), error=True)
            return
        self._start(tr("ms_signing_in"))
        self._qc = {"server": server, "tries": 0}
        self._run(self._qc_begin, self._qc_started, server)

    @staticmethod
    def _qc_begin(server: str) -> dict:
        from ..providers import jellyfin
        if not jellyfin.quick_connect_enabled(server):
            raise LocalizedError("err_ms_qc_off")
        return jellyfin.quick_connect_start(server)

    def _qc_started(self, got: Any) -> None:
        if not self._alive(got):
            return
        _gen, result, why = got
        if result is None:
            self._failed(why)
            return
        self._qc.update(result)
        self._say(tr("ms_qc_code", code=result["code"]))
        self._timer.start(QC_EVERY_MS)

    def _qc_check(self, server: str, secret: str) -> dict | None:
        from ..providers import jellyfin
        if not jellyfin.quick_connect_ready(server, secret):
            return None
        return jellyfin.quick_connect_finish(server, secret)

    def _qc_polled(self, got: Any) -> None:
        if not self._alive(got) or not self._qc:
            return
        _gen, result, why = got
        if why:
            self._failed(why)
        elif isinstance(result, Hiccup):
            self._hiccup(self._qc, result, QC_EVERY_MS)
        elif result is None:
            self._qc["hiccups"] = 0
            self._qc["tries"] += 1
            if self._qc["tries"] >= QC_TRIES:
                self._failed(tr("err_ms_qc_expired"))
            else:
                self._timer.start(QC_EVERY_MS)
        else:
            self._signed(dict(result))

    # -- Plex -------------------------------------------------------------------------

    def _plex_clicked(self) -> None:
        from ..providers import plex
        self._servers = []
        self._start(tr("ms_signing_in"))
        self._run(plex.create_pin, self._pin_made)

    def _pin_made(self, got: Any) -> None:
        if not self._alive(got):
            return
        _gen, result, why = got
        if result is None:
            self._failed(why)
            return
        from ..core.xdg import open_url
        from ..providers import plex
        self._pin = dict(result, tries=0)
        open_url(plex.auth_url(result["code"]))
        self._say(tr("ms_plex_waiting"))
        self._timer.start(PIN_EVERY_MS)

    @staticmethod
    def _plex_account(token: str) -> dict:
        from ..providers import plex
        return {"user": plex.account_name(token), "servers": plex.servers(token)}

    def _pin_polled(self, got: Any) -> None:
        if not self._alive(got) or not self._pin:
            return
        _gen, token, why = got
        if why:
            self._failed(why)
            return
        if isinstance(token, Hiccup):
            self._hiccup(self._pin, token, PIN_EVERY_MS)
            return
        if not token:
            self._pin["hiccups"] = 0
            self._pin["tries"] += 1
            if self._pin["tries"] >= PIN_TRIES:
                self._failed(tr("err_ms_pin_expired"))
            else:
                self._timer.start(PIN_EVERY_MS)
            return
        self._pin = {}
        self._plex_token = str(token)
        self._run(self._plex_account, self._plex_listed, self._plex_token)

    def _plex_listed(self, got: Any) -> None:
        if not self._alive(got):
            return
        _gen, result, why = got
        if result is None:
            self._failed(why)
            return
        self._servers = list(result["servers"])
        self._plex_user = str(result.get("user") or "")
        if not self._servers:
            self._failed(tr("ms_plex_no_servers"))
            return
        self.servers.clear()
        for s in self._servers:
            self.servers.addItem(s["name"], s["id"])
        i = next((n for n, s in enumerate(self._servers) if self._prefer and s["id"] == self._prefer), 0)
        self.servers.setCurrentIndex(i)
        self._buttons()
        self._server_picked(i)

    def _server_picked(self, index: int) -> None:
        if not (0 <= index < len(self._servers)):
            return
        from ..providers import plex
        s = self._servers[index]
        self._start(tr("ms_plex_checking", server=s["name"]))
        self._picking = s
        self._run(plex.pick_connection, self._plex_reached, list(s["connections"]), s["id"])

    def _plex_reached(self, got: Any) -> None:
        if not self._alive(got):
            return
        _gen, uri, _why = got
        s = self._picking
        conns = list(s["connections"])
        state = {"token": s["token"], "user": self._plex_user, "server": uri or (conns[0] if conns else ""),
                 "server_id": s["id"], "connections": " ".join(conns), "server_name": s["name"]}
        if not state["server"]:
            self._failed(tr("ms_plex_unreachable", server=s["name"]))
            return
        self._signed(state)
        if not uri:
            self._say(tr("ms_plex_unreachable", server=s["name"]), error=True)

    # -- waiting --------------------------------------------------------------------

    def _hiccup(self, wait: dict, h: Hiccup, every_ms: int) -> None:
        """A poll of *wait* did not get through: asked again a little later
        (twice as long each time) - given up after POLL_HICCUPS in a row.
        A cancel meanwhile stops the timer and drops the wait (_stop)."""
        wait["hiccups"] = n = int(wait.get("hiccups", 0)) + 1
        wait["tries"] = int(wait.get("tries", 0)) + 1      # still within the code's life
        if n > POLL_HICCUPS:
            self._failed(h.why)
            return
        log.info("media sign-in: a poll did not get through (%d of %d) - trying again", n, POLL_HICCUPS)
        self._timer.start(every_ms * 2 ** n)

    def _poll(self) -> None:
        if sip.isdeleted(self):
            return
        if self._qc.get("secret"):
            self._run(_patient, self._qc_polled, self._qc_check, self._qc["server"],
                      self._qc["secret"])
        elif self._pin.get("id"):
            from ..providers import plex
            self._run(_patient, self._pin_polled, plex.check_pin, self._pin["id"],
                      self._pin["code"])
