"""The app's notices in one place (ui/notices.py, ui/notice_widgets.py):
jobs under tokens that always end - finished, failed, cancelled, replaced
or let go by their own timeout, a late answer touching nothing -, shown only
after a moment and then for at least a moment, the person's own first,
groups merged, a panel with Cancel; cards that stack three high with the
rest folded, the newest on top and the ones that ask above, count the same
words, stay when they wait for an answer or say an error, pause under the
pointer and wait out the full screen; the window's older ways in routed
there (mw_busy); and on the real window the activity at the sidebar's foot,
the cards beside it in the list pane - never over the video."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

import pytest

pytest.importorskip("PyQt6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from dopeiptv.i18n import tr  # noqa: E402
from dopeiptv.ui import theme  # noqa: E402
from dopeiptv.ui.mw_busy import _BusyMixin  # noqa: E402
from dopeiptv.ui.notice_widgets import ActivityLine, ActivityPanel, CardStack, colours  # noqa: E402
from dopeiptv.ui.notices import Activity, NoticeHub, hub_of  # noqa: E402

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


def _hub(show_after=0, min_shown=0):
    hub = NoticeHub()
    hub.SHOW_AFTER_MS, hub.MIN_SHOWN_MS = show_after, min_shown
    return hub


# -- jobs ----------------------------------------------------------------------

def test_a_job_ends_by_its_own_token_only(app):
    hub = _hub()
    a = hub.start("Loading guide", key="guide")
    b = hub.start("Reading the library")
    assert [j.text for j in hub.jobs()] == ["Loading guide", "Reading the library"]
    assert hub.finish(a) and not hub.finish(a)          # once
    assert hub.alive(b) and not hub.alive(a)
    assert hub.cancel(b) and hub.jobs() == []
    assert not hub.update(b, "late") and not hub.cancel(0)


def test_a_newer_job_of_the_same_key_replaces_it_and_the_old_answer_is_moot(app):
    hub = _hub()
    old = hub.start("Checking catch-up…", key="ts_check")
    new = hub.start("Checking catch-up…", key="ts_check")
    assert [j.token for j in hub.jobs()] == [new]
    assert not hub.finish(old)                          # the old answer: nothing
    assert not hub.fail(old, "failed") and hub.cards() == []
    assert hub.alive(new)
    assert hub.end_key("ts_check") and not hub.end_key("ts_check")


def test_a_failure_ends_the_job_and_says_so(app):
    hub = _hub()
    t = hub.start("Downloading yt-dlp")
    assert hub.fail(t, "The download failed")
    assert hub.jobs() == []
    [card] = hub.cards()
    assert card.title == "The download failed" and card.level == "error"


def test_a_job_nobody_ends_is_let_go_and_a_word_keeps_it(app):
    hub = _hub()
    told = []
    t = hub.start("Loading guide", timeout_ms=40, on_expire=lambda: told.append(1))
    for _ in range(6):                                  # it keeps talking
        assert hub.update(t)
        _spin(app, secs=0.02)
    assert hub.alive(t)
    assert _spin(app, lambda: not hub.alive(t))         # then it is quiet
    assert told == [1] and not hub.finish(t)


def test_every_job_has_a_time_limit_and_idle_costs_nothing(app):
    hub = NoticeHub()
    t = hub.start("Work", timeout_ms=0)                 # none asked: the default
    assert hub._job_timers[t].interval() == NoticeHub.JOB_TIMEOUT_MS
    assert hub._vis.isActive()                          # waiting to show it
    hub.cancel(t)
    assert not hub._vis.isActive() and not hub._job_timers


def test_short_work_never_shows_and_shown_work_stays_a_moment(app):
    hub = NoticeHub()
    hub.SHOW_AFTER_MS, hub.MIN_SHOWN_MS = 120, 250
    quick = hub.start("Quick")
    _spin(app, secs=0.05)
    hub.finish(quick)
    _spin(app, secs=0.2)
    assert hub.entries() == []                          # never flickered on
    slow = hub.start("Slow")
    assert hub.entries() == []
    assert _spin(app, lambda: bool(hub.entries()))      # after the moment
    hub.finish(slow)                                    # done at once...
    assert [e.text for e in hub.entries()] == ["Slow"]  # ...still shown
    assert _spin(app, lambda: not hub.entries())        # then gone
    assert not hub._vis.isActive()


def test_a_replacement_of_shown_work_stays_on_screen(app):
    hub = NoticeHub()
    hub.SHOW_AFTER_MS = 60
    hub.start("Loading channels…", key="list")
    assert _spin(app, lambda: bool(hub.entries()))
    hub.start("Loading movies…", key="list")            # the newer words, no gap
    assert [e.text for e in hub.entries()] == ["Loading movies…"]


def test_the_line_leads_with_the_persons_own_work_then_the_oldest(app):
    hub = _hub()
    hub.start("Checking for updates…", kind="update")
    hub.start("Loading guide…", kind="guide")
    _spin(app, secs=0.02)
    assert hub.line_text() == "Checking for updates…  +1"
    hub.start("Reading folder Music…", kind="folder", mine=True)
    _spin(app, secs=0.02)
    assert hub.line_text() == "Reading folder Music…  +2"


def test_a_group_is_one_line_its_progress_summed(app):
    hub = _hub()
    words = lambda n: tr("notice_guide_sources", n=n)  # noqa: E731
    for k, done in (("a", 10.0), ("b", 50.0), ("c", 90.0)):
        hub.start("Loading guide…", key=k, kind="guide", group="guide", group_text=words,
                  done=done, total=100.0)
    _spin(app, secs=0.02)
    [e] = hub.entries()
    assert e.text == words(3) and abs(e.progress - 0.5) < 1e-9 and len(e.tokens) == 3
    hub.update(e.tokens[0], total=0.0)                  # one of them unknown now
    assert hub.entries()[0].progress is None


def test_cancel_reaches_the_producer_where_it_can_stop(app):
    hub = _hub()
    stopped = []
    a = hub.start("Scanning the network…", cancel=lambda: stopped.append("scan"))
    b = hub.start("Loading guide…")
    _spin(app, secs=0.02)
    by = {e.tokens[0]: e for e in hub.entries()}
    assert by[a].cancellable and not by[b].cancellable
    assert not hub.request_cancel([b]) and hub.alive(b)  # no way to stop it
    assert hub.request_cancel([a]) and stopped == ["scan"] and not hub.alive(a)


def test_a_producer_on_a_worker_thread_is_heard_in_order(app):
    hub = _hub()

    class Win:
        notices = hub
    seen = []

    def work():
        job = Activity(hub, "Searching in Movies…", kind="search")
        for n in range(5):
            job.say(f"Searching in Movies… · {n} found", done=n, total=4)
        job.finish()
        seen.append(job)

    t = threading.Thread(target=work)
    t.start()
    t.join(5)
    assert _spin(app, lambda: seen and seen[0].token and not hub.alive(seen[0].token))
    assert hub.jobs() == []
    with pytest.raises(ValueError):
        with Activity(hub_of(Win()), "Reading folder Music…", kind="folder"):
            raise ValueError("no such folder")
    assert _spin(app, lambda: "no such folder" in hub.texts())
    assert hub.jobs() == []


# -- cards ---------------------------------------------------------------------

def test_cards_newest_on_top_the_asking_above_the_same_words_counted(app):
    hub = _hub()
    hub.post("One")
    hub.post("Two")
    ask = hub.post("Port 3673 is taken", action=lambda: None, action_label="Use port 3674")
    hub.post("Three")
    assert [c.title for c in hub.shown_cards()] == ["Port 3673 is taken", "Three", "Two", "One"]
    again = hub.post("Two")
    assert [c.title for c in hub.shown_cards()][:2] == ["Port 3673 is taken", "Two"]
    assert next(c for c in hub.cards() if c.id == again).count == 2
    k = hub.post("Channel: 1", key="chan_entry")
    assert hub.post("Channel: 12", key="chan_entry") == k        # words follow, no count
    assert next(c for c in hub.cards() if c.id == k).count == 1
    assert hub.dismiss_key("chan_entry") and hub.dismiss(ask) and not hub.dismiss(ask)
    assert hub.post("") == 0
    cid = hub.post("Port is taken", title="Server mode")
    card = next(c for c in hub.cards() if c.id == cid)
    assert (card.title, card.body) == ("Server mode", "Port is taken")


def test_passing_notes_go_the_asking_and_errors_stay(app):
    hub = _hub()
    hub.CARD_MIN_MS = 10
    hub.post("Passing", ms=40)
    hub.post("Answer me", action=lambda: None, action_label="Use")
    hub.post("Stream error: HTTP 404", level="error", ms=40)
    assert _spin(app, lambda: "Passing" not in hub.texts())
    _spin(app, secs=0.1)
    assert hub.texts() == ["Answer me", "Stream error: HTTP 404"]


def test_a_note_under_the_pointer_waits(app):
    hub = _hub()
    hub.CARD_MIN_MS = 10
    hub.post("Hold me", ms=60)
    hub.pause(True)
    _spin(app, secs=0.15)
    assert hub.texts() == ["Hold me"]
    hub.pause(False)
    assert _spin(app, lambda: not hub.texts())


def test_a_cards_action_runs_once_after_it_goes(app):
    hub = _hub()
    done = []
    c = hub.post("Use port 3674?", action=lambda: done.append(hub.texts()),
                 action_label="Use port 3674")
    assert hub.activate(c) and not hub.activate(c)
    assert done == []                                    # after this event
    _spin(app, lambda: bool(done))
    assert done == [[]]


def test_held_cards_wait_and_a_stale_note_is_dropped(app):
    hub = _hub()
    hub.CARD_MIN_MS = 10
    hub.hold(True)
    hub.post("Recording started", ms=30)
    hub.post("Answer me", action=lambda: None)
    _spin(app, secs=0.15)
    assert hub.texts() == ["Recording started", "Answer me"]      # no time ran
    hub.cards()[0].posted -= NoticeHub.HELD_STALE_S + 1
    hub.hold(False)
    assert hub.texts() == ["Answer me"]


def test_hub_of_a_window_without_one_does_nothing():
    class Bare:
        pass
    hub = hub_of(Bare())
    assert hub.start("x") == 0 and hub.post("x") == 0 and hub.jobs() == []
    assert not hub.finish(1) and hub.texts() == [] and hub.entries() == []


# -- drawn ---------------------------------------------------------------------

def _stack(app, rtl=False):
    host = QWidget()
    host.resize(900, 600)
    if rtl:
        host.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    host.show()
    hub = _hub()
    stack = CardStack(hub, host)
    stack.set_anchor(QRect(200, 0, 500, 560), rtl)
    return host, hub, stack


def test_three_cards_show_the_rest_fold_and_open(app):
    host, hub, stack = _stack(app)
    for t in ("One", "Two", "Three", "Four"):
        hub.post(t)
    shown = stack.card_widgets()
    assert [w.title.text() for w in shown] == ["Four", "Three", "Two"]
    assert stack.fold.isVisible() and stack.fold.text() == "+" + tr("notice_more", n=1)
    QTest.mouseClick(stack.fold, Qt.MouseButton.LeftButton)
    assert len(stack.card_widgets()) == 4 and stack.fold.text() == tr("notice_less")
    card = stack.card_widgets()[0]
    assert card.accessibleName() == "Four" and card.accessibleDescription() == tr("notice_info")
    assert card.close_btn.accessibleName() == tr("notice_dismiss")
    QTest.mouseClick(card.close_btn, Qt.MouseButton.LeftButton)
    assert "Four" not in hub.texts() and not stack.fold.isVisible()
    g = stack.geometry()
    assert g.left() == 200 + CardStack.INSET and g.bottom() <= 560
    host.close()


def test_the_cards_corner_is_the_leading_one_and_mirrors(app):
    host, hub, stack = _stack(app, rtl=True)
    hub.post("Note")
    assert stack.geometry().right() == QRect(200, 0, 500, 560).right() - CardStack.INSET
    host.close()


def test_nothing_is_drawn_while_held_or_without_a_place(app):
    host, hub, stack = _stack(app)
    hub.post("Note")
    assert stack.isVisible()
    hub.hold(True)
    assert stack.isHidden() and hub.texts() == ["Note"]
    hub.hold(False)
    assert stack.isVisible()
    stack.set_anchor(None)
    assert stack.isHidden()
    host.close()


def test_the_line_says_the_work_and_its_panel_lists_and_stops_it(app):
    host = QWidget()
    host.resize(900, 600)
    host.show()
    hub = _hub()
    line = ActivityLine(hub, host, floating=True)
    line.setGeometry(16, 540, 280, ActivityLine.H)
    panel = ActivityPanel(hub, host)
    line.open_panel.connect(lambda: panel.open_at(line))
    hub.changed.connect(line.refresh)
    stopped = []
    a = hub.start(tr("notice_reading_folder", name="Music"), kind="folder", mine=True,
                  cancel=lambda: stopped.append(1))
    b = hub.start(tr("status_loading_programme_guide"), kind="guide", done=45, total=100)
    assert _spin(app, lambda: line.isVisible())
    assert line.text() == tr("notice_reading_folder", name="Music") + "  +1"
    assert tr("notice_working") in line.accessibleName() and "45%" in line.toolTip()
    QTest.mouseClick(line, Qt.MouseButton.LeftButton)
    assert panel.isVisible()
    rows = [panel._rows.itemAt(i).widget() for i in range(panel._rows.count())]
    assert len(rows) == 2 and "45%" in rows[1].accessibleName()
    assert panel.geometry().bottom() < line.geometry().top()          # upward from it
    from PyQt6.QtWidgets import QPushButton
    [cancel] = rows[0].findChildren(QPushButton)
    cancel.click()
    assert stopped == [1] and not hub.alive(a)
    assert _spin(app, lambda: panel._rows.count() == 1)               # live
    hub.finish(b)
    assert _spin(app, lambda: panel.isHidden())                       # by itself
    line.set_ring(True)
    assert line.sizeHint().width() == ActivityLine.H
    host.close()


def test_every_theme_reads(app):
    try:
        for key in theme.THEMES:
            theme.apply_theme(theme=key)
            c = colours()
            for text, on in (("title", "surface"), ("body", "surface"), ("muted", "surface"),
                             ("action", "action_bg"), ("line_text", "line_bg"),
                             ("line_more", "line_bg"), ("float_text", "float_bg")):
                assert theme.contrast(c[text], c[on]) >= 4.5, (key, text, on)
            assert theme.contrast(c["dismiss"], c["surface"]) >= 3.0, key
            assert theme.contrast(c["surface"], theme.P["pane"]) >= 1.1, key
    finally:
        theme.apply_theme(theme="graphite")


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
    assert w.notices.texts() == ["Saved", "Guide cache cleared", "Click here to read it",
                                 "Port 3673 is taken"]
    assert w.notices.cards()[-1].action_label == "Use port 3674"
    assert all(c.ms == 0 for c in w.notices.cards() if c.action)     # they wait
    w._show_busy("Loading channels…")
    w._show_busy("Loading movies…")                       # the newest words win
    assert [(j.text, j.kind, j.mine) for j in w.notices.jobs()] == [("Loading movies…", "list", True)]
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
    assert w.notices.cards()[-1].level == "error" and w.notices.cards()[-1].ms == 0
    assert w.line == "Playing: News"


def test_the_guide_says_how_far_it_got(app):
    w = _Window()
    w.notices.SHOW_AFTER_MS = 0
    t = w._guide_busy_begin()
    w._on_epg_progress(t, 40)
    [job] = w.notices.jobs()
    assert (job.kind, job.group, job.progress()) == ("guide", "guide", 0.4)
    w._on_epg_progress(t, 100)                               # the parse: no percentage
    assert job.text == tr("notice_guide_preparing") and job.progress() is None
    w._guide_busy_end(t)
    assert w.notices.jobs() == []


# -- the real window -----------------------------------------------------------

_CHILD = r"""
import os, sys, tempfile, time
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
s.setValue("check_updates", "false")
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
        time.sleep(0.003)


def rect(widget):
    return QRect(widget.mapTo(w, QPoint(0, 0)), widget.size())


pump(40)
w.switch_mode("live")
pump(30)
hub, cards, line = w.notices, w._notice_cards, w._activity_line
hub.SHOW_AFTER_MS = hub.MIN_SHOWN_MS = 0
for j in hub.jobs():
    hub.cancel(j.token)
pump(5)
assert not hasattr(w, "loading_bar") and not hasattr(w, "_toast")
# The activity: at the sidebar's foot, just above the source avatar.
w._show_busy(tr("status_loading_channels"))
pump(5)
assert line.isVisible() and line.text() == tr("status_loading_channels")
side, avatar = rect(w._side), rect(w._playlist_btn)
assert side.contains(rect(line)) and rect(line).bottom() < avatar.top(), (rect(line), avatar)
w._apply_filter()
pump(10)
assert not hub.jobs(), [j.text for j in hub.jobs()]
# The cards: from the bottom of the list pane on the sidebar's side, above
# its status line - never in the player's pane, never over the sidebar.
w._show_toast("A note", 60_000)
t = w._guide_busy_begin()
w._on_epg_progress(t, 10)
pump(10)
g, mid, det = rect(cards), rect(w._mid), rect(w._det)
assert mid.contains(g) and not det.intersects(g) and not side.intersects(g), (mid, g)
assert g.bottom() < rect(w.count_lbl).top() + 16, g
assert abs(g.left() - mid.left() - 16) <= 1, (g, mid)
assert hub.texts() == ["A note"], hub.texts()
assert line.text().startswith(tr("status_loading_programme_guide")), line.text()
# The panel of jobs opens upward from the line; the cards step aside.
line.open_panel.emit()
pump(10)
panel = w._notice_panel
assert panel.isVisible() and rect(panel).bottom() < rect(line).top()
assert not rect(panel).intersects(rect(cards)), (rect(panel), rect(cards))
panel.close_panel()
pump(5)
# The list loading meanwhile leaves the guide's job; the guide's end ends it.
w._show_busy(tr("status_loading_channels"))
w._hide_busy()
pump(3)
assert [j.key for j in hub.jobs()] == ["guide"]
w._guide_busy_end(t)
pump(3)
assert not hub.jobs()
# The video fills the screen: nothing drawn, the card waits.
w._player_fs = True
w._notices_place()
pump(3)
assert cards.isHidden() and hub.held and hub.texts() == ["A note"]
w._player_fs = False
w._notices_place()
pump(3)
assert cards.isVisible() and not hub.held
# The icon rail: the line becomes a ring.
w._set_sidebar_collapsed(True)
w._show_busy(tr("status_loading_channels"))
pump(10)
assert line.isVisible() and line.sizeHint().width() == line.H
w._hide_busy()
w._set_sidebar_collapsed(False)
pump(5)
# A typed channel number with no player: one card following the digits.
w._chan_entry_show("1", 2500)
w._chan_entry_show("12", 2500)
assert [x for x in hub.texts() if x.startswith(tr("chan_entry", num="1"))] == [tr("chan_entry", num="12")]
w._chan_entry_show("", 0)
assert tr("chan_entry", num="12") not in hub.texts()
# Home has no sidebar: the line at the same corner, the cards above it.
s.setValue("home_enabled", "true")
w._show_home_page()
job = hub.start(tr("sync_running"), kind="sync")
pump(10)
fl, central = w._activity_float, rect(w._center_stack)
assert fl.isVisible() and not line.isVisible() or not w._side.isVisible()
assert central.contains(rect(fl)) and rect(fl).left() - central.left() <= 20, rect(fl)
assert not rect(cards).intersects(rect(fl)) and rect(cards).bottom() < rect(fl).top()
hub.finish(job)
w.switch_mode("live")
pump(10)
assert fl.isHidden()
# An error: a card that stays, the status line keeps the list's words.
line_words = w.count_lbl.text()
w._error("boom")
pump(2)
assert hub.cards()[-1].level == "error" and "boom" in hub.texts()[-1] and hub.cards()[-1].ms == 0
assert w.count_lbl.text() == line_words
print("NOTICES_WINDOW_OK")
"""


def test_the_real_window_has_one_corner_for_its_notices():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "NOTICES_WINDOW_OK" in out.stdout, out.stdout[-2000:] + out.stderr[-4000:]
