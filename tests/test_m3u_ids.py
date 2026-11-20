"""Stable channel ids for M3U sources (core/m3u_ids.py, M3UClient): a
channel's id comes from the channel, not its place in the list, and what was
stored by place moves over at the start after the list was read."""
from __future__ import annotations

import json
import os

import pytest

pytest.importorskip("PyQt6.QtCore")

from PyQt6.QtCore import QSettings  # noqa: E402

from dopeiptv.core import m3u_ids  # noqa: E402
from dopeiptv.providers.client import M3UClient, make_client  # noqa: E402


def _m3u(*names: str, tvg: dict | None = None) -> str:
    lines = ["#EXTM3U"]
    for n in names:
        tid = (tvg or {}).get(n, "")
        lines.append(f'#EXTINF:-1 tvg-id="{tid}" group-title="News",{n}')
        lines.append(f"http://iptv.example/{n.lower().replace(' ', '')}.ts")
    return "\n".join(lines) + "\n"


def _client(text: str, **kw) -> M3UClient:
    c = M3UClient("http://iptv.example/list.m3u", **kw)
    c._parse(text)
    return c


def _ids(c: M3UClient) -> dict[str, int]:
    return {ch["name"]: ch["stream_id"] for ch in c._channels}


@pytest.fixture(autouse=True)
def _clean():
    yield
    m3u_ids._STABLE.clear()


def test_a_channel_keeps_its_id_when_the_list_changes_around_it():
    before = _ids(_client(_m3u("Alpha", "Bravo", "Charlie"), stable=True))
    after = _ids(_client(_m3u("New", "Charlie", "Alpha", "Bravo"), stable=True))
    assert {k: after[k] for k in before} == before
    assert all(m3u_ids.LINE_MAX <= v < 2 ** 31 for v in after.values())
    assert len(set(after.values())) == 4


def _probed_one_by_one(keys):
    """stable_ids as it was: each taken id tried in turn."""
    import zlib
    taken, seen, out = set(), {}, []
    for key in keys:
        n = seen.get(key, 0)
        seen[key] = n + 1
        sid = m3u_ids.LINE_MAX | (zlib.crc32(f"{key}\x1f{n}".encode()) & (m3u_ids.LINE_MAX - 1))
        while sid in taken:
            sid = m3u_ids.LINE_MAX | ((sid + 1) & (m3u_ids.LINE_MAX - 1))
        taken.add(sid)
        out.append(sid)
    return out


def test_ids_that_hash_alike_are_found_at_once_and_stay_the_same(monkeypatch):
    """A list made to hash alike took a step for every channel before it,
    each time: quadratic, minutes for a large list. The free id is found
    at once now - and it is the id it always was."""
    import random
    import time
    import types
    keys = [m3u_ids.channel_key("", f"Ch {i % 700}") for i in range(3000)]
    assert m3u_ids.stable_ids(keys) == _probed_one_by_one(keys)
    rnd = random.Random(7)
    hashes = {}

    def clustered(data):
        # Few distinct hashes, close together: long runs of taken ids,
        # one wrapping past the top of the range.
        return hashes.setdefault(data, rnd.choice((5, 9, 40, m3u_ids.LINE_MAX - 3)))
    fake = types.SimpleNamespace(crc32=clustered)
    monkeypatch.setattr(m3u_ids, "zlib", fake)
    import zlib as real
    monkeypatch.setattr(real, "crc32", clustered)
    small = [f"k{i}" for i in range(2000)]
    assert m3u_ids.stable_ids(small) == _probed_one_by_one(small)
    monkeypatch.setattr(m3u_ids, "zlib", types.SimpleNamespace(crc32=lambda data: 12345))
    t0 = time.perf_counter()
    ids = m3u_ids.stable_ids(f"same {i}" for i in range(200_000))
    assert time.perf_counter() - t0 < 5.0
    assert len(set(ids)) == 200_000 and ids[:3] == [m3u_ids.LINE_MAX | n for n in (12345, 12346, 12347)]


def test_the_same_name_twice_is_two_channels_told_apart_by_turn():
    c = _client(_m3u("Sport", "News", "Sport"), stable=True)
    a, b = c._channels[0]["stream_id"], c._channels[2]["stream_id"]
    assert a != b
    again = _client(_m3u("Extra", "Sport", "News", "Sport"), stable=True)
    sport = [ch["stream_id"] for ch in again._channels if ch["name"] == "Sport"]
    assert sport == [a, b]


def test_the_guide_id_tells_channels_of_one_name_apart():
    c = _client(_m3u("SVT1", tvg={"SVT1": "svt1.se"}), stable=True)
    d = _client(_m3u("SVT1", tvg={"SVT1": "svt1.fi"}), stable=True)
    assert c._channels[0]["stream_id"] != d._channels[0]["stream_id"]


def test_ids_that_hash_alike_take_the_next_free_one(monkeypatch):
    monkeypatch.setattr(m3u_ids.zlib, "crc32", lambda b: 7)
    got = m3u_ids.stable_ids(["a", "b", "c"])
    assert got == [m3u_ids.LINE_MAX | 7, m3u_ids.LINE_MAX | 8, m3u_ids.LINE_MAX | 9]


def test_by_place_until_moved_and_the_map_written(tmp_path):
    path = str(tmp_path / "m3u_ids_p1.json")
    c = _client(_m3u("Alpha", "Bravo"), map_path=path)
    assert _ids(c) == {"Alpha": 1, "Bravo": 2}
    remap = m3u_ids.load_map(path)
    stable = _ids(_client(_m3u("Alpha", "Bravo"), stable=True))
    assert remap is not None and remap.by_line == {1: stable["Alpha"], 2: stable["Bravo"]}
    assert c._channels[0]["num"] == 1


def test_an_old_id_still_plays_the_channel_at_its_place():
    c = _client(_m3u("Alpha", "Bravo"), stable=True)
    assert c.live_url(2) == "http://iptv.example/bravo.ts"
    assert c.live_url(_ids(c)["Bravo"]) == "http://iptv.example/bravo.ts"
    assert [ch["num"] for ch in c._channels] == [1, 2]      # numbers as before


def test_read_by_place_a_stable_id_from_another_computer_plays_too():
    stable = _ids(_client(_m3u("Alpha", "Bravo"), stable=True))
    c = _client(_m3u("Alpha", "Bravo"))
    assert c.live_url(stable["Bravo"]) == "http://iptv.example/bravo.ts"


def test_make_client_reads_a_moved_source_with_stable_ids(tmp_path, monkeypatch):
    from dopeiptv.providers import client as client_mod
    monkeypatch.setattr(client_mod, "_list_cache_path",
                        lambda pl: str(tmp_path / f"lists_{pl['id']}.json"))
    pl = {"id": "p1", "kind": "m3u", "server": "http://iptv.example/list.m3u"}
    c = make_client(pl)
    assert not c.stable and c.map_path == str(tmp_path / "m3u_ids_p1.json")
    m3u_ids._STABLE.add("p1")
    c = make_client(pl)
    assert c.stable and not c.map_path


def test_the_new_id_by_address_then_name_then_place():
    rows = [(1, 1001 | m3u_ids.LINE_MAX, "Alpha", "http://a"),
            (2, 1002 | m3u_ids.LINE_MAX, "Bravo", "http://b"),
            (3, 1003 | m3u_ids.LINE_MAX, "Charlie", "http://c")]
    m = m3u_ids.Remap.of(rows)
    A, B, C = (r[1] for r in rows)
    assert m.new_id(1) == A                                   # place alone
    assert m.new_id(1, "Bravo") == B                          # its name wins
    assert m.new_id(2, " bravo ") == B                        # the name at the place
    assert m.new_id(1, "Bravo", "http://c") == C              # the address over both
    assert m.new_id(3, "Gone") == C                           # a name not there: place
    assert m.new_id(9) is None                                # past the list
    assert m.new_id(A) is None and m.new_id("x") is None      # not an id by place
    assert m3u_ids.line_id(True) is None and m3u_ids.line_id("2") == 2


def _settings(tmp_path) -> QSettings:
    return QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)


def _put(s: QSettings, key: str, value) -> None:
    s.setValue(key, json.dumps(value))


def _get(s: QSettings, key: str):
    return json.loads(s.value(key))


def test_everything_stored_by_place_moves_in_every_profile(tmp_path):
    rows = [(1, m3u_ids.LINE_MAX + 11, "Alpha", "http://a"),
            (2, m3u_ids.LINE_MAX + 22, "Bravo", "http://b")]
    A, B = rows[0][1], rows[1][1]
    remap = m3u_ids.Remap.of(rows)
    s = _settings(tmp_path)
    _put(s, "profiles/me/favorites_p1",
         {"__default__": [{"stream_id": 1, "name": "Bravo"}], "Kids": [{"stream_id": 2}]})
    _put(s, "profiles/kid/favorites_p1", {"__default__": [{"stream_id": 2, "name": "Bravo"}]})
    _put(s, "profiles/me/history_p1", [
        {"_kind": "live", "stream_id": 2, "_key": "2", "name": "Bravo", "_url": "http://b"},
        {"_kind": "movie", "stream_id": 2, "_key": "2", "name": "A film"}])
    _put(s, "channel_overrides_p1", {"live": {"1": {"hidden": True}, "2": {"name": "B!"}},
                                     "vod": {"1": {"hidden": True}}})
    _put(s, "nerd_chno_p1", {"1": 101, "2": 102})
    _put(s, "nerd_epg_map_p1", {"2": "bravo.se"})
    _put(s, "profiles/me/epg_reminders_p1", [{"stream_id": 2, "name": "Bravo", "start": 5}])
    _put(s, "series_rules_p1", {"rules": [{"id": "r", "channel": {"stream_id": 1, "name": "Alpha"}}],
                                "taken": []})
    _put(s, "nerd_channel_prefs", {"p1:2": {"audio": "sv"}, "p2:2": {"audio": "fi"}})
    _put(s, "nerd_health_data", {"p1:1": {"starts": 3}, "p9:1": {"starts": 1}})
    _put(s, "recording_jobs", [{"source": "p1", "stream_id": 2, "url": "http://b"},
                               {"source": "p2", "stream_id": 2, "url": "http://x"}])
    _put(s, "favorites_p2", {"__default__": [{"stream_id": 1}]})
    _put(s, "profiles/me/viewing_stats", {"t": {"live:p1:1": {"n": "Bravo", "h": {}},
                                                "movie:p1:1": {"n": "A film", "h": {}}}})

    found, moved = m3u_ids.migrate(s, "p1", remap)
    assert found == moved == 16
    assert _get(s, "profiles/me/favorites_p1") == {
        "__default__": [{"stream_id": B, "name": "Bravo"}], "Kids": [{"stream_id": B}]}
    assert _get(s, "profiles/kid/favorites_p1")["__default__"][0]["stream_id"] == B
    hist = _get(s, "profiles/me/history_p1")
    assert (hist[0]["stream_id"], hist[0]["_key"]) == (B, str(B))
    assert (hist[1]["stream_id"], hist[1]["_key"]) == (2, "2")    # a film is no channel
    ov = _get(s, "channel_overrides_p1")
    assert ov["live"] == {str(A): {"hidden": True}, str(B): {"name": "B!"}}
    assert ov["vod"] == {"1": {"hidden": True}}
    assert _get(s, "nerd_chno_p1") == {str(A): 101, str(B): 102}
    assert _get(s, "nerd_epg_map_p1") == {str(B): "bravo.se"}
    assert _get(s, "profiles/me/epg_reminders_p1")[0]["stream_id"] == B
    assert _get(s, "series_rules_p1")["rules"][0]["channel"]["stream_id"] == A
    assert _get(s, "nerd_channel_prefs") == {f"p1:{B}": {"audio": "sv"}, "p2:2": {"audio": "fi"}}
    assert _get(s, "nerd_health_data") == {f"p1:{A}": {"starts": 3}, "p9:1": {"starts": 1}}
    jobs = _get(s, "recording_jobs")
    assert (jobs[0]["stream_id"], jobs[1]["stream_id"]) == (B, 2)
    assert _get(s, "favorites_p2") == {"__default__": [{"stream_id": 1}]}
    assert set(_get(s, "profiles/me/viewing_stats")["t"]) == {f"live:p1:{B}", "movie:p1:1"}

    assert m3u_ids.migrate(s, "p1", remap) == (0, 0)          # moved once, never again


def test_a_moved_key_never_takes_the_place_of_one_kept_already(tmp_path):
    B = m3u_ids.LINE_MAX + 22
    remap = m3u_ids.Remap.of([(2, B, "Bravo", "")])
    s = _settings(tmp_path)
    _put(s, "nerd_chno_p1", {"2": 7, str(B): 9})
    m3u_ids.migrate(s, "p1", remap)
    assert _get(s, "nerd_chno_p1") == {str(B): 9}


def test_start_moves_what_it_can_and_waits_for_the_rest(tmp_path):
    cache = str(tmp_path)
    s = _settings(tmp_path)
    sources = [{"id": "p1", "kind": "m3u"}, {"id": "p2", "kind": "m3u"},
               {"id": "p3", "kind": "m3u"}, {"id": "x1", "kind": "xtream"}]
    # p1: read last run, its map there; p2: stored by place, no map yet;
    # p3: nothing stored by place (new) - stable at once.
    rows = [(1, m3u_ids.LINE_MAX + 5, "Alpha", "")]
    m3u_ids.save_map(m3u_ids.map_path(cache, "p1"), rows)
    _put(s, "favorites_p1", {"__default__": [{"stream_id": 1}]})
    _put(s, "favorites_p2", {"__default__": [{"stream_id": 1}]})
    _put(s, "favorites_x1", {"__default__": [{"stream_id": 1}]})
    m3u_ids.start(s, sources, cache)
    assert m3u_ids.is_stable("p1") and m3u_ids.is_stable("p3")
    assert not m3u_ids.is_stable("p2") and not m3u_ids.is_stable("x1")
    assert _get(s, "favorites_p1")["__default__"][0]["stream_id"] == m3u_ids.LINE_MAX + 5
    assert _get(s, "favorites_p2")["__default__"][0]["stream_id"] == 1
    assert _get(s, "favorites_x1")["__default__"][0]["stream_id"] == 1
    assert not os.path.exists(m3u_ids.map_path(cache, "p1"))
    assert s.value("m3u_stable_p1") and not s.value("m3u_stable_p2")
    m3u_ids._STABLE.clear()
    m3u_ids.start(s, sources, cache)                          # the next start: the mark
    assert m3u_ids.is_stable("p1") and not m3u_ids.is_stable("p2")


def test_a_favourite_survives_a_channel_added_at_the_top(tmp_path):
    """Fable's case: Bravo is a favourite, the provider adds a channel at
    the top - the star stayed on the second place, now Alpha."""
    from dopeiptv.core.stores import FavoriteStore
    cache = str(tmp_path)
    s = _settings(tmp_path)
    pid = "p1"
    first = _client(_m3u("Alpha", "Bravo", "Charlie"),
                    map_path=m3u_ids.map_path(cache, pid))   # the update's first run
    bravo = next(ch for ch in first._channels if ch["name"] == "Bravo")
    favs = FavoriteStore(s, f"favorites_{pid}")
    favs.add("__default__", dict(bravo))
    m3u_ids.start(s, [{"id": pid, "kind": "m3u"}], cache)     # the next start
    later = _client(_m3u("Zulu", "Alpha", "Bravo", "Charlie"), stable=m3u_ids.is_stable(pid))
    kept = FavoriteStore(s, f"favorites_{pid}").groups["__default__"][0]["stream_id"]
    assert later._channel(kept)["name"] == "Bravo"
    assert later.live_url(kept) == "http://iptv.example/bravo.ts"
