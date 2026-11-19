"""Settings' SERVICES pages: Watch history, Sync and Integrations (their
rows and keys: docs/SETTINGS_MAP.md).

Each ``build_<page>(ctx)`` fills its page the first time it is needed
(SettingsShell.ensure_built), never as the window opens, and leaves what
the page stores and applies, if anything, as
``ctx.page_commits[<page key>]``: commit(put, changed, final), run by
open_settings' commit. *ctx* is the Settings window's SettingsContext
(ui/mw_settings.py). The services' own cards come from their mixins:
Simkl's (mw_simkl), sync's (mw_sync), Discord's (mw_discord) and the
radio's (mw_radio).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from PyQt6 import sip
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QLabel, QLineEdit, QPushButton

from ..core.workers import run_async
from ..i18n import tr
from ..providers.opensubtitles import OpenSubtitles
from ..providers.opensubtitles import bundled_key as bundled_os_key
from .settings_ui import hbox
from .theme import P

if TYPE_CHECKING:
    from .mw_settings import SettingsContext


def build_history(ctx: SettingsContext) -> None:
    """Watch history: Trakt, Simkl and what they keep. They were a page
    each, with a sync row each for the one store they fill together: a card
    each, and that store's own below."""
    win, d, history = ctx.win, ctx.dialog, ctx.pages["history"]
    own = ctx.own
    trakt_browser_btn = QPushButton(tr("trakt_connect_browser"),
                                    objectName="Primary")
    trakt_disconnect_btn = QPushButton(tr("trakt_disconnect"))
    trakt_watchlist_btn = QPushButton(tr("trakt_watchlist_btn"))
    c = history.card(tr("tab_trakt"))
    trakt_row = c.row(tr("trakt_not_connected"),
                      hbox(trakt_browser_btn, trakt_disconnect_btn,
                           stretch=False),
                      tr("trakt_connect_browser_hint"),
                      search="trakt account sign in", badge=own())
    c.row(tr("set_trakt_lists"), trakt_watchlist_btn,
          tr("trakt_scrobble_hint"), search="trakt")
    # Simkl's card (mw_simkl): only in a build with its client id.
    simkl_card = (win._build_simkl_card(history, d)
                  if hasattr(win, "_build_simkl_card") else None)
    sync_now_btn = QPushButton(tr("trakt_sync_now"))
    c = history.card(tr("set_card_watched_history"))
    sync_row = c.row(tr("trakt_sync_never"), sync_now_btn,
                     tr("trakt_sync_hint"), search="trakt simkl sync")

    def refresh_sync_status():
        n_m, n_e = (len(win.watched.movies),
                    sum(len(v) for v in win.watched.episodes.values()))
        if win.watched.last_sync_at:
            when = datetime.fromtimestamp(
                win.watched.last_sync_at).strftime("%Y-%m-%d %H:%M")
            sync_row.label.setText(
                tr("trakt_sync_status", when=when,
                   movies=n_m, episodes=n_e))
        else:
            sync_row.label.setText(tr("trakt_sync_never"))
        # Trakt's, Simkl's or both: the one store they fill together.
        sync_now_btn.setEnabled(
            bool(win._watch_accounts())
            and not win._watched_sync_running)

    def do_sync_now():
        sync_now_btn.setEnabled(False)
        sync_row.label.setText(tr("trakt_syncing"))
        # Kick a forced sync and re-poll status while it runs so the
        # label flips to "synced" without reopening the dialog.
        win._maybe_sync_watched(force=True)
        poll = QTimer(d)

        def tick():
            if not win._watched_sync_running:
                poll.stop()
                refresh_sync_status()
        poll.timeout.connect(tick)
        poll.start(400)

    sync_now_btn.clicked.connect(do_sync_now)

    def refresh_trakt_status():
        connected = win.trakt.is_connected()
        trakt_row.label.setText(tr("trakt_connected") if connected
                                else tr("trakt_not_connected"))
        # One of the two: connect while signed out, disconnect while
        # in.
        trakt_browser_btn.setVisible(not connected)
        trakt_disconnect_btn.setVisible(connected)
        trakt_row.hint_label.setVisible(not connected)
        trakt_watchlist_btn.setEnabled(connected)
        refresh_sync_status()

    def do_connect_browser():
        # Sign in through the browser with whatever Trakt app is
        # active (the built-in one unless the user saved their own).
        win._trakt_browser_auth_dialog(d)
        refresh_trakt_status()

    def do_trakt_disconnect():
        win.trakt.disconnect()
        refresh_trakt_status()

    trakt_browser_btn.clicked.connect(do_connect_browser)
    trakt_disconnect_btn.clicked.connect(do_trakt_disconnect)
    trakt_watchlist_btn.clicked.connect(
        lambda: win._open_trakt_dialog(d))
    refresh_trakt_status()
    if simkl_card is not None:
        # Signed in or out there: the history card says so here.
        simkl_card.on_change = refresh_sync_status


def build_sync(ctx: SettingsContext) -> None:
    """Sync between computers, for everyone (mw_sync)."""
    win, d, sync_page = ctx.win, ctx.dialog, ctx.pages["sync"]
    if hasattr(win, "_build_sync_card"):
        win._build_sync_card(sync_page, d)


def build_integrations(ctx: SettingsContext) -> None:
    """Integrations: OpenSubtitles' key and account, the Discord status and
    the radio directory."""
    win, d, integrations = ctx.win, ctx.dialog, ctx.pages["integrations"]
    # OpenSubtitles: a key (built in, or the user's own) and an
    # optional account, which only raises how many subtitles a day can
    # be fetched. A service's account, so beside the other services
    # rather than among the subtitle settings it serves.
    os_bundled = bool(bundled_os_key())
    os_key_edit = QLineEdit(win.settings.value("os_api_key", "") or "")
    os_test_btn = QPushButton(tr("btn_test"))
    os_user_edit = QLineEdit(win.settings.value("os_username", "") or "")
    os_pass_edit = QLineEdit(win.settings.value("os_password", "") or "")
    os_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
    os_user_edit.setPlaceholderText(tr("login_username"))
    os_pass_edit.setPlaceholderText(tr("login_password"))
    c = os_card = integrations.card("OpenSubtitles.com")
    c.row(tr("setting_api_key"), hbox(os_key_edit, os_test_btn,
                                      stretch=False),
          tr("os_key_builtin_hint") if os_bundled else tr("os_key_hint"),
          stretch_control=True)
    c.row(tr("set_card_account"),
          hbox(os_user_edit, os_pass_edit, stretch=False),
          tr("os_account_hint"), stretch_control=True,
          search=f"{tr('login_username')} {tr('login_password')}")
    os_status = QLabel()
    os_status.setWordWrap(True)
    c.shown_if(c.widget(os_status), lambda: bool(os_status.text()))

    def os_say(text: str, style: str) -> None:
        if sip.isdeleted(os_status):        # an answer after the window went
            return
        os_status.setText(text)
        os_status.setStyleSheet(style)
        os_card.refresh()

    def test_os_key() -> None:
        key = os_key_edit.text().strip() or bundled_os_key()
        if not key:
            os_say(tr("tmdb_enter_key"),
                   f"color:{P['error']}; font-size:12px;")
            return
        os_say(tr("tmdb_checking"), f"color:{P['muted']}; font-size:12px;")
        client = OpenSubtitles(key, os_user_edit.text(),
                               os_pass_edit.text())

        def check():
            # With an account, its password is what gets checked too.
            if client.username and client.password:
                client.login()
            client.search(languages="en", query="big buck bunny")
            return True

        def ok(_r):
            os_say(tr("tmdb_key_works"),
                   f"color:{P['accent']}; font-size:12px; font-weight:600;")

        def fail(msg):
            os_say(tr("tmdb_key_failed", msg=msg),
                   f"color:{P['error']}; font-size:12px;")

        run_async(win.pool, check, ok, fail)

    os_test_btn.clicked.connect(test_os_key)
    # What plays, as a Discord status (mw_discord): only in a build
    # that carries the Discord application's id.
    if hasattr(win, "_build_discord_card"):
        win._build_discord_card(integrations, d)
    # The radio directory: whether a station played is told
    # (mw_radio).
    win._build_radio_card(integrations)

    def commit_integrations(put, changed: set, final: bool) -> None:
        # Written only when changed: an untouched empty field is not
        # a setting.
        for skey, value in (
                ("os_api_key", os_key_edit.text().strip()),
                ("os_username", os_user_edit.text().strip()),
                ("os_password", os_pass_edit.text())):
            if value != (win.settings.value(skey, "") or ""):
                put(skey, value)
    ctx.page_commits["integrations"] = commit_integrations
