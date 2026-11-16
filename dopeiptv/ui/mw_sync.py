"""Settings sync in the window (core/sync.py does the work): when it runs,
what comes back put in place, and its card in Settings, Sync between
computers.

A round runs shortly after the start, half a minute after something that
syncs has changed and stayed so, every half hour, and on Sync now - on a
thread of its own (one at a time), never in the first seconds of a stream.
A miss is tried again later, quietly; Settings says how the last one went.
What another computer changed is written to the stores here; a theme and
shortcuts take effect at once, anything else that needs it with the next
start (a toast says so). What plays is never touched.

For everyone, and off until it is started here or joined with the code
from another computer. The page says plainly what dopeiptv.org gets: bytes
encrypted here, under a random number - nothing about the person.
"""

from __future__ import annotations

import time
from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import QObject, QSettings, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
    QVBoxLayout,
)

from .. import ORG
from ..core import build_check, sync
from ..core.log import log
from ..core.workers import run_async
from ..i18n import tr
from .theme import P

START_DELAY = 20          # s after the window opens
QUIET = 30                # s a change has to rest before it is sent
PERIOD = 30 * 60          # s between two rounds otherwise
PLAY_GRACE = 20           # s after a stream starts without a round
TICK_MS = 15_000
_BACKOFF = (60, 120, 300, 900, 1800)
#: Settings a sync puts in effect at once; the rest wait for the next start.
_LIVE_KEYS = frozenset({"theme", "accent", "accent_custom", "theme_follow_system",
                        "theme_light", "corner_style"})
#: A miss that trying again soon will not mend.
_STUCK = ("refused", "newer", "read", "big", "full")
#: The chips' words: the app's own names for what they sync.
_CHIP_TEXT = {"favorites": "nav_favorites", "settings": "settings_title",
              "sources": "tab_playlists", "history": "sync_cat_history"}


class _SyncSignals(QObject):
    changed = pyqtSignal()


def _work(remote: sync.Remote, key: bytes, doc: dict, snap: sync.Snapshot,
          cats: tuple[str, ...], fresh: bool, joining: bool,
          seen: frozenset[str]) -> object:
    """The round on the sync thread: its result, or the SyncError (which
    the pool's fail signal would turn into a bare string)."""
    try:
        return sync.round_trip(remote, key, doc, snap, cats, fresh=fresh,
                               joining=joining, seen=seen)
    except sync.SyncError as e:
        return e
    except Exception:
        log.warning("sync: round failed", exc_info=True)
        return sync.SyncError("fail")


def _ignore(_result: object = None) -> None:
    """The result of work done for its side effect."""


class _SyncMixin:
    """MainWindow mixin: settings sync between a person's computers."""

    #: Tests point these elsewhere.
    _sync_endpoint: str | None = None
    _sync_state_file: Path | None = None

    def _init_sync(self) -> None:
        self._sync_pool = QThreadPool()
        self._sync_pool.setMaxThreadCount(1)
        self._sync_signals = _SyncSignals()
        self._sync_busy = False
        self._sync_gen = 0
        self._sync_job: tuple | None = None
        self._sync_state: sync.State | None = None
        self._sync_status: tuple[str, int] = ("idle", 0)
        self._sync_fails = 0
        self._sync_next = time.monotonic() + START_DELAY
        self._sync_seen: str | None = None
        self._sync_seen_at = time.monotonic()
        self._sync_sent: str | None = None
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(TICK_MS)
        self._sync_timer.timeout.connect(self._sync_tick)
        self._sync_soon_timer = QTimer(self)
        self._sync_soon_timer.setSingleShot(True)
        self._sync_soon_timer.timeout.connect(self._sync_auto)
        if self._sync_on():
            self._sync_timer.start()

    # -- state ---------------------------------------------------------------------

    def _sync_on(self) -> bool:
        return sync.code_of(self.settings) is not None

    def _sync_own(self, settings):
        """*settings* pinned to the profile in use (mw_profiles), when the
        window keeps profiles."""
        own = getattr(self, "_own", None)
        return own(settings) if own is not None else settings

    def _sync_ctx(self) -> sync.Context:
        """What syncs: every profile's own (core/profiles.py) - the first
        profile's under the names it always had, the others' under their
        ids - and the household's settings and sources."""
        from ..core import profiles
        st, rs = self.settings, getattr(self, "_resume_settings", None)
        store = getattr(self, "playlist_store", None)
        reg = getattr(self, "profiles", None)
        if not isinstance(st, profiles.ProfileSettings) or reg is None:
            return sync.Context(st, rs, store)

        def pin(s, pid):
            return profiles.pinned(s, pid) if s is not None else None

        def views(pid):
            return pin(st, pid), pin(rs, pid)

        ctx = sync.Context(pin(st, profiles.MAIN_ID), pin(rs, profiles.MAIN_ID), store,
                           registry=reg, pin=views)
        ctx.refresh_others()
        return ctx

    def _sync_path(self) -> Path:
        if self._sync_state_file is not None:
            return Path(self._sync_state_file)
        ini = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                        ORG, "sync").fileName()
        return Path(ini).with_name("sync-state.json")

    def _sync_state_for(self, slot: str) -> sync.State:
        st = self._sync_state
        if st is None or st.slot != slot:
            st = self._sync_state = sync.State.load(self._sync_path(), slot)
        return st

    def _sync_set_status(self, kind: str, size: int = 0) -> None:
        self._sync_status = (kind, size)
        try:
            self._sync_signals.changed.emit()
        except RuntimeError:
            pass

    def _sync_write_state(self, state: sync.State, wait: bool = False) -> None:
        text, path = state.dump(), state.path
        if wait:
            sync.write_private(path, text)
        else:
            run_async(self._sync_pool, sync.write_private, _ignore, self._sync_write_failed,
                      path, text)

    @staticmethod
    def _sync_write_failed(msg: str) -> None:
        log.warning("sync: this computer's copy not written: %s", msg)

    # -- when ----------------------------------------------------------------------

    def _sync_tick(self) -> None:
        """Every few seconds while sync is on: is a round due?"""
        if not self._sync_on():
            self._sync_timer.stop()
            return
        if self._sync_busy:
            return
        now = time.monotonic()
        try:
            seen = sync.digest(self._sync_ctx(), sync.chosen(self.settings))
        except Exception:
            log.debug("sync: digest", exc_info=True)
            seen = None
        if seen != self._sync_seen:
            self._sync_seen, self._sync_seen_at = seen, now
        changed = seen is not None and seen != self._sync_sent \
            and now - self._sync_seen_at >= QUIET
        if changed or now >= self._sync_next:
            self.sync_now()

    def _sync_auto(self) -> None:
        self.sync_now()

    def _sync_soon(self, secs: float = 3.0) -> None:
        self._sync_soon_timer.start(int(secs * 1000))

    def _sync_mark_play(self) -> None:
        """A stream starts: no round for its first seconds."""
        self._sync_play_started = time.monotonic()

    def sync_now(self, manual: bool = False) -> bool:
        """Start a round (on the sync thread). False when none starts: one
        runs already, sync is off, or (unless *manual*) a stream has only
        just started."""
        if self._sync_busy or not self._sync_on():
            return False
        if not manual and build_check.settled() is None:
            # The build's verdict is still being worked out: never waited
            # for on the window's thread - the next tick asks again.
            build_check.start()
            return False
        if not build_check.allowed():
            # A modified build asks nothing of dopeiptv.org: said in Settings.
            self._sync_set_status("modified")
            return False
        now = time.monotonic()
        if not manual and now - getattr(self, "_sync_play_started", -1e9) < PLAY_GRACE:
            self._sync_next = max(self._sync_next, now + PLAY_GRACE)
            return False
        raw = sync.code_of(self.settings)
        if raw is None:
            return False
        slot, key = sync.derive(raw)
        state = self._sync_state_for(slot)
        cats = sync.chosen(self.settings)
        ctx = self._sync_ctx()
        try:
            snap = sync.collect(ctx, cats)
            self._sync_sent = sync.digest(ctx, cats)
        except Exception:
            log.warning("sync: what syncs could not be read", exc_info=True)
            self._sync_set_status("fail")
            self._sync_next = now + PERIOD
            return False
        remote = sync.Remote(slot, self._sync_endpoint)
        self._sync_job = (self._sync_gen, snap, cats, state)
        self._sync_busy = True
        self._sync_set_status("running")
        run_async(self._sync_pool, _work, self._sync_done, self._sync_crashed,
                  remote, key, state.doc, snap, cats, state.fresh, state.joining,
                  frozenset(state.seen))
        return True

    # -- what came back ------------------------------------------------------------

    def _sync_crashed(self, msg: str) -> None:
        self._sync_done(sync.SyncError("fail"))

    def _sync_done(self, result: object) -> None:
        job, self._sync_job = self._sync_job, None
        self._sync_busy = False
        if job is None or job[0] != self._sync_gen or not self._sync_on():
            # Stopped, or started again with another code, meanwhile: this
            # one's result is dropped; a new code's first round follows.
            if self._sync_on():
                self._sync_soon(1)
            self._sync_set_status(self._sync_status[0])
            return
        _gen, snap, cats, state = job
        if isinstance(result, sync.SyncError):
            self._sync_failed(result)
            return
        if not isinstance(result, sync.RoundResult):
            self._sync_failed(sync.SyncError("fail"))
            return
        ctx = self._sync_ctx()
        try:
            here = sync.collect(ctx, cats)
            applied, keep = sync.apply(ctx, result, snap, here, cats)
        except Exception:
            log.warning("sync: not put in place", exc_info=True)
            self._sync_failed(sync.SyncError("fail"))
            return
        state.doc, state.version = keep, result.version
        state.fresh = state.joining = False
        state.seen |= set(cats) - applied.skipped
        self._sync_write_state(state)
        self.settings.setValue(sync.K_LAST, int(time.time()))
        self._sync_fails = 0
        now = time.monotonic()
        self._sync_next = now + (5 if applied.skipped else PERIOD)
        try:
            self._sync_sent = self._sync_seen = sync.digest(ctx, cats)
            self._sync_seen_at = now
        except Exception:
            self._sync_sent = None
        log.info("sync: done - version %d, %d bytes%s%s", result.version, result.size,
                 ", sent" if result.uploaded else "",
                 ", changed here" if applied.any() else "")
        self._sync_set_status("ok")
        if applied.any():
            try:
                self._sync_show_applied(applied)
            except Exception:
                log.warning("sync: window not refreshed", exc_info=True)

    def _sync_failed(self, err: sync.SyncError) -> None:
        kind = err.kind
        log.info("sync: not done (%s)", kind)
        if kind == "none":
            # A code with nothing under it: a typo, most likely. Not joined.
            self._sync_forget()
            self._sync_set_status("none")
            return
        self._sync_fails += 1
        wait = PERIOD if kind in _STUCK else _BACKOFF[min(self._sync_fails, len(_BACKOFF)) - 1]
        self._sync_next = time.monotonic() + wait
        self._sync_set_status(kind, err.size)

    def _sync_show_applied(self, applied: sync.Applied) -> None:
        store = getattr(self, "playlist_store", None)
        pid = str(getattr(store, "active_id", "") or "")
        if pid and (pid in applied.favorites or pid in applied.history):
            self._sync_reload_stores(pid, pid in applied.favorites, pid in applied.history)
        if sync.GLOBAL_FP in applied.favorites:
            # Radio stations and web streams, which are no source's: the
            # profile's in use, read again.
            from ..core.stores import FavoriteStore
            own = self._sync_own(self.settings)
            self.radio_favs = FavoriteStore(own, "radio_favorites",
                                            id_key="stationuuid")
            self.web_favs = FavoriteStore(own, "web_favorites", id_key="url")
            playing = getattr(getattr(self, "player", None), "current_url", None)
            if getattr(self, "mode", "") == "fav" and not playing \
                    and hasattr(self, "_load_categories"):
                self._load_categories()
            elif getattr(self, "list_model", None) is not None:
                self.list_model.refresh_all()
        if applied.sources and hasattr(self, "_update_playlist_btn"):
            self._update_playlist_btn()
        if applied.settings & _LIVE_KEYS and hasattr(self, "_apply_theme_now"):
            self._apply_theme_now()
        keys = {k for k in applied.settings if k.startswith("shortcut/")}
        if keys and hasattr(self, "apply_shortcuts"):
            self.apply_shortcuts()
        later = applied.settings - _LIVE_KEYS - keys
        toast = getattr(self, "_show_toast", None)
        if toast is None:
            return
        if later:
            toast(tr("sync_applied_restart"), 6000)
        elif applied.settings or applied.sources:
            toast(tr("sync_applied"), 4000)

    def _sync_reload_stores(self, pid: str, favs: bool, history: bool) -> None:
        """The active playlist's stores read again (sync wrote under them)."""
        from ..core.stores import FavoriteStore, HistoryStore
        own = self._sync_own(self.settings)
        if favs:
            # The profile's in use, pinned to it (core/profiles.py).
            self.favs = FavoriteStore(own, f"favorites_{pid}")
            if hasattr(self, "_favorites_changed"):
                self.favs.on_change = self._favorites_changed
            self.movie_favs = FavoriteStore(own, f"movie_favorites_{pid}",
                                            id_key="stream_id")
            self.series_favs = FavoriteStore(own, f"series_favorites_{pid}",
                                             id_key="series_id")
        if history:
            self.history = HistoryStore(own, f"history_{pid}")
            resume_settings = getattr(self, "_resume_settings", None)
            if resume_settings is not None:
                from ..services.resume import ResumeStore
                self.resume = ResumeStore(self._sync_own(resume_settings), pid)
        playing = getattr(getattr(self, "player", None), "current_url", None)
        if favs and getattr(self, "mode", "") == "fav" and not playing \
                and hasattr(self, "_load_categories"):
            self._load_categories()
        elif getattr(self, "list_model", None) is not None:
            self.list_model.refresh_all()
        home = getattr(self, "_home_page", None)
        if home is not None and hasattr(self, "_home_showing") and self._home_showing():
            home.refresh()

    # -- on, off -------------------------------------------------------------------

    def _sync_begin(self, code: str, joining: bool) -> None:
        raw = sync.parse_code(code)
        if raw is None:
            return
        self._sync_gen += 1
        self.settings.setValue(sync.K_CODE, sync.format_code(raw))
        self.settings.remove(sync.K_LAST)
        if self.settings.value(sync.K_CATS) is None:
            sync.set_chosen(self.settings, sync.DEFAULT_CATS)
        slot, _key = sync.derive(raw)
        sync.forget(self._sync_path())
        state = self._sync_state = sync.State(self._sync_path(), slot)
        state.fresh = state.joining = joining
        if not joining:
            # The first computer: what it has is the start, not a newcomer's.
            state.seen = set(sync.chosen(self.settings))
        self._sync_fails = 0
        self._sync_sent = None
        self._sync_set_status("idle")
        self._sync_timer.start()
        self.sync_now(manual=True)

    def _sync_start_here(self) -> str:
        """Sync on, with a new code (this is the first computer)."""
        code = sync.new_code()
        self._sync_begin(code, joining=False)
        return code

    def _sync_join(self, text: str) -> bool:
        """Sync on with another computer's code; False for no code."""
        if sync.parse_code(text) is None:
            return False
        self._sync_begin(text, joining=True)
        return True

    def _sync_forget(self) -> None:
        self._sync_gen += 1
        self.settings.remove(sync.K_CODE)
        self.settings.remove(sync.K_LAST)
        path = self._sync_path()
        sync.forget(path)
        # Again after a write still queued: it holds the copy just forgotten.
        run_async(self._sync_pool, sync.forget, _ignore, None, path)
        self._sync_state = None
        self._sync_timer.stop()
        self._sync_soon_timer.stop()
        self._sync_set_status("idle")

    def _sync_stop(self, delete_there: bool) -> None:
        """Sync off here; *delete_there*: the site's copy goes too."""
        raw = sync.code_of(self.settings)
        if delete_there and raw is not None:
            slot, _key = sync.derive(raw)
            remote = sync.Remote(slot, self._sync_endpoint)
            run_async(self._sync_pool, remote.delete, _ignore, self._sync_delete_failed)
        self._sync_forget()

    @staticmethod
    def _sync_delete_failed(msg: str) -> None:
        log.info("sync: the copy there was not deleted: %s", msg)

    def _sync_on_close(self) -> None:
        """The window closes: what changed since the last round gets its
        time now (no network), so the next round knows when it was."""
        try:
            self._sync_pool.clear()         # a queued write of an older copy goes
            if self._sync_busy or not self._sync_on():
                return
            self._sync_pool.waitForDone(1000)   # one already writing finishes first
            raw = sync.code_of(self.settings)
            if raw is None:
                return
            state = self._sync_state_for(sync.derive(raw)[0])
            if state.fresh:
                return          # joined, never synced: its first round decides
            # A category not synced here yet waits for its first round,
            # which knows whether the others have it already.
            cats = [c for c in sync.chosen(self.settings) if c in state.seen]
            snap = sync.collect(self._sync_ctx(), cats)
            if sync.stamp(state.doc, snap, cats, int(time.time())):
                self._sync_write_state(state, wait=True)
        except Exception:
            log.debug("sync: nothing kept on close", exc_info=True)

    def _sync_status_text(self) -> tuple[str, str]:
        """(the status line, the line under it: what went wrong)."""
        kind, size = self._sync_status
        if kind == "running":
            line = tr("sync_running")
        else:
            try:
                last = int(self.settings.value(sync.K_LAST, 0) or 0)
            except (TypeError, ValueError):
                last = 0
            if last:
                from .qt_i18n import format_when
                line = tr("sync_last", when=format_when(last))
            else:
                line = tr("sync_never")
        problem = ""
        if kind == "modified" or not build_check.allowed():
            problem = build_check.notice("sync")
        elif kind == "big":
            from ..core.recording import format_size
            problem = tr("sync_err_big", size=format_size(size),
                         limit=format_size(sync.MAX_BLOB))
        elif kind in ("net", "busy", "refused", "read", "newer", "none", "full", "fail"):
            problem = tr(f"sync_err_{kind}")
        return line, problem

    # -- in Settings ---------------------------------------------------------------

    def _build_sync_card(self, page, dlg) -> None:
        # On a page of its own, titled as the card was: no heading twice.
        _SyncCard(self, page.card(), dlg)
        # What dopeiptv.org gets, in plain words, whether sync is on or off.
        page.card(tr("sync_privacy_title")).note(tr("sync_privacy"))


class _SyncCard(QObject):
    """Settings, Sync between computers: the sync card, off or on. Lives as
    long as the dialog."""

    def __init__(self, win, card, dlg) -> None:
        super().__init__(dlg)
        from .settings_ui import Chip, ChipGroups, hbox
        self.win, self.card, self.dlg = win, card, dlg
        self.revealed = False
        self._on = win._sync_on

        start_btn = QPushButton(tr("sync_start"), objectName="Primary")
        join_btn = QPushButton(tr("sync_join"))
        start_btn.clicked.connect(self.start)
        join_btn.clicked.connect(self.join)
        # A modified build: nothing to start - the line under it says why.
        self.official = build_check.allowed()
        start_btn.setEnabled(self.official)
        join_btn.setEnabled(self.official)
        # One over the other: side by side in the column of one control
        # (320 px at most) they cut "Start on this computer" short, and
        # most translations more.
        both = QVBoxLayout()
        both.setSpacing(8)
        both.addWidget(start_btn)
        both.addWidget(join_btn)
        card.shown_if(card.row(tr("sync_title"), both, tr("sync_off_hint"),
                               search="sync computers code encrypted privacy"),
                      self.is_off)

        chosen = sync.chosen(win.settings)
        self.chips = {cat: Chip(tr(_CHIP_TEXT[cat]), cat in chosen)
                      for cat in sync.CATEGORIES}
        for chip in self.chips.values():
            chip.toggled.connect(self.cats_changed)
        groups = ChipGroups([(tr("sync_what"), list(self.chips.values()))])
        card.shown_if(card.widget(groups, " ".join(c.text() for c in self.chips.values())),
                      self._on)
        card.shown_if(card.note(tr("sync_sources_note")).parentWidget(), self._on)

        self.show_btn = QPushButton(tr("sync_show"))
        self.copy_btn = QPushButton(tr("problem_copy"))
        self.show_btn.clicked.connect(self.toggle_reveal)
        self.copy_btn.clicked.connect(self.copy_code)
        self.code_row = card.row(tr("sync_code"), hbox(None, self.show_btn, self.copy_btn),
                                 "…", search="sync code")
        self.code_row.hint_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        card.shown_if(self.code_row, self._on)

        self.now_btn = QPushButton(tr("sync_now"))
        self.now_btn.clicked.connect(self.sync_now)
        self.status_row = card.row(tr("sync_never"), self.now_btn, search="sync now")
        card.shown_if(self.status_row, self._on)

        self.problem = QLabel()
        self.problem.setWordWrap(True)
        self.problem.setStyleSheet(f"color:{P['error']}; font-size:12px;")
        card.shown_if(card.widget(self.problem), self.has_problem)

        stop_btn = QPushButton(tr("btn_stop"))
        stop_btn.clicked.connect(self.stop)
        card.shown_if(card.row(tr("sync_stop"), stop_btn, tr("sync_stop_hint"),
                               search="sync stop off"),
                      self._on)
        win._sync_signals.changed.connect(self.refresh)
        self.refresh()

    # what shows when
    def is_off(self) -> bool:
        return not self._on()

    def has_problem(self) -> bool:
        return bool(self.problem.text())

    def refresh(self) -> None:
        if sip.isdeleted(self.card) or sip.isdeleted(self.problem):
            return
        code = str(self.win.settings.value(sync.K_CODE, "") or "")
        if self.code_row.hint_label is not None:
            self.code_row.hint_label.setText(
                (code if self.revealed else sync.masked(code)) if code else "")
        self.show_btn.setText(tr("sync_hide") if self.revealed else tr("sync_show"))
        line, problem = self.win._sync_status_text()
        self.status_row.label.setText(line)
        self.now_btn.setEnabled(not self.win._sync_busy and self.official)
        self.problem.setText(problem)
        self.card.refresh()

    # what the buttons do
    def start(self) -> None:
        if self.win._sync_start_here():
            self.revealed = True
        self.refresh()

    def join(self) -> None:
        text, ok = QInputDialog.getText(self.dlg, tr("sync_join_title"),
                                        tr("sync_join_prompt"), QLineEdit.EchoMode.Normal)
        if not ok or not text.strip():
            return
        if not self.win._sync_join(text):
            QMessageBox.warning(self.dlg, tr("sync_join_title"), tr("sync_code_bad"))
        self.refresh()

    def cats_changed(self, _on: bool = False) -> None:
        sync.set_chosen(self.win.settings,
                        [cat for cat, chip in self.chips.items() if chip.isChecked()])
        if self.win._sync_on():
            self.win._sync_soon()

    def toggle_reveal(self) -> None:
        self.revealed = not self.revealed
        self.refresh()

    def copy_code(self) -> None:
        code = str(self.win.settings.value(sync.K_CODE, "") or "")
        if not code:
            return
        QApplication.clipboard().setText(code)
        self.copy_btn.setText(tr("problem_copied"))
        QTimer.singleShot(1500, self.copied_done)

    def copied_done(self) -> None:
        if not sip.isdeleted(self.copy_btn):
            self.copy_btn.setText(tr("problem_copy"))

    def sync_now(self) -> None:
        self.win.sync_now(manual=True)
        self.refresh()

    def stop(self) -> None:
        from .widgets import confirm
        if not confirm(self.dlg, tr("sync_title"), tr("sync_stop_confirm"),
                       default_yes=False):
            return
        delete = confirm(self.dlg, tr("sync_title"), tr("sync_delete_ask"),
                         default_yes=False)
        self.win._sync_stop(delete)
        self.revealed = False
        self.refresh()
