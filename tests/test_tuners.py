"""An Enigma2 box's tuners (core/tuners.py) and the transponder check in the
window (ui/mw_connections.py): a service reference's transponder, what the
box says of its tuners, timers and TV, the verdicts (free, the last free
tuner, it would cut off a recording or the TV, a busy transponder is free),
a box that does not answer never in the way, the one question through the
shared path (the player, a multiview cell, a recording), the list's ring
and the In use now card's text."""
from __future__ import annotations

import logging
import threading
import time
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

from dopeiptv.core import tuners
from dopeiptv.core.tuners import BoxState, StateCache, Tune
from dopeiptv.i18n import tr

SVT1 = "1:0:19:283D:3FB:1:C00000:0:0:0:"
SVT2 = "1:0:19:283E:3FB:1:C00000:0:0:0:"
EURO = "1:0:19:1B1F:802:2:11A0000:0:0:0:"
IPTV = "4097:0:1:0:0:0:0:0:0:0:http%3a//iptv.example/live/a.ts:Web Kanal"


# -- service references ------------------------------------------------------------

def test_a_reference_names_its_transponder():
    assert tuners.transponder(SVT1) == "3FB:1:C00000"
    # Two channels of one multiplex: one transponder.
    assert tuners.transponder(SVT2) == tuners.transponder(SVT1)
    assert tuners.transponder(EURO) == "802:2:11A0000"
    # However the box writes the numbers.
    assert tuners.transponder("1:0:19:283d:03fb:0001:00c00000:0:0:0:") == "3FB:1:C00000"
    assert tuners.transponder("1:0:1:445D:453:1:C00000:0:0:0::ZDF") == "453:1:C00000"
    # DVB-T and cable have namespaces of their own.
    assert tuners.transponder("1:0:19:2:1:1:EEEE0000:0:0:0:") == "1:1:EEEE0000"


@pytest.mark.parametrize("sref", [
    IPTV,
    # An IPTV entry that carries made-up ids still has an address of its own.
    "4097:0:1:3E9:1:1:CCCC0000:0:0:0:http%3a//iptv.example/b.m3u8:B",
    "5002:0:1:1:1:1:C00000:0:0:0:http%3a//iptv.example/c.ts:C",
    # A DVB reference that points at another box's stream port.
    "1:0:19:283D:3FB:1:C00000:0:0:0:http%3a//192.168.1.9%3a8001/1%3a0%3a19%3a283D%3a3FB%3a1%3aC00000%3a0%3a0%3a0%3a:SVT1",
    "1:0:0:0:0:0:0:0:0:0:/media/hdd/movie/x.ts",          # a recording on the disk
    "1:64:1:0:0:0:0:0:0:0::--- Sport ---",                # a marker
    '1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "userbouquet.favourites.tv" ORDER BY bouquet',
    "", "N/A", "1:0:19", "x:y:z:a:b:c:d:e:f:g:", None,
])
def test_no_transponder_for_what_needs_no_tuner(sref):
    assert tuners.transponder(sref) is None


def test_the_stream_relay_on_the_box_is_the_channel_it_relays():
    relay = ("1:0:19:283D:3FB:1:C00000:0:0:0:http%3a//127.0.0.1%3a17999/"
             "1%3a0%3a19%3a283D%3a3FB%3a1%3aC00000%3a0%3a0%3a0%3a:SVT1")
    assert tuners.transponder(relay) == "3FB:1:C00000"
    # Quoted once more (OpenWebif's getcurrent), and with ids of zero outside.
    assert tuners.transponder(relay.replace("%", "%25")) == "3FB:1:C00000"
    zero = "4097:0:1:0:0:0:0:0:0:0:http%3a//127.0.0.1%3a17999/1%3a0%3a19%3a1B1F%3a802%3a2%3a11A0000%3a0%3a0%3a0%3a:E"
    assert tuners.transponder(zero) == "802:2:11A0000"


def test_a_stream_address_from_the_box_names_its_channel():
    assert tuners.stream_key(f"http://192.168.1.5:8001/{SVT1}") == "3FB:1:C00000"
    assert tuners.stream_key(f"http://root:secret@192.168.1.5:8001/{SVT1}") == "3FB:1:C00000"
    # The box's encoder: the options after the reference.
    assert tuners.stream_key(
        f"http://192.168.1.5:8002/{SVT1}?bitrate=1000000&width=640") == "3FB:1:C00000"
    for other in ("http://h.example.com/live/u/p/1.ts", "http://iptv.example/live/a.ts",
                  "/home/me/film.mkv", "", None):
        assert tuners.stream_key(other) is None


# -- what the box says ---------------------------------------------------------------

ABOUT_OWIF = """<e2abouts><e2about><e2webifversion>OWIF 2.2.1</e2webifversion>
<e2tunerinfo><e2nim><name>Tuner A</name><type>DVB-S2X</type></e2nim>
<e2nim><name>Tuner B</name><type>DVB-S2X</type></e2nim><e2nim><name></name></e2nim></e2tunerinfo>
</e2about></e2abouts>"""
ABOUT_FRONTENDS = """<e2abouts><e2about><e2frontends>
<e2frontend><e2name>Tuner A</e2name><e2model>BCM4501 (internal)</e2model></e2frontend>
<e2frontend><e2name>Tuner B</e2name><e2model>Philips TU1216</e2model></e2frontend>
</e2frontends></e2about></e2abouts>"""


def timer(ref: str, name: str, state: int, justplay: int = 0, disabled: int = 0) -> str:
    return (f"<e2timer><e2servicereference>{ref}</e2servicereference><e2servicename>Ch</e2servicename>"
            f"<e2name>{name}</e2name><e2disabled>{disabled}</e2disabled>"
            f"<e2justplay>{justplay}</e2justplay><e2state>{state}</e2state></e2timer>")


def current(ref: str, name: str) -> str:
    return (f"<e2currentserviceinformation><e2service><e2servicereference>{ref}</e2servicereference>"
            f"<e2servicename>{name}</e2servicename></e2service><e2eventlist></e2eventlist>"
            "</e2currentserviceinformation>")


def test_the_tuners_the_records_and_the_tv_as_the_box_says_them():
    assert tuners.tuner_names(ET.fromstring(ABOUT_OWIF)) == ["Tuner A", "Tuner B", "Tuner 3"]
    assert tuners.tuner_names(ET.fromstring(ABOUT_FRONTENDS)) == ["Tuner A", "Tuner B"]
    assert tuners.tuner_names(ET.fromstring("<e2abouts><e2about/></e2abouts>")) == []
    timers = ET.fromstring("<e2timerlist>" + timer(SVT1, "Rapport", 2) + timer(EURO, "Later", 0)
                           + timer(SVT2, "Zap", 2, justplay=1) + timer(EURO, "Off", 2, disabled=1)
                           + timer(EURO, "Done", 3) + timer(EURO, "Cykel", 2) + "</e2timerlist>")
    assert tuners.running_recordings(timers) == (
        Tune("recording", "Rapport", "3FB:1:C00000"), Tune("recording", "Cykel", "802:2:11A0000"))
    tv = tuners.on_tv(ET.fromstring(current(SVT1, "SVT1 HD")))
    assert tv == Tune("tv", "SVT1 HD", "3FB:1:C00000")
    # In standby, an IPTV channel or a recording played: no tuner.
    for ref in ("", IPTV, "1:0:0:0:0:0:0:0:0:0:/media/hdd/movie/x.ts"):
        assert tuners.on_tv(ET.fromstring(current(ref, "x"))) is None


# -- the verdicts -----------------------------------------------------------------------

TV_X = Tune("tv", "SVT1 HD", "A:1:C00000")
REC_Y = Tune("recording", "Rapport", "B:1:C00000")


def test_free_when_a_tuner_is_spare_or_the_transponder_busy_already():
    two = BoxState(3, TV_X)
    v = tuners.verdict(two, "C:1:C00000")
    assert v.kind == tuners.FREE and (v.tuners, v.in_use) == (3, 1)
    # Channels on a busy transponder are free, however full the box is.
    full = BoxState(2, TV_X, (REC_Y,))
    assert tuners.verdict(full, "B:1:C00000").kind == tuners.FREE
    assert tuners.verdict(full, "A:1:C00000").kind == tuners.FREE
    # An IPTV channel takes no tuner.
    assert tuners.verdict(full, None).kind == tuners.FREE
    # Two recordings on one transponder hold one tuner.
    shared = BoxState(2, None, (REC_Y, Tune("recording", "Sport", "B:1:C00000")))
    assert tuners.verdict(shared, "C:1:C00000").kind == tuners.LAST


def test_the_last_free_tuner():
    v = tuners.verdict(BoxState(2, TV_X), "C:1:C00000")
    assert v.kind == tuners.LAST and not v.cut


def test_it_would_cut_off_the_tv_before_a_recording():
    v = tuners.verdict(BoxState(2, TV_X, (REC_Y,)), "C:1:C00000")
    assert v.kind == tuners.CUTS and v.cut == (TV_X,) and v.cuts_tv and not v.cuts_recording


def test_it_would_cut_off_a_recording_and_says_which():
    v = tuners.verdict(BoxState(1, None, (REC_Y,)), "C:1:C00000")
    assert v.kind == tuners.CUTS and v.cut == (REC_Y,) and v.cuts_recording
    # The TV on the recording's transponder goes with it.
    tv_on_y = Tune("tv", "SVT2", "B:1:C00000")
    v = tuners.verdict(BoxState(1, tv_on_y, (REC_Y,)), "C:1:C00000")
    assert v.cut == (tv_on_y, REC_Y) and v.cuts_recording and v.cuts_tv


def test_the_apps_own_streams_hold_tuners_too():
    cell = Tune("multiview", "", "C:1:C00000")
    v = tuners.verdict(BoxState(2, None, (REC_Y,)), "D:1:C00000", [cell])
    # A stream of the app's own gives way before a recording.
    assert v.kind == tuners.CUTS and v.cut == (cell,)
    # ...but after the TV.
    v = tuners.verdict(BoxState(2, TV_X), "D:1:C00000", [cell])
    assert v.cut == (TV_X,)
    # One on the channel's own transponder: free.
    assert tuners.verdict(BoxState(1, None), "C:1:C00000", [cell]).kind == tuners.FREE


def test_a_box_that_does_not_say_its_tuners_says_nothing():
    assert tuners.verdict(BoxState(None, TV_X, (REC_Y,)), "C:1:C00000") is None
    assert tuners.verdict(None, "C:1:C00000") is None


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_a_state_is_kept_a_few_seconds_and_a_silent_box_is_left_alone():
    clock = Clock()
    cache = StateCache(clock)
    assert cache.state("b") is None and cache.due("b")
    cache.asking("b")
    assert not cache.due("b"), "never twice at a time"
    cache.put("b", BoxState(2))
    assert cache.state("b") == BoxState(2) and not cache.due("b")
    clock.t += tuners.ASK_SECS
    assert cache.due("b") and cache.state("b") is not None
    clock.t += tuners.USE_SECS
    assert cache.state("b") is None, "too old to say anything"
    cache.failed("b")
    assert not cache.due("b") and cache.did_not_answer("b")
    clock.t += tuners.RETRY_SECS
    assert cache.due("b")


# -- the client --------------------------------------------------------------------------

def test_the_client_asks_the_box_and_knows_each_channels_transponder(monkeypatch):
    from tests.test_boxes import FakeBox, openwebif_routes
    from dopeiptv.providers.enigma2 import Enigma2Client
    routes = openwebif_routes()
    routes["/web/about"] = ABOUT_OWIF
    routes["/web/timerlist"] = "<e2timerlist>" + timer(EURO, "Cykel", 2) + "</e2timerlist>"
    routes["/web/getcurrent"] = current(SVT1, "SVT1 HD")
    box = FakeBox(routes)
    try:
        c = Enigma2Client(box.base)
        c.authenticate()
        rows = {r["name"]: r["stream_id"] for r in c.live_streams()}
        assert c.tuner_key(rows["SVT1 HD"]) == "3FB:1:C00000"
        assert c.tuner_key(rows["Eurosport"]) == "802:2:11A0000"
        assert c.tuner_key(rows["Web Kanal"]) is None and c.tuner_key(99999) is None
        state = c.tuner_state()
        assert state.tuners == 3 and state.tv.name == "SVT1 HD"
        assert [t.name for t in state.recordings] == ["Cykel"]
        assert c.own_stream(c.live_url(rows["SVT1 HD"]))
        assert not c.own_stream(c.live_url(rows["Web Kanal"]))
        # The tuners were read with the login: /web/about is not asked again.
        abouts = len(box.paths("/web/about"))
        c.tuner_state()
        assert len(box.paths("/web/about")) == abouts
    finally:
        box.close()
    # A box that does not answer raises - off the window's thread.
    with pytest.raises(OSError):
        Enigma2Client(box.base).tuner_state()


# -- the window ------------------------------------------------------------------------

BOX = "http://box.example.com:8001/"


def ref(tsid: int) -> str:
    return f"1:0:19:1:{tsid:X}:1:C00000:0:0:0:"


class BoxClient:
    """A box source's client as the window sees it: stream_id = TSID."""

    max_connections = None
    box_info = {"name": "Living room box"}

    def __init__(self, state=None, slow: float = 0.0, fail: bool = False) -> None:
        self.state, self.slow, self.fail = state, slow, fail
        self.asked_on: list[str] = []

    def own_stream(self, url) -> bool:
        return str(url or "").startswith(BOX)

    def tuner_key(self, sid):
        return tuners.transponder(ref(int(sid)))

    def tuner_state(self):
        self.asked_on.append(threading.current_thread().name)
        if self.slow:
            time.sleep(self.slow)
        if self.fail:
            raise TimeoutError("timed out")
        return self.state

    def live_url(self, sid, fmt="ts") -> str:
        return BOX + ref(int(sid))


class _S(dict):
    def value(self, key, default=None):
        return self.get(key, default)


class _Rec:
    def __init__(self, jobs=()) -> None:
        self.jobs, self.added = list(jobs), []

    def total_cap_exceeded(self):
        return False

    def recorder(self):
        return ("ffmpeg", True)

    def add_job(self, url, title, *a, **k):
        self.added.append(url)

    def directory(self):
        return "/tmp/rec"


def window(client, player_url=None, jobs=(), cells=()):
    from dopeiptv.ui.mw_connections import _ConnectionsMixin
    from dopeiptv.ui.mw_recording import _RecordingMixin

    class W(_ConnectionsMixin, _RecordingMixin):
        def __init__(self):
            self.client = client
            self.player = SimpleNamespace(current_url=player_url, _dvr=None, _dvr_pool=None,
                                          isVisible=lambda: player_url is not None)
            self.rec = _Rec(jobs)
            self._multiview_win = (SimpleNamespace(cells=list(cells), _focused=None)
                                   if cells else None)
            self.playlist_store = SimpleNamespace(
                active_id="box1", active=lambda: {"id": "box1", "kind": "enigma2"},
                get=lambda pid: {"name": "Living room"} if pid == "box1" else None,
                playlists=lambda: [])
            self.settings = _S(mv_stop_docked="true")
            self._failover_source = None
            self.cast = SimpleNamespace(active=None)
            self._cast_ctx = None
            self.pool = object()
            self._playing_key = "k" if player_url else None
            self._last_playback = {"title": "Now"}
            self.asked: list[tuple[str, str]] = []
            self.answer = 1
            self.status: list[str] = []
            self.phone: list[str] = []

        def _choice_dialog(self, title, message, options, dont_ask_setting=None):
            self.asked.append((title, message))
            return self.answer

        def channel_display_name(self, it):
            return it.get("name") or "Ch"

        def playback_mode(self):
            return "embedded"

        def _item_key(self, it):
            return it.get("stream_id")

        def _set_status(self, text, *a, **k):
            self.status.append(text)

        def _phone_note(self, text):
            self.phone.append(text)
    return W()


@pytest.fixture
def no_async(monkeypatch):
    """The box asked off the window's thread: here, nothing runs unless the
    test runs it."""
    from dopeiptv.ui import mw_connections
    started: list[tuple] = []
    monkeypatch.setattr(mw_connections, "run_async",
                        lambda pool, fn, done, failed, *args: started.append((fn, done, args)))
    return started


ONE_TUNER_TV = BoxState(1, Tune("tv", "SVT1 HD", "3FB:1:C00000"))


def test_the_docked_player_is_asked_before_it_cuts_off_the_tv(no_async):
    w = window(BoxClient())
    w._tuner_cache().put("box1", ONE_TUNER_TV)
    assert not w._guard_stream_switch(BOX + ref(0x802), "Eurosport")
    title, text = w.asked[-1]
    assert title == tr("tuner_title") and tr("tuner_what") in text
    assert tr("tuner_full", n=1) in text
    assert tr("tuner_cuts", what=tr("tuner_w_tv", name="SVT1 HD")) in text
    # The phone's page hears it too, with where to answer.
    assert w.phone and tr("phone_answer_there") in w.phone[-1]
    w.answer = 0                                              # Go ahead anyway
    assert w._guard_stream_switch(BOX + ref(0x802), "Eurosport")
    # The same transponder as the TV, an IPTV address, another source: no question.
    w.asked.clear()
    assert w._guard_stream_switch(BOX + "1:0:19:2:3FB:1:C00000:0:0:0:", "SVT2")
    assert w._guard_stream_switch("http://iptv.example/live/a.ts", "Web")
    assert w._guard_stream_switch("http://h.example.com/live/u/p/1.ts", "Xtream")
    assert not w.asked


def test_the_players_own_tuner_is_let_go_on_a_zap(no_async):
    # The player holds the box's one tuner: zapping lets it go first.
    w = window(BoxClient(), player_url=BOX + ref(0x3FB))
    w._tuner_cache().put("box1", BoxState(1))
    assert w._guard_stream_switch(BOX + ref(0x802), "Eurosport") and not w.asked
    # A multiview cell next to it (the player kept) would need a second one.
    w.settings["mv_stop_docked"] = "false"
    assert not w._conn_ok_multiview(None, BOX + ref(0x802))
    assert tr("tuner_w_own", what=tr("conn_k_player")) in w.asked[-1][1]


def test_a_multiview_cell_and_a_recording_get_the_same_question(no_async):
    rec = Tune("recording", "Rapport", "3FB:1:C00000")
    w = window(BoxClient())
    w._tuner_cache().put("box1", BoxState(1, None, (rec,)))
    said = tr("tuner_cuts", what=tr("tuner_w_rec", name="Rapport"))
    assert not w._conn_ok_multiview(None, BOX + ref(0x802))
    assert said in w.asked[-1][1] and tr("tuner_what") in w.asked[-1][1]
    w._record_now({"stream_id": 0x802, "name": "Eurosport"}, 30)
    assert said in w.asked[-1][1] and tr("tuner_what_rec") in w.asked[-1][1]
    assert not w.rec.added, "cancelled: not recorded"
    w.answer = 0
    w._record_now({"stream_id": 0x802, "name": "Eurosport"}, 30)
    assert w.rec.added == [BOX + ref(0x802)]
    # A cast of it too.
    w.answer = 1
    assert not w._conn_ok_cast(BOX + ref(0x802)) and said in w.asked[-1][1]


def test_a_recording_the_app_makes_from_the_box_is_counted(no_async):
    job = {"status": "recording", "url": BOX + ref(0x3FB), "source": "box1"}
    w = window(BoxClient(), jobs=[job])
    w._tuner_cache().put("box1", BoxState(1))
    assert not w._conn_ok_multiview(None, BOX + ref(0x802))
    assert tr("tuner_w_own", what=tr("conn_k_recording")) in w.asked[-1][1]
    v = w._tuner_verdict(BOX + ref(0x802), w._conn_uses())
    assert v is not None and v.cuts_recording
    # The recording's own channel, or one beside it on its transponder: free.
    assert w._tuner_verdict(BOX + "1:0:19:7:3FB:1:C00000:0:0:0:", w._conn_uses()) is None


def test_a_box_that_does_not_answer_never_blocks_playback(monkeypatch):
    from dopeiptv.ui import mw_connections
    threads: list[threading.Thread] = []
    came: list = []

    def run_async(pool, fn, done, failed, *args):
        t = threading.Thread(target=lambda: came.append((done, fn(*args))), name="pool")
        threads.append(t)
        t.start()
    monkeypatch.setattr(mw_connections, "run_async", run_async)
    lines: list[str] = []
    handler = logging.Handler(logging.DEBUG)
    handler.emit = lambda record: lines.append(record.getMessage())
    from dopeiptv.core.log import log
    level = log.level
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        client = BoxClient(slow=1.0, fail=True)
        w = window(client)
        t0 = time.monotonic()
        assert w._guard_stream_switch(BOX + ref(0x802), "Eurosport")
        assert w._conn_ok_multiview(None, BOX + ref(0x803))
        assert time.monotonic() - t0 < 0.5, "the window never waits on the box"
        assert not w.asked
        for t in threads:
            t.join(5)
        assert client.asked_on == ["pool"], "asked once, off the window's thread"
        for done, result in came:
            done(result)
        assert w._tuner_cache().did_not_answer("box1")
        assert any("did not answer" in x for x in lines), lines
        # Not asked again at once; still nothing in the way.
        assert w._guard_stream_switch(BOX + ref(0x802), "Eurosport") and not w.asked
        assert len(threads) == 1
    finally:
        log.removeHandler(handler)
        log.setLevel(level)


def test_the_lists_ring_marks_what_would_cut_something_off(no_async):
    w = window(BoxClient())
    assert not w.tuner_mark({"stream_id": 0x802}, "live"), "nothing known yet"
    assert no_async, "asked for the marks"
    w._tuner_cache().put("box1", ONE_TUNER_TV)
    assert w.tuner_mark({"stream_id": 0x802}, "live")
    assert not w.tuner_mark({"stream_id": 0x3FB}, "live"), "the TV's transponder"
    assert not w.tuner_mark({"stream_id": 0x802}, "vod")
    # Not a box: never.
    other = window(SimpleNamespace(max_connections=None))
    assert not other.tuner_mark({"stream_id": 1}, "live")


def test_the_in_use_cards_text(no_async):
    from dopeiptv.core.connections import Ledger
    rec = Tune("recording", "Rapport", "3FB:1:C00000")
    client = BoxClient()
    w = window(client, player_url=BOX + ref(0x802))
    w._tuner_cache().put("box1", BoxState(2, Tune("tv", "SVT1 HD", "3FB:1:C00000"), (rec,)))
    lines = w._in_use_lines()
    texts = [t for _k, t in lines]
    assert (tr("inuse_source", name="Living room", count=tr("conn_in_use_only", n=1))
            + " " + tr("conn_used_by", who=tr("conn_k_player"))) in texts
    assert tr("inuse_box_tuners", name="Living room", n=2, total=2) in texts
    assert "• " + tr("tuner_w_tv", name="SVT1 HD") + ", " + tr("tuner_w_rec", name="Rapport") in texts
    assert "• " + tr("tuner_w_own", what=tr("conn_k_player")) in texts
    assert lines[-1] == ("note", tr("inuse_mark_note"))
    # An Xtream account's limit and another device on it.
    xw = window(SimpleNamespace(max_connections=2), player_url="http://h.example.com/live/u/p/1.ts")
    led = xw._conn_ledger()
    assert isinstance(led, Ledger)
    led.report("box1", 2, 2, 1)
    texts = [t for _k, t in xw._in_use_lines()]
    assert texts == [tr("inuse_source", name="Living room", count=tr("conn_in_use", n=2, max=2))
                     + " " + tr("conn_used_by", who=tr("conn_k_player") + ", " + tr("conn_k_other"))]
    # A box that did not answer, or does not say its tuners.
    w._tuner_cache().failed("box1")
    w._tuner_cache().forget("box1")
    w._tuner_cache().failed("box1")
    assert ("head", tr("inuse_box_silent", name="Living room")) in w._in_use_lines()
    w._tuner_cache().put("box1", BoxState(None))
    assert ("head", tr("inuse_box_unknown", name="Living room")) in w._in_use_lines()
    # Nothing at all.
    idle = window(SimpleNamespace(max_connections=None))
    assert idle._in_use_lines() == [("item", tr("inuse_none"))]
