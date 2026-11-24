"""The first-run guide, the demo and "no source yet", in the window.

The guide (ui/welcome.py) asks; the window does the work here:

* Connect: the source the form describes tried off the window's thread
  (providers/connect_check.py: logged in, its lists read and counted) under
  an id of its own, and kept only once it worked - then used at once with
  the client that logged in (main_window._use_source: no second login).
  One try at a time (a second Connect while one runs is nothing); Back, Esc
  or closing the guide drop it, and what it left in the cache (its lists, a
  playlist's id map) goes with it. Never a password in a log.
* The demo (providers/demo.py, main_window.start_demo): its stores in
  memory (core/stores.PassingSettings), what it plays kept nowhere
  (core/privacy.py) - a source of one's own taking its place leaves nothing
  of it behind (_leave_demo), and what plays from it stops.
* Just my files and radio: no source; Local files, with its Add folder, and
  Radio. Remembered, so the guide does not come back at every start.
* No source of one's own (explore, the demo): a calm strip under the list
  says so with "Add your own source" - in the pane's layout, never over the
  list - and the guide opens on the choice of what to watch; in the demo it
  also offers to leave it (back to no source). It can be put away for the
  session (its ✕), and on a narrow pane its buttons go under its words.
* Who is watching first: while "Who's watching?" is up, or a PIN is still
  to be given, the guide waits hidden - its keys, its Esc and its actions
  come after the profile is picked (_welcome_hold, _welcome_after_pick).
* A kids profile adds no source, brings no settings and turns no Nerd on
  without a grown-up's PIN (mw_profiles._kids_settings_unlock): the strip's
  button, Connect, the Bring step and the Nerd link ask it (the guide's
  *guard*); the Nerd link is not offered to it at all.

The welcome's Bring step (settings from another computer) is done here
with what Settings does: the sync code joined as Settings > Sync joins it
(mw_sync: _sync_join, its round on the sync thread), a backup picked and
staged as Settings' restore does it (dialogs.pick_backup, stage_backup).
The join takes favorites and settings, and the profiles with them; the
other computer's sources and watch history come along only where that
computer syncs them - this one then syncs them too, as it would with their
switches turned on in Settings. A miss before anything came leaves sync
off again here. A copy last saved more than a month ago is asked about
first, as in Settings (mw_sync's stale question): No leaves sync off and
the step says why. Nothing of it opens a stream, and the code is never
logged.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QBoxLayout, QFrame, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget,
)

from ..core import privacy
from ..core.log import log
from ..core.stores import PassingSettings
from ..core.workers import on_gui_thread, run_async
from ..i18n import tr
from ..providers.client import OfflineClient
from .theme import P
from .welcome import PAGE_CHOOSE, PAGE_WELCOME, WelcomeOverlay

#: What the other computer may sync that this one does not by default.
_FOLLOWED = ("sources", "history")
#: The welcome's words for a sync that did not finish (mw_sync's kinds).
_SYNC_MISS = {"net": "onb_sync_err_net", "none": "onb_sync_err_none",
              "read": "sync_err_read", "newer": "sync_err_newer", "stale": "sync_err_stale"}
#: What plays from no source at all: kept as ever while the demo is in use.
_OWN_KINDS = frozenset({"local", "radio", "web", "recording"})
#: The places no source fills: the strip leaves them be.
_OWN_PLACES = frozenset({"local", "radio", "rec"})
#: Chosen at the guide: no source, files and radio (the guide stays away).
FILES_ONLY_KEY = "welcome_files_only"


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


def drop_source_files(pid: str) -> None:
    """What a source tried and never kept left in the cache: its guide and
    its index, its lists, a playlist's id map (core/m3u_ids.py)."""
    from ..core.source_files import file_part
    from ..providers.epg import _epg_cache_dir
    try:
        d = _epg_cache_dir()
        part = file_part(pid)
    except Exception:
        return
    for name in (f"epg_{part}.xml", f"epg_{part}.xml.idx", f"lists_{part}.json",
                 f"m3u_ids_{part}.json"):
        try:
            os.remove(d / name)
        except OSError:
            pass
    shutil.rmtree(d / f"lists_{part}.d", ignore_errors=True)


class _OnboardingMixin:
    """MainWindow mixin: the first-run guide, the demo, and the strip that
    says there is no source of one's own yet."""

    #: The Bring step's sync: waiting for how it went; something came
    #: already (a miss after that leaves sync on); the source ids before.
    _wsync_pending = False
    _wsync_joined = False
    _wsync_hooked = False
    _wsync_before: frozenset[str] = frozenset()
    #: The connect step's try under way: its row, client and stop flag.
    _wtry: dict | None = None
    #: The demo in use: what its stores write to (in memory).
    _demo_kept: PassingSettings | None = None
    _source_strip: QWidget | None = None
    #: The guide waiting for "Who's watching?": the page it is to open on
    #: (show_welcome asked meanwhile), or True when it was up and hidden
    #: (_welcome_hold) - None when nothing waits.
    _welcome_waits: Any = None
    #: The strip put away for the session, and for which state ("demo",
    #: "none"): it comes back for the other.
    _strip_away: str = ""

    def _welcome_blocked(self) -> bool:
        """Who is watching is still to be said: the picker is up, or a PIN
        waits (mw_profiles) - the guide comes after."""
        return (getattr(self, "_profile_picker", None) is not None
                or bool(getattr(self, "_profile_must_pick", False)))

    def _welcome_hold(self) -> None:
        """(mw_profiles) "Who's watching?" is shown: the guide up now is
        hidden as it is, to come back once a profile is picked."""
        ov = self._wizard_view()
        if ov is not None and ov.isVisible():
            ov.hide()
            self._welcome_waits = True

    def _welcome_after_pick(self) -> None:
        """(mw_profiles) The picker gone: the guide that waited for it -
        as it was, or opened on the page asked for meanwhile."""
        if sip.isdeleted(self) or self._welcome_blocked():
            return
        waits, self._welcome_waits = self._welcome_waits, None
        if waits is None:
            return
        ov = self._wizard_view()
        if waits is True and ov is not None:
            ov.cover()
            self._update_provider_hint()
            return
        self.show_welcome(PAGE_WELCOME if waits is True else int(waits))

    def _welcome_may_change(self) -> bool:
        """The guide's guard: before a source is tried, settings are brought
        or Nerd is switched - a kids profile's grown-up PIN
        (mw_profiles._kids_settings_unlock); anyone else goes on."""
        unlock = getattr(self, "_kids_settings_unlock", None)
        return bool(unlock()) if callable(unlock) else True

    def _welcome_kids(self) -> bool:
        kids = getattr(self, "_profile_kids", None)
        return bool(kids()) if callable(kids) else False

    def show_welcome(self, page: int = PAGE_WELCOME) -> None:
        """Show the guide - from its first page, or *page* (the choice of
        what to watch, from "Add your own source"). Not while who is
        watching is still to be said: it waits for the profile."""
        if self._welcome_blocked():
            if self._welcome_waits is not True:
                self._welcome_waits = page
            return
        if self._welcome is None:
            self._welcome = WelcomeOverlay(
                self, settings=self.settings,
                on_connect=self._wizard_connect,
                on_explore=self._wizard_explore,
                on_language_change=self._wizard_language,
                on_demo=self.start_demo,
                on_files=self._wizard_files,
                guard=self._welcome_may_change,
                kids=self._welcome_kids)
            self._welcome.sync_asked.connect(self._wizard_sync)
            self._welcome.sync_cancelled.connect(self._wizard_sync_cancel)
            self._welcome.restore_asked.connect(self._wizard_restore)
            self._welcome.quit_asked.connect(self._wizard_quit)
            self._welcome.connect_cancelled.connect(self._wizard_connect_cancel)
            watch = getattr(self, "_notices_watch", None)
            if watch is not None:
                watch(self._welcome)
            if page != PAGE_WELCOME:
                self._welcome.reset(page)
        else:
            self._welcome.reset(page)
        self._welcome.cover()
        follow = getattr(self, "_notice_follow", None)
        if follow is not None:
            follow.poke()           # the notices held while it covers the window
        self._update_provider_hint()

    def _wizard_language(self, code: str) -> None:
        # The same switch as Settings makes: Arabic, Persian, Hebrew and
        # Urdu mirror the layout right away, and Qt's own buttons follow.
        self._set_language(code)

    # -- the connect step ---------------------------------------------------------

    def _wizard_connect(self, server: str, user: str, pw: str,
                        kind: str = "xtream", name: str = "", mac: str = "",
                        signed: dict | None = None, extra: dict | None = None) -> None:
        """The form's source tried, off the window's thread; kept once it
        worked. One try at a time."""
        if self._wtry is not None:
            return
        from ..core import offline
        if offline.stream_refusal(server):
            # Offline mode (no network, or turned on): nothing tried - said
            # plainly at once, never a wait (mw_offline).
            log.info("welcome: no try - offline mode is on")
            ov = self._wizard_view()
            if ov is not None:
                ov.connect_failed(tr("onb_err_no_network"))
            return
        # The name typed; else the server's host, or a playlist file's
        # name - never a login the address carries (core/names.py).
        from ..core.names import source_name
        name = name.strip() or source_name(server) or tr("playlist_default_name")
        store = self.playlist_store
        taken = {str(p.get("id")) for p in (store.playlists() if store is not None else [])}
        pid = uuid.uuid4().hex[:8]
        while pid in taken:
            pid = uuid.uuid4().hex[:8]
        row: dict[str, Any] = {"id": pid, "name": name, "kind": kind, "server": server,
                               "username": user, "password": pw,
                               "epg_url": str((extra or {}).get("epg_url") or ""),
                               "refresh": "never", "mac": mac}
        options = dict((extra or {}).get("options") or {})
        if signed:
            # A media server: the token its sign-in left, and its ids.
            row["token"] = str(signed.get("token") or "")
            options.update(signed.get("options") or {})
        if options:
            row["options"] = options
        ov = self._wizard_view()
        try:
            from ..providers.client import make_client
            client = make_client(row)
        except Exception as e:
            log.info("welcome: the source could not be set up - %s", type(e).__name__)
            from ..providers.connect_check import reason
            if ov is not None:
                ov.connect_failed(reason(e, kind))
            return
        stop = threading.Event()
        attempt = {"row": row, "client": client, "stop": stop}
        self._wtry = attempt
        log.info("welcome: trying a %s source", kind)

        def say(step: str) -> None:
            on_gui_thread(self._wizard_step, attempt, step)

        def work():
            from ..providers.connect_check import check
            return check(client, say, stop.is_set, kind)

        run_async(self.pool, work, lambda got: self._wizard_tried(attempt, got),
                  lambda msg: self._wizard_tried(attempt, None))

    def _wizard_step(self, attempt: dict, step: str) -> None:
        ov = self._wizard_view()
        if attempt is self._wtry and ov is not None:
            ov.connect_step(step)

    def _wizard_tried(self, attempt: dict, got: Any) -> None:
        """How the try went: kept and used, or why not - unless it was
        dropped meanwhile (its cache files go then)."""
        row = attempt["row"]
        if attempt is not self._wtry or attempt["stop"].is_set():
            drop_source_files(str(row["id"]))
            return
        self._wtry = None
        ov = self._wizard_view()
        if got is None or not got.ok:
            drop_source_files(str(row["id"]))
            from ..providers.connect_check import reason
            why = got.reason if got is not None else reason(RuntimeError(), row.get("kind", ""))
            log.info("welcome: the source did not work")
            if ov is not None:
                ov.connect_failed(why)
            return
        store = self.playlist_store
        pl = store.add(row)
        store.set_active(pl["id"])
        log.info("welcome: a %s source added - %d channels", row.get("kind"), got.channels)
        if ov is not None:
            ov.connect_done(got.channels, got.movies, got.series, got.warning)
        self.settings.remove(FILES_ONLY_KEY)
        self._use_source(pl["id"], attempt["client"], got.auth)

    def _wizard_connect_cancel(self) -> None:
        """Back or Esc while it tried: nothing of it is kept."""
        attempt, self._wtry = self._wtry, None
        if attempt is not None:
            attempt["stop"].set()
            log.info("welcome: the try was stopped")

    def _wizard_explore(self) -> None:
        # The strip under the list says there is no source yet, and how to
        # add one: nothing more is said here.
        self._wizard_connect_cancel()
        self._update_provider_hint()

    def _wizard_files(self) -> None:
        """Just my files and radio: no source - the demo left if it was in
        use - Local files, where its Add folder is; the guide is not shown
        again at start for it."""
        self._wizard_connect_cancel()
        if self._demo_kept is not None:
            self._demo_to_nothing()
        self.settings.setValue(FILES_ONLY_KEY, "true")
        self._show_place("local")
        self._update_provider_hint()

    # -- the demo -------------------------------------------------------------------

    def _passing_settings(self) -> PassingSettings | None:
        """What the stores write to while the demo is the source - in
        memory - else None (the settings, as ever)."""
        return self._demo_kept

    def _demo_stores(self) -> None:
        """(start_demo) The demo's stores, in memory."""
        from ..core.series_rules import SeriesRuleStore
        from ..core.stores import (
            CategoryOverrides, ChannelOverrides, WatchedStore, WatchlistStore,
        )
        self._demo_kept = kept = PassingSettings()
        privacy.keep_nothing(True, _OWN_KINDS)
        self._build_source_stores(None)
        self._watch_favorites()
        self.series_rules = SeriesRuleStore(kept, None)
        self.overrides = CategoryOverrides(kept, "category_overrides")
        self.channel_ov = ChannelOverrides(kept, "channel_overrides")
        self.watched = WatchedStore(kept)
        self.watchlist = WatchlistStore(kept)
        log.info("demo: started - nothing of it is kept")

    def _leave_demo(self) -> None:
        """A source taking the demo's place (start_demo, _use_source - which
        reads the source's own stores after this): what plays from the demo
        stops, and its stores go - the profile's own are read again."""
        if self._demo_kept is None:
            return
        p = getattr(self, "player", None)
        lp = getattr(self, "_last_playback", None) or {}
        if p is not None and lp.get("kind") not in _OWN_KINDS and (
                getattr(p, "current_url", None) or getattr(self, "_playing_group", None)):
            p.stop_btn.click()
        if lp.get("kind") not in _OWN_KINDS:
            self._last_playback = None
        self._prev_live_item = None
        self._demo_kept = None
        privacy.keep_nothing(False)
        # A film or channel of the demo's left in the column under the video.
        show = getattr(self, "_show_detail", None)
        if callable(show):
            show(None)
        from ..core.stores import WatchedStore, WatchlistStore
        cache = self._own(self._cache_settings)
        self.watched = WatchedStore(cache)
        self.watchlist = WatchlistStore(cache)
        self._build_source_stores(None)
        log.info("demo: left - nothing of it kept")

    def _demo_to_nothing(self) -> None:
        """Leave the demo for no source at all (the strip's Leave the demo,
        Just my files and radio): as at a first start - the empty client,
        no guide of its own, the window's lists read again."""
        if self._demo_kept is None:
            return
        store = getattr(self, "playlist_store", None)
        pid = getattr(store, "active_id", None) if store is not None else None
        if pid and store.get(pid):
            # A source of one's own is there (added while the demo ran, its
            # switch not through): that one, not none.
            self.switch_playlist(pid)
            return
        self._leave_demo()
        from ..providers.epg import XmltvGuide
        self.client = OfflineClient()
        self._base_title = self._no_source_title()
        self.setWindowTitle(self._base_title)
        self._last_cat = {}
        self._recent_cache = {}
        self._full_catalog = None
        self._search_index_cache = {}
        self.xmltv = XmltvGuide(self.client, None, cache_path=None)
        for name in ("_guide_source_changed", "_sp_source_changed", "_pf_source_changed",
                     "_place_series_source_changed", "_popular_changed", "_update_playlist_btn"):
            fn = getattr(self, name, None)
            if callable(fn):
                fn()
        self._refresh_places("source")
        self._refresh_mode_availability()
        if getattr(self, "mode", "live") not in _OWN_PLACES:
            self.switch_mode("live")
        self._update_provider_hint()

    def _no_source_title(self) -> str:
        from .. import APP_NAME
        return APP_NAME

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
                # How old what came is (mw_sync says so too, once it is in).
                saved = int(getattr(self, "_sync_joined_saved", 0) or 0)
                from .qt_i18n import format_when
                ov.sync_no_sources(tr("sync_joined_saved", when=format_when(saved, "d MMM yyyy HH:mm"))
                                   if saved else "")
            # The profiles that came: "Who's watching?" as at start - the
            # guide waits behind it, on the choice of what to watch.
            self._maybe_pick_profile_at_start()
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

    # -- "no source of one's own yet" ----------------------------------------------

    def _no_own_source(self) -> bool:
        """Explore (no source at all) or the demo."""
        return type(self.client) is OfflineClient or self._demo_kept is not None

    def _own_source_id(self) -> str | None:
        """The source in use's id - None while there is no source of one's
        own (explore, the demo): what is kept per source (stream health,
        own channel numbers, the avatar) is never the demo's under a stored
        source's id."""
        if self._demo_kept is not None:
            return None
        store = getattr(self, "playlist_store", None)
        return getattr(store, "active_id", None) if store is not None else None

    def _build_source_strip(self, lay) -> None:
        """(mw_build) The strip under the middle pane's pages that says
        there is no source of one's own - the demo's word, or none at all -
        with "Add your own source" (and in the demo, Leave the demo): in the
        pane's layout, so it never covers a row."""
        strip = _SourceStrip()
        self._source_strip_text = strip.text
        self._source_strip_btn = strip.add
        self._source_strip_leave = strip.leave
        strip.add.clicked.connect(self._strip_add_source)
        strip.leave.clicked.connect(self._demo_to_nothing)
        strip.away.clicked.connect(self._strip_put_away)
        strip.hide()
        lay.addWidget(strip)
        self._source_strip = strip

    def _strip_add_source(self) -> None:
        """Add your own source: the guide on the choice of what to watch - a
        kids profile only with a grown-up's PIN."""
        if not self._welcome_may_change():
            return
        self.show_welcome(PAGE_CHOOSE)

    def _strip_put_away(self) -> None:
        """Its ✕: away for the session, while this state lasts."""
        self._strip_away = "demo" if self._demo_kept is not None else "none"
        self._update_provider_hint()

    def _update_provider_hint(self) -> None:
        """The strip under the list while there is no source of one's own
        and the guide is closed - on the places a source fills, never over a
        video filling the window."""
        strip = self._source_strip
        if strip is None or sip.isdeleted(strip):
            return
        overlay_up = self._welcome is not None and self._welcome.isVisible()
        state = "demo" if self._demo_kept is not None else "none"
        on = (self._no_own_source() and not overlay_up and not getattr(self, "_player_fs", False)
              and getattr(self, "mode", "live") not in _OWN_PLACES
              and self._strip_away != state)
        if on:
            strip.say(state == "demo")
        strip.setVisible(on)


class _SourceStrip(QWidget):
    """The calm strip that says there is no source of one's own: its words,
    Add your own source, in the demo Leave the demo, and a ✕ that puts it
    away for the session. On a narrow pane (NARROW) its buttons go under its
    words, so the words take two lines, not five - and one under the other
    where they do not fit side by side (a long translation)."""

    NARROW = 560
    #: The frame's margins and the room between its parts.
    _MARGINS = (12, 8, 6, 8)
    _GAP = 10
    _BUTTON_GAP = 6

    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 10)
        frame = QFrame(objectName="SourceStrip")
        frame.setStyleSheet(
            f"#SourceStrip {{ background: {P['input']}; border: 1px solid {P['border']};"
            " border-radius: 10px; }"
            f"#SourceStripText {{ color: {P['text2']}; font-size: 12px; }}"
            f"QPushButton#SourceStripLeave {{ background: transparent; border: none;"
            f" color: {P['muted']}; font-size: 12px; padding: 4px 6px; }}"
            f"QPushButton#SourceStripLeave:hover, QPushButton#SourceStripLeave:focus"
            f" {{ color: {P['text']}; text-decoration: underline; }}"
            f"QToolButton#SourceStripAway {{ background: transparent; border: none;"
            f" color: {P['muted']}; padding: 2px; }}"
            f"QToolButton#SourceStripAway:hover, QToolButton#SourceStripAway:focus"
            f" {{ color: {P['text']}; }}")
        row = QHBoxLayout(frame)
        row.setContentsMargins(*self._MARGINS)
        row.setSpacing(self._BUTTON_GAP)
        # The words and the buttons: side by side, or the buttons under the
        # words on a narrow pane (_fit).
        self._box = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._box.setSpacing(self._GAP)
        self.text = QLabel("", objectName="SourceStripText")
        self.text.setWordWrap(True)
        self.text.setTextFormat(Qt.TextFormat.PlainText)
        self._box.addWidget(self.text, 1)
        # Add your own source and Leave the demo: in a row, or Add your own
        # source over Leave the demo where the row does not fit.
        self._buttons = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._buttons.setSpacing(self._BUTTON_GAP)
        self.leave = QPushButton(objectName="SourceStripLeave")
        self.leave.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add = QPushButton(objectName="Primary")
        self.add.setCursor(Qt.CursorShape.PointingHandCursor)
        self._buttons.addStretch(1)
        self._buttons.addWidget(self.leave)
        self._buttons.addWidget(self.add)
        self._box.addLayout(self._buttons)
        row.addLayout(self._box, 1)
        # The ✕ beside it all, at the words' first line on a narrow pane.
        self.away = QToolButton(objectName="SourceStripAway")
        self.away.setText("✕")
        self.away.setAutoRaise(True)
        self.away.setCursor(Qt.CursorShape.PointingHandCursor)
        row.addWidget(self.away, 0, Qt.AlignmentFlag.AlignVCenter)
        self._row = row
        outer.addWidget(frame)
        self.say(False)

    def say(self, demo: bool) -> None:
        self.text.setText(tr("onb_strip_demo") if demo else tr("onb_strip_none"))
        self.add.setText(tr("onb_add_own_source"))
        self.leave.setText(tr("onb_leave_demo"))
        self.leave.setVisible(demo)
        self.away.setToolTip(tr("common_close"))
        self.away.setAccessibleName(tr("common_close"))
        self._fit()

    def _fit(self) -> None:
        width = self.width()
        narrow = 0 < width < self.NARROW
        D = QBoxLayout.Direction
        changed = False
        want = D.TopToBottom if narrow else D.LeftToRight
        if self._box.direction() != want:
            self._box.setDirection(want)
            changed = True
        # The buttons' row as wide as the room under the words: else Add
        # your own source on top, Leave the demo under it.
        left, _top, right, _bottom = self._MARGINS
        room = width - left - right - self._BUTTON_GAP - self.away.sizeHint().width()
        need = self.add.sizeHint().width()
        if not self.leave.isHidden():
            need += self._BUTTON_GAP + self.leave.sizeHint().width()
        stack = narrow and need > room
        want_buttons = D.BottomToTop if stack else D.LeftToRight
        if self._buttons.direction() != want_buttons:
            self._buttons.setDirection(want_buttons)
            changed = True
        top = Qt.AlignmentFlag.AlignTop if narrow else Qt.AlignmentFlag.AlignVCenter
        self.text.setAlignment(top | Qt.AlignmentFlag.AlignLeading)
        self._row.setAlignment(self.away, top)
        if changed:
            # The height asked again for the new form (a word-wrapped label
            # keeps the height of its old width otherwise).
            self._box.invalidate()
            self.updateGeometry()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().resizeEvent(event)
        self._fit()
