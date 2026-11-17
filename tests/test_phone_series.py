"""The phone remote while an episode plays, and everything else the app
plays: Now playing names the series and "S2 E5 · Title", previous and
next go through the window's own episode path (without the resume
question), the Episodes view comes from a cached endpoint built on change
only (and a 304 for the same again), its episodes play through the series
page's path; films, series, Recent, local files, radio and recordings are
listed a page at a time from caches built off the window's thread, with
what the computer leaves out left out; the page's CSP still holds its own
hashes."""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import re
import threading
import time
from types import SimpleNamespace

import pytest

from dopeiptv.core import phone_browse as pb
from dopeiptv.core import phone_page
from dopeiptv.core.phone_remote import PhoneServer

TOKEN = "Ab3_-x9Ab3_-x9Ab3_-x9Ab3_-x9Qz12"


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)


def ask(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    data = json.dumps(body).encode() if body is not None else None
    conn.request(method, path, body=data, headers=dict(headers or {}, **(
        {"Content-Type": "application/json"} if data else {})))
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, raw


def _sha(text):
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


# -- the pure part ---------------------------------------------------------------------------

def test_a_list_is_built_once_per_version_and_paged():
    clock = [0.0]
    lists = pb.Lists(ttl=60, clock=lambda: clock[0])
    built = []

    def build():
        built.append(1)
        return [{"name": f"Film {i}"} for i in range(150)]
    rows, fresh = lists.get(("vod", 1), "v1", build)
    assert fresh and len(built) == 1
    assert lists.get(("vod", 1), "v1", build) == (rows, False) and len(built) == 1
    lists.get(("vod", 1), "v2", build)              # another version: built again
    clock[0] = 61
    lists.get(("vod", 1), "v2", build)              # too old: built again
    assert len(built) == 3
    page = pb.paged(rows, 0)
    assert len(page["items"]) == pb.PAGE and page["next"] == pb.PAGE and page["total"] == 150
    last = pb.paged(rows, 120)
    assert len(last["items"]) == 30 and last["next"] is None
    assert pb.paged(rows, 10 ** 9)["items"] == []


def test_two_phones_asking_at_once_build_it_once():
    lists = pb.Lists()
    gate = threading.Event()
    built = []

    def build():
        built.append(1)
        gate.wait(2)
        return ["x"]
    threads = [threading.Thread(target=lists.get, args=(("k",), 1, build)) for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in threads:
        t.join(5)
    assert len(built) == 1


def test_ids_are_opaque_stable_and_only_ours():
    refs = pb.Refs(cap=3)
    a = refs.put("local", "/srv/films/a.mkv", ("/srv/films/a.mkv", []))
    assert a == refs.put("local", "/srv/films/a.mkv", ("/srv/films/a.mkv", []))
    assert "/" not in a and pb.ref_of(a) == a and refs.get(a)[0] == "local"
    assert pb.ref_of("../../etc/passwd") == "" and pb.ref_of("x" * 16) == ""
    for i in range(4):
        refs.put("movie", i, {})
    assert refs.get(a) is None                       # the oldest forgotten past the cap
    assert pb.clean_path("vod/abc123") == "vod/abc123"
    assert pb.clean_path("local/../../etc") == "" and pb.clean_path("vod/<x>") == ""
    assert pb.query_of("  two   words ") == "two words" and len(pb.query_of("q" * 500)) == pb.MAX_QUERY
    assert pb.offset_of("-5") == 0 and pb.offset_of("x") == 0 and pb.offset_of("120") == 120


def test_the_episodes_view_lines_specials_last_and_the_season_it_opens_on():
    from dopeiptv.core import series_view as sv
    eps = [{"id": str(s * 100 + e), "season": s, "episode_num": e, "title": f"Show - S0{s}E0{e} Title {e}",
            "info": {"duration_secs": 2700}} for s in (0, 2, 1) for e in (1, 2)]
    seasons = sv.seasons(eps)
    assert [s.number for s in seasons] == [1, 2, 0]
    states = {"201": (False, 900.0, 2700.0), "101": (True, 0.0, 0.0)}
    shown = pb.episodes(seasons, "Show", ["Season 1", "Season 2", "Specials"],
                        lambda e: f"E{e['episode_num']}", states, lambda e: "r" + e["id"])
    assert [s["label"] for s in shown] == ["Season 1", "Season 2", "Specials"] and shown[2]["specials"]
    first = shown[1]["episodes"][0]
    assert first == {"ref": "r201", "key": "201", "n": "E1", "title": "Title 1", "min": 45,
                     "watched": False, "pct": 33}
    assert shown[0]["episodes"][0]["watched"] and shown[0]["episodes"][0]["pct"] == 0
    assert pb.opening(shown, "202", "101") == 1 and pb.opening(shown, "", "101") == 0
    assert pb.opening(shown, "nope", "") == 0


# -- the window's side -----------------------------------------------------------------------

class Bridge:
    """The window's thread, as the server's side sees it: what was asked
    there, and for how long."""

    def __init__(self, do):
        self.do, self.asked, self.inside = do, [], False
        self.spent = 0.0

    def call(self, action, params):
        self.asked.append(action)
        self.inside = True
        t = time.perf_counter()
        try:
            return self.do(action, params)
        finally:
            self.spent += time.perf_counter() - t
            self.inside = False


class Overrides:
    def __init__(self):
        self.data = {"vod": {"7": {"hidden": True}, "8": {"locked": True}}, "series": {}}

    def excluded_ids(self, mode, include_locked=True):
        return {cid for cid, e in self.data.get(mode, {}).items()
                if e.get("hidden") or (include_locked and e.get("locked"))}

    def is_locked(self, mode, cid):
        return bool(self.data.get(mode, {}).get(str(cid), {}).get("locked"))


class Client:
    """A source: films and series by category, a series' episodes."""

    def __init__(self):
        self.info_asked = []
        self.lists_asked = []

    def vod_categories(self):
        return [{"category_id": c, "category_name": n} for c, n in
                (("5", "Drama"), ("7", "Hidden one"), ("8", "Locked one"), ("9", "XXX Adults"))]

    def vod_streams(self, cid=None):
        self.lists_asked.append(("vod", cid, threading.current_thread() is threading.main_thread()))
        return [{"stream_id": 1000 + i, "name": f"Film {i:03d}", "category_id": str(cid),
                 "releaseDate": "2019-01-01", "stream_icon": f"http://img.example/{i}.jpg"}
                for i in range(130)]

    def series_categories(self):
        return [{"category_id": "3", "category_name": "Crime"}]

    def series_list(self, cid=None):
        return [{"series_id": 500, "name": "The Bridge", "category_id": "3", "cover": "http://img.example/b.jpg"}]

    def series_info(self, sid):
        self.info_asked.append((sid, threading.current_thread() is threading.main_thread()))
        return {"info": {"name": "The Bridge"}, "episodes": {
            "1": [{"id": str(100 + e), "episode_num": e, "title": f"Ep {e}", "container_extension": "mkv",
                   "info": {"duration_secs": 3480}} for e in (1, 2, 3)],
            "2": [{"id": str(200 + e), "episode_num": e, "title": f"Ep {e}", "container_extension": "mkv",
                   "info": {"duration_secs": 3480}} for e in (1, 2, 3, 4, 5, 6)],
            "0": [{"id": "900", "episode_num": 1, "title": "Behind the scenes", "container_extension": "mkv"}]}}

    def vod_url(self, sid, ext=None):
        return f"http://x/movie/{sid}.{ext or 'mkv'}"

    def episode_url(self, eid, ext=None):
        return f"http://x/series/{eid}.{ext or 'mkv'}"


def _window(kids=False):
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.ui.main_window import MainWindow
    from dopeiptv.ui.mw_phone import _PhoneMixin

    class W(_PhoneMixin):
        _RESUMABLE = MainWindow._RESUMABLE
        _episode_rows = staticmethod(MainWindow._episode_rows)
        _item_key = staticmethod(MainWindow._item_key)
        MEDIA_EXTS = MainWindow.MEDIA_EXTS
        AUDIO_EXTS = MainWindow.AUDIO_EXTS
        _SCAN_JUNK = frozenset()

        def __init__(self):
            self.client = Client()
            self.overrides = Overrides()
            self.channel_ov = SimpleNamespace(data={}, display_name=lambda m, k, base: base)
            self.parental = SimpleNamespace(session_unlocked=False)
            self.series_ctx = None
            self._sp_cache = None
            self._sp_ctx = None
            self._sp_local = None
            self._sp_start_once = None
            self.kids = kids
            self.played, self.stepped, self.states_built = [], [], 0
            self.watched_keys = {"101", "102", "103", "201"}
            self.saved = {"204": 1300.0, "1003": 2000.0}
            self.resume = SimpleNamespace(
                progress=lambda k, kind: (self.saved.get(str(k), 0.0), 3480.0 if str(k) in self.saved else 0.0, 0),
                saved_position=lambda k, kind: self.saved.get(str(k), 0.0))
            self.history = SimpleNamespace(items=lambda: [])
            self.radio_favs = SimpleNamespace(items=lambda: [])
            self.rec = SimpleNamespace(directory=lambda: "/nowhere", VIDEO_EXTS=(".ts",), read_info=lambda p: {})
            self._last_playback = None
            self._playing_key = None
            self.prev_ok, self.next_ok = True, True

        def _profile_locked(self):
            return False

        def _profile_refuse_locked(self):
            pass

        def _profile_kids(self):
            return self.kids

        def _kids_keep(self, mode):
            if not self.kids:
                return None

            class Keep:
                def ready(self):
                    return self

                def __call__(self, row):
                    return True
            return Keep()

        def _kids_allows(self, it, mode):
            return True

        def _continue_rows(self):
            return []

        def _local_dirs(self):
            return []

        @staticmethod
        def _mounted_roots():
            return []

        def _radio(self):
            return None

        def _phone_asked(self):
            pass

        def _come_forward(self):
            pass

        def watched_source(self, ep, kind):
            return "local" if str(ep.get("id")) in self.watched_keys else None

        def is_movie_watched(self, row):
            return row.get("stream_id") == 1004

        def _ms_resume_at(self, key, kind, item, pos):
            return pos

        def _sp_season_label(self, season):
            return (("Specials", "Specials") if season.specials else (f"Season {season.number}",) * 2)

        def _start_at_once(self, key, kind, item=None):
            pos = self.saved.get(str(key), 0.0)
            self._sp_start_once = (key, pos) if pos > 0 else None

        def _start_playback(self, url, title, icon, key, kind, record=True, item=None, **kw):
            self.played.append({"url": url, "key": key, "kind": kind, "start": self._sp_start_once,
                                "ctx": self.series_ctx, "queue": getattr(self, "_ep_queue_override", None),
                                "index": getattr(self, "_ep_index_override", None)})

        def _links_later(self, plan, again, key="play"):
            return False

        def _set_status(self, *a, **k):
            pass

        def _sp_episode_url(self, ep):
            return self.client.episode_url(ep.get("id"), ep.get("container_extension"))

        def _has_previous_episode(self):
            return self.prev_ok

        def _has_next_episode(self):
            return self.next_ok

        def _advance_episode(self, direction, ask=True):
            self.stepped.append(("episode", direction, ask))
            return True

        def _play_channel_step(self, direction):
            self.stepped.append(("channel", direction))
            return True

        def _pb_episode_states(self, series, episodes):
            self.states_built += 1
            return super()._pb_episode_states(series, episodes)

    from dopeiptv.ui import mw_series_page
    W._play_series_episode = mw_series_page._SeriesPageMixin._play_series_episode
    return W()


def _episode_playing(w, eid="204", season=2, number=4, title="Ep 4"):
    series = {"series_id": 500, "name": "The Bridge", "category_id": "3", "cover": "http://img.example/b.jpg"}
    w._last_playback = {"kind": "episode", "title": f"S{season} * E{number} - {title}",
                        "item": {"id": eid, "season": season, "episode_num": number, "title": title},
                        "series_ctx": series}
    w._playing_key = eid
    return series


def test_the_state_for_an_episode_names_its_series_and_episode():
    w = _window()
    series = _episode_playing(w)
    ep = w._phone_episode(w._last_playback)
    assert ep["series"] == "The Bridge" and ep["label"] == "S2 E4 · Ep 4" and ep["sid"] == "500"
    assert ep["key"] == "204" and ep["prev"] and ep["next"] and ep["v"] and ep["img"]
    # Worked out again only on a change: the same answer while nothing changed.
    assert w._phone_episode(w._last_playback) is ep
    w.next_ok = False
    again = w._phone_episode(w._last_playback)
    assert again is not ep and not again["next"] and again["v"] == ep["v"]
    w._playing_key = "205"
    w._last_playback["item"] = {"id": "205", "season": 2, "episode_num": 5, "title": "The Bridge - S02E05 Ep 5"}
    later = w._phone_episode(w._last_playback)
    assert later["label"] == "S2 E5 · Ep 5" and later["v"] != ep["v"]
    assert series["series_id"] == 500


def test_previous_and_next_go_through_the_windows_episode_path():
    w = _window()
    _episode_playing(w)
    assert w._phone_do("next", {}) == {"ok": True}
    assert w._phone_do("previous", {}) == {"ok": True}
    # The episode path, without the resume question - nobody at the computer answers it.
    assert w.stepped == [("episode", 1, False), ("episode", -1, False)]
    w._last_playback = {"kind": "live", "item": {"stream_id": 7}}
    w._phone_do("next", {})
    assert w.stepped[-1] == ("channel", 1)


def test_the_window_steps_episodes_in_their_queue_without_asking():
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.ui.main_window import MainWindow
    eps = [{"id": str(200 + e), "episode_num": e, "container_extension": "mkv"} for e in (1, 2, 3)]
    f = SimpleNamespace(series_ctx=None, _sp_start_once=None, playback_mode=lambda: "embedded",
                        client=Client(), _item_key=lambda e: e.get("id"), started=[])
    f._last_playback = {"kind": "episode", "ep_queue": eps, "ep_index": 1, "series_ctx": {"series_id": 500}}
    f.resume = SimpleNamespace(saved_position=lambda k, kind: 600.0 if k == "201" else 0.0)
    f._ms_resume_at = lambda key, kind, item, pos: pos

    def start(url, title, icon, key, kind, record=True, item=None):
        f.started.append((key, kind, f._sp_start_once, f._ep_index_override, f.series_ctx))
    f._start_playback = start
    for name in ("_episode_step_item", "_advance_episode", "_start_at_once", "_has_previous_episode",
                 "_has_next_episode", "_next_episode_item"):
        setattr(f, name, getattr(MainWindow, name).__get__(f))
    assert f._has_previous_episode() and f._has_next_episode()
    assert f._advance_episode(-1, ask=False)
    assert f.started[-1] == ("201", "episode", ("201", 600.0), 0, {"series_id": 500})
    assert f._sp_start_once is None                 # once, for that episode alone
    assert f._advance_episode(1, ask=False)
    assert f.started[-1][:4] == ("203", "episode", None, 2)
    f._last_playback["ep_index"] = 0
    assert not f._has_previous_episode() and not f._advance_episode(-1)


def _serve(w):
    from dopeiptv.ui import mw_phone
    bridge = Bridge(w._phone_do)
    w._phone_bridge = bridge
    srv = PhoneServer(0, TOKEN, w._phone_call, lambda: (b"", ""), host="127.0.0.1")
    srv.start()
    assert mw_phone
    return srv, bridge


def test_the_episodes_endpoint_is_cached_by_series_and_version_with_304s():
    w = _window()
    _episode_playing(w)
    srv, bridge = _serve(w)
    try:
        url = f"/{TOKEN}/api/episodes?sid=500&v=a1"
        code, h, raw = ask(srv.port, "GET", url)
        assert code == 200, raw
        d = json.loads(raw)
        assert d["series"] == "The Bridge" and d["playing"] == "204"
        assert [s["label"] for s in d["seasons"]] == ["Season 1", "Season 2", "Specials"]
        assert d["open"] == 1                         # the playing episode's season
        line = d["seasons"][1]["episodes"][3]
        assert line["n"] == "E4" and line["min"] == 58 and line["pct"] == 37 and not line["watched"]
        assert d["seasons"][0]["episodes"][0]["watched"]
        # The series' episodes asked for once, off the window's thread, and kept
        # where the series page keeps its own.
        assert w.client.info_asked == [("500", False)]
        assert (id(w.client), "500") in w._sp_cache
        # The same again: a 304 and nothing built.
        tag = h["etag"]
        assert ask(srv.port, "GET", url, headers={"If-None-Match": tag})[0] == 304
        assert w.states_built == 1 and w.client.info_asked == [("500", False)]
        # The window's thread: what it holds, read at once - nothing slow there.
        assert set(bridge.asked) <= {"_pb_series", "_pb_keep_series", "_pb_episode_states"}
        assert bridge.asked.count("_pb_keep_series") == 1
        # Another version (the next episode plays): built again, from what is kept.
        _episode_playing(w, "205", 2, 5, "Ep 5")
        code, h2, raw = ask(srv.port, "GET", f"/{TOKEN}/api/episodes?sid=500&v=a2")
        assert code == 200 and json.loads(raw)["playing"] == "205" and h2["etag"] != tag
        assert w.states_built == 2 and len(w.client.info_asked) == 1
        # An episode played from it: the series page's path, its series and queue.
        ref = d["seasons"][1]["episodes"][3]["ref"]
        code, _h, raw = ask(srv.port, "POST", f"/{TOKEN}/api/play_ref", {"ref": ref})
        assert code == 200 and json.loads(raw) == {"ok": True}
        p = w.played[-1]
        assert p["key"] == "204" and p["kind"] == "episode" and p["start"] == ("204", 1300.0)
        assert p["ctx"]["series_id"] == 500 and [e["id"] for e in p["queue"]][:4] == ["101", "102", "103", "201"]
        assert p["queue"][p["index"]]["id"] == "204"
        assert w.series_ctx is None and w._sp_start_once is None
        # Nothing the phone made up: an id it was never given plays nothing.
        assert ask(srv.port, "POST", f"/{TOKEN}/api/play_ref", {"ref": "e" + "0" * 15})[0] == 400
    finally:
        srv.stop()


def test_a_series_the_phone_never_listed_has_no_episodes_to_give():
    w = _window()
    srv, _bridge = _serve(w)
    try:
        assert ask(srv.port, "GET", f"/{TOKEN}/api/episodes?sid=777")[0] == 400
        assert w.client.info_asked == []
    finally:
        srv.stop()


def test_films_by_category_as_the_computer_shows_them_a_page_at_a_time():
    w = _window()
    srv, bridge = _serve(w)
    try:
        code, h, raw = ask(srv.port, "GET", f"/{TOKEN}/api/browse?p=vod")
        cats = json.loads(raw)["items"]
        # Hidden and locked (the lock is shut) left out, as in the window.
        assert [c["name"] for c in cats] == ["Drama", "XXX Adults"]
        drama = cats[0]["p"]
        code, h, raw = ask(srv.port, "GET", f"/{TOKEN}/api/browse?p={drama}")
        page = json.loads(raw)
        assert code == 200 and page["title"] == "Drama" and page["total"] == 130
        assert len(page["items"]) == pb.PAGE and page["next"] == pb.PAGE
        first = page["items"][0]
        assert first["name"] == "Film 000" and first["sub"] == "2019" and first["img"] == 1 and "mark" not in first
        assert page["items"][3]["pct"] == 57 and page["items"][4]["w"] == 1
        # The source's list read off the window's thread; once for both pages.
        ask(srv.port, "GET", f"/{TOKEN}/api/browse?p={drama}&o=60")
        assert w.client.lists_asked == [("vod", "5", False)]
        # The same page again: a 304.
        assert ask(srv.port, "GET", f"/{TOKEN}/api/browse?p={drama}",
                   headers={"If-None-Match": h["etag"]})[0] == 304
        # A search, done where the list is kept.
        found = json.loads(ask(srv.port, "GET", f"/{TOKEN}/api/browse?p={drama}&q=film%20012")[2])
        assert [r["name"] for r in found["items"]] == ["Film 012"]
        # Played as the list plays it: where it got to, no question asked.
        code, _h, raw = ask(srv.port, "POST", f"/{TOKEN}/api/play_ref", {"ref": page["items"][3]["ref"]})
        assert code == 200 and w.played[-1]["kind"] == "movie" and w.played[-1]["start"] == (1003, 2000.0)
        # Locked meanwhile: it is not played.
        w.overrides.data["vod"]["5"] = {"locked": True}
        assert ask(srv.port, "POST", f"/{TOKEN}/api/play_ref", {"ref": page["items"][0]["ref"]})[0] == 400
        assert set(bridge.asked) <= {"_pb_snap", "_pb_marks", "_pb_play"}
    finally:
        srv.stop()


def test_a_kids_profile_never_sees_a_category_for_adults():
    w = _window(kids=True)
    srv, _bridge = _serve(w)
    try:
        cats = json.loads(ask(srv.port, "GET", f"/{TOKEN}/api/browse?p=vod")[2])["items"]
        assert [c["name"] for c in cats] == ["Drama"]
    finally:
        srv.stop()


def test_a_portals_lists_only_as_loaded_never_fetched_for_the_phone():
    w = _window()

    class Portal(Client):
        paged_lists = True

        def loaded(self, kind):
            return ([{"stream_id": 1, "name": "Loaded film", "category_id": "5"}],
                    [{"category_id": "5", "category_name": "Drama"}], False)

        def vod_streams(self, cid=None):
            raise AssertionError("a portal paged through for the phone")

        def vod_categories(self):
            raise AssertionError("a portal asked for the phone")
    w.client = Portal()
    srv, _bridge = _serve(w)
    try:
        cats = json.loads(ask(srv.port, "GET", f"/{TOKEN}/api/browse?p=vod")[2])["items"]
        films = json.loads(ask(srv.port, "GET", f"/{TOKEN}/api/browse?p={cats[0]['p']}")[2])["items"]
        assert [f["name"] for f in films] == ["Loaded film"]
    finally:
        srv.stop()


def test_local_files_only_under_the_folders_the_app_knows(tmp_path):
    w = _window()
    music = tmp_path / "Music"
    (music / "Album").mkdir(parents=True)
    for n in ("01 One.flac", "02 Two.flac", "film.mkv", "notes.txt", ".hidden.mp3"):
        (music / n).write_bytes(b"x")
    (music / "Album" / "03 Deep match.flac").write_bytes(b"x")
    w._local_dirs = lambda: [str(music)]
    srv, _bridge = _serve(w)
    try:
        roots = json.loads(ask(srv.port, "GET", f"/{TOKEN}/api/browse?p=local")[2])["items"]
        assert [r["name"] for r in roots] == ["Music"] and str(tmp_path) not in json.dumps(roots)
        folder = json.loads(ask(srv.port, "GET", f"/{TOKEN}/api/browse?p={roots[0]['p']}")[2])["items"]
        assert [r["name"] for r in folder] == ["Album", "01 One.flac", "02 Two.flac", "film.mkv"]
        found = json.loads(ask(srv.port, "GET", f"/{TOKEN}/api/browse?p={roots[0]['p']}&q=deep")[2])["items"]
        assert [(r["name"], r["sub"]) for r in found] == [("03 Deep match.flac", "Album")]
        # A track plays with the folder's music as its queue (not the film).
        code, _h, _raw = ask(srv.port, "POST", f"/{TOKEN}/api/play_ref", {"ref": folder[2]["ref"]})
        assert code == 200 and w.played[-1]["kind"] == "local"
        assert [os.path.basename(t["_path"]) for t in w._track_queue] == ["01 One.flac", "02 Two.flac"]
        # A folder id made up, or one for a folder outside: nothing.
        outside = w._pb()[1].put("dir", "/etc", "/etc")
        assert ask(srv.port, "GET", f"/{TOKEN}/api/browse?p=local/{outside}")[0] == 400
    finally:
        srv.stop()


def test_a_poster_made_small_off_the_windows_thread(tmp_path):
    pytest.importorskip("PyQt6.QtGui")
    from PyQt6.QtGui import QColor, QImage
    from dopeiptv.ui import mw_phone_browse
    big = QImage(600, 900, QImage.Format.Format_RGB32)
    big.fill(QColor(120, 40, 40))
    path = tmp_path / "poster.png"
    assert big.save(str(path))
    data = mw_phone_browse.thumbnail(str(path))
    assert data[:3] == b"\xff\xd8\xff" and len(data) < 20_000
    small = QImage.fromData(data)
    assert max(small.width(), small.height()) == mw_phone_browse.THUMB_PX
    w = _window()
    ref = w._pb()[1].put("movie", 1, {"stream_id": 1, "stream_icon": "http://img.example/1.jpg"})
    asked = []

    def logo_file(url):
        asked.append((url, threading.current_thread() is threading.main_thread()))
        return str(path)
    w._phone_logo_file = logo_file
    srv, bridge = _serve(w)
    try:
        code, h, raw = ask(srv.port, "GET", f"/{TOKEN}/thumb/{ref}")
        assert code == 200 and h["content-type"] == "image/jpeg" and raw == data
        ask(srv.port, "GET", f"/{TOKEN}/thumb/{ref}")
        assert asked == [("http://img.example/1.jpg", False)]          # kept after the first
        assert bridge.asked == []                                       # never the window's thread
        assert ask(srv.port, "GET", f"/{TOKEN}/thumb/m{'0' * 15}")[0] == 404
    finally:
        srv.stop()


# -- the page ---------------------------------------------------------------------------------

def _page():
    words = {k: k.replace("_", " ").title() for k in phone_page.WORDS}
    html, csp = phone_page.render({"bg": "#17171C", "text": "#ECECF1", "accent": "#4C8DFF"},
                                  {"bg": "#F5F5F7", "text": "#1B1B1F", "accent": "#4C8DFF"}, words)
    html = html.decode()
    script = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    style = re.search(r"<style>(.*)</style>", html, re.S).group(1)
    return html, csp, script, style


def test_the_page_keeps_its_own_hashes_and_offers_episodes_and_sections():
    html, csp, script, style = _page()
    assert f"script-src {_sha(script)};" in csp and f"style-src {_sha(style)};" in csp
    assert '<button class="showIn" id="epsBtn" hidden>' in html
    for sec in ("all", "fav", "vod", "series", "recent", "local", "radio", "rec"):
        assert f'data-s="{sec}"' in html
    # An episode: its series as the title, "S2 E5 · Title" under it, prev/next its own.
    show = script[script.index("function show(s)"):script.index("function tick()")]
    assert "ep.series" in show and "ep.label" in show and "W.ep_prev" in show and "W.ep_next" in show
    assert "epsOffer(ep)" in show
    # The Episodes view: asked with its tag and version, the playing one marked and brought to.
    assert '"api/episodes?sid="' in script and "If-None-Match" in script
    assert "bringIntoView(at, $(\"epsHead\"))" in script
    # Back: the page's arrows and the phone's own gesture.
    assert "history.pushState" in script and '"popstate"' in script
    # The sections' row scrolls inside itself; nothing else sideways.
    assert "overflow:auto hidden" in style.split(".tabs{", 1)[1].split("}", 1)[0]
    for rule in ("html,body{", "body{", "#app{"):
        assert "overflow" not in style.split(rule, 1)[1].split("}", 1)[0]
    for w in ("ep_prev", "ep_next", "episodes", "find"):
        assert w in phone_page.WORDS


def test_the_radio_section_is_never_empty_my_stations_first_then_the_most_voted():
    from dopeiptv.i18n import tr
    w = _window()
    st = lambda n: {"stationuuid": f"u{n}", "name": f"Station {n}", "url": f"http://r/{n}"}  # noqa: E731
    asked = []

    class Radio:
        def top(self, which, limit):
            asked.append((which, limit))
            return [st(1), st(2)]
    # Nothing kept: the most voted alone, asked once and kept a while.
    rows = w._pb_radio({"favs": [], "radio": Radio()}, "")
    assert [r["name"] for r in rows] == ["Station 1", "Station 2"]
    assert all(r["k"] == "play" and r["ref"] for r in rows)
    w._pb_radio({"favs": [], "radio": Radio()}, "")
    assert len(asked) == 1 and asked[0][0] == "topvote"
    # Stations kept: they come first, each list under its heading.
    rows = w._pb_radio({"favs": [st(9)], "radio": Radio()}, "")
    assert [(r["k"], r["name"]) for r in rows] == [
        ("head", tr("radio_cat_favorites")), ("play", "Station 9"),
        ("head", tr("radio_cat_top_voted")), ("play", "Station 1"), ("play", "Station 2")]
    # The directory not reachable: what is kept, no heading.
    w2 = _window()
    assert [r["name"] for r in w2._pb_radio({"favs": [st(9)], "radio": None}, "")] == ["Station 9"]
