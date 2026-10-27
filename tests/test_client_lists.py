"""A big provider's lists without stalling the window: parsed with a Python
call per object (the interpreter is free between objects), kept on disk a
file per list - the reply's own bytes, nothing re-encoded - and read back
only when one is going to be used."""
import json
import os
import time

import pytest

from dopeiptv.core._lazy_requests import requests
from dopeiptv.providers import client as client_mod
from dopeiptv.providers.client import XtreamClient, json_object

ROWS = [{"stream_id": i, "name": f"Kanal {i} – ÅÄÖ \"x\"", "rating": 7.5,
         "tv_archive": i % 2 == 0, "icon": None, "ids": [i, i + 1]}
        for i in range(50)]


class _Reply:
    status_code = 200
    headers: dict = {}

    def __init__(self, body: bytes, encoding: str | None = "utf-8"):
        self.content = body
        self.encoding = encoding

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding or "utf-8", "replace")

    def raise_for_status(self):
        pass

    def json(self, **kw):
        return json.loads(self.text, **kw)


def _client(tmp_path, body: bytes | None = None, encoding="utf-8"):
    c = XtreamClient("http://h.tv", "u", "p",
                     cache_path=str(tmp_path / "lists_p1.json"))
    calls = []

    def get(*a, **k):
        calls.append(k.get("params", {}).get("action"))
        if body is None:
            raise requests.ConnectionError("down")
        return _Reply(body, encoding)
    c.session.get = get
    return c, calls


def _down():
    raise requests.ConnectionError("down")


def test_parsing_with_the_hook_gives_what_json_gives():
    text = json.dumps({"a": ROWS, "b": [[{"x": {"y": 1}}]], "c": {}})
    got = json.loads(text, object_pairs_hook=json_object)
    assert got == json.loads(text)
    rows = got["a"]
    # One key object for all rows, as json.loads() keeps them.
    assert next(iter(rows[0])) is next(iter(rows[1]))


def test_provider_answers_go_through_the_hook(tmp_path, monkeypatch):
    calls = []

    def counting(pairs):
        calls.append(1)
        return dict(pairs)
    monkeypatch.setattr(client_mod, "json_object", counting)
    c, _ = _client(tmp_path, json.dumps(ROWS).encode())
    assert c._api(action="get_live_streams") == ROWS
    assert len(calls) == len(ROWS)


def test_a_list_is_kept_as_the_provider_sent_it(tmp_path):
    body = (b'[ {"stream_id": 1, "name": "Kanal \xc3\x85"},\n'
            b'  {"stream_id": 2,"name":"B"} ]')
    c, _ = _client(tmp_path, body)
    assert c.live_streams()[0]["name"] == "Kanal Å"
    path = c._list_file(("get_live_streams", None))
    with open(path, "rb") as fh:
        assert fh.read() == body                   # not re-encoded
    assert not [p for p in os.listdir(os.path.dirname(path))
                if p.endswith(".part")]


def test_a_reply_read_in_another_charset_is_kept_as_it_was_read(tmp_path):
    body = '[{"stream_id": 1, "name": "Caf\xe9"}]'.encode("latin-1")
    c, _ = _client(tmp_path, body, encoding="ISO-8859-1")
    first = c.live_streams()
    again, _ = _client(tmp_path)                  # provider down
    assert again.live_streams() == first == [{"stream_id": 1, "name": "Café"}]


def test_a_new_start_within_minutes_uses_the_copy_on_disk(tmp_path,
                                                         monkeypatch):
    c, calls = _client(tmp_path, json.dumps(ROWS).encode())
    assert c.live_streams() == ROWS
    assert calls == ["get_live_streams"]
    again, calls2 = _client(tmp_path, b"[]")
    assert again.live_streams() == ROWS
    assert calls2 == []                           # fresh: no request
    # Older than the cache time: fetched, and the old copy left unread.
    path = again._list_file(("get_live_streams", None))
    old = time.time() - XtreamClient.LIST_CACHE_SECS - 60
    os.utime(path, (old, old))
    third, calls3 = _client(tmp_path, json.dumps(ROWS[:2]).encode())
    reads = []

    def spy(file, mode="r", *a, **k):
        if "r" in mode:
            reads.append(file)
        return open(file, mode, *a, **k)
    monkeypatch.setattr(client_mod, "open", spy, raising=False)
    assert third.live_streams() == ROWS[:2]
    assert reads == []
    assert calls3 == ["get_live_streams"]


def test_a_provider_that_is_down_gets_the_copy_on_disk(tmp_path):
    c, _ = _client(tmp_path, json.dumps(ROWS).encode())
    c.live_streams("7")
    path = c._list_file(("get_live_streams", "7"))
    old = time.time() - 3 * 86400
    os.utime(path, (old, old))
    down, _ = _client(tmp_path)
    assert down.live_streams("7") == ROWS
    with pytest.raises(requests.ConnectionError):
        down.vod_streams()                        # never kept: nothing


def test_only_the_list_asked_for_is_read(tmp_path):
    c, _ = _client(tmp_path, json.dumps(ROWS).encode())
    c.live_streams()
    c.vod_streams()
    c.series_list()
    again, _ = _client(tmp_path, b"[]")
    again.live_streams()
    assert list(again._list_cache) == [("get_live_streams", None)]


def test_the_single_file_of_older_versions_still_stands_in(tmp_path):
    legacy = tmp_path / "lists_p1.json"
    legacy.write_text(json.dumps(
        {"get_live_streams\x1f": [time.time() - 86400, ROWS]}))
    down, _ = _client(tmp_path)
    assert down.live_streams() == ROWS


def test_the_single_file_goes_once_it_is_old(tmp_path):
    legacy = tmp_path / "lists_p1.json"
    legacy.write_text("{}")
    c, _ = _client(tmp_path, b"[]")
    c.live_categories()
    assert legacy.exists()                        # recent: still a fallback
    old = time.time() - XtreamClient.SINGLE_FILE_KEEP_SECS - 60
    os.utime(legacy, (old, old))
    c2, _ = _client(tmp_path, b"[]")
    c2.vod_categories()
    assert not legacy.exists()


def test_a_refresh_keeps_the_lists_without_reading_the_disk(tmp_path):
    c, calls = _client(tmp_path, json.dumps(ROWS).encode())
    key = ("get_live_streams", None)
    assert c._cached_list(key, lambda: ROWS) == ROWS
    c.clear_list_cache()
    fetched = []
    assert c._cached_list(key, lambda: fetched.append(1) or ROWS[:2]) \
        == ROWS[:2]
    assert fetched == [1]       # a refresh re-fetches
    c.clear_list_cache()
    os.unlink(c._list_file(key))
    # The provider down: the list in memory stands in.
    assert c._cached_list(key, _down) == ROWS[:2]


def test_lists_of_a_removed_playlist_are_pruned(tmp_path, monkeypatch):
    from dopeiptv.providers import epg
    monkeypatch.setattr(epg, "_epg_cache_dir", lambda: tmp_path)
    for pid in ("keep", "gone"):
        (tmp_path / f"lists_{pid}.json").write_text("{}")
        (tmp_path / f"lists_{pid}.d").mkdir()
        (tmp_path / f"lists_{pid}.d" / "a.json").write_text("[]")
    assert epg.prune_epg_caches(["keep"]) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "lists_keep.d", "lists_keep.json"]
