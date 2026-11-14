"""A portal's recordings ask for their address when they start.

A Stalker portal's play address comes from create_link and lasts seconds
(StalkerClient.LINK_CACHE_SECS), so a recording scheduled with one - by a
series rule or from the guide - played a dead link days later. Here, with a
fake portal that counts what it is asked: planning asks it nothing; the
start asks once, off the window's thread, and the recorder takes that
address; no address, an error or no answer in time is said plainly; the job
is kept across a restart without an address; another source's channel asks
that source."""
from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from dopeiptv.core import series_rules as sr  # noqa: E402
from dopeiptv.core.recording import RecordingManager  # noqa: E402
from dopeiptv.i18n import tr  # noqa: E402
from dopeiptv.ui import mw_recording, mw_series  # noqa: E402
from dopeiptv.ui.mw_detail import _DetailMixin  # noqa: E402
from dopeiptv.ui.mw_recording import _RecordingMixin, rec_reason  # noqa: E402
from dopeiptv.ui.mw_series import _SeriesMixin  # noqa: E402

H = 3600.0


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _S(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value


class Portal:
    """A portal client as the window sees one: slow, short-lived links."""
    slow_links = True
    max_connections = None

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.asked: list[tuple[object, bool]] = []
        self.chans = [{"stream_id": 7, "name": "Seven", "epg_channel_id": "seven"},
                      {"stream_id": 8, "name": "Eight", "epg_channel_id": "eight"}]

    def live_streams(self, _cat=None):
        return [dict(c) for c in self.chans]

    def live_url(self, sid, fmt="ts"):
        self.asked.append((sid, threading.current_thread() is threading.main_thread()))
        if self.fail:
            raise RuntimeError("portal down")
        return f"http://portal.example/play/{sid}.ts?play_token=t{len(self.asked)}"


class Guide:
    def __init__(self, table):
        self.table = table

    def programmes_in(self, ch, lo, hi, keep):
        return [{"title": t, "start_timestamp": s, "stop_timestamp": e}
                for t, s, e in self.table.get(ch.get("stream_id"), [])
                if s < hi and e > lo and keep(t)]


class Store:
    def __init__(self, *pls):
        self.items = [dict(p) for p in pls]
        self.active_id = self.items[0]["id"] if self.items else ""

    def playlists(self):
        return list(self.items)

    def get(self, pid):
        return next((p for p in self.items if p.get("id") == pid), None)

    def active(self):
        return self.get(self.active_id)


class Window(_SeriesMixin, _RecordingMixin, _DetailMixin):
    """The mixins on a stand-in for the window: what they read of it."""

    def __init__(self, client, rec, store) -> None:
        self.client = client
        self.rec = rec
        self.playlist_store = store
        self.settings = _S()
        self.series_rules = sr.SeriesRuleStore(self.settings, "pl1")
        self.flashed: list[str] = []
        self.now_recorded: list = []

    def _series_channel_name(self, ch):
        return str(ch.get("name") or "?")

    def channel_display_name(self, ch):
        return str(ch.get("name") or "?")

    def _flash_status(self, text, *a, **k):
        self.flashed.append(text)

    def _within_storage_cap(self):
        return True

    def _record_now(self, *a, **k):
        self.now_recorded.append(a)


def _rec(tmp_path, name="s.ini") -> RecordingManager:
    s = QSettings(str(tmp_path / name), QSettings.Format.IniFormat)
    s.setValue("recordings_dir", str(tmp_path / "rec"))
    rec = RecordingManager(s)
    rec._timer.stop()
    rec.recorder = lambda: ("ffmpeg", "/bin/true")
    return rec


def _wait(app, cond, secs=10.0) -> bool:
    end = time.monotonic() + secs
    while time.monotonic() < end and not cond():
        app.processEvents()
        time.sleep(0.01)
    return cond()


def _window(tmp_path, portal=None):
    portal = portal or Portal()
    rec = _rec(tmp_path)
    w = Window(portal, rec, Store({"id": "pl1", "kind": "stalker", "name": "Portal"},
                                  {"id": "pl2", "kind": "xtream", "name": "Other"}))
    rec.address_for = w._rec_address_for
    return w, portal, rec


def test_planning_a_series_asks_the_portal_for_no_link(app, tmp_path):
    w, portal, rec = _window(tmp_path)
    now = time.time()
    rule = w.series_rules.add(sr.make_rule("The Show", sr.EXACT, portal.chans[0]))
    word = w.series_rules.add(sr.make_rule("news", sr.WORD))
    guide = Guide({7: [("The Show", now + H, now + 2 * H), ("The Show", now + 25 * H, now + 26 * H)],
                   8: [("Late News", now + 3 * H, now + 3.5 * H)]})
    found = mw_series._find(portal, guide, w.series_rules.rules(), lambda c: True, now, 0.0, "pl1")
    assert {(a.key, a.title) for a in found} == {("7", "The Show"), ("8", "Late News")}
    assert all(a.url == "" and a.source == "pl1" for a in found)
    assert w._series_schedule(found) == 3
    assert portal.asked == [], "planning must not create links"
    jobs = [j for j in rec.jobs if j.get("rule")]
    assert len(jobs) == 3 and {j["rule"] for j in jobs} == {rule["id"], word["id"]}
    for j in jobs:
        assert j["url"] == "" and j["source"] == "pl1" and j["source_kind"] == "stalker"
        assert j[RecordingManager.LATER] == {"source": "pl1", "stream_id": int(j["stream_key"])}
    # The guide's next look: nothing twice, and still no link asked for.
    found = mw_series._find(portal, guide, w.series_rules.rules(), lambda c: True, now, 0.0, "pl1")
    assert w._series_schedule(found) == 0 and portal.asked == []


def test_a_source_with_lasting_links_still_gets_them_when_planned(app, tmp_path):
    class Xtream(Portal):
        slow_links = False
    w, portal, rec = _window(tmp_path, Xtream())
    now = time.time()
    w.series_rules.add(sr.make_rule("The Show", sr.EXACT, portal.chans[0]))
    found = mw_series._find(portal, Guide({7: [("The Show", now + H, now + 2 * H)]}),
                            w.series_rules.rules(), None, now, 0.0, "pl1")
    assert [a.url for a in found] == ["http://portal.example/play/7.ts?play_token=t1"]
    assert w._series_schedule(found) == 1
    job = next(j for j in rec.jobs if j.get("rule"))
    assert job["url"] == found[0].url and RecordingManager.LATER not in job


def test_the_start_asks_once_off_the_windows_thread_and_records_that(app, tmp_path):
    w, portal, rec = _window(tmp_path)
    now = time.time()
    # A programme recorded from the guide, on a portal: no link now either.
    w._record_programme(portal.chans[0], {"title": "Film"}, now + H, now + 2 * H)
    assert portal.asked == [] and not w.now_recorded
    job = next(j for j in rec.jobs if j["title"] == "Film")
    assert job["url"] == "" and job[RecordingManager.LATER]["stream_id"] == 7
    assert job["stream_key"] == "7" and job["prog_start"] == now + H
    spawned: list[dict] = []
    rec._spawn = lambda j: spawned.append(dict(j)) or j.update(status="recording")
    rec.update_job_times(job["id"], now - 1, now + H)
    rec.tick()
    assert job["status"] == "scheduled" and job.get("_asking") is not None
    assert _wait(app, lambda: spawned)
    assert len(portal.asked) == 1 and portal.asked[0] == (7, False), portal.asked
    assert spawned[0]["url"] == "http://portal.example/play/7.ts?play_token=t1"
    assert job["url"] == spawned[0]["url"] and "_asking" not in job
    rec.tick()
    assert len(portal.asked) == 1


def test_no_address_is_said_not_recorded_blind(app, tmp_path):
    w, portal, rec = _window(tmp_path, Portal(fail=True))
    now = time.time()
    rec._spawn = lambda j: pytest.fail("nothing to record without an address")
    job = rec.add_job("", "Show", now + H, now + 2 * H,
                      extra=w._rec_extra(portal.chans[0]))
    rec.update_job_times(job["id"], now - 1, now + H)
    rec.tick()
    assert _wait(app, lambda: job["status"] == "failed")
    assert job["error"] == RecordingManager.NO_ADDRESS
    assert rec_reason(job["error"]) == tr("rec_reason_no_address")
    assert len(portal.asked) == 1
    # Its source deleted meanwhile: failed at once, nothing asked.
    gone = rec.add_job("", "Gone", now - 1, now + H,
                       extra={RecordingManager.LATER: {"source": "deleted", "stream_id": 7}})
    assert gone["status"] == "failed" and gone["error"] == RecordingManager.NO_ADDRESS
    assert len(portal.asked) == 1


def test_no_answer_in_time_gives_up_and_a_late_one_is_ignored(app, tmp_path):
    w, portal, rec = _window(tmp_path)
    release = threading.Event()

    def slow(job):
        def fetch():
            release.wait(10)
            return "http://portal.example/late.ts"
        return fetch
    rec.address_for = slow
    rec.ADDRESS_TIMEOUT_S = 0.05
    rec._spawn = lambda j: pytest.fail("given up: never started")
    now = time.time()
    job = rec.add_job("", "Show", now - 1, now + H,
                      extra={RecordingManager.LATER: {"source": "pl1", "stream_id": 7}})
    time.sleep(0.1)
    rec.tick()
    assert job["status"] == "failed" and job["error"] == RecordingManager.NO_ADDRESS
    release.set()
    _wait(app, lambda: False, 0.3)
    assert job["status"] == "failed" and job["url"] == ""


def test_a_job_without_its_address_survives_a_restart(app, tmp_path):
    rec = _rec(tmp_path)
    now = time.time()
    rec.add_job("", "Show", now + H, now + 2 * H, extra={
        RecordingManager.LATER: {"source": "pl1", "stream_id": 7}, "source": "pl1"})
    back = _rec(tmp_path)
    assert [(j["title"], j["url"], j[RecordingManager.LATER]) for j in back.jobs] == [
        ("Show", "", {"source": "pl1", "stream_id": 7})]
    # An entry with neither an address nor a way to get one is still left out.
    back.settings.setValue("recording_jobs", '[{"title": "x", "start": 1, "stop": null, '
                                             '"status": "scheduled", "url": ""}]')
    assert _rec(tmp_path).jobs == []


def test_another_sources_channel_asks_that_source(app, tmp_path, monkeypatch):
    w, portal, rec = _window(tmp_path)
    made: list[dict] = []

    class Other:
        def live_url(self, sid, fmt="ts"):
            return f"http://other.example/{sid}.ts"

    import dopeiptv.providers.client as client_mod
    monkeypatch.setattr(client_mod, "make_client", lambda pl: made.append(pl) or Other())
    fetch = w._rec_address_for({RecordingManager.LATER: {"source": "pl2", "stream_id": 3}})
    assert made == [] and fetch() == "http://other.example/3.ts" and made[0]["id"] == "pl2"
    assert mw_recording._live_address(portal, 9).endswith("/9.ts?play_token=t1")
    assert w._rec_address_for({RecordingManager.LATER: {"source": "nope", "stream_id": 3}}) is None
    assert w._rec_address_for({}) is None
