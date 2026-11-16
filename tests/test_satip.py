"""SAT>IP servers (providers/satip.py) against fakes on this computer's
loopback that answer as SES's specification says a server does: an SSDP
search answered on a test socket, the device description with its tuners
(X_SATIPCAP) and its channel list (X_SATIPM3U), an M3U, RTSP's OPTIONS.
Found by a search (multicast and sent to an address) and by Find on my
network; tuners per system from the description; channels from the
server's list, a Fritz!Box's, or one the person gives, with stable ids;
play addresses on the server with the transport the source asks for (the
player's, multiview's and the recorder's option); the guide note; and the
window's question before a stream the tuners have no room for."""
from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from dopeiptv.core import connections, rtsp, tuners
from dopeiptv.core.connections import Use
from dopeiptv.i18n import LocalizedError, tr
from dopeiptv.providers import lan_scan, satip
from dopeiptv.providers.client import make_client
from dopeiptv.providers.satip import SatIPClient

LOOP = "127.0.0.1"


def description(caps: str = "DVBS2-2,DVBT-1", m3u: str = "/channels.m3u", name: str = "Octopus NET",
                kind: str = "urn:ses-com:device:SatIPServer:1") -> bytes:
    cap = f'<satip:X_SATIPCAP xmlns:satip="urn:ses-com:satip">{caps}</satip:X_SATIPCAP>' if caps else ""
    lst = f'<satip:X_SATIPM3U xmlns:satip="urn:ses-com:satip">{m3u}</satip:X_SATIPM3U>' if m3u else ""
    return (f'<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0" configId="1">'
            f"<specVersion><major>1</major><minor>1</minor></specVersion><device>"
            f"<deviceType>{kind}</deviceType><friendlyName>{name}</friendlyName>"
            f"<manufacturer>Digital Devices</manufacturer><modelName>Octopus NET S2</modelName>"
            f"<UDN>uuid:5a7e11e0-0000-1000-8000-0001020304{len(name):02d}</UDN>{cap}{lst}"
            f"</device></root>").encode()


ASTRA = ("src=1&freq=11494&pol=h&ro=0.35&msys=dvbs2&mtype=8psk&plts=on&sr=22000&fec=23"
         "&pids=0,17,18,5100,5101,5102,5104")
SERVER_M3U = f"""#EXTM3U
#EXTINF:0 tvg-id="DasErste.de" tvg-logo="http://logos/ard.png" group-title="ARD",1. Das Erste HD
rtsp://sat.ip/?{ASTRA}
#EXTINF:0 group-title="ZDF",2. ZDF HD
rtsp://192.168.0.99/?src=1&freq=11362&pol=h&msys=dvbs2&sr=22000&pids=0,6100,6110,6120
#EXTINF:0 group-title="DVB-T",3sat
rtsp://sat.ip/?freq=506&bw=8&msys=dvbt2&plp=0&pids=0,300,301,302
"""
FRITZ_HD = """#EXTM3U
#EXTINF:0,Das Erste HD
#EXTVLCOPT:network-caching=1000
rtsp://192.168.178.1:554/?avm=1&freq=450&bw=8&msys=dvbc&mtype=256qam&sr=6900&specinv=1&pids=0,16,17,18,20,5100,5101,5102
"""
FRITZ_RADIO = """#EXTM3U
#EXTINF:0,Deutschlandfunk
rtsp://192.168.178.1:554/?avm=1&freq=410&bw=8&msys=dvbc&mtype=256qam&sr=6900&specinv=1&pids=0,16,17,18,20,610
"""


class Fake:
    """An HTTP server on the loopback: path -> body (bytes or str), or
    (status, body). Every path asked is kept in *asked*."""

    def __init__(self, routes: dict) -> None:
        self.routes, self.asked = routes, []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):  # noqa: N802
                path = self.path.split("?", 1)[0]
                fake.asked.append(path)
                got = fake.routes.get(path)
                status, body = got if isinstance(got, tuple) else (200, got)
                if got is None:
                    status, body = 404, b""
                data = body.encode() if isinstance(body, str) else body
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self.httpd = ThreadingHTTPServer((LOOP, 0), H)
        self.port = self.httpd.server_address[1]
        self.base = f"http://{LOOP}:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class Ssdp:
    """Answers an M-SEARCH for a SAT>IP server, as a server does, from the
    loopback; a search for anything else gets no answer."""

    def __init__(self, location: str) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((LOOP, 0))
        self.port = self.sock.getsockname()[1]
        self.location, self.asked = location, []
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            while True:
                data, addr = self.sock.recvfrom(4096)
                self.asked.append(data.decode())
                if satip.ST in data.decode():
                    self.sock.sendto((f"HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=1800\r\nEXT:\r\n"
                                      f"LOCATION: {self.location}\r\nSERVER: Linux/1.0 UPnP/1.1 SatIPServer/1.0\r\n"
                                      f"ST: {satip.ST}\r\nDEVICEID.SES.COM: 1\r\n\r\n").encode(), addr)
        except OSError:
            pass

    def close(self) -> None:
        self.sock.close()


class Rtsp:
    """Answers RTSP's OPTIONS on a TCP port, as a SAT>IP server does."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind((LOOP, 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.asked: list[str] = []
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            while True:
                conn, _a = self.sock.accept()
                with conn:
                    data = conn.recv(4096).decode()
                    self.asked.append(data)
                    conn.sendall(b"RTSP/1.0 200 OK\r\nCSeq: 1\r\nPublic: OPTIONS, DESCRIBE, SETUP, PLAY, TEARDOWN\r\n"
                                 b"Server: minisatip/1.3.4\r\n\r\n")
        except OSError:
            pass

    def close(self) -> None:
        self.sock.close()


def closed_port() -> int:
    s = socket.socket()
    s.bind((LOOP, 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(autouse=True)
def _alone(monkeypatch):
    """No proxy, no tuners remembered from another test, nothing asked
    where real servers keep their descriptions."""
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(SatIPClient, "_seen_caps", {})
    monkeypatch.setattr(SatIPClient, "SSDP_PORT", closed_port())
    monkeypatch.setattr(satip, "DESCRIPTION_PATHS", ())


@pytest.fixture
def fake():
    made: list = []

    def make(routes):
        f = Fake(routes)
        made.append(f)
        return f
    yield make
    for f in made:
        f.close()


def server(fake, **routes) -> Fake:
    return fake({"/desc.xml": description(), "/channels.m3u": SERVER_M3U, **routes})


# -- what a server says ---------------------------------------------------------------

def test_the_tuners_per_system_and_the_description():
    assert satip.parse_caps("DVBS2-4,DVBT-2") == {"DVBS2": 4, "DVBT": 2}
    assert satip.parse_caps(" dvbs2-2 , DVBC-4,DVBS-1,junk,DVBT2-0") == {"DVBS2": 3, "DVBC": 4}
    assert satip.parse_caps("") == {}
    assert satip.caps_text({"DVBS2": 4, "DVBT": 2}) == "DVB-S/S2 ×4, DVB-T ×2"
    d = satip.parse_description(description(), "http://10.0.0.7:8080/desc.xml")
    assert d is not None and d["name"] == "Octopus NET" and d["caps"] == {"DVBS2": 2, "DVBT": 1}
    assert d["m3u"] == "http://10.0.0.7:8080/channels.m3u" and d["udn"].startswith("uuid:")
    # Another device's description (a router's, a TV's) is no server's.
    assert satip.parse_description(description(kind="urn:schemas-upnp-org:device:MediaRenderer:1"),
                                   "http://10.0.0.1/x.xml") is None
    assert satip.parse_description(b"<html>router</html>", "http://10.0.0.1/") is None
    assert satip.parse_description(b'<!DOCTYPE x [<!ENTITY a "b">]><root/>', "http://x/") is None


def test_an_rtsp_options_answer_tells_a_server_without_a_description():
    srv = Rtsp()
    try:
        got = satip.rtsp_options(LOOP, srv.port)
        assert got == {"status": 200, "public": "OPTIONS, DESCRIBE, SETUP, PLAY, TEARDOWN",
                       "server": "minisatip/1.3.4"}
        assert srv.asked[0].startswith(f"OPTIONS rtsp://{LOOP}:{srv.port}/ RTSP/1.0\r\nCSeq: 1")
        assert "SETUP" not in srv.asked[0], "nothing set up, no tuner taken"
    finally:
        srv.close()
    assert satip.rtsp_options(LOOP, closed_port(), timeout=0.5) is None


# -- finding servers ------------------------------------------------------------------

def test_a_search_finds_the_server_and_its_tuners(fake):
    srv = server(fake)
    ssdp = Ssdp(f"{srv.base}/desc.xml")
    try:
        found = satip.discover(timeout=0.8, addr=(LOOP, ssdp.port))
        assert found == [{"kind": "satip", "server": f"http://{LOOP}", "name": f"Octopus NET - {LOOP}",
                          "description": f"{srv.base}/desc.xml", "caps": "DVB-S/S2 ×2, DVB-T ×1"}]
        assert any(f"ST: {satip.ST}" in a for a in ssdp.asked)
        # Asked at its address: a unicast search crosses to another VLAN.
        assert satip.discover_at([LOOP], timeout=0.8, port=ssdp.port) == found
        stop = threading.Event()
        stop.set()
        assert satip.discover_at([LOOP], timeout=0.8, port=ssdp.port, cancel=stop) == []
    finally:
        ssdp.close()


def test_find_on_my_network_finds_one_on_another_network(fake, monkeypatch):
    srv = server(fake)
    ssdp = Ssdp(f"{srv.base}/desc.xml")
    shut = closed_port()
    try:
        got = lan_scan.scan(hosts=[], extra=[LOOP], broadcast=False, ssdp_port=ssdp.port,
                            ports=dict.fromkeys(lan_scan.PORTS, shut))
        assert [(d["kind"], d["server"], d["description"]) for d in got] == [
            ("satip", f"http://{LOOP}", f"{srv.base}/desc.xml")]
    finally:
        ssdp.close()


# -- the channels -----------------------------------------------------------------------

def test_the_servers_own_list_grouped_with_logos_and_stable_ids(fake):
    srv = server(fake)
    c = SatIPClient(LOOP, options={"description": f"{srv.base}/desc.xml"})
    assert c.authenticate() == {"user_info": {"auth": 1}}
    info = c.account_info()
    assert info["name"] == "Octopus NET" and info["tuners"] == 3 and info["caps"] == "DVB-S/S2 ×2, DVB-T ×1"
    assert [x["category_name"] for x in c.live_categories()] == ["ARD", "ZDF", "DVB-T"]
    rows = {r["name"]: r for r in c.live_streams()}
    ard = rows["Das Erste HD"]
    assert ard["num"] == 1 and ard["stream_icon"] == "http://logos/ard.png"
    assert ard["epg_channel_id"] == "DasErste.de"
    # Every tuning on the server's own address and RTSP port - the list
    # named a stand-in and another address.
    assert c.live_url(ard["stream_id"]) == f"rtsp://{LOOP}:554/?{ASTRA}"
    assert c.live_url(rows["ZDF HD"]["stream_id"]).startswith(f"rtsp://{LOOP}:554/?src=1&freq=11362")
    assert rows["3sat"]["num"] == 3, "no number given: its place in the list"
    # The same channels after the list is reordered, one renamed and given
    # an audio track: the same ids.
    entries = SERVER_M3U.splitlines()[1:]
    pairs = [entries[i:i + 2] for i in range(0, len(entries), 2)]
    pairs.reverse()
    pairs[-1] = [pairs[-1][0].replace("1. Das Erste HD", "Das Erste"), pairs[-1][1] + ",5106"]
    srv.routes["/channels.m3u"] = "#EXTM3U\n" + "\n".join(x for p in pairs for x in p) + "\n"
    again = SatIPClient(LOOP, options={"description": f"{srv.base}/desc.xml"})
    again.authenticate()
    ids = {r["name"]: r["stream_id"] for r in again.live_streams()}
    assert ids == {"Das Erste": ard["stream_id"], "ZDF HD": rows["ZDF HD"]["stream_id"],
                   "3sat": rows["3sat"]["stream_id"]}
    # No guide in SAT>IP: said plainly.
    assert c.guide_note == tr("satip_no_guide") and c.epg_url == "" and c.own_guide() == b""


def test_stable_ids_from_the_tuning():
    key = satip.channel_key(f"rtsp://sat.ip/?{ASTRA}", "Das Erste HD")
    assert key == "satip|S|1|11494|h||5100"
    # Another list's spelling of the same channel: dvbs for dvbs2, 11494.00,
    # its own host, more PIDs, another name.
    assert satip.channel_key("rtsp://10.0.0.5:554/?msys=dvbs&freq=11494.00&pol=H&sr=22000&src=1"
                             "&pids=0,5100,5101,5102,5104,5106", "ARD") == key
    assert satip.channel_key(f"rtsp://sat.ip/?{ASTRA.replace('src=1', 'src=2')}") != key
    assert satip.service_pid("0,16,17,18,20,8191") == "" and satip.service_pid("all") == ""
    assert satip.channel_key("rtsp://tvh:9983/stream/channelid/123") == "satip|/stream/channelid/123?"


def test_two_services_on_one_pid_keep_their_own_ids(fake):
    shared = """#EXTM3U
#EXTINF:0,WDR Köln
rtsp://sat.ip/?src=1&freq=12421&pol=h&msys=dvbs&sr=27500&pids=0,28307,101,102
#EXTINF:0,WDR Bonn
rtsp://sat.ip/?src=1&freq=12421&pol=h&msys=dvbs&sr=27500&pids=0,28307,101,103
"""
    srv = server(fake, **{"/channels.m3u": shared})
    c = SatIPClient(LOOP, options={"description": f"{srv.base}/desc.xml"})
    c.authenticate()
    assert len({r["stream_id"] for r in c.live_streams()}) == 2


def test_a_fritzbox_lists_its_own_three(fake):
    box = fake({"/satipdesc.xml": description(caps="DVBC-4", m3u="", name="FRITZ!Box 6591 Cable"),
                "/dvb/m3u/tvhd.m3u": FRITZ_HD, "/dvb/m3u/radio.m3u": FRITZ_RADIO})
    c = SatIPClient(box.base, options={"description": f"{box.base}/satipdesc.xml"})
    c.authenticate()
    assert c.box_info["tuners"] == 4 and c.caps == {"DVBC": 4}
    assert [x["category_name"] for x in c.live_categories()] == [tr("satip_tv_hd"), tr("satip_radio")]
    assert "/dvb/m3u/tvsd.m3u" in box.asked, "the SD list tried too; its 404 is no failure"
    url = c.live_url(c.live_streams()[0]["stream_id"])
    assert url.startswith(f"rtsp://{LOOP}:554/?avm=1&freq=450") and "pids=0,16,17,18,20,5100" in url


def test_a_list_the_person_gives_wins_with_its_guide(fake, tmp_path):
    srv = server(fake)
    lst = tmp_path / "Astra 19.2E.m3u"
    lst.write_text('#EXTM3U url-tvg="http://epg.example/astra.xml"\n'
                   f'#EXTINF:0 tvg-chno="7" group-title="News",Euronews\nrtsp://sat.ip/?{ASTRA}\n', encoding="utf-8")
    c = SatIPClient(LOOP, options={"description": f"{srv.base}/desc.xml", "m3u": str(lst)})
    c.authenticate()
    assert [r["name"] for r in c.live_streams()] == ["Euronews"] and c.live_streams()[0]["num"] == 7
    assert "/channels.m3u" not in srv.asked, "the server's own list is not read then"
    assert c.epg_url == "http://epg.example/astra.xml" and c.guide_note == ""
    # Several, one after the other; one of them a web address.
    assert satip.split_lists(f"{lst} {srv.base}/channels.m3u") == [str(lst), f"{srv.base}/channels.m3u"]
    assert satip.split_lists("http://a/1.m3u, http://b/2.m3u\nfile:///x/y z.m3u") == [
        "http://a/1.m3u", "http://b/2.m3u", "file:///x/y z.m3u"]
    two = SatIPClient(LOOP, options={"description": f"{srv.base}/desc.xml",
                                     "m3u": f"{lst.as_uri()} {srv.base}/channels.m3u"})
    two.authenticate()
    assert len(two.live_streams()) == 4


def test_no_list_and_an_unreadable_one_say_what_to_do(fake, tmp_path):
    bare = fake({"/desc.xml": description(m3u="")})
    with pytest.raises(LocalizedError) as e:
        SatIPClient(bare.base, options={"description": f"{bare.base}/desc.xml"}).authenticate()
    assert e.value.key == "err_satip_no_list"
    with pytest.raises(LocalizedError) as e:
        SatIPClient(bare.base, options={"description": f"{bare.base}/desc.xml",
                                        "m3u": str(tmp_path / "missing.m3u")}).authenticate()
    assert e.value.key == "err_satip_list_unread"
    # Nothing at the address at all: no SAT>IP server there.
    with pytest.raises(LocalizedError) as e:
        SatIPClient(f"rtsp://{LOOP}:{closed_port()}").authenticate()
    assert e.value.key == "err_box_not_found"


def test_found_by_a_search_sent_to_its_address(fake, monkeypatch):
    srv = server(fake)
    ssdp = Ssdp(f"{srv.base}/desc.xml")
    monkeypatch.setattr(SatIPClient, "SSDP_PORT", ssdp.port)
    try:
        c = SatIPClient(LOOP)
        c.authenticate()
        assert c.box_info["name"] == "Octopus NET" and len(c.live_streams()) == 3
        # A refresh asks where it was found, without searching again.
        before = len(ssdp.asked)
        c.clear_list_cache()
        c.live_categories()
        assert len(ssdp.asked) == before
    finally:
        ssdp.close()


def test_a_server_with_no_description_but_rtsp_and_a_list(tmp_path):
    srv = Rtsp()
    lst = tmp_path / "hotbird.m3u"
    lst.write_text(f"#EXTM3U\n#EXTINF:0,Rai 1\nrtsp://sat.ip/?{ASTRA}\n", encoding="utf-8")
    try:
        c = SatIPClient(f"rtsp://{LOOP}:{srv.port}", options={"m3u": str(lst)})
        c.authenticate()
        assert c.box_info["name"] == f"minisatip/1.3.4 ({LOOP})" and c.tuner_limit() is None
        # Its RTSP port, as typed, for the tunings.
        assert c.live_url(c.live_streams()[0]["stream_id"]) == f"rtsp://{LOOP}:{srv.port}/?{ASTRA}"
    finally:
        srv.close()


def test_test_connection_asks_its_rtsp_port_and_reads_no_stream(tmp_path):
    from dopeiptv.core import conn_test as ct
    srv = Rtsp()
    lst = tmp_path / "astra.m3u"
    lst.write_text(f"#EXTM3U\n#EXTINF:0,Das Erste HD\nrtsp://sat.ip/?{ASTRA}\n", encoding="utf-8")
    try:
        t = ct.ConnTest({"id": "s1", "kind": "satip", "server": f"rtsp://{LOOP}:{srv.port}",
                         "options": {"m3u": str(lst)}}, make_client, None, speed_secs=0.3)
        t.run()
        st = {s.name: s for s in t.snapshot()}
        assert {k: s.state for k, s in st.items()} == {"dns": ct.OK, "tcp": ct.OK, "tls": ct.SKIP,
                                                       "login": ct.OK, "stream": ct.OK, "speed": ct.SKIP}
        assert st["tcp"].params["port"] == srv.port
        assert st["stream"].key == "ct_stream_rtsp" and st["speed"].key == "ct_skip_rtsp"
        said = [a for a in srv.asked if a]      # the TCP step connects and says nothing
        assert said and all(a.startswith("OPTIONS ") for a in said), "nothing set up: no tuner taken"
    finally:
        srv.close()


def test_make_client_builds_one():
    c = make_client({"kind": "satip", "server": "192.168.178.1", "options": {"transport": "udp"}})
    assert isinstance(c, SatIPClient) and c.server == "http://192.168.178.1" and c.transport() == "udp"
    d = SatIPClient("http://192.168.178.1:49000/satipdesc.xml")
    assert d.server == "http://192.168.178.1"
    assert d.options["description"] == "http://192.168.178.1:49000/satipdesc.xml"
    # The RTSP port typed: its own, not the web side's.
    for typed in ("10.0.0.9:554", "rtsp://10.0.0.9"):
        e = SatIPClient(typed)
        assert (e.server, e.rtsp_port, e.test_address) == ("http://10.0.0.9", 554, "rtsp://10.0.0.9:554")
    assert SatIPClient("rtsp://10.0.0.9:9983").rtsp_port == 9983


# -- playback ---------------------------------------------------------------------------

def test_the_play_address_and_the_transport(fake):
    srv = server(fake)
    http = SatIPClient(LOOP, options={"description": f"{srv.base}/desc.xml", "transport": "http"})
    http.authenticate()
    urls = [http.live_url(r["stream_id"]) for r in http.live_streams()]
    assert f"http://{LOOP}:8875/?{ASTRA}" in urls
    http.options["http_port"] = "8080"
    assert http.play_address(f"rtsp://sat.ip/?{ASTRA}") == f"http://{LOOP}:8080/?{ASTRA}"
    # A server's own other address is kept; one naming the loopback is put
    # on the server's.
    c = SatIPClient("10.0.0.9")
    assert c.play_address("rtsp://10.0.0.9:9983/stream/channelid/7") == "rtsp://10.0.0.9:9983/stream/channelid/7"
    assert c.play_address("http://0.0.0.0:9981/stream/channelid/7") == "http://10.0.0.9:9981/stream/channelid/7"
    # The player, multiview and the recorder take the source's transport
    # for an RTSP address of a SAT>IP source - and nothing for any other.
    sat = {"_kind": "satip"}
    url = f"rtsp://10.0.0.9:554/?{ASTRA}"
    assert rtsp.mpv_options(sat, url) == {"rtsp-transport": "tcp"}
    assert rtsp.mpv_options(dict(sat, transport="udp"), url) == {"rtsp-transport": "udp"}
    assert rtsp.ffmpeg_options(dict(sat, transport="udp"), url) == ["-rtsp_transport", "udp"]
    assert rtsp.mpv_args(sat, url) == ["--rtsp-transport=tcp"]
    assert rtsp.mpv_options({"_kind": "m3u"}, url) == {} and rtsp.mpv_options(sat, "http://x/1.ts") == {}
    assert rtsp.mpv_options(None, url) == {}


def test_the_player_sets_it_for_that_stream_alone():
    from dopeiptv.core import nerd
    from dopeiptv.media.embedded import rtsp_file_options
    url = f"rtsp://10.0.0.9:554/?{ASTRA}"
    try:
        nerd.set_source({"kind": "satip", "options": {"transport": "udp"}})
        assert rtsp_file_options(url) == {"rtsp-transport": "udp"}
        assert rtsp_file_options("http://10.0.0.9:8875/?freq=1") == {}
        nerd.set_source({"kind": "m3u", "server": "http://x/list.m3u"})
        assert rtsp_file_options(url) == {}, "another source's RTSP opens as it always did"
    finally:
        nerd.set_source(None)


def test_a_recording_takes_the_sources_transport(tmp_path):
    from PyQt6.QtCore import QSettings

    from dopeiptv.core.recording import RecordingManager
    from dopeiptv.core.stores import PlaylistStore
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    store = PlaylistStore(s)
    pl = store.add({"name": "Octopus", "kind": "satip", "server": "10.0.0.9", "options": {"transport": "udp"}})
    rec = RecordingManager.__new__(RecordingManager)
    rec.settings = s
    url = f"rtsp://10.0.0.9:554/?{ASTRA}"
    job = {"url": url, "source": pl["id"], "source_kind": "satip"}
    assert rec._rtsp_options(job, "ffmpeg") == ["-rtsp_transport", "udp"]
    assert rec._rtsp_options(job, "mpv") == ["--rtsp-transport=udp"]
    assert rec._rtsp_options(dict(job, source="gone"), "ffmpeg") == ["-rtsp_transport", "tcp"]
    assert rec._rtsp_options(dict(job, source_kind="m3u"), "ffmpeg") == []
    assert rec._rtsp_options(dict(job, url="http://10.0.0.9:8875/?freq=1"), "ffmpeg") == []


# -- the tuners ---------------------------------------------------------------------------

S1 = f"rtsp://10.0.0.9:554/?{ASTRA}"
S2 = "rtsp://10.0.0.9:554/?src=1&freq=11362&pol=h&msys=dvbs2&sr=22000&pids=0,6100"
S3 = "rtsp://10.0.0.9:554/?src=1&freq=10744&pol=h&msys=dvbs&sr=22000&pids=0,61"
T1 = "rtsp://10.0.0.9:554/?freq=506&bw=8&msys=dvbt2&plp=0&pids=0,300"


def caps_client(caps: dict | None, **options) -> SatIPClient:
    c = SatIPClient("10.0.0.9", options=options)
    c.caps = dict(caps or {})
    return c


def test_each_stream_takes_a_tuner_of_its_system():
    c = caps_client({"DVBS2": 2, "DVBT2": 1})
    assert c.tuner_limit() == 3
    assert c.tuner_verdict(S2, [Use("player", "", S1)]) is None, "one of two in use: room"
    v = c.tuner_verdict(S3, [Use("player", "", S1), Use("recording", "", S2)])
    assert v is not None and v.kind == tuners.CUTS and v.counted
    assert (v.tuners, v.in_use, v.system) == (2, 2, "DVB-S/S2")
    assert [t.what for t in v.cut] == ["player", "recording"]
    # A DVB-T2 channel has its own tuner free; another server's streams are not counted.
    assert c.tuner_verdict(T1, [Use("player", "", S1), Use("recording", "", S2)]) is None
    assert c.tuner_verdict(S3, [Use("player", "", "rtsp://10.0.0.10:554/?freq=1&msys=dvbs2")]) is None
    assert c.tuner_verdict("http://provider.example/live/1.ts", [Use("player", "", S1)]) is None
    # Fewer, as the person says: for every system together.
    low = caps_client({"DVBS2": 2, "DVBT2": 1}, tuners="2")
    assert low.tuner_limit() == 2
    v = low.tuner_verdict(T1, [Use("player", "", S1), Use("multiview", "", S2)])
    assert v is not None and (v.tuners, v.system) == (2, "")


def test_with_no_number_said_a_second_stream_is_asked():
    c = caps_client(None)
    assert c.tuner_limit() is None and c.tuner_verdict(S1, []) is None
    v = c.tuner_verdict(S2, [Use("player", "", S1)])
    assert v is not None and v.kind == tuners.CUTS and v.tuners == 0
    # The person says it has two.
    assert caps_client(None, tuners="2").tuner_verdict(S2, [Use("player", "", S1)]) is None
    # The tuners another source of the same server learnt this session count.
    SatIPClient._seen_caps["10.0.0.9"] = {"DVBS2": 1}
    assert SatIPClient("10.0.0.9").tuner_limit() == 1


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
                                          isVisible=lambda: False)
            self.rec = _Rec(jobs)
            self._multiview_win = (SimpleNamespace(cells=list(cells), _focused=None) if cells else None)
            self.playlist_store = SimpleNamespace(
                active_id="sat1", active=lambda: {"id": "sat1", "kind": "satip"},
                get=lambda pid: {"name": "Octopus"} if pid == "sat1" else None, playlists=lambda: [])
            self.settings = _S(mv_stop_docked="false")
            self._failover_source = None
            self.cast = SimpleNamespace(active=None)
            self._cast_ctx = None
            self.pool = object()
            self._playing_key = None
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


class _Cell:
    def __init__(self, url=None) -> None:
        self.url, self.source_id = url, "sat1"


def test_the_window_asks_before_a_stream_the_tuners_have_no_room_for():
    c = caps_client({"DVBS2": 1})
    c.live_url = lambda sid, fmt="ts": {1: S1, 2: S2}[int(sid)]
    w = window(c, player_url=S1, cells=[_Cell()])
    # A multiview cell beside the player: the one tuner is the player's.
    assert not w._conn_ok_multiview(None, S2)
    title, text = w.asked[-1]
    assert title == tr("tuner_title") and tr("satip_what") in text
    assert tr("satip_full", n=1, system="DVB-S/S2") in text
    assert tr("conn_used_by", who=tr("tuner_w_own", what=tr("conn_k_player"))) in text
    assert tr("satip_risk") in text and w.phone
    w.answer = 0
    assert w._conn_ok_multiview(None, S2), "go ahead anyway"
    # A cast or a zap lets the player's stream go first: no question.
    w.asked.clear()
    assert w._conn_ok_cast(S2) and w._guard_stream_switch(S2, "ZDF") and not w.asked
    # A recording over its own connection while the player plays.
    w.answer = 1
    w._record_in_background({"stream_id": 2, "name": "ZDF"}, "ZDF", 0, None, "")
    assert tr("tuner_what_rec") in w.asked[-1][1] and not w.rec.added
    # Two tuners: room for the second.
    c.caps = {"DVBS2": 2}
    w.asked.clear()
    assert w._conn_ok_multiview(None, S2) and not w.asked


def test_a_running_recording_holds_its_tuner_and_in_use_says_so():
    c = caps_client(None)
    job = {"status": "recording", "url": S1, "source": "sat1"}
    w = window(c, jobs=[job])
    assert not w._conn_ok_multiview(None, S2)
    text = w.asked[-1][1]
    assert tr("satip_unknown") in text and tr("tuner_w_own", what=tr("conn_k_recording")) in text
    lines = w._in_use_lines()
    assert ("head", tr("inuse_satip_unknown", name="Octopus", n=1)) in lines
    assert ("item", "• " + tr("tuner_w_own", what=tr("conn_k_recording"))) in lines
    c.caps = {"DVBS2": 4}
    assert ("head", tr("inuse_box_tuners", name="Octopus", n=1, total=4)) in w._in_use_lines()
    # A source of another kind: nothing of this.
    other = window(SimpleNamespace(max_connections=None))
    assert other._tuner_verdict(S2, [Use("recording", "", S1)]) is None
    assert connections.is_network(S1)


def test_the_guide_note_unless_the_source_has_a_guide():
    from dopeiptv.ui.mw_media import _MediaServerMixin

    class W(_MediaServerMixin):
        def __init__(self, epg):
            self.client = SimpleNamespace(live_note="", guide_note=tr("satip_no_guide"))
            self.playlist_store = SimpleNamespace(active=lambda: {"kind": "satip", "epg_url": epg})
            self.said: list[str] = []

        def _ms_client(self):
            return None

        def _set_status(self, text, *a, **k):
            self.said.append(text)
    w = W("")
    w._ms_live_note([{"category_id": "x"}])
    assert w.said == [tr("satip_no_guide")]
    w = W("http://epg.example/guide.xml")
    w._ms_live_note([{"category_id": "x"}])
    assert w.said == []
