"""The window's side of Kodi, Discord and Simkl: the cast panel with a Kodi
in it and its "Add Kodi…" form, Discord's status following the player, and
Trakt and Simkl kept side by side - the watched sync reading both, a mark
told to both, the menu naming the accounts signed in."""
import os
import threading
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


class FakeSettings:
    def __init__(self, **kw):
        self.d = dict(kw)

    def value(self, key, default=None):
        return self.d.get(key, default)

    def setValue(self, key, value):  # noqa: N802
        self.d[key] = value

    def remove(self, key):
        self.d.pop(key, None)


def _inline_threads(monkeypatch, *modules):
    class _Thread:
        def __init__(self, target, daemon=False, **_kw):
            self.target = target

        def start(self):
            self.target()

    for mod in modules:
        monkeypatch.setattr(mod.threading, "Thread", _Thread)


# -- Trakt and Simkl, side by side ------------------------------------------------

class Account:
    def __init__(self, name, connected=True, movies=(), shows=None, wl=()):
        self.name = name
        self.connected = connected
        self.movies, self.shows, self.wl = list(movies), dict(shows or {}), list(wl)
        self.calls = []

    def is_connected(self):
        return self.connected

    def watched_movies(self, titles):
        titles.update({m: f"{self.name} {m}" for m in self.movies})
        return list(self.movies)

    def watched_shows(self, titles):
        return {k: list(v) for k, v in self.shows.items()}

    def watchlist_movies(self):
        return list(self.wl)

    def watchlist_shows(self):
        return []

    def __getattr__(self, name):
        if name.startswith(("add_", "remove_")):
            return lambda *a: self.calls.append((name, *a))
        raise AttributeError(name)


def _accounts_window(trakt, simkl):
    from dopeiptv.ui.mw_context import _ContextMenuMixin
    from dopeiptv.ui.mw_trakt import _TraktMixin

    class W(_TraktMixin, _ContextMenuMixin):
        pass

    w = W()
    w.trakt, w.simkl = trakt, simkl
    return w


def test_the_watched_sync_reads_every_account_signed_in(monkeypatch):
    from dopeiptv.ui import mw_trakt
    monkeypatch.setattr(mw_trakt.QTimer, "singleShot",
                        staticmethod(lambda _ms, fn: fn()))
    _inline_threads(monkeypatch, mw_trakt)
    got = {}
    trakt = Account("T", movies=[1, 2], shows={10: [[1, 1]]}, wl=[5])
    simkl = Account("S", movies=[2, 3], shows={10: [[1, 1], [1, 2]], 11: [[2, 1]]}, wl=[6])
    w = _accounts_window(trakt, simkl)
    w._watched_sync_running = False
    w.watched = SimpleNamespace(
        replace=lambda m, s, mt, st: got.update(movies=sorted(set(m)), shows=s, titles=mt))
    w.watchlist = SimpleNamespace(replace=lambda m, s: got.update(wl=sorted(m)))
    w._push_local_watched_to_trakt = lambda mine=None: got.setdefault("pushed", True)
    def done():
        w._watched_sync_running = False
        got.setdefault("done", True)

    w._on_watched_sync_done = done
    w._on_watched_sync_failed = lambda: got.setdefault("failed", True)
    w._maybe_sync_watched(force=True)
    assert got["movies"] == [1, 2, 3] and got["wl"] == [5, 6]
    assert got["shows"] == {10: [[1, 1], [1, 2]], 11: [[2, 1]]}
    assert got["done"] and got["pushed"] and "failed" not in got
    # Simkl alone does the same; neither signed in, nothing runs.
    got.clear()
    trakt.connected = False
    w._maybe_sync_watched(force=True)
    assert got["movies"] == [2, 3]
    got.clear()
    simkl.connected = False
    w._maybe_sync_watched(force=True)
    assert got == {}


def test_a_mark_goes_to_every_account_and_the_menu_names_them(monkeypatch):
    from dopeiptv.i18n import tr
    from dopeiptv.ui import mw_trakt
    _inline_threads(monkeypatch, mw_trakt)
    trakt, simkl = Account("T"), Account("S", connected=False)
    w = _accounts_window(trakt, simkl)
    assert w._account_action("ctx_mark_watched") == tr("ctx_mark_watched_trakt")
    w._accounts_push("add_movie_history", 7)
    assert trakt.calls == [("add_movie_history", 7)] and simkl.calls == []
    simkl.connected = True
    assert w._accounts_name() == "Trakt + Simkl"
    assert w._account_action("ctx_watchlist_add") == "Add to Watch Later + Trakt + Simkl"
    trakt.connected = False
    assert w._account_action("ctx_unmark_watched") == "Unmark as watched + Simkl"
    w._accounts_push("remove_show_watchlist", 9)
    assert simkl.calls == [("remove_show_watchlist", 9)]
    # One that refuses does not stop the other.
    trakt.connected = True
    trakt.add_episode_history = lambda *a: (_ for _ in ()).throw(RuntimeError("down"))
    w._accounts_push("add_episode_history", 1, 2, 3)
    assert simkl.calls[-1] == ("add_episode_history", 1, 2, 3)
    assert w._push_to_accounts("add_episode_history", 1, 2, 4) is True


def test_local_marks_are_pushed_to_simkl_too(monkeypatch):
    marked = []
    trakt, simkl = Account("T", connected=False), Account("S")
    w = _accounts_window(trakt, simkl)
    w.watched = SimpleNamespace(
        pending_trakt_pushes=lambda: ([11], [(20, 1, 2)]),
        pending_show_pushes=lambda: [30],
        mark_movie_synced=lambda t: marked.append(("movie", t)),
        mark_episode_synced=lambda *a: marked.append(("ep", *a)),
        mark_show_synced=lambda t: marked.append(("show", t)))
    w.watchlist = SimpleNamespace(
        pending_trakt_pushes=lambda: ([40], [50]),
        mark_movie_synced=lambda t: marked.append(("wl", t)),
        mark_show_synced=lambda t: marked.append(("wls", t)))
    w._push_local_favorites_to_trakt = lambda: marked.append("favs")
    w._push_local_watched_to_trakt()
    assert [c[0] for c in simkl.calls] == [
        "add_movie_history", "add_episode_history", "add_show_history",
        "add_movie_watchlist", "add_show_watchlist"]
    # Favourites are a Trakt list: not pushed while Trakt is signed out.
    assert "favs" not in marked and ("movie", 11) in marked and ("wls", 50) in marked


# -- Simkl's scrobbles -----------------------------------------------------------------

def test_simkl_hears_what_plays_and_never_in_private(monkeypatch):
    from dopeiptv.core import privacy
    from dopeiptv.ui import mw_simkl
    from dopeiptv.ui.mw_simkl import _SimklMixin
    _inline_threads(monkeypatch, mw_simkl)
    sent = []
    simkl = SimpleNamespace(is_connected=lambda: True,
                            scrobble_start=lambda p, g: sent.append(("start", p, g)),
                            scrobble_pause=lambda p, g: sent.append(("pause", p, g)),
                            scrobble_stop=lambda p, g: sent.append(("stop", p, g)))
    w = type("W", (_SimklMixin,), {})()
    w.simkl, w._simkl_active, w.series_ctx = simkl, None, None
    w.tmdb = SimpleNamespace(tmdb_id_for=lambda title, kind: 329865)
    w.player = SimpleNamespace(progress_percent=lambda: 85.0)
    w._show_tmdb_id_for_episode = lambda: 95396
    w._simkl_start_for_item("movie", {"name": "EN | Arrival (2016) 1080p"})
    kind, payload, _g = sent[-1]
    assert kind == "start"
    assert payload["movie"]["ids"] == {"tmdb": 329865} and payload["movie"]["year"] == 2016
    w._simkl_paused(True)
    w._simkl_paused(False)
    w._simkl_stop_current()
    assert [s[0] for s in sent] == ["start", "pause", "start", "stop"] and sent[-1][2] == 85.0
    assert w._simkl_active is None
    w.series_ctx = {"name": "Severance"}
    w._simkl_start_for_item("episode", {"season": "2", "episode_num": "4"})
    assert sent[-1][1] == {"show": {"title": "Severance", "ids": {"tmdb": 95396}},
                           "episode": {"season": 2, "number": 4}}
    sent.clear()
    w._simkl_active = None
    privacy.set_private(True)
    try:
        w._simkl_start_for_item("movie", {"name": "Arrival"})
        assert sent == [] and w._simkl_active is None
    finally:
        privacy.set_private(False)


# -- Discord ---------------------------------------------------------------------

class FakePresence:
    made = []

    def __init__(self, client_id):
        self.client_id = client_id
        self.shown = []
        self.stopped = False
        FakePresence.made.append(self)

    def show(self, act):
        self.shown.append(act)

    def stop(self):
        self.stopped = True


def _discord_window(monkeypatch, app_id="99", **settings):
    from dopeiptv.core import discord_rpc
    from dopeiptv.ui.mw_discord import _DiscordMixin
    monkeypatch.setattr(discord_rpc, "DISCORD_APP_ID", app_id)
    monkeypatch.setattr(discord_rpc, "Presence", FakePresence)
    FakePresence.made = []
    w = type("W", (_DiscordMixin,), {})()
    w.settings = FakeSettings(**settings)
    w.xmltv = SimpleNamespace(current_programme=lambda it: {"title": "Rapport"})
    w.series_ctx = None
    w._last_playback = {}
    return w


def test_discord_shows_what_plays(monkeypatch):
    w = _discord_window(monkeypatch, discord_presence="true")
    w._discord_started(5, "SVT1", {"name": "SVT1"}, "live", False)
    (p,) = FakePresence.made
    assert p.client_id == "99"
    act = p.shown[-1]
    assert act["details"] == "Watching SVT1" and act["state"] == "Rapport"
    assert "start" in act["timestamps"]
    w._discord_started(5, "SVT1", {"name": "SVT1"}, "live", True)   # a seek: the same
    assert len(p.shown) == 1
    w._discord_paused(True)
    assert p.shown[-1]["state"] == "Paused" and "timestamps" not in p.shown[-1]
    w._discord_paused(False)
    assert "timestamps" in p.shown[-1]
    w._last_playback = {"kind": "live"}
    w._discord_finished()                 # a drop the app reconnects
    assert p.shown[-1] is not None
    w._discord_stopped()
    assert p.shown[-1] is None
    # Only "Watching TV".
    w.settings.setValue("discord_presence_detail", "tv")
    w.series_ctx = {"name": "Severance"}
    w._discord_started(9, "Hello", {"season": 1, "episode_num": 2, "title": "Hello"},
                       "episode", False)
    assert p.shown[-1]["details"] == "Watching TV" and "state" not in p.shown[-1]
    w.settings.setValue("discord_presence_detail", "names")
    w._discord_apply()
    assert p.shown[-1]["details"] == "Watching Severance"
    assert p.shown[-1]["state"] == "S1 E2 · Hello"
    w._discord_quit()
    assert p.stopped


def test_discord_shows_nothing_in_private_or_while_off(monkeypatch):
    from dopeiptv.core import privacy
    w = _discord_window(monkeypatch)                    # switch off
    w._discord_started(5, "SVT1", {}, "live", False)
    assert FakePresence.made == []                       # no thread, nothing
    w.settings.setValue("discord_presence", "true")
    w._discord_apply()                                   # on: shows what plays
    assert FakePresence.made[0].shown[-1]["details"] == "Watching SVT1"
    privacy.set_private(True)
    try:
        w._discord_push()
        assert FakePresence.made[0].shown[-1] is None
        w._discord_started(6, "Kanal 5", {}, "live", False)
        assert FakePresence.made[0].shown[-1] is None
    finally:
        privacy.set_private(False)
    w._discord_push()                                    # what played in private stays unsaid
    assert FakePresence.made[0].shown[-1] is None
    # No application id in the build: nothing at all.
    w = _discord_window(monkeypatch, app_id="", discord_presence="true")
    w._discord_started(5, "SVT1", {}, "live", False)
    assert FakePresence.made == []


# -- the cast panel with a Kodi in it --------------------------------------------------

class _Mgr:
    def __init__(self, names, suffix=""):
        self.names, self.suffix, self.active = names, suffix, None

    def knows(self, name):
        return bool(self.suffix) and name.endswith(self.suffix)

    def scan(self):
        return list(self.names)


def test_the_cast_panel_offers_kodi(app, monkeypatch):
    from PyQt6.QtCore import QThreadPool
    from PyQt6.QtWidgets import QWidget

    from dopeiptv.i18n import tr
    from dopeiptv.providers import casting
    from dopeiptv.ui import cast_dialog
    monkeypatch.setattr(casting.ChromecastManager, "available", staticmethod(lambda: True))
    kodis = _Mgr(["Den (Kodi)"], " (Kodi)")
    kodis.devices = {"Den (Kodi)": object()}
    hub = casting.CastHub(_Mgr(["Kitchen"]), _Mgr([], " (DLNA)"), kodis)
    win = QWidget()
    win.settings = FakeSettings()
    win.cast, win.pool, win._cast_device = hub, QThreadPool(), None
    answers = []
    win.add_kodi = lambda parent, current: answers.pop(0)
    d = cast_dialog.CastDialog(win, "http://a", "A", probe=False)
    win.pool.waitForDone(3000)
    app.processEvents()
    assert not d.kodi_btn.isHidden()
    names = [d.list.item(i).text() for i in range(d.list.count())]
    assert names == ["Kitchen", "Den (Kodi)"]
    d.list.setCurrentRow(1)
    assert all(r.isHidden() for r in d.track_rows) and d.kind_box.isHidden()
    assert d.dlna_note.text() == tr("cast_kodi_note") and not d.dlna_note.isHidden()
    # Added by hand: in the list, chosen, remembered for the next panel -
    # beside the one it was opened on, which is still there.
    answers.append("Bedroom (Kodi)")
    kodis.devices["Bedroom (Kodi)"] = object()
    d._add_kodi()
    assert d.list.currentItem().text() == "Bedroom (Kodi)"
    assert win.settings.value("cast_devices").split("\n") == [
        "Kitchen", "Den (Kodi)", "Bedroom (Kodi)"]
    # The same Kodi under the name it now gives itself: in its place.
    d.list.setCurrentRow(1)
    del kodis.devices["Den (Kodi)"]
    answers.append("Living room (Kodi)")
    d._add_kodi()
    assert [d.list.item(i).text() for i in range(d.list.count())] == [
        "Kitchen", "Bedroom (Kodi)", "Living room (Kodi)"]
    # Forgotten: gone from the list.
    d.list.setCurrentRow(1)
    del kodis.devices["Bedroom (Kodi)"]
    answers.append("")
    d._add_kodi()
    assert [d.list.item(i).text() for i in range(d.list.count())] == [
        "Kitchen", "Living room (Kodi)"]
    d.close()
    # Without the window's form there is no button.
    del win.add_kodi
    d2 = cast_dialog.CastDialog(win, "http://a", "A", probe=False, managing=True)
    assert d2.kodi_btn.isHidden()
    d2.close()


def test_add_kodi_checks_before_it_keeps(app):
    from PyQt6.QtCore import QThreadPool, QTimer
    from PyQt6.QtWidgets import QDialog, QWidget

    from dopeiptv.i18n import LocalizedError, tr
    from dopeiptv.providers import kodi
    from dopeiptv.ui.mw_cast import _KodiAddDialog

    asked = []
    done = threading.Event()

    class Kodi:
        def device(self, label):
            return kodi.KodiDevice("Den", "10.0.0.5", 8081, "kodi", "pw", manual=True)

        def add(self, host, port, user, password):
            asked.append((host, port, user, password))
            done.set()
            if host == "10.0.0.9":
                raise LocalizedError("kodi_needs_login")
            return "Den (Kodi)"

        def forget(self, label):
            asked.append(("forget", label))

    parent = QWidget()
    pool = QThreadPool()
    d = _KodiAddDialog(parent, Kodi(), "Den (Kodi)", pool)
    assert d.address.text() == "10.0.0.5:8081" and d.user.text() == "kodi"
    assert d.forget_btn is not None
    d.show()
    d.address.setText("not an address at all")
    d._check()
    assert d.status.text() == tr("kodi_bad_address") and not asked
    d.address.setText("10.0.0.9")
    d._check()
    pool.waitForDone(3000)
    for _ in range(20):
        app.processEvents()
    assert asked[-1] == ("10.0.0.9", 8080, "kodi", "pw")
    assert d.status.text() == tr("kodi_needs_login") and d.ok_btn.isEnabled()
    d.address.setText("10.0.0.5:8081")
    d._check()
    pool.waitForDone(3000)
    for _ in range(20):
        app.processEvents()
    assert d.result() == QDialog.DialogCode.Accepted and d.result_label == "Den (Kodi)"
    d2 = _KodiAddDialog(parent, Kodi(), "Den (Kodi)", pool)
    d2.show()
    QTimer.singleShot(0, d2.forget_btn.click)
    app.processEvents()
    assert d2.result_label == "" and asked[-1] == ("forget", "Den (Kodi)")
