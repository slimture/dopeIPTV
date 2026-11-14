"""Nerd, round 3: picons from a folder, a channel's guide by hand, multicast
through udpxy (played for real), several guides as one, a source's guide
shift, recording timers on Enigma2 and Tvheadend, presets."""
from __future__ import annotations

import gzip
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dopeiptv.core import nerd, nerd_lists
from dopeiptv.providers import epg as epg_mod
from dopeiptv.providers.client import make_client
from dopeiptv.providers.enigma2 import Enigma2Client
from dopeiptv.providers.epg import XmltvGuide, split_guide_urls
from dopeiptv.providers.tvheadend import TvheadendClient
from tests.test_boxes import EURO, SVT1, FakeBox, openwebif_routes, tvh_routes
from tests.test_nerd import TS, S, _libmpv, _plays


@pytest.fixture
def nerd_on():
    made = []

    def turn(**kw):
        s = S(nerd_enabled="true", **kw)
        made.append(s)
        nerd.install(s)
        return s
    yield turn
    nerd.install(None)


@pytest.fixture
def fake():
    made = []

    def make(routes, auth=None):
        b = FakeBox(routes, auth)
        made.append(b)
        return b
    yield make
    for b in made:
        b.close()


# -- picons and guide mapping --------------------------------------------------------

def test_snp_names():
    assert nerd_lists.snp_name("SVT1 HD") == "svt1hd"
    assert nerd_lists.snp_name("Arte & Co+ *") == "arteandcoplusstar"
    assert nerd_lists.snp_name("Télé Ñ") == "telen"


def test_picons_from_a_folder_by_reference_or_name(tmp_path, nerd_on):
    (tmp_path / "1_0_19_283D_3FB_1_C00000_0_0_0.png").write_bytes(b"x")
    (tmp_path / "eurosport.png").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("", encoding="utf-8")
    nerd_on(nerd_picon_dir=str(tmp_path))
    rows = [{"stream_id": 1, "name": "SVT1 HD", "_key": "1:0:19:283D:3FB:1:C00000:0:0:0", "stream_icon": ""},
            {"stream_id": 2, "name": "Eurosport HD", "_key": "x", "stream_icon": ""},
            {"stream_id": 3, "name": "Own Logo", "_key": "y", "stream_icon": "http://own/logo.png"},
            {"stream_id": 4, "name": "Nothing", "_key": "z", "stream_icon": ""}]
    nerd_lists.decorate(rows, None, "p1")
    assert rows[0]["stream_icon"].endswith("1_0_19_283D_3FB_1_C00000_0_0_0.png")
    assert rows[1]["stream_icon"].endswith("eurosport.png")          # "hd" off the name
    assert rows[2]["stream_icon"] == "http://own/logo.png"          # its own logo kept
    assert rows[3]["stream_icon"] == ""


def test_picons_can_replace_the_sources_logos(tmp_path, nerd_on):
    (tmp_path / "ownlogo.svg").write_bytes(b"x")
    nerd_on(nerd_picon_dir=str(tmp_path), nerd_picon_override="true")
    rows = [{"stream_id": 3, "name": "Own Logo", "stream_icon": "http://own/logo.png"}]
    nerd_lists.decorate(rows, None, None)
    assert rows[0]["stream_icon"].endswith("ownlogo.svg")


def test_nothing_changes_with_nerd_off(tmp_path):
    (tmp_path / "svt1.png").write_bytes(b"x")
    nerd.install(S(nerd_enabled="false", nerd_picon_dir=str(tmp_path), nerd_udpxy="http://r:4022",
                   nerd_epg_map_p1=json.dumps({"1": "x"})))
    rows = [{"stream_id": 1, "name": "SVT1", "stream_icon": "", "epg_channel_id": "own"}]
    assert nerd_lists.decorate(rows, None, "p1")[0] == {"stream_id": 1, "name": "SVT1", "stream_icon": "",
                                                       "epg_channel_id": "own"}
    assert nerd_lists.udpxy_url("udp://@239.1.1.1:1234") == "udp://@239.1.1.1:1234"
    nerd.install(None)


def test_a_channels_guide_chosen_by_hand(nerd_on):
    s = nerd_on()
    nerd_lists.set_epg_map(s, "p1", 7, "svt1.se")
    rows = [{"stream_id": 7, "name": "SVT1", "epg_channel_id": "wrong.id"},
            {"stream_id": 8, "name": "SVT2", "epg_channel_id": "svt2.se"}]
    nerd_lists.decorate(rows, None, "p1")
    assert rows[0]["epg_channel_id"] == "svt1.se" and rows[0]["_epg_own"] == "wrong.id"
    assert rows[1]["epg_channel_id"] == "svt2.se"
    assert nerd_lists.epg_map("other") == {}
    nerd_lists.set_epg_map(s, "p1", 7, None)
    assert nerd_lists.epg_map("p1") == {}


def test_make_client_lists_go_through_nerd(tmp_path, nerd_on, fake):
    box = fake(openwebif_routes())
    (tmp_path / "eurosport.png").write_bytes(b"x")
    nerd_on(nerd_picon_dir=str(tmp_path), nerd_picon_override="true")
    c = make_client({"id": "b1", "kind": "enigma2", "server": box.base, "options": {"picons": False}})
    rows = c.live_streams("Sport")
    assert rows[0]["stream_icon"].endswith("eurosport.png")


# -- udpxy, played for real ------------------------------------------------------------

def test_udpxy_addresses(nerd_on):
    nerd_on(nerd_udpxy="192.168.1.1:4022")
    assert nerd_lists.udpxy_url("udp://@239.1.1.1:1234") == "http://192.168.1.1:4022/udp/239.1.1.1:1234"
    assert nerd_lists.udpxy_url("rtp://239.2.2.2:5000") == "http://192.168.1.1:4022/rtp/239.2.2.2:5000"
    assert nerd_lists.udpxy_url("http://x/a.ts") == "http://x/a.ts"
    assert nerd_lists.udpxy_url("udp://@239.1.1.1:1234", {"udpxy": "http://other:81/"}) == \
        "http://other:81/udp/239.1.1.1:1234"


def test_a_multicast_m3u_plays_through_udpxy(tmp_path, nerd_on):
    """An operator's multicast playlist, a fake udpxy, and a real mpv."""
    paths = []

    class Udpxy(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            paths.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("Content-Length", str(len(TS)))
            self.end_headers()
            self.wfile.write(TS)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Udpxy)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        m3u = tmp_path / "iptv.m3u"
        m3u.write_text("#EXTM3U\n#EXTINF:-1 group-title=\"ISP\",SVT1\nudp://@239.1.1.1:1234\n", encoding="utf-8")
        nerd_on(nerd_udpxy=f"http://127.0.0.1:{srv.server_address[1]}")
        c = make_client({"id": "m", "kind": "m3u", "server": str(m3u)})
        c.authenticate()
        url = c.live_url(c.live_streams()[0]["stream_id"])
        assert url.endswith("/udp/239.1.1.1:1234")
        m = _libmpv()
        try:
            assert _plays(m, url)
        finally:
            m.terminate()
        assert paths[-1] == "/udp/239.1.1.1:1234"
    finally:
        srv.shutdown()
        srv.server_close()


# -- several guides as one; a source's guide shift ------------------------------------

def _xmltv(cid: str, title: str) -> bytes:
    from dopeiptv.providers.boxes import xmltv_time
    now = int(time.time())
    return (f'<?xml version="1.0"?><tv generator-info-name="x"><channel id="{cid}"><display-name>{cid}</display-name>'
            f'</channel><programme start="{xmltv_time(now - 60)}" stop="{xmltv_time(now + 3600)}" channel="{cid}">'
            f"<title>{title}</title></programme></tv>").encode()


def test_split_guide_urls():
    assert split_guide_urls("http://a/x.xml, https://b/y.xml.gz\nhttp://c/z?u=1,2") == [
        "http://a/x.xml", "https://b/y.xml.gz", "http://c/z?u=1,2"]
    assert split_guide_urls("") == [] and split_guide_urls("http://one/") == ["http://one/"]


def test_several_guides_as_one(fake):
    a = fake({"/a.xml": _xmltv("svt1.se", "Rapport").decode()})
    b = fake({"/b.xml.gz": gzip.compress(_xmltv("dr1.dk", "TV Avisen"))})
    down = fake({})
    g = XmltvGuide(None, f"{a.base}/a.xml, {b.base}/b.xml.gz, {down.base}/gone.xml")
    g._parse(g._download())
    g._loaded = True
    assert g.current_programme({"epg_channel_id": "svt1.se"})["title"] == "Rapport"
    assert g.current_programme({"epg_channel_id": "dr1.dk"})["title"] == "TV Avisen"


def test_all_guides_failing_is_a_failure(fake):
    down = fake({})
    g = XmltvGuide(None, f"{down.base}/a.xml, {down.base}/b.xml")
    with pytest.raises(RuntimeError):
        g._download()


def test_tv_body_of_odd_guides():
    assert epg_mod._tv_body(b'<?xml?><tv a="1"><x/></tv>') == b"<x/>"
    assert epg_mod._tv_body(b"<tv/>") == b""
    with pytest.raises(ValueError):
        epg_mod._tv_body(b"<html></html>")


def test_a_sources_guide_shift(nerd_on):
    from dopeiptv.ui.mw_recording import _RecordingMixin

    class W(_RecordingMixin):
        settings = S(epg_delay_min="10")
    nerd.install(None)
    assert W()._epg_delay_minutes() == 10
    nerd_on()
    nerd.set_source({"kind": "xtream", "options": {"epg_shift": "-60"}})
    assert W()._epg_delay_minutes() == -50
    nerd.set_source(None)


# -- timers on the box ------------------------------------------------------------------

def test_enigma2_timer(fake):
    calls = []
    routes = openwebif_routes()
    routes["/web/timeradd"] = lambda q: (calls.append(q), "<e2simplexmlresult><e2state>True</e2state>"
                                         "<e2statetext>Timer added successfully</e2statetext></e2simplexmlresult>")[1]
    box = fake(routes)
    c = Enigma2Client(box.base)
    c.authenticate()
    sid = next(r["stream_id"] for r in c.live_streams() if r["name"] == "Eurosport")
    assert c.add_timer(sid, 1_800_000_000, 1_800_003_600, "Cykel", "Etapp 3") == (True, "Timer added successfully")
    assert calls[0]["sRef"] == EURO and calls[0]["begin"] == "1800000000" and calls[0]["end"] == "1800003600"
    assert calls[0]["name"] == "Cykel" and calls[0]["justplay"] == "0"
    assert c.add_timer(999, 1, 2, "x") == (False, "")


def test_tvheadend_timer_behind_digest(fake):
    got = []

    class H(BaseHTTPRequestHandler):
        pass
    routes = tvh_routes()
    box = fake(routes, auth=("digest", "kodi", "s3cret"))
    c = TvheadendClient(box.base, "kodi", "s3cret")
    c.authenticate()
    # The fake answers GET; a POST to it arrives here instead.
    import types

    def post(self, path, data, timeout=None):
        got.append((path, json.loads(data["conf"])))
        return types.SimpleNamespace(json=lambda: {"uuid": "dvr1"})
    c.post = types.MethodType(post, c)
    sid = c.live_streams("Nyheter")[0]["stream_id"]
    assert c.add_timer(sid, 1_800_000_000, 1_800_003_600, "Rapport") == (True, "dvr1")
    path, conf = got[0]
    assert path == "/api/dvr/entry/create" and conf["channel"] == "c1"
    assert conf["start"] == 1_800_000_000 and conf["title"] == {"eng": "Rapport"}


def test_box_post_answers_digest(fake):
    """BoxClient.post logs in as the box asks, for real."""
    seen = []

    class Post(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n).decode()
            if not self.headers.get("Authorization", "").startswith("Digest "):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Digest realm="tvheadend", nonce="n", qop="auth"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            seen.append(body)
            data = b'{"uuid": "abc"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Post)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = TvheadendClient(f"http://127.0.0.1:{srv.server_address[1]}", "u", "p")
        r = c.post("/api/dvr/entry/create", {"conf": "{}"})
        assert r.json() == {"uuid": "abc"} and seen == ["conf=%7B%7D"]
    finally:
        srv.shutdown()
        srv.server_close()


# -- presets --------------------------------------------------------------------------

def test_presets_fill_a_source():
    from dopeiptv.ui.source_presets import PRESETS, fill
    by = {p.label: p for p in PRESETS}
    assert fill(by["Fritz!Box TV (HD)"], "") == {
        "kind": "m3u", "server": "http://fritz.box/dvb/m3u/tvhd.m3u", "epg_url": "", "name": "Fritz!Box TV (HD)"}
    t = fill(by["Threadfin"], "http://nas.local/")
    assert t["server"] == "http://nas.local:34400/m3u/threadfin.m3u"
    assert t["epg_url"] == "http://nas.local:34400/xmltv/threadfin.xml"
    for p in PRESETS:
        src = fill(p, "10.0.0.5")
        assert src["server"].startswith("http://") and "{host}" not in src["server"] + src["epg_url"]


def test_svt1_reference_in_test_data():
    # The picon test above uses the same reference the Enigma2 fakes do.
    assert SVT1.startswith("1:0:19:283D")
