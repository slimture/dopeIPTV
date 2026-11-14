"""Answers to feedback, where they can be seen: a window of their own from
the app menu (and Settings), new ones counted on its entry and marked by a
dot, a note that opens it, asked soon after a report and then less often -
and the app menu laid out as each platform has it."""
from __future__ import annotations

import importlib
import os
import subprocess
import sys

import pytest

from dopeiptv.core import report

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# -- when answers are asked ----------------------------------------------------------

def test_asked_two_minutes_after_a_report_then_every_quarter_hour_for_a_day():
    sent = 1_000_000.0
    items = [{"c": "a", "t": sent}]
    assert report.next_ask(items, sent) == report.ASK_FIRST == 120
    assert report.next_ask(items, sent + 60) == 60
    # Asked before the report went (the window was open, say): still two minutes.
    assert report.next_ask(items, sent + 1, last=sent - 10) == 119
    # Then every fifteen minutes while it waits, for the first day.
    assert report.next_ask(items, sent + 120, last=sent + 120) == report.ASK_SOON == 900
    assert report.next_ask(items, sent + 5 * 3600, last=sent + 5 * 3600 - 100) == 800
    # A day on, or answered: every six hours.
    later = sent + 25 * 3600
    assert report.next_ask(items, later, last=later) == report.ASK_LATER == 6 * 3600
    answered = [{"c": "a", "t": sent, "r": [{"code": "thanks"}]}]
    assert report.next_ask(answered, sent + 600, last=sent + 600) == 6 * 3600
    # Never asked yet this run, old report: now.
    assert report.next_ask(items, later, last=0) == 0


def test_nothing_is_asked_without_a_report_to_ask_about():
    now = 5_000_000.0
    assert report.next_ask([], now) is None
    old = [{"c": "a", "t": now - (report.ASK_DAYS + 1) * 86400, "r": [{"code": "thanks"}]}]
    assert report.next_ask(old, now) is None
    assert report.open_codes(old, now) == []
    assert report.open_codes([{"c": "b", "t": now - 3600}, *old], now) == ["b"]


def test_new_answers_counted_merged_and_marked_seen():
    items = [{"c": "a", "t": 1, "seen": 1, "r": [{"code": "thanks"}]},
             {"c": "b", "t": 2, "seen": 0, "r": [{"code": "known"}]},
             {"c": "c", "t": 3}]
    assert report.unread(items) == 1
    fresh = report.merge_replies(items, {"a": [{"code": "thanks"}, {"code": "fixed"}],
                                         "c": [{"code": "note", "text": "hi"}, "junk"],
                                         "zzz": [{"code": "thanks"}]})
    assert fresh == 2 and len(items[0]["r"]) == 2 and items[2]["r"] == [{"code": "note", "text": "hi"}]
    assert report.unread(items) == 3
    assert report.mark_seen(items) and report.unread(items) == 0
    assert not report.mark_seen(items)
    # The same answers again are nothing new.
    assert report.merge_replies(items, {"a": items[0]["r"]}) == 0


# -- the note that opens it ------------------------------------------------------------

def test_a_note_with_an_action_does_it_when_clicked_and_one_without_does_nothing():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QWidget

    from dopeiptv.ui.widgets import _Toast
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    host.resize(600, 400)
    host.show()
    toast = _Toast(host)
    clicked = []
    toast.show_message("Answered - click to read", 5000, on_click=lambda: clicked.append(1))
    assert toast.cursor().shape() == Qt.CursorShape.PointingHandCursor
    QTest.mouseClick(toast, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert clicked == [1] and toast.isHidden()
    toast.show_message("Just a note", 5000)
    assert toast.cursor().shape() != Qt.CursorShape.PointingHandCursor
    QTest.mouseClick(toast, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert clicked == [1] and toast.isVisible()
    host.close()


# -- the menus, per platform -------------------------------------------------------------

def _menu_actions():
    from PyQt6.QtGui import QAction
    from PyQt6.QtWidgets import QMenuBar
    bar = QMenuBar()
    names = ("about", "settings", "open_video", "refresh", "reminders", "multiview", "cast", "private",
             "clear", "feedback", "answers", "website", "supporter", "donate", "quit")
    acts = {n: QAction(n, bar) for n in names}
    return bar, acts


def test_on_a_mac_a_real_menu_bar_and_no_second_app_menu():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtGui import QAction, QKeySequence
    from PyQt6.QtWidgets import QApplication

    from dopeiptv import APP_NAME
    from dopeiptv.i18n import tr
    from dopeiptv.ui import app_menu
    app = QApplication.instance() or QApplication([])  # noqa: F841
    bar, acts = _menu_actions()
    menus = app_menu.lay_out(bar, acts, mac=True)
    assert [a.text() for a in bar.actions()] == [APP_NAME, tr("menu_file"), tr("menu_view"),
                                                tr("nav_history"), tr("menu_help")]

    def names(menu):
        return [a.text() for a in menu.actions() if not a.isSeparator()]
    assert names(menus["app"]) == ["about", "settings", "supporter", "donate", "quit"]
    assert names(menus["file"]) == ["open_video", "refresh", "reminders"]
    assert names(menus["view"]) == ["multiview", "cast"]
    assert names(menus["history"]) == ["private", "clear"]
    assert names(menus["help"]) == ["feedback", "answers", "website"]
    role = QAction.MenuRole
    assert acts["about"].menuRole() == role.AboutRole
    assert acts["settings"].menuRole() == role.PreferencesRole
    assert acts["quit"].menuRole() == role.QuitRole
    assert acts["supporter"].menuRole() == acts["donate"].menuRole() == role.ApplicationSpecificRole
    # Everything in the app menu goes into the real one: no second "dopeIPTV".
    assert all(a.menuRole() not in (role.NoRole, role.TextHeuristicRole) for a in menus["app"].actions())
    # Nothing elsewhere is moved there by its words.
    for key in ("file", "view", "history", "help"):
        assert all(a.menuRole() == role.NoRole for a in menus[key].actions())
    assert acts["settings"].shortcut() == QKeySequence(QKeySequence.StandardKey.Preferences)


def test_on_linux_and_windows_one_menu_in_groups():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication

    from dopeiptv.ui import app_menu
    app = QApplication.instance() or QApplication([])  # noqa: F841
    bar, acts = _menu_actions()
    menus = app_menu.lay_out(bar, acts, mac=False)
    assert len(bar.actions()) == 1 and all(m is menus["app"] for m in menus.values())
    seq = ["|" if a.isSeparator() else a.text() for a in menus["app"].actions()]
    assert seq == ["about", "|", "settings", "|", "open_video", "refresh", "reminders", "|",
                   "multiview", "cast", "|", "private", "clear", "|", "feedback", "answers", "website",
                   "|", "supporter", "donate", "|", "quit"]
    # An action that is not there leaves no empty group behind.
    bar2, acts2 = _menu_actions()
    del acts2["multiview"], acts2["cast"]
    seq2 = ["|" if a.isSeparator() else a.text() for a in app_menu.lay_out(bar2, acts2, mac=False)["app"].actions()]
    assert "| |" not in " ".join(seq2) and seq2[0] != "|" and seq2.count("|") == 6, seq2


def test_a_macs_clear_history_shortcut_is_its_delete_key(monkeypatch):
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.ui import mw_privacy
    try:
        monkeypatch.setattr(sys, "platform", "darwin")
        assert importlib.reload(mw_privacy).CLEAR_SHORTCUT == "Ctrl+Shift+Backspace"
        monkeypatch.setattr(sys, "platform", "linux")
        assert importlib.reload(mw_privacy).CLEAR_SHORTCUT == "Ctrl+Shift+Delete"
    finally:
        monkeypatch.undo()
        importlib.reload(mw_privacy)


# -- the window, in the app ----------------------------------------------------------------

_WINDOW = r"""
import os, json, sys, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QPushButton
from dopeiptv.core import report
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import dialogs
from dopeiptv.ui.main_window import MainWindow
from tests.test_report import Inbox

app = QApplication.instance() or QApplication([])
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
inbox = Inbox()
report.REPLIES = inbox.url.replace("/report", "/replies")
report.ENDPOINT = inbox.url
now = time.time()

def wait(cond, rounds=200):
    for _ in range(rounds):
        app.processEvents(); w.pool.waitForDone(5)
        if cond():
            return True
    return False

# No report asked about: nothing scheduled, nothing asked, no count, the entry is there.
w = MainWindow(DemoClient(), s, PlaylistStore(s))
w.resize(1000, 650); w.show(); app.processEvents()
assert not w._answers_timer.isActive()
menu = w._answers_menu
texts = [a.text() for a in menu.actions()]
assert tr("fba_menu") + "…" in texts and tr("feedback_menu") + "…" in texts and tr("menu_website") in texts, texts
# Empty: Send feedback… closes it and opens the feedback window.
opened = []
w.open_report = lambda kind, *a, **k: opened.append(kind)
dlg = w.open_answers()
assert dlg.cards == [] and dlg.send_btn is not None and not dlg.check_btn.isVisibleTo(dlg)
dlg.send_btn.click()
assert wait(lambda: opened == ["feedback"]) and w._answers_window() is None
assert inbox.asked == []

# Reports: one answered and read, one with a new answer, one waiting.
report.save_tickets(s, [
    {"c": "old", "t": int(now - 9 * 86400), "w": "Darker guide please", "seen": 1,
     "r": [{"t": int(now - 8 * 86400), "code": "thanks"}]},
    {"c": "mid", "t": int(now - 3 * 86400), "w": "Guide empty", "seen": 0,
     "r": [{"t": int(now - 3600), "code": "fixed", "version": "1.5.1"}]},
    {"c": "new", "t": int(now - 600), "w": "Channel numbers in the grid?", "seen": 0}])
w._show_unread()
assert w._answers_action.text() == tr("fba_menu") + " (1)…", w._answers_action.text()
assert w._answers_dot is not None and not w._answers_dot.isHidden()
w.retranslate_ui()
assert w._answers_action.text().endswith("(1)…")
# The timer: the waiting report was sent ten minutes ago, never asked this run.
w._answers_last = 0.0
w._schedule_answers()
assert w._answers_timer.isActive() and w._answers_timer.interval() <= 1500, w._answers_timer.interval()

# Opening it: the cards newest first, the new answer marked, the waiting one said; all read now.
w._answers_last = time.time()          # asked just now: opening does not ask again
dlg = w.open_answers()
assert [c.new for c in dlg.cards] == [0, 1, 0], [c.new for c in dlg.cards]
def labels(card):
    from PyQt6.QtWidgets import QLabel
    return [l.text() for l in card.findChildren(QLabel)]
assert tr("fba_waiting") in labels(dlg.cards[0])
assert any("1.5.1" in t for t in labels(dlg.cards[1])) and tr("fba_new") in labels(dlg.cards[1])
assert any(tr("fbr_thanks") in t for t in labels(dlg.cards[2]))
assert report.unread(report.tickets(s)) == 0 and w._answers_action.text() == tr("fba_menu") + "…"
assert w._answers_dot.isHidden()
assert inbox.asked == [] and tr("fba_checked_none", time="")[:5] in dlg.status.text(), dlg.status.text()

# Check now, against the fake site: one request though asked twice, the answer shown, still read.
inbox.answers[report.code_hash("new")] = [{"t": int(now), "code": "note", "text": "Coming in 1.6"}]
w._answers_last = 0.0
dlg.check_btn.click()
w.check_answers_now()                  # while the first is out: not a second one
assert w._answers_busy and not dlg.check_btn.isEnabled()
assert wait(lambda: not w._answers_busy)
assert len(inbox.asked) == 1 and set(inbox.asked[0]) == {report.code_hash(c) for c in ("old", "mid", "new")}
assert any("Coming in 1.6" in t for t in labels(dlg.cards[0])) and dlg.cards[0].new == 1
assert dlg.check_btn.isEnabled() and dlg.status.text(), dlg.status.text()
assert report.unread(report.tickets(s)) == 0
# Answered now: every six hours from here.
left = w._answers_timer.interval()
assert 6 * 3600 * 1000 - 5000 < left <= 6 * 3600 * 1000, left
# Again at once: the last word stands, nothing sent.
dlg.check_btn.click()
assert len(inbox.asked) == 1
dlg.close(); app.processEvents()

# A new answer while the window is closed: the note says so and opens it.
toasts = []
real_toast = w._show_action_toast
def spy(text, ms, act):
    toasts.append(text)
    real_toast(text, ms, act)
w._show_action_toast = spy
inbox.answers[report.code_hash("mid")] = [{"t": int(now - 3600), "code": "fixed", "version": "1.5.1"},
                                          {"t": int(now), "code": "note", "text": "Out now"}]
w._answers_last = 0.0
w._ask_answers()
assert wait(lambda: toasts), toasts
assert "answered" in toasts[-1] and w._toast.isVisible()
assert w._answers_action.text() == tr("fba_menu") + " (1)…"
QTest.mouseClick(w._toast, Qt.MouseButton.LeftButton)
assert wait(lambda: w._answers_window() is not None)
assert report.unread(report.tickets(s)) == 0 and w._toast.isHidden()
w._answers_window().close(); app.processEvents()

# After a report with a code goes (the real Send): asked two minutes on, then
# every quarter of an hour.
del w.open_report
def fake_feedback(self):
    # The answers shown here link to their window, which opens over this one.
    self.all_btn.click()
    win = w._answers_window()
    assert win is not None and win.parent() is self
    win.close()
    self.topic_chips["idea"].setChecked(True)
    self.message.setPlainText("Channel numbers in the grid, please")
    return 1
dialogs.FeedbackDialog.exec = fake_feedback
w._answers_timer.stop()
w._answers_last = 0.0
w.open_report("feedback")
assert wait(lambda: len(report.tickets(s)) == 4), report.tickets(s)
assert inbox.got and inbox.got[-1][2]["reply"] == report.code_hash(report.tickets(s)[-1]["c"])
left = w._answers_timer.interval()
assert w._answers_timer.isActive() and 115_000 < left <= 120_000, left
items = report.tickets(s)
items[-1]["t"] -= 120                        # two minutes on, and asked then
report.save_tickets(s, items)
w._answers_last = time.time()
w._schedule_answers()
left = w._answers_timer.interval()
assert 895_000 < left <= 900_000, left

# Settings: Feedback with both buttons; Answers opens over Settings itself.
seen = {}
def in_settings(self):
    for b in self.findChildren(QPushButton):
        if b.text() == tr("feedback_menu") + "…":
            b.click(); seen["feedback"] = True
        elif b.text().startswith(tr("fba_menu")):
            b.click()
            win = w._answers_window()
            seen["answers"] = win is not None and win.parent() is self
            win.close()
    return 0
QDialog.exec = in_settings
opened.clear()
w.open_report = lambda kind, *a, **k: opened.append(kind)
w.open_settings()
assert seen == {"feedback": True, "answers": True} and opened == ["feedback"], (seen, opened)
inbox.close()
print("ANSWERS_WINDOW_OK")
"""


def test_the_answers_window_menu_count_note_and_schedule():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    out = subprocess.run([sys.executable, "-c", _WINDOW], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "ANSWERS_WINDOW_OK" in out.stdout, out.stdout + out.stderr[-4000:]
