"""Boxes on the home network against fake ones that answer as the real
thing does (the XML and JSON shapes are OpenWebif's, the Dreambox
WebInterface's, Tvheadend's and SiliconDust's own): bouquets and markers,
bouquets in bouquets, IPTV in a bouquet, odd encodings, basic and digest
logins, the guide handed over as XMLTV, recordings as films, the LAN scan
and HDHomeRun's broadcast discovery."""
from __future__ import annotations

import hashlib
import json
import re
import socket
import struct
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlsplit

import pytest

from dopeiptv.core.log import redact
from dopeiptv.i18n import LocalizedError
from dopeiptv.providers import boxes, hdhomerun, lan_scan
from dopeiptv.providers.boxes import RECORDINGS, build_xmltv, normalize_server, parse_xml, with_login
from dopeiptv.providers.client import make_client
from dopeiptv.providers.enigma2 import (
    RADIO_BOUQUETS, TV_BOUQUETS, Enigma2Client, iptv_address, is_marker, picon_name, ref_key,
)
from dopeiptv.providers.epg import XmltvGuide
from dopeiptv.providers.hdhomerun import HDHomeRunClient
from dopeiptv.providers.tvheadend import TvheadendClient

NOW = int(time.time())


# -- a fake box ---------------------------------------------------------------

class FakeBox:
    """routes: path -> body (str/bytes/dict/list) or callable(query) ->
    body or (status, body). auth: None, ("basic"|"digest", user, pw)."""

    def __init__(self, routes: dict, auth: tuple | None = None) -> None:
        self.routes, self.auth, self.calls = routes, auth, []
        box = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                u = urlsplit(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
                box.calls.append((u.path, q))
                if box.auth and not box.authorized(self.headers.get("Authorization", ""), u):
                    self.send_response(401)
                    kind, *_ = box.auth
                    self.send_header("WWW-Authenticate", 'Digest realm="tvheadend", nonce="n0nce", qop="auth"'
                                     if kind == "digest" else 'Basic realm="OpenWebif enigma2"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                route = box.routes.get(u.path)
                status, body = 200, route
                if callable(route):
                    body = route(q)
                    if isinstance(body, tuple):
                        status, body = body
                if route is None:
                    status, body = 404, "not found"
                if isinstance(body, (dict, list)):
                    data, ctype = json.dumps(body).encode(), "application/json"
                elif isinstance(body, bytes):
                    data, ctype = body, "text/xml"
                else:
                    data, ctype = str(body).encode("utf-8"), "text/xml; charset=UTF-8"
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def authorized(self, header: str, u) -> bool:
        kind, user, pw = self.auth
        if kind == "basic":
            import base64
            return header == "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
        if not header.startswith("Digest "):
            return False
        f = dict(re.findall(r'(\w+)="?([^",]*)"?', header[7:]))
        ha1 = hashlib.md5(f"{user}:tvheadend:{pw}".encode()).hexdigest()
        ha2 = hashlib.md5(f"GET:{f.get('uri')}".encode()).hexdigest()
        want = hashlib.md5(f"{ha1}:n0nce:{f.get('nc')}:{f.get('cnonce')}:auth:{ha2}".encode()).hexdigest()
        return f.get("username") == user and f.get("response") == want

    def paths(self, path: str) -> list[dict]:
        return [q for p, q in self.calls if p == path]

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def fake():
    made: list[FakeBox] = []

    def make(routes, auth=None):
        b = FakeBox(routes, auth)
        made.append(b)
        return b
    yield make
    for b in made:
        b.close()


def svc(ref: str, name: str) -> str:
    return (f"<e2service><e2servicereference>{ref}</e2servicereference>"
            f"<e2servicename>{name}</e2servicename></e2service>")


def servicelist(*items: str) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?>\n<e2servicelist>' + "".join(items) + "</e2servicelist>"


def event(ref: str, name: str, start: int, secs: int, title: str, short: str = "", long: str = "") -> str:
    return (f"<e2event><e2eventid>1</e2eventid><e2eventstart>{start}</e2eventstart>"
            f"<e2eventduration>{secs}</e2eventduration><e2eventcurrenttime>{NOW}</e2eventcurrenttime>"
            f"<e2eventtitle>{title}</e2eventtitle><e2eventdescription>{short}</e2eventdescription>"
            f"<e2eventdescriptionextended>{long}</e2eventdescriptionextended>"
            f"<e2eventservicereference>{ref}</e2eventservicereference>"
            f"<e2eventservicename>{name}</e2eventservicename>"
            f'<e2eventgenre id="1">Nyheter</e2eventgenre></e2event>')


SVT1 = "1:0:19:283D:3FB:1:C00000:0:0:0:"
SVT2 = "1:0:19:283E:3FB:1:C00000:0:0:0:"
EURO = "1:0:19:1B1F:802:2:11A0000:0:0:0:"
HIDDEN = "1:512:19:1B20:802:2:11A0000:0:0:0:"
MARKER = "1:64:1:0:0:0:0:0:0:0::--- Sport ---"
IPTV = "4097:0:1:0:0:0:0:0:0:0:http%3a//iptv.example/live/a.ts:Web Kanal"
IPTV2 = "4097:0:1:0:0:0:0:0:0:0:http%3a//iptv.example/live/b.ts:Web Kanal 2"
RADIO = "1:0:2:3:3FB:1:C00000:0:0:0:"
FAV_TV = '1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "userbouquet.favourites.tv" ORDER BY bouquet'
SPORT_TV = '1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "userbouquet.sport.tv" ORDER BY bouquet'
SUB_TV = '1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "subbouquet.nordic.tv" ORDER BY bouquet'
RADIO_B = '1:7:2:0:0:0:0:0:0:0:FROM BOUQUET "userbouquet.radio.radio" ORDER BY bouquet'

ABOUT_OWIF = """<?xml version="1.0" encoding="UTF-8"?>
<e2abouts><e2about><e2oeversion>OE-Alliance 5.3</e2oeversion><e2enigmaversion>2024-01-02</e2enigmaversion>
<e2distroversion>openatv</e2distroversion><e2imageversion>7.3</e2imageversion>
<e2webifversion>OWIF 2.2.1</e2webifversion><e2model>Vu+ Duo 4K</e2model>
<e2tunerinfo><e2nim><name>Tuner A</name><type>DVB-S2X</type><live></live><rec></rec></e2nim></e2tunerinfo>
</e2about></e2abouts>"""

MOVIES = f"""<?xml version="1.0" encoding="UTF-8"?>
<e2movielist>
<e2movie><e2servicereference>1:0:0:0:0:0:0:0:0:0:/media/hdd/movie/20260101 2000 - SVT1 - Rapport.ts</e2servicereference>
<e2title>Rapport</e2title><e2description>Nyheter</e2description><e2descriptionextended>Med sporten.</e2descriptionextended>
<e2servicename>SVT1 HD</e2servicename><e2time>{NOW - 86400}</e2time><e2length>29:30</e2length><e2tags></e2tags>
<e2filename>/media/hdd/movie/20260101 2000 - SVT1 - Rapport.ts</e2filename><e2filesize>1000</e2filesize></e2movie>
<e2movie><e2servicereference>1:0:0:0:0:0:0:0:0:0:/media/hdd/movie/older.ts</e2servicereference>
<e2title>Older</e2title><e2description>None</e2description><e2descriptionextended></e2descriptionextended>
<e2servicename>SVT2</e2servicename><e2time>{NOW - 7 * 86400}</e2time><e2length>?:??</e2length><e2tags></e2tags>
<e2filename>/media/hdd/movie/older.ts</e2filename><e2filesize>10</e2filesize></e2movie>
<e2locations><e2location>/media/hdd/movie/</e2location></e2locations>
</e2movielist>"""


def openwebif_routes() -> dict:
    def allservices(q):
        if "type" in q:
            return ('<e2servicelistrecursive><e2bouquet><e2servicereference>' + RADIO_B.replace('"', "&quot;") +
                    '</e2servicereference><e2servicename>Radio</e2servicename><e2servicelist>' +
                    svc(RADIO, "P1") + "</e2servicelist></e2bouquet></e2servicelistrecursive>")
        return ('<?xml version="1.0" encoding="UTF-8"?>\n<e2servicelistrecursive>'
                '<e2bouquet><e2servicereference>' + FAV_TV.replace('"', "&quot;") + '</e2servicereference>'
                '<e2servicename>Favourites (TV)</e2servicename><e2servicelist>' +
                svc(SVT1, "SVT1 HD") + svc(MARKER, "--- Sport ---") + svc(EURO, "Eurosport") +
                svc(HIDDEN, "Hidden") + svc(IPTV, "Web Kanal") + svc(IPTV2, "Web Kanal 2") +
                '</e2servicelist></e2bouquet>'
                '<e2bouquet><e2servicereference>' + SPORT_TV.replace('"', "&quot;") + '</e2servicereference>'
                '<e2servicename>Sport</e2servicename><e2servicelist>' + svc(EURO, "Eurosport") +
                "</e2servicelist></e2bouquet></e2servicelistrecursive>")

    def epgmulti(q):
        if q.get("bRef") == FAV_TV:
            return ("<e2eventlist>" +
                    event(SVT1, "SVT1 HD", NOW - 1800, 3600, "Rapport", "Nyheter", "Nyheter\x8amed sport") +
                    event(SVT1, "SVT1 HD", NOW + 1800, 1800, "Sporten") +
                    event(EURO, "Eurosport", NOW - 600, 7200, "Cykel") +
                    event(SVT2, "Not in a bouquet", NOW, 600, "Ignored") + "</e2eventlist>")
        return "<e2eventlist>" + event(EURO, "Eurosport", NOW - 600, 7200, "Cykel") + "</e2eventlist>"

    return {"/web/about": ABOUT_OWIF, "/web/getallservices": allservices,
            "/web/epgmulti": epgmulti, "/web/movielist": MOVIES,
            "/web/zap": '<e2simplexmlresult><e2state>True</e2state><e2statetext>Active service is now x</e2statetext></e2simplexmlresult>',
            "/web/powerstate": "<e2powerstate><e2instandby>false</e2instandby></e2powerstate>"}


# -- Enigma2: OpenWebif ---------------------------------------------------------

def test_openwebif_bouquets_markers_hidden_and_iptv(fake):
    box = fake(openwebif_routes())
    c = Enigma2Client(box.base)
    assert c.authenticate() == {"user_info": {"auth": 1}}
    assert c.box_info["openwebif"] and c.box_info["model"] == "Vu+ Duo 4K"
    assert "openatv 7.3" in c.box_info["name"] and c.box_info["tuners"] == ["Tuner A"]
    cats = [x["category_name"] for x in c.live_categories()]
    assert cats == ["Favourites (TV)", "Sport", "Radio (radio)"]
    fav = c.live_streams("Favourites (TV)")
    assert [r["name"] for r in fav] == ["SVT1 HD", "Eurosport", "Web Kanal", "Web Kanal 2"]
    # One channel in two bouquets is one channel: the same id, once in All.
    sport = c.live_streams("Sport")
    assert sport[0]["stream_id"] == fav[1]["stream_id"]
    assert len(c.live_streams()) == 5
    # DVB streams from the box's stream port, the reference as it is.
    host = "127.0.0.1"
    assert c.live_url(fav[0]["stream_id"]) == f"http://{host}:8001/{SVT1}"
    # IPTV in a bouquet plays from its own address.
    assert c.live_url(fav[2]["stream_id"]) == "http://iptv.example/live/a.ts"
    assert c.live_url(fav[3]["stream_id"]) == "http://iptv.example/live/b.ts"
    # Picons on OpenWebif, by the usual file name.
    assert fav[0]["stream_icon"] == f"{box.base}/picon/1_0_19_283D_3FB_1_C00000_0_0_0.png"
    assert fav[2]["stream_icon"].endswith("/picon/1_0_1_0_0_0_0_0_0_0.png")
    # One request for every bouquet (and one for radio).
    assert len(box.paths("/web/getallservices")) == 2 and not box.paths("/web/getservices")


def test_ids_stay_the_same_when_the_box_reorders(fake):
    box = fake(openwebif_routes())
    c = Enigma2Client(box.base)
    c.authenticate()
    before = {r["name"]: r["stream_id"] for r in c.live_streams()}
    routes = openwebif_routes()
    old = routes["/web/getallservices"]
    routes["/web/getallservices"] = lambda q: old(q).replace(svc(SVT1, "SVT1 HD"), "").replace(
        svc(EURO, "Eurosport") + svc(HIDDEN, "Hidden"), svc(HIDDEN, "Hidden") + svc(EURO, "Eurosport") +
        svc(SVT1, "SVT1 HD"))
    box.routes = routes
    c.clear_list_cache()
    after = {r["name"]: r["stream_id"] for r in c.live_streams()}
    assert after == before


def test_openwebif_guide_becomes_xmltv_the_guide_reads(fake):
    box = fake(openwebif_routes())
    c = Enigma2Client(box.base)
    c.authenticate()
    data = c.own_guide()
    assert data.startswith(b"<?xml") and b"Ignored" not in data
    # One request per bouquet, radio too; nothing per channel.
    assert len(box.paths("/web/epgmulti")) == 3 and not box.paths("/web/epgservice")
    g = XmltvGuide(c)
    g._parse(g._download())
    g._loaded = True
    svt1 = next(r for r in c.live_streams() if r["name"] == "SVT1 HD")
    now = g.current_programme(svt1)
    assert now and now["title"] == "Rapport"
    titles = [e["title"] for e in g._entries_for(svt1)]
    assert titles == ["Rapport", "Sporten"]
    # DVB's own line break (0x8A) is a line break.
    assert now["description"] == "Nyheter\nmed sport"


def test_recordings_are_films_that_play_from_the_box(fake):
    box = fake(openwebif_routes())
    c = Enigma2Client(box.base)
    c.authenticate()
    assert c.vod_categories() == [{"category_id": RECORDINGS, "category_name": "Recordings on the box"}]
    films = c.vod_streams(RECORDINGS)
    assert [f["name"] for f in films] == ["Rapport", "Older"]          # newest first
    info = c.vod_info(films[0]["stream_id"])["info"]
    assert info["plot"] == "Nyheter\n\nMed sporten." and info["duration_secs"] == 1770
    assert c.vod_info(films[1]["stream_id"])["info"]["plot"] == ""     # "None" is empty
    url = c.vod_url(films[0]["stream_id"])
    assert url == box.base + "/file?action=download&file=" + quote(
        "/media/hdd/movie/20260101 2000 - SVT1 - Rapport.ts", safe="/")
    assert films[0]["container_extension"] == "ts"


def test_basic_login_masked_and_carried_to_the_stream(fake, caplog):
    box = fake(openwebif_routes(), auth=("basic", "root", "pa:ss w0rd"))
    with pytest.raises(LocalizedError) as e:
        Enigma2Client(box.base).authenticate()
    assert e.value.key == "err_box_login_needed"
    with pytest.raises(LocalizedError) as e:
        Enigma2Client(box.base, "root", "wrong").authenticate()
    assert e.value.key == "err_wrong_login"
    c = Enigma2Client(box.base, "root", "pa:ss w0rd")
    c.authenticate()
    sid = next(r["stream_id"] for r in c.live_streams() if r["name"] == "SVT1 HD")
    url = c.live_url(sid)
    assert url == f"http://root:pa%3Ass%20w0rd@127.0.0.1:8001/{SVT1}"
    assert "pa%3Ass%20w0rd" not in redact(url)
    c2 = Enigma2Client(box.base, "root", "pa:ss w0rd", {"stream_login": False, "stream_port": "17999"})
    c2.authenticate()
    assert c2.live_url(sid) == f"http://127.0.0.1:17999/{SVT1}"


def test_options_turn_parts_off(fake):
    box = fake(openwebif_routes())
    c = Enigma2Client(box.base, options={"radio": False, "recordings": "false", "picons": False,
                                         "iptv_direct": False})
    c.authenticate()
    assert [x["category_name"] for x in c.live_categories()] == ["Favourites (TV)", "Sport"]
    assert c.vod_categories() == [] and not box.paths("/web/movielist")
    fav = c.live_streams("Favourites (TV)")
    assert all(r["stream_icon"] == "" for r in fav)
    assert c.live_url(fav[2]["stream_id"]).startswith("http://127.0.0.1:8001/4097:0:1:")


def test_zap_and_power(fake):
    box = fake(openwebif_routes())
    c = Enigma2Client(box.base)
    c.authenticate()
    sid = next(r["stream_id"] for r in c.live_streams() if r["name"] == "Eurosport")
    assert c.zap(sid) is True and box.paths("/web/zap")[-1]["sRef"] == EURO
    assert c.power(4) is True and box.paths("/web/powerstate")[-1]["newstate"] == "4"
    assert c.zap(12345) is False


# -- Enigma2: the old Dreambox WebInterface -------------------------------------

def dreambox_routes() -> dict:
    latin = ('<?xml version="1.0" encoding="ISO-8859-1"?>\n<e2servicelist>' +
             svc(SVT1, "SVT1 \x86HD\x87") + svc(SUB_TV, "Nordic") + svc(MARKER, "x") +
             "</e2servicelist>").encode("latin-1") + "".encode()
    latin = latin.replace(b"SVT1 ", "Kanal ö ".encode("latin-1") + b"\x01")

    def getservices(q):
        ref = q.get("sRef")
        if ref == TV_BOUQUETS:
            return servicelist(svc(FAV_TV.replace('"', "&quot;"), "Favoriter"))
        if ref == RADIO_BOUQUETS:
            return servicelist()
        if ref == FAV_TV:
            return latin
        if ref == SUB_TV:
            return servicelist(svc(SVT2, "SVT2"))
        return 404, "no"

    def epgservice(q):
        ref = q.get("sRef")
        return "<e2eventlist>" + event(ref, "x", NOW - 60, 600, f"Show on {ref.split(':')[3]}") + "</e2eventlist>"

    return {"/web/about": """<?xml version="1.0" encoding="UTF-8"?>
<e2abouts><e2about><e2enigmaversion>2011-06-01</e2enigmaversion><e2imageversion>Experimental 2011-06-01</e2imageversion>
<e2webifversion>1.7.5</e2webifversion><e2fpversion>None</e2fpversion><e2model>dm800se</e2model></e2about></e2abouts>""",
            "/web/getservices": getservices, "/web/epgservice": epgservice,
            "/web/movielist": "<e2movielist></e2movielist>"}


def test_dreambox_webinterface_one_bouquet_at_a_time(fake):
    box = fake(dreambox_routes())
    c = Enigma2Client(box.base)
    c.authenticate()
    assert not c.box_info["openwebif"] and c.box_info["model"] == "dm800se"
    assert not box.paths("/web/getallservices")
    cats = [x["category_name"] for x in c.live_categories()]
    assert cats == ["Favoriter", "Favoriter / Nordic"]
    names = [r["name"] for r in c.live_streams()]
    # Latin-1 as declared, DVB emphasis marks and a control character gone.
    assert names == ["Kanal ö HD", "SVT2"]
    assert all(r["stream_icon"] == "" for r in c.live_streams())     # no /picon/ there
    assert c.vod_categories() == []
    data = c.own_guide()
    assert not box.paths("/web/epgmulti") and len(box.paths("/web/epgservice")) == 2
    assert b"Show on 283D" in data and b"Show on 283E" in data


def test_not_an_enigma2_box(fake):
    box = fake({"/": "hello"})
    with pytest.raises(LocalizedError) as e:
        Enigma2Client(box.base).authenticate()
    assert e.value.key == "err_box_not_found"
    box2 = fake({"/web/about": "<html><body>router</body></html>"})
    with pytest.raises(LocalizedError) as e:
        Enigma2Client(box2.base).authenticate()
    assert e.value.key == "err_box_not_found"


def test_service_reference_helpers():
    assert is_marker(MARKER) and not is_marker("1:320:1:0:0:0:0:0:0:0::numbered")
    assert iptv_address(IPTV) == "http://iptv.example/live/a.ts" and iptv_address(SVT1) == ""
    assert ref_key(SVT1) == "1:0:19:283D:3FB:1:C00000:0:0:0" and ref_key(IPTV) != ref_key(IPTV2)
    assert ref_key(SVT1.lower()) == ref_key(SVT1)
    assert picon_name(SVT1) == "1_0_19_283D_3FB_1_C00000_0_0_0"
    assert picon_name("4097:0:1:2B66:3F3:1:C00000:0:0:0:http%3a//x") == "1_0_1_2B66_3F3_1_C00000_0_0_0"


# -- Tvheadend -------------------------------------------------------------------

def tvh_routes() -> dict:
    return {
        "/api/serverinfo": {"sw_version": "4.3-2290", "api_version": 19, "name": "Tvheadend",
                            "capabilities": ["caclient", "tvadapters", "satip_client", "timeshift"]},
        "/api/channeltag/grid": {"entries": [
            {"uuid": "t1", "name": "Nyheter", "enabled": True, "internal": False, "index": 2},
            {"uuid": "t2", "name": "TV channels", "enabled": True, "internal": True, "index": 0},
            {"uuid": "t3", "name": "HD", "enabled": True, "internal": False, "index": 1},
            {"uuid": "t4", "name": "Off", "enabled": False, "internal": False, "index": 3}]},
        "/api/channel/grid": lambda q: {"entries": [
            {"uuid": "c2", "name": "SVT2", "number": 2, "enabled": True, "tags": ["t3", "t2"],
             "icon": "https://logos.example/svt2.png", "icon_public_url": "imagecache/2"},
            {"uuid": "c1", "name": "SVT1", "number": 1, "enabled": True, "tags": ["t1", "t3", "t4"],
             "icon": "picon://1_0_19.png", "icon_public_url": "imagecache/1"},
            {"uuid": "c9", "name": "Off air", "number": 9, "enabled": False, "tags": []},
            {"uuid": "c5", "name": "Untagged", "number": 0, "enabled": True, "tags": []},
        ], "total": 4} if q.get("limit") == "999999" else {"entries": [], "total": 4},
        "/api/dvr/entry/grid_finished": {"entries": [
            {"uuid": "r1", "disp_title": "Rapport", "disp_subtitle": "19:30", "disp_description": "Nyheter.",
             "start": NOW - 3600, "stop": NOW - 1800, "start_real": NOW - 3660, "stop_real": NOW - 1740,
             "channelname": "SVT1", "url": "dvrfile/r1", "filename": "/rec/Rapport.mkv", "duration": 0}]},
        "/xmltv/channels": ('<?xml version="1.0" encoding="UTF-8"?><tv><channel id="c1"><display-name>SVT1</display-name>'
                            f'</channel><programme start="{boxes.xmltv_time(NOW - 600)}" stop="{boxes.xmltv_time(NOW + 600)}" '
                            'channel="c1"><title>Rapport</title></programme></tv>'),
    }


def test_tvheadend_digest_login_tags_and_streams(fake):
    box = fake(tvh_routes(), auth=("digest", "kodi", "s3cret"))
    c = TvheadendClient(box.base, "kodi", "s3cret", {"profile": "pass"})
    c.authenticate()
    assert c.box_info["version"] == "4.3-2290" and "satip_client" in c.box_info["capabilities"]
    cats = [x["category_name"] for x in c.live_categories()]
    assert cats == ["All channels", "HD", "Nyheter"]       # tag order, internal and off tags out
    assert [r["name"] for r in c.live_streams("All channels")] == ["SVT1", "SVT2", "Untagged"]
    assert [r["name"] for r in c.live_streams("HD")] == ["SVT1", "SVT2"]
    svt1 = c.live_streams("Nyheter")[0]
    assert svt1["num"] == 1 and svt1["epg_channel_id"] == "c1"
    assert c.live_url(svt1["stream_id"]) == (
        f"http://kodi:s3cret@127.0.0.1:{box.port}/stream/channel/c1?profile=pass")
    # An http icon as it is, a picon:// one through the server's image cache.
    svt2 = c.live_streams("HD")[1]
    assert svt2["stream_icon"] == "https://logos.example/svt2.png"
    assert svt1["stream_icon"] == f"http://kodi:s3cret@127.0.0.1:{box.port}/imagecache/1"
    rec = c.vod_streams()[0]
    assert rec["name"] == "Rapport - 19:30" and rec["container_extension"] == "mkv"
    assert c.vod_info(rec["stream_id"])["info"]["duration_secs"] == 1920
    assert c.vod_url(rec["stream_id"]).endswith(f"127.0.0.1:{box.port}/dvrfile/r1")
    assert b"<programme" in c.own_guide()


def test_tvheadend_wrong_login_and_anonymous(fake):
    box = fake(tvh_routes(), auth=("digest", "kodi", "s3cret"))
    with pytest.raises(LocalizedError) as e:
        TvheadendClient(box.base, "kodi", "nope").authenticate()
    assert e.value.key == "err_wrong_login"
    anon = fake(tvh_routes())
    c = TvheadendClient(anon.base)
    c.authenticate()
    assert c.live_url(c.live_streams()[0]["stream_id"]) == f"{anon.base}/stream/channel/c1"


def test_tvheadend_default_port():
    assert TvheadendClient("192.168.1.5").server == "http://192.168.1.5:9981"
    assert TvheadendClient("https://tv.example/tvh/").server == "https://tv.example/tvh"
    assert Enigma2Client("192.168.1.10").server == "http://192.168.1.10"


# -- HDHomeRun --------------------------------------------------------------------

def hdhr_routes(port_holder: list) -> dict:
    return {
        "/discover.json": lambda q: {"FriendlyName": "HDHomeRun FLEX 4K", "ModelNumber": "HDFX-4K",
                                     "DeviceID": "1053ABCD", "TunerCount": 4, "FirmwareVersion": "20250815",
                                     # what a device behind NAT says: the typed address wins
                                     "LineupURL": "http://10.9.9.9:80/lineup.json?show=found"},
        "/lineup.json": [
            {"GuideNumber": "2.1", "GuideName": "WCBS-HD", "URL": "http://10.0.0.5:5004/auto/v2.1", "HD": 1,
             "Favorite": 1},
            {"GuideNumber": "4.1", "GuideName": "WNBC", "URL": "http://10.0.0.5:5004/auto/v4.1"},
            {"GuideNumber": "7.1", "GuideName": "Locked", "URL": "http://10.0.0.5:5004/auto/v7.1", "DRM": 1},
        ],
    }


def test_hdhomerun_lineup_favourites_drm_and_transcode(fake):
    box = fake(hdhr_routes([]))
    c = HDHomeRunClient(box.base, options={"transcode": "internet720"})
    c.authenticate()
    assert c.box_info["name"] == "HDHomeRun FLEX 4K (HDFX-4K)" and c.box_info["tuners"] == 4
    assert box.paths("/lineup.json") == [{"show": "found"}]
    assert [x["category_name"] for x in c.live_categories()] == ["Favorites", "Channels"]
    rows = c.live_streams("Channels")
    assert [r["name"] for r in rows] == ["WCBS-HD", "WNBC"] and rows[0]["num"] == 2
    assert c.live_url(rows[0]["stream_id"]) == "http://10.0.0.5:5004/auto/v2.1?transcode=internet720"
    assert c.own_guide() == b""          # no DeviceAuth: an emulator's guide is set by hand
    c2 = HDHomeRunClient(box.base, options={"show_drm": True})
    c2.authenticate()
    assert len(c2.live_streams("Channels")) == 3


def test_hdhomerun_cloud_guide(fake, monkeypatch):
    routes = hdhr_routes([])
    routes["/discover.json"] = {"FriendlyName": "HDHomeRun", "DeviceID": "1", "DeviceAuth": "abc123",
                                "LineupURL": "/lineup.json"}
    calls = []

    def guide(q):
        calls.append(q)
        if "Start" in q:
            return []
        return [{"GuideNumber": "2.1", "Guide": [
            {"StartTime": NOW - 60, "EndTime": NOW + 1800, "Title": "News", "EpisodeTitle": "Ep 1",
             "Synopsis": "Local news.", "Filter": ["News"]}]}]
    routes["/api/guide.php"] = guide
    box = fake(routes)
    monkeypatch.setattr(hdhomerun, "GUIDE_API", box.base + "/api/guide.php")
    c = HDHomeRunClient(box.base)
    c.authenticate()
    data = c.own_guide()
    assert calls[0] == {"DeviceAuth": "abc123"} and calls[1]["Start"] == str(NOW + 1800)
    root = parse_xml(data)
    p = root.find("programme")
    assert p.get("channel") == "2.1" and p.findtext("sub-title") == "Ep 1" and p.findtext("category") == "News"
    off = HDHomeRunClient(box.base, options={"cloud_guide": False})
    off.authenticate()
    assert off.own_guide() == b""


def test_discovery_packet_round_trip():
    pkt = hdhomerun.discover_packet()
    assert pkt[:2] == b"\x00\x02" and zlib.crc32(pkt[:-4]) == struct.unpack("<I", pkt[-4:])[0]
    payload = bytes([0x01, 4]) + struct.pack(">I", 1) + bytes([0x02, 4]) + bytes.fromhex("1053ABCD")
    base = b"http://192.168.1.50:80"
    payload += bytes([0x2A, len(base)]) + base
    body = struct.pack(">HH", 3, len(payload)) + payload
    reply = body + struct.pack("<I", zlib.crc32(body))
    got = hdhomerun.parse_reply(reply, "192.168.1.50")
    assert got == {"server": "http://192.168.1.50:80", "kind": "hdhomerun", "device_id": "1053ABCD",
                   "storage": False}
    assert hdhomerun.parse_reply(reply[:-1] + b"\x00", "x") is None      # bad CRC


def test_discovery_over_udp():
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]

    def answer():
        data, addr = srv.recvfrom(2048)
        assert data == hdhomerun.discover_packet()
        payload = bytes([0x01, 4]) + struct.pack(">I", 1) + bytes([0x02, 4]) + b"\x12\x34\x56\x78"
        body = struct.pack(">HH", 3, len(payload)) + payload
        srv.sendto(body + struct.pack("<I", zlib.crc32(body)), addr)
    t = threading.Thread(target=answer, daemon=True)
    t.start()
    got = hdhomerun.discover(timeout=1.0, target=("127.0.0.1", port))
    t.join(2)
    srv.close()
    assert got == [{"server": "http://127.0.0.1", "kind": "hdhomerun", "device_id": "12345678",
                    "storage": False}]


# -- the LAN scan ------------------------------------------------------------------

def test_scan_finds_each_kind(fake):
    e2 = fake(openwebif_routes())
    tvh = fake({"/api/serverinfo": {"sw_version": "4.2.8", "api_version": 15}})
    hd = fake(hdhr_routes([]))
    got = lan_scan.scan(hosts=["127.0.0.1"], broadcast=False,
                        ports={"enigma2": e2.port, "tvheadend": tvh.port, "hdhomerun": hd.port})
    kinds = {d["kind"]: d for d in got}
    assert set(kinds) == {"enigma2", "tvheadend", "hdhomerun"}
    assert kinds["enigma2"]["server"] == e2.base and "Vu+ Duo 4K (openatv 7.3)" in kinds["enigma2"]["name"]
    assert kinds["tvheadend"]["server"] == tvh.base and "4.2.8" in kinds["tvheadend"]["name"]
    assert kinds["hdhomerun"]["name"].startswith("HDHomeRun FLEX 4K (HDFX-4K)")


def test_scan_sees_a_box_that_wants_a_login(fake):
    tvh = fake(tvh_routes(), auth=("digest", "a", "b"))
    got = lan_scan.probe("127.0.0.1", {"enigma2": 1, "tvheadend": tvh.port, "hdhomerun": 1})
    assert got == [{"kind": "tvheadend", "server": tvh.base, "name": "Tvheadend - 127.0.0.1", "login": True}]


def test_nothing_on_a_closed_port():
    assert lan_scan.probe("127.0.0.1", {"enigma2": 1, "tvheadend": 1, "hdhomerun": 1}) == []


def test_local_networks_are_private_slash_24s():
    for net in lan_scan.local_networks():
        assert net.prefixlen == 24 and net.is_private


# -- the shared parts ---------------------------------------------------------------

def test_make_client_builds_each_kind():
    assert isinstance(make_client({"kind": "enigma2", "server": "10.0.0.2"}), Enigma2Client)
    t = make_client({"kind": "tvheadend", "server": "10.0.0.3", "username": "u", "password": "p",
                     "options": {"profile": "matroska"}})
    assert isinstance(t, TvheadendClient) and t.opt("profile") == "matroska"
    assert isinstance(make_client({"kind": "hdhomerun", "server": "10.0.0.4"}), HDHomeRunClient)


def test_xml_that_breaks_the_rules_still_parses():
    raw = ('<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE x [<!ENTITY a "aaaa">]>'
           "<e2servicelist><e2service><e2servicename>Bad\x0b\x1fname</e2servicename></e2service>"
           "</e2servicelist>").encode() + b"\xff"
    root = parse_xml(raw.replace(b"\xff", b""))
    assert boxes.child_text(root.find("e2service"), "e2servicename") == "Badname"
    root = parse_xml(b'<?xml version="1.0" encoding="UTF-8"?><a>caf\xe9</a>')
    assert root.text == "caf�"


def test_xmltv_writer_escapes_and_drops_bad_events():
    data = build_xmltv([("a&b", "A <&> B")], [
        {"channel": "a&b", "start": NOW, "stop": NOW + 60, "title": 'Q&A "live"', "desc": "<b>"},
        {"channel": "a&b", "start": NOW, "stop": NOW + 60, "title": "duplicate"},
        {"channel": "a&b", "start": NOW + 60, "stop": NOW + 60, "title": "no length"},
        {"channel": "a&b", "start": NOW + 120, "stop": NOW + 180, "title": ""}])
    root = parse_xml(data)
    progs = root.findall("programme")
    assert len(progs) == 1 and progs[0].findtext("title") == 'Q&A "live"'
    assert root.find("channel").get("id") == "a&b"


def test_server_and_login_helpers():
    assert normalize_server("box.local", 80) == "http://box.local"
    assert normalize_server("10.0.0.2:8080/", 9981) == "http://10.0.0.2:8080"
    assert normalize_server("http://10.0.0.2", 9981) == "http://10.0.0.2"
    assert with_login("http://h:1/p?q=1", "a b", "c@d") == "http://a%20b:c%40d@h:1/p?q=1"
    assert with_login("http://h/p", "", "x") == "http://h/p"


def test_a_box_that_is_down_keeps_its_channels(fake):
    box = fake(openwebif_routes())
    c = Enigma2Client(box.base)
    c.authenticate()
    n = len(c.live_streams())
    box.routes = {}
    c.clear_list_cache()
    assert len(c.live_streams()) == n


def test_options_survive_export_and_import():
    from dopeiptv.core.stores import PlaylistStore

    class Settings(dict):
        def value(self, k, d=None):
            return self.get(k, d)

        def setValue(self, k, v):
            self[k] = v
    s = PlaylistStore(Settings())
    s.items = [{"id": "x", "name": "Box", "kind": "enigma2", "server": "http://10.0.0.2",
                "username": "", "password": "", "options": {"stream_port": "17999", "radio": False}}]
    data = s.export_data()
    t = PlaylistStore(Settings())
    t.items = []
    assert t.import_data(data) == 1
    assert t.items[0]["kind"] == "enigma2" and t.items[0]["options"] == {"stream_port": "17999", "radio": False}
