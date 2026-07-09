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
