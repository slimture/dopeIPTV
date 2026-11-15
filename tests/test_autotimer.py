"""An Enigma2 box's AutoTimer, against a fake OpenWebif that answers as the
plugin does (the XML is buildConfig's for the web interface; /autotimer/edit
changes only what it is sent, unquoting its texts a second time): the rules
read, a rule changed and read back with what the editor does not know left
untouched, on/off through /autotimer/change or /autotimer/edit, remove, a box
without the plugin said plainly, the simulate and the box's timers as what
the box records - which the app's series plan then leaves to the box - and
the series card's rows saying who records each."""
from __future__ import annotations

import time
from urllib.parse import unquote
from xml.etree import ElementTree as ET

import pytest

from dopeiptv.core import series_rules as sr
from dopeiptv.i18n import tr
from dopeiptv.providers import autotimer as at
from dopeiptv.providers.enigma2 import Enigma2Client
from tests.test_boxes import EURO, FAV_TV, IPTV, SPORT_TV, SVT1, FakeBox, openwebif_routes

NOW = int(time.time())
H = 3600
OK = ('<?xml version="1.0" encoding="UTF-8" ?>\n<e2simplexmlresult>\n\t<e2state>True</e2state>\n'
      '\t<e2statetext>{}</e2statetext>\n</e2simplexmlresult>\n')
NO_PLUGIN = ('<?xml version="1.0" encoding="UTF-8" ?><e2simplexmlresult><e2state>false</e2state>'
             '<e2statetext>AutoTimer Plugin not found</e2statetext></e2simplexmlresult>')
CONFIG = f"""<?xml version="1.0" ?>
<autotimer version="7">

 <defaults id="-1" encoding="UTF-8">
 </defaults>

 <timer name="Tatort" match="Tatort" enabled="yes" id="1" from="20:00" to="23:30" offset="5.0,10.0" location="/media/hdd/movie/krimi/" lastBegin="0" counter="0" left="0" avoidDuplicateDescription="2" encoding="UTF-8" searchType="exact" vps_enabled="yes" series_labeling="yes" always_zap="1" funky="kept">
  <e2service>
   <e2servicereference>{SVT1}</e2servicereference>
   <e2servicename>SVT1 HD</e2servicename>
  </e2service>
  <e2service>
   <e2servicereference>{SPORT_TV.replace('"', "&quot;")}</e2servicereference>
   <e2servicename>Sport</e2servicename>
  </e2service>
  <afterevent from="01:00" to="06:00">standby</afterevent>
  <exclude where="title">Vorschau</exclude>
  <include where="dayofweek">weekend</include>
  <e2tags>Krimi</e2tags>
  <e2tag>Krimi</e2tag>
 </timer>

 <timer name="news" match="Nyheter" enabled="no" id="2" encoding="UTF-8" justplay="1" setEndtime="0" zap_wakeup="always">
 </timer>

</autotimer>
"""


def _plugin_edit(rules: ET.Element, q: dict) -> str:
    """/autotimer/edit as AutoTimerAddOrEditAutoTimerResource does it: the
    rule by its id (a new one without), only the fields sent changed, the
    texts unquoted once more."""
    rid = q.get("id")
    el = next((t for t in rules.findall("timer") if t.get("id") == rid), None) if rid else None
    if el is None:
        el = ET.SubElement(rules, "timer", {"id": str(1 + max(int(t.get("id")) for t in rules.findall("timer")))})

    def attr(name: str, value: str | None) -> None:
        if value in (None, ""):
            el.attrib.pop(name, None)
        else:
            el.set(name, value)
    for key, name in (("match", "match"), ("name", "name")):
        if key in q:
            el.set(name, unquote(q[key]))
    if "enabled" in q:
        el.set("enabled", "yes" if q["enabled"] in ("yes", "1") else "no")
    if "searchType" in q:
        attr("searchType", None if q["searchType"] == "partial" else q["searchType"])
    if "searchCase" in q:
        attr("searchCase", "sensitive" if q["searchCase"] == "sensitive" else None)
    if "justplay" in q:
        attr("justplay", "1" if q["justplay"] == "1" else None)
    if "timespanFrom" in q:
        attr("from", q["timespanFrom"] or None)
        attr("to", q["timespanTo"] or None)
    if "offset" in q:
        attr("offset", q["offset"] or None)
    if "location" in q:
        attr("location", q["location"] or None)
    if "avoidDuplicateDescription" in q:
        attr("avoidDuplicateDescription", None if q["avoidDuplicateDescription"] == "0"
             else q["avoidDuplicateDescription"])
    if "services" in q or "bouquets" in q:
        for s in el.findall("e2service"):
            el.remove(s)
        for ref in [x for x in unquote(q.get("services", "") + "," + q.get("bouquets", "")).split(",") if x]:
            s = ET.SubElement(el, "e2service")
            ET.SubElement(s, "e2servicereference").text = ref
            ET.SubElement(s, "e2servicename").text = "?"
    return OK.format("AutoTimer was changed successfully" if rid else "AutoTimer was added successfully")


def box_routes(state: dict, change: bool = True) -> dict:
    r = openwebif_routes()
    r["/autotimer"] = lambda q: ET.tostring(state["rules"], encoding="unicode")
    r["/autotimer/edit"] = lambda q: _plugin_edit(state["rules"], q)
    r["/autotimer/remove"] = lambda q: OK.format("AutoTimer was removed")
    if change:
        r["/autotimer/change"] = lambda q: OK.format("AutoTimer was changed successfully")
    return r


@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def fake():
    made: list[FakeBox] = []

    def make(routes):
        b = FakeBox(routes)
        made.append(b)
        return b
    yield make
    for b in made:
        b.close()


def _client(box) -> Enigma2Client:
    c = Enigma2Client(box.base)
    c.authenticate()
    return c


# -- reading the rules ----------------------------------------------------------

def test_the_rules_as_the_box_lists_them():
    got = at.parse_rules(ET.fromstring(CONFIG))
    assert got.present and [r.id for r in got.rules] == [1, 2]
    t, n = got.rules
    assert (t.name, t.match, t.enabled, t.search_type, t.case) == ("Tatort", "Tatort", True, "exact", False)
    assert t.services == [SVT1] and t.bouquets == [SPORT_TV] and t.names[SPORT_TV] == "Sport"
    assert t.timespan == ("20:00", "23:30") and t.offset == (5, 10)
    assert t.location == "/media/hdd/movie/krimi/" and t.avoid_dup == 2 and t.always_zap
    assert t.days == ["weekend"] and t.day_set() == {"5", "6"} and t.tags == ["Krimi"]
    # What the editor does not show is still there, as the box wrote it.
    assert t.attrs["vps_enabled"] == "yes" and t.attrs["funky"] == "kept"
    assert (n.enabled, n.justplay, n.search_type, n.timespan, n.offset) == (False, True, "partial", None, None)


def test_a_rule_changed_and_read_back_keeps_what_the_editor_does_not_know(fake):
    state = {"rules": ET.fromstring(CONFIG)}
    box = fake(box_routes(state))
    c = _client(box)
    a = c.autotimer()
    rule = a.rules().rules[0]
    assert a.present is True
    new = rule.copy(match="Tatort 100%", offset=(2, 15), location="", services=[SVT1, IPTV],
                    bouquets=[FAV_TV], timespan=None)
    assert a.save(new, rule) == (True, "AutoTimer was changed successfully")
    sent = box.paths("/autotimer/edit")[-1]
    # Texts the plugin unquotes again go quoted; an IPTV entry's %3a survives.
    assert sent["match"] == "Tatort%20100%25" and sent["id"] == "1" and sent["always_zap"] == "1"
    assert "dayofweek" not in sent and "funky" not in sent and "vps_enabled" not in sent
    back = a.rules().rules[0]
    assert back.match == "Tatort 100%" and back.offset == (2, 15) and back.location == ""
    assert back.services == [SVT1, IPTV] and back.bouquets == [FAV_TV] and back.timespan is None
    el = state["rules"].find("timer")
    assert el.get("funky") == "kept" and el.get("vps_enabled") == "yes" and el.get("series_labeling") == "yes"
    assert [x.tag for x in el if x.tag != "e2service"] == ["afterevent", "exclude", "include", "e2tags", "e2tag"]
    assert el.find("afterevent").get("from") == "01:00"


def test_the_edit_query_sends_the_days_only_when_they_changed():
    rule = at.parse_rules(ET.fromstring(CONFIG)).rules[0]
    same = at.edit_params(rule.copy(days=["5", "6"]), rule)
    assert "dayofweek" not in same                           # "weekend" stays "weekend"
    p = at.edit_params(rule.copy(days=["0", "2"]), rule)
    assert p["dayofweek"] == ["0", "2"] and p["title"] == [""]
    every = at.edit_params(rule.copy(days=[]), rule)
    assert every["dayofweek"] == [""]                        # every day again
    fresh = at.edit_params(at.Rule(match="Bolibompa", search_type=at.PARTIAL))
    assert "id" not in fresh and fresh["name"] == "Bolibompa" and fresh["services"] == ""
    assert fresh["offset"] == "" and fresh["timespanFrom"] == "" and "always_zap" not in fresh


def test_on_off_and_remove(fake):
    state = {"rules": ET.fromstring(CONFIG)}
    box = fake(box_routes(state))
    a = _client(box).autotimer()
    tatort, news = a.rules().rules
    assert a.set_enabled(news, True)[0]
    assert box.paths("/autotimer/change") == [{"id": "2", "enabled": "yes"}]
    assert a.remove(tatort) == (True, "AutoTimer was removed")
    assert box.paths("/autotimer/remove") == [{"id": "1"}]
    # A plugin without /autotimer/change: the edit with the id and the switch.
    box2 = fake(box_routes({"rules": ET.fromstring(CONFIG)}, change=False))
    a2 = _client(box2).autotimer()
    tatort = a2.rules().rules[0]
    assert a2.set_enabled(tatort, False)[0]
    sent = box2.paths("/autotimer/edit")[-1]
    assert sent == {"id": "1", "match": "Tatort", "name": "Tatort", "enabled": "no", "always_zap": "1"}


def test_a_box_without_the_plugin_says_so(fake):
    r = openwebif_routes()
    r["/autotimer"] = NO_PLUGIN
    r["/web/timerlist"] = "<e2timerlist></e2timerlist>"
    a = _client(fake(r)).autotimer()
    got = a.rules()
    assert not got.present and got.rules == [] and a.present is False
    assert "not found" in got.said
    assert a.records(NOW) == []
    # The old web interface has no /autotimer at all.
    r2 = openwebif_routes()
    a2 = _client(fake(r2)).autotimer()
    assert a2.rules().present is False and a2.present is False


# -- what the box will record -----------------------------------------------------

SIMULATE = f"""<?xml version="1.0" encoding="UTF-8" ?>
<e2autotimersimulate api_version="1.7">
<e2simulatedtimer>
   <e2servicereference>{SVT1}</e2servicereference>
   <e2servicename>SVT1 HD</e2servicename>
   <e2name>Tatort</e2name>
   <e2timebegin>{NOW + 2 * H - 300}</e2timebegin>
   <e2timeend>{NOW + 3 * H + 600}</e2timeend>
   <e2autotimername>Tatort</e2autotimername>
   <e2state>OK</e2state>
</e2simulatedtimer>
<e2simulatedtimer>
   <e2servicereference>{EURO}</e2servicereference>
   <e2servicename>Eurosport</e2servicename>
   <e2name>Tatort</e2name>
   <e2timebegin>{NOW + 5 * H}</e2timebegin>
   <e2timeend>{NOW + 6 * H}</e2timeend>
   <e2autotimername>Tatort</e2autotimername>
   <e2state>Skip</e2state>
   <e2message>Skipping an event because a timer on same service exists</e2message>
</e2simulatedtimer>
</e2autotimersimulate>"""
TIMERS = f"""<?xml version="1.0" encoding="UTF-8"?>
<e2timerlist>
<e2timer><e2servicereference>{EURO}</e2servicereference><e2servicename>Eurosport</e2servicename>
<e2name>Tatort</e2name><e2timebegin>{NOW + 5 * H}</e2timebegin><e2timeend>{NOW + 6 * H}</e2timeend>
<e2justplay>0</e2justplay><e2disabled>0</e2disabled><e2state>0</e2state></e2timer>
<e2timer><e2servicereference>{EURO}</e2servicereference><e2servicename>Eurosport</e2servicename>
<e2name>Zap only</e2name><e2timebegin>{NOW + H}</e2timebegin><e2timeend>{NOW + H}</e2timeend>
<e2justplay>1</e2justplay><e2disabled>0</e2disabled><e2state>0</e2state></e2timer>
<e2timer><e2servicereference>{SVT1}</e2servicereference><e2servicename>SVT1 HD</e2servicename>
<e2name>Over</e2name><e2timebegin>{NOW - 3 * H}</e2timebegin><e2timeend>{NOW - 2 * H}</e2timeend>
<e2justplay>0</e2justplay><e2disabled>0</e2disabled><e2state>3</e2state></e2timer>
</e2timerlist>"""


def test_the_simulate_and_the_timers_are_what_the_box_records(fake):
    sims = at.parse_simulate(ET.fromstring(SIMULATE))
    assert [(x["title"], x["channel"]) for x in sims] == [("Tatort", "SVT1 HD")]   # no Skip
    state = {"rules": ET.fromstring(CONFIG)}
    r = box_routes(state)
    r["/autotimer/simulate"] = SIMULATE
    r["/web/timerlist"] = TIMERS
    box = fake(r)
    c = _client(box)
    a = c.autotimer()
    got = a.records(NOW)
    svt, euro = str(c.stream_of(SVT1)), str(c.stream_of(EURO))
    assert sorted((x["key"], x["start"], x["title"]) for x in got) == sorted([
        (euro, NOW + 5 * H, "Tatort"), (svt, NOW + 2 * H - 300, "Tatort")])
    # The simulate is the box's whole guide: asked again only after a while
    # or a change on the box; the timers every time.
    a.records(NOW + 60)
    assert len(box.paths("/autotimer/simulate")) == 1 and len(box.paths("/web/timerlist")) == 2
    a.save(a.rules().rules[1].copy(enabled=True))
    a.records(NOW + 120)
    assert len(box.paths("/autotimer/simulate")) == 2
    # One rule's preview: oe-alliance's takes its id; OpenPLi's answers for
    # every rule, so what is another rule's is left out.
    tatort, news = a.rules().rules
    assert [x["title"] for x in a.simulate(tatort)] == ["Tatort"] and a.simulate(news) == []
    assert box.paths("/autotimer/simulate")[-1] == {"id": "2"}


def test_run_now_says_what_the_box_did(fake):
    r = box_routes({"rules": ET.fromstring(CONFIG)})
    r["/autotimer/parse"] = ('<?xml version="1.0" encoding="UTF-8" ?><e2simplexmlresult><ignore />'
                             "<e2state>True</e2state><e2statetext>Found a total of 3 matching Events.\n"
                             "1 Timer were added and\n0 modified,\n0 conflicts encountered,\n0 similars added."
                             "</e2statetext></e2simplexmlresult>")
    ok, said = _client(fake(r)).autotimer().run()
    assert ok and said.startswith("Found a total of 3 matching Events.")


# -- the app's plan leaves to the box what the box records ---------------------------

def _airing(title, key, start, stop, rule="r1"):
    return sr.Airing(rule, {"stream_id": int(key)}, key, title, start, stop, source="pl1")


def test_the_series_plan_leaves_to_the_box_what_it_records():
    now = float(NOW)
    show = _airing("Tatort", "1", now + 2 * H, now + 3 * H)
    other_channel = _airing("Tatort", "2", now + 2 * H, now + 3 * H)
    short_after = _airing("Kurz", "1", now + 3 * H, now + 3 * H + 900)
    later = _airing("Tatort", "1", now + 26 * H, now + 27 * H)
    box = [{"key": "1", "start": now + 2 * H - 300, "stop": now + 3 * H + 600,
            "title": "Tatort", "source": "pl1"}]
    left: list = []
    plans = sr.plan([show, other_channel, short_after, later], [], [], now, left_out=left, box=box)
    assert [p.airing for p in plans] == [short_after, later]
    assert [(a, why) for a, why in left] == [(show, sr.BOX), (other_channel, sr.BOX)]
    # A timer the box named otherwise still holds the whole programme.
    named = [{"key": "1", "start": now + 3 * H - 60, "stop": now + 3 * H + 1200,
              "title": "My timer", "source": "pl1"}]
    assert sr.plan([short_after], [], [], now, box=named) == []
    # A rule's recording scheduled before the box took it on: the box's now;
    # forgotten, so it can be scheduled again should the box stop.
    job = {"id": "j1", "rule": "r1", "status": "scheduled", "stream_key": "1", "source": "pl1",
           "title": "Tatort", "start": now + 2 * H - 120, "stop": now + 3 * H + 600,
           "prog_start": now + 2 * H, "prog_stop": now + 3 * H}
    other_source = dict(job, id="j2", source="pl9", stream_key="1")
    by_hand = dict(job, id="j3", rule="")
    assert sr.left_to_box([job, other_source, by_hand], box, now) == [job]

    class S(dict):
        def value(self, k, d=None):
            return self.get(k, d)

        def setValue(self, k, v):  # noqa: N802 - Qt's name
            self[k] = v
    store = sr.SeriesRuleStore(S(), "pl1")
    store.remember([sr.Plan(show, show.start, show.stop), sr.Plan(later, later.start, later.stop)], now)
    store.forget([job])
    assert [t["start"] for t in store.taken()] == [later.start]


def test_the_window_leaves_the_box_its_recordings(app, tmp_path):
    from dopeiptv.ui import mw_series
    from tests.test_recording_address_later import Guide, Store, Window, _rec
    now = time.time()

    class Box:
        slow_links = True
        max_connections = None
        chans = [{"stream_id": 7, "name": "Seven"}, {"stream_id": 8, "name": "Eight"}]

        def __init__(self):
            self.at = type("AT", (), {"present": True,
                                      "records": lambda _s, _now: [{"key": "7", "start": now + 2 * H - 300,
                                                                     "stop": now + 3 * H + 600, "title": "Tatort"}]})()

        def live_streams(self, _cat=None):
            return [dict(c) for c in self.chans]

        def live_url(self, sid, fmt="ts"):
            return f"http://box/{sid}"

        def autotimer(self):
            return self.at

    box = Box()
    rec = _rec(tmp_path)
    w = Window(box, rec, Store({"id": "pl1", "kind": "enigma2", "name": "Box"}))
    w._rec_extra = lambda ch, later=False: {"source": "pl1"}
    rule = w.series_rules.add(sr.make_rule("Tatort", sr.EXACT, None))
    # Scheduled by the rule before the box took it on.
    rec.add_job("", "Tatort", now + 2 * H - 120, now + 3 * H, extra={
        "rule": rule["id"], "stream_key": "7", "source": "pl1",
        "prog_start": now + 2 * H, "prog_stop": now + 3 * H})
    guide = Guide({7: [("Tatort", now + 2 * H, now + 3 * H), ("Tatort", now + 26 * H, now + 27 * H)],
                   8: [("Tatort", now + 30 * H, now + 31 * H)]})
    found = mw_series._find(box, guide, w.series_rules.rules(), lambda c: True, now, 0.0, "pl1")
    assert found.box and found.box[0]["source"] == "pl1"
    assert w._series_schedule(found) == 2
    starts = sorted(j["prog_start"] for j in rec.jobs if j.get("rule"))
    assert starts == [now + 26 * H, now + 30 * H]                  # not the box's
    assert w.flashed[0] == tr("at_left_to_box_n", n=1)
    # Looked through again: nothing said twice.
    w._series_schedule(mw_series._find(box, guide, w.series_rules.rules(), lambda c: True,
                                       now, 0.0, "pl1"))
    assert [f for f in w.flashed if f == tr("at_left_to_box_n", n=1)] == [w.flashed[0]]


# -- the series card's rows ---------------------------------------------------------

def test_the_rows_say_who_records_each(app):
    from types import SimpleNamespace

    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QListWidget, QWidget

    from dopeiptv.ui import autotimer_ui
    tatort, news = at.parse_rules(ET.fromstring(CONFIG)).rules
    assert autotimer_ui.rule_label(tatort) == tr("series_row_title_on", title="Tatort",
                                                 channel="SVT1 HD, Sport")
    assert autotimer_ui.rule_label(news) == tr("at_row_off", rule=tr("series_row_word_any", word="news"))
    assert autotimer_ui.marked("x", True) == tr("series_row_by", rule="x", who=tr("series_by_box"))
    assert autotimer_ui.marked("x", False) == tr("series_row_by", rule="x", who=tr("series_by_app"))
    parent = QWidget()
    lst = QListWidget(parent)
    client = SimpleNamespace(autotimer=lambda: SimpleNamespace(present=True))
    panel = autotimer_ui.BoxRules(SimpleNamespace(client=client, pool=None), lst, lambda: None, parent)
    assert panel.active and panel.present
    panel._came(at.Rules(True, [tatort, news]))
    rows = [i.text() for i in panel.items(QIcon())]
    assert rows == [tr("series_row_by", rule=autotimer_ui.rule_label(news), who=tr("series_by_box")),
                    tr("series_row_by", rule=autotimer_ui.rule_label(tatort), who=tr("series_by_box"))]
    for item in panel.items(QIcon()):
        lst.addItem(item)
    lst.setCurrentRow(1)
    assert panel.current() is tatort
    panel.sync()
    assert panel.buttons["at_off"].text() == tr("at_off")
    panel._came(at.Rules(False, said="AutoTimer Plugin not found"))
    assert panel.status.text() == tr("at_none")
    # Any other source: nothing of the box's, no marks.
    plain = autotimer_ui.BoxRules(SimpleNamespace(client=object(), pool=None), lst, lambda: None, parent)
    assert not plain.active and plain.items(QIcon()) == []
    parent.deleteLater()


def test_the_editor_reads_back_what_it_shows(app):
    from types import SimpleNamespace

    from PyQt6.QtCore import Qt

    from dopeiptv.ui.autotimer_ui import AutoTimerDialog
    tatort = at.parse_rules(ET.fromstring(CONFIG)).rules[0]
    client = SimpleNamespace(
        autotimer=lambda: SimpleNamespace(present=True),
        movie_folders=lambda: [], bouquet_channels=lambda: [
            (FAV_TV, "Favourites (TV)", [(SVT1, "SVT1 HD"), (EURO, "Eurosport")])])

    class Pool:
        def start(self, _w):
            pass
    d = AutoTimerDialog(None, client, Pool(), tatort)
    same = d.value()
    assert (same.services, same.bouquets) == ([SVT1], [SPORT_TV])   # Sport is not in the list: kept
    assert same.offset == (5, 10) and same.day_set() == {"5", "6"} and same.timespan == ("20:00", "23:30")
    assert same.location == "/media/hdd/movie/krimi/" and same.avoid_dup == 2
    top = d.tree.topLevelItem(0)
    top.child(0).setCheckState(0, Qt.CheckState.Unchecked)
    top.setCheckState(0, Qt.CheckState.Checked)
    d.own_pad.setChecked(False)
    d.span.setChecked(False)
    d.days[0].setChecked(True)
    d.justplay.setCurrentIndex(1)
    got = d.value()
    assert got.services == [] and got.bouquets == [SPORT_TV, FAV_TV]
    assert got.offset is None and got.timespan is None and got.justplay
    assert got.day_set() == {"0", "5", "6"} and got.attrs["funky"] == "kept"
    d.deleteLater()
