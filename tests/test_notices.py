"""The app's notices, calm and in one place (ui/notices.py,
ui/notice_widgets.py, mw_busy): jobs under tokens that always end -
finished, failed, cancelled, replaced or let go by their own timeout, a late
answer touching nothing -, shown only after a moment and then for at least
a moment, the person's own first, groups merged, a panel with Cancel;
passing words on the activity line and gone; errors and messages only a
count on it, listed in the panel ("Working on", "Messages": the newest
first, read ones faded, Clear all); only what needs a decision a small card
over the line, one at a time; stream errors said on the video alone. On the
real window all of it in the sidebar's fixed foot: nothing over the list,
the categories or the video, the categories only shorter while a card shows
(none covered, every one reachable, the chosen one in view) at 700 px high
with 64 long names, the card beside the icon rail."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

import pytest

pytest.importorskip("PyQt6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QFontMetrics  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget  # noqa: E402

from dopeiptv.i18n import tr  # noqa: E402
from dopeiptv.ui import theme  # noqa: E402
from dopeiptv.ui.mw_busy import _BusyMixin  # noqa: E402
from dopeiptv.ui.notice_widgets import (  # noqa: E402
    ActivityLine,
    ActivityPanel,
    DecisionCard,
    _font,
    breakable,
    colours,
    one_line,
    two_lines,
)
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
    assert not hub.fail(old, "failed") and hub.messages() == []
    assert hub.alive(new)
    assert hub.end_key("ts_check") and not hub.end_key("ts_check")


def test_a_failure_ends_the_job_and_is_a_message(app):
    hub = _hub()
    t = hub.start("Downloading yt-dlp")
    assert hub.fail(t, "The download failed")
    assert hub.jobs() == []
    [msg] = hub.messages()
    assert msg.title == "The download failed" and msg.level == "error"
    assert hub.unread() == 1 and hub.unread_level() == "error"


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
    assert not hub._note_timer.isActive()


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
    assert _spin(app, lambda: any("no such folder" in t for t in hub.texts()))
    assert hub.jobs() == [] and hub.messages()[0].level == "error"


# -- notes, messages, decisions ------------------------------------------------

def test_passing_words_are_the_lines_and_then_gone(app):
    hub = _hub()
    hub.NOTE_MIN_MS = 10
    assert hub.post("EPG cache cleared", ms=40) == 0            # no message
    assert hub.note_text() == "EPG cache cleared" and hub.messages() == []
    hub.post("Low disk space", level="warning")                 # nothing to do: a note
    assert hub.note_text() == "Low disk space" and hub.unread() == 0
    hub.note("Copied", ms=40)
    assert _spin(app, lambda: hub.note_text() == "")
    assert hub.said()[-3:] == ["EPG cache cleared", "Low disk space", "Copied"]
    hub.note(tr("chan_entry", num="1"), key="chan_entry")
    hub.note(tr("chan_entry", num="12"), key="chan_entry")      # the words follow
    assert hub.note_text() == tr("chan_entry", num="12")
    assert hub.dismiss_key("chan_entry") and hub.note_text() == ""
    assert hub.post("") == 0 and not hub._note_timer.isActive()


def test_errors_and_actions_are_messages_counted_until_read(app):
    hub = _hub()
    a = hub.post("Could not delete", level="error")
    b = hub.post("dopeIPTV answered your feedback", action=lambda: None)
    assert [m.id for m in hub.messages()] == [b, a]                 # the newest first
    assert hub.unread() == 2 and hub.unread_level() == "error"
    again = hub.post("Could not delete", level="error")
    assert again == a and hub.messages()[0].id == a and hub.messages()[0].count == 2
    k = hub.post("Recording: News", level="error", key="rec")
    assert hub.post("Recording: Sport", level="error", key="rec") == k
    assert next(m for m in hub.messages() if m.id == k).count == 1
    hub.mark_read()
    assert hub.unread() == 0 and all(m.read for m in hub.messages())
    hub.post("Could not delete", level="error")                     # news again
    assert hub.unread() == 1 and not hub.messages()[0].read
    cid = hub.post("Port is taken", title="Server mode", level="error")
    msg = next(m for m in hub.messages() if m.id == cid)
    assert (msg.title, msg.body) == ("Server mode", "Port is taken")
    hub.clear()
    assert hub.messages() == [] and hub.unread() == 0


def test_messages_are_kept_to_a_bound_the_read_ones_first(app):
    hub = _hub()
    hub.MAX_MESSAGES = 3
    first = hub.post("One", level="error")
    hub.mark_read()
    for t in ("Two", "Three", "Four"):
        hub.post(t, level="error")
    assert first not in [m.id for m in hub.messages()]
    assert [m.title for m in hub.messages()] == ["Four", "Three", "Two"]


def test_a_decision_is_the_card_one_at_a_time_and_its_action_runs_once(app):
    hub = _hub()
    done = []
    a = hub.post("Port 3673 is taken", action=lambda: done.append(hub.decisions()),
                 action_label="Use port 3674", decide=True)
    b = hub.post("Another question", action=lambda: None, decide=True)
    assert [m.id for m in hub.decisions()] == [a, b] and hub.unread() == 0
    hub.post("An error", level="error")
    hub.clear()                                                 # decisions wait
    assert [m.id for m in hub.decisions()] == [a, b]
    assert hub.activate(a) and not hub.activate(a)
    assert done == []                                           # after this event
    assert _spin(app, lambda: bool(done))
    assert [m.id for m in done[0]] == [b]
    assert hub.dismiss(b) and hub.decisions() == []


def test_held_drops_a_passing_note_and_keeps_the_rest(app):
    hub = _hub()
    hub.note("Saved")
    hub.hold(True)
    assert hub.note_text() == "" and hub.held
    hub.note("Recording started")                               # no news later
    hub.post("Could not delete", level="error")
    hub.post("Port 3673 is taken", action=lambda: None, decide=True)
    hub.hold(False)
    assert hub.note_text() == "" and hub.unread() == 1 and len(hub.decisions()) == 1


def test_hub_of_a_window_without_one_does_nothing():
    class Bare:
        pass
    hub = hub_of(Bare())
    assert hub.start("x") == 0 and hub.post("x") == 0 and hub.jobs() == []
    assert not hub.finish(1) and hub.texts() == [] and hub.entries() == []
    assert hub.note_text() == "" and hub.messages() == [] and hub.decisions() == []
    assert hub.unread() == 0 and hub.said() == []


# -- words cut to their room -----------------------------------------------------

def test_two_lines_at_most_then_an_ellipsis(app):
    font = _font(13)
    fm = QFontMetrics(font)
    assert two_lines("Short", font, 200) == "Short"
    long = ("Port 3673 is used by another program - the server cannot start there, "
            "pick another one in Settings")
    out = two_lines(long, font, 160)
    lines = out.split("\n")
    assert len(lines) == 2 and lines[1].endswith("…")
    assert all(fm.horizontalAdvance(x) <= 160 for x in lines)
    word = two_lines("Supercalifragilisticexpialidocious" * 3, font, 120)
    assert all(fm.horizontalAdvance(x) <= 120 for x in word.split("\n"))
    assert two_lines("", font, 100) == ""


def test_one_line_keeps_the_count_and_gives_up_the_start(app):
    fm = QFontMetrics(_font(13))
    text = "Searching in Documentaries and nature… · 12 found"
    out = one_line(text, fm, 180)
    assert out.endswith(" · 12 found") and "…" in out and fm.horizontalAdvance(out) <= 180
    assert one_line("Saved", fm, 180) == "Saved"
    plain = one_line("Loading the programme guide for every source", fm, 120)
    assert plain.endswith("…") and fm.horizontalAdvance(plain) <= 120


# -- drawn ---------------------------------------------------------------------

def _host(app, rtl=False):
    host = QWidget()
    host.resize(900, 600)
    if rtl:
        host.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    host.show()
    return host


def test_the_line_says_a_note_the_work_or_the_count(app):
    host = _host(app)
    hub = _hub()
    line = ActivityLine(hub, host)
    line.setGeometry(16, 540, 220, ActivityLine.H)
    hub.changed.connect(line.refresh)
    line.refresh()
    assert line.isHidden()                                      # nothing: nothing drawn
    hub.note("EPG cache cleared")
    assert line.isVisible() and line.text() == "EPG cache cleared"
    hub._note_over()
    hub.start(tr("notice_reading_folder", name="Music"), kind="folder", mine=True)
    hub.start(tr("status_loading_programme_guide"), kind="guide", done=45, total=100)
    _spin(app, secs=0.02)
    assert line.text() == tr("notice_reading_folder", name="Music") + "  +1"
    assert tr("notice_reading_folder", name="Music") in line.accessibleName()
    assert "45%" in line.toolTip()
    for j in hub.jobs():
        hub.cancel(j.token)
    hub.post("Could not delete", level="error")
    hub.post("dopeIPTV answered your feedback", action=lambda: None)
    assert line.badge() == 2 and line.text() == tr("notice_unread", n=2)
    assert tr("notice_unread", n=2) in line.toolTip()
    line.set_ring(True)
    assert line.sizeHint().width() == ActivityLine.H and line.badge() == 2
    hub.mark_read()
    assert line.badge() == 0 and _spin(app, lambda: line.isHidden())    # nothing left
    host.close()


def test_the_panel_lists_the_work_and_the_messages_and_marks_them_read(app):
    host = _host(app)
    hub = _hub()
    line = ActivityLine(hub, host)
    line.setGeometry(16, 540, 240, ActivityLine.H)
    panel = ActivityPanel(hub, host)
    line.open_panel.connect(lambda: panel.open_at(line))
    hub.changed.connect(line.refresh)
    stopped = []
    a = hub.start(tr("notice_reading_folder", name="Music"), kind="folder", mine=True,
                  cancel=lambda: stopped.append(1))
    b = hub.start(tr("status_loading_programme_guide"), kind="guide", done=45, total=100)
    old = hub.post("Could not delete", level="error")
    hub.mark_read()
    new = hub.post("dopeIPTV answered your feedback", action=lambda: None)
    assert _spin(app, lambda: line.isVisible() and len(hub.entries()) == 2)
    QTest.mouseClick(line, Qt.MouseButton.LeftButton)
    assert panel.isVisible() and panel.geometry().bottom() < line.geometry().top()
    assert panel._work_head.text() == tr("notice_working") and panel._work_head.isVisibleTo(panel)
    assert panel._msg_head.text() == tr("notice_messages")
    assert panel.clear_btn.text() == tr("notice_clear_all")
    jobs = [panel._rows.itemAt(i).widget() for i in range(panel._rows.count())]
    msgs = [panel._msgs.itemAt(i).widget() for i in range(panel._msgs.count())]
    assert len(jobs) == 2 and "45%" in jobs[1].accessibleName()
    assert [m.msg_id for m in msgs] == [new, old]                    # the newest first
    from PyQt6.QtWidgets import QLabel
    names = [m.findChildren(QLabel)[0].objectName() for m in msgs]
    assert names == ["NoticeRowText", "NoticeRowRead"]               # read ones faded
    assert msgs[0].accessibleDescription() == tr("notice_info")
    assert msgs[1].accessibleDescription() == tr("notice_error")
    assert hub.unread() == 0 and line.badge() == 0                   # seen now
    [cancel] = jobs[0].findChildren(QPushButton)
    cancel.click()
    assert stopped == [1] and not hub.alive(a)
    assert _spin(app, lambda: panel._rows.count() == 1)              # live
    msgs[1].dismiss_btn.click()
    assert [m.id for m in hub.messages()] == [new]
    panel.clear_btn.click()
    assert hub.messages() == []
    hub.finish(b)
    # Now empty: it goes over to History, where what was said stays.
    assert _spin(app, lambda: panel.tab == "history" and panel.history.isVisibleTo(panel))
    assert panel.isVisible() and panel.tab_history.isChecked()
    shown = [h.text for h in hub.history()]
    assert "dopeIPTV answered your feedback" in shown and "Could not delete" in shown
    assert hub.history()[-1].level in ("error", "cancelled", "info", "done")
    host.close()


def test_the_history_keeps_what_was_said_and_the_persons_work_that_ended(app, monkeypatch):
    """Everything said since the start - notes too, and while the video
    fills the screen - and the person's work done or cancelled; in memory
    alone, at most HISTORY_MAX, nothing while private viewing is on."""
    from dopeiptv.core import privacy
    hub = _hub()
    hub.note("Copied")
    hub.post("Guide is old", level="warning")                 # a note: still kept
    hub.post("Could not save", level="error")
    hub.hold(True)                                           # full screen
    hub.note("Saved while you watched")
    hub.hold(False)
    mine = hub.start("Searching in Music…", kind="search", mine=True)
    hub._jobs[mine].shown_at = 1.0                           # it was on screen
    hub.finish(mine)
    quiet = hub.start("Loading channels…", kind="list")      # not the person's: no news
    hub._jobs[quiet].shown_at = 1.0
    hub.finish(quiet)
    stop = hub.start("Downloading Film…", kind="download", cancel=lambda: None)
    hub.request_cancel([stop])
    got = [(h.level, h.text) for h in hub.history()]
    assert got == [("cancelled", "Downloading Film…"), ("done", "Searching in Music…"),
                   ("info", "Saved while you watched"), ("error", "Could not save"),
                   ("warning", "Guide is old"), ("info", "Copied")]
    assert [h.text for h in hub.history(("error",))] == ["Could not save"]
    assert [h.level for h in hub.history(("done", "cancelled"))] == ["cancelled", "done"]
    # Private viewing: nothing kept meanwhile.
    monkeypatch.setattr(privacy, "private", lambda: True)
    hub.post("Something private failed", level="error")
    monkeypatch.setattr(privacy, "private", lambda: False)
    assert len(hub.history()) == 6
    # Bounded, the oldest first out; Copy's text and Clear.
    for i in range(hub.HISTORY_MAX + 5):
        hub.note(f"note {i}")
    assert len(hub.history()) == hub.HISTORY_MAX
    assert hub.history()[0].text == f"note {hub.HISTORY_MAX + 4}"
    text = hub.history_text(("info",), {"info": "Note"})
    assert text.splitlines()[-1].endswith(f"Note  note {hub.HISTORY_MAX + 4}")
    hub.clear_history()
    assert hub.history() == []


def test_the_history_never_shows_a_login_and_copies_scrubbed(app):
    hub = _hub()
    hub.post("Failed http://alice:s3cret@example.com/live/alice/s3cret/1.ts", level="error")
    [h] = hub.history()
    assert "s3cret" not in h.text and "alice" not in h.text
    assert "s3cret" not in hub.history_text(personal=["s3cret"])


def test_the_history_tab_lists_filters_copies_and_clears(app):
    host = _host(app)
    hub = _hub()
    panel = ActivityPanel(hub, host)
    anchor = QWidget(host)
    anchor.setGeometry(16, 540, 240, 30)
    anchor.show()
    # Nothing going on: it opens on History, which says it is empty.
    panel.open_at(anchor)
    assert panel.isVisible() and panel.tab == "history"
    assert panel.history.empty.isVisible() and not panel.history.clear_btn.isEnabled()
    assert panel.history.empty.text() == tr("notice_hist_empty")
    hub.post("Could not save", level="error")
    hub.note("Copied")
    assert _spin(app, lambda: panel.history._rows.count() == 3)    # two rows and the stretch
    rows = [panel.history._rows.itemAt(i).widget() for i in range(2)]
    assert rows[0].text == "Copied" and rows[1].text == "Could not save"
    assert rows[1].accessibleName().startswith(tr("notice_error") + ": Could not save")
    panel.history.chips["error"].click()
    assert panel.history._rows.count() == 2
    panel.history.chips["done"].click()
    assert panel.history.empty.text() == tr("notice_hist_none")
    panel.history.chips["all"].click()
    # Copy list: what the list shows, its words saying so; a row's own Copy.
    assert panel.history.copy_btn.text() == tr("notice_hist_copy_list")
    assert panel.history.copy_btn.toolTip() == tr("notice_hist_copy_tip")
    panel.history.copy_btn.click()
    got = QApplication.clipboard().text()
    assert "Could not save" in got and "Copied" in got
    assert panel.history.copy_btn.text() == tr("problem_copied")
    rows = [panel.history._rows.itemAt(i).widget() for i in range(2)]
    assert rows[1].contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
    panel.history.copy_one(rows[1].item_id)
    one = QApplication.clipboard().text()
    assert "Could not save" in one and "Copied" not in one and one.count("\n") == 0
    # Filtered to errors, Copy list copies the errors alone.
    panel.history.chips["error"].click()
    panel.history.copy_btn.click()
    assert "Copied" not in QApplication.clipboard().text()
    panel.history.chips["all"].click()
    panel.history.clear_btn.click()
    assert hub.history() == [] and panel.history.empty.isVisible()
    # Work going on: it opens on Now; the tab goes back and forth.
    panel.close_panel()
    hub.start("Searching…", kind="search", mine=True)
    hub._jobs[max(hub._jobs)].shown_at = 1.0
    panel.open_at(anchor)
    assert panel.tab == "now" and not panel.history.isVisibleTo(panel)
    panel.tab_history.click()
    assert panel.tab == "history" and panel.width() == ActivityPanel.W_HISTORY
    panel.tab_now.click()
    assert panel.tab == "now" and panel.width() == ActivityPanel.W
    host.close()


def test_a_long_message_fits_the_panel_whole(app):
    """A message of five lines and a count was cut off at the top and the
    foot: the panel was laid out for a wider line than its own width."""
    host = _host(app)
    hub = _hub()
    line = ActivityLine(hub, host)
    line.setGeometry(16, 540, 240, ActivityLine.H)
    panel = ActivityPanel(hub, host)
    words = ("Error: The Apple TV has not reached this Mac. If macOS asked whether "
             "dopeIPTV may accept incoming connections, allow it (System Settings -> "
             "Network -> Firewall -> Options), and check that the Apple TV is on the "
             "same network.")
    hub.post(words, level="error")
    hub.post(words, level="error")                                   # counted: x2
    panel.open_at(line)
    app.processEvents()
    from PyQt6.QtWidgets import QLabel
    [row] = [panel._msgs.itemAt(i).widget() for i in range(panel._msgs.count())]
    label = row.findChildren(QLabel)[0]
    assert label.text().endswith("×2")
    assert panel.width() == ActivityPanel.W
    need = label.heightForWidth(label.width())
    assert label.height() >= need, (label.height(), need)
    assert row.mapTo(panel, row.rect().bottomLeft()).y() <= panel.height()
    hub.post("Another one", level="error")                           # added while open
    app.processEvents()
    rows = [panel._msgs.itemAt(i).widget() for i in range(panel._msgs.count())]
    tops = [r.geometry().top() for r in rows]
    assert len(rows) == 2 and tops[1] >= rows[0].geometry().bottom(), tops   # never over each other
    assert all(r.findChildren(QLabel)[0].height() >= 14 for r in rows)


def test_an_address_in_what_is_said_shows_its_host_alone_and_no_login(app):
    """An error from the provider quoted the address it failed on - the
    login in its query and all - in the panel, and kept it in what was said."""
    hub = _hub()
    url = ("http://tv.example:2095/player_api.php?username=kalle77&password=secret123"
           "&action=get_series")
    hub.post("Error: 502 Server Error: Bad Gateway for url: " + url, level="error")
    hub.note("Could not reach " + url)
    token = hub.start("Loading " + url)
    hub.update(token, "Still loading " + url)
    words = " | ".join([*hub.texts(), *hub.said(), hub.note_text(),
                        *[e.text for e in hub.entries()]])
    assert "http://tv.example:2095" in words
    for leak in ("kalle77", "secret123", "player_api", "get_series"):
        assert leak not in words, (leak, words)


def test_a_long_run_breaks_and_its_row_fits_the_panel(app):
    """A run Qt could not break - an address, a path - was as wide as
    itself: the row got the height of fewer lines and was cut off."""
    host = _host(app)
    hub = _hub()
    line = ActivityLine(hub, host)
    line.setGeometry(16, 540, 240, ActivityLine.H)
    panel = ActivityPanel(hub, host)
    hub.post("Could not save to /home/someone/Videos/dopeIPTV/Recordings/"
             "SVT1_HD_2026-11-22_19-30_Rapport_with_a_very_long_name_indeed.ts", level="error")
    panel.open_at(line)
    app.processEvents()
    from PyQt6.QtWidgets import QLabel
    [row] = [panel._msgs.itemAt(i).widget() for i in range(panel._msgs.count())]
    label = row.findChildren(QLabel)[0]
    assert "\u200b" in label.text()
    assert label.minimumSizeHint().width() <= label.width()
    assert label.height() >= label.heightForWidth(label.width())
    assert row.mapTo(panel, row.rect().bottomLeft()).y() <= panel.height()
    assert breakable("short words stay as they are") == "short words stay as they are"
    panel.close_panel()
    host.close()


def test_the_decision_card_one_action_two_lines_the_rest_counted(app):
    host = _host(app)
    lay = QVBoxLayout(host)
    lay.addStretch(1)
    hub = _hub()
    card = DecisionCard(hub, host)
    card.setFixedWidth(230)
    lay.addWidget(card)
    hub.changed.connect(card.refresh)
    assert not card.refresh() and card.isHidden()
    long = ("Port 3673 is used by another program - the server cannot start there; "
            "it can start on the next free port instead")
    took = []
    hub.post(long, action=lambda: took.append(1), action_label="Use port 3674", decide=True)
    hub.post("Another question", action=lambda: None, decide=True)
    _spin(app, secs=0.05)
    assert card.isVisible() and card.text() == long and card.toolTip() == long
    lines = card.label.text().split("\n")
    assert len(lines) == 2 and lines[1].endswith("…")
    fm = QFontMetrics(card.label.font())
    assert all(fm.horizontalAdvance(x) <= card.label.width() + 1 for x in lines), lines
    assert card.action_btn.text() == "Use port 3674" and card.more.text() == "+1"
    assert card.close_btn.accessibleName() == tr("notice_dismiss")
    assert card.accessibleName() == long and card.accessibleDescription() == tr("notice_info")
    card.action_btn.click()
    assert _spin(app, lambda: took == [1])
    assert card.text() == "Another question" and card.more.isHidden()
    QTest.keyClick(card, Qt.Key.Key_Escape)
    assert hub.decisions() == [] and card.isHidden()
    host.close()


def test_a_click_on_the_decision_cards_words_shows_them_whole(app):
    host = _host(app)
    lay = QVBoxLayout(host)
    lay.addStretch(1)
    hub = _hub()
    card = DecisionCard(hub, host)
    card.setFixedWidth(230)
    lay.addWidget(card)
    hub.changed.connect(card.refresh)
    body = ("Server mode lets every device on this computer's networks use the tuner "
            "and watch without its token - what it did before 1.6.9.")
    hub.post(body, title="The tuner is open to every device", action=lambda: None,
             action_label="Close it", decide=True)
    _spin(app, secs=0.05)
    assert card.text() == "The tuner is open to every device - " + body
    folded = card.label.height()
    assert card.cut() and card.label.text().count("\n") == 1
    QTest.mouseClick(card.label, Qt.MouseButton.LeftButton)
    _spin(app, secs=0.05)
    assert card.label.text() == card.text() and card.label.wordWrap()
    assert card.label.height() > folded
    QTest.mouseClick(card.label, Qt.MouseButton.LeftButton)
    _spin(app, secs=0.05)
    assert card.label.text().endswith("…") and card.label.height() == folded
    hub.post("Short", action=lambda: None, decide=True, key="short")
    hub.dismiss(hub.decisions()[0].id)
    _spin(app, secs=0.05)
    QTest.mouseClick(card.label, Qt.MouseButton.LeftButton)     # nothing to show more of
    assert card.label.text() == "Short" and not card.label.wordWrap()
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
    assert w.notices.note_text() == "Saved"
    w._flash_status("Guide cache cleared")
    assert w.notices.note_text() == "Guide cache cleared" and w.notices.messages() == []
    act = []
    w._show_action_toast("Click here to read it", 9000, lambda: act.append(1))
    w._show_action_toast("Port 3673 is taken", 15000, lambda: act.append(2),
                         label="Use port 3674", decide=True)
    assert w.notices.texts() == ["Click here to read it", "Port 3673 is taken"]
    [ask] = w.notices.decisions()
    assert ask.action_label == "Use port 3674" and w.notices.unread() == 1
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
    w._stream_failed_quietly()                               # said on the video alone
    assert w.notices.jobs() == [] and w.notices.messages() == []
    w._set_status(tr("status_error", msg="404"), error=True)  # an error: a message
    assert w.notices.messages()[0].level == "error" and w.notices.unread() == 1
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
s.setValue("check_updates", "false")
store = PlaylistStore(s)
pl = store.add({"name": "Pirat", "server": "http://pirat.invalid", "username": "u", "password": "p"})
store.set_active(pl["id"])
theme.apply_theme(s)
app.setStyleSheet(theme.build_style())


class Client(DemoClient):
    # A long list of categories with long names.
    def live_categories(self):
        names = ["Sweden | SVT, TV4 and the regional channels", "News", "Sport",
                 "Documentaries and nature from around the world", "Kids", "Music"]
        return [{"category_id": str(i), "category_name": f"{names[i % len(names)]} {i}"}
                for i in range(64)]


w = mw.MainWindow(Client(), s, store)
w.resize(1180, 700)
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
hub, line, card, bell = w.notices, w._activity_line, w._decision_card, w._notice_bell
hub.SHOW_AFTER_MS = hub.MIN_SHOWN_MS = 0
for j in hub.jobs():
    hub.cancel(j.token)
pump(5)
cats, area = w.cat_list, w._side_scroll
assert cats.count() >= 64, cats.count()
assert not hasattr(w, "loading_bar") and not hasattr(w, "_toast")
assert not hasattr(w, "_notice_cards")
side, avatar, gear = rect(w._side), rect(w._playlist_btn), rect(w._settings_btn)
win = w.rect()
# The foot is a fixed area under the part that scrolls: the avatar on screen.
assert w._side_foot.parentWidget() is w._side and area.widget().isAncestorOf(cats)
assert not area.widget().isAncestorOf(line) and not area.widget().isAncestorOf(w._playlist_btn)
assert win.contains(avatar) and side.contains(avatar), (avatar, side)
# Nothing happens: no line, only the bell beside the source and Settings.
assert line.isHidden() and bell.isVisible() and bell.badge() == 0
assert rect(bell).top() == avatar.top() and side.contains(rect(bell)), (rect(bell), avatar)
assert bell.accessibleName() == bell.name() and bell.toolTip() == bell.name()

# The activity: in the foot, just above the source avatar.
w._show_busy(tr("status_loading_channels"))
pump(5)
assert line.isVisible() and line.text() == tr("status_loading_channels")
assert side.contains(rect(line)) and rect(line).bottom() < avatar.top(), (rect(line), avatar)
w._apply_filter()
pump(10)
assert not hub.jobs(), [j.text for j in hub.jobs()]
# Passing words: the line's, no message, nothing anywhere else.
w._flash_status(tr("epg_cache_cleared"))
pump(3)
assert line.text() == tr("epg_cache_cleared") and hub.messages() == []
hub._note_over()
pump(3)
assert line.isHidden()
# An error: only a count on the bell, no line; the status line keeps the list's words.
line_words = w.count_lbl.text()
w._error("boom")
w._show_action_toast(tr("fbr_new"), 9000, lambda: None)
pump(3)
assert line.isHidden() and line.badge() == 0 and bell.badge() == 2
assert tr("notice_unread", n=2) in bell.toolTip() and tr("notice_unread", n=2) in bell.accessibleName()
# The bell opens the panel on the messages, lined up with the foot; again closes it.
bell.click()
pump(10)
panel = w._notice_panel
assert panel.isVisible() and panel.tab == "now" and rect(panel).bottom() < rect(bell).top()
assert abs(rect(panel).left() - rect(w._side_foot).left()) <= 8 and win.contains(rect(panel))
assert hub.unread() == 0 and bell.badge() == 0
bell.click()
pump(5)
assert panel.isHidden()
# The ✕ in its corner closes it too, the focus back on the bell.
bell.click()
pump(5)
assert panel.isVisible() and panel.close_btn.isVisible()
assert panel.close_btn.accessibleName() == tr("common_close")
cb = rect(panel.close_btn)
assert cb.right() <= rect(panel).right() and cb.top() - rect(panel).top() < 40, (cb, rect(panel))
panel.close_btn.click()
pump(5)
assert panel.isHidden()
assert w.count_lbl.text() == line_words and card.isHidden()
# A stream error is said on the video, never as a message.
w._show_stream_error("HTTP 404")
pump(3)
assert len(hub.messages()) == 2 and not any("HTTP 404" in t for t in hub.texts())
# The panel opens upward from the line, inside the window; reading clears the count.
w._show_busy(tr("status_loading_channels"))
pump(3)
line.open_panel.emit()
pump(10)
assert panel.isVisible() and rect(panel).bottom() < rect(line).top()
assert win.contains(rect(panel)) and hub.unread() == 0 and line.badge() == 0
panel.close_panel()
w._hide_busy()
hub.clear()
pump(5)
assert line.isHidden()

# A decision: the card right above the line, in the foot; the categories
# only get shorter - none covered, the chosen one in view, every one reachable.
cats.setCurrentRow(30)
cats.scrollToItem(cats.currentItem())
pump(5)
vp_before = rect(area.viewport())
words = ("Port 3673 is used by another program - the server cannot start there; "
         "it can start on the next free port instead")
w._show_action_toast(words, 15000, lambda: None, label=tr("nerd_server_use_port", port=3674),
                     decide=True)
w._show_action_toast("Another question", 15000, lambda: None, decide=True)
hub.start(tr("notice_searching_in", name="Movies"), key="search", kind="search", mine=True)
pump(20)
c, ln, vp = rect(card), rect(line), rect(area.viewport())
assert card.isVisible() and line.isVisible() and w._decision_float.isHidden()
assert side.contains(c) and c.bottom() < ln.top() and ln.bottom() < avatar.top(), (c, ln, avatar)
assert rect(w._playlist_btn) == avatar and win.contains(avatar)        # never pushed off
assert vp.bottom() < c.top() and vp.height() < vp_before.height(), (vp, c, vp_before)
assert not rect(w._mid).intersects(c) and not rect(w._mid).intersects(ln)
assert card.toolTip() == words and card.label.text().count("\n") == 1
assert card.label.text().endswith("…") and card.more.text() == "+1"
foot = [c, ln, avatar, gear]


def visible_part(row):
    item = cats.item(row)
    r = cats.visualItemRect(item)
    r = r.intersected(cats.viewport().rect())
    if r.isEmpty():
        return QRect()
    r = QRect(cats.viewport().mapTo(w, r.topLeft()), r.size())
    return r.intersected(vp)


def check_rows():
    shown = 0
    for row in range(cats.count()):
        part = visible_part(row)
        if part.isEmpty():
            continue
        shown += 1
        for f in foot:
            assert not part.intersects(f), (row, part, f)
        hit = w.childAt(part.center())
        assert hit is cats.viewport() or cats.viewport().isAncestorOf(hit), (row, hit)
    return shown


assert check_rows() >= 1
label = rect(w._source_label).intersected(vp)
assert all(not label.intersects(f) for f in foot), (label, foot)
chosen = visible_part(30)
assert chosen.height() == cats.visualItemRect(cats.item(30)).height(), chosen   # in view
# Every category reachable: the side scrolled to its end, the list to its last.
area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
cats.scrollToBottom()
pump(5)
last = visible_part(cats.count() - 1)
assert last.height() == cats.visualItemRect(cats.item(cats.count() - 1)).height(), last
assert check_rows() >= 1
# The card goes: the room comes back.
hub.dismiss(hub.decisions()[0].id)
pump(5)
assert card.isVisible() and card.text() == "Another question" and card.more.isHidden()
hub.dismiss(hub.decisions()[0].id)
hub.end_key("search")
pump(10)
assert card.isHidden() and line.isHidden()
assert rect(area.viewport()).height() == vp_before.height(), (rect(area.viewport()), vp_before)
# The video fills the screen: nothing drawn, a decision waits.
w._show_action_toast("Port 3673 is taken", 15000, lambda: None, label="Use port 3674",
                     decide=True)
w._player_fs = True
w._notices_place()
pump(3)
assert card.isHidden() and line.isHidden() and hub.held and len(hub.decisions()) == 1
w._player_fs = False
w._notices_place()
pump(5)
assert card.isVisible() and not hub.held
# The icon rail: the line a ring with the count, the card beside the rail.
w._error("boom")
w._set_sidebar_collapsed(True)
pump(15)
side = rect(w._side)
fc = w._decision_float
assert line.isHidden() and bell.isVisible() and bell.badge() == 1     # nothing runs: no ring
assert card.isHidden() and fc.isVisible() and fc.text() == "Port 3673 is taken"
assert rect(fc).left() >= side.right() and win.contains(rect(fc)), (rect(fc), side)
assert not rect(fc).intersects(rect(w._playlist_btn)) and not rect(fc).intersects(side)
assert side.contains(rect(bell)) and rect(bell).top() > rect(w._playlist_btn).top()
hub.start(tr("notice_searching_in", name="Movies"), key="search", kind="search", mine=True)
pump(5)
assert line.isVisible() and line.sizeHint().width() == line.H and line.badge() == 0
assert side.contains(rect(line)) and rect(line).bottom() < rect(w._playlist_btn).top()
hub.end_key("search")
pump(5)
bell.click()
pump(5)
assert panel.isVisible() and rect(panel).left() >= side.right() and win.contains(rect(panel))
panel.close_panel()
hub.clear()                                  # no ring left: the card still beside
pump(5)
assert line.isHidden() and fc.isVisible() and rect(fc).left() >= side.right()
assert rect(fc).bottom() < rect(w._playlist_btn).top() and win.contains(rect(fc))
w._set_sidebar_collapsed(False)
pump(10)
assert fc.isHidden() and card.isVisible()
hub.clear()
for m in hub.decisions():
    hub.dismiss(m.id)
for j in hub.jobs():
    hub.cancel(j.token)
pump(5)
# A typed channel number with no player: the line's words, following the digits.
w._chan_entry_show("1", 2500)
w._chan_entry_show("12", 2500)
assert hub.note_text() == tr("chan_entry", num="12") and hub.messages() == []
w._chan_entry_show("", 0)
assert hub.note_text() == ""
# A page with no sidebar (one over the whole window, in the central stack):
# the line at the same corner, a decision above it.
from PyQt6.QtWidgets import QWidget as _W
over = _W()
w._center_stack.addWidget(over)
w._center_stack.setCurrentWidget(over)
job = hub.start(tr("sync_running"), kind="sync")
w._show_action_toast("Port 3673 is taken", 15000, lambda: None, decide=True)
pump(10)
fl, central = w._activity_float, rect(w._center_stack)
assert not w._side.isVisible() and fl.isVisible() and not line.isVisible()
assert central.contains(rect(fl)) and rect(fl).left() - central.left() <= 20, rect(fl)
assert fc.isVisible() and rect(fc).bottom() < rect(fl).top() and central.contains(rect(fc))
hub.finish(job)
w.switch_mode("live")
pump(10)
assert fl.isHidden() and fc.isHidden() and card.isVisible()
# Another theme: the card and the panel keep style sheets of their own, and
# take the new theme's colours with the rest of the window.
from dopeiptv.ui.notice_widgets import colours
before = (panel.styleSheet(), card.styleSheet(), fc.styleSheet())
s.setValue("theme", "light" if not theme.P.get("light") else "dark")
w._apply_theme_now()
pump(5)
c = colours()
assert c["surface"] in panel.styleSheet() and panel.styleSheet() != before[0]
assert c["line_bg"] in card.styleSheet() and card.styleSheet() != before[1]
assert c["float_bg"] in fc.styleSheet() and fc.styleSheet() != before[2]
# The welcome over the window: nothing of the notices over it or opened by
# itself; a decision and a message wait, a note goes, all back as it goes.
hub.clear()
for m in hub.decisions():
    hub.dismiss(m.id)
pump(5)
w.show_welcome()
pump(10)
ov = w._welcome
assert ov.isVisible() and hub.held
w._show_action_toast("Port 3673 is taken", 15000, lambda: None, decide=True)
w._error("boom")
hub.note("Copied")
pump(10)
assert card.isHidden() and fc.isHidden() and line.isHidden() and w._activity_float.isHidden()
assert hub.note_text() == "" and len(hub.decisions()) == 1
w._set_sidebar_collapsed(True)
pump(10)
assert fc.isHidden() and line.isHidden() and w._activity_float.isHidden()
w._set_sidebar_collapsed(False)
w._notices_open_history()
bell.click()
pump(5)
assert panel.isHidden()
ov.dismiss()
pump(15)
assert not ov.isVisible() and not hub.held
assert card.isVisible() and card.text() == "Port 3673 is taken"
assert bell.isVisible() and bell.badge() == 1
bell.click()
pump(5)
assert panel.isVisible()
panel.close_btn.click()
pump(5)
assert panel.isHidden()
print("NOTICES_WINDOW_OK")
"""


def test_the_real_window_keeps_its_notices_in_the_sidebars_foot():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "NOTICES_WINDOW_OK" in out.stdout, out.stdout[-2000:] + out.stderr[-4000:]


# -- a job's own act: the recording's line -------------------------------------

def test_a_cancel_the_person_declines_leaves_the_job_running(app):
    hub = _hub()
    answers = [False, True]
    a = hub.start("Recording News", cancel=lambda: answers.pop(0))
    assert not hub.request_cancel([a]) and hub.alive(a)       # "Keep recording"
    assert hub.request_cancel([a]) and not hub.alive(a)       # "Stop recording"


def test_a_click_on_the_line_does_the_one_jobs_act_else_opens_the_panel(app):
    host = _host(app)
    hub = _hub()
    line = ActivityLine(hub, host)
    line.setGeometry(16, 540, 240, ActivityLine.H)
    hub.changed.connect(line.refresh)
    opened, asked = [], []
    line.open_panel.connect(lambda: opened.append(1))
    a = hub.start("Recording News", kind="record", mine=True,
                  act=lambda: asked.append("news"), cancel=lambda: False,
                  cancel_text=tr("rec_stop_recording"))
    assert _spin(app, lambda: line.isVisible())
    assert hub.direct() is not None
    QTest.mouseClick(line, Qt.MouseButton.LeftButton)
    assert asked == ["news"] and opened == []                 # asked, no panel
    b = hub.start("Recording Sport", kind="record", act=lambda: asked.append("sport"))
    _spin(app, secs=0.02)
    assert hub.direct() is None                               # two: pick in the panel
    QTest.mouseClick(line, Qt.MouseButton.LeftButton)
    assert opened == [1] and asked == ["news"]
    hub.finish(b)
    hub.post("Could not delete", level="error")
    assert hub.direct() is None                               # a message waits: the panel
    hub.mark_read()
    assert hub.direct() is not None
    panel = ActivityPanel(hub, host)
    panel.open_at(line)
    [row] = [panel._rows.itemAt(i).widget() for i in range(panel._rows.count())]
    [stop] = row.findChildren(QPushButton)
    assert stop.text() == tr("rec_stop_recording")
    stop.click()
    assert hub.alive(a)                                       # declined: it records on
    host.close()
