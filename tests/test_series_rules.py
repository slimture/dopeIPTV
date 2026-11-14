"""Series recordings and recording padding.

The pure part (core/series_rules.py): what a rule matches, the padding, and
what the planner leaves out - an airing recorded already on its channel, one
a rule scheduled before (a cancelled recording stays cancelled), the same
programme at the same time on a second channel, past a rule's limit - and
back-to-back recordings on one channel that do not share their padding.

Then the window, in a child process (its embedded player owns a GL widget):
"Record series…" from a programme, the rules looked through a guide made
here, jobs scheduled with the recorder stubbed, nothing twice, a cancelled
one not brought back, the padding rows and the rule list in Settings.
"""
from __future__ import annotations

import os
import subprocess
import sys
from array import array

from dopeiptv.core import series_rules as sr

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = 1_800_000_000.0
H = 3600.0


class _Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value


def _guide(table):
    """programmes(channel, lo, hi, keep) over {stream_id: [(title, start, stop)]}."""
    def programmes(ch, lo, hi, keep):
        out = []
        for title, start, stop in table.get(ch.get("stream_id"), []):
            if start < hi and stop > lo and keep(title):
                out.append({"title": title, "start_timestamp": start,
                            "stop_timestamp": stop})
        return out
    return programmes


def _airing(rule, sid, title, start, stop, url=None):
    return sr.Airing(rule, {"stream_id": sid, "name": f"Ch{sid}"}, str(sid),
                     title, start, stop, url or f"http://x/{sid}.ts")


CH1 = {"stream_id": 1, "name": "One", "epg_channel_id": "one.se"}
CH2 = {"stream_id": 2, "name": "Two"}


def test_titles_match_as_the_rule_says():
    exact = sr.make_rule("  The   Show ", sr.EXACT, CH1)
    word = sr.make_rule("news", sr.WORD)
    assert exact["text"] == "The Show" and exact["scope"] == sr.THIS
    assert exact["channel"] == {"stream_id": 1, "name": "One",
                                "epg_channel_id": "one.se"}
    assert sr.rule_key(exact) == "1" and sr.rule_key(word) == ""
    assert sr.matches(exact, "the show") and sr.matches(exact, "THE SHOW ")
    assert not sr.matches(exact, "The Show: Special")
    assert sr.matches(word, "Evening News") and sr.matches(word, "NEWSNIGHT")
    assert not sr.matches(word, "Sports")
    assert sr.make_rule("   ") is None and sr.make_rule("x", "regex") is None
    # A channel without an id cannot be a one-channel rule.
    assert sr.make_rule("x", sr.EXACT, {"name": "No id"})["scope"] == sr.ANY
    m = sr.Matcher([exact, word])
    assert m("The Show") == (exact["id"],) and m("The News Show") == (word["id"],)


def test_padding_is_read_clamped_and_applied():
    s = _Settings()
    assert sr.padding(s) == (0, 0)                     # nothing changes by default
    s.update({sr.PAD_BEFORE_KEY: "5", sr.PAD_AFTER_KEY: 15})
    assert sr.padding(s) == (5, 15)
    s.update({sr.PAD_BEFORE_KEY: "999", sr.PAD_AFTER_KEY: "-3"})
    assert sr.padding(s) == (sr.PAD_BEFORE_MAX, 0)
    s.update({sr.PAD_BEFORE_KEY: "soon", sr.PAD_AFTER_KEY: None})
    assert sr.padding(s) == (0, 0)
    assert sr.padded(NOW + H, NOW + 2 * H, 2, 10, NOW) == (NOW + H - 120, NOW + 2 * H + 600)
    assert sr.padded(NOW + H, NOW + 2 * H, 0, 0, NOW) == (NOW + H, NOW + 2 * H)
    # Never before now.
    assert sr.padded(NOW + 60, NOW + H, 5, 0, NOW) == (NOW, NOW + H)


def test_airings_found_for_their_channel_window_and_rule():
    this = sr.make_rule("The Show", sr.EXACT, CH1)
    word = sr.make_rule("news", sr.WORD)
    table = {
        1: [("The Show", NOW + H, NOW + 1.5 * H),
            ("The Show", NOW + 30, NOW + H),             # starts too soon
            ("The Show", NOW - H, NOW + 0.5 * H),        # on the air already
            ("Late News", NOW + 2 * H, NOW + 3 * H),
            ("The Show", NOW + 8 * 86400, NOW + 8 * 86400 + H)],   # too far ahead
        2: [("The Show", NOW + H, NOW + 1.5 * H),        # not this rule's channel
            ("News", NOW + 4 * H, NOW + 5 * H)],
    }
    chans = [CH1, CH2, dict(CH1)]                        # CH1 listed twice
    found = sr.find_airings([this, word], chans, _guide(table), NOW)
    got = [(a.rule, a.key, a.title, a.start) for a in found]
    assert got == [(this["id"], "1", "The Show", NOW + H),
                   (word["id"], "1", "Late News", NOW + 2 * H),
                   (word["id"], "2", "News", NOW + 4 * H)], got
    # At most so many per rule, the soonest.
    many = {2: [("News", NOW + i * H, NOW + i * H + 600) for i in range(1, 9)]}
    found = sr.find_airings([word], [CH2], _guide(many), NOW, per_rule=3)
    assert [a.start for a in found] == [NOW + H, NOW + 2 * H, NOW + 3 * H]
    # Damaged rules are no rules.
    assert sr.find_airings([{"id": "x"}], [CH1], _guide(table), NOW) == []
    # The one-channel rules' channels, as the rules keep them.
    assert sr.rule_channels([this, word, dict(this)]) == [this["channel"]]


def test_a_guide_offset_moves_the_airings_onto_the_clock():
    rule = sr.make_rule("The Show", sr.EXACT, CH1)
    table = {1: [("The Show", NOW + H, NOW + 2 * H),
                 ("The Show", NOW - 1800, NOW + 1800)]}   # on at the guide's now
    found = sr.find_airings([rule], [CH1], _guide(table), NOW, shift=H)
    assert [(a.start, a.stop) for a in found] == [
        (NOW + H - 1800, NOW + H + 1800), (NOW + 2 * H, NOW + 3 * H)]
    assert [a.start for a in sr.find_airings([rule], [CH1], _guide(table), NOW)] == [NOW + H]


def test_plan_pads_and_leaves_out_what_is_recorded():
    a = _airing("r", 1, "The Show", NOW + H, NOW + 1.5 * H)
    plans = sr.plan([a], [], [], NOW, 2, 10)
    assert [(p.start, p.stop) for p in plans] == [(NOW + H - 120, NOW + 1.5 * H + 600)]
    # The same channel and start scheduled already (by the guide, padded).
    job = {"status": "scheduled", "url": a.url, "stream_key": "1", "title": "x",
           "start": NOW + H - 300, "stop": NOW + 1.5 * H + 300,
           "prog_start": NOW + H, "prog_stop": NOW + 1.5 * H}
    assert sr.plan([a], [job], [], NOW) == []
    # A timer recording on the same address over most of it.
    manual = {"status": "scheduled", "url": a.url, "title": "One",
              "start": NOW + H - 600, "stop": NOW + 2 * H}
    assert sr.plan([a], [manual], [], NOW) == []
    # Finished or cancelled jobs take nothing.
    assert len(sr.plan([a], [dict(manual, status="cancelled")], [], NOW)) == 1
    # A rule scheduled it before (cancelled since): not again.
    taken = [{"key": "1", "start": NOW + H, "stop": NOW + 1.5 * H,
              "title": "the show", "rule": "r"}]
    assert sr.plan([a], [], taken, NOW) == []
    # Only a few minutes of another recording on the channel is no cover.
    other = dict(job, prog_start=NOW + 0.5 * H, prog_stop=NOW + H + 300)
    assert len(sr.plan([a], [other], [], NOW)) == 1


def test_one_programme_at_one_time_is_recorded_once():
    hd = _airing("any", 1, "The Show", NOW + H, NOW + 1.5 * H)
    sd = _airing("any", 2, "the  show", NOW + H, NOW + 1.5 * H)
    repeat = _airing("any", 2, "The Show", NOW + 5 * H, NOW + 5.5 * H)
    plans = sr.plan([sd, hd, repeat], [], [], NOW)
    assert [(p.airing.key, p.airing.start) for p in plans] == [
        ("1", NOW + H), ("2", NOW + 5 * H)]
    # Already recorded on the HD channel by hand: not on SD either.
    job = {"status": "recording", "url": hd.url, "stream_key": "1",
           "title": "The Show", "start": NOW + H, "stop": NOW + 1.5 * H}
    assert [p.airing.key for p in sr.plan([sd], [job], [], NOW)] == []
    # Another programme at the same time on another channel is not a copy.
    other = _airing("any", 2, "Other", NOW + H, NOW + 1.5 * H)
    assert len(sr.plan([hd, other], [], [], NOW)) == 2


def test_a_rule_keeps_so_many_upcoming_recordings():
    mine = [{"status": "scheduled", "rule": "r", "url": f"u{i}", "title": "t",
             "start": NOW + i * H, "stop": NOW + i * H + 60} for i in range(1, 4)]
    airings = [_airing("r", 9, "News", NOW + 10 * H + i * H,
                       NOW + 10 * H + i * H + 600) for i in range(5)]
    assert len(sr.plan(airings, mine, [], NOW, per_rule=5)) == 2
    assert len(sr.plan(airings, [], [], NOW, per_rule=5)) == 5
    # Too soon to plan.
    assert sr.plan([_airing("r", 9, "News", NOW + 10, NOW + H)], [], [], NOW) == []


def test_back_to_back_recordings_meet_instead_of_overlapping():
    ep1 = _airing("r", 1, "Show", NOW + H, NOW + 1.5 * H)
    ep2 = _airing("r", 1, "Show", NOW + 1.5 * H, NOW + 2 * H)
    p1, p2 = sr.plan([ep1, ep2], [], [], NOW, 5, 10)
    assert p1.stop == p2.start == NOW + 1.5 * H
    assert p1.start == NOW + H - 300 and p2.stop == NOW + 2 * H + 600
    # A gap between them: they meet halfway, each with its own programme whole.
    ep3 = _airing("r", 1, "Show", NOW + 1.5 * H + 240, NOW + 2 * H)
    p1, p3 = sr.plan([ep1, ep3], [], [], NOW, 5, 10)
    assert p1.stop == p3.start == NOW + 1.5 * H + 120
    # Next to a recording already there: starts with its own programme.
    job = {"status": "scheduled", "url": ep1.url, "stream_key": "1", "title": "Show",
           "start": NOW + H - 300, "stop": NOW + 1.5 * H + 600,
           "prog_start": NOW + H, "prog_stop": NOW + 1.5 * H}
    (p2,) = sr.plan([ep2], [job], [], NOW, 5, 10)
    assert p2.start == NOW + 1.5 * H and p2.stop == NOW + 2 * H + 600
    # Before one already there: ends where it starts, never inside its own.
    job2 = dict(job, start=NOW + 1.5 * H - 300, stop=NOW + 2 * H,
                prog_start=NOW + 1.5 * H, prog_stop=NOW + 2 * H)
    (p1,) = sr.plan([ep1], [job2], [], NOW, 5, 10)
    assert p1.start == NOW + H - 300 and p1.stop == NOW + 1.5 * H
    # Other channels are left as they are.
    ch2 = _airing("r", 2, "Show", NOW + 1.5 * H, NOW + 2 * H)
    p1, q = sr.plan([ep1, ch2], [], [], NOW, 5, 10)
    assert p1.stop == NOW + 1.5 * H + 600 and q.start == NOW + 1.5 * H - 300


def test_a_rules_recording_follows_its_programme_in_the_guide():
    job = {"id": "j", "status": "scheduled", "rule": "r", "url": "http://x/1.ts",
           "stream_key": "1", "title": "Show", "start": NOW + H - 120,
           "stop": NOW + 2 * H + 600, "prog_start": NOW + H, "prog_stop": NOW + 2 * H}
    moved = _airing("r", 1, "Show", NOW + H + 600, NOW + 2 * H + 600)
    assert sr.retimed([moved], [job], NOW) == [(job, moved)]
    assert sr.retimed([_airing("r", 1, "Show", NOW + H, NOW + 2 * H)], [job], NOW) == []
    assert sr.retimed([moved], [dict(job, rule="other")], NOW) == []
    assert sr.retimed([moved], [dict(job, status="recording")], NOW) == []
    assert sr.retimed([moved], [dict(job, rule=None)], NOW) == []    # by hand: left be


def _in(source, rule, sid, title, start, stop):
    """An airing in *source*, known by its stream id only (a portal's)."""
    return sr.Airing(rule, {"stream_id": sid}, str(sid), title, start, stop, "", source)


def _job(source, sid, title, start, stop, rule=None, **kw):
    j = {"id": f"{source}{sid}{start}", "status": "scheduled", "url": "", "stream_key": str(sid),
         "title": title, "start": start, "stop": stop, "prog_start": start, "prog_stop": stop}
    if source:
        j["source"] = source
    if rule:
        j["rule"] = rule
    j.update(kw)
    return j


def test_one_stream_id_in_two_sources_is_two_channels():
    a = _in("A", "r", 1, "Show", NOW + H, NOW + 2 * H)
    # Source B's stream 1 recording then is another channel: not this one.
    other = _job("B", 1, "Other", NOW + H, NOW + 2 * H)
    assert [p.airing for p in sr.plan([a], [other], [], NOW)] == [a]
    # The same source's: recorded already. One kept before sources were (no
    # source): taken as this channel, as it always was.
    assert sr.plan([a], [_job("A", 1, "Other", NOW + H, NOW + 2 * H)], [], NOW) == []
    assert sr.plan([a], [_job("", 1, "Other", NOW + H, NOW + 2 * H)], [], NOW) == []
    # A rule's recording in source B does not follow source A's guide.
    mine = _job("B", 1, "Show", NOW + H, NOW + 2 * H, rule="r")
    moved = _in("A", "r", 1, "Show", NOW + H + 600, NOW + 2 * H + 600)
    assert sr.retimed([moved], [mine], NOW) == []
    assert sr.retimed([moved], [dict(mine, source="A")], NOW) == [(dict(mine, source="A"), moved)]
    # Found in a source, an airing says which.
    found = sr.find_airings([sr.make_rule("Show")], [{"stream_id": 1}],
                            _guide({1: [("Show", NOW + H, NOW + 2 * H)]}), NOW, source="A")
    assert [(a.key, a.source) for a in found] == [("1", "A")]


def test_a_programme_moved_far_takes_its_recording_with_it():
    job = _job("A", 1, "Show", NOW + H, NOW + H + 1800, rule="r")
    # Two hours later: no overlap with where it was - the same recording
    # moves, rather than a second one at the new time.
    later = _in("A", "r", 1, "Show", NOW + 3 * H, NOW + 3 * H + 1800)
    assert sr.retimed([later], [job], NOW) == [(job, later)]
    # The nearest of two, never one past MOVED_MAX_S, never another title.
    tomorrow = _in("A", "r", 1, "Show", NOW + H + 86400, NOW + H + 86400 + 1800)
    sooner = _in("A", "r", 1, "Show", NOW + 2 * H, NOW + 2 * H + 1800)
    assert sr.retimed([tomorrow, later, sooner], [job], NOW) == [(job, sooner)]
    assert sr.retimed([tomorrow], [job], NOW) == []
    assert sr.retimed([_in("A", "r", 1, "Other", NOW + 3 * H, NOW + 4 * H)], [job], NOW) == []
    # One another recording has already is not taken from it.
    there = _job("A", 1, "Show", NOW + 3 * H, NOW + 3 * H + 1800, rule="r", id="other")
    assert sr.retimed([later], [job, there], NOW) == []
    # The programme still where it was: that one, not a far one.
    still = _in("A", "r", 1, "Show", NOW + H, NOW + H + 1800)
    assert sr.retimed([later, still], [job], NOW) == []
    # Moved: the plan does not schedule the new time a second time.
    moved = dict(job, start=later.start, stop=later.stop, prog_start=later.start,
                 prog_stop=later.stop)
    assert sr.plan([later], [moved], [], NOW) == []


def test_a_keyword_on_any_channel_records_so_many_at_once():
    word = sr.make_rule("news", sr.WORD)
    rid = word["id"]
    # Four news programmes at once (not one programme on four channels:
    # that is recorded once anyway).
    news = [_in("A", rid, sid, f"News {sid}", NOW + H, NOW + 2 * H) for sid in (1, 2, 3, 4)]
    left: list = []
    got = sr.plan(news, [], [], NOW, wide={rid}, left_out=left)
    assert [p.airing.key for p in got] == ["1", "2"] and len(got) == sr.MAX_AT_ONCE
    assert [(a.key, why) for a, why in left] == [("3", sr.AT_ONCE), ("4", sr.AT_ONCE)]
    # One after the other is not at once; another rule is not held back.
    after = _in("A", rid, 3, "News 3", NOW + 2 * H, NOW + 3 * H)
    assert len(sr.plan(news[:2] + [after], [], [], NOW, wide={rid})) == 3
    assert len(sr.plan(news, [], [], NOW)) == 4
    # Its recordings already scheduled count.
    jobs = [_job("A", sid, "News", NOW + H, NOW + 2 * H, rule=rid) for sid in (5, 6)]
    assert sr.plan(news, jobs, [], NOW, wide={rid}) == []


def test_never_more_at_once_than_the_account_has_connections():
    show = _in("A", "r", 1, "Show", NOW + H, NOW + 2 * H)
    manual = _job("A", 2, "Film", NOW + 1.5 * H, NOW + 3 * H)
    left: list = []
    assert sr.plan([show], [manual], [], NOW, connections=1, left_out=left) == []
    assert left == [(show, sr.CONNECTIONS)]
    assert len(sr.plan([show], [manual], [], NOW, connections=2)) == 1
    # Not known (None) or no limit (0): no cap. Another source's recording
    # takes another account's connection; one riding the player's, none.
    assert len(sr.plan([show], [manual], [], NOW, connections=None)) == 1
    assert len(sr.plan([show], [manual], [], NOW, connections=0)) == 1
    assert len(sr.plan([show], [dict(manual, source="B")], [], NOW, connections=1)) == 1
    assert len(sr.plan([show], [dict(manual, inplayer=True)], [], NOW, connections=1)) == 1
    # Back to back is one at a time.
    nxt = _in("A", "r", 1, "Show", NOW + 2 * H, NOW + 3 * H)
    assert len(sr.plan([show, nxt], [], [], NOW, connections=1)) == 2


def test_the_store_keeps_rules_per_source_and_forgets_with_them():
    s = _Settings()
    store = sr.SeriesRuleStore(s, "pl1")
    rule = sr.make_rule("The Show", sr.EXACT, CH1)
    assert store.add(rule)["id"] == rule["id"]
    # The same again (any spelling) is the one already kept.
    again = sr.make_rule("the show", sr.EXACT, CH1)
    assert store.add(again)["id"] == rule["id"] and len(store.rules()) == 1
    assert store.add(sr.make_rule("The Show"))["scope"] == sr.ANY
    assert len(store.rules()) == 2 and store.add({"id": "bad"}) is None
    a = _airing(rule["id"], 1, "The Show", NOW + H, NOW + 2 * H)
    store.remember([sr.Plan(a, a.start, a.stop)], NOW)
    assert store.taken() == [{"key": "1", "start": NOW + H, "stop": NOW + 2 * H,
                              "title": "the show", "rule": rule["id"]}]
    # Kept in the settings, per source.
    back = sr.SeriesRuleStore(s, "pl1")
    assert [r["id"] for r in back.rules()] == [r["id"] for r in store.rules()]
    assert back.taken() == store.taken() and sr.SeriesRuleStore(s, "pl2").rules() == []
    # Long over: forgotten.
    back.remember([], NOW + 2 * H + sr.TAKEN_KEEP_S + 1)
    assert back.taken() == []
    # A rule deleted takes what it scheduled from the memory with it.
    store.remove(rule["id"])
    assert store.taken() == [] and store.get(rule["id"]) is None
    assert not store.remove("nope")
    # Damaged settings: an empty store, no error.
    for junk in ("not json", "[1, 2]", '{"rules": 5, "taken": "x"}',
                 '{"rules": [{"id": "a", "kind": "title"}]}'):
        assert sr.SeriesRuleStore(_Settings({"series_rules": junk})).rules() == []


def test_the_guide_hands_over_only_the_titles_asked_for():
    from dopeiptv.providers.client import DemoClient
    from dopeiptv.providers.epg import XmltvGuide
    g = XmltvGuide(DemoClient())
    g._by_id = {"one.se": (array("q", [100, 200, 300]), array("q", [200, 300, 400]),
                           ("A", "B", "A"), ("", "", ""))}
    g._loaded = True
    ch = {"epg_channel_id": "one.se"}
    assert [p["start_timestamp"] for p in g.programmes_in(ch, 0, 1000)] == [100, 200, 300]
    asked = []
    got = g.programmes_in(ch, 150, 1000, lambda t: asked.append(t) or t == "A")
    assert [p["start_timestamp"] for p in got] == [100, 300] and asked == ["A", "B", "A"]


_WINDOW = r"""
import os, sys, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from array import array
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QComboBox, QDialog, QLineEdit, QListWidget, QPushButton
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
from dopeiptv.ui.settings_ui import Stepper

w = MainWindow(DemoClient(), s, PlaylistStore(s))
# No recorder process: a recorder "found", and nothing may start.
w.rec.recorder = lambda: ("ffmpeg", "/bin/true")
started = []
w.rec._spawn = lambda j: started.append(j)

def wait(cond, secs=15):
    end = time.monotonic() + secs
    while not cond() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    assert cond()

def idle():
    wait(lambda: not getattr(w, "_series_busy", False))
    app.processEvents()

# Settings untouched: no padding key written (0 and 0 stay the default).
QDialog.exec = lambda self: 1
w.open_settings()
assert s.value(sr.PAD_BEFORE_KEY) is None and s.value(sr.PAD_AFTER_KEY) is None
s.setValue(sr.PAD_BEFORE_KEY, 2)
s.setValue(sr.PAD_AFTER_KEY, 10)

H = 3600
base = int(time.time() // 60 * 60) + 2 * H
def sched(*progs):
    progs = sorted(progs, key=lambda p: p[1])
    return (array("q", [p[1] for p in progs]), array("q", [p[2] for p in progs]),
            tuple(p[0] for p in progs), tuple("" for _p in progs))
g = XmltvGuide(w.client)
g._by_id = {
    "bbb": sched(("The Show", base, base + 1800), ("News at Ten", base + 1800, base + 3600),
                 ("The Show", base + 86400, base + 86400 + 1800)),
    "bip": sched(("The Show", base, base + 1800), ("The Show", base + 3 * H, base + 3 * H + 1800),
                 ("Late News", base + H, base + H + 1200)),
    "sin": sched(("Other", base, base + H)),
}
g._by_name = {normalize_name("Big Buck Bunny"): "bbb", normalize_name("Apple BipBop"): "bip",
              normalize_name("Sintel"): "sin"}
g._loaded = True
w.xmltv = g
chans = w.client.live_streams(None)
bbb, bip, sin = chans[0], chans[1], chans[2]
url = {c["stream_id"]: w.client.live_url(c["stream_id"], "ts") for c in chans}

def answer(text=None, kind=0, where=0, then=None):
    # The series dialog answered as given; any other dialog goes to *then*.
    def run(self):
        if self.windowTitle() == tr("series_title"):
            if text is not None:
                self.findChild(QLineEdit).setText(text)
            combos = self.findChildren(QComboBox)
            combos[0].setCurrentIndex(kind)
            combos[1].setCurrentIndex(where)
            return 1
        return then(self) if then else 0
    QDialog.exec = run

def rule_jobs(status="scheduled"):
    return sorted(((j["url"], j["prog_start"], j["start"], j["stop"], j["title"])
                   for j in w.rec.jobs if j.get("rule") and j["status"] == status),
                  key=lambda t: (t[1], t[0]))

# 1. "Record series…" on a programme: this channel, the exact title. Both
# airings on that channel, padded; not the other channel's.
answer()
rule = w._record_series(bbb, "The Show")
assert rule and rule["scope"] == sr.THIS and rule["kind"] == sr.EXACT, rule
wait(lambda: len(rule_jobs()) == 2)
idle()
assert rule_jobs() == [
    (url[1], base, base - 120, base + 1800 + 600, "The Show"),
    (url[1], base + 86400, base + 86400 - 120, base + 86400 + 1800 + 600, "The Show")], rule_jobs()
j = next(j for j in w.rec.jobs if j.get("rule"))
assert j["stream_key"] == "1" and j["prog_stop"] == base + 1800 and j["channel"] == "Big Buck Bunny"

# 2. Looked through again: nothing twice.
w._check_series_rules()
idle()
assert len(rule_jobs()) == 2

# 3. Any channel: the repeat on the other channel, not its copy at the same time.
answer(where=1)
anyr = w._record_series(bbb, "The Show")
assert anyr["scope"] == sr.ANY
wait(lambda: len(rule_jobs()) == 3)
idle()
assert (url[2], base + 3 * H, base + 3 * H - 120, base + 3 * H + 1800 + 600, "The Show") in rule_jobs()
assert not [t for t in rule_jobs() if t[0] == url[2] and t[1] == base]

# 4. Cancelled by the user: not brought back.
rep = next(j for j in w.rec.jobs if j.get("rule") and j["url"] == url[2])
w.rec.cancel(rep["id"])
w._check_series_rules()
idle()
assert len(rule_jobs()) == 2 and len(rule_jobs("cancelled")) == 1

# 5. A keyword on any channel. On the first channel it starts right after
# the show, where that recording's padding would have had it start inside.
answer(text="news", kind=1, where=0)
word = w._record_series(None, "")
assert word["kind"] == sr.WORD and word["scope"] == sr.ANY
wait(lambda: len(rule_jobs()) == 4)
idle()
news = [t for t in rule_jobs() if "News" in t[4]]
assert news == [(url[1], base + 1800, base + 1800, base + 3600 + 600, "News at Ten"),
                (url[2], base + H, base + H - 120, base + H + 1200 + 600, "Late News")], news

# 6. A programme recorded from the guide by hand: padded, and known as such.
w._record_programme(sin, {"title": "Other"}, base, base + H)
mine = [j for j in w.rec.jobs if j["title"] == "Other"]
assert len(mine) == 1 and (mine[0]["start"], mine[0]["stop"]) == (base - 120, base + H + 600)
assert mine[0]["stream_key"] == "3" and mine[0]["prog_start"] == base and "rule" not in mine[0]

# 7. Settings > Recording: the padding steppers and the rules, deleted at once
# with the recordings they scheduled that had not started.
def settings(self):
    boxes = {b.accessibleName(): b for b in self.findChildren(Stepper)}
    assert boxes[tr("rec_pad_before")].value() == 2 and boxes[tr("rec_pad_after")].value() == 10
    boxes[tr("rec_pad_before")].setValue(5)
    boxes[tr("rec_pad_after")].setValue(30)
    lst = self.findChild(QListWidget, "SeriesRuleList")
    rows = [lst.item(i).text() for i in range(lst.count())]
    assert rows == [tr("series_row_word_any", word="news"),
                    tr("series_row_title_on", title="The Show", channel="Big Buck Bunny"),
                    tr("series_row_title_any", title="The Show")], rows
    delete = [b for b in self.findChildren(QPushButton) if b.text() == tr("ctx_delete")][0]
    lst.setCurrentRow(0)
    delete.click()
    assert lst.count() == 2
    # Add a keyword from here: the same dialog, any channel.
    answer(text="Sport", kind=1, where=0, then=settings)
    [b for b in self.findChildren(QPushButton) if b.text() == tr("series_add_word")][0].click()
    assert lst.count() == 3
    return 1
answer(then=settings)
w.open_settings()
assert int(s.value(sr.PAD_BEFORE_KEY)) == 5 and int(s.value(sr.PAD_AFTER_KEY)) == 30
assert sr.padding(s) == (5, 30)
assert not [t for t in rule_jobs() if "News" in t[4]], rule_jobs()
assert len([j for j in w.rec.jobs if "News" in j["title"] and j["status"] == "cancelled"]) == 2
assert {r["text"] for r in w.series_rules.rules()} == {"The Show", "Sport"}
idle()
assert not started, started
print("SERIES_OK", flush=True)
"""


def test_the_window_schedules_from_rules_against_a_guide():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for _attempt in (1, 2):
        out = subprocess.run([sys.executable, "-c", _WINDOW], cwd=_REPO_ROOT, env=env,
                             capture_output=True, text=True, timeout=180)
        if "SERIES_OK" in out.stdout:
            return
        if "AssertionError" in out.stderr:
            break
    raise AssertionError(out.stdout + out.stderr[-4000:])


def test_the_guides_menu_hands_the_programme_to_the_window():
    from types import SimpleNamespace
    from unittest import mock

    from dopeiptv.ui.epg_grid import EpgGridDialog

    win = SimpleNamespace(_record_series=mock.Mock(return_value={"text": "News"}))
    grid = SimpleNamespace(window=win, info=mock.Mock())
    ch = {"stream_id": 5, "name": "One"}
    EpgGridDialog._record_series(grid, ch, {"title": "News", "start_timestamp": 1.0})
    win._record_series.assert_called_once_with(ch, "News", parent=grid)
    grid.info.setText.assert_called_once_with("● News")
    win._record_series.return_value = None                   # cancelled: nothing said
    EpgGridDialog._record_series(grid, ch, {"title": "News"})
    assert grid.info.setText.call_count == 1
