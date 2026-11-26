"""Remind me on a word: a series rule that sets a reminder for each airing it
finds instead of recording it.

The pure part (core/series_rules.py): what a rule does (an old rule
records), a recording rule and a reminder rule on one title both acting, the
reminder at the programme's own start - never the recording's padding -
none twice, one the user removed not set again, one for a programme on two
channels at once, a rule's limit, a reminder following its programme when
the guide moves it; the reminders' store keeping the rule. Then the window,
in a child process: "Remind me" in the rule's dialog, reminders and no
recordings, nothing twice, a removed one not brought back, the rule deleted
with its reminders, and the Settings list saying which rules remind.
"""
from __future__ import annotations

import os
import subprocess
import sys

from dopeiptv.core import series_rules as sr
from dopeiptv.services.reminders import ReminderStore

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = 1_800_000_000.0
H = 3600.0


class _Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value


def _guide(table):
    def programmes(ch, lo, hi, keep):
        return [{"title": t, "start_timestamp": a, "stop_timestamp": b}
                for t, a, b in table.get(ch.get("stream_id"), [])
                if a < hi and b > lo and keep(t)]
    return programmes


CH1 = {"stream_id": 1, "name": "One"}
CH2 = {"stream_id": 2, "name": "Two"}


def _airing(rule, sid, title, start, stop):
    return sr.Airing(rule, {"stream_id": sid, "name": f"Ch{sid}"}, str(sid), title, start, stop)


def test_a_rule_records_or_reminds_and_an_old_one_records():
    rec = sr.make_rule("News", sr.WORD)
    rem = sr.make_rule("News", sr.WORD, action=sr.REMIND)
    assert rec["action"] == sr.RECORD and rem["action"] == sr.REMIND
    assert sr.action(rem) == sr.REMIND and sr.action({"id": "x", "kind": sr.WORD}) == sr.RECORD
    assert sr.make_rule("News", sr.WORD, action="email") is None
    store = sr.SeriesRuleStore(_Settings(), "p")
    a, b = store.add(rec), store.add(rem)
    assert a["id"] != b["id"] and len(store.rules()) == 2       # both kept
    assert store.add(sr.make_rule("news", sr.WORD, action=sr.REMIND))["id"] == b["id"]
    assert sr.of_action(store.rules(), sr.REMIND) == {b["id"]}
    # A stored rule from before there was a choice still reads, and records.
    s = _Settings()
    s["series_rules_p"] = '{"rules": [{"id": "old", "kind": "title", "text": "X", "scope": "any"}]}'
    assert [sr.action(r) for r in sr.SeriesRuleStore(s, "p").rules()] == [sr.RECORD]


def test_a_recording_rule_and_a_reminder_rule_both_get_the_airing():
    rec = sr.make_rule("The Show")
    rem = sr.make_rule("show", sr.WORD, action=sr.REMIND)
    rem2 = sr.make_rule("the", sr.WORD, action=sr.REMIND)
    table = {1: [("The Show", NOW + H, NOW + 2 * H)]}
    found = sr.find_airings([rec, rem, rem2], [CH1], _guide(table), NOW)
    assert sorted(a.rule for a in found) == sorted([rec["id"], rem["id"]])


def test_a_reminder_is_for_the_programmes_own_start_and_set_once():
    rid = "r1"
    a1 = _airing(rid, 1, "News", NOW + H, NOW + 2 * H)
    a2 = _airing(rid, 1, "News", NOW + 25 * H, NOW + 26 * H)
    soon = _airing(rid, 1, "News", NOW + 30, NOW + H)              # on in a moment
    got = sr.remind_plan([a2, soon, a1], [], [], NOW)
    assert got == [a1, a2]                                          # no padding: the airings themselves
    # One already set for that channel and start - by hand - is not set again.
    have = [{"stream_id": 1, "start": int(NOW + H), "title": "News"}]
    assert sr.remind_plan([a1, a2], have, [], NOW) == [a2]
    # One set before and removed by the user (it is in the rule's memory).
    taken = [{"key": "1", "start": NOW + 25 * H, "stop": NOW + 26 * H, "title": "news", "rule": rid}]
    assert sr.remind_plan([a1, a2], [], taken, NOW) == [a1]


def test_the_same_programme_on_two_channels_at_once_is_one_reminder():
    rid = "r1"
    one = _airing(rid, 1, "Big Match", NOW + H, NOW + 3 * H)
    two = _airing(rid, 2, "Big Match", NOW + H, NOW + 3 * H)
    other = _airing(rid, 2, "Big Match Highlights", NOW + H, NOW + 2 * H)
    assert sr.remind_plan([two, one, other], [], [], NOW) == [one, other]


def test_a_rule_keeps_so_many_upcoming_reminders():
    rid = "r1"
    airings = [_airing(rid, 1, "News", NOW + (i + 1) * H, NOW + (i + 1) * H + 600) for i in range(40)]
    assert len(sr.remind_plan(airings, [], [], NOW)) == sr.MAX_PER_RULE
    mine = [{"stream_id": 9, "start": int(NOW + 100 * H + i), "title": f"x{i}", "rule": rid}
            for i in range(sr.MAX_PER_RULE - 2)]
    assert len(sr.remind_plan(airings, mine, [], NOW)) == 2


def test_a_reminder_follows_its_programme_when_the_guide_moves_it():
    rid = "r1"
    r = {"stream_id": 1, "start": int(NOW + H), "stop": int(NOW + 2 * H), "title": "News", "rule": rid}
    moved = _airing(rid, 1, "News", NOW + H + 600, NOW + 2 * H + 600)
    elsewhere = _airing(rid, 1, "News", NOW + 10 * H, NOW + 11 * H)
    assert sr.remind_retimed([elsewhere, moved], [r], NOW) == [(r, moved)]
    still = _airing(rid, 1, "News", NOW + H, NOW + 2 * H)
    assert sr.remind_retimed([still], [r], NOW) == []
    by_hand = dict(r, rule=None)
    assert sr.remind_retimed([moved], [by_hand], NOW) == []


def test_what_one_kind_of_rule_set_never_stops_the_other():
    rec, rem = "rec", "rem"
    taken = [{"key": "1", "start": 1.0, "rule": rem}, {"key": "1", "start": 2.0, "rule": rec},
             {"key": "1", "start": 3.0}]
    assert [t["start"] for t in sr.taken_for(taken, {rec}, legacy=True)] == [2.0, 3.0]
    assert [t["start"] for t in sr.taken_for(taken, {rem})] == [1.0]
    # The recording planner, given only the recording rules' memory,
    # records what a reminder rule reminded of.
    a = _airing(rec, 1, "News", NOW + H, NOW + 2 * H)
    remembered = [{"key": "1", "start": NOW + H, "stop": NOW + 2 * H, "title": "news", "rule": rem}]
    assert sr.plan([a], [], sr.taken_for(remembered, {rec}, legacy=True), NOW)
    assert not sr.plan([a], [], remembered, NOW)


def test_the_reminders_keep_the_rule_and_go_with_it():
    s = _Settings()
    r = ReminderStore(s, "p")
    r.add(CH1, "News", NOW + H, rule="r1", stop=NOW + 2 * H)
    r.add(CH1, "News", NOW - H, rule="r1", stop=NOW)            # due already
    r.add(CH2, "Film", NOW + H)                                 # by hand
    back = ReminderStore(s, "p").all()
    assert {(x["title"], x.get("rule"), x.get("stop")) for x in back} == {
        ("News", "r1", int(NOW + 2 * H)), ("News", "r1", int(NOW)), ("Film", None, None)}
    assert r.move(1, NOW + H, NOW + H + 600, NOW + 2 * H + 600)
    assert ReminderStore(s, "p").has(1, NOW + H + 600) and not r.has(1, NOW + H)
    assert not r.move(1, 123, 456)
    assert r.remove_rule("r1", NOW) == 1
    assert {x["title"] for x in r.all()} == {"News", "Film"}    # the past one, and the hand's


_WINDOW = r"""
import os, sys, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from array import array
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (QApplication, QComboBox, QDialog, QLineEdit, QListWidget,
                             QPushButton, QRadioButton)
app = QApplication.instance() or QApplication([])
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
s.setValue("recordings_dir", tempfile.mkdtemp())
from dopeiptv.core import series_rules as sr
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.providers.epg import XmltvGuide, normalize_name
from dopeiptv.ui.main_window import MainWindow

w = MainWindow(DemoClient(), s, PlaylistStore(s))
w.rec.recorder = lambda: ("ffmpeg", "/bin/true")
started = []
w.rec._spawn = lambda j: started.append(j)
s.setValue(sr.PAD_BEFORE_KEY, 5)
s.setValue(sr.PAD_AFTER_KEY, 15)

def wait(cond, secs=15):
    end = time.monotonic() + secs
    while not cond() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    assert cond()

def idle():
    wait(lambda: not getattr(w, "_series_busy", False))
    app.processEvents()

H = 3600
base = int(time.time() // 60 * 60) + 2 * H
def sched(*progs):
    progs = sorted(progs, key=lambda p: p[1])
    return (array("q", [p[1] for p in progs]), array("q", [p[2] for p in progs]),
            tuple(p[0] for p in progs), tuple("" for _p in progs))
g = XmltvGuide(w.client)
g._by_id = {
    "bbb": sched(("Evening News", base, base + 1800), ("Film", base + 1800, base + 7200),
                 ("Evening News", base + 86400, base + 86400 + 1800)),
    "bip": sched(("Evening News", base, base + 1800), ("Late News", base + 3 * H, base + 3 * H + 1200)),
}
g._by_name = {normalize_name("Unified Live"): "bbb", normalize_name("Unified Live 2"): "bip"}
g._loaded = True
w.xmltv = g
bbb = w.client.live_streams(None)[0]

seen_titles = []
def answer(text=None, kind=1, where=0, remind=None):
    def run(self):
        seen_titles.append(self.windowTitle())
        if text is not None:
            self.findChild(QLineEdit).setText(text)
        combos = self.findChildren(QComboBox)
        combos[0].setCurrentIndex(kind)
        combos[1].setCurrentIndex(where)
        if remind is not None:
            [b for b in self.findChildren(QRadioButton)
             if b.text() == tr("series_do_remind" if remind else "series_do_record")][0].setChecked(True)
        return 1
    QDialog.exec = run

def rule_reminders():
    return sorted((r["start"], r["ch"]["name"], r["title"]) for r in w.reminders.all() if r.get("rule"))

# 1. From a programme, opened as "Remind me of every airing": a keyword on any
# channel. Every airing gets a reminder at its own start - not padded - and
# the simulcast at the same moment on the other channel only once.
answer(text="news", kind=1, where=1)
rule = w._record_series(bbb, "Evening News", remind=True)
assert seen_titles[-1] == tr("series_remind_title"), seen_titles
assert rule and sr.action(rule) == sr.REMIND and rule["scope"] == sr.ANY, rule
wait(lambda: len(rule_reminders()) == 3)
idle()
assert rule_reminders() == [
    (base, "Unified Live", "Evening News"),
    (base + 3 * H, "Unified Live 2", "Late News"),
    (base + 86400, "Unified Live", "Evening News")], rule_reminders()
assert not [j for j in w.rec.jobs if j.get("rule")], w.rec.jobs     # nothing recorded

# 2. Looked through again: nothing twice.
w._check_series_rules()
idle()
assert len(rule_reminders()) == 3

# 3. One removed by the user (the reminders window): not brought back.
w.reminders.remove(bbb["stream_id"], base + 86400)
w._check_series_rules()
idle()
assert len(rule_reminders()) == 2

# 4. A recording rule on the same title still records it, padded.
answer(text="Evening News", kind=0, where=0, remind=False)
rec_rule = w._record_series(bbb, "Evening News")
assert sr.action(rec_rule) == sr.RECORD
wait(lambda: len([j for j in w.rec.jobs if j.get("rule")]) == 2)
idle()
jobs = sorted((j["start"], j["stop"]) for j in w.rec.jobs if j.get("rule"))
assert jobs[0] == (base - 300, base + 1800 + 900), jobs
assert len(rule_reminders()) == 2

# 5. Settings > Recording lists the reminder rule as one; deleting it takes
# its reminders still to come along, and leaves the recordings be.
def settings(self):
    from dopeiptv.ui.settings_ui import SettingsShell
    self.findChild(SettingsShell).show_page("recording")   # built as it shows
    lst = self.findChild(QListWidget, "SeriesRuleList")
    rows = [lst.item(i).text() for i in range(lst.count())]
    assert tr("series_row_remind", rule=tr("series_row_word_any", word="news")) in rows, rows
    assert tr("series_row_title_on", title="Evening News", channel="Unified Live") in rows, rows
    lst.setCurrentRow(rows.index(tr("series_row_remind", rule=tr("series_row_word_any", word="news"))))
    [b for b in self.findChildren(QPushButton) if b.text() == tr("ctx_delete")][0].click()
    return 1
QDialog.exec = settings
w.open_settings()
assert rule_reminders() == [], rule_reminders()
assert len([j for j in w.rec.jobs if j.get("rule") and j["status"] == "scheduled"]) == 2
assert not started
print("REMIND_OK", flush=True)
"""


def test_the_window_sets_reminders_from_a_rule():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for _attempt in (1, 2):
        out = subprocess.run([sys.executable, "-c", _WINDOW], cwd=_REPO_ROOT, env=env,
                             capture_output=True, text=True, timeout=180)
        if "REMIND_OK" in out.stdout:
            return
        if "AssertionError" in out.stderr:
            break
    raise AssertionError(out.stdout + out.stderr[-4000:])


def test_the_guides_menu_offers_remind_me_too():
    from types import SimpleNamespace
    from unittest import mock

    from dopeiptv.ui.epg_grid import EpgGrid

    win = SimpleNamespace(_record_series=mock.Mock(return_value={"text": "News"}))
    grid = SimpleNamespace(window=win, info=mock.Mock())
    ch = {"stream_id": 5, "name": "One"}
    EpgGrid._record_series(grid, ch, {"title": "News"}, remind=True)
    win._record_series.assert_called_once_with(ch, "News", parent=grid, remind=True)
    grid.info.setText.assert_called_once_with("🔔 News")
