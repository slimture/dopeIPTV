"""An answer that comes back after a profile switch (the phone remote, the
remote API or Home Assistant switch the profile at any moment, even while a
dialog is open): it goes to the profile that asked - a watched mark whose
TMDB id was looked up, a Trakt or Simkl sign-in finishing, a watched sync -
and the profile switched to is neither written into nor held back by it."""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_APP = None


def _app():
    from PyQt6.QtWidgets import QApplication
    global _APP
    _APP = QApplication.instance() or QApplication([])   # kept alive
    return _APP


class _Inline:
    """threading.Thread, run where it is started."""

    def __init__(self, target, daemon=False):
        self.target = target

    def start(self):
        self.target()


class _Watched:
    def __init__(self):
        self.marks, self.shows, self.synced, self.replaced = [], [], [], []
        self.last_sync_at = 0

    def mark_movie_local(self, tid):
        self.marks.append(tid)

    def mark_movie_local_by_stream(self, sid):
        pass

    def mark_series_local_by_stream(self, sid):
        pass

    def mark_show_local(self, tid):
        self.shows.append(tid)

    def mark_show_synced(self, tid):
        self.synced.append(tid)

    def add_local_item(self, item, kind, tid):
        pass

    def replace(self, movies, shows, movie_titles, show_titles):
        self.replaced.append(sorted(movies))


class _Account:
    def __init__(self, movies=()):
        self.pushed, self.movies = [], list(movies)

    def is_connected(self):
        return True

    def add_movie_history(self, tid):
        self.pushed.append(("movie", tid))

    def add_show_history(self, tid):
        self.pushed.append(("show", tid))

    def watched_movies(self, titles):
        return list(self.movies)

    def watched_shows(self, titles):
        return {}

    def watchlist_movies(self):
        return []

    def watchlist_shows(self):
        return []


def test_a_watched_mark_answered_after_a_switch_goes_to_the_profile_that_marked_it(monkeypatch):
    from dopeiptv.ui import mw_trakt
    from dopeiptv.ui.mw_trakt import _TraktMixin
    monkeypatch.setattr(mw_trakt.threading, "Thread", _Inline)
    asked = []

    class W(_TraktMixin):
        def __init__(self, watched, account):
            self.watched, self.trakt, self.simkl = watched, account, None
            self.tmdb = SimpleNamespace(tmdb_id_for=lambda *_a: None)
            self.list_model = SimpleNamespace(refresh_all=lambda: None)
            self.series_ctx = None

        def _ms_mark(self, *_a):
            pass

        def _resolve_tmdb_id_async(self, item, kind, on_id):
            asked.append(on_id)             # the answer comes later

        def _error(self, msg):
            raise AssertionError(msg)

    mine, theirs = _Watched(), _Watched()
    me, lisa = _Account(), _Account()
    w = W(mine, me)
    w._mark_movie_watched({"name": "Film", "stream_id": 7}, push_to_trakt=True)
    w._mark_series_watched({"name": "Show", "series_id": 8}, push_to_trakt=True)
    # The profile is switched before TMDB answers.
    w.watched, w.trakt = theirs, lisa
    asked[0](42)
    asked[1](99)
    assert mine.marks == [42] and mine.shows == [99] and mine.synced == [99]
    assert me.pushed == [("movie", 42), ("show", 99)]
    assert theirs.marks == theirs.shows == [] and lisa.pushed == []


def test_a_trakt_sign_in_finished_after_a_switch_is_the_profile_that_signed_in(monkeypatch):
    _app()
    from dopeiptv.ui import mw_trakt
    from dopeiptv.ui.mw_trakt import _TraktMixin
    pending = []
    monkeypatch.setattr(mw_trakt, "run_async",
                        lambda pool, fn, ok, err=None, *a: pending.append((fn, ok)))
    monkeypatch.setattr(mw_trakt, "open_url", lambda url: None)

    class Trakt:
        client_id = "cid"

        def __init__(self):
            self.codes, self.state = [], None

        def authorize_url(self, state, challenge):
            self.state = state
            return "https://trakt.invalid/authorize"

        def exchange_code(self, code, verifier):
            self.codes.append(code)
            return {}

    me, lisa = Trakt(), Trakt()
    synced = []

    class W(_TraktMixin):
        _profile_gen = 0

        def __init__(self):
            self.trakt, self.pool = me, None

        def _maybe_sync_watched(self, force=False):
            synced.append(self.trakt)

    w = W()

    def exec_(dlg):
        # The phone switches the profile while the browser is open.
        w.trakt, w._profile_gen = lisa, 1
        _fn, captured = pending.pop(0)
        captured({"code": "THECODE", "state": me.state})
        fn, exchanged = pending.pop(0)
        exchanged(fn())
        return 0

    monkeypatch.setattr(mw_trakt.QDialog, "exec", exec_)
    w._trakt_browser_auth_dialog(None)
    assert me.codes == ["THECODE"] and lisa.codes == []
    assert synced == [], "the profile switched to is not synced with this account"


def test_a_simkl_sign_in_finished_after_a_switch_syncs_no_one_else(monkeypatch):
    from dopeiptv.ui import mw_simkl
    from dopeiptv.ui.mw_simkl import _SimklMixin

    class Account:
        def __init__(self, connected):
            self.connected = connected

        def is_connected(self):
            return self.connected

    class W(_SimklMixin):
        _profile_gen = 0

        def __init__(self):
            self.simkl = Account(False)

    w = W()
    switch = {"on": True}

    class Dialog:
        def __init__(self, parent, win):
            self.simkl = win.simkl          # the account it signs in

        def exec(self):
            self.simkl.connected = True
            if switch["on"]:
                # Switched to a profile signed in to its own Simkl account.
                w.simkl, w._profile_gen = Account(True), w._profile_gen + 1
            return 1

    monkeypatch.setattr(mw_simkl, "_SimklPinDialog", Dialog)
    assert w._simkl_connect_dialog(None) is False
    switch["on"] = False
    w.simkl = Account(False)
    assert w._simkl_connect_dialog(None) is True


def test_a_watched_sync_running_at_a_switch_does_not_hold_the_next_profile_back(monkeypatch):
    _app()
    from dopeiptv.core import profiles
    from dopeiptv.ui import mw_trakt
    from dopeiptv.ui.mw_profiles import _ProfilesMixin
    from dopeiptv.ui.mw_trakt import _TraktMixin
    runs = []

    class Hold:
        def __init__(self, target, daemon=False):
            self.target = target

        def start(self):
            runs.append(self.target)        # run when the test says

    monkeypatch.setattr(mw_trakt.threading, "Thread", Hold)
    monkeypatch.setattr(mw_trakt.QTimer, "singleShot", staticmethod(lambda _ms, fn: fn()))
    watched = {"a": _Watched(), "b": _Watched()}
    accounts = {"a": _Account([1]), "b": _Account([2])}
    refreshed = []

    class Store:
        def get(self, pid):
            return SimpleNamespace(id=pid, name=pid.upper()) if pid in ("a", "b") else None

        def set_active(self, pid):
            pass

    class W(_ProfilesMixin, _TraktMixin):
        _watched_sync_running = False
        mode = "live"

        def __init__(self):
            self.profiles, self.playlist_store = Store(), None
            self.parental = SimpleNamespace(lock_session=lambda: None)
            self.list_model = SimpleNamespace(
                refresh_all=lambda: refreshed.append(profiles.active_id()))
            self._build_profile_accounts()

        def _build_profile_accounts(self):
            pid = profiles.active_id()
            self.trakt, self.simkl = accounts[pid], None
            self.watched, self.watchlist = watched[pid], SimpleNamespace(replace=lambda *_a: None)
            self.movie_favs = self.series_favs = None

        def _profiles_on(self):
            return True

        def _push_local_watched_to_trakt(self, mine=None):
            pass

        def _profile_stop_all(self):
            pass

        def _stats_save(self):
            pass

        def _build_source_stores(self, pid):
            pass

        def _watch_favorites(self):
            pass

        def _build_radio_web_favs(self):
            pass

        def _profile_refresh(self):
            pass

        def _show_toast(self, *_a):
            pass

        def _profile_after_switch(self):
            pass

    try:
        profiles.activate("a")
        w = W()
        w._maybe_sync_watched(force=True)           # A's sync, under way
        assert len(runs) == 1 and w._watched_sync_running
        assert w.switch_profile("b")
        w._maybe_sync_watched(force=True)           # B's, at its own time
        assert len(runs) == 2, "B's sync was held back by A's"
        runs[0]()                                   # A's answer comes in
        assert watched["a"].replaced == [[1]] and watched["b"].replaced == []
        assert w._watched_sync_running and refreshed == [], "B's is still running"
        runs[1]()
        assert watched["b"].replaced == [[2]]
        assert not w._watched_sync_running and refreshed == ["b"]
        # Back and forth while both run: never a second sync for one profile.
        w._maybe_sync_watched(force=True)           # B's again
        assert w.switch_profile("a")
        w._maybe_sync_watched(force=True)           # A's
        assert len(runs) == 4
        assert w.switch_profile("b") and w._watched_sync_running
        w._maybe_sync_watched(force=True)
        assert len(runs) == 4, "B has one running already"
    finally:
        profiles.activate("")
