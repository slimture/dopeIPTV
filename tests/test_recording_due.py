"""A scheduled recording that comes due while something plays from the same
account is counted as one started by hand (AGENTS.md, one connection):
its channel playing - recorded from the player's rewind buffer, else the
player's stream, no connection of its own; room on the account (a known
limit with room, nothing of the app's on it) - its own connection; else it
waits, nothing opened, a note offers to record anyway, and it starts by
itself once there is room - or fails at its stop time, saying why."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from dopeiptv.core.recording import RecordingManager  # noqa: E402
from dopeiptv.i18n import tr  # noqa: E402
from dopeiptv.ui.mw_connections import _ConnectionsMixin  # noqa: E402
from dopeiptv.ui.mw_recording import _RecordingMixin, rec_reason  # noqa: E402
from dopeiptv.ui.mw_source_label import _SourceLabelMixin  # noqa: E402

H = 3600.0
A7 = "http://a.example.com/live/u/p/7.ts"
A8 = "http://a.example.com/live/u/p/8.ts"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _S(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value


class Store:
    """a and b: one provider account added twice; c another account."""

    def __init__(self):
        self.active_id = "a"
        self.items = {pid: {"id": pid, "name": pid.upper(), "kind": "xtream",
                            "server": "http://a.example.com", "username": user}
                      for pid, user in (("a", "u"), ("b", "u"), ("c", "w"))}

    def get(self, pid):
        return self.items.get(pid)

    def active(self):
        return self.items.get(self.active_id)

    def playlists(self):
        return list(self.items.values())


class Player:
    def __init__(self, url, dvr=None):
        self.current_url, self._dvr, self._dvr_pool = url, dvr, None
        self._paused = False
        self.recorded: list[str] = []

    def start_stream_record(self, path):
        self.recorded.append(path)
        return True

    def take_buffer_recorder(self):
        return None


class Buffer:
    """The player's rewind buffer: holds the last ten minutes."""
    KEY_LEAD = 3.0

    def __init__(self, url):
        self.url, self.streaming, self.recording = url, True, False
        self.asked: list[tuple[str, float | None]] = []

    def span(self):
        now = time.time()
        return now - 600, now

    def record(self, path, since):
        self.asked.append((path, since))
        return SimpleNamespace(alive=True, error="", stop=lambda *a: None)


class Window(_RecordingMixin, _ConnectionsMixin, _SourceLabelMixin):
    def __init__(self, rec):
        self.rec = rec
        self.playlist_store = Store()
        self.client = SimpleNamespace(max_connections=None)
        self.player = None
        self._multiview_win = None
        self._failover_source = self._failover_key = None
        self._last_playback = None
        self._playing_key = self._playing_item = self._playing_group = None
        self.cast = SimpleNamespace(active=None)
        self._cast_ctx = None
        self._player_fs = False
        self.settings = _S()
        self.asked: list[str] = []
        self.toasts: list[tuple[str, object]] = []
        self.answer = 1

    def _item_key(self, it):
        return (it or {}).get("stream_id")

    def _flash_status(self, *_a, **_k):
        pass

    def _set_status(self, *_a, **_k):
        pass

    def _show_action_toast(self, text, _ms, on_click):
        self.toasts.append((text, on_click))

    def _choice_dialog(self, title, message, options, dont_ask_setting=None):
        self.asked.append(message)
        return self.answer

    def plays(self, url, sid, dvr=None):
        """Channel *sid* of a, the source in use, plays in the player."""
        self.player = Player(url if dvr is None else "http://127.0.0.1:9/dvr", dvr)
        self._note_playing_source(url, sid, "live")
        self._last_playback = {"url": url, "kind": "live", "key": sid}
        self._playing_group, self._playing_key = "live", sid
        self._playing_item = {"stream_id": sid, "name": f"Ch {sid}"}


def _setup(tmp_path):
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.setValue("recordings_dir", str(tmp_path / "rec"))
    rec = RecordingManager(s)
    rec._timer.stop()
    rec.recorder = lambda: ("ffmpeg", "/bin/true")
    spawned: list[str] = []

    def spawn(j):
        spawned.append(j["title"])
        j.update(status="recording", proc=SimpleNamespace(
            poll=lambda: None, terminate=lambda: None, wait=lambda _t=None: 0))

    rec._spawn = spawn
    w = Window(rec)
    rec.may_open = w._rec_may_open
    return w, rec, spawned


def _due(rec, title, url, sid, source="a", stop_in=H):
    now = time.time()
    return rec.add_job(url, title, now + H, now + stop_in, channel=title,
                       extra={"source": source, "source_kind": "xtream", "stream_key": str(sid)})


def _start_now(rec, job):
    rec.update_job_times(job["id"], time.time() - 1, job["stop"])
    rec.tick()


def test_beside_another_channel_it_waits_and_never_opens_a_second_silently(app, tmp_path):
    w, rec, spawned = _setup(tmp_path)
    w.plays(A7, 7)
    job = _due(rec, "Film", A8, 8)
    changed: list[int] = []
    rec.jobs_changed.connect(lambda: changed.append(1))
    _start_now(rec, job)
    assert not spawned and job["status"] == "scheduled" and job["_held"]
    assert len(w.toasts) == 1 and tr("rec_held", title="Film") in w.toasts[0][0]
    assert tr("rec_waiting_room") in w._job_item(job)["name"]
    n = len(changed)
    rec.tick()
    rec.tick()
    assert not spawned and len(w.toasts) == 1 and len(changed) == n, "said once, not every tick"
    assert "_held" not in rec.settings.value("recording_jobs"), "never kept past a restart"
    # Record anyway, from the note: asked with what is in use; Keep waiting.
    w.toasts[0][1]()
    assert len(w.asked) == 1 and tr("rec_held_what", title="Film") in w.asked[0]
    assert tr("conn_test_unknown") in w.asked[0] and not spawned
    w.answer = 0                                    # Go ahead anyway
    w._rec_go_ahead(job["id"])
    assert spawned == ["Film"] and "_held" not in job


def test_it_starts_by_itself_once_the_player_lets_its_connection_go(app, tmp_path):
    w, rec, spawned = _setup(tmp_path)
    w.plays(A7, 7)
    job = _due(rec, "Film", A8, 8)
    _start_now(rec, job)
    assert not spawned
    w.player.current_url = None                     # Stop
    rec.tick()
    assert spawned == ["Film"] and not w.asked


def test_a_known_limit_with_room_opens_it_and_a_full_one_holds_it(app, tmp_path):
    w, rec, spawned = _setup(tmp_path)
    w.plays(A7, 7)
    w.client.max_connections = 2
    _start_now(rec, _due(rec, "Film", A8, 8))
    assert spawned == ["Film"] and not w.toasts
    # Now the player and that recording hold both.
    late = _due(rec, "Late", "http://a.example.com/live/u/p/9.ts", 9)
    _start_now(rec, late)
    assert spawned == ["Film"] and late["_held"]
    w._rec_go_ahead(late["id"])
    assert tr("conn_over", max=2, n=2) in w.asked[-1]
    assert tr("conn_k_player") in w.asked[-1] and tr("conn_k_recording") in w.asked[-1]


def test_nothing_of_the_apps_on_its_account_opens_it(app, tmp_path):
    """Another account's channel plays (c): the recording of a's is a's
    first connection. b is a's account added again: its recording waits."""
    w, rec, spawned = _setup(tmp_path)
    w.plays("http://a.example.com/live/w/p/7.ts", 7)
    w._playing_src = ("c", "C")                     # c's channel plays
    _start_now(rec, _due(rec, "Film", A8, 8))
    assert spawned == ["Film"] and not w.toasts
    rec.cancel(rec.jobs[-1]["id"])
    w._playing_src = ("b", "B")                     # the same account as a
    job = _due(rec, "News", A8, 8)
    _start_now(rec, job)
    assert spawned == ["Film"] and job["_held"]


def test_its_channel_playing_is_recorded_from_the_rewind_buffer(app, tmp_path):
    w, rec, spawned = _setup(tmp_path)
    buf = Buffer(A7)
    w.plays(A7, 7, dvr=buf)
    job = _due(rec, "News", A7, 7)
    start = time.time() - 120                       # the padding before, in the buffer
    rec.update_job_times(job["id"], start, job["stop"])
    rec.tick()
    assert not spawned and not w.toasts
    assert job["status"] == "recording" and job["inplayer"] and job["recorder"] is not None
    assert len(buf.asked) == 1 and abs(buf.asked[0][1] - start) < 1, "from its start"
    assert job["path"] == buf.asked[0][0] and not w.player.recorded


def test_its_channel_playing_without_a_buffer_rides_the_players_stream(app, tmp_path):
    """A portal's recording (its address asked at the start) on the channel
    that plays: no address asked, the player's stream recorded."""
    w, rec, spawned = _setup(tmp_path)
    w.plays(A7, 7)
    now = time.time()
    job = rec.add_job("", "Show", now + H, now + 2 * H, channel="Show",
                      extra={"source": "a", "source_kind": "stalker",
                             RecordingManager.LATER: {"source": "a", "stream_id": 7}})
    rec.address_for = lambda j: pytest.fail("no address asked")
    _start_now(rec, job)
    assert job["status"] == "recording" and job["inplayer"] and job["recorder"] is None
    assert w.player.recorded == [job["path"]] and job["url"] == A7 and not spawned
    # Another source's channel 7 is another channel.
    other = rec.add_job("", "Other", now + H, now + 2 * H,
                        extra={"source": "c", RecordingManager.LATER: {"source": "c",
                                                                      "stream_id": 7}})
    rec.address_for = lambda j: None
    _start_now(rec, other)
    assert other["status"] == "failed" and len(w.player.recorded) == 1


def test_an_archive_of_its_channel_is_not_what_airs(app, tmp_path):
    w, rec, spawned = _setup(tmp_path)
    w.plays(A7, 7)
    w._playing_catchup = True                       # a programme from the archive
    job = _due(rec, "News", A7, 7)
    _start_now(rec, job)
    assert job["_held"] and not w.player.recorded and not spawned


def test_held_past_its_stop_time_it_fails_saying_why(app, tmp_path):
    w, rec, spawned = _setup(tmp_path)
    w.plays(A7, 7)
    job = _due(rec, "Film", A8, 8)
    _start_now(rec, job)
    assert job["_held"]
    rec.update_job_times(job["id"], time.time() - 60, time.time() - 1)
    job["_held"] = True                             # it waited until then
    rec.tick()
    assert job["status"] == "failed" and job["error"] == RecordingManager.NO_ROOM
    assert rec_reason(job["error"]) == tr("rec_reason_no_room") and not spawned


def test_without_a_window_to_ask_it_opens_as_before(app, tmp_path):
    w, rec, spawned = _setup(tmp_path)
    w.plays(A7, 7)
    rec.may_open = None
    _start_now(rec, _due(rec, "Film", A8, 8))
    assert spawned == ["Film"]
