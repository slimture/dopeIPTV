"""The app's notices in one place (ui/notices.py): jobs under tokens that
always end - finished, failed, cancelled, replaced or let go by their own
timeout, a late answer touching nothing -, cards that stack, refresh, time
out, wait for an answer, and are held while the video fills the screen; the
window's older ways in routed there (mw_busy); and on the real window the
corner in the list pane, above the status line, never over the video."""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

pytest.importorskip("PyQt6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from dopeiptv.i18n import tr  # noqa: E402
from dopeiptv.ui.mw_busy import _BusyMixin  # noqa: E402
from dopeiptv.ui.notices import NoticeArea, NoticeHub, hub_of  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _spin(app, until=None, secs=3.0):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.005)
    return until() if until is not None else True


# -- jobs ----------------------------------------------------------------------

def test_a_job_ends_by_its_own_token_only(app):
    hub = NoticeHub()
    a = hub.start("Loading guide", key="guide")
    b = hub.start("Reading the library")
    assert [j.text for j in hub.jobs()] == ["Reading the library", "Loading guide"]
    assert hub.finish(a) and not hub.finish(a)          # once
    assert hub.alive(b) and not hub.alive(a)
    assert hub.cancel(b) and hub.jobs() == []
    assert not hub.update(b, "late") and not hub.cancel(0)


def test_a_newer_job_of_the_same_key_replaces_it_and_the_old_answer_is_moot(app):
    hub = NoticeHub()
    old = hub.start("Checking archive…", key="ts_check")
    new = hub.start("Checking archive…", key="ts_check")
    assert [j.token for j in hub.jobs()] == [new]
    assert not hub.finish(old)                          # the old answer: nothing
    assert not hub.fail(old, "failed") and hub.cards() == []
    assert hub.alive(new)
    assert hub.end_key("ts_check") and not hub.end_key("ts_check")


def test_a_failure_ends_the_job_and_says_so(app):
    hub = NoticeHub()
    t = hub.start("Downloading yt-dlp")
    assert hub.fail(t, "The download failed")
    assert hub.jobs() == []
    [card] = hub.cards()
    assert card.text == "The download failed" and card.level == "error"


def test_a_job_nobody_ends_is_let_go_and_a_word_keeps_it(app):
    hub = NoticeHub()
    told = []
    t = hub.start("Loading guide", timeout_ms=40, on_expire=lambda: told.append(1))
    for _ in range(6):                                  # it keeps talking
        assert hub.update(t)
        _spin(app, secs=0.02)
    assert hub.alive(t)
    assert _spin(app, lambda: not hub.alive(t))         # then it is quiet
    assert told == [1] and not hub.finish(t)


def test_every_job_has_a_time_limit(app):
    hub = NoticeHub()
    t = hub.start("Work", timeout_ms=0)                 # none asked: the default
    assert hub._job_timers[t].interval() == NoticeHub.JOB_TIMEOUT_MS
    hub.cancel(t)


# -- cards ---------------------------------------------------------------------

def test_cards_stack_refresh_and_keep_three(app):
    hub = NoticeHub()
    a = hub.post("One")
    hub.post("Two")
    assert hub.post("One") == a and hub.texts() == ["Two", "One"]   # moved, not twice
    hub.post("Three")
    hub.post("Four")
    assert hub.texts() == ["One", "Three", "Four"]
    k = hub.post("Channel: 1", key="chan_entry")
    assert hub.post("Channel: 12", key="chan_entry") == k
    assert hub.texts()[-1] == "Channel: 12" and len(hub.texts()) == 3
    assert hub.dismiss_key("chan_entry") and "Channel: 12" not in hub.texts()
    assert hub.post("") == 0


def test_a_card_times_out_unless_it_waits_for_an_answer(app):
    hub = NoticeHub()
    hub.post("Short", ms=30)
    hub.post("Answer me", action=lambda: None, action_label="Use", answer=True)
    assert _spin(app, lambda: hub.texts() == ["Answer me"])
    _spin(app, secs=0.1)
    assert hub.texts() == ["Answer me"]


def test_a_cards_action_runs_once_after_it_goes(app):
    hub = NoticeHub()
    done = []
    c = hub.post("Use port 3674?", action=lambda: done.append(hub.texts()),
                 action_label="Use port 3674")
    assert hub.activate(c) and not hub.activate(c)
    assert done == []                                    # after this event
    _spin(app, lambda: bool(done))
    assert done == [[]]


def test_held_cards_wait_and_a_stale_note_is_dropped(app):
    hub = NoticeHub()
    hub.hold(True)
    hub.post("Recording started", ms=30)
    hub.post("Stream error", level="error", ms=30)
    _spin(app, secs=0.15)
    assert hub.texts() == ["Recording started", "Stream error"]   # no time ran
    hub.cards()[0].posted -= NoticeHub.HELD_STALE_S + 1
    hub.hold(False)
    assert hub.texts() == ["Stream error"]
    assert _spin(app, lambda: hub.texts() == [])


def test_hub_of_a_window_without_one_does_nothing():
    class Bare:
        pass
    hub = hub_of(Bare())
    assert hub.start("x") == 0 and hub.post("x") == 0 and hub.jobs() == []
    assert not hub.finish(1) and hub.texts() == []


# -- drawn ---------------------------------------------------------------------

def _host(app, rtl=False):
    host = QWidget()
    host.resize(900, 600)
    if rtl:
        host.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    host.show()
    hub = NoticeHub(host)
    area = NoticeArea(hub, host)
    area.set_anchor(QRect(200, 0, 500, 560), rtl)
    return host, hub, area


def test_the_corner_draws_the_jobs_and_cards_and_nothing_else(app):
    host, hub, area = _host(app)
    assert area.isHidden()
    t = hub.start(tr("status_loading_programme_guide"))
    hub.start(tr("local_scanning"))
    assert area.isVisible() and area.activity.isVisible()
    assert area.activity.text() == tr("local_scanning") + " · " + tr("notice_more", n=1)
    assert tr("status_loading_programme_guide") in area.activity.toolTip()
    assert tr("notice_working") in area.activity.accessibleName()
    hub.post("Stream error", level="error")
    [card] = area.card_widgets()
    assert card.accessibleName() == "Stream error"
    assert card.accessibleDescription() == tr("notice_error")
    assert card.close_btn.accessibleName() == tr("notice_dismiss")
    g = area.geometry()
    assert g.right() <= 700 and g.bottom() <= 560 and g.left() >= 200
    assert card.geometry().bottom() < area.activity.geometry().top()   # cards above
    QTest.mouseClick(card.close_btn, Qt.MouseButton.LeftButton)
    assert hub.cards() == [] and area.card_widgets() == []
    for j in hub.jobs():
        hub.finish(j.token)
    assert area.isHidden() and not hub.alive(t)
    host.close()


def test_the_corner_is_the_trailing_one_and_mirrors(app):
    host, hub, area = _host(app)
    hub.post("Note")
    ltr = area.geometry()
    assert ltr.right() == QRect(200, 0, 500, 560).right() - NoticeArea.INSET
    host.close()
    host, hub, area = _host(app, rtl=True)
    hub.post("Note")
    assert area.geometry().left() == 200 + NoticeArea.INSET
    host.close()


def test_nothing_is_drawn_while_held_or_without_a_place(app):
    host, hub, area = _host(app)
    hub.post("Note", ms=60_000)
    assert area.isVisible()
    hub.hold(True)
    assert area.isHidden() and hub.texts() == ["Note"]
    hub.hold(False)
    assert area.isVisible()
    area.set_anchor(None)
    assert area.isHidden()
    host.close()


# -- the window's older ways in ------------------------------------------------

class _Window(_BusyMixin, QWidget):
    def __init__(self):
        super().__init__()
        self.notices = NoticeHub(self)
        self.count_lbl = None
        self._rest_status = None

    def _write_status(self, text, error=False, emphasis=False):
        self.line = text


def test_the_older_calls_land_in_the_hub(app):
    w = _Window()
    w._show_toast("Saved", 3000)
    w._flash_status("Guide cache cleared")
    act = []
    w._show_action_toast("Click here to read it", 9000, lambda: act.append(1))
    w._show_action_toast("Port 3673 is taken", 9000, lambda: act.append(2), label="Use port 3674")
    assert w.notices.texts() == ["Guide cache cleared", "Click here to read it",
                                 "Port 3673 is taken"]
    assert w.notices.cards()[-1].action_label == "Use port 3674"
    w._show_busy("Loading channels…")
    w._show_busy("Loading movies…")                       # the newest words win
    assert [j.text for j in w.notices.jobs()] == ["Loading movies…"]
    w._busy_say("Reading the library… 120")
    assert [j.text for j in w.notices.jobs()] == ["Reading the library… 120"]
    w._hide_busy()
    assert w.notices.jobs() == []


def test_the_status_line_keeps_the_lists_state(app):
    w = _Window()
    w._set_status("Pirat · 120 channels")
    assert w.line == "Pirat · 120 channels"
    w._set_status(tr("status_reconnecting"), emphasis=True)  # activity: a job
    assert [j.text for j in w.notices.jobs()] == [tr("status_reconnecting")]
    assert w.line == "Pirat · 120 channels"
    w._set_status("Playing: News")                           # the next status ends it
    assert w.notices.jobs() == [] and w.line == "Playing: News"
    w._set_status(tr("status_checking_stream"), emphasis=True)
    w._set_status(tr("status_stream_error", msg="404"), error=True)   # an error: a card
    assert w.notices.jobs() == []
    assert w.notices.cards()[-1].level == "error"
    assert w.line == "Playing: News"


# -- the real window -----------------------------------------------------------

_CHILD = r"""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QPoint, QRect, QSettings
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from dopeiptv import i18n
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw
from dopeiptv.ui import theme

i18n.set_language("en")
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
s.setValue("home_enabled", "false")
store = PlaylistStore(s)
pl = store.add({"name": "Pirat", "server": "http://pirat.invalid", "username": "u", "password": "p"})
store.set_active(pl["id"])
theme.apply_theme(s)
w = mw.MainWindow(DemoClient(), s, store)
w.resize(1200, 760)
w.show()


def pump(n=20):
    for _ in range(n):
        app.processEvents()
        w.pool.waitForDone(5)


pump(40)
w.switch_mode("live")
pump(30)
hub, area = w.notices, w._notice_area
for j in hub.jobs():
    hub.cancel(j.token)
pump(5)
assert not hasattr(w, "loading_bar") and not hasattr(w, "_toast")
# The list's work: a job, ended by the list itself.
w._show_busy(tr("status_loading_channels"))
pump(3)
assert area.isVisible() and area.activity.text() == tr("status_loading_channels")
w._apply_filter()
pump(3)
assert not hub.jobs(), [j.text for j in hub.jobs()]
# Its place: inside the list pane, above the status line - never in the
# player's pane.
w._show_toast("A note", 60_000)
w._guide_busy_begin()
tok = w._guide_busy_token
w._on_epg_progress(tok, 10)
pump(5)
mid = QRect(w._mid.mapTo(w, QPoint(0, 0)), w._mid.size())
det = QRect(w._det.mapTo(w, QPoint(0, 0)), w._det.size())
g = area.geometry()
assert mid.contains(g), (mid, g)
assert not det.intersects(g), (det, g)
assert g.bottom() < w.count_lbl.mapTo(w, QPoint(0, 0)).y(), g
assert hub.texts() == ["A note"] and area.activity.text() == tr("status_loading_programme_guide")
# The list loading meanwhile leaves the guide's job; the guide's end ends it.
w._show_busy(tr("status_loading_channels"))
w._hide_busy()
pump(3)
assert [j.key for j in hub.jobs()] == ["guide"]
w._guide_busy_end(tok)
pump(3)
assert not hub.jobs()
# The video fills the screen: nothing drawn, the card waits.
w._player_fs = True
w._notices_place()
pump(3)
assert area.isHidden() and hub.held and hub.texts() == ["A note"]
w._player_fs = False
w._notices_place()
pump(3)
assert area.isVisible() and not hub.held
# A typed channel number with no player: one card following the digits.
w._chan_entry_show("1", 2500)
w._chan_entry_show("12", 2500)
assert [t for t in hub.texts() if "12" in t or ": 1" in t] == [tr("chan_entry", num="12")]
w._chan_entry_show("", 0)
assert tr("chan_entry", num="12") not in hub.texts()
# An error: a card, the status line keeps the list's words.
line = w.count_lbl.text()
w._error("boom")
pump(2)
assert hub.cards()[-1].level == "error" and "boom" in hub.texts()[-1]
assert w.count_lbl.text() == line
print("NOTICES_WINDOW_OK")
"""


def test_the_real_window_has_one_corner_for_its_notices():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "NOTICES_WINDOW_OK" in out.stdout, out.stdout[-2000:] + out.stderr[-4000:]
