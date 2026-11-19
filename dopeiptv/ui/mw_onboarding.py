"""First-run onboarding + offline provider-hint mixin for MainWindow.

The welcome overlay and its wizard steps, plus the "+ Add provider" hint shown
when the app is running against the empty offline stand-in.

The welcome's Bring step (settings from another computer) is done here
with what Settings does: the sync code joined as Settings > Sync joins it
(mw_sync: _sync_join, its round on the sync thread), a backup picked and
staged as Settings' restore does it (dialogs.pick_backup, stage_backup).
The join takes favorites and settings, and the profiles with them; the
other computer's sources and watch history come along only where that
computer syncs them - this one then syncs them too, as it would with their
switches turned on in Settings. A miss before anything came leaves sync
off again here. Nothing of it opens a stream, and the code is never logged.
"""
from __future__ import annotations

import json
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QPropertyAnimation, Qt, QTimer
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QPushButton

from ..core.log import log
from ..i18n import tr
from ..providers.client import DemoClient, OfflineClient
from .welcome import WelcomeOverlay

#: What the other computer may sync that this one does not by default.
_FOLLOWED = ("sources", "history")
#: The welcome's words for a sync that did not finish (mw_sync's kinds).
_SYNC_MISS = {"net": "onb_sync_err_net", "none": "onb_sync_err_none",
              "read": "sync_err_read", "newer": "sync_err_newer"}


def _a_source(ident: str) -> bool:
    """A source's entry in the synced copy (core/sync's ["s", id])."""
    try:
        got = json.loads(ident)
    except ValueError:
        return False
    return isinstance(got, list) and len(got) == 2 and got[0] == "s"


def shared_there(doc: dict[str, Any]) -> set[str]:
    """Of _FOLLOWED, what the synced copy *doc* holds: what the other
    computer syncs (a source, a title watched)."""
    from ..core import sync
    out = set()
    if any(_a_source(k) for k in sync.live(doc, "sources")):
        out.add("sources")
    if sync.live(doc, "history"):
        out.add("history")
    return out


class _OnboardingMixin:
    """MainWindow mixin: the first-run welcome wizard and the '+ Add provider' hint."""

    #: The Bring step's sync: waiting for how it went; something came
    #: already (a miss after that leaves sync on); the source ids before.
    _wsync_pending = False
    _wsync_joined = False
    _wsync_hooked = False
    _wsync_before: frozenset[str] = frozenset()

    def show_welcome(self) -> None:
        """Show the first-run onboarding wizard (no provider configured)."""
        if self._welcome is None:
            self._welcome = WelcomeOverlay(
                self, settings=self.settings,
                on_connect=self._wizard_connect,
                on_explore=self._wizard_explore,
                on_connect_trakt=self._wizard_connect_trakt,
                on_language_change=self._wizard_language,
                on_demo=self.start_demo)
            self._welcome.sync_asked.connect(self._wizard_sync)
            self._welcome.sync_cancelled.connect(self._wizard_sync_cancel)
            self._welcome.restore_asked.connect(self._wizard_restore)
            self._welcome.quit_asked.connect(self._wizard_quit)
        else:
            self._welcome.reset()
        # If Trakt was linked in an earlier visit, show it as connected.
        self._welcome.set_trakt_connected(self.trakt.is_connected())
        self._welcome.cover()
        self._update_provider_hint()

    def _wizard_connect_trakt(self) -> None:
        """Run the Trakt sign-in flow, then reflect the result in the wizard so
        the user gets a clear 'connected' confirmation on the same screen."""
        self._trakt_connect_flow(self)
        if self._welcome is not None:
            self._welcome.set_trakt_connected(self.trakt.is_connected())

    def _wizard_language(self, code: str) -> None:
        # The same switch as Settings makes: Arabic, Persian, Hebrew and
        # Urdu mirror the layout right away, and Qt's own buttons follow.
        self._set_language(code)

    def _wizard_connect(self, server: str, user: str, pw: str,
                        kind: str = "xtream", name: str = "", mac: str = "",
                        signed: dict | None = None) -> None:
        # Use the name the user typed; fall back to the server host so an
        # unnamed playlist still gets a sensible label.
        name = name.strip() or server.split("//")[-1].split("/")[0] \
            or tr("playlist_default_name")
        row = {"name": name, "kind": kind, "server": server, "username": user,
               "password": pw, "epg_url": "", "refresh": "never", "mac": mac}
        if signed:
            # A media server: the token its sign-in left, and its ids.
            row["token"] = str(signed.get("token") or "")
            if signed.get("options"):
                row["options"] = dict(signed["options"])
        pl = self.playlist_store.add(row)
        self.playlist_store.set_active(pl["id"])
        self.switch_playlist(pl["id"])
        # The wizard stays open for the optional Trakt step and hides itself
        # on Finish; the provider hint should not appear now.
        self._update_provider_hint()

    def _wizard_explore(self) -> None:
        self._set_status(tr("welcome_add_hint"))
        self._update_provider_hint()

    # -- the Bring step: settings from another computer ----------------------

    def _wizard_view(self) -> WelcomeOverlay | None:
        ov = self._welcome
        return ov if ov is not None and not sip.isdeleted(ov) else None

    def _wizard_source_ids(self) -> set[str]:
        store = getattr(self, "playlist_store", None)
        return {str(p.get("id")) for p in (store.playlists() if store is not None else [])}

    def _wizard_sync(self, code: str) -> None:
        """The welcome's sync code: joined as Settings > Sync joins it, its
        rounds on the sync thread; the welcome hears how it went."""
        from ..core import build_check, sync
        ov = self._wizard_view()
        raw = sync.parse_code(code)
        if raw is None:
            if ov is not None:
                ov.sync_failed(tr("sync_code_bad"))
            return
        if not build_check.allowed():
            if ov is not None:
                ov.sync_failed(build_check.notice("sync"))
            return
        if not self._wsync_hooked:
            self._sync_signals.changed.connect(self._wizard_sync_changed)
            self._wsync_hooked = True
        self._wsync_before = frozenset(self._wizard_source_ids())
        self._wsync_pending = True
        if sync.code_of(self.settings) == raw:
            # Syncing with it already (from Settings): one more round.
            self._wsync_joined = True
            self.sync_now(manual=True)
        else:
            self._wsync_joined = False
            self._sync_join(code)

    def _wizard_sync_changed(self) -> None:
        if self._wsync_pending:
            # Looked at once mw_sync is done with it: a round says "ok"
            # before what came is shown, a miss forgets after saying so.
            QTimer.singleShot(0, self._wizard_sync_check)

    def _wizard_sync_check(self) -> None:
        from ..core import sync
        if not self._wsync_pending or self._sync_busy:
            return
        kind = self._sync_status[0]
        if kind in ("idle", "running"):
            return
        if kind in ("ok", "older"):
            self._wsync_joined = True
            state = self._sync_state
            have = sync.chosen(self.settings)
            there = shared_there(state.doc) if state is not None else set()
            more = [c for c in _FOLLOWED if c in there and c not in have]
            if more:
                # The other computer syncs its sources or history: this one
                # does too now, as with those switches on in Settings, and
                # the next round brings what is there.
                sync.set_chosen(self.settings, list(have) + more)
                if self.sync_now(manual=True):
                    return
            self._wsync_pending = False
            self._wizard_sync_landed()
            return
        self._wsync_pending = False
        if not self._wsync_joined and self._sync_on():
            # Nothing came: sync stays off here, as it was.
            self._sync_forget()
        ov = self._wizard_view()
        if ov is not None:
            ov.sync_failed(self._wizard_sync_miss(kind))

    @staticmethod
    def _wizard_sync_miss(kind: str) -> str:
        from ..core import build_check
        if kind == "modified":
            return build_check.notice("sync")
        return tr(_SYNC_MISS.get(kind, "onb_sync_err_other"))

    def _wizard_sync_cancel(self) -> None:
        """Back (or Esc) while it fetched."""
        if not self._wsync_pending:
            return
        self._wsync_pending = False
        if not self._wsync_joined and self._sync_on():
            self._sync_forget()

    def _wizard_sync_landed(self) -> None:
        ov = self._wizard_view()
        came = [p for p in self.playlist_store.playlists()
                if str(p.get("id")) not in self._wsync_before]
        log.info("welcome: synced from another computer, %d source(s) with it", len(came))
        if not came:
            if ov is not None:
                ov.sync_no_sources()
            return
        self._wizard_load(came)
        # "Who's watching?" as at start, with two profiles or more.
        self._maybe_pick_profile_at_start()

    def _wizard_load(self, came: list[dict]) -> None:
        """Sources that came (a sync, a backup's playlists): the welcome
        goes and the app uses one, as after Connect."""
        ov = self._wizard_view()
        if ov is not None:
            ov.finish()
        store = self.playlist_store
        active = store.active()
        ids = {str(p.get("id")) for p in came}
        pl = active if active is not None and str(active.get("id")) in ids else came[0]
        store.set_active(pl["id"])
        self.switch_playlist(pl["id"])

    def _wizard_restore(self) -> None:
        """Restore a backup…: picked, shown and staged as Settings' restore
        does it - at the first start there is nothing here to lose."""
        from ..core import backup
        from .dialogs import RestoreBackupDialog, pick_backup, stage_backup
        ov = self._wizard_view()
        b, why = pick_backup(self)
        if b is None:
            if why and ov is not None:
                ov.restore_failed(why)
            return
        store = getattr(self, "playlist_store", None)
        dlg = RestoreBackupDialog(self, b, backup.summary(b),
                                  len(backup.missing_paths(b)), backup.risky(b),
                                  can_add=store is not None)
        if not dlg.exec():
            return
        if dlg.choice() == "add":
            before = self._wizard_source_ids()
            added = store.import_data(b.sources)
            log.info("restore: %d playlist(s) added from a backup", added)
            came = [p for p in store.playlists() if str(p.get("id")) not in before]
            if came:
                self._wizard_load(came)
            elif ov is not None:
                ov.restore_failed(tr("pl_import_done", n=0))
            return
        if not backup.replaceable(b):
            if ov is not None:
                ov.restore_failed(tr("restore_cannot_replace"))
            return
        parental = getattr(self, "parental", None)
        if parental is not None and parental.has_pin() and not self._request_unlock():
            return
        why = stage_backup(b)
        if ov is not None:
            if why:
                ov.restore_failed(why)
            else:
                ov.restore_staged()

    def _wizard_quit(self) -> None:
        """Quit with a backup staged: closed as Settings' restore closes
        the app - nothing written on the way out - and put in place as it
        starts again (core/backup.apply_pending)."""
        self._resetting = True
        self.close()

    # -- "no provider yet" affordance ----------------------------------------

    def _update_provider_hint(self) -> None:
        """Show a big pulsing '+ Add provider' button in the middle pane
        whenever the app is running without a real provider and the wizard is
        closed. It brings the wizard back and disappears the moment a provider
        is added."""
        # Show the hint until a REAL provider is added: explore mode
        # (OfflineClient) and the demo (DemoClient) both still want it, so the
        # user can graduate from trying channels to entering their own. A real
        # Xtream/M3U client removes it for good. M3UClient also subclasses
        # OfflineClient, so key on the exact types. Never float it over a
        # maximized/fullscreen video.
        no_provider = type(self.client) in (OfflineClient, DemoClient)
        overlay_up = self._welcome is not None and self._welcome.isVisible()
        if no_provider and not overlay_up and not self._player_fs:
            if self._add_provider_btn is None:
                self._build_provider_hint()
            self._add_provider_btn.setText(tr("onb_add_provider"))
            self._add_provider_btn.show()
            self._add_provider_btn.raise_()
            self._position_provider_hint()
            self._add_provider_anim.start()
        elif self._add_provider_btn is not None:
            self._add_provider_anim.stop()
            self._add_provider_btn.hide()

    def _build_provider_hint(self) -> None:
        btn = QPushButton(tr("onb_add_provider"), self)
        btn.setMinimumHeight(46)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # Always red, independent of the app theme, so it clearly reads as
        # a call to action.
        btn.setStyleSheet(
            "QPushButton { background:#e5354b; color:#ffffff; font-weight:700;"
            " font-size:14px; border:none; border-radius:10px; padding:0 22px; }"
            "QPushButton:hover { background:#c8283b; }")
        btn.clicked.connect(self.show_welcome)
        # Slow opacity pulse to draw the eye without being noisy.
        eff = QGraphicsOpacityEffect(btn)
        btn.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", self)
        anim.setDuration(1500)
        anim.setStartValue(1.0)
        anim.setKeyValueAt(0.5, 0.45)
        anim.setEndValue(1.0)
        anim.setLoopCount(-1)
        self._add_provider_btn = btn
        self._add_provider_anim = anim

    def _position_provider_hint(self) -> None:
        btn = self._add_provider_btn
        if btn is None or not btn.isVisible():
            return
        # Centre it at the bottom of the *visible* central area: the central
        # stack is always laid out and always visible, so the button stays
        # centred whatever page the middle pane shows.
        # A fixed margin above the bottom edge (not a percentage) keeps it
        # planted as the window is resized or the columns are dragged.
        anchor = getattr(self, "_center_stack", None) or self.listw
        btn.adjustSize()
        w = max(240, btn.width() + 40)
        h = 46
        margin = 22
        tl = anchor.mapTo(self, anchor.rect().topLeft())
        x = tl.x() + (anchor.width() - w) // 2
        y = tl.y() + anchor.height() - h - margin
        btn.setGeometry(x, y, w, h)
