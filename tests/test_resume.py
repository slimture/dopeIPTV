"""Unit tests for the extracted ResumeStore."""

import json

from dopeiptv.services.resume import ResumeStore


class FakeSettings:
    """Minimal QSettings stand-in: a dict with value()/setValue()."""
    def __init__(self, values=None):
        self._v = dict(values or {})

    def value(self, key, default=None):
        return self._v.get(key, default)

    def setValue(self, key, val):
        self._v[key] = val


def test_key_is_playlist_scoped():
    assert ResumeStore(FakeSettings(), "abc")._key == "resume_positions_abc"
    assert ResumeStore(FakeSettings(), None)._key == "resume_positions"


def test_record_and_resume_roundtrip():
    s = FakeSettings()
    r = ResumeStore(s, "p")
    r.record("vod", 42, pos=600, dur=3600)
    assert r.saved_position(42, "movie") == 600
    # persisted as JSON under the scoped key
    assert json.loads(s.value("resume_positions_p"))["vod:42"]["pos"] == 600


def test_position_near_start_is_not_saved():
    r = ResumeStore(FakeSettings(), "p")
    r.record("vod", 1, pos=30, dur=3600)      # < 60s in
    assert r.saved_position(1, "movie") == 0.0


def test_position_near_end_is_dropped():
    r = ResumeStore(FakeSettings(), "p")
    r.record("vod", 1, pos=3500, dur=3600)    # > 95% -> effectively finished
    assert r.saved_position(1, "movie") == 0.0


def test_record_removes_previous_point_when_finished():
    r = ResumeStore(FakeSettings(), "p")
    r.record("episode", 7, pos=600, dur=1800)
    assert r.saved_position(7, "episode") == 600
    r.record("episode", 7, pos=1790, dur=1800)  # watched to the end
    assert r.saved_position(7, "episode") == 0.0


def test_kind_group_mapping():
    r = ResumeStore(FakeSettings(), "p")
    r.record("rec", "file.ts", pos=200, dur=1000)
    assert r.saved_position("file.ts", "recording") == 200
    # unknown kind -> no group -> nothing to resume
    assert r.saved_position("file.ts", "live") == 0.0


def test_corrupt_stored_json_is_ignored():
    s = FakeSettings({"resume_positions_p": "{not json"})
    r = ResumeStore(s, "p")           # must not raise
    assert r.saved_position(1, "movie") == 0.0


def test_continue_watching_lists_movies_with_progress():
    r = ResumeStore(FakeSettings(), "p")
    r.record("vod", 42, pos=1800, dur=3600,
             item={"name": "Dune", "stream_id": 42,
                   "container_extension": "mp4"})
    r.record("vod", 7, pos=300, dur=6000,
             item={"name": "Old", "stream_id": 7})
    cw = r.continue_watching()
    assert [x["name"] for x in cw] == ["Old", "Dune"] or \
           {x["name"] for x in cw} == {"Dune", "Old"}
    by_name = {x["name"]: x for x in cw}
    assert by_name["Dune"]["_progress_pct"] == 50
    assert by_name["Dune"]["_kind"] == "vod"
    assert by_name["Dune"]["_resume_pos"] == 1800


def test_continue_watching_includes_episodes_with_context():
    r = ResumeStore(FakeSettings(), "p")
    r.record("episode", 5, pos=600, dur=1800,
             item={"name": "S1 E2", "id": 5, "container_extension": "mp4"},
             series_ctx={"series_id": 88, "name": "Severance"})
    rows = r.continue_watching()
    assert len(rows) == 1
    ep = rows[0]
    assert ep["_kind"] == "episode"
    assert ep["_series_ctx"]["series_id"] == 88
    assert ep["id"] == 5
    # The cover pipeline resolves episode art from the SERIES title; without
    # this stamp the Home shelf TMDB-searched the mangled row name and the
    # poster came and went at random.
    assert ep["_series_title"] == "Severance"
    assert ep["name"] == "Severance · S1 E2"


def test_an_episode_played_from_continue_watching_names_its_series_once():
    """A Continue watching row says "Show · S1 E2"; played from there and
    kept again, the row's name went into the store with its "Show · ", and
    the next row said it twice - then three times (the window's title read
    "Show · Show · Show · S2 * E3 - ..."). Kept and shown, the series once;
    a store an older app wrote that way is shown once too."""
    r = ResumeStore(FakeSettings(), "p")
    ctx = {"series_id": 88, "name": "Jorden runt på 6 steg"}
    r.record("episode", 5, pos=600, dur=1800,
             item={"name": "S2 * E3 - Jorden runt på 6 steg - S02E03", "id": 5}, series_ctx=ctx)
    for _ in range(3):
        row = r.continue_watching()[0]
        r.record("episode", 5, pos=700, dur=1800, item=row, series_ctx=ctx)
    assert r.continue_watching()[0]["name"] == "Jorden runt på 6 steg · S2 * E3 - Jorden runt på 6 steg - S02E03"
    stored = json.loads(r._settings.value(r._key))
    assert stored["episode:5"]["item"]["name"] == "S2 * E3 - Jorden runt på 6 steg - S02E03"
    # What an older app kept, "Show · " three times in front: shown once.
    stored["episode:5"]["item"]["name"] = "Jorden runt på 6 steg · " * 3 + "S2 * E3"
    r._settings.setValue(r._key, json.dumps(stored))
    r2 = ResumeStore(r._settings, "p")
    assert r2.continue_watching()[0]["name"] == "Jorden runt på 6 steg · S2 * E3"


def test_continue_watching_drops_near_start():
    r = ResumeStore(FakeSettings(), "p")
    r.record("vod", 9, pos=30, dur=3600,
             item={"name": "Barely", "stream_id": 9})  # < 60s dropped
    assert r.continue_watching() == []


def test_continue_watching_clear_removes_row():
    r = ResumeStore(FakeSettings(), "p")
    r.record("vod", 42, pos=1800, dur=3600, item={"name": "Dune"})
    assert len(r.continue_watching()) == 1
    r.clear("vod", 42)
    assert r.continue_watching() == []


def test_snapshot_keeps_identity_of_a_history_row():
    # Playing a movie from History passes THAT row as the item: it carries
    # _key and _url but no provider stream_id. Dropping those from the
    # snapshot left the Continue-watching row with no identity (its history
    # entry got key=None and stacked up as a duplicate that could not resume)
    # and no URL to play (the view built /movie/user/pass/None.mp4).
    r = ResumeStore(FakeSettings(), "p")
    r.record("vod", 77, pos=900, dur=5400,
             item={"name": "Dune", "_key": 77, "_kind": "movie",
                   "_url": "http://h.tv/movie/u/p/77.mkv"})
    row = r.continue_watching()[0]
    assert row["_key"] == 77
    assert row["_url"] == "http://h.tv/movie/u/p/77.mkv"


def test_a_local_file_resumes_where_it_stopped(tmp_path):
    """The window records a local file's position under group "local"; the
    store must look it up under the same name. It did not - the kind map
    had no "local" - so every local play started from zero no matter how
    much of the rest of the chain was right."""
    from dopeiptv.services.resume import ResumeStore

    class _S:
        def __init__(self):
            self.d = {}

        def value(self, k, default=None):
            return self.d.get(k, default)

        def setValue(self, k, v):
            self.d[k] = v

    path = "/Volumes/oden/Film/En.Film.2020.mkv"
    r = ResumeStore(_S(), "pl1")
    r.record("local", path, pos=600.0, dur=5400.0)
    assert r.saved_position(path, "local") == 600.0

    # Same rules as everything else: the first minute and the last 5% are
    # not worth resuming.
    r.record("local", path, pos=30.0, dur=5400.0)
    assert r.saved_position(path, "local") == 0.0
    r.record("local", path, pos=5300.0, dur=5400.0)
    assert r.saved_position(path, "local") == 0.0
