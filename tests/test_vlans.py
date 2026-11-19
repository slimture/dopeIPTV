"""A home network split in VLANs: what crosses to the other parts is a
question to one address, so the other networks Nerd lists are swept by
unicast (providers/lan_scan.py) - boxes and media servers for "Find on my
network", Chromecasts, Kodis and DLNA TVs for the cast window - and a
device can be added by its address (providers/cast_devices.py), found out
per kind, kept, listed by CastHub beside what the searches find, after a
restart too, and cast to as to a found one. With nothing found, the window
says why a split network hides devices and what fixes it. Fake servers on
this computer's loopback stand in for the devices."""
from __future__ import annotations

import json
import socket
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dopeiptv.core import home_net
from dopeiptv.i18n import LocalizedError, tr
from dopeiptv.providers import cast_devices, casting, dlna, kodi, lan_scan

LOOP = "127.0.0.1"
RENDERER_XML = b"""<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0"><device>
<friendlyName>Bedroom TV</friendlyName><UDN>uuid:tv-1</UDN>
<serviceList><service><serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>
<controlURL>/avt</controlURL></service>
<service><serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType>
<controlURL>/rc</controlURL></service></serviceList></device></root>"""


class FakeSettings:
    def __init__(self, **kw):
        self.d = dict(kw)

    def value(self, key, default=None):
        return self.d.get(key, default)

    def setValue(self, key, value):  # noqa: N802
        self.d[key] = value


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy",
                "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)


class Fake:
    """An HTTP server on the loopback answering *routes*: path -> (status,
    body bytes or a JSON-able, headers). POST answers from the same table,
    keyed "POST <path>"."""

    def __init__(self, routes: dict) -> None:
        self.routes = routes
        self.asked: list[str] = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _answer(self, key: str) -> None:
                fake.asked.append(key)
                path = key.split("?", 1)[0]
                got = fake.routes.get(path)
                if got is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status, body, headers = got
                if not isinstance(body, bytes):
                    body = json.dumps(body).encode()
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                self._answer(self.path)

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                self._answer("POST " + self.path)
        self.httpd = ThreadingHTTPServer((LOOP, 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def fake():
    made: list[Fake] = []

    def make(routes):
        f = Fake(routes)
        made.append(f)
        return f
    yield make
    for f in made:
        f.close()


class SsdpResponder:
    """Answers a unicast M-SEARCH as a renderer does, from the loopback."""

    def __init__(self, location: str) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((LOOP, 0))
        self.sock.settimeout(3)
        self.port = self.sock.getsockname()[1]
        self.location = location
        self.asked: list[bytes] = []
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            while True:
                data, addr = self.sock.recvfrom(4096)
                self.asked.append(data)
                self.sock.sendto(("HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=1800\r\n"
                                  f"LOCATION: {self.location}\r\n"
                                  "ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n\r\n").encode(), addr)
        except OSError:
            pass

    def close(self) -> None:
        self.sock.close()


def kodi_routes(name="Den"):
    return {"POST /jsonrpc": (200, {"jsonrpc": "2.0", "id": 1, "result": "pong"}, {})}


def chromecast_routes(name="Living room"):
    return {"/setup/eureka_info": (200, {"name": name, "device_info": {
        "name": name, "model_name": "Chromecast Ultra", "ssdp_udn": "8a3c6c3e-1a2b-4c5d-9e8f-0123456789ab"}}, {})}


def closed_port() -> int:
    s = socket.socket()
    s.bind((LOOP, 0))
    port = s.getsockname()[1]
    s.close()
    return port


# -- the sweep -----------------------------------------------------------------------------

def test_the_sweep_finds_what_takes_a_connection(fake):
    a, b = fake({}), fake({})
    shut = closed_port()
    got = lan_scan.open_ports([(LOOP, a.port), (LOOP, shut), (LOOP, b.port)], limit=2)
    assert got == {(LOOP, a.port), (LOOP, b.port)}
    stop = threading.Event()
    stop.set()
    assert lan_scan.open_ports([(LOOP, a.port)], cancel=stop) == set()


def test_find_on_my_network_asks_the_other_networks_by_unicast(fake, monkeypatch):
    plex = fake({"/identity": (200, {"MediaContainer": {"machineIdentifier": "abc"}}, {})})
    jf = fake({"/System/Info/Public": (200, {"Id": "j1", "ServerName": "Basement",
                                              "ProductName": "Jellyfin Server"}, {})})
    shut = closed_port()
    ports = {"enigma2": shut, "tvheadend": shut, "hdhomerun": shut, "jellyfin": jf.port,
             "plex": plex.port}
    tried: list[tuple[str, int]] = []
    real = lan_scan._open

    def no_one_by_one(host, port, timeout=lan_scan.CONNECT):
        tried.append((host, port))
        return real(host, port, timeout)
    monkeypatch.setattr(lan_scan, "_open", no_one_by_one)
    # As Nerd > Network lists them: read when the search starts.
    monkeypatch.setattr(home_net, "configured", lambda *a: [LOOP])
    stop = threading.Event()
    progress: list[int] = []
    got = lan_scan.scan(progress=progress.append, hosts=[], ports=ports, broadcast=False,
                        cancel=stop)
    assert {(d["kind"], d["server"]) for d in got} == {
        ("plex", f"http://{LOOP}:{plex.port}"), ("jellyfin", f"http://{LOOP}:{jf.port}")}
    # Swept at once: no port was tried one at a time afterwards.
    assert tried == [] and progress[-1] == 100
    # Stopped before it starts: nothing asked.
    stop.set()
    assert lan_scan.scan(hosts=[], ports=ports, broadcast=False, extra=[LOOP], cancel=stop) == []
    # The local network's own addresses are not asked twice.
    asked_before = len(plex.asked)
    lan_scan.scan(hosts=[LOOP], ports=ports, broadcast=False, extra=[LOOP])
    assert len(plex.asked) == asked_before + 1


def test_the_cast_window_sweep_finds_each_kind(fake):
    tv = fake({"/desc.xml": (200, RENDERER_XML, {"Content-Type": "text/xml"})})
    ssdp = SsdpResponder(f"http://{LOOP}:{tv.port}/desc.xml")
    cc = fake(chromecast_routes())
    kd = fake(kodi_routes())
    try:
        got = lan_scan.scan_cast([LOOP], ports={"chromecast": cc.port, "kodi": kd.port},
                                 ssdp_port=ssdp.port)
    finally:
        ssdp.close()
    kinds = {d["kind"]: d for d in got}
    assert set(kinds) == {"chromecast", "kodi", "dlna"}
    assert kinds["chromecast"]["name"] == "Living room" and kinds["chromecast"]["port"] == 8009
    assert kinds["chromecast"]["model"] == "Chromecast Ultra"
    assert kinds["kodi"] == {"kind": "kodi", "host": LOOP, "port": kd.port, "name": f"Kodi ({LOOP})"}
    r = kinds["dlna"]["renderer"]
    assert r.name == "Bedroom TV" and r.av_url == f"http://{LOOP}:{tv.port}/avt"
    # A unicast search names the device itself, not the multicast group.
    assert ssdp.asked and f"HOST: {LOOP}:{ssdp.port}".encode() in ssdp.asked[0]


def test_a_kodi_that_wants_a_login_and_one_that_is_not_kodi(fake):
    locked = fake({"POST /jsonrpc": (401, b"", {"WWW-Authenticate": 'Basic realm="XBMC"'})})
    other = fake({"POST /jsonrpc": (401, b"", {"WWW-Authenticate": 'Basic realm="router"'})})
    got = lan_scan.probe_cast(LOOP, ports={"kodi": locked.port, "chromecast": closed_port()},
                              kinds=("kodi",))
    assert got == [{"kind": "kodi", "host": LOOP, "port": locked.port, "name": f"Kodi ({LOOP})",
                    "login": True}]
    assert lan_scan.probe_cast(LOOP, ports={"kodi": other.port}, kinds=("kodi",)) == []


def test_the_nerd_page_test_searches_and_says_what_answered(monkeypatch):
    monkeypatch.setattr(lan_scan, "scan", lambda **kw: [{"kind": "plex", "server": "http://x",
                                                         "name": "Plex - 192.168.20.5"}])
    monkeypatch.setattr(lan_scan, "scan_cast", lambda hosts, **kw: [
        {"kind": "kodi", "host": "192.168.30.7", "port": 8080, "name": "Den"}])
    check = lan_scan.check_networks("192.168.20.0/30")
    assert check.state == "ok" and check.params["n"] == 2
    assert "Plex - 192.168.20.5" in check.params["names"] and "Den" in check.params["names"]
    monkeypatch.setattr(lan_scan, "scan", lambda **kw: [])
    monkeypatch.setattr(lan_scan, "scan_cast", lambda hosts, **kw: [])
    check = lan_scan.check_networks("192.168.20.0/30")
    assert check.state == "warn" and check.key == "nerd_ck_ln_nothing" and check.params["n"] == 2
    assert lan_scan.check_networks("8.8.8.8").key == "nerd_ck_ln_public"


# -- adding a device by its address --------------------------------------------------------

def test_the_address_typed():
    assert cast_devices.parse_address("192.168.20.31") == ("192.168.20.31", 0, "")
    assert cast_devices.parse_address(" 192.168.20.31:8080 ") == ("192.168.20.31", 8080, "")
    assert cast_devices.parse_address("tv.lan") == ("tv.lan", 0, "")
    assert cast_devices.parse_address("[fd00::5]:8008") == ("fd00::5", 8008, "")
    assert cast_devices.parse_address("http://192.168.20.31:9197/dmr") == (
        "192.168.20.31", 9197, "http://192.168.20.31:9197/dmr")
    for bad in ("", "a b", "ftp://x", "http://user@x", "http://", "-x", "x;y"):
        assert cast_devices.parse_address(bad) is None, bad


def test_each_kind_is_found_out_at_its_address(fake):
    cc = fake(chromecast_routes("Kitchen"))
    kd = fake(kodi_routes())
    tv = fake({"/dmr": (200, RENDERER_XML, {})})
    shut = closed_port()
    got = cast_devices.detect(LOOP, ports={"chromecast": cc.port, "kodi": shut})
    assert got["kind"] == "chromecast" and got["name"] == "Kitchen"
    got = cast_devices.detect(f"{LOOP}:{kd.port}", ports={"chromecast": shut})
    assert got["kind"] == "kodi" and got["port"] == kd.port
    got = cast_devices.detect(f"http://{LOOP}:{tv.port}/dmr")
    assert got["kind"] == "dlna" and got["renderer"].name == "Bedroom TV"
    # Asked as the kind chosen only.
    with pytest.raises(LocalizedError) as e:
        cast_devices.detect(f"{LOOP}:{kd.port}", kind="chromecast",
                            ports={"chromecast": shut})
    assert e.value.key == "cast_addr_nothing"
    with pytest.raises(LocalizedError) as e:
        cast_devices.detect(f"http://{LOOP}:{tv.port}/dmr", kind="kodi")
    assert e.value.key == "cast_addr_nothing"
    with pytest.raises(LocalizedError) as e:
        cast_devices.detect(f"http://{LOOP}:{cc.port}/setup/eureka_info")
    assert e.value.key == "cast_addr_not_dlna"
    with pytest.raises(LocalizedError) as e:
        cast_devices.detect("not an address")
    assert e.value.key == "cast_addr_bad"


def test_a_dlna_tv_by_its_unicast_search(fake):
    tv = fake({"/desc.xml": (200, RENDERER_XML, {})})
    ssdp = SsdpResponder(f"http://{LOOP}:{tv.port}/desc.xml")
    try:
        got = cast_devices.detect(LOOP, kind="dlna", ssdp_port=ssdp.port)
    finally:
        ssdp.close()
    assert got["renderer"].location == f"http://{LOOP}:{tv.port}/desc.xml"
    # No answer to the search: the paths renderers keep their description at.
    tv2 = fake({"/dmr": (200, RENDERER_XML, {})})
    got = lan_scan.dlna_at(LOOP, ssdp_port=closed_port(), paths=((tv2.port, "/dmr"),),
                           timeout=0.3)
    assert got is not None and got["renderer"].name == "Bedroom TV"


# -- kept, listed and cast to ------------------------------------------------------------------

class _FakeCc:
    def __init__(self, name, host):
        self.name = name
        self.cast_info = types.SimpleNamespace(host=host)

    def disconnect(self, timeout=2):
        pass


def _fake_pc(found=()):
    made = []

    def get_chromecast_from_host(host, tries=None, retry_wait=None, timeout=None):
        made.append(host)
        return _FakeCc(host[4], host[0])

    class Browser:
        def stop_discovery(self):
            pass
    pc = types.SimpleNamespace(get_chromecasts=lambda timeout=6: (list(found), Browser()),
                               get_chromecast_from_host=get_chromecast_from_host)
    return pc, made


def _hub(monkeypatch, settings, found=()):
    from dopeiptv.providers import chromecast
    pc, made = _fake_pc(found)
    monkeypatch.setattr(chromecast, "_pychromecast", pc)
    monkeypatch.setattr(chromecast, "_pc_checked", True)
    monkeypatch.setattr(dlna, "ssdp_search", lambda timeout=3.0: [])
    monkeypatch.setattr(kodi.mdns, "browse", lambda *a, **k: [])
    hub = casting.CastHub(chromecast.ChromecastManager(), dlna.DlnaManager(),
                          kodi.KodiManager(settings), settings=settings)
    return hub, made


def test_devices_added_by_address_are_listed_kept_and_cast_to(fake, monkeypatch):
    cc = fake(chromecast_routes("Attic"))
    tv = fake({"/dmr": (200, RENDERER_XML, {})})
    kd = fake(kodi_routes())
    s = FakeSettings()
    hub, made = _hub(monkeypatch, s)
    monkeypatch.setattr(lan_scan, "CAST_PORTS", {"chromecast": cc.port, "kodi": closed_port()})
    assert hub.add_by_address(LOOP, "chromecast") == "Attic"
    assert hub.add_by_address(f"http://{LOOP}:{tv.port}/dmr") == "Bedroom TV (DLNA)"
    assert hub.add_by_address(f"{LOOP}:{kd.port}", "kodi") == f"Kodi ({LOOP}) (Kodi)"
    kept = json.loads(s.d[cast_devices.SETTINGS_KEY])
    assert [d["kind"] for d in kept] == ["chromecast", "dlna"]
    assert json.loads(s.d[kodi.SETTINGS_KEY])[0]["port"] == kd.port      # with the other Kodis
    names = hub.scan()
    assert {"Attic", "Bedroom TV (DLNA)", f"Kodi ({LOOP}) (Kodi)"} <= set(names)
    assert hub.is_added("Attic") and hub.is_added("Bedroom TV (DLNA)")
    assert not hub.is_added(f"Kodi ({LOOP}) (Kodi)") and not hub.is_added("Elsewhere")
    assert hub.is_dlna("Bedroom TV (DLNA)") and hub._for("Attic") is hub.chromecast
    # A restart: read back from the settings, listed while nothing answers.
    tv.close()
    hub2, _made = _hub(monkeypatch, s)
    names = hub2.scan()
    assert {"Attic", "Bedroom TV (DLNA)"} <= set(names)
    assert hub2.chromecast._device("Attic").cast_info.host == LOOP
    # Cast to as to a found one: the renderer kept is the one asked.
    sent: list[tuple] = []
    monkeypatch.setattr(dlna.DlnaManager, "_set_uri", lambda self, url, meta: sent.append((self.active, url)))
    monkeypatch.setattr(dlna.DlnaManager, "_call", lambda self, *a, **k: {})
    monkeypatch.setattr(dlna.DlnaManager, "_wait_for_playing", lambda self: True)
    monkeypatch.setattr(dlna.DlnaManager, "_start_polling", lambda self: None)
    assert hub2.cast("Bedroom TV (DLNA)", "http://provider/live/1.ts", "Channel") == "Bedroom TV (DLNA)"
    assert sent[0][0].av_url == f"http://{LOOP}:{tv.port}/avt"
    # Forgotten: out of the settings and the list.
    hub2.stop()
    assert hub2.forget_added(hub2.added_key("Bedroom TV (DLNA)"))
    assert "Bedroom TV (DLNA)" not in hub2.scan()
    assert [d.kind for d in cast_devices.load(s)] == ["chromecast"]
    assert not hub2.forget_added("dlna:nothing-like-it")


def test_one_mdns_finds_is_not_listed_twice(monkeypatch):
    s = FakeSettings()
    cast_devices.remember(s, cast_devices.Added("chromecast", "10.0.20.5", "Attic", 8009))
    hub, made = _hub(monkeypatch, s, found=[_FakeCc("Attic speaker", "10.0.20.5")])
    assert hub.scan().count("Attic speaker") == 1 and "Attic" not in hub.scan()
    assert made == []


def test_what_answers_on_the_other_networks_joins_the_list(monkeypatch):
    s = FakeSettings()
    hub, _made = _hub(monkeypatch, s)
    r = dlna.Renderer("Hall TV", "http://10.0.30.4:9197/dmr", "http://10.0.30.4:9197/avt",
                      "urn:schemas-upnp-org:service:AVTransport:1")
    monkeypatch.setattr(home_net, "configured", lambda *a: ["10.0.30.4", "10.0.30.9"])
    monkeypatch.setattr(lan_scan, "scan_cast", lambda hosts: [
        {"kind": "dlna", "host": "10.0.30.4", "location": r.location, "name": r.name, "renderer": r},
        {"kind": "kodi", "host": "10.0.30.9", "port": 8080, "name": "Den"},
        {"kind": "chromecast", "host": "10.0.30.7", "port": 8009, "name": "Hall", "uuid": ""}])
    names = hub.scan()
    assert {"Hall TV (DLNA)", "Den (Kodi)", "Hall"} <= set(names)
    assert not hub.is_added("Hall TV (DLNA)")           # found, not kept
    assert cast_devices.SETTINGS_KEY not in s.d and kodi.SETTINGS_KEY not in s.d
    # Nerd off (or the list emptied): they go at the next search.
    monkeypatch.setattr(home_net, "configured", lambda *a: [])
    assert "Den (Kodi)" not in hub.scan()


def test_a_kodi_that_wants_a_login_is_sent_to_its_own_form(monkeypatch):
    hub, _made = _hub(monkeypatch, FakeSettings())
    monkeypatch.setattr(cast_devices, "detect", lambda text, kind="auto": {
        "kind": "kodi", "host": "10.0.0.5", "port": 8080, "name": "Kodi", "login": True})
    with pytest.raises(LocalizedError) as e:
        hub.add_by_address("10.0.0.5")
    assert e.value.key == "cast_addr_kodi_login"


def test_stored_entries_that_make_no_sense_are_left_out():
    s = FakeSettings(cast_added_devices=json.dumps([
        {"kind": "dlna", "host": "x", "renderer": {"av_url": "file:///etc/passwd",
                                                   "av_type": "urn:schemas-upnp-org:service:AVTransport:1"}},
        {"kind": "airplay", "host": "x"}, {"kind": "chromecast"}, "nonsense",
        {"kind": "chromecast", "host": "10.0.0.9", "name": "Ok\x00ay"}]))
    got = cast_devices.load(s)
    assert [(d.kind, d.host, d.name) for d in got] == [("chromecast", "10.0.0.9", "Okay")]
    assert cast_devices.load(FakeSettings(cast_added_devices="{not json")) == []


# -- when nothing is found ----------------------------------------------------------------------

def test_nothing_found_says_why_and_what_helps():
    pytest.importorskip("PyQt6")
    from dopeiptv.ui.cast_dialog import none_found_text
    from dopeiptv.ui.box_finder import nothing_found_text
    why = tr("vlan_nothing_found")
    for words in ("VLAN", "mDNS", "Avahi", "Multicast DNS", "UniFi", "SSDP", "UPnP", "IGMP"):
        assert words in why, words
    cast = none_found_text()
    assert tr("cast_none_found") in cast and why in cast and tr("cast_add_by_address") in cast
    boxes = nothing_found_text()
    assert tr("playlist_nothing_found") in boxes and why in boxes


def test_the_windows_show_it(monkeypatch):
    pytest.importorskip("PyQt6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QThreadPool
    from PyQt6.QtWidgets import QApplication, QWidget

    from dopeiptv.ui import cast_dialog
    from dopeiptv.ui.box_finder import FindOnNetwork
    app = QApplication.instance() or QApplication([])

    class Empty:
        active = None

        def scan(self):
            return []

        def knows(self, name):
            return False
    monkeypatch.setattr(casting.ChromecastManager, "available", staticmethod(lambda: True))
    hub = casting.CastHub(Empty(), Empty(), Empty())
    win = QWidget()
    win.settings = FakeSettings()
    win.cast, win.pool, win._cast_device = hub, QThreadPool(), None
    win.add_cast_device = lambda parent, current: None
    d = cast_dialog.CastDialog(win, "http://a", "A", probe=False)
    win.pool.waitForDone(5000)
    for _ in range(20):
        app.processEvents()
    assert tr("vlan_nothing_found") in d.status.text()
    assert not d.addr_btn.isHidden()
    d.close()
    btn = FindOnNetwork()
    btn._busy = True
    btn._done([])
    assert tr("vlan_nothing_found") in btn.status.text() and not btn._busy
    # Pressed again while it searches: the search is told to stop.
    btn._busy = True
    btn._start()
    assert btn._cancel.is_set()


def test_the_cast_window_adds_and_forgets_by_address(monkeypatch):
    pytest.importorskip("PyQt6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QThreadPool
    from PyQt6.QtWidgets import QApplication, QWidget

    from dopeiptv.ui import cast_dialog
    app = QApplication.instance() or QApplication([])

    class Mgr:
        active = None

        def __init__(self, names):
            self.names = names

        def scan(self):
            return list(self.names)

        def knows(self, name):
            return False
    monkeypatch.setattr(casting.ChromecastManager, "available", staticmethod(lambda: True))
    hub = casting.CastHub(Mgr(["Kitchen"]), Mgr([]), Mgr([]))
    added = {"Attic"}
    monkeypatch.setattr(hub, "is_added", lambda name: name in added)
    win = QWidget()
    win.settings = FakeSettings()
    win.cast, win.pool, win._cast_device = hub, QThreadPool(), None
    answers: list = []
    asked: list = []

    def add_cast_device(parent, current):
        asked.append(current)
        return answers.pop(0)
    win.add_cast_device = add_cast_device
    d = cast_dialog.CastDialog(win, "http://a", "A", probe=False)
    win.pool.waitForDone(5000)
    app.processEvents()
    answers.append("Attic")
    d._add_by_address()
    assert d.list.currentItem().text() == "Attic" and asked == [None]
    assert win.settings.value("cast_devices").split("\n") == ["Kitchen", "Attic"]
    # Opened on the one added that way, and forgotten.
    answers.append("")
    d._add_by_address()
    assert asked[-1] == "Attic"
    assert [d.list.item(i).text() for i in range(d.list.count())] == ["Kitchen"]
    # Opened on a found one: a new device beside it, nothing taken away.
    d.list.setCurrentRow(0)
    answers.append("Hall")
    d._add_by_address()
    assert asked[-1] is None
    assert [d.list.item(i).text() for i in range(d.list.count())] == ["Kitchen", "Hall"]
    d.close()


def test_the_address_dialog_checks_before_it_keeps():
    pytest.importorskip("PyQt6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QThreadPool, QTimer
    from PyQt6.QtWidgets import QApplication, QDialog, QWidget

    from dopeiptv.ui.mw_cast import _AddressDialog
    app = QApplication.instance() or QApplication([])
    asked: list = []

    class Hub:
        def add_by_address(self, text, kind):
            asked.append((text, kind))
            if text == "10.0.0.9":
                raise LocalizedError("cast_addr_nothing", address=text)
            return "Attic"

        def added_key(self, name):
            return "chromecast:10.0.0.5" if name == "Attic" else ""

        def forget_added(self, key):
            asked.append(("forget", key))
            return True
    parent = QWidget()
    pool = QThreadPool()
    d = _AddressDialog(parent, Hub(), None, pool)
    assert d.forget_btn is None
    d.show()
    d.address.setText("not an address")
    d._check()
    assert d.status.text() == tr("cast_addr_bad") and not asked
    d.address.setText("10.0.0.9")
    d.kind.setCurrentIndex(d.kind.findData("chromecast"))
    d._check()
    pool.waitForDone(3000)
    for _ in range(20):
        app.processEvents()
    assert asked[-1] == ("10.0.0.9", "chromecast")
    assert d.status.text() == tr("cast_addr_nothing", address="10.0.0.9") and d.ok_btn.isEnabled()
    d.address.setText("10.0.0.5")
    d._check()
    pool.waitForDone(3000)
    for _ in range(20):
        app.processEvents()
    assert d.result() == QDialog.DialogCode.Accepted and d.result_label == "Attic"
    d2 = _AddressDialog(parent, Hub(), "Attic", pool)
    d2.show()
    QTimer.singleShot(0, d2.forget_btn.click)
    app.processEvents()
    assert d2.result_label == "" and asked[-1] == ("forget", "chromecast:10.0.0.5")


# -- an HDHomeRun by its address -----------------------------------------------------------------

def test_an_hdhomerun_emulator_that_names_the_loopback_is_reached_at_its_address():
    from dopeiptv.providers.hdhomerun import HDHomeRunClient
    c = HDHomeRunClient("http://192.168.30.40:5004")
    assert c._reachable("http://127.0.0.1:5004/auto/v2.1") == "http://192.168.30.40:5004/auto/v2.1"
    assert c._reachable("http://0.0.0.0:34400/stream/7") == "http://192.168.30.40:34400/stream/7"
    # A real tuner names its own address: kept, on whichever part of the network.
    assert c._reachable("http://10.0.20.5:5004/auto/v4.1") == "http://10.0.20.5:5004/auto/v4.1"
    assert HDHomeRunClient("http://127.0.0.1")._reachable(
        "http://127.0.0.1:5004/auto/v1") == "http://127.0.0.1:5004/auto/v1"
