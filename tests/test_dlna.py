"""Casting to DLNA / UPnP TVs: finding them, what they are told, and the
hub that puts them beside the Chromecasts. A stand-in TV - an HTTP server
answering SOAP like a renderer - plays the part of the television."""
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from dopeiptv.i18n import LocalizedError
from dopeiptv.providers import dlna

_DESC = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
 <device>
  <deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
  <friendlyName>Living Room TV</friendlyName>
  <UDN>uuid:tv-1</UDN>
  <serviceList>
   <service>
    <serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType>
    <controlURL>/RenderingControl/control</controlURL>
   </service>
   <service>
    <serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>
    <controlURL>AVTransport/control</controlURL>
   </service>
  </serviceList>
 </device>
</root>"""


def _reply(action, values):
    body = "".join(f"<{k}>{v}</{k}>" for k, v in values.items())
    return (
        '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.'
        'org/soap/envelope/"><s:Body><u:' + action + 'Response xmlns:u="urn:'
        'schemas-upnp-org:service:AVTransport:1">' + body + "</u:" + action
        + "Response></s:Body></s:Envelope>")


_FAULT = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap'
          '.org/soap/envelope/"><s:Body><s:Fault><detail><UPnPError xmlns="'
          'urn:schemas-upnp-org:control-1-0"><errorCode>{code}</errorCode>'
          '<errorDescription>{text}</errorDescription></UPnPError></detail>'
          "</s:Fault></s:Body></s:Envelope>")


class FakeTV:
    """A renderer: remembers every action, and plays along."""

    def __init__(self):
        self.calls = []
        self.state = "NO_MEDIA_PRESENT"
        self.status = "OK"
        self.rel = "0:00:00"
        self.volume, self.mute = 30, "0"
        self.refuse_metadata = False
        self.start_playing = True
        tv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path != "/desc.xml":
                    self.send_error(404)
                    return
                data = _DESC.encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n).decode()
                action = self.headers["SOAPAction"].strip('"').split("#")[1]
                tv.calls.append((self.path, action, body))
                code, out = tv.act(action, body)
                data = out.encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def act(self, action, body):
        if action == "SetAVTransportURI":
            if self.refuse_metadata and "DIDL-Lite" in body:
                return 500, _FAULT.format(code=714, text="Illegal MIME")
            self.state = "STOPPED"
            return 200, _reply(action, {})
        if action == "Play":
            if self.start_playing:
                self.state = "PLAYING"
            else:
                self.status = "ERROR_OCCURRED"
            return 200, _reply(action, {})
        if action == "Pause":
            self.state = "PAUSED_PLAYBACK"
            return 200, _reply(action, {})
        if action == "Stop":
            self.state = "STOPPED"
            return 200, _reply(action, {})
        if action == "Seek":
            self.rel = body.split("<Target>")[1].split("<")[0]
            return 200, _reply(action, {})
        if action == "GetTransportInfo":
            return 200, _reply(action, {
                "CurrentTransportState": self.state,
                "CurrentTransportStatus": self.status,
                "CurrentSpeed": "1"})
        if action == "GetPositionInfo":
            return 200, _reply(action, {"Track": "1", "RelTime": self.rel,
                                        "TrackDuration": "1:00:00"})
        if action == "GetVolume":
            return 200, _reply(action, {"CurrentVolume": self.volume})
        if action == "GetMute":
            return 200, _reply(action, {"CurrentMute": self.mute})
        if action == "SetVolume":
            self.volume = int(body.split("<DesiredVolume>")[1].split("<")[0])
            return 200, _reply(action, {})
        if action == "SetMute":
            self.mute = body.split("<DesiredMute>")[1].split("<")[0]
            return 200, _reply(action, {})
        return 500, _FAULT.format(code=401, text="Invalid Action")

    def actions(self):
        return [a for _p, a, _b in self.calls]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def tv():
    t = FakeTV()
    yield t
    t.close()


def _manager(tv):
    m = dlna.DlnaManager()
    r = dlna.describe(tv.base + "/desc.xml")
    m.renderers = {r.name + dlna.SUFFIX: r}
    return m, r.name + dlna.SUFFIX


def test_the_description_says_where_to_send_what(tv):
    r = dlna.describe(tv.base + "/desc.xml")
    assert r.name == "Living Room TV" and r.udn == "uuid:tv-1"
    # A relative control address is resolved against the description's.
    assert r.av_url == tv.base + "/AVTransport/control"
    assert r.rc_url == tv.base + "/RenderingControl/control"
    assert r.av_type == "urn:schemas-upnp-org:service:AVTransport:1"
    assert dlna.describe(tv.base + "/missing.xml") is None


def test_a_document_with_entities_is_not_expanded():
    with pytest.raises(ValueError):
        dlna._parse_xml(b'<!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>')
    with pytest.raises(ValueError):
        dlna._parse_xml(b"<x>" + b"a" * (dlna.MAX_XML + 1) + b"</x>")


def test_helpers():
    assert dlna.hms(3725) == "1:02:05" and dlna.hms(-3) == "0:00:00"
    assert dlna.parse_hms("1:02:05") == 3725
    assert dlna.parse_hms("0:00:07.250") == 7
    assert dlna.parse_hms("NOT_IMPLEMENTED") == 0
    assert dlna.mime_for("http://p/live/u/p/1") == "video/mp2t"
    assert dlna.mime_for("http://p/movie/u/p/9.mkv?x=1") == "video/x-matroska"
    assert dlna.mime_for("/music/a.flac") == "audio/flac"
    meta = dlna.didl("http://h/a?x=1&y=2", "Tom & Jerry <1>", "video/mp4")
    assert "Tom &amp; Jerry &lt;1&gt;" in meta
    assert "http://h/a?x=1&amp;y=2</res>" in meta
    assert 'protocolInfo="http-get:*:video/mp4:*"' in meta


def test_a_cast_tells_the_tv_and_follows_it(tv, monkeypatch):
    monkeypatch.setattr(dlna, "POLL_S", 0.1)
    m, name = _manager(tv)
    assert m.knows(name)
    assert m.cast(name, "http://p/hls/1.m3u8", "News & Weather",
                  source="http://p/live/u/p/1.ts", start=90,
                  duration=3600) == name
    assert tv.actions()[:3] == ["SetAVTransportURI", "Play",
                                "GetTransportInfo"]
    seturi = tv.calls[0][2]
    # The stream the player reads, not the HLS wrapper of it - escaped
    # once as the URI and twice inside the description.
    assert "<CurrentURI>http://p/live/u/p/1.ts</CurrentURI>" in seturi
    assert "News &amp;amp; Weather" in seturi
    assert "video/mp2t" in seturi
    assert "Seek" in tv.actions() and tv.rel == "0:01:30"
    assert m.state == "PLAYING/None" and m.duration == 3600
    time.sleep(0.5)                             # the watcher reads the TV
    assert 90 <= m.position() < 95
    assert m.volume() == (0.3, False)
    m.set_volume(0.55)
    m.set_muted(True)
    assert tv.volume == 55 and tv.mute == "1"
    m.pause()
    time.sleep(0.3)
    assert m.state == "PAUSED/None"
    m.resume()
    m.seek(600)
    assert tv.rel == "0:10:00"
    m.stop()
    assert tv.state == "STOPPED" and m.active is None
    # When the title runs out on its own, the window hears it ended.
    m.cast(name, "http://p/movie/1.mp4", "Film")
    m._played = True
    tv.state = "STOPPED"
    m._poll(1)
    assert m.state == "IDLE/FINISHED"
    m.stop()


def test_a_fussy_tv_gets_the_stream_without_a_description(tv, monkeypatch):
    m, name = _manager(tv)
    tv.refuse_metadata = True
    m.cast(name, "http://p/movie/1.mkv", "Film")
    assert tv.actions()[:4] == ["SetAVTransportURI", "Stop",
                                "SetAVTransportURI", "Play"]
    assert "<CurrentURIMetaData></CurrentURIMetaData>" in tv.calls[2][2]
    m.stop()


def test_a_tv_that_cannot_play_it_says_so(tv, monkeypatch):
    monkeypatch.setattr(dlna, "START_WAIT_S", 1.0)
    m, name = _manager(tv)
    tv.start_playing = False
    with pytest.raises(LocalizedError) as e:
        m.cast(name, "http://p/movie/1.avi", "Film")
    assert e.value.key == "cast_err_dlna_no_start"
    assert m.active is None and tv.actions()[-1] == "Stop"
    with pytest.raises(LocalizedError) as e:
        m.cast("Nobody (DLNA)", "http://x", "x")
    assert e.value.key == "cast_err_not_found"


def test_a_local_file_is_served_to_the_tv_with_ranges(tv, tmp_path):
    f = tmp_path / "Home Video 2025.mp4"
    f.write_bytes(bytes(range(256)) * 40)          # 10240 bytes
    m, name = _manager(tv)
    m.cast(name, str(f), "Home video", source=str(f))
    seturi = tv.calls[0][2]
    url = seturi.split("<CurrentURI>")[1].split("<")[0]
    assert url.startswith("http://") and url.endswith("Home_Video_2025.mp4")
    # The TV is on the LAN; the test talks to the same port on loopback.
    local = "http://127.0.0.1:" + url.split(":", 2)[2].split("/", 1)[0] \
        + "/" + url.split("/", 3)[3]
    with urlopen(local) as r:
        assert r.status == 200 and len(r.read()) == 10240
        assert r.headers["Content-Type"] == "video/mp4"
    with urlopen(Request(local, headers={"Range": "bytes=10-19"})) as r:
        assert r.status == 206 and r.read() == bytes(range(10, 20))
        assert r.headers["Content-Range"] == "bytes 10-19/10240"
    with urlopen(Request(local, headers={"Range": "bytes=-4"})) as r:
        assert r.read() == bytes(range(252, 256))
    wrong = local.rsplit("/", 2)[0] + "/guess/Home_Video_2025.mp4"
    with pytest.raises(HTTPError) as e:
        urlopen(wrong)
    assert e.value.code == 404
    m.stop()
    with pytest.raises(URLError):
        urlopen(local, timeout=2)                  # gone with the cast


def test_ssdp_answers_are_read_once_each():
    resp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    resp.bind(("127.0.0.1", 0))
    resp.settimeout(3)
    port = resp.getsockname()[1]
    got = []

    def answer():
        for _ in range(4):                          # 2 targets x 2 sends
            try:
                data, addr = resp.recvfrom(4096)
            except socket.timeout:
                return
            got.append(data.decode())
            for loc in ("http://192.168.1.20:8080/desc.xml",
                        "http://192.168.1.20:8080/desc.xml",
                        "ftp://nope/desc.xml"):
                resp.sendto(("HTTP/1.1 200 OK\r\nST: x\r\nLOCATION: " + loc
                             + "\r\n\r\n").encode(), addr)

    t = threading.Thread(target=answer, daemon=True)
    t.start()
    found = dlna.ssdp_search(1.0, ("127.0.0.1", port))
    t.join(3)
    resp.close()
    assert found == ["http://192.168.1.20:8080/desc.xml"]
    assert got and got[0].startswith("M-SEARCH * HTTP/1.1")
    assert 'MAN: "ssdp:discover"' in got[0]
    assert any("MediaRenderer:1" in g for g in got)


class _FakeMgr:
    def __init__(self, names, dlna_kind=False):
        self.names = names
        self.active = None
        self.state = "x"
        self.duration = 0.0
        self.bridge = object()
        self.calls = []
        self.dlna_kind = dlna_kind

    def knows(self, name):
        return name.endswith(dlna.SUFFIX)

    def scan(self):
        return list(self.names)

    def cast(self, name, url, title, *a, **k):
        self.calls.append(("cast", name, url))
        self.active = name
        return name

    def stop(self):
        self.calls.append(("stop",))
        self.active = None

    def position(self):
        return 42.0 if self.dlna_kind else 7.0

    def shutdown(self):
        self.calls.append(("shutdown",))


def test_the_hub_sends_each_device_to_its_own_kind(monkeypatch):
    pytest.importorskip("PyQt6")
    from dopeiptv.providers import casting
    monkeypatch.setattr(casting.ChromecastManager, "available",
                        staticmethod(lambda: True))
    cc = _FakeMgr(["Kitchen"])
    tvs = _FakeMgr(["Living Room TV (DLNA)"], dlna_kind=True)
    hub = casting.CastHub(cc, tvs)
    assert hub.scan() == ["Kitchen", "Living Room TV (DLNA)"]
    assert hub.is_dlna("Living Room TV (DLNA)") and not hub.is_dlna("Kitchen")
    hub.cast("Kitchen", "http://a", "A")
    assert hub.active == "Kitchen" and hub.position() == 7.0
    # Moving the cast to the TV ends it on the Chromecast.
    hub.cast("Living Room TV (DLNA)", "http://a", "A")
    assert ("stop",) in cc.calls and hub.position() == 42.0
    assert hub.active == "Living Room TV (DLNA)"
    hub.stop()
    assert tvs.active is None
    hub.shutdown()
    assert ("shutdown",) in cc.calls and ("shutdown",) in tvs.calls
    # On app close the device with a cast running is stopped first.
    order = []
    cc.shutdown = lambda: order.append("chromecast")
    tvs.shutdown = lambda: order.append("tv")
    tvs.active = "Living Room TV (DLNA)"
    hub.shutdown()
    assert order == ["tv", "chromecast"]
    # Without pychromecast, the TVs are still found.
    monkeypatch.setattr(casting.ChromecastManager, "available",
                        staticmethod(lambda: False))
    assert hub.scan() == ["Living Room TV (DLNA)"]


def test_the_cast_panel_hides_what_a_tv_cannot_do(monkeypatch):
    pytest.importorskip("PyQt6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QSettings, QThreadPool
    from PyQt6.QtWidgets import QApplication, QWidget

    from dopeiptv.providers import casting, chromecast
    app = QApplication.instance() or QApplication([])   # kept alive

    hub = casting.CastHub(_FakeMgr(["Kitchen"]),
                          _FakeMgr(["Living Room TV (DLNA)"], True))
    monkeypatch.setattr(casting.ChromecastManager, "available",
                        staticmethod(lambda: True))
    win = QWidget()                     # QDialog needs a real parent
    win.settings = QSettings("dopeiptv-test", "cast-dlna")
    win.settings.clear()
    win.cast, win.pool, win._cast_device = hub, QThreadPool(), None
    tracks = {"audio": [{"index": 0, "codec": "aac"},
                        {"index": 1, "codec": "ac3", "lang": "swe"}],
              "subtitle": [{"index": 0, "codec": "subrip", "lang": "swe"}]}
    d = chromecast.CastDialog(win, "http://a", "A", tracks=tracks,
                              probe=False)
    win.pool.waitForDone(3000)
    QApplication.processEvents()
    names = [d.list.item(i).text() for i in range(d.list.count())]
    assert names == ["Kitchen", "Living Room TV (DLNA)"]
    d.list.setCurrentRow(0)
    assert not d.track_rows[0].isHidden() and d.dlna_note.isHidden()
    assert not d.kind_box.isHidden()
    d.list.setCurrentRow(1)
    assert all(r.isHidden() for r in d.track_rows)
    assert not d.dlna_note.isHidden() and d.kind_box.isHidden()
    d.list.setCurrentRow(0)
    assert not d.track_rows[1].isHidden()
    d.close()
    app.processEvents()
