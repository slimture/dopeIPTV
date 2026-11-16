"""Recording from the phone remote: what plays recorded through the player's
record button's own path (from the stream that plays, no connection of its
own) and stopped through the record menu's; the channel's next programme
recorded through the scheduled-recording path (the padding, the job list)
or reminded; and the app's questions in any of it - a held recording's one
connection too many, a box's tuners, a play's - asked on the phone in the
window's own words and choices, never left waiting on this screen, and
nothing they ask about started without an explicit answer there."""
from __future__ import annotations

import re
import time

import pytest

from dopeiptv.core import phone_page
from dopeiptv.i18n import tr


class _NoDialog:
    """Nothing may wait on the computer's screen for a phone's request."""

    def __init__(self, *a, **k):
        raise AssertionError("a dialog was left on the computer for the phone's request")

    @staticmethod
    def warning(*a, **k):
        raise AssertionError("a warning box was left on the computer for the phone's request")


class _Player:
    def __init__(self, visible=True):
        self.visible = visible
        self.current_url = "http://x/live/u/p/7.ts"
        self.records: list[str] = []
        self.can_record = True

    def isVisible(self):  # noqa: N802
        return self.visible

    def start_stream_record(self, path):
        self.records.append(path)
        return self.can_record

    def take_buffer_recorder(self):
        return "buffer"


class _Rec:
    """The recording manager as the window's paths use it."""

    def __init__(self):
        self.jobs: list[dict] = []
        self.cancelled: list[str] = []
        self.ticks = 0
        self.full = False
        self.holds = False

    def recorder(self):
        return ("ffmpeg", "/usr/bin/ffmpeg")

    def total_cap_exceeded(self):
        return self.full

    def folder_used_bytes(self):
        return 9 * 10**9

    def total_cap_bytes(self):
        return 8 * 10**9

    def directory(self):
        return "/rec"

    def build_path(self, title, folder="", channel=""):
        return f"/rec/{title}.ts"

    def add_inplayer_job(self, title, path, stop_ts, url="", icon=None, recorder=None, channel="",
                         extra=None):
        job = {"id": f"j{len(self.jobs)}", "title": title, "path": path, "stop": stop_ts, "url": url,
               "status": "recording", "inplayer": True, "start": time.time(), "recorder": recorder,
               "channel": channel}
        self.jobs.append(job)
        return job

    def add_job(self, url, title, start_ts, stop_ts, folder="", icon=None, channel="", extra=None):
        job = {"id": f"j{len(self.jobs)}", "url": url, "title": title, "start": start_ts, "stop": stop_ts,
               "status": "scheduled", "channel": channel, **(extra or {})}
        if self.holds and start_ts <= time.time():
            job["_held"] = True         # RecordingManager.may_open: one connection too many
        self.jobs.append(job)
        return job

    def cancel(self, job_id):
        self.cancelled.append(job_id)
        for j in self.jobs:
            if j["id"] == job_id:
                j["status"] = "done" if j["status"] == "recording" else "cancelled"

    def tick(self):
        self.ticks += 1


class _Reminders:
    def __init__(self):
        self.added: list[tuple] = []

    def add(self, ch, title, start, stop=None):
        self.added.append((ch.get("stream_id"), title, start, stop))

    def has(self, sid, start):
        return any(a[0] == sid and a[2] == start for a in self.added)


CH = {"stream_id": 7, "name": "SVT1", "stream_icon": ""}


@pytest.fixture
def window(tmp_path, monkeypatch):
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import QSettings

    from dopeiptv.ui import mw_recording
    from dopeiptv.ui.mw_detail import _DetailMixin
    from dopeiptv.ui.mw_phone import _PhoneMixin
    from dopeiptv.ui.mw_recording import _RecordingMixin
    from dopeiptv.ui.mw_reminders import _RemindersMixin

    monkeypatch.setattr(mw_recording, "QDialog", _NoDialog)
    monkeypatch.setattr(mw_recording, "QMessageBox", _NoDialog)
    R = _RecordingMixin

    class W(_PhoneMixin):
        _choice_dialog = R._choice_dialog
        _rec_warn = R._rec_warn
        _recorder_ready = R._recorder_ready
        _within_storage_cap = R._within_storage_cap
        _fmt_gb = R.__dict__["_fmt_gb"]
        _record_now = R._record_now
        _record_in_background = R._record_in_background
        _with_conn_warning = R._with_conn_warning
        _one_more_over = R._one_more_over
        _rec_go_ahead = R._rec_go_ahead
        _record_programme = _DetailMixin._record_programme
        _add_reminder = _RemindersMixin._add_reminder

        def __init__(self):
            self.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
            self.player = _Player()
            self.rec = _Rec()
            self.reminders = _Reminders()
            self.client = type("C", (), {"live_url": staticmethod(lambda sid, fmt="ts": f"http://x/live/{sid}.ts")})()
            self._playing_item = dict(CH)
            self._playing_group = "live"
            self._playing_key = "live:7"
            self._playing_catchup = False
            self._last_playback = {"kind": "live", "title": "SVT1", "item": dict(CH), "url": "http://x/live/7.ts"}
            self.why = None
            self.toasts: list[str] = []
            self.status: list[str] = []

        def channel_display_name(self, it):
            return it.get("name") or ""

        def playback_mode(self):
            return "embedded"

        def _item_key(self, it):
            return f"live:{it.get('stream_id')}"

        def _rec_inplayer_url(self, it):
            return "http://x/live/7.ts"

        def _rec_player_source(self):
            return {"source": "s1"}

        def _rec_source(self):
            return {"source": "s1"}

        def _set_status(self, text):
            self.status.append(text)

        def _warn_pause_while_recording(self):
            pass

        def _scheduled_address(self, ch):
            return f"http://x/live/{ch['stream_id']}.ts", {}

        def _rec_held_why(self, j):
            return self.why

        def _conn_warning(self, v):
            return "Your account allows 1 connections at a time. In use now: 1."

        def _show_toast(self, text, ms=0):
            self.toasts.append(text)

        def _profile_locked(self):
            return False

    return W()


# -- the questions ---------------------------------------------------------------------------------

def test_a_question_is_the_same_id_again_and_answers_are_checked():
    from dopeiptv.ui.mw_phone_rec import ANSWERS_MAX, answers_of, question_id
    opts = [("Go ahead anyway", "normal"), ("Keep waiting", "primary")]
    assert question_id("T", "M", opts) == question_id("T", "M", list(opts))
    assert question_id("T", "M", opts) != question_id("T", "M2", opts)
    assert answers_of([{"q": "abc", "i": "1", "dont": True}]) == [{"q": "abc", "i": 1, "dont": True}]
    assert answers_of([{"q": "abc", "i": 0, "dont": "yes"}]) == [{"q": "abc", "i": 0, "dont": False}]
    assert answers_of("nope") == answers_of(None) == answers_of([1]) == answers_of([{"i": "x"}]) == []
    assert len(answers_of([{"q": "a", "i": 0}] * 10)) == ANSWERS_MAX
    assert answers_of([{"q": "x" * 99, "i": 0}])[0]["q"] == "x" * 16


def test_a_held_recording_goes_ahead_only_with_the_phones_yes(window):
    w = window
    w.rec.jobs.append({"id": "h1", "title": "News", "status": "scheduled", "_held": True,
                       "start": time.time() - 5, "stop": None, "channel": "SVT1"})
    w.why = (object(), None, False)                   # one connection too many
    r = w._phone_do("rec_go", {"id": "h1"})
    ask = r["ask"]
    # The window's own question, in its words and with its choices - and
    # nothing left on this screen (a dialog here fails the test).
    assert r["ok"] is False and ask["title"] == tr("conn_title")
    assert tr("rec_held_what", title="News") in ask["text"] and "allows 1 connections" in ask["text"]
    assert [c["label"] for c in ask["choices"]] == [tr("conn_anyway"), tr("rec_held_wait")]
    assert [c["kind"] for c in ask["choices"]] == ["normal", "primary"]
    assert not w.rec.jobs[0].get("_go_ahead") and w.rec.ticks == 0
    # Keep waiting: nothing; an answer to another question: asked again.
    r = w._phone_do("rec_go", {"id": "h1", "answers": [{"q": ask["q"], "i": 1}]})
    assert r == {"ok": False} and not w.rec.jobs[0].get("_go_ahead") and w.rec.ticks == 0
    r = w._phone_do("rec_go", {"id": "h1", "answers": [{"q": "0" * 16, "i": 0}]})
    assert r["ask"]["q"] == ask["q"] and not w.rec.jobs[0].get("_go_ahead")
    # Go ahead anyway: the recording's own connection, as the window's yes.
    r = w._phone_do("rec_go", {"id": "h1", "answers": [{"q": ask["q"], "i": 0}]})
    assert r == {"ok": True} and w.rec.jobs[0]["_go_ahead"] is True and w.rec.ticks == 1
    assert w.__dict__.get("_phone_ask_hook") is None         # the screen's own questions again after


def test_a_dont_ask_again_ticked_on_the_phone_is_the_apps_own_key(window):
    from dopeiptv.ui.mw_phone_rec import PhoneQuestions
    q = PhoneQuestions(window.settings, [])
    opts = [("Remind me", "primary"), ("Cancel", "normal")]
    assert q("Not live", "Later?", opts, "hide_upcoming_prompt") is None
    assert q.asked["dont"] is True and q.asked["choices"][0] == {"label": "Remind me", "kind": "primary"}
    q2 = PhoneQuestions(window.settings, [{"q": q.asked["q"], "i": 0, "dont": True}])
    assert q2("Not live", "Later?", opts, "hide_upcoming_prompt") == 0
    assert window.settings.value("hide_upcoming_prompt") == "true"
    # Out of range is no answer: asked.
    q3 = PhoneQuestions(window.settings, [{"q": q.asked["q"], "i": 5, "dont": False}])
    assert q3("Not live", "Later?", opts) is None and q3.asked is not None


def test_a_question_asked_later_in_the_phones_request_is_said_in_the_state(window, monkeypatch):
    from dopeiptv.ui import mw_phone_rec
    w = window
    clock = [1000.0]
    monkeypatch.setattr(mw_phone_rec.time, "monotonic", lambda: clock[0])
    went: list[int | None] = []

    def later_play():
        went.append(w._choice_dialog("Recording in progress", "Switch?",
                                     [("Watch the recorded channel", "primary"), ("Cancel", "normal")]))

    with w._phone_asking(None):
        again = w._phone_keep_asking(later_play)       # what _links_later keeps for later
    assert w.__dict__.get("_phone_ask_hook") is None
    again()                                            # the address came: the question now
    assert went == [None]
    late = w._phone_late_ask()
    assert late is not None and late["title"] == "Recording in progress"
    # The request sent again with the answer clears it; a minute on, it is gone.
    with w._phone_asking([{"q": late["q"], "i": 0}]) as q:
        again2 = w._phone_keep_asking(later_play)
    assert w._phone_late_ask() is None and q.asked is None
    again2()
    assert went[-1] == 0
    with w._phone_asking(None):
        again3 = w._phone_keep_asking(later_play)
    again3()
    assert w._phone_late_ask() is not None
    clock[0] += mw_phone_rec.LATE_S + 1
    assert w._phone_late_ask() is None


# -- recording what plays -----------------------------------------------------------------------

def test_record_and_stop_go_through_the_windows_own_paths(window):
    w = window
    assert w._phone_rec_asks() is True                # asked first unless turned off
    r = w._phone_do("rec", {})
    assert r == {"ok": True}
    j = w.rec.jobs[-1]
    # The player's record button's path: from the stream that plays, until stopped.
    assert j["inplayer"] and j["recorder"] == "buffer" and j["stop"] is None and w.player.records
    rec = w._phone_rec_state()
    assert rec == {"id": j["id"], "title": "SVT1", "start": int(j["start"]), "here": True}
    r = w._phone_do("rec_stop", {"id": j["id"]})
    assert r == {"ok": True} and w.rec.cancelled == [j["id"]] and w._phone_rec_state() is None
    assert w._phone_do("rec_stop", {"id": j["id"]}) == {"ok": False}       # over already
    assert w._phone_do("rec_stop", {"id": "nope"}) == {"ok": False}


def test_dont_ask_again_is_one_key_for_every_phone(window):
    from dopeiptv.ui.mw_phone_rec import REC_ASK_KEY
    w = window
    assert w._phone_do("rec", {"dont": True})["ok"]
    assert w.settings.value(REC_ASK_KEY) == "false" and w._phone_rec_asks() is False
    assert w._phone_do("rec_ask", {"ask": True}) == {"ok": True, "rec_ask": True}
    assert w._phone_do("rec_ask", {"ask": False}) == {"ok": True, "rec_ask": False}


def test_only_a_live_channel_airing_now_is_recorded(window):
    w = window
    w._last_playback = {"kind": "movie", "title": "Sintel", "item": {}}
    assert w._phone_do("rec", {}) == {"ok": False} and not w.rec.jobs
    w._last_playback = {"kind": "live", "title": "SVT1", "item": dict(CH)}
    w._playing_catchup = True                         # an archive's stream is not what airs now
    assert w._phone_do("rec", {}) == {"ok": False} and not w.rec.jobs
    w._playing_catchup = False
    # Nothing plays (the stream stopped or failed), or the docked player does
    # not show it: Record would open a connection of its own - nothing.
    w.player.current_url = None
    assert w._phone_do("rec", {}) == {"ok": False} and not w.rec.jobs
    w.player.current_url = "http://x/live/u/p/7.ts"
    w.player.visible = False
    assert w._phone_do("rec", {}) == {"ok": False} and not w.rec.jobs
    w.player.visible = True
    w._playing_key = "live:8"                         # another channel than the one it names
    assert w._phone_do("rec", {}) == {"ok": False} and not w.rec.jobs


def test_what_stops_a_recording_is_said_on_the_phone_not_left_here(window):
    w = window
    w.rec.full = True
    r = w._phone_do("rec", {})
    assert r["ok"] is False and r["note"] == tr("rec_cap_reached", used="9.0 GB", cap="8.0 GB")
    assert not w.rec.jobs


def test_where_the_app_asks_in_a_recording_the_phone_asks_and_only_its_answer_goes(window):
    w = window
    # The player cannot record its own stream: the window asks what to do -
    # here that question is the phone's, the same words and choices.
    w.player.can_record = False
    w.rec.holds = True
    r = w._phone_do("rec", {})
    ask = r["ask"]
    assert r["ok"] is False and ask["title"] == tr("rec_switch_title") and not w.rec.jobs
    assert [c["label"] for c in ask["choices"]] == [tr("rec_switch_and_record"), tr("rec_record_background"),
                                                   tr("common_cancel")]
    assert w._phone_do("rec", {"answers": [{"q": ask["q"], "i": 2}]}) == {"ok": False} and not w.rec.jobs
    # In the background: a connection of its own - one too many, so it waits
    # as on the computer, and the phone says so.
    r = w._phone_do("rec", {"answers": [{"q": ask["q"], "i": 1}]})
    assert r == {"ok": False, "note": tr("rec_held", title="SVT1")}
    assert w.rec.jobs[-1]["_held"] and w.rec.jobs[-1]["status"] == "scheduled"
    # Recordings offers it: a held row, Record anyway asked as above.
    rows = w._phone_rec_jobs()
    assert rows[-1]["held"] is True and rows[-1]["status"] == "scheduled"


def test_the_state_says_whether_to_ask_what_records_and_a_later_question(window):
    w = window
    w._remote_status = lambda: {"state": "playing", "volume": 40, "kind": "live", "title": "SVT1"}
    w._phone_stamp = lambda: "s"
    w._phone_sections = lambda: []
    w._phone_channel = lambda item, title: {"id": item.get("stream_id"), "name": title}
    w.xmltv = None
    st, target = w._phone_state_parts()
    assert st["rec_ask"] is True and st["can_rec"] is True and "rec" not in st and "ask" not in st
    assert target is not None
    w._phone_do("rec", {"dont": True})
    st2, _t = w._phone_state_parts()
    assert st2["rec_ask"] is False and st2["rec"]["title"] == "SVT1" and st2["rec"]["here"] is True
    # Steady while it records: the same state again, the same tag.
    from dopeiptv.core.phone_remote import state_tag
    assert state_tag(dict(st2, time=1)) == state_tag(dict(w._phone_state_parts()[0], time=2))
    w._playing_catchup = True
    assert "can_rec" not in w._phone_state_parts()[0]
    w._phone_late = (time.monotonic(), {"q": "abc", "title": "T", "text": "M", "choices": []})
    assert w._phone_state_parts()[0]["ask"]["q"] == "abc"


# -- the next programme ----------------------------------------------------------------------------

def test_the_next_programme_lands_in_the_job_list_with_the_padding(window):
    from dopeiptv.core import series_rules
    w = window
    w.settings.setValue(series_rules.PAD_BEFORE_KEY, 2)
    w.settings.setValue(series_rules.PAD_AFTER_KEY, 5)
    start = int(time.time()) + 3600
    prog = {"title": "Rapport", "start": start, "stop": start + 1800}
    r = w._phone_do("_rec_next", {"item": dict(CH), "prog": prog, "what": "record"})
    j = w.rec.jobs[-1]
    assert r["ok"] is True and j["status"] == "scheduled" and j["title"] == "Rapport"
    assert j["start"] == start - 120 and j["stop"] == start + 1800 + 300
    assert j["prog_start"] == start and j["stream_key"] == "7" and j["url"] == "http://x/live/7.ts"
    assert "Rapport" in r["note"]
    # Reminded: the guide's own reminder, once.
    r = w._phone_do("_rec_next", {"item": dict(CH), "prog": prog, "what": "remind"})
    assert r == {"ok": True, "note": tr("reminder_set", title="Rapport")}
    assert w.reminders.added == [(7, "Rapport", start, start + 1800)]
    assert w._phone_do("_rec_next", {"item": dict(CH), "prog": prog, "what": "remind"}) == {"ok": True}
    assert len(w.reminders.added) == 1
    assert w._phone_do("_rec_next", {"item": dict(CH), "prog": {}, "what": "record"}) == {"ok": False}


def test_the_next_programme_is_the_one_the_page_saw(window):
    from types import SimpleNamespace

    w = window
    start = int(time.time()) + 3600
    guide = SimpleNamespace(is_loaded=lambda: True, listings_for=lambda item, n: [
        {"title": "Now", "start_timestamp": start - 7200, "stop_timestamp": start},
        {"title": "Rapport", "start_timestamp": start, "stop_timestamp": start + 1800}])
    calls: list[tuple] = []

    class Bridge:
        def call(self, action, params):
            calls.append((action, params))
            if action == "_state":
                return {}, (guide, dict(CH))
            return w._phone_do(action, params)

    w._phone_bridge = Bridge()
    assert w._phone_call("rec_next", {"start": start - 60, "what": "record"}) == {"ok": False}
    assert [c[0] for c in calls] == ["_state"] and not w.rec.jobs
    r = w._phone_call("rec_next", {"start": start, "what": "record"})
    assert r["ok"] and w.rec.jobs[-1]["title"] == "Rapport"


# -- the recordings list and the page -----------------------------------------------------------

def test_the_recordings_list_shows_what_records_and_what_is_to_come(window):
    w = window
    now = time.time()
    w.rec.jobs += [{"id": "a", "title": "Live", "status": "recording", "start": now - 60, "channel": "SVT1"},
                   {"id": "b", "title": "Later", "status": "scheduled", "start": now + 600, "channel": "K5"},
                   {"id": "c", "title": "Held", "status": "scheduled", "start": now - 5, "channel": "",
                    "_held": True},
                   {"id": "d", "title": "Old", "status": "done", "start": now - 9000, "channel": ""}]
    snap = {"dir": "/nowhere/at/all", "rec_exts": (".ts",), "read_info": lambda p: {},
            "jobs": w._phone_rec_jobs(), "now_title": "NOW", "later_title": "LATER",
            "files_title": "FILES", "waiting": "WAITING"}
    rows = w._pb_recordings(snap)
    assert [r["k"] for r in rows] == ["head", "job", "head", "job", "job"]
    assert [r.get("state") for r in rows if r["k"] == "job"] == ["rec", "held", "sched"]
    assert rows[0]["name"] == "NOW" and rows[2]["name"] == "LATER"
    assert "WAITING" in rows[3]["sub"] and rows[4]["sub"].startswith("K5 · ")


def test_the_page_asks_before_recording_and_always_before_stopping():
    script, html = phone_page._JS, phone_page._HTML
    # Its own row under Now playing's others, never among the transport keys.
    row = html.index('id="nowRec"')
    assert html.index('id="nowActs"') < row < html.index('id="recBtn"') < html.index('id="dock"')
    assert "recBtn" not in html[html.index('id="dock"'):]
    # Record asks first unless the household turned that off; a long press asks anyway.
    body = re.search(r"function recPressed\(\) \{(.*?)\n\}", script, re.S).group(1)
    assert "stopAsk(" in body and "if (st.rec_ask) recAsk(); else recNow(false);" in body
    assert "recAsk()" in re.search(r"function initRec\(\) \{(.*?)\n\}", script, re.S).group(1)
    # Stop always asks: never a stop request without its sheet.
    assert script.count('send("api/rec_stop"') == 2
    for m in re.finditer(r'send\("api/rec_stop"', script):
        assert "closeSheet();" in script[m.start() - 40:m.start()]
    # The app's question answered there: the same request again, with the answer.
    ask = re.search(r"function askSheet\(ask, path, body\) \{(.*?)\n\}", script, re.S).group(1)
    assert "send(path, Object.assign({}, body || {}, {answers}))" in ask
    send = re.search(r"async function send\(path, body\) \{(.*?)\n\}", script, re.S).group(1)
    assert "quickUntil = Date.now() +" in send and "askSheet(r.ask, path, body)" in send
    # Its words are the app's.
    assert {"rec", "rec_stop", "rec_this", "remind", "dont_ask", "rec_ask", "rec_stop_ask"} <= set(phone_page.WORDS)
