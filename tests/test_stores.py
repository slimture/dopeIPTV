"""Unit tests for dopeiptv.stores."""

from unittest.mock import MagicMock

from dopeiptv.stores import (FavoriteStore, HistoryStore, WatchedStore,
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
