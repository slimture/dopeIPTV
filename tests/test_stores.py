"""Unit tests for dopeiptv.stores."""

from unittest.mock import MagicMock

from dopeiptv.stores import FavoriteStore, HistoryStore


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
