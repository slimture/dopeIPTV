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

import pytest

pytest.importorskip("PyQt6.QtCore")
from PyQt6.QtCore import QSettings  # noqa: E402

from dopeiptv.core import backup, mqtt_control, privacy, profiles, sync  # noqa: E402
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
    "home_sh_resume": "true", "home_sh_pop_movies": "false",
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
                "simkl_access_token", "recent_searches", "theme", "accent", "audio_lang",
                "sub_lang", "sub_lang2", "sub_mode", "home_sh_resume", "track_prefs"):
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
    assert "2468" not in (profiles.raw(views["main"]).value(profiles.K_LIST) or "")
    assert store.grown_up_pin("2468") and not store.grown_up_pin("0000")
    store.set_pin(kid.id, "1111")
    assert not store.grown_up_pin("1111")         # a kids profile's PIN opens no Settings
    assert store.get(MAIN_ID).public().get("pin") is True
    assert "pin_hash" not in store.get(MAIN_ID).public()
    store.clear_pin(MAIN_ID)
    assert not store.get(MAIN_ID).has_pin


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


def _ctx(main, resume, playlists, reg):
    """As the window makes it (mw_sync._sync_ctx)."""
    ctx = sync.Context(profiles.pinned(main, MAIN_ID), profiles.pinned(resume, MAIN_ID),
                       playlists, registry=reg,
                       pin=lambda pid: (profiles.pinned(main, pid), profiles.pinned(resume, pid)))
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
