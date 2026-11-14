"""A Stalker / Ministra portal's archive (catch-up), against a fake portal:
channels flagged with an archive, the guide's programmes by day, and
create_link with type=tv_archive - in StalkerClient, then in the window,
which asks for the link off its thread and plays it as any archive."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from dopeiptv.core._lazy_requests import requests
from dopeiptv.providers.boxes import BoxClient
from dopeiptv.providers.client import (
    DemoClient, M3UClient, OfflineClient, XtreamClient, b64, epg_times,
)
from dopeiptv.providers.stalker import (
    StalkerClient, archive_address, archive_cmds, pick_programme,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TS = (Path(__file__).parent / "data" / "tiny.ts").read_bytes()
MAC = "00:1A:79:AB:CD:EF"
NOW = int(time.time())
# Hour-long programmes on the half hour, from 60 hours back to 6 ahead: one
# spans every UTC midnight.
FIRST = NOW // 3600 * 3600 + 1800 - 60 * 3600


def utc(stamp: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(stamp))


def day_of(stamp: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(stamp))


CHANNELS = [
    # Ministra's own list: "archive" and "enable_tv_archive", hours.
    {"id": "11", "name": "Archive One", "number": "1", "tv_genre_id": "1",
     "cmd": "ffmpeg http://localhost/ch/11_", "archive": 1,
     "enable_tv_archive": 1, "tv_archive_duration": 168},
    {"id": "12", "name": "Day And A Half", "number": "2", "tv_genre_id": "1",
     "cmd": "ffmpeg http://localhost/ch/12_", "enable_tv_archive": "1",
     "tv_archive_duration": "36"},
    # Flagged, with no depth and no guide.
    {"id": "13", "name": "No Guide", "number": "3", "tv_genre_id": "1",
     "cmd": "ffmpeg http://localhost/ch/13_", "tv_archive": "1"},
    # A depth but no flag: Ministra fills the column in for every channel.
    {"id": "14", "name": "Live Only", "number": "4", "tv_genre_id": "1",
     "cmd": "ffmpeg http://localhost/ch/14_", "archive": 0,
     "enable_tv_archive": "0", "tv_archive_duration": "168"},
]


def programmes(ch: str) -> list[dict]:
    if ch not in ("11", "12"):
        return []
    out = []
    for i in range(66):
        start = FIRST + i * 3600
        stop = start + 3600
        out.append({"id": f"{ch}{i:03d}", "ch_id": ch, "name": f"Show {ch}-{i}",
                    "descr": "", "time": utc(start), "time_to": utc(stop),
                    "duration": 3600, "real_id": f"{ch}_{start}",
                    "start_timestamp": start, "stop_timestamp": stop,
                    "mark_archive": 1 if stop <= NOW else 0})
    return out


def prog_at(ch: str, stamp: float) -> dict:
    return next(p for p in programmes(ch)
                if p["start_timestamp"] <= stamp < p["stop_timestamp"])


class ArchivePortal(ThreadingHTTPServer):
    """Handshake and profile, the lineup, the guide by day (dated in UTC,
    or *tz* hours east of it), create_link for the archive - keyed by the
    programme's id, its real_id or a cmd of its own - and the archive's
    streams. Records every portal call."""

    daemon_threads = True
    API = "/stalker_portal/server/load.php"

    def __init__(self, **opts) -> None:
        self.opts = opts
        self.calls: list[dict] = []
        self.tokens: set[str] = set()
        self.served: list[str] = []
        self.links = 0
        self.lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.serve_forever,
                         kwargs={"poll_interval": 0.05}, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def of(self, action: str, kind: str | None = None) -> list[dict]:
        with self.lock:
            return [q for q in self.calls if q.get("action") == action
                    and kind in (None, q.get("type"))]

    def handle_error(self, request, client_address) -> None:
        pass            # a player that hangs up early is no error

    def close(self) -> None:
        self.shutdown()
        self.server_close()


class _Handler(BaseHTTPRequestHandler):
    server: ArchivePortal

    def log_message(self, *a) -> None:
        pass

    def _send(self, code: int, body: bytes, ctype: str = "text/html") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def _js(self, js) -> None:
        self._send(200, json.dumps({"js": js}).encode(), "application/json")

    def do_GET(self) -> None:
        srv = self.server
        parts = urlsplit(self.path)
        if parts.path.startswith(("/archive/", "/live/")):
            with srv.lock:
                srv.served.append(parts.path)
            return self._send(200, TS, "video/mp2t")
        if parts.path != srv.API:
            return self._send(404, b"not here")
        q = {k: v[0] for k, v in
             parse_qs(parts.query, keep_blank_values=True).items()}
        with srv.lock:
            srv.calls.append(q)
        kind, action = q.get("type"), q.get("action")
        if (kind, action) == ("stb", "handshake"):
            with srv.lock:
                token = f"tok{len(srv.tokens) + 1}"
                srv.tokens.add(token)
            return self._js({"token": token, "random": "r"})
        if self.headers.get("Authorization", "")[7:] not in srv.tokens:
            return self._send(401, b"")
        answer = getattr(self, f"_{kind}_{action}", None)
        if answer is None:
            return self._send(404, b"no such action")
        answer(q)

    def _stb_get_profile(self, q: dict) -> None:
        self._js({"id": "7", "name": "MAG", "status": 0, "blocked": "0"})

    def _account_info_get_main_info(self, q: dict) -> None:
        self._js({})

    def _itv_get_genres(self, q: dict) -> None:
        self._js([{"id": "*", "title": "All"}, {"id": "1", "title": "TV"}])

    def _itv_get_all_channels(self, q: dict) -> None:
        self._js({"total_items": len(CHANNELS), "data": CHANNELS})

    def _vod_get_categories(self, q: dict) -> None:
        self._js([{"id": "*", "title": "All"}])

    def _vod_get_ordered_list(self, q: dict) -> None:
        self._js({"total_items": 0, "max_page_items": 14, "data": []})

    def _itv_create_link(self, q: dict) -> None:
        ch = re.search(r"/ch/(\d+)_", q.get("cmd", ""))
        with self.server.lock:
            self.server.links += 1
            n = self.server.links
        self._js({"id": n, "cmd": f"ffmpeg {self.server.url}/live/"
                  f"{ch.group(1) if ch else 0}.ts?token=L{n}"})

    def _itv_get_short_epg(self, q: dict) -> None:
        self._js([])

    def _epg_get_simple_data_table(self, q: dict) -> None:
        o = self.server.opts
        if o.get("no_table"):
            return self._send(404, b"")
        shift = int(o.get("tz", 0)) * 3600
        rows = [dict(p) for p in programmes(q.get("ch_id", ""))
                if day_of(p["start_timestamp"] + shift) == q.get("date")]
        if o.get("key") == "cmd":
            for p in rows:
                p["cmd"] = f"auto /media/epg{p['id']}.mpg"
        page, per = int(q.get("p") or 1), 4
        self._js({"total_items": len(rows), "max_page_items": per,
                  "cur_page": 0, "selected_item": 0,
                  "data": rows[(page - 1) * per:page * per]})

    def _tv_archive_create_link(self, q: dict) -> None:
        srv = self.server
        o = srv.opts
        if o.get("slow"):
            time.sleep(o["slow"])
        if o.get("refuse"):
            return self._js({"id": 0, "cmd": "", "error": "nothing_to_play"})
        m = re.fullmatch(r"auto /media/(\S+)\.mpg", q.get("cmd", ""))
        name = m.group(1) if m else ""
        key = o.get("key", "id")
        found = None
        for ch in ("11", "12"):
            for p in programmes(ch):
                if name == {"id": p["id"], "real_id": p["real_id"],
                            "cmd": "epg" + p["id"]}[key]:
                    found = p
        if found is None:
            return self._js({"id": 0, "cmd": "", "error": "nothing_to_play"})
        with srv.lock:
            srv.links += 1
            n = srv.links
        tail = " position:0 media_len:3600" if o.get("mag") else ""
        self._js({"id": found["id"], "error": "", "storage_id": 1,
                  "cmd": f"ffmpeg {srv.url}/archive/{found['id']}.ts"
                         f"?token=T{n}{tail}"})


@pytest.fixture
def portal():
    made: list[ArchivePortal] = []

    def make(**opts) -> ArchivePortal:
        p = ArchivePortal(**opts)
        made.append(p)
        return p

    yield make
    for p in made:
        try:
            p.close()
        except Exception:
            pass


def client(p: ArchivePortal) -> StalkerClient:
    c = StalkerClient(p.url + "/stalker_portal/c/", MAC)
    c.live_streams()
    return c


def span(p: dict) -> tuple[int, int]:
    return p["start_timestamp"], p["stop_timestamp"]


# -- the lineup ------------------------------------------------------------------

def test_channels_with_an_archive_say_so_in_whole_days(portal):
    rows = {r["stream_id"]: r for r in client(portal()).live_streams()}
    got = {sid: (r["tv_archive"], r["tv_archive_duration"])
           for sid, r in rows.items()}
    # 168 hours, 36 hours, flagged with no depth, a depth with no flag.
    assert got == {11: (1, 7), 12: (1, 2), 13: (1, 1), 14: (0, 0)}


def test_only_a_portal_plays_its_archive_by_programme():
    assert StalkerClient.archive_by_programme is True
    for cls in (XtreamClient, M3UClient, OfflineClient, DemoClient, BoxClient):
        assert not getattr(cls, "archive_by_programme", False), cls
        assert not hasattr(cls, "archive_link") and not hasattr(cls, "archive_table")


# -- what create_link is asked, and what comes back --------------------------------

def test_the_address_in_an_archive_answer():
    assert archive_address({"cmd": "ffmpeg http://h/a/1.ts?t=2"}) == "http://h/a/1.ts?t=2"
    # A box's player options after the address are the box's.
    assert archive_address({"cmd": "auto http://h/get.php?f=1.mpg&start=0 "
                                   "position:0 media_len:3600"}) == \
        "http://h/get.php?f=1.mpg&start=0"
    assert archive_address("http://h/a b.ts") == "http://h/a%20b.ts"
    assert archive_address({"cmd": "auto /media/1.mpg"}) == ""
    assert archive_address({"cmd": "", "error": "nothing_to_play"}) == ""
    assert archive_address({"cmd": None}) == "" and archive_address(False) == ""


def test_what_create_link_is_asked_for_a_programme():
    assert archive_cmds({"id": "5", "real_id": "11_100"}) == [
        "auto /media/5.mpg", "auto /media/11_100.mpg"]
    assert archive_cmds({"id": 5, "real_id": "", "cmd": "auto /media/x5.mpg"}) == [
        "auto /media/x5.mpg", "auto /media/5.mpg"]
    assert archive_cmds({"id": "5", "real_id": "5"}) == ["auto /media/5.mpg"]
    assert archive_cmds({}) == []


def test_which_programme_a_moment_or_a_guide_programme_is():
    rows = [{"start": 100, "stop": 200, "row": "a"}, {"start": 200, "stop": 300, "row": "b"}]
    assert pick_programme(rows, 150)["row"] == "a"
    assert pick_programme(rows, 200)["row"] == "b"
    assert pick_programme(rows, 300) is None and pick_programme(rows, 50) is None
    # A guide two minutes late or early: still the programme it overlaps most.
    assert pick_programme(rows, 102, 202)["row"] == "a"
    assert pick_programme(rows, 198, 298)["row"] == "b"
    assert pick_programme(rows, 400, 500) is None


def test_a_programme_plays_through_tv_archive_create_link(portal):
    p = portal()
    c = client(p)
    prog = prog_at("11", NOW - 3 * 3600)
    start, stop = span(prog)
    got = c.archive_link(11, start, stop)
    assert got is not None
    assert got["url"].startswith(f"{p.url}/archive/{prog['id']}.ts?token=T"), got
    assert (got["start"], got["stop"], got["offset"]) == (start, stop, 0.0)
    assert got["title"] == prog["name"] and got["id"] == prog["id"]
    [q] = p.of("create_link", "tv_archive")
    assert q["cmd"] == f"auto /media/{prog['id']}.mpg"
    assert {t["date"] for t in p.of("get_simple_data_table")} == {day_of(start)}
    # Each play asks for a link of its own: a portal's token is often good once.
    again = c.archive_link(11, start, stop)
    assert again["url"] != got["url"] and len(p.of("create_link")) == 2
    # The day's guide was read once.
    assert len({(t["date"], t["p"]) for t in p.of("get_simple_data_table")}) == \
        len(p.of("get_simple_data_table"))


def test_a_moment_inside_a_programme_is_an_offset_into_it(portal):
    c = client(portal())
    prog = prog_at("12", NOW - 5 * 3600)
    got = c.archive_link(12, prog["start_timestamp"] + 754)
    assert got["id"] == prog["id"] and got["offset"] == 754.0


def test_a_guide_a_few_minutes_off_finds_the_same_programme(portal):
    c = client(portal())
    prog = prog_at("11", NOW - 7 * 3600)
    start, stop = span(prog)
    late = c.archive_link(11, start + 180, stop + 180)
    early = c.archive_link(11, start - 180, stop - 180)
    assert late["id"] == early["id"] == prog["id"]
    # Played from the moment the guide says, inside the portal's programme.
    assert (late["offset"], early["offset"]) == (180.0, 0.0)


@pytest.mark.parametrize("key", ["real_id", "cmd"])
def test_portals_that_key_their_archive_otherwise(portal, key):
    p = portal(key=key)
    c = client(p)
    prog = prog_at("11", NOW - 4 * 3600)
    got = c.archive_link(11, *span(prog))
    assert got is not None and f"/archive/{prog['id']}.ts" in got["url"]
    asked = [q["cmd"] for q in p.of("create_link", "tv_archive")]
    if key == "real_id":
        assert asked == [f"auto /media/{prog['id']}.mpg",
                         f"auto /media/{prog['real_id']}.mpg"]
    else:
        assert asked == [f"auto /media/epg{prog['id']}.mpg"]


def test_the_players_options_after_the_address_are_left_out(portal):
    c = client(portal(mag=True))
    got = c.archive_link(11, NOW - 2 * 3600)
    assert got["url"].endswith(tuple(f"token=T{n}" for n in range(1, 9))), got
    assert " " not in got["url"]


def test_a_portal_that_dates_its_days_in_its_own_time_zone(portal):
    # A programme from before midnight UTC lies under the next day there.
    p = portal(tz=3)
    c = client(p)
    midnight = NOW // 86400 * 86400 - 86400
    prog = prog_at("11", midnight - 1000)
    assert day_of(prog["start_timestamp"]) != day_of(prog["start_timestamp"] + 3 * 3600)
    got = c.archive_link(11, midnight - 1000)
    assert got is not None and got["id"] == prog["id"]


def test_a_programme_that_began_the_day_before(portal):
    c = client(portal())
    midnight = NOW // 86400 * 86400 - 86400
    prog = prog_at("11", midnight + 600)       # 23:30 - 00:30 UTC
    assert day_of(prog["start_timestamp"]) != day_of(midnight + 600)
    got = c.archive_link(11, midnight + 600)
    assert got["id"] == prog["id"] and got["offset"] == float(midnight + 600 - prog["start_timestamp"])


def test_a_refused_programme_is_no_link(portal):
    p = portal(refuse=True)
    c = client(p)
    assert c.archive_link(11, *span(prog_at("11", NOW - 3 * 3600))) is None
    # Asked by its id, then by its real_id: nothing either way.
    assert len(p.of("create_link", "tv_archive")) == 2


@pytest.mark.parametrize("opts", [{}, {"no_table": True}])
def test_no_programme_then_is_no_link_and_nothing_is_asked(portal, opts):
    p = portal(**opts)
    c = client(p)
    assert c.archive_link(13, NOW - 3600) is None
    if opts:
        assert c.archive_link(11, NOW - 3600) is None
    assert p.of("create_link", "tv_archive") == []


def test_a_portal_that_does_not_answer_raises(portal):
    p = portal()
    c = client(p)
    t = NOW - 3 * 3600
    assert c.archive_link(11, t)
    p.close()
    # The guide is kept; the link is not to be had.
    with pytest.raises(requests.ConnectionError):
        c.archive_link(11, t)
    # Nor is a day of the guide not read before.
    c.NET_COOLDOWN_SECS = 0
    c._net_down_until = 0.0
    with pytest.raises(requests.ConnectionError):
        c.archive_link(11, NOW - 40 * 3600)


def test_create_link_keeps_to_its_time_budget(portal, monkeypatch):
    monkeypatch.setattr(StalkerClient, "ARCHIVE_BUDGET_SECS", 1.0)
    c = client(portal(slow=3))
    t0 = time.monotonic()
    with pytest.raises(requests.Timeout):
        c.archive_link(11, NOW - 3 * 3600)
    assert time.monotonic() - t0 < 2.5


# -- the archive's list ------------------------------------------------------------

def test_the_archive_list_reads_the_days_back_and_keeps_the_archived(portal):
    p = portal()
    c = client(p)
    got = c.archive_table(11, 2)
    dates = sorted({q["date"] for q in p.of("get_simple_data_table")})
    assert dates == sorted({day_of(NOW - d * 86400) for d in range(3)})
    starts = [int(epg_times(e)[0].timestamp()) for e in got]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)
    # Only what the portal marks as in the archive: nothing still to come.
    assert all(int(e["stop_timestamp"]) <= NOW for e in got)
    want = [q for q in programmes("11") if q["mark_archive"]
            and day_of(q["start_timestamp"]) in dates]
    assert [b64(e["title"]) for e in got] == [q["name"] for q in want]
    # Read back no further than the archive list goes.
    p.calls.clear()
    c.archive_table(12, 30)
    assert len({q["date"] for q in p.of("get_simple_data_table")}) == \
        StalkerClient.ARCHIVE_TABLE_DAYS + 1


def test_the_archive_list_without_a_guide_table_is_the_short_one(portal):
    p = portal(no_table=True)
    c = client(p)
    assert c.archive_table(11, 3) == []
    assert p.of("get_short_epg")


# -- the window ---------------------------------------------------------------------

_CHILD = r"""
import os, sys, json, tempfile, time, threading
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import make_client
from dopeiptv.ui import main_window as mw
from tests.test_stalker_archive import MAC, NOW, ArchivePortal, prog_at, programmes

portal = ArchivePortal()


def external(*_a, **_k):
    raise AssertionError("an external player was started")


mw.launch_player = external
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
s.setValue("playlists", json.dumps([{"id": "p", "name": "Portal", "kind": "stalker",
                                     "server": portal.url + "/stalker_portal/c/",
                                     "mac": MAC}]))
s.setValue("active_playlist", "p")
store = PlaylistStore(s)
client = make_client(store.active())
w = mw.MainWindow(client, s, store)
if w.player is None:
    print("ARCHIVE_SKIP: no embedded player here", flush=True)
    os._exit(0)
# The player itself is not under test: what it is asked to play, and from where.
played = []


def play(url, title, start=0.0, **k):
    played.append((url, start))
    w.player.current_url = url
    return True


w.player.play = play
w.player.isVisible = lambda: True
w.wake.acquire = w.wake.release = lambda *a, **k: None
# Where the portal is asked from: never the window's thread.
on_gui = []
real_link = client.archive_link


def archive_link(*a, **k):
    on_gui.append(threading.current_thread() is threading.main_thread())
    return real_link(*a, **k)


client.archive_link = archive_link


def until(done, secs=20.0):
    end = time.monotonic() + secs
    while time.monotonic() < end and not done():
        app.processEvents()
        w.pool.waitForDone(10)
        time.sleep(0.01)
    return done()


def status():
    return w.count_lbl.text()


chans = {r["stream_id"]: r for r in client.live_streams(None)}
it = chans[11]
assert (w._timeshift_days(it), w._timeshift_days(chans[14])) == (7, 0)

# A programme picked in the guide: the portal's link for it, played from its start.
prog = prog_at("11", NOW - 3 * 3600)
pick = {"start_timestamp": prog["start_timestamp"], "stop_timestamp": prog["stop_timestamp"],
        "title": prog["name"]}
w._play_timeshift(it, prog=pick)
assert until(lambda: len(played) == 1), (played, status())
url, start = played[-1]
assert url.startswith(portal.url + "/archive/" + prog["id"] + ".ts?token="), url
assert start == 0.0, start
assert on_gui == [False], on_gui
[q] = portal.of("create_link", "tv_archive")
assert q["cmd"] == "auto /media/%s.mpg" % prog["id"], q
assert w._playing_catchup and w._ts_from_portal and w._ts_catchup_program
assert w._ts_segment_start == prog["start_timestamp"] == w._ts_program_start
assert w.player._seek_mode == "program", w.player._seek_mode
assert w._last_playback["kind"] == "live" and w._last_playback["item"] is it

# A real mpv plays the portal's link.
try:
    import mpv
    m = mpv.MPV(vo="null", ao="null", terminal=False, keep_open="yes")
except Exception as e:
    print("MPV_SKIPPED", e)
    m = None
if m is not None:
    from tests.test_nerd import _plays
    assert _plays(m, url), url
    m.terminate()
    print("MPV_PLAYED")

# Not seekable is no reason to leave a portal's archive: it is the portal's own link.
w._verify_catchup(w._catchup_verify_token, recheck=True)
assert w._playing_catchup and len(played) == 1

# Scrubbing the programme's bar: a new link, played that far in.
w._seek_program(600)
assert until(lambda: len(played) == 2), played
assert played[-1][1] == 600.0 and played[-1][0] != url, played[-1]
assert w._ts_segment_start == prog["start_timestamp"] == w._ts_program_start
assert w.player._seek_mode == "program" and w.player._program_base == 0.0

# Going back three hours: the programme on then, from that moment in it.
back = prog_at("11", time.time() - 3 * 3600)
w._play_timeshift(it, back_min=180)
assert until(lambda: len(played) == 3), played
want = time.time() - 3 * 3600 - back["start_timestamp"]
assert abs(played[-1][1] - want) < 5, (played[-1], want)
assert "/archive/%s.ts" % back["id"] in played[-1][0]
assert not w._ts_catchup_program and w.player._seek_mode == "timeline"
assert w._ts_segment_start == back["start_timestamp"]

# Its end: on with the next programme, from its start.
nxt = prog_at("11", back["stop_timestamp"])
w._ts_candidate_started -= 60
w._on_player_finished()
assert until(lambda: len(played) == 4), played
assert "/archive/%s.ts" % nxt["id"] in played[-1][0] and played[-1][1] == 0.0, played[-1]
assert w._playing_catchup and w._ts_segment_start == nxt["start_timestamp"]

# Cut off ten minutes in: on from there, in the same programme.
w._ts_candidate_started -= 60
w.player.playback_position = lambda: 600.0
w._on_player_finished()
assert until(lambda: len(played) == 5), played
assert "/archive/%s.ts" % nxt["id"] in played[-1][0] and played[-1][1] == 600.0, played[-1]
del w.player.playback_position

# A programme picked plays to its end, then live - as on every source.
w._play_timeshift(it, prog=pick)
assert until(lambda: len(played) == 6), played
w._ts_candidate_started -= 60
w._on_player_finished()
assert until(lambda: len(played) == 7), played
assert played[-1][0].startswith(portal.url + "/live/11.ts"), played[-1]
assert not w._playing_catchup and not w._ts_from_portal

# The portal refuses: a note, live plays on, its timeline as it was.
portal.opts["refuse"] = True
w._play_timeshift(it, prog=pick)
assert until(lambda: status() == tr("ts_archive_unavailable")), status()
w.pool.waitForDone(2000)
app.processEvents()
assert len(played) == 7 and not w._playing_catchup
assert w.player._seek_mode == "timeline"
portal.opts["refuse"] = False

# No programme in the guide then: the same note, and no link asked for.
links = len(portal.of("create_link", "tv_archive"))
w.play_live_channel(chans[13])
assert until(lambda: len(played) == 8), played
w._flash_status("")
w._play_timeshift(chans[13], back_min=60)
assert until(lambda: status() == tr("ts_archive_unavailable")), status()
assert len(portal.of("create_link", "tv_archive")) == links and len(played) == 8

# The portal is not reached: that says nothing about the archive.
portal.close()
w._flash_status("")
w._play_timeshift(it, back_min=600)
assert until(lambda: status() == tr("ts_check_failed")), status()
assert len(played) == 8 and not w._playing_catchup
assert str(w._item_key(it)) not in w._ts_broken_set()
assert all(g is False for g in on_gui), on_gui
print("ARCHIVE_OK", flush=True)
# Out before Qt's teardown, which the GL widget can crash offscreen.
os._exit(0)
"""


def _child_env(tmp_path) -> dict:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               NO_PROXY="127.0.0.1,localhost,::1", no_proxy="127.0.0.1,localhost,::1")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    # Nothing of the user's: the app's own folders go to the test's.
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        env[var] = str(tmp_path / var.lower())
    return env


def test_the_window_plays_a_portal_archive_programme(tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT,
                         env=_child_env(tmp_path), capture_output=True, text=True,
                         timeout=240)
    if "ARCHIVE_SKIP" in out.stdout:
        pytest.skip(out.stdout.strip())
    assert "ARCHIVE_OK" in out.stdout, out.stdout + out.stderr[-4000:]
    if "MPV_SKIPPED" in out.stdout:
        pytest.skip("libmpv not available: the archive was asked for, not played")
    assert "MPV_PLAYED" in out.stdout
