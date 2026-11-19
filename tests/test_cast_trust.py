"""What a device on the network can do to the app by saying it is a TV.

Anything on the home network can answer a search - SSDP, mDNS - and call
itself "Living room TV". What it says is taken narrowly:

- where its description and its control addresses are: on a device of the
  home, never on this computer itself or out on the internet, and no
  redirect followed - or the app's requests, and the stream's address with
  the login in it, go wherever the answer points;
- how much it sends: an answer is read to a size and for a time, no more;
- which device a name stands for: the one cast to before is known by its
  own id and address, a newcomer under its name is said with its address
  and never picked for the person.
"""
from __future__ import annotations

import json
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dopeiptv.i18n import LocalizedError
from dopeiptv.providers import dlna, kodi, lan_scan, mdns

LOOP = "127.0.0.1"


class Server:
    """An HTTP server on this computer: *answer(handler)* writes what it
    says; every request is kept."""

    def __init__(self, answer):
        self.seen: list[tuple[str, str, bytes]] = []
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _any(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                srv.seen.append((self.command, self.path, body))
                try:
                    answer(self)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            do_GET = do_POST = _any

        self.httpd = ThreadingHTTPServer((LOOP, 0), H)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.base = f"http://{LOOP}:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _send(h, code, body=b"", headers=()):
    h.send_response(code)
    for k, v in headers:
        h.send_header(k, v)
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def _desc(av="/av", rc="/rc", base="", av_type="urn:schemas-upnp-org:service:AVTransport:1"):
    return (f'<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0">{base}'
            "<device><friendlyName>Living room TV</friendlyName><UDN>uuid:tv-1</UDN><serviceList>"
            f"<service><serviceType>{av_type}</serviceType><controlURL>{av}</controlURL></service>"
            "<service><serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType>"
            f"<controlURL>{rc}</controlURL></service></serviceList></device></root>").encode()


# -- DLNA: where an answer may point ----------------------------------------------------

def _answer(*locations):
    return ("HTTP/1.1 200 OK\r\nST: x\r\n" + "".join(f"LOCATION: {loc}\r\n" for loc in locations)
            + "\r\n").encode()


def test_a_description_is_taken_only_where_a_tv_can_be(monkeypatch):
    monkeypatch.setattr(dlna, "lan_address", lambda: "192.168.1.10")
    take = lambda loc, sender: dlna._from_sender(_answer(loc), sender, [0])   # noqa: E731
    # On the device that answered.
    assert take("http://192.168.1.20:9197/dmr", "192.168.1.20") == ["http://192.168.1.20:9197/dmr"]
    # An SSDP relay answers for a TV on another part of the home network.
    assert take("http://192.168.30.7:9197/dmr", "192.168.1.1") == ["http://192.168.30.7:9197/dmr"]
    # Never on this computer itself: its own services are not a TV's description.
    for own in ("http://127.0.0.1:8080/x", "http://[::1]:8080/x", "http://192.168.1.10:8000/x",
                "http://0.0.0.0/x", "http://localhost:8080/x"):
        assert take(own, "192.168.1.66") == [], own
    # Never outside the home's ranges.
    assert take("http://93.184.216.34/desc.xml", "192.168.1.66") == []
    assert take("http://192.168.30.7:9197/dmr", "93.184.216.34") == []
    # A device with an address outside them answering for itself is still itself.
    assert take("http://93.184.216.34/desc.xml", "93.184.216.34") == ["http://93.184.216.34/desc.xml"]


def test_a_description_named_by_a_host_s_name_is_looked_up_and_only_so_many(monkeypatch):
    asked: list[str] = []

    def addresses(host, timeout=1.5):
        asked.append(host)
        return {"192.168.1.20"} if host == "tv.lan" else {"10.9.9.9"}
    monkeypatch.setattr(dlna, "_addresses", addresses)
    named = [0]
    assert dlna._from_sender(_answer("http://tv.lan:9197/dmr"), "192.168.1.20", named) == \
        ["http://tv.lan:9197/dmr"]
    assert dlna._from_sender(_answer("http://evil.lan/x"), "192.168.1.20", named) == []
    flood = _answer(*[f"http://h{i}.lan/x" for i in range(50)])
    assert dlna._from_sender(flood, "192.168.1.20", named) == []
    assert len(asked) == dlna._NAMED_MAX                    # the rest are not even looked up


def test_a_search_drops_an_answer_that_points_elsewhere():
    resp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    resp.bind((LOOP, 0))
    resp.settimeout(3)

    def answer():
        try:
            _data, addr = resp.recvfrom(4096)
        except socket.timeout:
            return
        resp.sendto(_answer("http://93.184.216.34/steal", f"http://{LOOP}:9197/dmr",
                            "http://[::1]:8080/x"), addr)
    t = threading.Thread(target=answer, daemon=True)
    t.start()
    found = dlna.ssdp_search(0.8, (LOOP, resp.getsockname()[1]))
    t.join(3)
    resp.close()
    assert found == [f"http://{LOOP}:9197/dmr"]            # the one on the answering device


def test_a_description_is_not_followed_to_another_address():
    other = Server(lambda h: _send(h, 200, _desc()))
    tv = Server(lambda h: _send(h, 302, headers=[("Location", other.base + "/desc.xml")]))
    try:
        assert dlna.describe(tv.base + "/desc.xml") is None
        assert other.seen == []                             # nothing asked of where it pointed
    finally:
        tv.close()
        other.close()


def test_a_tv_is_controlled_where_it_is_and_nowhere_else():
    other = Server(lambda h: _send(h, 200, b"<x/>"))
    try:
        # The AVTransport address on another host: this is no TV to cast to.
        tv = Server(lambda h: _send(h, 200, _desc(av=other.base.replace(LOOP, "127.0.0.2") + "/av")))
        assert dlna.describe(tv.base + "/desc.xml") is None
        tv.close()
        # URLBase pointing every control address elsewhere: the same.
        tv = Server(lambda h: _send(h, 200, _desc(base="<URLBase>http://192.168.1.1:5000/</URLBase>")))
        assert dlna.describe(tv.base + "/desc.xml") is None
        tv.close()
        # Only the volume's address elsewhere: a TV without volume control.
        tv = Server(lambda h: _send(h, 200, _desc(rc="http://192.168.1.1:5000/ctl")))
        r = dlna.describe(tv.base + "/desc.xml")
        assert r is not None and r.av_url == tv.base + "/av" and r.rc_url == ""
        tv.close()
        # Another port of the same device is the same device.
        tv = Server(lambda h: _send(h, 200, _desc(av=f"http://{LOOP}:{other.port}/av")))
        r = dlna.describe(tv.base + "/desc.xml")
        assert r is not None and r.av_url == other.base + "/av"
        tv.close()
    finally:
        other.close()


@pytest.mark.parametrize("stype", [
    'urn:schemas-upnp-org:service:AVTransport:1"><evil xmlns="x',
    "urn:schemas-upnp-org:service:AVTransport:1\r\nX-Injected: 1",
    "urn:schemas-upnp-org:service:AVTransport:1#AddPortMapping",
    "urn:schemas-upnp-org:service:AVTransport:",
])
def test_a_service_type_is_only_what_upnp_writes(stype):
    from xml.sax.saxutils import escape
    tv = Server(lambda h: _send(h, 200, _desc(av_type=escape(stype))))
    try:
        assert dlna.describe(tv.base + "/desc.xml") is None
        # Nor from the settings, nor into a request.
        assert dlna.Renderer.from_dict({"name": "TV", "location": tv.base + "/d", "av_url": tv.base + "/av",
                                        "av_type": stype}) is None
        n = len(tv.seen)
        with pytest.raises(dlna.SoapError):
            dlna.soap(tv.base + "/av", stype, "Play", [("InstanceID", 0)])
        assert len(tv.seen) == n                            # nothing was sent
    finally:
        tv.close()
    ok = "urn:schemas-upnp-org:service:AVTransport:2"
    assert dlna.Renderer.from_dict({"name": "TV", "location": "http://h/d", "av_url": "http://h/av",
                                    "av_type": ok}) is not None


def test_a_request_to_a_tv_is_not_passed_on_to_another_address():
    """The request that tells a TV what to play holds the stream's address
    - a provider's login in it. A "TV" that answers with a redirect would
    have it posted wherever it says."""
    other = Server(lambda h: _send(h, 200, b"<x/>"))
    tv = Server(lambda h: _send(h, 307, headers=[("Location", other.base + "/steal")]))
    try:
        with pytest.raises(dlna.SoapError):
            dlna.soap(tv.base + "/av", "urn:schemas-upnp-org:service:AVTransport:1", "SetAVTransportURI",
                      [("InstanceID", 0), ("CurrentURI", "http://iptv.example/live/anna/hunter2/1.ts")])
        assert len(tv.seen) == 1 and other.seen == []
    finally:
        tv.close()
        other.close()


def test_an_answer_that_never_ends_is_not_read_on(monkeypatch):
    class Endless:
        status_code = 200
        closed = False

        def iter_content(self, n):
            while True:
                yield b"x" * n

        def close(self):
            self.closed = True
    r = Endless()
    got = dlna._read_body(r)
    assert len(got) == dlna.MAX_XML + 1 and r.closed       # a byte over: no description
    with pytest.raises(ValueError):
        dlna._parse_xml(got)

    class Slow(Endless):
        def iter_content(self, n):
            while True:
                clock[0] += 1.0
                yield b"x"
    clock = [0.0]
    monkeypatch.setattr(dlna.time, "monotonic", lambda: clock[0])
    slow = Slow()
    assert len(dlna._read_body(slow)) <= dlna.READ_S + 2 and slow.closed

    def big(h):
        h.send_response(200)
        h.end_headers()
        for _ in range(64):                                 # 4 MB of description
            h.wfile.write(b"<x>" + b"a" * 65536)
    monkeypatch.undo()
    tv = Server(big)
    try:
        started = time.monotonic()
        assert dlna.describe(tv.base + "/desc.xml") is None
        assert time.monotonic() - started < 10
    finally:
        tv.close()


# -- mDNS: whose word an address is -----------------------------------------------------

SERVICE = "_xbmc-jsonrpc-h._tcp.local"


def _name(labels):
    return b"".join(bytes([len(p.encode())]) + p.encode() for p in labels) + b"\x00"


def _mdns(instance="Kodi", host="kodipi", ip="192.168.1.20", port=8080, with_srv=True):
    svc = mdns.labels(SERVICE)
    inst, target = (instance,) + svc, (host, "local")

    def rr(name, rtype, rdata):
        return _name(name) + struct.pack("!HHIH", rtype, 0x8001, 120, len(rdata)) + rdata
    records = [rr(svc, mdns.T_PTR, _name(inst))]
    if with_srv:
        records.append(rr(inst, mdns.T_SRV, struct.pack("!HHH", 0, 0, port) + _name(target)))
    if ip:
        records.append(rr(target, mdns.T_A, socket.inet_aton(ip)))
    return struct.pack("!HHHHHH", 0, 0x8400, 0, len(records), 0, 0) + b"".join(records)


def _hosts(*packets):
    return [(i.name, i.host) for i in mdns.instances(list(packets), SERVICE)]


def test_an_answer_cannot_place_a_device_on_this_computer_or_out_on_the_internet():
    # Its own address, or none given: itself.
    assert _hosts((_mdns(ip="192.168.1.20"), "192.168.1.20")) == [("Kodi", "192.168.1.20")]
    assert _hosts((_mdns(ip=""), "192.168.1.20")) == [("Kodi", "192.168.1.20")]
    # A reflector answers for a device on another part of the home network.
    assert _hosts((_mdns(ip="192.168.30.7"), "192.168.1.1")) == [("Kodi", "192.168.30.7")]
    # This computer's own services, a multicast address, a server out on the
    # internet: where the stream's address would go, not where a Kodi is.
    for ip in ("127.0.0.1", "0.0.0.0", "224.0.0.251", "93.184.216.34"):
        assert _hosts((_mdns(ip=ip), "192.168.1.66")) == [], ip
    assert mdns.at_home("192.168.1.20") and mdns.at_home("169.254.3.4")
    assert not mdns.at_home("127.0.0.1") and not mdns.at_home("8.8.8.8") and not mdns.at_home("x")


def test_nobody_else_s_word_for_where_another_s_device_is():
    """The first answer for a host's name used to count for everyone's
    instances: a device on the network could say where the real Kodi is."""
    real = (_mdns(ip="192.168.1.20"), "192.168.1.20")
    liar = (_mdns(instance="Other", host="kodipi", ip="192.168.1.66"), "192.168.1.66")
    for order in ((liar, real), (real, liar)):
        found = dict(_hosts(*order))
        assert found["Kodi"] == "192.168.1.20" and found["Other"] == "192.168.1.66"


def test_a_flood_of_answers_fills_no_memory():
    recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv.bind((LOOP, 0))
    send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    packets: list[tuple[bytes, str]] = []
    stop = threading.Event()

    def flood():
        while not stop.is_set():
            try:
                send.sendto(b"x" * 64, recv.getsockname())
            except OSError:
                return
    t = threading.Thread(target=flood, daemon=True)
    t.start()
    try:
        mdns._collect(recv, time.monotonic() + 1.0, packets)
    finally:
        stop.set()
        t.join(2)
        recv.close()
        send.close()
    assert len(packets) <= mdns.MAX_PACKETS


# -- Kodi and what answers when an address is asked ---------------------------------------

def test_a_request_to_a_kodi_is_not_passed_on_to_another_address():
    other = Server(lambda h: _send(h, 200, b'{"jsonrpc":"2.0","id":1,"result":"OK"}'))
    box = Server(lambda h: _send(h, 307, headers=[("Location", other.base + "/jsonrpc")]))
    try:
        rpc = kodi.KodiRpc(kodi.KodiDevice("Fake", LOOP, box.port))
        with pytest.raises(kodi.KodiError):
            rpc.call("Player.Open", {"item": {"file": "http://iptv.example/live/anna/hunter2/1.ts"}})
        assert len(box.seen) == 1 and other.seen == []
    finally:
        box.close()
        other.close()


def test_a_kodi_s_answer_is_read_to_a_size(monkeypatch):
    monkeypatch.setattr(kodi, "MAX_ANSWER", 4096)
    big = json.dumps({"jsonrpc": "2.0", "id": 1, "result": "x" * 100_000}).encode()
    box = Server(lambda h: _send(h, 200, big))
    ok = Server(lambda h: _send(h, 200, b'{"jsonrpc":"2.0","id":1,"result":"pong"}'))
    try:
        with pytest.raises(kodi.KodiError):
            kodi.KodiRpc(kodi.KodiDevice("Fake", LOOP, box.port)).call("JSONRPC.Ping")
        assert kodi.KodiRpc(kodi.KodiDevice("Real", LOOP, ok.port)).call("JSONRPC.Ping") == "pong"
    finally:
        box.close()
        ok.close()


def test_an_address_asked_what_it_is_is_not_followed_elsewhere(monkeypatch):
    from dopeiptv.core._lazy_requests import requests
    real = Server(lambda h: _send(h, 200, json.dumps(
        {"name": "Kitchen", "device_info": {"ssdp_udn": "u-1"}}).encode()
        if h.command == "GET" else b'{"jsonrpc":"2.0","id":1,"result":"pong"}'))
    away = Server(lambda h: _send(h, 307, headers=[("Location", real.base + h.path)]))
    try:
        with requests.Session() as sess:
            assert lan_scan._chromecast(sess, LOOP, real.port)["name"] == "Kitchen"
            assert lan_scan._kodi(sess, LOOP, real.port)["kind"] == "kodi"
            n = len(real.seen)
            assert lan_scan._chromecast(sess, LOOP, away.port) is None
            assert lan_scan._kodi(sess, LOOP, away.port) is None
            assert len(real.seen) == n
            monkeypatch.setattr(lan_scan, "SHORT_ANSWER", 64)
            long = Server(lambda h: _send(h, 200, json.dumps({"name": "x" * 500}).encode()))
            assert lan_scan._chromecast(sess, LOOP, long.port) is None
            long.close()
    finally:
        real.close()
        away.close()


# -- which device a name stands for ---------------------------------------------------------

class _CC:
    def __init__(self, name, host, uuid="u"):
        self.name, self.uuid = name, uuid
        self.cast_info = type("Info", (), {"host": host})()


def _chromecasts(*devices):
    from dopeiptv.providers.chromecast import ChromecastManager
    m = ChromecastManager()
    m.devices = list(devices)
    return m


def test_two_devices_of_one_name_never_stand_in_for_each_other():
    real, fake = _CC("Living room TV", "192.168.1.20", "u-real"), _CC("Living room TV", "192.168.1.66", "u-fake")
    other = _CC("Kitchen", "192.168.1.21")
    m = _chromecasts(fake, real, other)
    labels = m.labels()
    assert set(labels) == {"Kitchen", "Living room TV (192.168.1.20)", "Living room TV (192.168.1.66)"}
    assert labels["Living room TV (192.168.1.20)"] is real
    assert m._device("Living room TV") is None             # the bare name is nobody's
    assert m._device("Living room TV (192.168.1.66)") is fake and m._device("Kitchen") is other
    assert m.label_of(real) == "Living room TV (192.168.1.20)" and m.label_of(other) == "Kitchen"
    # One device of a name: its name, as it always was.
    assert set(_chromecasts(real, other).labels()) == {"Kitchen", "Living room TV"}


def _hub(cc=None, tvs=None, kodis=None):
    from dopeiptv.providers import casting
    hub = casting.CastHub(cc or _chromecasts(), dlna.DlnaManager(), kodi.KodiManager())
    hub.dlna.renderers = dict(tvs or {})
    hub.kodi.devices = dict(kodis or {})
    return hub


def test_a_device_is_known_by_its_own_id_and_its_address():
    tv = dlna.Renderer("TV", "http://192.168.1.31:9197/dmr", "http://192.168.1.31:9197/av",
                       "urn:schemas-upnp-org:service:AVTransport:1", udn="uuid:tv-1")
    hub = _hub(_chromecasts(_CC("Kitchen", "192.168.1.21", "abc")), {"TV (DLNA)": tv},
               {"Den (Kodi)": kodi.KodiDevice("Den", "192.168.1.40", 8080)})
    assert hub.where("Kitchen") == "192.168.1.21" and hub.ident("Kitchen") == "chromecast:abc@192.168.1.21"
    assert hub.where("TV (DLNA)") == "192.168.1.31" and hub.ident("TV (DLNA)") == "dlna:uuid:tv-1@192.168.1.31"
    assert hub.ident("Den (Kodi)") == "kodi:8080@192.168.1.40"
    assert hub.where("Nobody") == "" and hub.ident("Nobody") == "" and hub.ident(None) == ""


def test_nothing_is_sent_to_another_device_under_a_known_name():
    """Cast to "Kitchen" before; today something else answers to that name."""
    sent: list[tuple] = []
    cc = _chromecasts(_CC("Kitchen", "192.168.1.66", "evil"))
    cc.cast = lambda name, url, title, *a, **k: sent.append((name, url)) or name   # type: ignore[method-assign]
    hub = _hub(cc)
    url = "http://iptv.example/live/anna/hunter2/1.m3u8"
    with pytest.raises(LocalizedError) as e:
        hub.cast("Kitchen", url, "News", expect="chromecast:abc@192.168.1.21")
    assert e.value.key == "cast_other_device" and "192.168.1.66" in str(e.value)
    assert sent == []
    # The device it was: cast to as ever. And one picked knowingly (no *expect*).
    assert hub.cast("Kitchen", url, "News", expect="chromecast:evil@192.168.1.66") == "Kitchen"
    assert hub.cast("Kitchen", url, "News") == "Kitchen"
    assert sent == [("Kitchen", url)] * 2


def test_a_device_not_met_in_this_run_is_looked_for_before_it_is_judged():
    cc = _chromecasts()
    found = _CC("Kitchen", "192.168.1.66", "evil")
    cc.scan = lambda: cc.devices.append(found) or ["Kitchen"]     # type: ignore[method-assign]
    sent: list = []
    cc.cast = lambda name, url, title, *a, **k: sent.append(name) or name      # type: ignore[method-assign]
    hub = _hub(cc)
    with pytest.raises(LocalizedError):
        hub.cast("Kitchen", "http://x/1.m3u8", "News", expect="chromecast:abc@192.168.1.21")
    assert sent == [] and cc.devices == [found]


# -- the cast window --------------------------------------------------------------------------

class _FakeHub:
    """A hub whose devices are what a test says they are."""

    def __init__(self, devices):
        self.devices = dict(devices)            # name -> (ident, address)
        self.casts: list[tuple] = []
        self.added: list = []

    def scan(self):
        return sorted(self.devices)

    def ident(self, name):
        return self.devices.get(name, ("", ""))[0]

    def where(self, name):
        return self.devices.get(name, ("", ""))[1]

    def cast(self, name, url, title, *a, **k):
        self.casts.append((name, k.get("expect", "")))
        return name

    def plays_as_is(self, name):
        return False

    def is_kodi(self, name):
        return False

    def is_airplay(self, name):
        return False


@pytest.fixture
def window():
    pytest.importorskip("PyQt6.QtWidgets")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QSettings, QThreadPool
    from PyQt6.QtWidgets import QApplication, QWidget
    app = QApplication.instance() or QApplication([])
    win = QWidget()
    win.settings = QSettings("dopeiptv-test", "cast-trust")
    win.settings.clear()
    win.pool, win._cast_device, win._cast_ctx = QThreadPool(), None, {}
    win.app = app
    yield win
    win.settings.clear()


def _dialog(window, hub):
    from dopeiptv.ui import cast_dialog
    window.cast = hub
    d = cast_dialog.CastDialog(window, "http://a/1.m3u8", "News", tracks={"audio": []},
                               probe=False, live=True)
    window.pool.waitForDone(3000)
    for _ in range(5):
        window.app.processEvents()
    return d


def _settle(window):
    window.pool.waitForDone(3000)
    for _ in range(5):
        window.app.processEvents()


def test_the_tv_cast_to_before_is_picked_only_while_it_is_the_same_device(window):
    from dopeiptv.i18n import tr
    from dopeiptv.ui import cast_dialog
    real = ("chromecast:abc@192.168.1.21", "192.168.1.21")
    hub = _FakeHub({"Kitchen": real, "Bedroom": ("chromecast:b@192.168.1.22", "192.168.1.22")})
    d = _dialog(window, hub)
    assert d.list.item(0).toolTip() in ("192.168.1.22", "192.168.1.21")    # which device a name is
    d.list.setCurrentRow([d.list.item(i).text() for i in range(d.list.count())].index("Kitchen"))
    d._cast()
    _settle(window)
    assert hub.casts == [("Kitchen", "")]                   # never cast to before: nothing to compare
    assert cast_dialog.known_ident(window.settings, "Kitchen") == real[0]
    d.close()
    # The same device next time: on top, picked, and cast to with what it was.
    d = _dialog(window, hub)
    assert d.list.currentItem().text() == "Kitchen" and d._strangers == set()
    d._cast()
    _settle(window)
    assert hub.casts[-1] == ("Kitchen", real[0])
    d.close()
    # Another device answers to "Kitchen": listed, said, never picked for the person.
    hub.devices["Kitchen"] = ("chromecast:evil@192.168.1.66", "192.168.1.66")
    d = _dialog(window, hub)
    assert d._strangers == {"Kitchen"} and d.list.currentItem() is None
    note = tr("cast_other_device", name="Kitchen", address="192.168.1.66")
    assert note in d.status.text()
    n = len(hub.casts)
    d._cast()                                               # Enter: nothing is picked, nothing sent
    _settle(window)
    assert len(hub.casts) == n
    # Picked knowingly, its address said: cast to, and known as this from now on.
    d.list.setCurrentRow([d.list.item(i).text() for i in range(d.list.count())].index("Kitchen"))
    assert d.status.text() == note
    d._cast()
    _settle(window)
    assert hub.casts[-1] == ("Kitchen", "")
    assert cast_dialog.known_ident(window.settings, "Kitchen") == "chromecast:evil@192.168.1.66"
    d.close()


def test_a_cast_before_the_search_is_done_names_the_device_it_expects(window):
    """The list shows last time's names at once; Enter then casts to a name
    the search has not confirmed yet - the hub is told which device that
    name was, and sends nothing to another."""
    from dopeiptv.ui import cast_dialog
    hub = _FakeHub({})
    window.cast = hub
    window.settings.setValue("cast_devices", "Kitchen")
    cast_dialog.remember_device(window.settings, True, "Kitchen", "chromecast:abc@192.168.1.21")
    # Opened without a search (as on a running cast): last time's names only.
    d = cast_dialog.CastDialog(window, "http://a/1.m3u8", "News", tracks={"audio": []},
                               probe=False, live=True, managing=True)
    assert d.list.currentItem().text() == "Kitchen"
    d._cast()
    _settle(window)
    assert hub.casts == [("Kitchen", "chromecast:abc@192.168.1.21")]
    d.close()


def test_only_so_many_devices_are_remembered(window):
    from dopeiptv.ui import cast_dialog
    for i in range(cast_dialog.KNOWN_MAX + 10):
        cast_dialog.remember_device(window.settings, False, f"TV {i}", f"dlna:u{i}@10.0.0.{i}")
    assert cast_dialog.known_ident(window.settings, "TV 0") == ""
    assert cast_dialog.known_ident(window.settings, f"TV {cast_dialog.KNOWN_MAX + 9}") != ""
    assert cast_dialog.last_device(window.settings, False) == f"TV {cast_dialog.KNOWN_MAX + 9}"
    # What the app kept before it knew devices apart still reads.
    window.settings.setValue(cast_dialog.LAST_DEVICE_KEY, json.dumps({"any": "Old TV"}))
    assert cast_dialog.last_device(window.settings, True) == "Old TV"
    assert cast_dialog.known_ident(window.settings, "Old TV") == ""


def test_a_tv_the_router_moved_is_still_that_tv():
    """A device is one by its own id where it has one: a new address from
    the router does not make it a stranger; a new id under the same name
    does. Without an id (a Kodi added by hand) the address decides."""
    from dopeiptv.ui.cast_dialog import same_device
    assert same_device("chromecast:uuid-1@192.168.1.20", "chromecast:uuid-1@192.168.1.31")
    assert not same_device("chromecast:uuid-1@192.168.1.20", "chromecast:uuid-2@192.168.1.20")
    assert not same_device("chromecast:uuid-1@192.168.1.20", "dlna:uuid-1@192.168.1.20")
    assert same_device("kodi:@192.168.1.9", "kodi:@192.168.1.9")
    assert not same_device("kodi:@192.168.1.9", "kodi:@192.168.1.10")
