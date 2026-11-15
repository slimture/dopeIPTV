"""VDR, DVBViewer Media Server, NextPVR and MythTV against fake servers that
answer as their documentation says (restfulapi's and streamdev's JSON and
M3U, the DMS's XML, NextPVR's session API, MythTV's Services API): logins
and refused logins, channels with groups, logos and stable ids, the guide
as XMLTV the guide reads, recordings as films, play addresses with their
login or session, the notes for no guide and no live TV, recording on the
server, Find on my network telling each by its answer - and the real window
listing a VDR's channels and playing one."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from dopeiptv.i18n import LocalizedError, tr
from dopeiptv.providers import lan_scan, vdr
from dopeiptv.providers.boxes import RECORDINGS
from dopeiptv.providers.client import make_client
from dopeiptv.providers.dvbviewer import DVBViewerClient, duration_secs, local_time, ole_day
from dopeiptv.providers.epg import XmltvGuide
from dopeiptv.providers.mythtv import MythTVClient, iso, unix
from dopeiptv.providers.nextpvr import NextPVRClient, login_digest
from dopeiptv.providers.vdr import VdrClient, parse_m3u

NOW = int(time.time())
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# -- a fake server ------------------------------------------------------------------

class Fake:
    """routes: path or (method, path) -> body (str/bytes/dict/list) or
    callable(query, form) -> body or (status, body). auth: None or
    ("basic"|"digest", user, password, realm). Every request is kept in
    *calls* as (method, path, query, form)."""

    def __init__(self, routes: dict, auth: tuple | None = None) -> None:
        self.routes, self.auth, self.calls = routes, auth, []
        srv = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self._answer("GET")

            def do_POST(self):
                self._answer("POST")

            def _answer(self, method: str) -> None:
                u = urlsplit(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
                size = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(size).decode("utf-8") if size else ""
                form = {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}
                srv.calls.append((method, u.path, q, form))
                if srv.auth and not srv.authorized(self.headers.get("Authorization", ""), method):
                    kind, _u, _p, realm = srv.auth
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", f'Digest realm="{realm}", nonce="n0nce", qop="auth"'
                                     if kind == "digest" else f'Basic realm="{realm}"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                route = srv.routes.get((method, u.path), srv.routes.get(u.path))
                status, body = 200, route
                if callable(route):
                    body = route(q, form)
                    if isinstance(body, tuple):
                        status, body = body
                if route is None:
                    status, body = 404, "not found"
                if isinstance(body, (dict, list)):
                    data, ctype = json.dumps(body).encode(), "application/json"
                elif isinstance(body, bytes):
                    data, ctype = body, "video/mp2t" if body[:1] == b"G" else "text/xml"
                else:
                    data = str(body).encode("utf-8")
                    ctype = "audio/x-mpegurl" if data.startswith(b"#EXTM3U") else "text/xml; charset=UTF-8"
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def authorized(self, header: str, method: str) -> bool:
        kind, user, pw, realm = self.auth
        if kind == "basic":
            return header == "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
        if not header.startswith("Digest "):
            return False
        f = dict(re.findall(r'(\w+)="?([^",]*)"?', header[7:]))
        ha1 = hashlib.md5(f"{user}:{realm}:{pw}".encode()).hexdigest()
        ha2 = hashlib.md5(f"{method}:{f.get('uri')}".encode()).hexdigest()
        want = hashlib.md5(f"{ha1}:n0nce:{f.get('nc')}:{f.get('cnonce')}:auth:{ha2}".encode()).hexdigest()
        return f.get("username") == user and f.get("response") == want

    def hits(self, path: str, method: str = "GET") -> list[tuple[dict, dict]]:
        return [(q, form) for m, p, q, form in self.calls if p == path and m == method]

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def fake():
    made: list[Fake] = []

    def make(routes, auth=None):
        f = Fake(routes, auth)
        made.append(f)
        return f
    yield make
    for f in made:
        f.close()


def read_guide(client):
    """The guide as the app reads it, from the client's own XMLTV."""
    g = XmltvGuide(client)
    g._parse(g._download())
    g._loaded = True
    return g


# Every port the scan asks, closed (port 1), unless a test opens one.
CLOSED = dict.fromkeys(lan_scan.PORTS, 1)


# -- VDR -------------------------------------------------------------------------------

ARD, ZDF, B3 = "S19.2E-1-1019-10301", "S19.2E-1-1011-11110", "S19.2E-1-1093-28006"
VDR_CHANNELS = [
    {"channel_id": ZDF, "name": "ZDF HD", "number": 2, "group": "Öffentlich-Rechtliche", "image": False,
     "is_radio": False, "transponder": 11953},
    {"channel_id": ARD, "name": "Das Erste HD", "number": 1, "group": "Öffentlich-Rechtliche", "image": True,
     "is_radio": False, "transponder": 11494},
    {"channel_id": B3, "name": "\x86Bayern 3\x87", "number": 3, "group": "Radio", "image": False, "is_radio": True},
]


def vdr_routes(plugins=("restfulapi", "streamdev-server")) -> dict:
    def channels(q, _f):
        start, limit = int(q.get("start", 0)), int(q.get("limit", 10 ** 6))
        rows = VDR_CHANNELS[start:start + limit]
        return {"channels": rows, "count": len(rows), "total": len(VDR_CHANNELS)}

    def events(cid):
        def answer(q, _f):
            assert int(q["timespan"]) >= 3 * 86400 and int(q["from"]) < NOW
            if cid != ARD:
                return {"events": [], "count": 0, "total": 0}
            return {"events": [
                {"id": 1, "title": "Tagesschau", "short_text": "", "description": "Nachrichten\x8amit Wetter",
                 "start_time": NOW - 600, "duration": 900, "channel": ARD, "contents": ["News/Current Affairs"]},
                {"id": 2, "title": "Tatort", "short_text": "Der Fall", "description": "", "start_time": NOW + 300,
                 "duration": 5400, "channel": ARD, "contents": []}], "count": 2, "total": 2}
        return answer

    return {
        "/info.json": {"version": "0.2.6.5", "channel": ARD, "eventid": 1, "start_time": NOW - 600,
                       "duration": 900, "title": "Tagesschau",
                       "diskusage": {"free_mb": 123456, "used_percent": 41},
                       "vdr": {"version": "2.6.4", "plugins": [{"name": p, "version": "1"} for p in plugins]}},
        "/channels.json": channels,
        f"/events/{ARD}.json": events(ARD), f"/events/{ZDF}.json": events(ZDF), f"/events/{B3}.json": events(B3),
        "/recordings.json": {"recordings": [
            {"number": 0, "name": "Krimi~Tatort", "file_name": "/video/Tatort/2026-01-01.20.15.1-0.rec",
             "relative_file_name": "Tatort/2026-01-01.20.15.1-0.rec", "inode": "2049:131074", "duration": 5400,
             "channel_id": ARD, "event_title": "Tatort", "event_short_text": "Der Fall",
             "event_description": "Ein Krimi.", "event_start_time": NOW - 86400, "event_duration": 5400},
            {"number": 1, "name": "Ohne Inode", "file_name": "/video/x.rec", "duration": 60}],
            "count": 2, "total": 2},
        ("POST", "/timers"): lambda q, f: "",
    }


STREAMDEV_M3U = ("#EXTM3U\n#EXTINF:-1,1 Das Erste HD\nhttp://10.9.9.9:3000/TS/1\n"
                 '#EXTINF:-1 group-title="Radio",3 Bayern 3\nhttp://10.9.9.9:3000/TS/3\n'
                 f"#EXTINF:-1,ZDF HD\nhttp://10.9.9.9:3000/TS/{ZDF}\n")


def test_vdr_restfulapi_channels_groups_logos_and_streamdev_streams(fake, monkeypatch):
    monkeypatch.setattr(vdr, "PAGE", 2)              # the list comes in pages
    sd = fake({"/channels.m3u": STREAMDEV_M3U})
    box = fake(vdr_routes())
    c = VdrClient(box.base, options={"streamdev_port": str(sd.port)})
    c.authenticate()
    assert c.mode == "restfulapi" and c.box_info["name"] == "VDR 2.6.4"
    assert c.live_note == "" and c.guide_note == ""
    assert [q["start"] for q, _f in box.hits("/channels.json")] == ["0", "2"]
    assert [x["category_name"] for x in c.live_categories()] == ["Öffentlich-Rechtliche", "Radio"]
    rows = c.live_streams()
    assert [r["name"] for r in rows] == ["Das Erste HD", "ZDF HD", "Bayern 3"]      # by number, marks gone
    ard = rows[0]
    assert ard["num"] == 1 and ard["epg_channel_id"] == ARD
    assert ard["stream_icon"] == f"{box.base}/channels/image/{ARD}" and rows[1]["stream_icon"] == ""
    assert c.live_url(ard["stream_id"]) == f"http://127.0.0.1:{sd.port}/TS/{ARD}"
    # The same ids however the VDR orders its channels.
    VDR_CHANNELS.reverse()
    try:
        again = VdrClient(box.base, options={"streamdev_port": str(sd.port)})
        again.authenticate()
        assert {r["name"]: r["stream_id"] for r in again.live_streams()} == {r["name"]: r["stream_id"] for r in rows}
    finally:
        VDR_CHANNELS.reverse()
    no_radio = VdrClient(box.base, options={"radio": False})
    no_radio.authenticate()
    assert [r["name"] for r in no_radio.live_streams()] == ["Das Erste HD", "ZDF HD"]


def test_vdr_guide_recordings_and_timer(fake):
    box = fake(vdr_routes())
    c = VdrClient(box.base, options={"streamdev_port": "3999"})
    c.authenticate()
    g = read_guide(c)
    ard = next(r for r in c.live_streams() if r["name"] == "Das Erste HD")
    now = g.current_programme(ard)
    assert now and now["title"] == "Tagesschau" and now["description"] == "Nachrichten\nmit Wetter"
    assert [e["title"] for e in g._entries_for(ard)] == ["Tagesschau", "Tatort"]
    assert len(box.hits(f"/events/{ARD}.json")) == 1
    # Recordings: films that play through streamdev, by device:inode.
    assert c.vod_categories() == [{"category_id": RECORDINGS, "category_name": tr("box_recordings")}]
    films = c.vod_streams()
    assert [f["name"] for f in films] == ["Tatort - Der Fall"]                 # none without an inode
    info = c.vod_info(films[0]["stream_id"])["info"]
    assert info["plot"] == "Ein Krimi." and info["duration_secs"] == 5400 and info["genre"] == "Das Erste HD"
    assert c.vod_url(films[0]["stream_id"]) == "http://127.0.0.1:3999/TS/2049:131074.rec"
    # A timer on the VDR, in its local time.
    start, stop = NOW + 3600, NOW + 3600 + 5400
    assert c.add_timer(ard["stream_id"], start, stop, "Tatort: Der Fall") == (True, "")
    _q, form = box.hits("/timers", "POST")[-1]
    a, b = datetime.fromtimestamp(start), datetime.fromtimestamp(stop)
    assert form == {"flags": "1", "file": "Tatort| Der Fall", "weekdays": "-------", "channel": ARD,
                    "day": a.strftime("%Y-%m-%d"), "start": a.strftime("%H%M"), "stop": b.strftime("%H%M"),
                    "priority": "50", "lifetime": "99", "aux": ""}


def test_vdr_with_only_streamdev_has_channels_and_says_there_is_no_guide(fake):
    sd = fake({"/channels.m3u": STREAMDEV_M3U,
               "/recordings.m3u": "#EXTM3U\n#EXTINF:-1,Tatort\nhttp://10.9.9.9:3000/TS/2049:131074.rec\n"})
    c = VdrClient(sd.base)
    c.authenticate()
    assert c.mode == "streamdev" and c.guide_note == tr("vdr_streamdev_only")
    rows = {r["name"]: r for r in c.live_streams()}
    assert set(rows) == {"Das Erste HD", "Bayern 3", "ZDF HD"}
    assert rows["Das Erste HD"]["num"] == 1 and rows["Das Erste HD"]["category_name"] == tr("box_channels")
    assert rows["Bayern 3"]["category_name"] == "Radio"
    # On the address that answered, not the one the VDR names itself by.
    assert c.live_url(rows["Das Erste HD"]["stream_id"]) == f"{sd.base}/TS/1"
    assert c.live_url(rows["ZDF HD"]["stream_id"]) == f"{sd.base}/TS/{ZDF}"
    assert c.vod_url(c.vod_streams()[0]["stream_id"]) == f"{sd.base}/TS/2049:131074.rec"
    assert c.own_guide() == b""
    assert not c.add_timer(rows["ZDF HD"]["stream_id"], NOW + 60, NOW + 120, "x")[0]


def test_vdr_streamdev_at_its_port_and_no_streamdev_said(fake):
    sd = fake({"/channels.m3u": STREAMDEV_M3U})
    rest = fake({})                       # restfulapi is not there
    c = VdrClient(rest.base, options={"streamdev_port": str(sd.port)})
    c.authenticate()
    assert c.mode == "streamdev" and c.live_url(c.live_streams()[0]["stream_id"]).startswith(sd.base)
    box = fake(vdr_routes(plugins=("restfulapi", "epgsearch")))
    c = VdrClient(box.base)
    c.authenticate()
    assert c.live_note == tr("vdr_no_streamdev")
    with pytest.raises(LocalizedError) as e:
        VdrClient(fake({}).base).authenticate()
    assert e.value.key == "err_box_not_found"


def test_vdr_login_refused_then_carried_to_streams(fake):
    box = fake(vdr_routes(), auth=("basic", "vdr", "s3cret", "VDR"))
    for user, pw, key in (("", "", "err_box_login_needed"), ("vdr", "nope", "err_wrong_login")):
        with pytest.raises(LocalizedError) as e:
            VdrClient(box.base, user, pw).authenticate()
        assert e.value.key == key
    c = VdrClient(box.base, "vdr", "s3cret", {"streamdev_port": "3000"})
    c.authenticate()
    assert c.live_url(c.live_streams()[0]["stream_id"]) == f"http://vdr:s3cret@127.0.0.1:3000/TS/{ARD}"


def test_streamdev_m3u_parser():
    got = parse_m3u('#EXTM3U\n#EXTGRP:News\n#EXTINF:-1 tvg-id="ard.de" tvg-logo="http://l/a.png",ARD\n'
                    "http://h/TS/1\n#EXTINF:0,Radio, with a comma\nhttp://h/TS/2\n")
    assert got[0] == {"name": "ARD", "group": "News", "logo": "http://l/a.png", "tvg_id": "ard.de",
                      "url": "http://h/TS/1"}
    assert got[1]["name"] == "Radio, with a comma" and got[1]["group"] == "News"


# -- DVBViewer Media Server -------------------------------------------------------------

DVB_IDS = {"ard": "2305843009213693952", "wdr": "2305843009213693953", "zdf": "2305843009213693954",
           "kabel": "2305843009213693955"}
DVB_START = "20260101201500"


def dvb_routes(port_holder: list) -> dict:
    def epg(q, _f):
        assert q["lvl"] == "2" and float(q["start"]) < float(q["end"])
        if q["channel"] != "281479":
            return '<?xml version="1.0" encoding="utf-8" ?><epg Ver="1"></epg>'
        a = datetime.fromtimestamp(NOW - 600).strftime("%Y%m%d%H%M%S")
        b = datetime.fromtimestamp(NOW + 600).strftime("%Y%m%d%H%M%S")
        return ('<?xml version="1.0" encoding="utf-8" ?><epg Ver="1">'
                f'<programme start="{a}" stop="{b}" channel="281479"><eventid>9</eventid>'
                "<titles><title>Tagesschau</title></titles><events><event>Mit Wetter</event></events>"
                "<descriptions><description>Nachrichten.</description></descriptions></programme></epg>")

    return {
        "/api/version.html": '<?xml version="1.0" encoding="utf-8" ?><version iver="33620224">'
                             "DVBViewer Media Server 3.2.0.0</version>",
        "/api/getchannelsxml.html": (
            '<?xml version="1.0" encoding="utf-8" ?><channels>'
            '<root name="Astra 19.2E"><group name="ARD">'
            f'<channel nr="1" name="Das Erste HD" EPGID="281479" ID="{DVB_IDS["ard"]}" flags="24">'
            "<logo>Logos/Das Erste HD.png</logo></channel>"
            f'<channel nr="2" name="WDR HD Köln" EPGID="281480" ID="{DVB_IDS["wdr"]}"/></group>'
            f'<group name="ZDF"><channel nr="3" name="ZDF HD" EPGID="281481" ID="{DVB_IDS["zdf"]}"/></group></root>'
            '<root name="DVB-C"><group name="ARD">'
            f'<channel nr="4" name="Das Erste (Kabel)" EPGID="281482" ID="{DVB_IDS["kabel"]}"/></group></root>'
            "</channels>"),
        "/api/getfavourites.html": (
            '<?xml version="1.0" encoding="utf-8" ?><favourites>'
            f'<section caption="News"><entry nr="0" ID="{DVB_IDS["zdf"]}">ZDF HD</entry></section>'
            f'<entry>{DVB_IDS["ard"]}|Das Erste HD</entry></favourites>'),
        "/api/epg.html": epg,
        "/api/recordings.html": lambda q, _f: (
            '<?xml version="1.0" encoding="utf-8" ?><recordings Ver="1">'
            f"<imageURL>http://127.0.0.1:{port_holder[0]}/thumbnails/video/</imageURL>"
            f'<recording id="17" start="{DVB_START}" duration="013000" charset="255">'
            "<channel>Das Erste HD</channel><title>Tatort</title><info>Der Fall</info><desc>Ein Krimi.</desc>"
            "<image>17.jpg</image></recording></recordings>"),
        "/api/timeradd.html": "",
    }


def test_dvbviewer_basic_login_channels_groups_favourites(fake):
    holder: list = []
    box = fake(dvb_routes(holder), auth=("basic", "admin", "pa ss", "DVBViewer Recording Service"))
    holder.append(box.port)
    with pytest.raises(LocalizedError) as e:
        DVBViewerClient(box.base, "admin", "nope").authenticate()
    assert e.value.key == "err_wrong_login"
    c = DVBViewerClient(box.base, "admin", "pa ss", {"stream_port": "7600"})
    c.authenticate()
    assert c.box_info == {"kind": "dvbviewer", "name": "DVBViewer Media Server 3.2.0.0", "version": "33620224"}
    assert [x["category_name"] for x in c.live_categories()] == [
        "News", tr("box_favorites"), "Astra 19.2E · ARD", "Astra 19.2E · ZDF", "DVB-C · ARD"]
    assert [r["name"] for r in c.live_streams("News")] == ["ZDF HD"]
    assert [r["name"] for r in c.live_streams(tr("box_favorites"))] == ["Das Erste HD"]
    ard = c.live_streams("Astra 19.2E · ARD")[0]
    assert ard["num"] == 1 and ard["epg_channel_id"] == "281479"
    assert ard["stream_icon"] == f"http://admin:pa%20ss@127.0.0.1:{box.port}/Logos/Das Erste HD.png"
    assert c.live_url(ard["stream_id"]) == "http://admin:pa%20ss@127.0.0.1:7600/upnp/channelstream/1.ts"
    assert len(c.live_streams(None)) == 4                         # each channel once
    plain = DVBViewerClient(box.base, "admin", "pa ss", {"favourites": False})
    plain.authenticate()
    assert "News" not in [x["category_name"] for x in plain.live_categories()]
    assert plain.live_url(ard["stream_id"]).endswith(":7522/upnp/channelstream/1.ts")


def test_dvbviewer_guide_recordings_and_timer(fake):
    holder: list = []
    box = fake(dvb_routes(holder))
    holder.append(box.port)
    c = DVBViewerClient(box.base)
    c.authenticate()
    g = read_guide(c)
    ard = c.live_streams("Astra 19.2E · ARD")[0]
    now = g.current_programme(ard)
    assert now and now["title"] == "Tagesschau" and now["description"] == "Nachrichten."
    assert len(box.hits("/api/epg.html")) == 4                    # one question per channel
    film = c.vod_streams()[0]
    assert film["name"] == "Tatort - Der Fall" and film["stream_icon"] == f"{box.base}/thumbnails/video/17.jpg"
    info = c.vod_info(film["stream_id"])["info"]
    assert info["duration_secs"] == 5400 and info["plot"] == "Ein Krimi." and info["genre"] == "Das Erste HD"
    assert film["added"] == str(local_time(DVB_START))
    assert c.vod_url(film["stream_id"]) == "http://127.0.0.1:7522/upnp/recordings/17.ts"
    start, stop = NOW + 7200, NOW + 7200 + 2700
    assert c.add_timer(ard["stream_id"], start, stop, "Tatort")[0]
    q, _f = box.hits("/api/timeradd.html")[-1]
    a, b = datetime.fromtimestamp(start), datetime.fromtimestamp(stop)
    assert q == {"ch": DVB_IDS["ard"], "dor": str(int(ole_day(start))), "enable": "1",
                 "start": str(a.hour * 60 + a.minute), "stop": str(b.hour * 60 + b.minute), "prio": "50",
                 "title": "Tatort", "encoding": "255"}


def test_dvbviewer_dates_and_not_a_dms(fake):
    assert duration_secs("013000") == 5400 and duration_secs("01:30:00") == 5400 and duration_secs("90") == 90
    stamp = local_time("20260101201500")
    assert datetime.fromtimestamp(stamp) == datetime(2026, 1, 1, 20, 15)
    assert local_time("01.01.2026 20:15:00") == stamp and local_time("garbage") == 0
    # Day 46023 is 2026-01-01 in Delphi's count.
    assert int(ole_day(stamp)) == 46023
    with pytest.raises(LocalizedError) as e:
        DVBViewerClient(fake({"/api/version.html": "<html>Channels DVR</html>"}).base).authenticate()
    assert e.value.key == "err_box_not_found"


# -- NextPVR --------------------------------------------------------------------------------

class NextPVR:
    """NextPVR's /service as its session API answers: a session only after
    the login with the PIN's MD5."""

    def __init__(self, pin: str = "1234") -> None:
        self.pin, self.salts, self.good, self.n = pin, {}, set(), 0
        self.saved: list[dict] = []

    @staticmethod
    def ok(body: str) -> str:
        return f'<?xml version="1.0" encoding="utf-8" ?><rsp stat="ok">{body}</rsp>'

    def __call__(self, q, _f):
        m = q.get("method")
        if m == "session.initiate":
            assert q["ver"] == "1.0" and q["device"]
            self.n += 1
            sid = f"sid-{self.n}"
            self.salts[sid] = f"salt{self.n}"
            return self.ok(f"<sid>{sid}</sid><salt>{self.salts[sid]}</salt>")
        if m == "session.login":
            want = hashlib.md5((":" + hashlib.md5(self.pin.encode()).hexdigest() + ":"
                                + self.salts.get(q["sid"], "")).encode()).hexdigest()
            if q.get("md5") != want:
                return '<rsp stat="fail"><err code="2" msg="Login failed"/></rsp>'
            self.good.add(q["sid"])
            return self.ok("")
        if q.get("sid") not in self.good:
            return '<rsp stat="fail"><err code="8" msg="Unknown session"/></rsp>'
        if m == "setting.version":
            return self.ok("<version>50201</version><readableVersion>5.2.1</readableVersion>")
        if m == "setting.list":
            return self.ok("<PrePadding>2</PrePadding><PostPadding>5</PostPadding>")
        if m == "channel.groups":
            return self.ok("<groups><group><name>All Channels</name></group><group><name>News</name></group></groups>")
        if m == "channel.list":
            chans = [("7179", "Seven", "7", "0x1", True), ("8200", "SBS", "3", "0x1", False),
                     ("9001", "Triple J", "200", "0xa", False)]
            if q.get("group_id") == "News":
                chans = chans[1:2]
            return self.ok("<channels>" + "".join(
                f"<channel><id>{i}</id><name>{n}</name><number>{num}</number><minor>0</minor><type>{t}</type>"
                + ("<icon>true</icon>" if icon else "") + "</channel>"
                for i, n, num, t, icon in chans) + "</channels>")
        if m == "channel.listings":
            if q["channel_id"] != "7179":
                return self.ok("<listings></listings>")
            return self.ok(f"<listings><l><id>1</id><name>News</name><description>Today.</description>"
                           f"<subtitle>Evening</subtitle><genre>News</genre><start>{(NOW - 600) * 1000}</start>"
                           f"<end>{(NOW + 600) * 1000}</end></l></listings>")
        if m == "recording.list":
            assert q["filter"] == "ready"
            return self.ok(
                "<recordings><recording><id>55</id><name>Doctor Who</name><subtitle>Blink</subtitle>"
                f"<desc>Don't blink.</desc><channel>Seven</channel><start_time_ticks>{NOW - 86400}</start_time_ticks>"
                "<duration_seconds>2700</duration_seconds><status>ready</status></recording>"
                "<recording><id>56</id><name>Failed</name><status>failed</status></recording></recordings>")
        if m == "recording.save":
            self.saved.append(dict(q))
            return self.ok("<recording_id>77</recording_id>")
        return '<rsp stat="fail"><err code="1" msg="Unknown method"/></rsp>'


def test_nextpvr_pin_login_session_channels_and_groups(fake):
    api = NextPVR()
    srv = fake({"/service": api})
    with pytest.raises(LocalizedError) as e:
        NextPVRClient(srv.base, password="0000").authenticate()
    assert e.value.key == "err_wrong_pin"
    c = NextPVRClient(srv.base, password="1234")
    c.authenticate()
    assert c.box_info["name"] == "NextPVR 5.2.1" and c.sid in api.good
    login = [q for q, _f in srv.hits("/service") if q["method"] == "session.login"][-1]
    assert login["sid"] == c.sid and login["md5"] == login_digest("1234", api.salts[c.sid])
    assert [x["category_name"] for x in c.live_categories()] == [tr("box_channels"), "News"]
    rows = c.live_streams(tr("box_channels"))
    assert [r["name"] for r in rows] == ["SBS", "Seven", "Triple J"]                # by number
    seven = rows[1]
    assert seven["num"] == 7 and seven["epg_channel_id"] == "7179"
    assert seven["stream_icon"] == f"{srv.base}/service?method=channel.icon&channel_id=7179&sid={c.sid}"
    assert rows[0]["stream_icon"] == ""
    assert c.live_url(seven["stream_id"]) == f"{srv.base}/live?channeloid=7179&client=dopeIPTV&sid={c.sid}"
    assert [r["name"] for r in c.live_streams("News")] == ["SBS"]
    no_radio = NextPVRClient(srv.base, password="1234", options={"radio": False})
    no_radio.authenticate()
    assert "Triple J" not in [r["name"] for r in no_radio.live_streams()]
    # NextPVR's own default PIN when none is typed.
    default = fake({"/service": NextPVR(pin="0000")})
    NextPVRClient(default.base).authenticate()


def test_nextpvr_guide_recordings_timer_and_a_lost_session(fake):
    api = NextPVR()
    srv = fake({"/service": api})
    c = NextPVRClient(srv.base, password="1234")
    c.authenticate()
    g = read_guide(c)
    seven = next(r for r in c.live_streams() if r["name"] == "Seven")
    now = g.current_programme(seven)                       # milliseconds read as seconds
    assert now and now["title"] == "News" and now["description"] == "Today."
    films = c.vod_streams()
    assert [f["name"] for f in films] == ["Doctor Who - Blink"]                  # the failed one left out
    info = c.vod_info(films[0]["stream_id"])["info"]
    assert info["duration_secs"] == 2700 and info["plot"] == "Don't blink." and info["genre"] == "Seven"
    assert c.vod_url(films[0]["stream_id"]) == f"{srv.base}/live?recording=55&client=dopeIPTV&sid={c.sid}"
    # The server restarted: the session is gone, a new login gives a new one.
    old = c.sid
    api.good.clear()
    assert c.add_timer(seven["stream_id"], NOW + 600, NOW + 4200, "Doctor Who") == (True, "77")
    assert c.sid != old and c.live_url(seven["stream_id"]).endswith(f"sid={c.sid}")
    saved = api.saved[-1]
    assert (saved["name"], saved["channel"], saved["time_t"], saved["duration"]) == (
        "Doctor Who", "7179", str(NOW + 600), "3600")
    assert (saved["pre_padding"], saved["post_padding"]) == ("2", "5")      # the server's own
    with pytest.raises(LocalizedError) as e:
        NextPVRClient(fake({"/service": "<html/>"}).base).authenticate()
    assert e.value.key == "err_box_not_found"


# -- MythTV -----------------------------------------------------------------------------------

MYTH_CHANNELS = [
    {"ChanId": "1001", "ChanNum": "1", "CallSign": "BBC1", "ChannelName": "BBC One", "Visible": "true",
     "IconURL": "/Guide/GetChannelIcon?ChanId=1001", "XMLTVID": "bbc1.uk", "SourceId": "1"},
    {"ChanId": "1002", "ChanNum": "2_1", "CallSign": "BBC2", "ChannelName": "BBC Two", "Visible": True,
     "IconURL": "", "XMLTVID": "", "SourceId": "1"},
    {"ChanId": "1009", "ChanNum": "9", "CallSign": "HID", "ChannelName": "Hidden", "Visible": "false"},
]


def myth_routes(scheduled: list) -> dict:
    def channels(q, _f):
        assert q["OnlyVisible"] == "true"
        rows = MYTH_CHANNELS[1:2] if q.get("ChannelGroupID") == "3" else MYTH_CHANNELS
        return {"ChannelInfoList": {"StartIndex": "0", "Count": str(len(rows)), "ChannelInfos": rows}}

    def guide(q, _f):
        a, b = unix(q["StartTime"]), unix(q["EndTime"])
        progs = [p for p in ({"StartTime": iso(NOW - 600), "EndTime": iso(NOW + 600), "Title": "EastEnders",
                              "SubTitle": "Episode 1", "Description": "Drama.", "Category": "Soap"},
                             {"StartTime": iso(NOW + 600), "EndTime": iso(NOW + 2400), "Title": "News",
                              "SubTitle": "", "Description": "", "Category": "News"})
                 if a <= unix(p["StartTime"]) < b]
        return {"ProgramGuide": {"Channels": [{"ChanId": "1001", "ChanNum": "1", "CallSign": "BBC1",
                                               "Programs": progs},
                                              {"ChanId": "9999", "Programs": progs}]}}

    def recorded(q, _f):
        assert q["Descending"] == "true"
        rec = {"Title": "Doctor Who", "SubTitle": "Blink", "Description": "Don't blink.",
               "StartTime": iso(NOW - 86400), "EndTime": iso(NOW - 86400 + 2700), "FileName": "1001_2026.ts",
               "Channel": {"ChanId": "1001", "ChannelName": "BBC One", "CallSign": "BBC1"},
               "Recording": {"RecordedId": "42", "RecGroup": "Default", "StartTs": iso(NOW - 86460),
                             "EndTs": iso(NOW - 86400 + 2760)}}
        live = dict(rec, Title="Live buffer", Recording=dict(rec["Recording"], RecordedId="43", RecGroup="LiveTV"))
        gone = dict(rec, Title="Gone", Recording=dict(rec["Recording"], RecordedId="44", RecGroup="Deleted"))
        return {"ProgramList": {"Programs": [rec, live, gone]}}

    def add(_q, form):
        scheduled.append(form)
        return {"uint": "57"}

    return {
        "/Myth/GetHostName": {"String": "mythbox"},
        "/Myth/GetBackendInfo": {"BackendInfo": {"Build": {"Version": "v34.0"}}},
        "/Channel/GetChannelInfoList": channels,
        "/Guide/GetChannelGroupList": {"ChannelGroupList": {"ChannelGroups": [
            {"GroupId": "3", "Name": "Favorites", "Password": ""}]}},
        "/Guide/GetProgramGuide": guide,
        "/Dvr/GetRecordedList": recorded,
        ("POST", "/Dvr/AddRecordSchedule"): add,
    }


def test_mythtv_channels_for_the_guide_without_live_tv(fake):
    srv = fake(myth_routes([]), auth=("digest", "myth", "mythtv", "MythTV"))
    with pytest.raises(LocalizedError) as e:
        MythTVClient(srv.base, "myth", "wrong").authenticate()
    assert e.value.key == "err_wrong_login"
    c = MythTVClient(srv.base, "myth", "mythtv")
    c.authenticate()
    assert c.box_info["name"] == "MythTV v34.0 (mythbox)"
    assert [x["category_name"] for x in c.live_categories()] == [tr("box_channels"), "Favorites"]
    rows = c.live_streams(tr("box_channels"))
    assert [r["name"] for r in rows] == ["BBC One", "BBC Two"]                    # the hidden one left out
    one = rows[0]
    assert one["num"] == 1 and rows[1]["num"] == 2 and one["epg_channel_id"] == "1001"
    assert one["stream_icon"] == f"http://myth:mythtv@127.0.0.1:{srv.port}/Guide/GetChannelIcon?ChanId=1001"
    # No live TV: listed for the guide, no address, and why said.
    assert one["_no_live"] and c.live_url(one["stream_id"]) == ""
    assert c.live_note == tr("myth_no_live")
    assert [r["name"] for r in c.live_streams("Favorites")] == ["BBC Two"]


def test_mythtv_guide_recordings_and_a_scheduled_recording(fake):
    scheduled: list = []
    srv = fake(myth_routes(scheduled))
    c = MythTVClient(srv.base)
    c.authenticate()
    g = read_guide(c)
    one = next(r for r in c.live_streams() if r["name"] == "BBC One")
    now = g.current_programme(one)
    assert now and now["title"] == "EastEnders" and now["description"] == "Drama."
    assert [e["title"] for e in g._entries_for(one)] == ["EastEnders", "News"]
    assert len(srv.hits("/Guide/GetProgramGuide")) == 7                       # half a day each
    films = c.vod_streams()
    assert [f["name"] for f in films] == ["Doctor Who - Blink"]              # LiveTV and Deleted left out
    film = films[0]
    assert film["stream_icon"] == f"{srv.base}/Content/GetPreviewImage?RecordedId=42"
    assert c.vod_url(film["stream_id"]) == f"{srv.base}/Content/GetRecording?RecordedId=42"
    info = c.vod_info(film["stream_id"])["info"]
    assert info["duration_secs"] == 2820 and info["genre"] == "BBC One" and film["added"] == str(NOW - 86460)
    assert c.add_timer(one["stream_id"], NOW + 600, NOW + 2400, "News") == (True, "57")
    assert scheduled[-1] == {"Title": "News", "ChanId": "1001", "Station": "BBC1", "StartTime": iso(NOW + 600),
                             "EndTime": iso(NOW + 2400), "Type": "Single Record"}
    assert unix("2026-01-01T20:15:00Z") == int(datetime(2026, 1, 1, 20, 15).replace(
        tzinfo=__import__("datetime").timezone.utc).timestamp())


def test_a_channel_without_an_address_is_said_not_shared(fake):
    from dopeiptv.services.share import Share
    from dopeiptv.ui.mw_box import _BoxMixin
    from dopeiptv.ui.mw_media import _MediaServerMixin
    srv = fake(myth_routes([]))
    c = MythTVClient(srv.base)
    c.authenticate()

    class W(_BoxMixin, _MediaServerMixin):
        client = c
        status = toast = ""

        def _show_toast(self, text, ms=0):
            self.toast = text

        def _set_status(self, text):
            self.status = text
    w = W()
    one = c.live_streams()[0]
    assert w._box_no_live(one) and w.toast == tr("myth_no_live")
    assert not w._box_no_live({"stream_id": 1})
    w._ms_live_note(c.live_categories())
    assert w.status == tr("myth_no_live")
    # The shared playlist leaves out what has no address.
    pl = {"id": "m", "kind": "mythtv", "server": srv.base}
    rows, _cats = Share(lambda: [pl], make_client=lambda _pl: c).channels(pl)
    assert rows == []


# -- make_client and Find on my network ----------------------------------------------------

def test_make_client_builds_each_new_kind():
    for kind, cls, port in (("vdr", VdrClient, 8002), ("dvbviewer", DVBViewerClient, 8089),
                            ("nextpvr", NextPVRClient, 8866), ("mythtv", MythTVClient, 6544)):
        c = make_client({"kind": kind, "server": "10.0.0.7", "options": {"recordings": False}})
        assert isinstance(c, cls) and c.server == f"http://10.0.0.7:{port}"
        assert not c.opt_flag("recordings", True)


def test_find_on_my_network_tells_each_by_its_answer(fake):
    holder: list = []
    rest = fake(vdr_routes())
    dvb = fake(dvb_routes(holder), auth=("basic", "a", "b", "DVBViewer Recording Service"))
    npvr = fake({"/service": NextPVR()})
    myth = fake(myth_routes([]))
    got = lan_scan.probe("127.0.0.1", dict(CLOSED, vdr=rest.port, dvbviewer=dvb.port, nextpvr=npvr.port,
                                           mythtv=myth.port))
    kinds = {d["kind"]: d for d in got}
    assert set(kinds) == {"vdr", "dvbviewer", "nextpvr", "mythtv"}
    assert kinds["vdr"] == {"kind": "vdr", "server": rest.base, "name": "VDR 2.6.4 - 127.0.0.1"}
    assert kinds["dvbviewer"] == {"kind": "dvbviewer", "server": dvb.base, "name": "DVBViewer - 127.0.0.1",
                                  "login": True}
    assert kinds["nextpvr"]["server"] == npvr.base and kinds["nextpvr"]["login"]
    assert kinds["mythtv"]["name"] == "MythTV (mythbox) - 127.0.0.1"
    # streamdev alone, on its port, is a VDR too; a DMS without a login names itself.
    sd = fake({"/channels.m3u": STREAMDEV_M3U})
    holder.clear()
    open_dvb = fake(dvb_routes(holder))
    got = lan_scan.probe("127.0.0.1", dict(CLOSED, streamdev=sd.port, dvbviewer=open_dvb.port))
    assert sorted((d["kind"], d["server"], d["name"]) for d in got) == [
        ("dvbviewer", open_dvb.base, "DVBViewer Media Server 3.2.0.0 - 127.0.0.1"),
        ("vdr", sd.base, "VDR (streamdev) - 127.0.0.1")]
    # Something else on those ports is not taken for one.
    other = fake({"/info.json": {"version": "1"}, "/channels.m3u": "<html/>",
                  "/Myth/GetHostName": {"name": "x"}, "/service": "<rsp stat='ok'/>",
                  "/api/version.html": (401, "")})
    got = lan_scan.probe("127.0.0.1", dict(CLOSED, vdr=other.port, streamdev=other.port, dvbviewer=other.port,
                                           nextpvr=other.port, mythtv=other.port))
    assert got == []


def test_the_sweep_asks_every_port_at_once(fake):
    a, b = fake({}), fake({})
    t0 = time.monotonic()
    up = lan_scan.sweep(["127.0.0.1", "127.0.0.2"], [a.port, b.port, 1], budget=2)
    assert up == {"127.0.0.1": {a.port, b.port}}
    assert time.monotonic() - t0 < 2.0
    # A scan asks only the hosts that answered the sweep.
    got = lan_scan.scan(hosts=["127.0.0.1", "127.0.0.2"], broadcast=False,
                        ports=dict(CLOSED, vdr=fake(vdr_routes()).port))
    assert [d["kind"] for d in got] == ["vdr"]


# -- the real window --------------------------------------------------------------------------

_CHILD = r"""
import faulthandler, json, os, sys, tempfile
faulthandler.dump_traceback_later(150, exit=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from PyQt6.QtTest import QTest
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import make_client
from dopeiptv.ui.main_window import MainWindow
from tests.test_home_backends import ARD, Fake, vdr_routes

sd = Fake({})
routes = vdr_routes()
box = Fake(routes)
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
pl = {"id": "v", "name": "VDR", "kind": "vdr", "server": box.base, "options": {"streamdev_port": str(sd.port)}}
s.setValue("playlists", json.dumps([pl]))
s.setValue("active_playlist", "v")
w = MainWindow(make_client(pl), s, PlaylistStore(s))
played = []
w._start_playback = lambda url, title, icon, key, kind, record=True, item=None, catchup=False, **k: \
    played.append((url, title, kind))


def wait(cond, n=800):
    for _ in range(n):
        app.processEvents()
        w.pool.waitForDone(5)
        if cond():
            return True
        QTest.qWait(5)
    return False


def rows():
    return [w.list_model.item_at(i) for i in range(w.list_model.rowCount())]


w.switch_mode("live")
assert wait(lambda: len([r for r in rows() if r and r.get("stream_id") is not None]) >= 2), rows()
names = [r["name"] for r in rows() if r and r.get("stream_id") is not None]
assert names[:2] == ["Das Erste HD", "ZDF HD"], names
assert {c["category_name"] for c in w.client.live_categories()} == {"Öffentlich-Rechtliche", "Radio"}
ard = next(r for r in rows() if r and r.get("name") == "Das Erste HD")
w.play_item(ard)
assert wait(lambda: played), played
assert played[-1] == (sd.base + "/TS/" + ARD, "Das Erste HD", "live"), played
print("VDR_WINDOW_OK", flush=True)
os._exit(0)
"""


def test_the_window_lists_a_vdrs_channels_and_plays_one(tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        env[var] = str(tmp_path / var.lower())
        os.makedirs(env[var], exist_ok=True)
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "VDR_WINDOW_OK" in out.stdout, out.stdout + out.stderr[-4000:]
