"""Unit tests for dopeiptv.core.stores."""

from unittest.mock import MagicMock

from dopeiptv.core.stores import (FavoriteStore, HistoryStore, WatchedStore,
                             WatchlistStore)


def _mock_settings():
    store = {}
    s = MagicMock()
    s.value = lambda k, d="": store.get(k, d)
    s.setValue = lambda k, v: store.__setitem__(k, v)
    return s


def test_favorite_store_add_remove():
    s = _mock_settings()
    fav = FavoriteStore(s)
    item = {"stream_id": "100", "name": "CNN"}
    fav.add("default", item)
    items = fav.items("default")
    assert any(x.get("stream_id") == "100" for x in items)
    fav.remove("100")
    items = fav.items("default")
    assert not any(x.get("stream_id") == "100" for x in items)


def test_favorite_store_groups():
    s = _mock_settings()
    fav = FavoriteStore(s)
    fav.add("sports", {"stream_id": "200", "name": "ESPN"})
    fav.add("news", {"stream_id": "300", "name": "BBC"})
    assert any(x["stream_id"] == "200" for x in fav.items("sports"))
    assert not any(x["stream_id"] == "200" for x in fav.items("news"))


def test_favorite_store_group_colors():
    s = _mock_settings()
    fav = FavoriteStore(s)
    assert fav.group_color("sports") == {}
    fav.set_group_color("sports", color="#ff0000")
    fav.set_group_color("sports", bgcolor="#001122")
    assert fav.group_color("sports") == {"color": "#ff0000",
                                         "bgcolor": "#001122"}
    # Clearing both empties the entry.
    fav.set_group_color("sports", color="", bgcolor="")
    assert fav.group_color("sports") == {}


def test_favorite_store_series_id_key():
    """A series favourites store keys on series_id, not stream_id."""
    s = _mock_settings()
    fav = FavoriteStore(s, "series_favorites", id_key="series_id")
    fav.add("all", {"series_id": "42", "name": "Severance"})
    assert fav.is_favorite("42")
    assert not fav.is_favorite("99")
    fav.remove("42")
    assert not fav.is_favorite("42")


def test_watched_store_local_then_trakt_push():
    s = _mock_settings()
    w = WatchedStore(s)
    w.mark_movie_local(555)
    w.mark_episode_local(700, 1, 3)
    movies, episodes = w.pending_trakt_pushes()
    assert movies == [555]
    assert episodes == [(700, 1, 3)]
    # Promote them; they must no longer be pending and must not be lost.
    w.mark_movie_synced(555)
    w.mark_episode_synced(700, 1, 3)
    assert w.pending_trakt_pushes() == ([], [])
    assert w.is_movie_watched(555)
    assert w.is_episode_watched(700, 1, 3)


def test_watched_store_local_snapshots():
    s = _mock_settings()
    w = WatchedStore(s)
    w.add_local_item({"stream_id": "10", "name": "Dune"}, "vod", 111)
    w.add_local_item({"series_id": "20", "name": "Severance"}, "series", 222)
    items = w.local_watched_items()
    assert len(items) == 2
    # Series first (newest), then the movie.
    assert items[0]["_kind"] == "series"
    assert items[1]["_kind"] == "vod"
    # Re-adding the same movie must not duplicate it.
    w.add_local_item({"stream_id": "10", "name": "Dune"}, "vod", 111)
    assert len(w.local_watched_items()) == 2
    # Remove by tmdb id.
    w.remove_local_item("vod", 111, "10")
    kinds = [x["_kind"] for x in w.local_watched_items()]
    assert kinds == ["series"]


def test_watched_store_snapshots_persist():
    store = {}
    s = MagicMock()
    s.value = lambda k, d="": store.get(k, d)
    s.setValue = lambda k, v: store.__setitem__(k, v)
    w = WatchedStore(s)
    w.add_local_item({"stream_id": "10", "name": "Dune"}, "vod", 111)
    # A fresh store reading the same settings sees the snapshot.
    w2 = WatchedStore(s)
    assert len(w2.local_watched_items()) == 1
    assert w2.local_watched_items()[0]["name"] == "Dune"


def test_watched_store_trakt_titles_carry_and_persist():
    store = {}
    s = MagicMock()
    s.value = lambda k, d="": store.get(k, d)
    s.setValue = lambda k, v: store.__setitem__(k, v)
    w = WatchedStore(s)
    w.replace([111, 222], {333: [[1, 1]]},
              {111: "Dune (2021)", 222: "Sicario"},
              {333: "Severance (2022)"})
    assert w.trakt_title(111, "vod") == "Dune (2021)"
    assert w.trakt_title(222, "vod") == "Sicario"
    assert w.trakt_title(333, "series") == "Severance (2022)"
    # A movie id has no show title and vice-versa.
    assert w.trakt_title(111, "series") is None
    assert w.trakt_title(999, "vod") is None
    # replace() runs on the sync worker thread and deliberately doesn't write
    # QSettings; a main-thread save (any local mark) flushes the whole blob.
    # After that, a fresh store reads the titles back.
    w._save()
    w2 = WatchedStore(s)
    assert w2.trakt_title(111, "vod") == "Dune (2021)"
    assert w2.trakt_title(333, "series") == "Severance (2022)"


def test_watched_store_replace_without_titles_keeps_ids():
    s = _mock_settings()
    w = WatchedStore(s)
    w.replace([1, 2], {})
    assert w.trakt_movies == {1, 2}
    # No titles passed -> lookups just return None, no crash.
    assert w.trakt_title(1, "vod") is None


def test_watched_store_whole_show_push():
    s = _mock_settings()
    w = WatchedStore(s)
    w.mark_show_local(500)
    assert w.pending_show_pushes() == [500]
    w.mark_show_synced(500)
    assert w.pending_show_pushes() == []
    # A fresh store keeps the synced state (no re-push after restart).
    w2 = WatchedStore(s)
    assert w2.pending_show_pushes() == []
    w2.unmark_show(500)
    assert 500 not in w2.local_shows and 500 not in w2.synced_shows


def test_watched_store_stream_marks_not_pushed():
    """Stream-id-only marks have no TMDB id, so nothing to push."""
    s = _mock_settings()
    w = WatchedStore(s)
    w.mark_movie_local_by_stream(12345)
    assert w.pending_trakt_pushes() == ([], [])


def test_watchlist_store_local_then_trakt_push():
    s = _mock_settings()
    wl = WatchlistStore(s)
    wl.add_movie_local({"stream_id": "1", "name": "Dune"}, 111)
    wl.add_show_local({"series_id": "2", "name": "Severance"}, 222)
    movies, shows = wl.pending_trakt_pushes()
    assert movies == [111]
    assert shows == [222]
    wl.mark_movie_synced(111)
    wl.mark_show_synced(222)
    assert wl.pending_trakt_pushes() == ([], [])


def test_history_store_add():
    s = _mock_settings()
    hist = HistoryStore(s)
    hist.add("http://example.com/1", "CNN", None, "k1", "live")
    assert len(hist.entries) == 1
    assert hist.entries[0]["name"] == "CNN"


def test_history_store_dedup():
    s = _mock_settings()
    hist = HistoryStore(s)
    hist.add("http://example.com/1", "CNN", None, "k1", "live")
    hist.add("http://example.com/1", "CNN", None, "k1", "live")
    assert len(hist.entries) == 1


def test_history_store_dedup_survives_key_type_mismatch():
    # Providers mix int and str ids (and a key that round-tripped through
    # JSON can flip type) - the same episode must never stack up twice.
    s = _mock_settings()
    hist = HistoryStore(s)
    hist.add("http://x/series/5.mp4", "Ep", None, 5, "episode")
    hist.add("http://x/series/5.mp4", "Ep", None, "5", "episode")
    assert len(hist.entries) == 1


def test_history_store_keeps_series_context_extra():
    # The episode's series snapshot rides along via extra, and a name
    # override there replaces the bare "S01 E01" with "Series · S01 E01".
    s = _mock_settings()
    hist = HistoryStore(s)
    hist.add("http://x/series/5.mp4", "S1 * E1 - Pilot", None, 5, "episode",
             extra={"_series_ctx": {"series_id": 88, "name": "Severance"},
                    "_series_title": "Severance",
                    "name": "Severance · S1 * E1 - Pilot"})
    e = hist.entries[0]
    assert e["_series_ctx"]["series_id"] == 88
    assert e["_series_title"] == "Severance"
    assert e["name"] == "Severance · S1 * E1 - Pilot"


def test_a_trakt_sync_survives_a_restart(tmp_path):
    """A completed Trakt sync must reach disk.

    WatchedStore.replace() rebuilds the whole Trakt layer from a fresh sync
    payload - and its _save() had drifted below the `return` of the method
    underneath it, where it could never run. The sync updated memory only:
    every watched mark from Trakt was gone on the next start, and last_sync_at
    never stuck either, so the app re-synced on every launch.
    """
    from PyQt6.QtCore import QSettings

    from dopeiptv.core.stores import WatchedStore

    s = QSettings("dopeiptv-test", "watched-persist")
    s.clear()
    store = WatchedStore(s)
    store.replace([11, 22], {77: [[1, 2], [1, 3]]},
                  movie_titles={11: "A Film"}, show_titles={77: "A Show"})
    assert store.last_sync_at > 0
    s.sync()

    fresh = WatchedStore(QSettings("dopeiptv-test", "watched-persist"))
    assert 11 in fresh.trakt_movies and 22 in fresh.trakt_movies
    assert fresh.trakt_episodes.get(77) == {(1, 2), (1, 3)}
    assert fresh.trakt_title(11, "movie") == "A Film"
    assert fresh.trakt_title(77, "series") == "A Show"
    assert fresh.last_sync_at == store.last_sync_at


def test_history_remove_matches_int_and_str_keys():
    # add() compares keys as strings; remove() compared them raw, so a row
    # stored with key 42 survived remove("42").
    h = HistoryStore(_mock_settings())
    h.add("http://x/1", "One", None, 42, "movie")
    h.remove("42", "movie")
    assert h.items() == []


def test_favorite_remove_from_a_missing_folder_creates_nothing():
    fav = FavoriteStore(_mock_settings())
    fav.add("all", {"stream_id": 1})
    fav.remove(1, "No such folder")
    assert "No such folder" not in fav.groups


def test_watched_clear_forgets_whole_show_marks():
    w = WatchedStore(_mock_settings())
    w.local_shows.add(5)
    w.synced_shows.add(5)
    w.clear()
    assert not w.local_shows and not w.synced_shows


def test_a_malformed_watched_cache_does_not_break_startup():
    import json
    for data in (
            [1, 2],
            {"trakt_episodes": {"7": [[1, 2], [3], "x", [1, 2, 3]]},
             "local_movies": 5, "local_items": {"a": 1},
             "trakt_movie_titles": {"abc": "x", "9": "Nine"},
             "last_sync_at": "yesterday"},
            {"trakt_episodes": [1], "trakt_show_titles": [1],
             "synced_shows": None}):
        s = _mock_settings()
        s.setValue("trakt_watched_cache", json.dumps(data))
        s.setValue("trakt_watchlist_cache", json.dumps(data))
        w = WatchedStore(s)
        WatchlistStore(s)
        if isinstance(data, dict) and "local_movies" in data:
            assert w.episodes_for(7) == {(1, 2)}
            assert w.trakt_movie_titles == {9: "Nine"}
            assert w.last_sync_at == 0


def test_renaming_a_folder_onto_another_merges_without_duplicates():
    fav = FavoriteStore(_mock_settings())
    fav.add("Sport", {"stream_id": 1, "name": "One"})
    fav.add("Sport", {"stream_id": 2, "name": "Two"})
    fav.add("News", {"stream_id": 2, "name": "Two"})
    fav.add("News", {"stream_id": 3, "name": "Three"})
    fav.rename_group("Sport", "News")
    assert "Sport" not in fav.groups
    assert [x["stream_id"] for x in fav.items("News")] == [2, 3, 1]
    # A reorder of the merged folder used to run out of rows and crash.
    fav.reorder("News", [1, 2, 3])
    assert [x["stream_id"] for x in fav.items("News")] == [1, 2, 3]


def test_reorder_survives_a_folder_already_holding_duplicates():
    s = _mock_settings()
    s.setValue("favorites", '{"F": [{"stream_id": 1}, {"stream_id": 2},'
                            ' {"stream_id": 1}]}')
    fav = FavoriteStore(s)
    fav.reorder("F", [2, 1])
    assert [x["stream_id"] for x in fav.items("F")] == [2, 1]


def test_a_folder_keeps_its_colour_through_a_rename_and_loses_it_on_delete():
    fav = FavoriteStore(_mock_settings())
    fav.add("Kids", {"stream_id": 7})
    fav.set_group_color("Kids", color="#ff0000")
    fav.rename_group("Kids", "Children")
    assert fav.group_color("Children") == {"color": "#ff0000"}
    assert fav.group_color("Kids") == {}
    fav.remove_group("Children")
    fav.ensure_group("Children")
    assert fav.group_color("Children") == {}


def test_playlists_can_be_reordered():
    from dopeiptv.core.stores import PlaylistStore
    s = _mock_settings()
    s.setValue("playlists", '[{"id": "a"}, {"id": "b"}, {"id": "c"}, 7]')
    store = PlaylistStore(s)
    assert [p["id"] for p in store.playlists()] == ["a", "b", "c"]
    store.reorder(["c", "a", "zz", "c"])
    assert [p["id"] for p in store.playlists()] == ["c", "a", "b"]
    # Persisted, so the menu and the next start show the same order.
    assert [p["id"] for p in PlaylistStore(s).playlists()] == ["c", "a", "b"]


def test_an_export_brings_m3u_playlists_back():
    """The kind was not exported and the import wanted a user name and a
    password - so every M3U playlist was silently lost on the way."""
    from dopeiptv.core.stores import PlaylistStore
    src = PlaylistStore(_mock_settings())
    src.add({"name": "Xt", "kind": "xtream", "server": "http://x.tv",
             "username": "u", "password": "p", "epg_url": "",
             "refresh": "24h"})
    src.add({"name": "Free", "kind": "m3u",
             "server": "https://iptv-org.github.io/iptv/index.m3u",
             "username": "", "password": "", "epg_url": "",
             "refresh": "never"})
    src.add({"name": "Old", "server": "http://old.tv", "username": "a",
             "password": "b"})                    # stored before kinds
    data = src.export_data()
    assert [p["kind"] for p in data] == ["xtream", "m3u", "xtream"]

    dst = PlaylistStore(_mock_settings())
    assert dst.import_data(data) == 3
    assert [(p["name"], p["kind"]) for p in dst.playlists()] == [
        ("Xt", "xtream"), ("Free", "m3u"), ("Old", "xtream")]
    assert dst.playlists()[0]["refresh"] == "24h"
    # The same file twice adds nothing new.
    assert dst.import_data(data) == 0


def test_an_import_skips_what_it_cannot_use():
    import pytest

    from dopeiptv.core.stores import PlaylistStore
    store = PlaylistStore(_mock_settings())
    with pytest.raises(ValueError):
        store.import_data({"name": "not a list"})
    assert store.import_data([
        "junk", {"server": "http://x.tv"},           # Xtream without login
        {"kind": "m3u", "server": ""},               # M3U without address
        {"server": ["bad"], "username": 1, "password": None},
        {"kind": "m3u", "server": " http://ok/list.m3u "}]) == 1
    assert store.playlists()[0]["server"] == "http://ok/list.m3u"
    assert store.playlists()[0]["name"] == "ok"
