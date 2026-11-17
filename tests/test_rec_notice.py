"""A running recording on the activity line for as long as it runs, and a
click on it asking before it stops (mw_recording's `_rec_notices`,
`_rec_ask_stop`)."""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from dopeiptv.i18n import tr  # noqa: E402
from dopeiptv.ui.mw_recording import REC_NOTICE_MS, _RecordingMixin  # noqa: E402
from dopeiptv.ui.notices import NoticeHub  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _Rec:
    def __init__(self, jobs):
        self.jobs = jobs
        self.cancelled = []

    def cancel(self, jid):
        self.cancelled.append(jid)
        for j in self.jobs:
            if j["id"] == jid:
                j["status"] = "done"


class _Win(_RecordingMixin):
    def __init__(self, jobs, answer=None):
        self.notices = NoticeHub()
        self.notices.SHOW_AFTER_MS = self.notices.MIN_SHOWN_MS = 0
        self.rec = _Rec(jobs)
        self.asked = []
        self._answer = answer

    def _choice_dialog(self, title, message, options, dont_ask_setting=None):
        self.asked.append((title, message, [o[0] for o in options]))
        return self._answer


def _shown(app, hub, secs=2.0):
    end = time.monotonic() + secs
    while time.monotonic() < end and not hub.entries():
        app.processEvents()
        time.sleep(0.005)
    return hub.entries()


def _job(jid, title, status="recording"):
    return {"id": jid, "title": title, "status": status}


def test_each_recording_stays_on_the_line_as_long_as_it_runs(app):
    w = _Win([_job("a", "Kvällsnytt"), _job("b", "Sport", status="scheduled")])
    w._rec_notices()
    w._rec_notices()                                   # a change again: not twice
    jobs = w.notices.jobs()
    assert [j.text for j in jobs] == [tr("notice_recording", title="Kvällsnytt")]
    [j] = jobs
    assert j.kind == "record" and j.mine and j.act is not None
    assert j.cancel_text == tr("rec_stop_recording")
    assert REC_NOTICE_MS >= 24 * 3600 * 1000           # never let go mid-recording
    w.rec.jobs[1]["status"] = "recording"
    w._rec_notices()
    assert len(w.notices.jobs()) == 2                  # two: picked in the panel
    w.rec.jobs[0]["status"] = "done"
    w._rec_notices()
    assert [j.text for j in w.notices.jobs()] == [tr("notice_recording", title="Sport")]


def test_a_click_asks_and_only_stop_stops_it(app):
    w = _Win([_job("a", "Kvällsnytt")], answer=1)      # Keep recording
    w._rec_notices()
    assert _shown(app, w.notices)
    act = w.notices.direct()
    assert act is not None and act() is False
    assert w.rec.cancelled == [] and len(w.notices.jobs()) == 1
    title, message, options = w.asked[0]
    assert title == tr("rec_stop_ask_title")
    assert message == tr("rec_stop_ask", title="Kvällsnytt")
    assert options == [tr("rec_stop_recording"), tr("rec_keep_recording")]
    w._answer = None                                    # closed: nothing stops
    assert act() is False and w.rec.cancelled == []
    w._answer = 0
    token = w.notices.jobs()[0].token
    assert w.notices.request_cancel([token])            # the panel's Stop recording
    assert w.rec.cancelled == ["a"] and w.notices.jobs() == []


def test_a_recording_that_ended_meanwhile_is_not_asked_about(app):
    w = _Win([_job("a", "Kvällsnytt", status="done")], answer=0)
    assert w._rec_ask_stop("a") is True and w.asked == [] and w.rec.cancelled == []
