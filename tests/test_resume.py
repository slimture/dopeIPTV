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
