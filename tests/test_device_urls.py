"""What a device on the network names for itself stays on that device
(providers/device_urls.py): a SAT>IP server that names a file of this
computer as its channel list has it left unread (the person's own file list
still reads), a list or an address it names on another host is asked of
the device itself, and no redirect from it is followed. (DLNA's own:
tests/test_dlna.py.)"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dopeiptv.i18n import LocalizedError
from dopeiptv.providers import device_urls, satip
from dopeiptv.providers.client import make_box_client

LOOP = "127.0.0.1"


class Fake:
    """path -> (status, body, headers) on the loopback; GETs and POSTs kept."""

    def __init__(self, routes: dict) -> None:
        self.routes, self.asked = routes, []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _answer(self):
                n = int(self.headers.get("Content-Length") or 0)
                fake.asked.append((self.command, self.path, self.rfile.read(n) if n else b"",
                                   dict(self.headers)))
                status, body, headers = fake.routes.get(self.path, (404, b"", {}))
                data = body.encode() if isinstance(body, str) else body
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            do_GET = do_POST = _answer  # noqa: N815

        self.httpd = ThreadingHTTPServer((LOOP, 0), H)
        self.port = self.httpd.server_address[1]
        self.base = f"http://{LOOP}:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def fake():
    made = []

    def make(routes):
        f = Fake(routes)
        made.append(f)
        return f
    yield make
    for f in made:
        f.close()


def test_an_address_a_device_names_is_put_on_the_device():
    on = device_urls.on_host
    assert on("http://192.168.1.5:8080/d.xml?a=1", "192.168.1.5") == "http://192.168.1.5:8080/d.xml?a=1"
    assert on("http://10.9.9.9:8080/d.xml?a=1", "192.168.1.5") == "http://192.168.1.5:8080/d.xml?a=1"
    assert on("http://u:p@evil.example/x", "192.168.1.5") == "http://192.168.1.5/x"
    assert on("https://h/x", "fe80::1") == "https://[fe80::1]/x"
    for bad in ("file:///etc/passwd", "file:////192.0.2.9/share/tv.m3u", "/dev/zero",
                "C:\\x.m3u", "ftp://h/x", "-o", "", "http://[bad/x"):
        assert on(bad, "192.168.1.5") == "", bad
    assert on("https://h/x", "h", ("http",)) == ""


def test_a_broadcast_answer_names_no_address_but_a_web_one():
    import json

    from dopeiptv.providers import hdhomerun, jellyfin
    for addr in ("file:///etc/passwd", "javascript:alert(1)"):
        got = jellyfin.parse_discovery(json.dumps({"Address": addr, "Id": "x"}).encode(),
                                       "192.168.1.9", "jellyfin")
        assert got["server"] == "http://192.168.1.9:8096"
    import struct
    import zlib
    base = b"http://10.0.0.99:5004"
    payload = bytes([0x02, 4]) + b"\x12\x34\x56\x78" + bytes([0x2A, len(base)]) + base
    body = struct.pack(">HH", 3, len(payload)) + payload
    got = hdhomerun.parse_reply(body + struct.pack("<I", zlib.crc32(body)), "192.168.1.50")
    assert got["server"] == "http://192.168.1.50:5004"


def _satip_description(m3u: str) -> str:
    return ('<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device>'
            "<deviceType>urn:ses-com:device:SatIPServer:1</deviceType><friendlyName>S</friendlyName>"
            "<satip:X_SATIPCAP xmlns:satip='urn:ses-com:satip'>DVBS2-4</satip:X_SATIPCAP>"
            f"<satip:X_SATIPM3U xmlns:satip='urn:ses-com:satip'>{m3u}</satip:X_SATIPM3U>"
            "</device></root>")


LIST = ("#EXTM3U\n#EXTINF:0,Das Erste HD\n"
        "rtsp://sat.ip/?src=1&freq=11494&pol=h&msys=dvbs2&sr=22000&pids=0,5100\n")


def test_a_sat_ip_server_s_list_is_never_a_file_of_this_computer(fake, tmp_path):
    private = tmp_path / "my provider.m3u"
    private.write_text("#EXTM3U\n#EXTINF:-1,Mine\nhttp://198.51.100.7/live/MYUSER/MYPASSWORD/1.ts\n")
    for said in ("file://" + str(private).replace(" ", "%20"), str(private),
                 "file:////192.0.2.9/share/tv.m3u", "file:///dev/zero"):
        dev = fake({"/desc.xml": (200, _satip_description(said), {})})
        assert satip.parse_description(_satip_description(said).encode(),
                                       dev.base + "/desc.xml")["m3u"] in ("", dev.base + str(private))
        client = make_box_client({"kind": "satip", "server": dev.base + "/desc.xml"})
        client.SSDP_PORT = 1
        with pytest.raises(LocalizedError) as e:    # no list of its own: the file is not read
            client.authenticate()
        assert e.value.key == "err_satip_no_list"
    # The person's own file list reads, as ever.
    mine = tmp_path / "astra.m3u"
    mine.write_text(LIST)
    dev = fake({"/desc.xml": (200, _satip_description(""), {})})
    client = make_box_client({"kind": "satip", "server": dev.base + "/desc.xml",
                              "options": {"m3u": str(mine)}})
    client.SSDP_PORT = 1
    client.authenticate()
    assert [r["name"] for r in client.live_streams()] == ["Das Erste HD"]


def test_a_sat_ip_list_on_another_host_is_asked_of_the_server(fake):
    dev = fake({"/channels.m3u": (200, LIST, {})})
    dev.routes["/desc.xml"] = (200, _satip_description("http://203.0.113.9:%d/channels.m3u" % dev.port), {})
    client = make_box_client({"kind": "satip", "server": dev.base + "/desc.xml"})
    client.SSDP_PORT = 1
    client.authenticate()
    assert client.description["m3u"] == dev.base + "/channels.m3u"
    assert [r["name"] for r in client.live_streams()] == ["Das Erste HD"]
    # A redirect from there to another host is not followed.
    other = fake({"/l.m3u": (200, LIST, {})})
    dev.routes["/channels.m3u"] = (302, b"", {"Location": f"http://localhost:{other.port}/l.m3u"})
    client = make_box_client({"kind": "satip", "server": dev.base + "/desc.xml"})
    client.SSDP_PORT = 1
    with pytest.raises(LocalizedError):
        client.authenticate()
    assert other.asked == []
    # Nor one from the description itself, and none larger than one is read.
    other.routes["/desc.xml"] = dev.routes["/desc.xml"]
    dev.routes["/moved.xml"] = (302, b"", {"Location": f"http://localhost:{other.port}/desc.xml"})
    assert satip.describe(dev.base + "/moved.xml") is None and other.asked == []
    dev.routes["/big.xml"] = (200, _satip_description("/x") + " " * satip.MAX_XML, {})
    assert satip.describe(dev.base + "/big.xml") is None
