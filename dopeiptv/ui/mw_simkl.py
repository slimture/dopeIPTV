"""Simkl in the window (providers/simkl.py talks to Simkl): the sign-in with
a code, the scrobbles of what plays, its card in Settings, and the watchlist
and history window. Marking a title watched or for later, and the watched
sync, go to every account signed in - Trakt, Simkl or both - through
mw_trakt's _watch_accounts.

Offered only in a build that carries dopeIPTV's Simkl client id; without it
nothing here shows, and every hook returns at once.
"""

from __future__ import annotations

import threading
import time

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QListWidget, QMessageBox, QPushButton,
    QTabWidget, QVBoxLayout,
)

from ..core import privacy
from ..core.log import log
from ..core.workers import run_async
from ..core.xdg import open_url
from ..i18n import tr
from ..providers.metadata import PosterResolver
from ..providers.simkl import scrobble_episode, scrobble_movie
from .theme import P


class _SimklMixin:
    """MainWindow mixin: Simkl beside Trakt."""

    # -- scrobbling ------------------------------------------------------------------

    def _simkl_payload(self, kind: str, item: dict) -> dict | None:
        """What Simkl is told plays: the TMDB id the app already has for it
        (never a lookup on the window's thread) and its title."""
        if kind == "movie":
            title = item.get("name") or item.get("title") or ""
            if not title:
                return None
            cleaned, year = PosterResolver.clean_title(title)
            tid = item.get("_tmdb_id")
            if not isinstance(tid, int) and self.tmdb is not None:
                tid = self.tmdb.tmdb_id_for(title, "vod")
            return scrobble_movie(tid if isinstance(tid, int) else None,
                                  cleaned or title, year)
        if kind == "episode":
            sctx = self.series_ctx or {}
            show = sctx.get("name") or sctx.get("title") or ""
            try:
                season = int(item.get("season"))
                episode = int(item.get("episode_num"))
            except (TypeError, ValueError):
                return None
            if not show:
                return None
            cleaned, _year = PosterResolver.clean_title(show)
            return scrobble_episode(self._show_tmdb_id_for_episode(),
                                    cleaned or show, season, episode)
        return None

    def _simkl_start_for_item(self, kind: str, item) -> None:
        simkl = getattr(self, "simkl", None)
        if not item or simkl is None or not simkl.is_connected() or privacy.private():
            return              # private viewing: Simkl is not told either
        try:
            payload = self._simkl_payload(kind, item)
        except Exception:
            log.debug("simkl: no scrobble", exc_info=True)
            return
        if not payload:
            return
        self._simkl_active = {"payload": payload}
        self._simkl_send("scrobble_start", payload, 0.0)

    def _simkl_send(self, method: str, payload: dict, progress: float) -> None:
        simkl = self.simkl

        def job() -> None:
            try:
                getattr(simkl, method)(payload, progress)
            except Exception as e:
                log.debug("simkl: %s failed (%s)", method, type(e).__name__)
        threading.Thread(target=job, daemon=True).start()

    def _simkl_progress(self) -> float:
        p = getattr(self, "player", None)
        try:
            return float(p.progress_percent()) if p is not None else 0.0
        except Exception:
            return 0.0

    def _simkl_stop_current(self) -> None:
        active = getattr(self, "_simkl_active", None)
        if not active:
            return
        self._simkl_active = None
        self._simkl_send("scrobble_stop", active["payload"], self._simkl_progress())

    def _simkl_paused(self, paused: bool) -> None:
        """The player paused or played on: Simkl keeps the place, so
        another device can pick it up there."""
        active = getattr(self, "_simkl_active", None)
        if not active or privacy.private():
            return
        self._simkl_send("scrobble_pause" if paused else "scrobble_start",
                         active["payload"], self._simkl_progress())

    # -- Settings: Simkl's card on the Watch history page ---------------------------------

    def _build_simkl_card(self, pg, dlg):
        """Simkl's card on Settings > Watch history, beside Trakt's - only
        in a build with the client id. Returns it (its on_change is told
        when the account changes), or None."""
        simkl = getattr(self, "simkl", None)
        if simkl is None or not simkl.available():
            return None
        return _SimklCard(self, pg, dlg)

    # -- sign-in -------------------------------------------------------------------

    def _simkl_connect_dialog(self, parent) -> bool:
        """The PIN sign-in: Simkl's code shown, simkl.com/pin opened, and
        the answer asked for until it comes or the code runs out. True once
        signed in - for the profile in use: one switched to meanwhile (from
        the phone) is not the one that signed in, and is not synced with
        this account. The dialog signs in the account it was opened with."""
        gen = getattr(self, "_profile_gen", 0)
        d = _SimklPinDialog(parent, self)
        d.exec()
        return d.simkl.is_connected() and gen == getattr(self, "_profile_gen", 0)

    def _open_simkl_dialog(self, parent) -> None:
        if not self.simkl.is_connected():
            QMessageBox.information(parent, "Simkl", tr("simkl_connect_first"))
            return
        _SimklListsDialog(parent, self).exec()


class _SimklCard(QObject):
    """Settings > Watch history, Simkl's card: the account and its lists.
    The watched history they fill with Trakt's has one card of its own on
    the page; *on_change* is told when the account changes, to bring it up
    to date. Lives as long as the dialog."""

    def __init__(self, win, pg, dlg) -> None:
        super().__init__(dlg)
        from .settings_ui import hbox
        self.win, self.dlg = win, dlg
        self.on_change = None
        self.connect_btn = QPushButton(tr("simkl_connect"), objectName="Primary")
        self.disconnect_btn = QPushButton(tr("trakt_disconnect"))
        self.lists_btn = QPushButton(tr("trakt_watchlist_btn"))
        c = pg.card(tr("tab_simkl"))
        badge = None
        if getattr(win, "_profile_many", lambda: False)():
            # The account is the profile's own (core/profiles.py).
            from .source_page import Origin
            badge = Origin()
            badge.set(tr("profile_own"), True)
            badge.setToolTip(tr("profile_own_tip"))
        self.account_row = c.row(tr("trakt_not_connected"),
                                 hbox(self.connect_btn, self.disconnect_btn,
                                      stretch=False),
                                 tr("simkl_connect_hint"),
                                 search="simkl account sign in pin code", badge=badge)
        c.row(tr("set_trakt_lists"), self.lists_btn, tr("simkl_scrobble_hint"),
              search="simkl scrobble watchlist history")
        self.poll = QTimer(self)
        self.poll.setInterval(400)
        self.poll.timeout.connect(self.tick)
        self.connect_btn.clicked.connect(self.sign_in)
        self.disconnect_btn.clicked.connect(self.sign_out)
        self.lists_btn.clicked.connect(self.lists)
        self.refresh()

    def _alive(self) -> bool:
        return not sip.isdeleted(self.account_row)

    def refresh(self) -> None:
        if not self._alive():
            return
        connected = self.win.simkl.is_connected()
        self.account_row.label.setText(tr("simkl_connected") if connected
                                       else tr("trakt_not_connected"))
        self.connect_btn.setVisible(not connected)
        self.disconnect_btn.setVisible(connected)
        self.account_row.hint_label.setVisible(not connected)
        self.lists_btn.setEnabled(connected)
        if self.on_change is not None:
            self.on_change()

    def sign_in(self, _checked: bool = False) -> None:
        if self.win._simkl_connect_dialog(self.dlg):
            self.win._maybe_sync_watched(force=True)
            self.poll.start()
        self.refresh()

    def sign_out(self, _checked: bool = False) -> None:
        self.win.simkl.disconnect()
        log.info("simkl: signed out")
        self.refresh()

    def lists(self, _checked: bool = False) -> None:
        self.win._open_simkl_dialog(self.dlg)

    def tick(self) -> None:
        if not self.win._watched_sync_running:
            self.poll.stop()
            self.refresh()


class _SimklPinDialog(QDialog):
    """Simkl's code, and the wait for it to be typed at simkl.com/pin."""

    def __init__(self, parent, win) -> None:
        super().__init__(parent)
        self.win = win
        self.simkl = win.simkl
        self.code = ""
        self.interval = 5
        self.expires_at = 0.0
        self.setWindowTitle(tr("simkl_connect_title"))
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        self.info = QLabel(tr("simkl_requesting_code"))
        self.info.setWordWrap(True)
        self.info.setStyleSheet("font-size:13px;")
        lay.addWidget(self.info)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.cancelled = False
        self.finished.connect(self._closed)
        run_async(win.pool, self.simkl.start_pin, self._started, self._start_failed)

    def _closed(self, _result: int) -> None:
        self.cancelled = True           # closed any way at all: stop asking

    def _live(self) -> bool:
        return not self.cancelled and not sip.isdeleted(self)

    def _started(self, data: dict) -> None:
        if not self._live():
            return
        self.code = str(data.get("user_code") or "")
        try:
            self.interval = max(2, int(data.get("interval") or 5))
            self.expires_at = time.time() + int(data.get("expires_in") or 900)
        except (TypeError, ValueError):
            self.expires_at = time.time() + 900
        page = str(data.get("verification_url") or "https://simkl.com/pin/")
        self.info.setText(tr("trakt_enter_code", url=page, code=self.code))
        open_url(str(data.get("verification_uri_complete") or page))
        QTimer.singleShot(self.interval * 1000, self._poll)

    def _start_failed(self, msg: str) -> None:
        if self._live():
            self.info.setText(tr("simkl_could_not_start", msg=msg))

    def _poll(self) -> None:
        if not self._live() or not self.code:
            return
        if time.time() > self.expires_at:
            self.info.setText(tr("trakt_code_expired"))
            return
        run_async(self.win.pool, self.simkl.poll_pin, self._polled, self._poll_failed,
                  self.code)

    def _polled(self, data) -> None:
        if not self._live():
            return
        if data is None:
            QTimer.singleShot(self.interval * 1000, self._poll)
            return
        self.info.setText(tr("simkl_connected_excl"))
        QTimer.singleShot(700, self.accept)

    def _poll_failed(self, msg: str) -> None:
        if self._live():
            self.info.setText(tr("simkl_login_failed", msg=msg))


class _SimklListsDialog(QDialog):
    """What the Simkl account holds: Plan to watch, and what was watched."""

    def __init__(self, parent, win) -> None:
        super().__init__(parent)
        self.win = win
        self.setWindowTitle(tr("simkl_watchlist_title"))
        self.setMinimumSize(480, 500)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)
        self.wl_list = QListWidget()
        self.hist_list = QListWidget()
        tabs.addTab(self.wl_list, tr("trakt_tab_watchlist"))
        tabs.addTab(self.hist_list, tr("nav_history"))
        self.status = QLabel(tr("common_loading"))
        self.status.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        lay.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        run_async(win.pool, self._load, self._loaded, self._failed)

    def _load(self) -> tuple[list, list]:
        simkl = self.win.simkl
        return simkl.watchlist(), simkl.history(100)

    def _loaded(self, lists) -> None:
        if sip.isdeleted(self):
            return
        watchlist, history = lists
        for e in watchlist:
            self.wl_list.addItem(e["title"])
        for e in history:
            self.hist_list.addItem(f"{e['watched_at'][:10]}  {e['title']}".strip())
        self.status.setText("")

    def _failed(self, msg: str) -> None:
        if not sip.isdeleted(self):
            self.status.setText(tr("simkl_load_failed", msg=msg))
