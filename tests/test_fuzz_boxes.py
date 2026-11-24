"""Fuzzing the boxes and servers on the home network (tests/fuzz_util.py):
Enigma2 (OpenWebif and the old WebInterface), Tvheadend, HDHomeRun, VDR,
DVBViewer, NextPVR, MythTV, SAT>IP, Jellyfin, Emby and Plex - each served
by a fake that answers as the real one does (the suite's own routes for
them), every answer now and then mutated: XML as a tree and as bytes,
declared encodings that lie, JSON of the wrong shape. A client raises what
its callers catch (it is not the box, the login, a failed request, an
answer that does not parse) and hands on what it promises: rows that are
objects, a guide of its own that is XMLTV the guide reads."""
from __future__ import annotations

import inspect
import json
import random
import xml.etree.ElementTree as ET

import pytest

from dopeiptv.core._lazy_requests import requests
from dopeiptv.i18n import LocalizedError
from dopeiptv.providers import boxes, dlna, epg, mediaserver, satip
from dopeiptv.providers.client import make_client
from dopeiptv.providers.dvbviewer import DVBViewerClient
from dopeiptv.providers.enigma2 import Enigma2Client
from dopeiptv.providers.hdhomerun import HDHomeRunClient
from dopeiptv.providers.mythtv import MythTVClient
from dopeiptv.providers.nextpvr import NextPVRClient
from dopeiptv.providers.satip import SatIPClient
from dopeiptv.providers.tvheadend import TvheadendClient
from dopeiptv.providers.vdr import VdrClient
from tests import fuzz_util as fz
from tests.test_boxes import dreambox_routes, hdhr_routes, openwebif_routes, tvh_routes
from tests.test_home_backends import NextPVR, dvb_routes, myth_routes, vdr_routes
from tests.test_media_servers import TOKEN, UID, jellyfin_routes, plex_routes
from tests.test_satip import SERVER_M3U, description

BASE = "http://192.168.1.50:8000"
#: What a client may raise to the window: not the box, the login, a request
#: that failed, an answer that does not parse.
ALLOWED = (LocalizedError, requests.RequestException, ValueError, ET.ParseError)
VOCAB = ("e2service", "e2servicereference", "e2servicename", "e2event", "e2eventstart", "e2eventduration",
         "e2eventtitle", "e2movie", "e2bouquet", "e2servicelist", "channel", "group", "root", "programme",
         "recording", "rsp", "sid", "salt", "listings", "l", "device", "deviceType", "X_SATIPCAP", "X_SATIPM3U")


@pytest.fixture(autouse=True)
def _alone(monkeypatch):
    store: dict = {}

    class S:
        def value(self, k, d=None):
            return store.get(k, d)

        def setValue(self, k, v):  # noqa: N802 - QSettings' name
            store[k] = v
    mediaserver.install_device_store(S())
    # SAT>IP's searches over UDP and RTSP: nothing on this network answers.
    monkeypatch.setattr(dlna, "ssdp_unicast", lambda *a, **k: {})
    monkeypatch.setattr(satip, "rtsp_options", lambda *a, **k: None)
    monkeypatch.setattr(satip, "DESCRIPTION_PATHS", ())
    monkeypatch.setattr(SatIPClient, "_seen_caps", {})
    yield
    mediaserver.install_device_store(None)


def _route(routes: dict, method: str, path: str):
    return routes.get((method, path), routes.get(path))


def _call(route, asked: fz.Asked):
    if not callable(route):
        return route
    n = len(inspect.signature(route).parameters)
    from urllib.parse import parse_qsl
    form = dict(parse_qsl(asked.body.decode("utf-8", "replace"), keep_blank_values=True))
    if n >= 3:
        try:
            body = json.loads(asked.body or b"null")
        except ValueError:
            body = None
        return route(asked.query, body, {k.lower(): v for k, v in asked.headers.items()})
    return route(asked.query, form) if n == 2 else route(asked.query)


def _encode(body) -> bytes:
    if isinstance(body, (dict, list, bool, int, float)) or body is None:
        return json.dumps(body).encode()
    return body if isinstance(body, bytes) else str(body).encode("utf-8")


def _mutated(rng: random.Random, body, data: bytes) -> bytes:
    if isinstance(body, (dict, list)):
        return fz.json_body(rng, body)
    head = data.lstrip()[:1]
    if head == b"<" and rng.random() < 0.85:
        return fz.mutate_xml(rng, data, VOCAB)
    if data.startswith(b"#EXTM3U") and rng.random() < 0.7:
        return fz.mutate_text(rng, data.decode("utf-8", "replace"), tokens=(
            "#EXTINF:", ",", '"', "\n", "rtsp://", "?", "&", "freq=", "msys=", "pids=")).encode("utf-8", "surrogatepass")
    return fz.mutate_bytes(rng, data)


class Served:
    """A fake box: the routes' answers, each mutated with chance *p* - what
    was served kept, to say what an input did."""

    def __init__(self, routes: dict) -> None:
        self.routes, self.rng, self.p, self.log = routes, random.Random(0), 0.0, []

    def start(self, n: int) -> None:
        self.rng = random.Random(n)
        self.p = self.rng.choice((0.1, 0.3, 0.6, 1.0))
        self.log = []

    def __call__(self, asked: fz.Asked):
        route = _route(self.routes, asked.method, asked.path)
        if route is None:
            return 404, b"not found"
        try:
            body = _call(route, asked)
        except Exception:  # noqa: BLE001 - a route of the suite's that this request surprised
            return 500, b""
        status = 200
        if isinstance(body, tuple):
            status, body = body
        data = _encode(body)
        if self.rng.random() < self.p:
            data = _mutated(self.rng, body, data)
            if len(self.log) < 40:
                self.log.append((asked.method, asked.path, data[:600]))
        return status, data

    def explain(self, _n: object) -> str:
        return "mutated answers: " + fz.shown(self.log, 6000)


def _exercise(c) -> list:
    """What the window asks of a box client after its login."""
    out: list = []
    for call in (c.live_categories, c.live_streams, c.vod_categories, c.vod_streams,
                 getattr(c, "series_categories", None), getattr(c, "series_list", None)):
        if call is None:
            continue
        rows = call()
        assert isinstance(rows, list) and all(isinstance(r, dict) for r in rows), call.__name__
        out.append(rows)
    for r in out[1][:5] if len(out) > 1 else []:
        assert isinstance(c.live_url(r["stream_id"]), str)
    for rows in out[2:]:
        for r in rows[:3]:
            if "stream_id" in r:
                assert isinstance(c.vod_info(r["stream_id"]), dict)
                assert isinstance(c.vod_url(r["stream_id"]), str)
            if "series_id" in r and hasattr(c, "series_info"):
                assert isinstance(c.series_info(r["series_id"]), dict)
    guide = c.own_guide()
    assert isinstance(guide, bytes)
    if guide:
        # The box's guide is XMLTV the app wrote itself: it always reads.
        epg.parse_xmltv(guide, 30)
    out.append(guide)
    return out


KINDS = {
    "enigma2-openwebif": (lambda: Enigma2Client(BASE), openwebif_routes),
    "enigma2-dreambox": (lambda: Enigma2Client(BASE), dreambox_routes),
    "tvheadend": (lambda: TvheadendClient(BASE), tvh_routes),
    "hdhomerun": (lambda: HDHomeRunClient(BASE), lambda: hdhr_routes([])),
    "vdr": (lambda: VdrClient(BASE, options={"streamdev_port": "8000"}), vdr_routes),
    "dvbviewer": (lambda: DVBViewerClient(BASE), lambda: dvb_routes([8000])),
    "nextpvr": (lambda: NextPVRClient(BASE, password="1234"), lambda: {"/service": NextPVR()}),
    "mythtv": (lambda: MythTVClient(BASE), lambda: myth_routes([])),
    "satip": (lambda: SatIPClient(BASE, options={"description": BASE + "/desc.xml"}),
              lambda: {"/desc.xml": description(m3u="/channels.m3u"), "/channels.m3u": SERVER_M3U}),
    "jellyfin": (lambda: make_client({"kind": "jellyfin", "server": BASE, "username": "anna", "token": TOKEN,
                                      "options": {"user_id": UID}}), jellyfin_routes),
    "emby": (lambda: make_client({"kind": "emby", "server": BASE, "username": "anna", "token": TOKEN,
                                  "options": {"user_id": UID}}), lambda: jellyfin_routes("/emby", new_api=False)),
    "plex": (lambda: make_client({"kind": "plex", "server": BASE, "username": "anna", "token": "den-tok",
                                  "options": {"server_id": "m1"}}), lambda: plex_routes(dvr=True)),
}


@pytest.mark.parametrize("kind", list(KINDS))
def test_fuzz_box(kind):
    make, routes = KINDS[kind]
    served = Served(routes())

    def target(n: int) -> list:
        served.start(n)
        c = make()
        try:
            c.authenticate()
        except ALLOWED:
            return []
        return _exercise(c)

    with fz.FakeNet(served):
        served.start(0)
        served.p = 0.0
        clean = make()
        clean.authenticate()
        assert clean.live_streams() or clean.vod_streams(), "the fake answers as the box does"
        fz.fuzz(f"box-{kind}", target, lambda rng: rng.randrange(1 << 30), allowed=ALLOWED,
                min_runs=12, explain=served.explain)


# -- the readers they share -------------------------------------------------------

XML_SAMPLES = [s.encode() if isinstance(s, str) else s for s in (
    openwebif_routes()["/web/about"], openwebif_routes()["/web/movielist"],
    '<?xml version="1.0" encoding="ISO-8859-1"?><e2servicelist><e2service><e2servicename>K\xf6</e2servicename>'
    "</e2service></e2servicelist>".encode("latin-1"),
    '<?xml version="1.0" encoding="UTF-16"?><a>x</a>'.encode("utf-16"),
)]


def test_fuzz_parse_xml():
    """A box's XML, whatever its declaration says it is written in: an
    Element, or ParseError - never another exception, nor text that
    encodes to nothing (a lone surrogate an escape decoded)."""
    def target(data: bytes) -> object:
        root = boxes.parse_xml(data)
        for el in root.iter():
            for text in (el.text, el.tail, *el.attrib.values()):
                if text:
                    text.encode("utf-8")
                    boxes.clean_text(text, lines=True)
        return root
    fz.fuzz("box-xml", target, lambda rng: fz.mutate_xml(rng, rng.choice(XML_SAMPLES), VOCAB),
            allowed=(ET.ParseError,), seeds=XML_SAMPLES)


def test_fuzz_box_helpers():
    def make(rng: random.Random) -> object:
        return fz.json_value(rng, {"t": "x", "n": 5, "s": "SVT1 \x86HD\x87"})

    def target(v: object) -> object:
        vals = list(v.values()) if isinstance(v, dict) else [v]
        for x in vals:
            text = boxes.clean_text(x)
            assert "\x00" not in text
            boxes.clean_text(x, lines=True)
            boxes._number(x)
            if isinstance(x, str):
                boxes.normalize_server(fz.decoded(x), 80)
                boxes.with_login("http://h/" + fz.decoded(x), "u:@", "p/w")
        return vals
    fz.fuzz("box-helpers", target, make)


def test_fuzz_build_xmltv():
    """The guide a box's events make is XMLTV that parses, whatever a box
    put in its titles - control characters among them."""
    def make(rng: random.Random) -> tuple:
        names = [(fz.decoded(rng.choice(fz.ODD_TEXT)) or "c", fz.decoded(rng.choice(fz.ODD_TEXT)))
                 for _ in range(rng.randint(1, 3))]
        events = []
        for _ in range(rng.randint(0, 6)):
            start = rng.choice((1_780_000_000, -5, 0, 10 ** 12, 2 ** 40))
            events.append({"channel": rng.choice(names)[0], "start": start, "stop": start + rng.choice((60, 0, -1)),
                           "title": fz.decoded(fz.mutate_text(rng, "Rapport", 2)),
                           "desc": fz.decoded(rng.choice(fz.ODD_TEXT)), "subtitle": fz.decoded(rng.choice(fz.ODD_TEXT))})
        return names, events

    def target(arg: tuple) -> object:
        names, events = arg
        data = boxes.build_xmltv(names, events)
        return epg.parse_xmltv(data, 10 ** 6)
    fz.fuzz("build-xmltv", target, make)


def test_fuzz_dlna_and_satip_descriptions():
    """A device's description on the network, of any shape: a renderer or
    None, a SAT>IP server or None - never an exception."""
    from tests.test_dlna import _DESC
    samples = [_DESC.encode(), description()]

    def target(data: bytes) -> object:
        with fz.FakeNet(lambda asked: data):
            got = dlna.describe("http://192.168.1.9:1400/desc.xml")
        assert got is None or (got.av_url.startswith("http://192.168.1.9") and dlna.Renderer.from_dict(got.to_dict()))
        sat = satip.parse_description(data, "http://192.168.1.9/desc.xml")
        assert sat is None or isinstance(sat["caps"], dict)
        return got, sat
    fz.fuzz("descriptions", target, lambda rng: fz.mutate_xml(rng, rng.choice(samples), VOCAB + (
        "service", "serviceType", "controlURL", "URLBase", "friendlyName", "UDN", "serviceList", "deviceList")),
        seeds=samples)

    def caps(text: str) -> dict:
        got = satip.parse_caps(text)
        assert all(isinstance(n, int) and n > 0 for n in got.values())
        satip.caps_text(got)
        return got
    fz.fuzz("satip-caps", caps, lambda rng: fz.mutate_text(rng, "DVBS2-4,DVBT-2", tokens=(",", "-", " ", "DVBS")))

    def m3u(text: str) -> object:
        entries, _guide = satip.parse_m3u(text)
        for e in entries[:20]:
            satip.tuning(e["url"]), satip.channel_key(e["url"], e["name"]), satip.system_of(e["url"])
            satip.service_pid(e["url"])
        satip.split_lists(text)
        return entries
    fz.fuzz("satip-m3u", m3u, lambda rng: fz.decoded(fz.mutate_text(rng, SERVER_M3U, tokens=(
        "#EXTINF:", ",", "?", "&", "=", "freq=", "msys=", "pids=", "src=", "rtsp://", "\n"))))


def test_stable_ids_that_hash_alike_take_linear_time(monkeypatch):
    """Channels a box names so that they hash alike (CRC-32 is easily
    steered) still get their ids in time that grows with their number."""
    import types
    monkeypatch.setattr(boxes, "zlib", types.SimpleNamespace(crc32=lambda data: 12345))

    def ids(n: int) -> list[int]:
        taken = boxes.Taken()
        return [boxes.stable_id(f"k{i}", taken) for i in range(n)]
    fz.linear(ids, lambda n: n, 1500)
    # The same ids as trying each in turn, from any ids taken before.
    for before in ((), (12345, 12347), (0x7FFFFFFF, 1)):
        plain, runs = set(before), boxes.Taken(before)
        want = []
        for i in range(300):
            sid = 12345 if i % 3 else 0x7FFFFFFF
            while sid in plain:
                sid = sid % 0x7FFFFFFF + 1
            plain.add(sid)
            want.append(sid)
        monkeypatch.setattr(boxes, "zlib", types.SimpleNamespace(
            crc32=lambda data: 0x7FFFFFFF if int(data[1:]) % 3 == 0 else 12345))
        assert [boxes.stable_id(f"k{i}", runs) for i in range(300)] == want
