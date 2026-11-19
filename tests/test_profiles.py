"""Profiles (core/profiles.py): one per person in the household.

- the split: what is a profile's own, what the household shares, in one
  declared place;
- the first start: an existing settings file (and the three beside it)
  becomes the first profile's, copied - every value still read the same, the
  old keys left for a downgrade;
- two profiles keep separate favorites, history, resume points, watched
  marks, Trakt accounts; a store kept past a switch writes to its own;
- the PIN, a kids profile's limits, removing a profile;
- settings sync and the full backup carry every profile;
- Clear recent history and Home Assistant's commands follow the profile.
"""
from __future__ import annotations

import json
import sys

import pytest

pytest.importorskip("PyQt6.QtCore")
from PyQt6.QtCore import QSettings  # noqa: E402

from dopeiptv.core import (  # noqa: E402
    backup, mqtt_control, privacy, profiles, shortcut_keys, sync,
)
from dopeiptv.core.profiles import MAIN_ID, ProfileSettings, ProfileStore  # noqa: E402
from dopeiptv.core.stores import (  # noqa: E402
    FavoriteStore, HistoryStore, PlaylistStore, WatchedStore, WatchlistStore,
)


@pytest.fixture(autouse=True)
def _no_profile():
    profiles.activate("")
    yield
    profiles.activate("")


def _ini(path, kind=QSettings):
    return kind(str(path), QSettings.Format.IniFormat)


SOURCE = {"id": "a", "name": "Home IPTV", "kind": "xtream", "server": "http://iptv.example:8080",
          "username": "anna", "password": "pw", "epg_url": "", "refresh": "never"}

# A settings file as 1.5 leaves it: one person's everything, by the old names.
OLD_MAIN = {
    "playlists": json.dumps([SOURCE]), "active_playlist": "a", "language": "sv",
    "favorites_a": json.dumps({"all": [{"stream_id": 1, "name": "SVT1"}],
                               "Sport": [{"stream_id": 2, "name": "Eurosport"}]}),
    "favorites_a_locked": json.dumps(["Sport"]),
    "favorites_a_colors": json.dumps({"Sport": {"color": "#ff0000"}}),
    "movie_favorites_a": json.dumps({"all": [{"stream_id": 30, "name": "Film"}]}),
    "series_favorites_a": json.dumps({"all": [{"series_id": 40, "name": "Serie"}]}),
    "radio_favorites": json.dumps({"all": [{"stationuuid": "r1", "name": "P3"}]}),
    "web_favorites": json.dumps({"all": [{"url": "https://youtu.be/x", "name": "Clip"}]}),
    "history_a": json.dumps([{"_key": 1, "_kind": "live", "name": "SVT1", "_url": "http://x/1",
                              "_watched_at": "2026-11-01T20:00:00"}]),
    "epg_reminders_a": json.dumps([{"ch": {"stream_id": 1}, "start": 9, "title": "News"}]),
    "viewing_stats": json.dumps({"v": 1, "h": {}}),
    "theme": "nord", "accent": "green", "theme_follow_system": "false",
    "audio_lang": "swe", "sub_mode": "lang", "sub_lang": "eng", "sub_lang2": "swe",
    "track_prefs": json.dumps({"vod:30": {"aid": 2}}),
    "last_place": "films", "last_list_place": "films",
    "trakt_access_token": "tok", "trakt_refresh_token": "ref", "trakt_expires_at": 99,
    "simkl_access_token": "stok",
    # The household's.
    "recording_jobs": json.dumps([{"id": "j", "url": "http://x", "status": "scheduled"}]),
    "series_rules_a": json.dumps([{"title": "News"}]),
    "category_overrides_a": json.dumps({"live": {"5": {"locked": True}}}),
    "nerd_enabled": "true", "stream_format": "ts", "parental_pin_hash": "h",
    "parental_salt": "s", "volume": 70, "window_geometry": "geo",
}
OLD_RESUME = {"resume_positions_a": json.dumps({"vod:30": {"pos": 600, "dur": 5000, "ts": 1}}),
              "_migrated": "1"}
OLD_FOLLOW = {"followed_series_a": json.dumps({"40": {"snap": {"series_id": 40}, "known": [],
                                                      "new": [], "primed": True}})}
OLD_CACHE = {"trakt_watched_cache": json.dumps({"local_movies": [550], "local_items": [{"_kind": "vod"}]}),
             "trakt_watchlist_cache": json.dumps({"movies": [{"stream_id": 30}], "shows": []}),
             "tmdb_poster_cache_v3": "{}", "_migrated": "1"}


def _old_files(tmp_path):
    files = {}
    for name, data in (("main", OLD_MAIN), ("resume", OLD_RESUME), ("follow", OLD_FOLLOW),
                       ("cache", OLD_CACHE)):
        s = _ini(tmp_path / f"{name}.ini")
        for k, v in data.items():
            s.setValue(k, v)
        s.sync()
        files[name] = s
    return files


def _views(tmp_path):
    return {name: _ini(tmp_path / f"{name}.ini", ProfileSettings)
            for name in ("main", "resume", "follow", "cache")}


# -- the split ---------------------------------------------------------------------------

def test_the_split_is_declared_in_one_place():
    for key in ("favorites_a", "favorites_a_locked", "favorites_a_colors", "movie_favorites_x",
                "series_favorites_x", "radio_favorites", "web_favorites", "history", "history_a",
                "resume_positions_a", "epg_reminders_a", "followed_series_a", "viewing_stats",
                "trakt_watched_cache", "trakt_watchlist_cache", "trakt_access_token",
                "simkl_access_token", "theme", "accent", "audio_lang",
                "sub_lang", "sub_lang2", "sub_mode", "last_place", "last_list_place",
                "track_prefs"):
        assert profiles.is_own(key), key
    for key in profiles.SHARED_EXAMPLES:
        assert not profiles.is_own(key), key
    # Every store a profile keeps is named, and the avatar's colours are the
    # theme's own fixed palette.
    assert {"FavoriteStore", "HistoryStore", "ResumeStore", "WatchedStore", "WatchlistStore",
            "FollowedSeriesStore", "ReminderStore", "ViewingLog", "TraktClient",
            "SimklClient"} <= set(profiles.OWN_STORES)
    from dopeiptv.ui import theme
    assert set(profiles.COLOURS) <= set(theme.ACCENTS)


def test_where_a_key_is_kept():
    assert profiles.stored_key("theme", "kid") == "profiles/kid/theme"
    assert profiles.stored_key("playlists", "kid") == "playlists"
    assert profiles.stored_key("theme", "") == "theme"
    assert profiles.stored_key("profiles/x/theme", "kid") == "profiles/x/theme"
    raw = ["theme", "playlists", "profiles/me/theme", "profiles/kid/theme", "profiles/kid/history_a"]
    assert profiles.view_keys(raw, "kid") == ["playlists", "theme", "history_a"]
    assert profiles.view_keys(raw, "") == raw


# -- the first start ---------------------------------------------------------------------

def test_an_existing_settings_file_becomes_the_first_profile_with_nothing_lost(tmp_path):
    old = _old_files(tmp_path)
    before = {name: {k: s.value(k) for k in s.allKeys()} for name, s in old.items()}
    views = _views(tmp_path)
    store = profiles.start(views["main"], [views["resume"], views["follow"], views["cache"]],
                           "Jag", "1.6.0")
    assert profiles.active_id() == MAIN_ID
    assert [p.name for p in store.profiles()] == ["Jag"] and store.active_id == MAIN_ID
    for name, values in before.items():
        raw = profiles.raw(views[name])
        for key, value in values.items():
            # Every value reads as it did, through the profile's view...
            assert views[name].value(key) == value, (name, key)
            # ...and the old key is still there, as it was, for a downgrade.
            assert raw.value(key) == value, (name, key)
            if profiles.is_own(key):
                assert raw.value(f"profiles/{MAIN_ID}/{key}") == value, (name, key)
            else:
                assert not raw.contains(f"profiles/{MAIN_ID}/{key}"), (name, key)
    # A downgrade reads the old file: everything is where it was.
    again = _ini(tmp_path / "main.ini")
    assert again.value("favorites_a") == OLD_MAIN["favorites_a"]
    # The stores read the profile's copy.
    favs = FavoriteStore(profiles.pinned(views["main"]), "favorites_a")
    assert favs.is_favorite(1) and favs.is_locked("Sport")
    assert WatchedStore(profiles.pinned(views["cache"])).local_movies == {550}
    # A second start does not copy again (a change since stays).
    views["main"].setValue("theme", "dracula")
    assert not profiles.migrate(views["main"], [views["cache"]], "Jag")
    assert views["main"].value("theme") == "dracula"
    assert profiles.raw(views["main"]).value("theme") == "nord"


def test_a_first_start_that_stopped_half_way_keeps_its_copies(tmp_path):
    old = _old_files(tmp_path)
    old["main"].setValue(f"profiles/{MAIN_ID}/theme", "light")      # copied before it stopped
    old["main"].sync()
    views = _views(tmp_path)
    profiles.start(views["main"], [views["cache"]], "Me")
    assert views["main"].value("theme") == "light"
    assert views["main"].value("accent") == "green"


# -- two profiles --------------------------------------------------------------------------

def _two(tmp_path):
    _old_files(tmp_path)
    views = _views(tmp_path)
    store = profiles.start(views["main"], [views["resume"], views["follow"], views["cache"]], "Me")
    kid = store.add("Lisa", kids=True)
    return views, store, kid


def test_two_profiles_keep_their_own_apart(tmp_path):
    from dopeiptv.services.resume import ResumeStore
    views, store, kid = _two(tmp_path)
    me_main = profiles.pinned(views["main"], MAIN_ID)
    kid_main = profiles.pinned(views["main"], kid.id)
    kid_favs = FavoriteStore(kid_main, "favorites_a")
    assert kid_favs.items() == []                         # a new profile starts empty
    kid_favs.add("all", {"stream_id": 9, "name": "Barnkanalen"})
    assert not FavoriteStore(me_main, "favorites_a").is_favorite(9)
    assert FavoriteStore(me_main, "favorites_a").is_favorite(1)
    HistoryStore(kid_main, "history_a").add("http://x/9", "Barnkanalen", None, "9", "live")
    assert [e["_key"] for e in HistoryStore(me_main, "history_a").items()] == [1]
    assert [e["_key"] for e in HistoryStore(kid_main, "history_a").items()] == ["9"]
    kid_resume = ResumeStore(profiles.pinned(views["resume"], kid.id), "a")
    kid_resume.record("vod", 77, 300.0, 3000.0)
    me_resume = ResumeStore(profiles.pinned(views["resume"], MAIN_ID), "a")
    assert me_resume.saved_position(77, "movie") == 0
    assert ResumeStore(profiles.pinned(views["resume"], kid.id), "a").saved_position(77, "movie") == 300
    assert me_resume.saved_position(30, "movie") == 600
    assert ResumeStore(profiles.pinned(views["resume"], kid.id), "a").saved_position(30, "movie") == 0
    kid_watched = WatchedStore(profiles.pinned(views["cache"], kid.id))
    kid_watched.mark_movie_local(123)
    assert WatchedStore(profiles.pinned(views["cache"], MAIN_ID)).local_movies == {550}
    assert WatchedStore(profiles.pinned(views["cache"], kid.id)).local_movies == {123}
    WatchlistStore(profiles.pinned(views["cache"], kid.id)).add_movie_local({"stream_id": 5}, None)
    assert len(WatchlistStore(profiles.pinned(views["cache"], MAIN_ID)).movies) == 1
    # What the app reads through its settings follows the profile in use.
    assert views["main"].value("theme") == "nord"
    profiles.activate(kid.id)
    assert views["main"].value("theme") is None
    views["main"].setValue("theme", "light")
    assert views["main"].value("playlists") == OLD_MAIN["playlists"]      # shared
    profiles.activate(MAIN_ID)
    assert views["main"].value("theme") == "nord"


def test_a_store_kept_past_a_switch_writes_to_its_own_profile(tmp_path):
    views, store, kid = _two(tmp_path)
    profiles.activate(MAIN_ID)
    mine = FavoriteStore(profiles.pinned(views["main"]), "favorites_a")
    profiles.activate(kid.id)                     # a switch while it was held
    mine.add("all", {"stream_id": 4, "name": "Late"})
    assert FavoriteStore(profiles.pinned(views["main"], MAIN_ID), "favorites_a").is_favorite(4)
    assert not FavoriteStore(profiles.pinned(views["main"], kid.id), "favorites_a").is_favorite(4)


def test_a_trakt_sign_in_is_the_profiles_own(tmp_path):
    from dopeiptv.providers.trakt import TraktClient
    views, store, kid = _two(tmp_path)
    me = TraktClient(profiles.pinned(views["main"], MAIN_ID))
    lisa = TraktClient(profiles.pinned(views["main"], kid.id))
    assert me.access_token == "tok" and lisa.access_token == ""
    lisa._store_tokens({"access_token": "lisa-tok", "refresh_token": "r", "expires_in": 60})
    assert lisa.access_token == "lisa-tok" and me.access_token == "tok"
    lisa.disconnect()
    assert lisa.access_token == "" and me.access_token == "tok"
    from dopeiptv.providers.simkl import SimklClient
    assert SimklClient(profiles.pinned(views["main"], kid.id)).settings.value(
        "simkl_access_token", "") == ""


def test_a_renewal_on_a_worker_after_a_switch_is_kept_by_its_own_profile(tmp_path, monkeypatch):
    """The sign-in is in the client's memory, renewed there on a worker and
    written on the window's thread - through the profile's pinned view, so
    into the profile that signed in, whoever is in use by then."""
    import threading
    import time
    widgets = pytest.importorskip("PyQt6.QtWidgets")
    app = widgets.QApplication.instance() or widgets.QApplication([])
    from dopeiptv.providers import trakt as T

    class Answer:
        status_code = 200

        def json(self):
            return {"access_token": "me-new", "refresh_token": "me-ref2", "expires_in": 86400}
    monkeypatch.setattr(T.requests, "post", lambda *a, **k: Answer())
    views, store, kid = _two(tmp_path)
    me = T.TraktClient(profiles.pinned(views["main"], MAIN_ID))     # "tok", due long ago
    profiles.activate(kid.id)                                       # Lisa's turn now
    got = {}
    t = threading.Thread(target=lambda: got.update(me._headers()))
    t.start()
    t.join(10)
    assert got["Authorization"] == "Bearer me-new" and me.refresh_token == "me-ref2"
    assert profiles.pinned(views["main"], MAIN_ID).value("trakt_access_token") == "tok"
    end = time.monotonic() + 5
    while (profiles.pinned(views["main"], MAIN_ID).value("trakt_access_token") == "tok"
           and time.monotonic() < end):
        app.processEvents()
        time.sleep(0.01)
    mine = profiles.pinned(views["main"], MAIN_ID)
    assert (mine.value("trakt_access_token"), mine.value("trakt_refresh_token")) == ("me-new", "me-ref2")
    assert views["main"].value("trakt_access_token", "") == ""       # Lisa's is still none
    assert T.TraktClient(mine).access_token == "me-new"              # a client made later reads it


class _OnceTrakt:
    """Trakt's token endpoint: a refresh token is good once. An answer in
    *holds* is kept back until its event is set - renewed at Trakt, the
    answer still on its way."""

    class Answer:
        def __init__(self, code, body):
            self.status_code, self._body = code, body

        def json(self):
            return self._body

    def __init__(self, good: str):
        import threading
        self.good, self.n, self.sent = {good}, 0, []
        self.lock = threading.Lock()
        self.holds: dict = {}
        self.arrived = threading.Event()

    def post(self, url, json=None, **_kw):
        rt = json["refresh_token"]
        with self.lock:
            self.sent.append(rt)
            if rt in self.good:
                self.good.discard(rt)
                self.n += 1
                self.good.add(f"ref{self.n}")
                answer = self.Answer(200, {"access_token": f"acc{self.n}",
                                           "refresh_token": f"ref{self.n}", "expires_in": 86400})
            else:
                answer = self.Answer(400, {"error": "invalid_grant"})
            hold = self.holds.pop(rt, None)
        if hold is not None:
            self.arrived.set()
            hold.wait(10)
        return answer


def _settle(app, done, secs: float = 5.0):
    import time
    end = time.monotonic() + secs
    while not done() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def test_a_renewal_in_flight_through_a_switch_and_back_is_what_the_new_client_goes_on_with(
        tmp_path, monkeypatch):
    """A→B→A while a worker of the first client renews: the client made on
    the way back must not keep the refresh token Trakt has just retired -
    its next renewal would be refused and sign the profile out."""
    import threading
    widgets = pytest.importorskip("PyQt6.QtWidgets")
    app = widgets.QApplication.instance() or widgets.QApplication([])
    from dopeiptv.providers import trakt as T
    trakt = _OnceTrakt("ref")
    monkeypatch.setattr(T.requests, "post", trakt.post)
    views, store, kid = _two(tmp_path)
    old = T.TraktClient(profiles.pinned(views["main"], MAIN_ID))       # "ref", due long ago
    trakt.holds["ref"] = answered = threading.Event()
    worker = threading.Thread(target=old._ensure_fresh_token)          # a watched sync's
    worker.start()
    assert trakt.arrived.wait(10)                 # renewed at Trakt, the answer on its way
    profiles.activate(kid.id)                     # Lisa...
    T.TraktClient(profiles.pinned(views["main"], kid.id))
    profiles.activate(MAIN_ID)                    # ...and back: a new client
    new = T.TraktClient(profiles.pinned(views["main"], MAIN_ID))
    answered.set()
    worker.join(10)
    assert (new.access_token, new.refresh_token) == ("acc1", "ref1")
    assert new.refresh_tokens() is True           # its next renewal: Trakt's own token
    assert trakt.sent == ["ref", "ref1"] and new.is_connected()
    mine = profiles.pinned(views["main"], MAIN_ID)
    _settle(app, lambda: mine.value("trakt_refresh_token") == "ref2")
    assert (mine.value("trakt_access_token"), mine.value("trakt_refresh_token")) == ("acc2", "ref2")
    assert profiles.pinned(views["main"], kid.id).value("trakt_access_token", "") == ""


def test_the_profiles_clients_renew_one_at_a_time_and_a_newer_answer_stays(
        tmp_path, monkeypatch):
    """A worker of the new client that finds the sign-in due while the old
    client's renewal is out waits for it, and goes on with what it brought
    - never a second renewal with the same, retired token."""
    import threading
    widgets = pytest.importorskip("PyQt6.QtWidgets")
    app = widgets.QApplication.instance() or widgets.QApplication([])
    from dopeiptv.providers import trakt as T
    trakt = _OnceTrakt("ref")
    monkeypatch.setattr(T.requests, "post", trakt.post)
    views, store, kid = _two(tmp_path)
    old = T.TraktClient(profiles.pinned(views["main"], MAIN_ID))
    new = T.TraktClient(profiles.pinned(views["main"], MAIN_ID))
    trakt.holds["ref"] = answered = threading.Event()
    first = threading.Thread(target=old._ensure_fresh_token)
    first.start()
    assert trakt.arrived.wait(10)
    second = threading.Thread(target=new._ensure_fresh_token)
    second.start()
    second.join(0.3)
    assert second.is_alive()                      # waits for the renewal under way
    answered.set()
    first.join(10)
    second.join(10)
    assert trakt.sent == ["ref"] and new.refresh_token == "ref1" and old.refresh_token == "ref1"
    _settle(app, lambda: profiles.pinned(views["main"], MAIN_ID).value("trakt_refresh_token") == "ref1")
    assert profiles.pinned(views["main"], MAIN_ID).value("trakt_refresh_token") == "ref1"


def test_a_simkl_sign_in_is_the_profiles_own_with_its_refresh_token(tmp_path):
    # The access token alone was a profile's own: the refresh token and its
    # expiry were the household's, so a second sign-in took the first
    # profile's renewals, and a sign-out revoked the other's grant.
    from dopeiptv.providers import simkl
    views, store, kid = _two(tmp_path)
    me = simkl.SimklClient(profiles.pinned(views["main"], MAIN_ID), client_id="c")
    lisa = simkl.SimklClient(profiles.pinned(views["main"], kid.id), client_id="c")
    me._store({"access_token": "me-a", "refresh_token": "me-r", "expires_in": 7 * 86400})
    lisa._store({"access_token": "lisa-a", "refresh_token": "lisa-r", "expires_in": 60})
    assert me.settings.value(simkl.REFRESH_KEY) == "me-r"
    assert int(me.settings.value(simkl.EXPIRES_KEY)) > int(lisa.settings.value(simkl.EXPIRES_KEY))
    assert not me._due() and lisa._due()
    lisa._forget()
    assert me.access_token == "me-a" and me.settings.value(simkl.REFRESH_KEY) == "me-r"
    for key in (simkl.TOKEN_KEY, simkl.REFRESH_KEY, simkl.EXPIRES_KEY):
        assert profiles.is_own(key), key


# -- the PIN, kids, removing one --------------------------------------------------------------

def test_a_pin_is_four_digits_kept_hashed(tmp_path):
    views, store, kid = _two(tmp_path)
    me = store.get(MAIN_ID)
    assert not profiles.valid_pin("123") and not profiles.valid_pin("12a4")
    with pytest.raises(ValueError):
        store.set_pin(MAIN_ID, "12345")
    store.set_pin(MAIN_ID, "2468")
    store.reload()
    me = store.get(MAIN_ID)
    assert me.has_pin and me.verify("2468") and not me.verify("1357")
    # The PIN itself is no value of what is kept, only its salt and hash -
    # whose hex holds "2468" now and then by chance, so whole values are
    # looked at, not the text.
    def values(o):
        if isinstance(o, dict):
            for v in o.values():
                yield from values(v)
        elif isinstance(o, list):
            for v in o:
                yield from values(v)
        else:
            yield str(o)
    kept = json.loads(profiles.raw(views["main"]).value(profiles.K_LIST) or "[]")
    assert "2468" not in list(values(kept))
    assert store.grown_up_pin("2468") and not store.grown_up_pin("0000")
    store.set_pin(kid.id, "1111")
    assert not store.grown_up_pin("1111")         # a kids profile's PIN opens no Settings
    assert store.get(MAIN_ID).public().get("pin") is True
    assert "pin_hash" not in store.get(MAIN_ID).public()
    store.clear_pin(MAIN_ID)
    assert not store.get(MAIN_ID).has_pin


def test_a_pin_asked_over_the_network_cannot_be_guessed(tmp_path, monkeypatch):
    # The phone remote, the remote API and Home Assistant switch to a
    # profile with its PIN: four digits, so wrong ones are counted and the
    # switch by PIN is refused for a while after a few - even the right PIN.
    from dopeiptv.ui import mw_profiles
    views, store, kid = _two(tmp_path)
    profiles.activate(kid.id)
    store.set_pin(MAIN_ID, "2468")
    store.reload()
    clock = [1000.0]
    monkeypatch.setattr(mw_profiles.time, "monotonic", lambda: clock[0])

    class W(mw_profiles._ProfilesMixin):
        def __init__(self):
            self.profiles = store
            self.parental = type("P", (), {"has_pin": lambda s: False})()
            self.switched: list[str] = []

        def switch_profile(self, pid):
            self.switched.append(pid)
            return True

        def _profile_close_picker(self):
            pass

        def _profile_state(self):
            return {}

    w = W()
    with pytest.raises(ValueError, match="has a PIN"):
        w._profile_switch_remote({"name": "Me"})        # none given: not a guess
    for guess in ("0000", "1111", "2222", "3333", "4444"):
        with pytest.raises(ValueError, match="has a PIN"):
            w._profile_switch_remote({"name": "Me", "pin": guess})
    with pytest.raises(ValueError, match="too many"):
        w._profile_switch_remote({"name": "Me", "pin": "2468"})
    assert w.switched == []
    clock[0] += mw_profiles.REMOTE_PIN_SECS + 1
    assert w._profile_switch_remote({"name": "Me", "pin": "2468"})["ok"] is True
    assert w.switched == [MAIN_ID]


def test_names_and_how_many(tmp_path):
    views, store, kid = _two(tmp_path)
    with pytest.raises(ValueError):
        store.add("lisa")                         # the name is taken, in any case
    with pytest.raises(ValueError):
        store.add("   ")
    for n in range(profiles.MAX_PROFILES - 2):
        store.add(f"P{n}")
    with pytest.raises(ValueError):
        store.add("One too many")
    assert len({p.id for p in store.profiles()}) == profiles.MAX_PROFILES
    assert profiles.initials("Anna Berg") == "AB" and profiles.initials("lisa") == "LI"


def test_removing_a_profile_takes_only_its_own(tmp_path):
    views, store, kid = _two(tmp_path)
    FavoriteStore(profiles.pinned(views["main"], kid.id), "favorites_a").add("all", {"stream_id": 9})
    profiles.pinned(views["cache"], kid.id).setValue("trakt_watched_cache", "{}")
    profiles.activate(MAIN_ID)
    assert not store.remove(MAIN_ID, views.values())      # never the one in use
    assert store.remove(kid.id, views.values())
    assert store.get(kid.id) is None
    for s in views.values():
        assert not [k for k in profiles.raw(s).allKeys() if k.startswith(f"profiles/{kid.id}/")]
    assert FavoriteStore(profiles.pinned(views["main"], MAIN_ID), "favorites_a").is_favorite(1)
    assert not store.remove(MAIN_ID, views.values())      # never the last one


def test_a_kids_profile_sees_nothing_for_adults():
    for name in ("XXX", "Adult", "Adults only", "18+", "EN | Erotica", "For adults"):
        assert profiles.adult_category(name), name
    for name in ("Kids", "Adultswim", "Sport", "Classic 1980s"):
        assert not profiles.adult_category(name), name
    assert profiles.adult_item({"is_adult": "1"}) and profiles.adult_item({"is_adult": 1})
    assert not profiles.adult_item({"is_adult": "0"}) and not profiles.adult_item({})


# -- sync and backup carry every profile ---------------------------------------------------------

def _computer(tmp_path, name, with_profiles=True):
    main = _ini(tmp_path / f"{name}.ini", ProfileSettings)
    resume = _ini(tmp_path / f"{name}-resume.ini", ProfileSettings)
    playlists = PlaylistStore(main)
    if not playlists.items:
        playlists.add(dict(SOURCE))
    reg = profiles.start(main, [resume], "Me")
    return main, resume, playlists, reg


def _ctx(main, resume, playlists, reg, platform=sys.platform):
    """As the window makes it (mw_sync._sync_ctx)."""
    ctx = sync.Context(profiles.pinned(main, MAIN_ID), profiles.pinned(resume, MAIN_ID),
                       playlists, registry=reg,
                       pin=lambda pid: (profiles.pinned(main, pid), profiles.pinned(resume, pid)),
                       platform=platform)
    ctx.refresh_others()
    return ctx


def test_settings_sync_carries_every_profile(tmp_path):
    a = _computer(tmp_path, "a")
    a_reg = a[3]
    kid = a_reg.add("Lisa", colour="pink", emoji="🦊", kids=True)
    a_reg.set_pin(kid.id, "1234")
    FavoriteStore(profiles.pinned(a[0], kid.id), "favorites_a").add("all", {"stream_id": 9, "name": "B"})
    FavoriteStore(profiles.pinned(a[0], MAIN_ID), "favorites_a").add("all", {"stream_id": 1, "name": "S"})
    profiles.pinned(a[0], kid.id).setValue("theme", "light")
    profiles.pinned(a[0], kid.id).setValue("trakt_access_token", "never-synced")
    HistoryStore(profiles.pinned(a[0], kid.id), "history_a").add("http://x/9", "B", None, "9", "live")
    cats = sync.CATEGORIES
    snap_a = sync.collect(_ctx(*a), cats)
    s = snap_a.values["settings"]
    fp = sync.source_fp(SOURCE)
    assert s[f"profiles/{kid.id}/theme"] == "light"
    assert f"profiles/{kid.id}/trakt_access_token" not in s          # a login never syncs
    meta = json.loads(s[f"profile/{kid.id}"])
    assert meta == {"name": "Lisa", "colour": "pink", "emoji": "🦊", "kids": True}
    assert "pin_hash" not in s[f"profile/{kid.id}"] and "salt" not in s[f"profile/{kid.id}"]
    favs = snap_a.values["favorites"]
    assert sync._eid(fp, "chan", "i", "all", 1) in favs               # the first profile: as always
    assert sync._eid(f"{fp}@{kid.id}", "chan", "i", "all", 9) in favs
    assert sync._eid(fp, "chan", "i", "all", 9) not in favs
    assert sync._eid(f"{fp}@{kid.id}", "h", "live", "9") in snap_a.values["history"]
    doc = sync.empty_doc()
    sync.stamp(doc, snap_a, cats, 1000)

    # Another computer, the same code: Lisa arrives, with her own, and no PIN.
    b = _computer(tmp_path, "b")
    ctx_b = _ctx(*b)
    before = sync.collect(ctx_b, cats)
    result = sync.RoundResult(merged=doc, stamped=doc, version=1, uploaded=True, size=0)
    applied, _keep = sync.apply(ctx_b, result, before, before, cats)
    b_reg = b[3]
    b_reg.reload()
    lisa = b_reg.get(kid.id)
    assert lisa is not None and lisa.name == "Lisa" and lisa.kids and not lisa.has_pin
    assert profiles.pinned(b[0], kid.id).value("theme") == "light"
    assert FavoriteStore(profiles.pinned(b[0], kid.id), "favorites_a").is_favorite(9)
    assert FavoriteStore(profiles.pinned(b[0], MAIN_ID), "favorites_a").is_favorite(1)
    assert not FavoriteStore(profiles.pinned(b[0], MAIN_ID), "favorites_a").is_favorite(9)
    assert [e["_key"] for e in HistoryStore(profiles.pinned(b[0], kid.id), "history_a").items()] == ["9"]

    # A profile removed on one computer is never taken out of another by sync.
    b_reg.remove(kid.id, [b[0], b[1]])
    snap_b = sync.collect(_ctx(*b), cats)
    doc_b = dict(doc, c={k: dict(v) for k, v in doc["c"].items()})
    sync.stamp(doc_b, snap_b, cats, 2000)
    assert "d" not in doc_b["c"]["settings"][f"profile/{kid.id}"]
    assert "d" not in doc_b["c"]["settings"][f"profiles/{kid.id}/theme"]


def test_a_profile_removed_here_is_not_brought_back_by_the_next_sync(tmp_path):
    # The synced copy still holds the profile (sync never takes one out):
    # the next round read it as one another computer has, and Lisa came
    # back - her favourites and theme with her - on the computer that had
    # just removed her.
    a = _computer(tmp_path, "a")
    kid = a[3].add("Lisa")
    profiles.pinned(a[0], kid.id).setValue("theme", "light")
    FavoriteStore(profiles.pinned(a[0], kid.id), "favorites_a").add("all", {"stream_id": 9, "name": "B"})
    cats = ("favorites", "settings")
    doc = sync.empty_doc()
    sync.stamp(doc, sync.collect(_ctx(*a), cats), cats, 1000)
    assert a[3].remove(kid.id, [a[0], a[1]])
    ctx = _ctx(*a)
    before = sync.collect(ctx, cats)
    stamped = sync._copy(doc)
    sync.stamp(stamped, before, cats, 2000)
    merged = sync.merge(doc, stamped)
    result = sync.RoundResult(merged=merged, stamped=stamped, version=2, uploaded=True, size=0)
    sync.apply(ctx, result, before, sync.collect(_ctx(*a), cats), cats)
    a[3].reload()
    assert a[3].get(kid.id) is None
    assert profiles.pinned(a[0], kid.id).value("theme") is None
    assert not [k for k in profiles.raw(a[0]).allKeys() if k.startswith(f"profiles/{kid.id}/")]
    # Another computer still has her: she stays there, and is not added
    # back here either.
    assert not a[3].merge_public([{"id": kid.id, "name": "Lisa"}])
    assert a[3].get(kid.id) is None


def test_an_older_computer_syncs_on_with_the_first_profile(tmp_path):
    # A computer still on a version without profiles reads the first
    # profile's entries as it always did, and passes the others' by.
    a = _computer(tmp_path, "a")
    kid = a[3].add("Lisa")
    profiles.pinned(a[0], kid.id).setValue("theme", "light")
    profiles.pinned(a[0], MAIN_ID).setValue("theme", "nord")
    snap = sync.collect(_ctx(*a), ("settings",))
    doc = sync.empty_doc()
    sync.stamp(doc, snap, ("settings",), 1000)
    old = _ini(tmp_path / "old.ini")
    old_ctx = sync.Context(old, None, PlaylistStore(old))
    before = sync.collect(old_ctx, ("settings",))
    result = sync.RoundResult(merged=doc, stamped=doc, version=1, uploaded=True, size=0)
    sync.apply(old_ctx, result, before, before, ("settings",))
    assert old.value("theme") == "nord"
    assert not [k for k in old.allKeys() if k.startswith(("profiles/", "profile/"))]


def _round_to(tmp_path, name, platform, doc):
    """Another computer of *platform* taking *doc* (the code's copy)."""
    c = _computer(tmp_path, name)
    ctx = _ctx(*c, platform=platform)
    before = sync.collect(ctx, ("settings",))
    result = sync.RoundResult(merged=doc, stamped=doc, version=1, uploaded=True, size=0)
    sync.apply(ctx, result, before, before, ("settings",))
    return c


def test_keyboard_shortcuts_sync_as_the_households_per_system(tmp_path):
    # The household's keys: one entry per system, whoever set them, and
    # every profile on a computer of that system has them.
    assert not profiles.is_own(shortcut_keys.SETTING)
    a = _computer(tmp_path, "a")
    kid = a[3].add("Lisa")
    profiles.activate(kid.id)
    a[0].setValue(shortcut_keys.SETTING, shortcut_keys.dump({"pause": "K"}))
    s = sync.collect(_ctx(*a, platform="darwin"), ("settings",)).values["settings"]
    assert [k for k in s if "keyboard_shortcuts" in k] == ["keyboard_shortcuts@macos"]
    doc = sync.empty_doc()
    sync.stamp(doc, sync.collect(_ctx(*a, platform="darwin"), ("settings",)), ("settings",), 1000)
    for name, platform, want in (("mac", "darwin", {"pause": "K"}), ("lin", "linux", {})):
        c = _round_to(tmp_path, name, platform, doc)
        assert c[3].get(kid.id) is not None
        for pid in (MAIN_ID, kid.id):
            profiles.activate(pid)
            assert shortcut_keys.parse(c[0].value(shortcut_keys.SETTING) or "") == want, (name, pid)
        assert not [k for k in profiles.raw(c[0]).allKeys() if "/keyboard_shortcuts" in k]


def test_were_shortcuts_a_profiles_own_each_would_sync_its_own_per_system(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "OWN_KEYS", profiles.OWN_KEYS | {shortcut_keys.SETTING})
    a = _computer(tmp_path, "a")
    kid = a[3].add("Lisa")
    profiles.pinned(a[0], kid.id).setValue(shortcut_keys.SETTING, shortcut_keys.dump({"pause": "K"}))
    profiles.pinned(a[0], MAIN_ID).setValue(shortcut_keys.SETTING, shortcut_keys.dump({"mute": "J"}))
    snap = sync.collect(_ctx(*a, platform="darwin"), ("settings",))
    s = snap.values["settings"]
    assert json.loads(s[f"profiles/{kid.id}/keyboard_shortcuts@macos"]) == {"pause": "K"}
    assert json.loads(s["keyboard_shortcuts@macos"]) == {"mute": "J"}
    doc = sync.empty_doc()
    sync.stamp(doc, snap, ("settings",), 1000)
    mac = _round_to(tmp_path, "mac", "darwin", doc)
    lin = _round_to(tmp_path, "lin", "linux", doc)
    for c, kid_keys, my_keys in ((mac, {"pause": "K"}, {"mute": "J"}), (lin, {}, {})):
        got = {pid: shortcut_keys.parse(profiles.pinned(c[0], pid).value(shortcut_keys.SETTING) or "")
               for pid in (kid.id, MAIN_ID)}
        assert got == {kid.id: kid_keys, MAIN_ID: my_keys}


def test_a_full_backup_holds_every_profile(tmp_path):
    _old_files(tmp_path)
    views = _views(tmp_path)
    store = profiles.start(views["main"], [views["resume"], views["follow"], views["cache"]], "Me")
    kid = store.add("Lisa")
    FavoriteStore(profiles.pinned(views["main"], kid.id), "favorites_a").add("all", {"stream_id": 9})
    profiles.pinned(views["cache"], kid.id).setValue(
        "trakt_watchlist_cache", json.dumps({"movies": [{"stream_id": 5}], "shows": []}))
    raws = {name: profiles.raw(s) for name, s in views.items()}
    doc = backup.collect(raws, "1.6.0")
    main = doc["stores"]["main"]
    assert f"profiles/{kid.id}/favorites_a" in main and f"profiles/{MAIN_ID}/favorites_a" in main
    assert profiles.K_LIST in main
    assert f"profiles/{kid.id}/trakt_watchlist_cache" in doc["stores"]["cache"]
    assert f"profiles/{MAIN_ID}/resume_positions_a" in doc["stores"]["resume"]
    counts = backup.summary(backup.parse(doc))
    # Each profile's own once: Me's three favourite lists' four, Lisa's one -
    # not the old names' copies again.
    assert counts["profiles"] == 2
    assert counts["favorites"] == 2 + 1 + 1 + 1 + 1 + 1
    assert counts["watchlist"] == 2 and counts["resume"] == 1
    # Put back on another computer: every profile is there.
    fresh = {name: _ini(tmp_path / f"new-{name}.ini") for name in backup.STORES}
    backup.restore(backup.parse(doc), fresh)
    new_views = {name: _ini(tmp_path / f"new-{name}.ini", ProfileSettings) for name in backup.STORES}
    reg = profiles.start(new_views["main"], [new_views["cache"]], "Me")
    assert {p.name for p in reg.profiles()} == {"Me", "Lisa"}
    assert FavoriteStore(profiles.pinned(new_views["main"], kid.id), "favorites_a").is_favorite(9)


def test_a_backup_from_before_profiles_becomes_the_first_profile_again(tmp_path):
    (tmp_path / "old").mkdir()
    old = _old_files(tmp_path / "old")
    doc = backup.collect(old, "1.5.0")
    views = {name: _ini(tmp_path / f"cur-{name}.ini", ProfileSettings) for name in backup.STORES}
    reg = profiles.start(views["main"], [views["cache"]], "Me")
    reg.add("Lisa")
    raws = {name: profiles.raw(s) for name, s in views.items()}
    backup.restore(backup.parse(doc), raws)
    # The next start: no profiles in the file any more - the old names are
    # made the first profile's again, nothing lost.
    again = {name: _ini(tmp_path / f"cur-{name}.ini", ProfileSettings) for name in backup.STORES}
    reg = profiles.start(again["main"], [again["cache"]], "Me")
    assert [p.id for p in reg.profiles()] == [MAIN_ID]
    assert again["main"].value("favorites_a") == OLD_MAIN["favorites_a"]
    assert again["cache"].value("trakt_watched_cache") == OLD_CACHE["trakt_watched_cache"]


# -- what follows the profile in use ---------------------------------------------------------------

def test_clear_recent_history_clears_the_profile_in_use(tmp_path):
    views, store, kid = _two(tmp_path)
    profiles.activate(kid.id)
    HistoryStore(profiles.pinned(views["main"]), "history_a").add("http://x/9", "B", None, "9", "live")
    kid_history = HistoryStore(profiles.pinned(views["main"]), "history_a")
    out = privacy.clear_recent(views["main"], views["resume"], kid_history, 0)
    assert out["channels"] == 1
    assert HistoryStore(profiles.pinned(views["main"], kid.id), "history_a").items() == []
    assert [e["_key"] for e in HistoryStore(profiles.pinned(views["main"], MAIN_ID),
                                            "history_a").items()] == [1]
    from dopeiptv.services.resume import ResumeStore
    assert ResumeStore(profiles.pinned(views["resume"], MAIN_ID), "a").saved_position(30, "movie") == 600


def test_home_assistant_switches_the_profile_and_sees_it():
    assert mqtt_control.parse("dope", "dope/command/profile", b"Lisa") == ("profile", "Lisa")
    assert mqtt_control.parse("dope", "dope/command/profile", b"Lisa", retained=True) is None
    from dopeiptv.core import events
    d = events.Dispatcher()
    d.set_profile("Lisa")
    d.set_state("playing", {"title": "x"})
    assert d.state["profile"] == "Lisa"
    d.set_profile(None)
    assert "profile" not in d.state


def test_the_household_list_reads_back_whatever_is_in_it(tmp_path):
    s = _ini(tmp_path / "x.ini")
    s.setValue(profiles.K_LIST, json.dumps([{"id": "me", "name": "Me"}, {"id": "me", "name": "Dup"},
                                            {"id": "a/b", "name": "bad"}, "junk",
                                            {"id": "k", "name": "Kid", "colour": "nope", "kids": 1}]))
    store = ProfileStore(s)
    assert [(p.id, p.name, p.colour, p.kids) for p in store.profiles()] == [
        ("me", "Me", "blue", False), ("k", "Kid", "blue", True)]
    s.setValue(profiles.K_LIST, "not json")
    assert ProfileStore(s).profiles() == []


# -- what the first start left under the old names ----------------------------------------

def test_what_is_cleared_or_signed_out_is_not_left_under_the_old_names(tmp_path):
    """1.5's keys stay under their old names for a downgrade (migrate): the
    history a person clears and the account they sign out of must not stay
    there - in the settings file and in every backup made after."""
    from dopeiptv.providers.simkl import SimklClient
    from dopeiptv.providers.trakt import TraktClient
    views, store, kid = _two(tmp_path)
    raw_main, raw_resume = profiles.raw(views["main"]), profiles.raw(views["resume"])
    assert json.loads(raw_main.value("history_a")) and raw_main.value("trakt_access_token") == "tok"

    def clear(seconds):
        return privacy.clear_recent(
            views["main"], views["resume"],
            HistoryStore(profiles.pinned(views["main"]), "history_a"), seconds)

    # Another profile's clearing and signing out are its own: the first's stay.
    profiles.activate(kid.id)
    clear(0)
    TraktClient(profiles.pinned(views["main"], kid.id)).disconnect()
    assert raw_main.value("history_a") == OLD_MAIN["history_a"]
    assert raw_resume.value("resume_positions_a") == OLD_RESUME["resume_positions_a"]
    assert raw_main.value("trakt_access_token") == "tok"

    # The last hour cleared: what was watched before it stays, there too.
    profiles.activate(MAIN_ID)
    clear(3600)
    assert raw_main.value("history_a") == OLD_MAIN["history_a"]

    # Everything cleared, both accounts signed out: gone under the old names.
    assert clear(0) == {"channels": 1, "titles": 0, "positions": 1}
    assert json.loads(raw_main.value("history_a")) == []
    assert json.loads(raw_resume.value("resume_positions_a")) == {}
    TraktClient(profiles.pinned(views["main"], MAIN_ID)).disconnect()
    SimklClient(profiles.pinned(views["main"], MAIN_ID))._forget()     # disconnect, nothing sent
    for key in ("trakt_access_token", "trakt_refresh_token", "trakt_expires_at",
                "simkl_access_token"):
        assert not raw_main.contains(key), key
        assert not raw_main.contains(f"profiles/{MAIN_ID}/{key}"), key
    made = json.dumps(backup.collect({"main": raw_main, "resume": raw_resume}, "1.6.0"))
    assert '"tok"' not in made and "http://x/1" not in made and "SVT1" in made
    # What was not taken out is there as it was, for a downgrade.
    assert raw_main.value("favorites_a") == OLD_MAIN["favorites_a"]
    assert raw_main.value("theme") == "nord"


def test_one_thing_taken_out_is_not_left_under_the_old_names(tmp_path):
    """The same for one thing at a time: a row removed from Recently
    viewed, a resume point removed from Continue watching, a series no
    longer followed - gone from the first profile's copy under the old
    name too; another profile's removals leave it alone."""
    from dopeiptv.core.stores import FollowedSeriesStore
    from dopeiptv.services.resume import ResumeStore
    views, store, kid = _two(tmp_path)
    raw = {name: profiles.raw(views[name]) for name in ("main", "resume", "follow")}

    def stores(pid):
        return (HistoryStore(profiles.pinned(views["main"], pid), "history_a"),
                ResumeStore(profiles.pinned(views["resume"], pid), "a"),
                FollowedSeriesStore(profiles.pinned(views["follow"], pid), "a"))

    def remove_all(pid):
        history, resume, followed = stores(pid)
        history.remove("1", "live")             # an int key in the file, a str one asked
        resume.clear("vod", 30)
        followed.unfollow(40)

    remove_all(kid.id)
    assert raw["main"].value("history_a") == OLD_MAIN["history_a"]
    assert raw["resume"].value("resume_positions_a") == OLD_RESUME["resume_positions_a"]
    assert raw["follow"].value("followed_series_a") == OLD_FOLLOW["followed_series_a"]
    # Something the old copy does not have: the copy is not even rewritten.
    HistoryStore(profiles.pinned(views["main"], MAIN_ID), "history_a").remove("9", "live")
    assert raw["main"].value("history_a") == OLD_MAIN["history_a"]

    remove_all(MAIN_ID)
    history, resume, followed = stores(MAIN_ID)
    assert history.items() == [] and resume.saved_position(30, "movie") == 0
    assert not followed.is_followed(40)
    assert json.loads(raw["main"].value("history_a")) == []
    assert json.loads(raw["resume"].value("resume_positions_a")) == {}
    assert json.loads(raw["follow"].value("followed_series_a")) == {}
    made = json.dumps(backup.collect({k: raw[k] for k in ("main", "resume")}, "1.6.1"))
    assert "http://x/1" not in made and '\\"pos\\": 600' not in made
    # What was not taken out is there as it was, for a downgrade.
    assert raw["main"].value("favorites_a") == OLD_MAIN["favorites_a"]
    assert raw["main"].value("epg_reminders_a") == OLD_MAIN["epg_reminders_a"]
