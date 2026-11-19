"""Server mode: one provider connection per channel shared by every device
watching it, never more channels at once than the tuners, the app's own
viewing first, the HDHomeRun API and its UDP discovery as Plex reads them,
the home network and the token, the browser page and its CSP, and the
headless start and stop."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from dopeiptv.core import hdhr, server_page
from dopeiptv.core.dvr_ingest import wait_until
from dopeiptv.core.dvr_pool import DvrPool
from dopeiptv.core.restream import SERVER
from dopeiptv.services import home_server as hs

ROOT = Path(__file__).resolve().parent.parent
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# -- a provider on the loopback, serving TS --------------------------------------------

class Provider(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        self.connections: dict[str, int] = {}
        self.open_now = 0
        self.lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), _ProviderHandler)

    def total(self) -> int:
        with self.lock:
            return sum(self.connections.values())


class _ProviderHandler(BaseHTTPRequestHandler):
    server: Provider

    def log_message(self, *a) -> None:
        pass

    def do_GET(self) -> None:
        srv = self.server
        with srv.lock:
            srv.connections[self.path] = srv.connections.get(self.path, 0) + 1
            srv.open_now += 1
        try:
            if self.path.startswith("/page/"):      # an expired account's answer
                body = b"<html><body>Your subscription has expired</body></html>" * 20
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.end_headers()
            pkt = b"\x47" + b"x" * 187
            while True:
                self.wfile.write(pkt * 40)
                self.wfile.flush()
                time.sleep(0.01)
        except OSError:
            pass
        finally:
            with srv.lock:
                srv.open_now -= 1


@pytest.fixture
def provider():
    p = Provider()
    threading.Thread(target=p.serve_forever, daemon=True).start()
    yield p
    p.shutdown()
    p.server_close()


class FakeClient:
    def __init__(self, base: str) -> None:
        self.base = base
        self.max_connections = None

    def live_categories(self):
        return [{"category_id": "1", "category_name": "News"},
                {"category_id": "2", "category_name": "Hidden"},
                {"category_id": "3", "category_name": "XXX Adult"},
                {"category_id": "4", "category_name": "Locked"}]

    def live_streams(self, _cat=None):
        return [{"stream_id": 1, "name": "One", "category_id": "1", "num": 5, "epg_channel_id": "one.tv"},
                {"stream_id": 2, "name": "Two", "category_id": "1", "num": 5},
                {"stream_id": 3, "name": "Three", "category_id": "1"},
                {"stream_id": 4, "name": "Secret", "category_id": "2", "num": 9},
                {"stream_id": 5, "name": "Night", "category_id": "3", "num": 10},
                {"stream_id": 6, "name": "Kid lock", "category_id": "4", "num": 11},
                {"stream_id": 7, "name": "Marked", "category_id": "1", "num": 12, "is_adult": "1"},
                {"stream_id": 8, "name": "By hand", "category_id": "1", "num": 13}]

    def live_url(self, sid, fmt="ts"):
        return f"{self.base}/live/{sid}.ts"


class Host:
    def __init__(self, pool: DvrPool) -> None:
        self._pool = pool
        self.app = 0
        self.app_sess = None
        self.saved: dict[str, str] = {}

    def sources(self):
        return [{"id": "p1", "name": "Home", "kind": "xtream", "server": "http://x.invalid"}]

    def filters(self, pid):
        return {"2", "4"}, {"8"}, {"3": 1}

    def app_use(self, pid):
        return self.app, []

    def app_session(self, pid, sid):
        return self.app_sess

    def pool(self):
        return self._pool

    def save(self, changes):
        self.saved.update(changes)
        return hs.Config(port=int(changes.get(hs.K_PORT, 0) or 0), token="tok-" + "x" * 20,
                         device_id="1234567F")

    def page(self):
        return server_page.render({"bg": "#101010", "text": "#eeeeee"}, {"bg": "#ffffff"},
                                  {"srv_page_title": "dopeIPTV server"})


TOKEN = "tok-" + "x" * 20


@pytest.fixture
def server(tmp_path, provider):
    pool = DvrPool(tmp_path, 60.0, 10**9, min_free=0)
    host = Host(pool)
    client = FakeClient(f"http://127.0.0.1:{provider.server_address[1]}")
    cfg = hs.Config(port=0, tuners=0, token=TOKEN, device_id=hdhr.new_device_id())
    srv = hs.HomeServer(host, cfg, bind="127.0.0.1", discovery_port=0,
                        make_client=lambda _pl: client, guide_path=lambda pid: str(tmp_path / f"epg_{pid}.xml"))
    srv.start()
    srv.restream.grace = 0.3
    srv.restream.TICK = 0.1
    yield srv, host, provider, pool
    srv.stop()
    pool.close_all(wait=2.0)


def _url(srv, path: str) -> str:
    return f"http://127.0.0.1:{srv.port}{path}"


def _get(srv, path: str, headers: dict | None = None):
    req = urllib.request.Request(_url(srv, path), headers=headers or {})
    return OPENER.open(req, timeout=10)


def _watch(srv, path: str):
    """A device watching: the stream open, its first bytes read."""
    r = _get(srv, path)
    assert r.status == 200 and r.headers["Content-Type"] == "video/mp2t"
    assert r.read(188 * 10)
    return r


# -- one connection per channel, never more than the tuners ------------------------------

def test_two_devices_on_one_channel_share_one_provider_connection(server):
    srv, _host, provider, _pool = server
    a = _watch(srv, f"/stream/p1/1?token={TOKEN}")
    b = _watch(srv, "/auto/v5")                 # the same channel, as Plex asks for it
    try:
        assert a.read(188 * 100) and b.read(188 * 100)
        assert provider.connections == {"/live/1.ts": 1}, "one connection to the account"
        assert provider.open_now == 1
        st = srv.restream.status()
        assert st["in_use"] == 1 and len(st["channels"][0]["viewers"]) == 2
    finally:
        a.close()
        b.close()


def test_a_second_channel_at_one_tuner_is_refused_without_a_connection(server):
    srv, _host, provider, _pool = server
    a = _watch(srv, f"/stream/p1/1?token={TOKEN}")
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(srv, "/auto/v1")                # channel 3, by its number of the app's own
        assert e.value.code == 503
        assert e.value.headers["X-HDHomeRun-Error"] == "805 All Tuners In Use"
        assert "tuners" in e.value.read().decode()
        assert set(provider.connections) == {"/live/1.ts"}, "never one more than allowed"
    finally:
        a.close()


def test_a_channel_nobody_watches_gives_its_tuner_to_the_next(server):
    srv, _host, provider, _pool = server
    srv.restream.grace = 30.0                   # it would wait - but a new channel asks
    a = _watch(srv, f"/stream/p1/1?token={TOKEN}")
    a.close()
    assert wait_until(lambda: not srv.restream.status()["channels"][0]["viewers"], 5)
    b = _watch(srv, f"/stream/p1/3?token={TOKEN}")
    try:
        assert wait_until(lambda: provider.open_now == 1, 5)
        assert provider.connections.get("/live/3.ts") == 1
    finally:
        b.close()


def test_the_last_device_leaving_stops_the_connection_after_the_grace(server):
    srv, _host, provider, _pool = server
    a = _watch(srv, f"/stream/p1/1?token={TOKEN}")
    assert provider.open_now == 1
    a.close()
    assert wait_until(lambda: provider.open_now == 0, 8), "let go once nobody watches"
    assert srv.restream.in_use() == 0


def test_the_app_s_own_viewing_counts_first(server):
    srv, host, provider, _pool = server
    host.app = 1                                # the person watches something already
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(srv, f"/stream/p1/1?token={TOKEN}")
    assert e.value.code == 503
    assert provider.total() == 0, "no connection beyond the account's limit"


def test_the_app_takes_the_tuner_back_from_a_device(server):
    srv, _host, provider, _pool = server
    a = _watch(srv, f"/stream/p1/1?token={TOKEN}")
    try:
        assert srv.restream.make_room("p1", own=1) == 1
        assert wait_until(lambda: provider.open_now == 0, 5), "the device's channel let go"
        a.read(1 << 20)                         # what was on its way
        assert a.read(4096) == b"", "the device's stream ends"
    finally:
        a.close()


def test_the_app_and_a_device_share_a_channel_both_watch(server):
    srv, host, provider, pool = server
    url = f"http://127.0.0.1:{provider.server_address[1]}/live/1.ts"
    mine = pool.open(url, "live:1")             # the person's player, with its buffer
    host.app_sess = mine
    host.app = 1
    assert mine.store.wait_for_data(10)
    a = _watch(srv, f"/stream/p1/1?token={TOKEN}")
    try:
        assert provider.connections == {"/live/1.ts": 1}, "the player's connection, shared"
        assert srv.restream.in_use() == 0, "the app's own, not the server's"
        assert mine.held_by(SERVER)
        pool.release(mine)                      # the person zaps away: the device goes on
        assert mine.streaming and srv.restream.in_use() == 1
    finally:
        a.close()


def test_the_player_tuning_to_a_served_channel_takes_no_connection(server):
    srv, _host, provider, pool = server
    a = _watch(srv, f"/stream/p1/1?token={TOKEN}")
    try:
        s = srv.restream.session_of("p1", "1")
        assert s is not None
        pool.adopt(s)                           # the player's live_hook hands it over
        assert pool.active is s and srv.restream.in_use() == 0
        a.close()
        time.sleep(1.0)                         # the grace runs out: the player keeps it
        assert provider.open_now == 1 and s.streaming
        assert provider.connections == {"/live/1.ts": 1}
    finally:
        a.close()


# -- what it serves ---------------------------------------------------------------------------

def test_the_lineup_never_holds_a_hidden_locked_or_adult_channel(server):
    srv = server[0]
    names = {c.name: c.number for c in srv.lineup()}
    assert set(names) == {"One", "Two", "Three"}
    assert names["Three"] == "1", "the app's own number wins"
    assert names["One"] == "5"
    assert names["Two"] not in ("5", "1"), "a number taken twice gets one of its own"


def test_hdhomerun_api_as_plex_reads_it(server):
    srv = server[0]
    base = f"http://127.0.0.1:{srv.port}"
    d = json.loads(_get(srv, "/discover.json").read())
    for key in ("FriendlyName", "ModelNumber", "FirmwareName", "TunerCount", "DeviceID",
                "BaseURL", "LineupURL", "FirmwareVersion"):
        assert key in d, key
    assert d["FriendlyName"] == "dopeIPTV" and d["BaseURL"] == base
    assert d["LineupURL"] == base + "/lineup.json" and d["TunerCount"] == 1
    assert hdhr.valid_device_id(d["DeviceID"])
    line = json.loads(_get(srv, "/lineup.json").read())
    assert isinstance(line, list) and line
    assert all(set(e) >= {"GuideNumber", "GuideName", "URL"} for e in line)
    assert all(isinstance(e["GuideNumber"], str) for e in line)
    assert {e["GuideName"]: e["URL"] for e in line}["One"] == base + "/auto/v5"
    st = json.loads(_get(srv, "/lineup_status.json").read())
    assert st["ScanInProgress"] == 0 and st["ScanPossible"] == 1 and st["SourceList"]
    xml = _get(srv, "/device.xml").read().decode()
    assert "<friendlyName>dopeIPTV</friendlyName>" in xml and d["DeviceID"] in xml
    post = OPENER.open(urllib.request.Request(_url(srv, "/lineup.post?scan=start"), data=b"",
                                              method="POST"), timeout=5)
    assert post.status == 200


def test_the_guide_carries_the_lineup_s_numbers(server, tmp_path):
    srv = server[0]
    (tmp_path / "epg_p1.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?><tv>'
        '<channel id="one.tv"><display-name>One HD</display-name></channel>'
        '<channel id="three"><display-name>Three</display-name></channel>'
        '<channel id="other"><display-name>Elsewhere</display-name></channel>'
        '<programme start="20260101000000 +0000" stop="20260101010000 +0000" channel="one.tv">'
        '<title>News &amp; more</title></programme>'
        '<programme start="20260101000000 +0000" stop="20260101010000 +0000" channel="other">'
        '<title>Not here</title></programme>'
        '<programme start="20260101000000 +0000" stop="20260101010000 +0000" channel="three">'
        '<title>Third</title></programme></tv>', encoding="utf-8")
    import xml.etree.ElementTree as ET
    text = _get(srv, f"/xmltv.xml?token={TOKEN}").read()
    root = ET.fromstring(text)
    ids = {c.get("id") for c in root.findall("channel")}
    lineup = {e["GuideNumber"] for e in json.loads(_get(srv, "/lineup.json").read())}
    assert ids == lineup, "every lineup channel, under its number"
    progs = {p.get("channel"): p.findtext("title") for p in root.findall("programme")}
    assert progs == {"5": "News & more", "1": "Third"}


def test_the_playlist_points_at_the_server(server):
    srv = server[0]
    text = _get(srv, f"/playlist.m3u?token={TOKEN}").read().decode()
    assert text.startswith("#EXTM3U")
    assert f"http://127.0.0.1:{srv.port}/stream/p1/1?token={TOKEN}" in text
    assert "/xmltv.xml?token=" in text and "x.invalid" not in text


# -- who may ask --------------------------------------------------------------------------------

def test_a_token_is_needed_but_for_the_tuner(server):
    srv = server[0]
    for path in ("/playlist.m3u", "/xmltv.xml", "/", "/api/status", "/stream/p1/1",
                 "/playlist.m3u?token=wrong"):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(srv, path)
        assert e.value.code == 401, path
    assert _get(srv, "/discover.json").status == 200
    assert _get(srv, "/api/status", {"X-Dopeiptv-Token": TOKEN}).status == 200
    assert _get(srv, "/api/status", {"Authorization": f"Bearer {TOKEN}"}).status == 200


def test_an_address_outside_the_home_is_refused(server, monkeypatch):
    srv = server[0]
    monkeypatch.setattr(hs.home_net, "is_home_client", lambda _ip, _own="": False)
    for path in ("/discover.json", "/auto/v5", f"/playlist.m3u?token={TOKEN}"):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(srv, path)
        assert e.value.code == 403, path


def test_public_addresses_are_not_home():
    from dopeiptv.core import home_net
    assert not home_net.is_home_client("8.8.8.8")
    assert not home_net.is_home_client("2001:4860::8888")
    assert home_net.is_home_client("192.168.1.20") and home_net.is_home_client("10.0.5.1")


# -- the browser page ---------------------------------------------------------------------------

def _sha(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


def test_the_page_renders_and_its_csp_allows_exactly_its_own_code():
    from dopeiptv.i18n import tr
    html, csp = server_page.render({"bg": "#17171c", "accent": "#4c8dff", "text": "bad;}"},
                                   {"bg": "#ffffff"}, {k: tr(k) for k in server_page.WORDS})
    text = html.decode("utf-8")
    script = re.search(r"<script>(.*?)</script>", text, re.S).group(1)
    style = re.search(r"<style>(.*?)</style>", text, re.S).group(1)
    assert f"script-src {_sha(script)};" in csp and f"style-src {_sha(style)};" in csp
    assert "default-src 'none'" in csp and "connect-src 'self'" in csp
    assert "--accent:#4c8dff" in style and "bad;}" not in style, "#rrggbb only"
    assert "http" not in re.sub(r"connect-src 'self'", "", csp)
    assert not re.search(r"""(src|href)=["']https?:""", text), "nothing from elsewhere"
    for key in server_page.WORDS:
        assert tr(key) != key, f"{key} has no English text"


def test_the_page_is_served_with_its_csp(server):
    srv = server[0]
    r = _get(srv, f"/?token={TOKEN}")
    assert r.status == 200 and "script-src 'sha256-" in r.headers["Content-Security-Policy"]
    view = json.loads(_get(srv, "/api/settings", {"X-Dopeiptv-Token": TOKEN}).read())
    assert view["sources"][0]["id"] == "p1" and "--server" in view["command"]
    cats = json.loads(_get(srv, "/api/categories?source=p1", {"X-Dopeiptv-Token": TOKEN}).read())
    assert [c["name"] for c in cats["categories"]] == ["News"], "never hidden, locked or adult"


def test_the_page_changes_only_valid_settings(server):
    srv, host, _p, _pool = server
    body = json.dumps({"tuners": 2, "sources": {"p1": ["1"], "nope": []}}).encode()
    req = urllib.request.Request(_url(srv, "/api/settings"), data=body, method="POST",
                                 headers={"X-Dopeiptv-Token": TOKEN, "Content-Type": "application/json"})
    assert json.loads(OPENER.open(req, timeout=5).read())["ok"]
    assert host.saved[hs.K_TUNERS] == "2"
    assert json.loads(host.saved[hs.K_SOURCES]) == {"p1": ["1"]}
    with pytest.raises(ValueError):
        hs.changes_from_page({"port": 80}, ["p1"])


# -- the HDHomeRun discovery on UDP ---------------------------------------------------------------

def test_device_ids_pass_libhdhomerun_s_checksum():
    for _ in range(50):
        dev = hdhr.new_device_id()
        assert re.fullmatch(r"[0-9A-F]{8}", dev) and hdhr.valid_device_id(dev)
        wrong = dev[:-1] + format((int(dev[-1], 16) + 1) % 16, "X")
        assert not hdhr.valid_device_id(wrong)


def test_the_discovery_answer_as_a_client_reads_it():
    from dopeiptv.providers.hdhomerun import discover_packet, parse_reply
    dev = hdhr.new_device_id()
    assert hdhr.wants(discover_packet(), dev)
    assert not hdhr.wants(hdhr.frame(hdhr.TYPE_DISCOVER_REQ,
                                     hdhr._tlv(hdhr.TAG_DEVICE_TYPE, (5).to_bytes(4, "big"))), dev)
    assert not hdhr.wants(hdhr.frame(hdhr.TYPE_DISCOVER_REQ,
                                     hdhr._tlv(hdhr.TAG_DEVICE_ID, (0x12345678).to_bytes(4, "big"))), dev)
    data = hdhr.reply(dev, 3, "http://192.168.1.5:3675")
    # The frame: type 3, the payload's length, its tags, CRC32 little-endian.
    assert data[:2] == b"\x00\x03"
    assert int.from_bytes(data[2:4], "big") == len(data) - 8
    import zlib
    assert int.from_bytes(data[-4:], "little") == zlib.crc32(data[:-4])
    got = parse_reply(data, "192.168.1.5")
    assert got == {"server": "http://192.168.1.5:3675", "kind": "hdhomerun",
                   "device_id": dev, "storage": False}
    tags = hdhr.tags(data)[1]
    assert tags[hdhr.TAG_TUNER_COUNT] == [b"\x03"]
    assert tags[hdhr.TAG_LINEUP_URL] == [b"http://192.168.1.5:3675/lineup.json"]
    assert tags[hdhr.TAG_DEVICE_TYPE] == [b"\x00\x00\x00\x01"]
    long = hdhr._tlv(hdhr.TAG_BASE_URL, b"x" * 200)
    assert long[1] == (200 & 0x7F) | 0x80 and long[2] == 200 >> 7


def test_the_responder_answers_a_broadcast_on_the_network():
    from dopeiptv.providers.hdhomerun import discover_packet, parse_reply
    dev = hdhr.new_device_id()
    r = hdhr.Responder(lambda ip: (dev, 2, f"http://{ip}:3675"), port=0, host="127.0.0.1")
    assert r.start()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(3)
        s.sendto(discover_packet(), ("127.0.0.1", r.port))
        data, _addr = s.recvfrom(2048)
        assert parse_reply(data, "127.0.0.1")["device_id"] == dev
        s.sendto(b"\x00\x02\x00\x00garbage", ("127.0.0.1", r.port))
        with pytest.raises(socket.timeout):
            s.recvfrom(2048)                     # nothing for a broken packet
    finally:
        s.close()
        r.stop()


# -- without a window ----------------------------------------------------------------------------

def _env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ, HOME=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path / "config"),
               XDG_CACHE_HOME=str(tmp_path / "cache"), XDG_DATA_HOME=str(tmp_path / "data"),
               DOPEIPTV_LOG="info", QT_QPA_PLATFORM="offscreen")
    env.pop("DOPEIPTV_LOG_FILE", None)
    return env


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM")
def test_headless_starts_answers_and_stops_on_sigterm(tmp_path):
    proc = subprocess.Popen([sys.executable, str(ROOT / "dopeiptv.py"), "--server", "--port", "0",
                             "--no-discovery"], cwd=str(tmp_path), env=_env(tmp_path),
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    port = None
    lines: list[str] = []
    try:
        end = time.monotonic() + 30
        while time.monotonic() < end and proc.poll() is None:
            line = proc.stderr.readline()
            lines.append(line)
            m = re.search(r"server: listening on port (\d+)", line)
            if m:
                port = int(m.group(1))
                break
        assert port, "".join(lines)
        d = json.loads(OPENER.open(f"http://127.0.0.1:{port}/discover.json", timeout=5).read())
        assert d["FriendlyName"] == "dopeIPTV"
        proc.send_signal(signal.SIGTERM)
        out = proc.communicate(timeout=20)[1]
        assert proc.returncode == 0, out
        assert "server: done" in out
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


_NO_WIDGETS = r"""
import os, signal, sys, threading, time
def later():
    time.sleep(2.0)
    bad = [m for m in ("PyQt6.QtWidgets", "PyQt6.QtGui", "PyQt6.QtOpenGLWidgets", "mpv")
           if m in sys.modules]
    sys.stdout.write("LOADED %r\n" % bad)
    sys.stdout.flush()
    os.kill(os.getpid(), signal.SIGTERM)
threading.Thread(target=later, daemon=True).start()
from dopeiptv.launcher import main
sys.argv = ["dopeiptv", "--server", "--port", "0", "--no-discovery"]
sys.exit(main())
"""


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM")
def test_headless_imports_no_widget_and_no_player(tmp_path):
    r = subprocess.run([sys.executable, "-c", _NO_WIDGETS], cwd=str(ROOT), env=_env(tmp_path),
                       capture_output=True, text=True, timeout=60)
    assert "LOADED []" in r.stdout, r.stdout + r.stderr
    assert r.returncode == 0, r.stderr


# -- an adaptive (HLS) channel, through ffmpeg ---------------------------------------------------

@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="needs ffmpeg")
def test_an_hls_channel_is_restreamed_as_ts_over_one_connection(tmp_path):
    seg = tmp_path / "seg.ts"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=96x64:rate=10",
                    "-t", "2", "-c:v", "mpeg2video", "-f", "mpegts", str(seg)], check=True, timeout=60)
    data = seg.read_bytes()
    asked: list[str] = []
    # Long enough to be a run that streamed (over the ingest's GOOD_BYTES),
    # and sent as a server sends it, a segment at a time: three short
    # segments ended within a second as a failed try, and the playlist was
    # asked for again while the test still counted.
    from dopeiptv.core.dvr_ingest import Ingest
    segments = Ingest.GOOD_BYTES // len(data) * 4 + 4

    class Hls(BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:
            asked.append(self.path)
            if self.path.endswith(".m3u8"):
                body = ("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n"
                        + "".join(f"#EXTINF:2.0,\nseg{i}.ts\n" for i in range(segments))
                        + "#EXT-X-ENDLIST\n").encode()
                ctype = "application/vnd.apple.mpegurl"
            else:
                time.sleep(0.1)
                body, ctype = data, "video/mp2t"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    web = ThreadingHTTPServer(("127.0.0.1", 0), Hls)
    threading.Thread(target=web.serve_forever, daemon=True).start()

    class HlsClient(FakeClient):
        def live_url(self, sid, fmt="ts"):
            return f"http://127.0.0.1:{web.server_address[1]}/hls/index.m3u8"

    pool = DvrPool(tmp_path / "dvr", 60.0, 10**9, min_free=0)
    cfg = hs.Config(port=0, token=TOKEN, device_id=hdhr.new_device_id())
    srv = hs.HomeServer(Host(pool), cfg, bind="127.0.0.1", discovery_port=0,
                        make_client=lambda _pl: HlsClient("http://x.invalid"))
    srv.start()
    try:
        r = _get(srv, f"/stream/p1/1?token={TOKEN}")
        got = r.read(188 * 20)
        r.close()
        assert got[:1] == b"\x47" and got[188:189] == b"\x47", "MPEG-TS, packet by packet"
        assert asked[0] == "/hls/index.m3u8" and asked.count("/hls/index.m3u8") == 1
    finally:
        srv.stop()
        pool.close_all(wait=2.0)
        web.shutdown()
        web.server_close()


# -- the player's side ----------------------------------------------------------------------------

def _player(monkeypatch, tmp_path):
    from dopeiptv.media import dvr_player

    monkeypatch.setattr(dvr_player, "dvr_base", lambda: tmp_path / "dvr")

    class Settings(dict):
        def value(self, key, default=None):
            return self.get(key, default)

    class Player(dvr_player.DvrPlayerMixin):
        def __init__(self) -> None:
            self._settings = Settings(live_rewind_kept="false")

        def _live_rewind_minutes(self) -> int:
            return 0
    return Player()


def test_without_server_mode_the_player_has_no_hook():
    from dopeiptv.media.dvr_player import DvrPlayerMixin
    assert DvrPlayerMixin.live_hook is None


def test_the_player_keeps_the_pool_the_server_reads_while_its_buffer_is_off(monkeypatch, tmp_path):
    p = _player(monkeypatch, tmp_path)
    pool = p.dvr_pool_for_server(120.0)
    assert pool is p._dvr_pool and pool.keep_seconds == 120.0
    pool.shared = True
    assert p._dvr_start("http://p.invalid/live/1.ts", "live:1") is None
    assert p._dvr_pool is pool, "the server reads from it: not closed"
    pool.shared = False
    p._dvr_start("http://p.invalid/live/1.ts", "live:1")
    assert p._dvr_pool is None, "the buffer off and no server: closed as before"


def test_the_player_adopts_the_server_s_session(monkeypatch, tmp_path, provider):
    p = _player(monkeypatch, tmp_path)
    pool = p.dvr_pool_for_server(120.0)
    url = f"http://127.0.0.1:{provider.server_address[1]}/live/1.ts"
    s = pool.acquire(url, "srv:p1\x1f1", SERVER)
    try:
        local = p._dvr_adopt(s)
        assert local and local.startswith("http://127.0.0.1:") and local.endswith("/live.ts")
        assert pool.active is s and p._dvr is s
        assert wait_until(lambda: provider.connections == {"/live/1.ts": 1}, 5)
        p._end_dvr()                            # the person zaps away: the server keeps it
        assert s.streaming and pool.active is None
        pool.let_go(s, SERVER)
        assert s.frozen, "nobody reads it any more: its connection closes"
        assert provider.connections == {"/live/1.ts": 1}
    finally:
        pool.close_all(wait=2.0)


# -- what devices are never given, and channels the server cannot carry ----------------------------

def _serving(tmp_path, client):
    pool = DvrPool(tmp_path / "dvr", 60.0, 10**9, min_free=0)
    cfg = hs.Config(port=0, token=TOKEN, device_id=hdhr.new_device_id())
    srv = hs.HomeServer(Host(pool), cfg, bind="127.0.0.1", discovery_port=0,
                        make_client=lambda _pl: client,
                        guide_path=lambda pid: str(tmp_path / f"epg_{pid}.xml"))
    srv.start()
    return srv, pool


def test_logos_with_a_login_or_a_token_never_reach_a_device(tmp_path):
    from dopeiptv.services.share import public_logo

    class Logos(FakeClient):
        def live_streams(self, _cat=None):
            return [{"stream_id": 1, "name": "Box", "category_id": "1", "num": 1,
                     "epg_channel_id": "box.tv",
                     "stream_icon": "http://root:box-secret@192.168.1.9/picon/1_0_19.png"},
                    {"stream_id": 2, "name": "Plex", "category_id": "1", "num": 2,
                     "stream_icon": "http://192.168.1.8:32400/photo/:/transcode?url=%2Fx"
                                    "&X-Plex-Token=plex-secret"},
                    {"stream_id": 3, "name": "Picon", "category_id": "1", "num": 3,
                     "stream_icon": "/home/someone/picons/3.png"},
                    {"stream_id": 4, "name": "Plain", "category_id": "1", "num": 4,
                     "stream_icon": "https://logos.example/4.png"}]

    (tmp_path / "epg_p1.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?><tv><channel id="box.tv"><display-name>Box</display-name>'
        '<icon src="http://root:box-secret@192.168.1.9/picon/guide.png"/></channel></tv>',
        encoding="utf-8")
    srv, pool = _serving(tmp_path, Logos("http://x.invalid"))
    try:
        m3u = _get(srv, f"/playlist.m3u?token={TOKEN}").read().decode()
        guide = _get(srv, f"/xmltv.xml?token={TOKEN}").read().decode()
        for text in (m3u, guide):
            assert "secret" not in text and "/home/someone" not in text
            assert "https://logos.example/4.png" in text, "a logo without a login stays"
    finally:
        srv.stop()
        pool.close_all(wait=2.0)
    assert public_logo("https://logos.example/4.png") == "https://logos.example/4.png"
    assert public_logo("http://u:p@box/picon.png") == ""
    assert public_logo("file:///tmp/x.png") == "" and public_logo(None) == ""


def test_a_channel_whose_provider_sends_a_page_is_not_asked_again_for_every_try(tmp_path, provider):
    class Expired(FakeClient):
        def live_url(self, sid, fmt="ts"):
            return f"{self.base}/page/{sid}.ts"

    srv, pool = _serving(tmp_path, Expired(f"http://127.0.0.1:{provider.server_address[1]}"))
    srv.restream.TICK = 0.1
    try:
        for _try in range(4):                   # a TV's app trying again by itself
            with pytest.raises(urllib.error.HTTPError) as e:
                _get(srv, "/auto/v5")
            assert e.value.code == 503
            assert e.value.headers["X-HDHomeRun-Error"] == "807 No Video Data"
        assert provider.connections == {"/page/1.ts": 1}, "the page asked for once, not each time"
    finally:
        srv.stop()
        pool.close_all(wait=2.0)


def test_an_address_the_server_cannot_read_is_refused_at_once(tmp_path):
    class SatIp(FakeClient):
        def live_url(self, sid, fmt="ts"):
            return "rtsp://192.168.1.7:554/?src=1&freq=11494&pol=h&msys=dvbs2&sr=22000&pids=0"

    srv, pool = _serving(tmp_path, SatIp("http://x.invalid"))
    try:
        began = time.monotonic()
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(srv, "/auto/v5")
        assert e.value.code == 503 and e.value.headers["X-HDHomeRun-Error"] == "807 No Video Data"
        assert "rtsp" in e.value.read().decode()
        assert time.monotonic() - began < 5, "said at once, not after the buffer's five tries"
        assert srv.restream.in_use() == 0 and not pool.sessions
    finally:
        srv.stop()
        pool.close_all(wait=2.0)


def test_the_command_to_start_it_in_the_flatpak_is_the_host_s(monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.setenv("FLATPAK_ID", "io.github.slimture.dopeIPTV")
    assert hs.launch_command() == "flatpak run io.github.slimture.dopeIPTV --server"
    monkeypatch.delenv("FLATPAK_ID")
    assert hs.launch_command().endswith("--server")


def test_a_port_already_taken_leaves_no_thread_running(tmp_path):
    from PyQt6.QtCore import QSettings

    from dopeiptv.ui import mw_server

    blocker = socket.socket()
    blocker.bind(("0.0.0.0", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    settings.setValue("nerd_server_port", str(port))
    pool = DvrPool(tmp_path / "dvr", 60.0, 10**9, min_free=0)

    class Player:
        live_hook = None

        def dvr_pool_for_server(self, _keep):
            return pool

    class Window(mw_server._ServerMixin):
        def __init__(self) -> None:
            self.settings, self.player, self.told = settings, Player(), []

        def _show_toast(self, text, _ms) -> None:
            self.told.append(text)

        def _show_action_toast(self, text, _ms, _act, label=None, decide=False) -> None:
            self.told.append(text)

    def ticking() -> int:
        return sum(1 for t in threading.enumerate() if t.name == "restream" and t.is_alive())

    def told() -> bool:
        # The note comes once it is known whose the port is (off the
        # window's thread, handed to it).
        from PyQt6.QtCore import QCoreApplication
        if QCoreApplication.instance() is not None:
            QCoreApplication.processEvents()
        return bool(w.told)

    before = ticking()
    w = Window()
    try:
        w._server_start()
        assert w._home is None and not pool.shared
        assert wait_until(told, 10), "the note came"
        # At most as many as before: a restream an earlier test let go may
        # still be ending when this one starts, and goes meanwhile.
        assert wait_until(lambda: ticking() <= before, 5), "the failed server's restream stopped"
    finally:
        blocker.close()


def test_a_zap_under_way_never_gives_a_device_the_channel_before():
    """The window sets the item it plays some way before the player leaves
    the session of the channel before it: the restream must not take that
    session for the new channel (a device, then the player, saw the old
    channel under the new one's name)."""
    from dopeiptv.ui import mw_server

    class Session:
        streaming = True

        def __init__(self, key: str) -> None:
            self.key = key

    class Window:
        def __init__(self, item: dict, dvr: Session) -> None:
            self._playing_item, self.player = item, type("P", (), {"_dvr": dvr})()

        _item_key = staticmethod(lambda it: it.get("stream_id"))

        def _conn_player_source(self) -> str:
            return "p1"

    def asked(item: dict, dvr: Session):
        return mw_server._WindowHost(Window(item, dvr), None).app_session("p1", "2")

    one, two = Session("live:1"), Session("live:2")
    assert asked({"stream_id": 2}, one) is None, "channel 1's session, while the zap to 2 is under way"
    assert asked({"stream_id": 2}, two) is two
    adopted = Session("srv:p1\x1f2")
    assert asked({"stream_id": 2}, adopted) is adopted, "the server's own, played by the player"


# -- the tuner's endpoints: this computer's own networks only --------------------------------------

def test_the_tuner_answers_only_this_computer_s_own_networks():
    from ipaddress import ip_network

    from dopeiptv.core import home_net

    reads: list[int] = []
    now = [100.0]

    def networks():
        reads.append(1)
        return [ip_network("192.168.1.0/24"), ip_network("fe80::/64"),
                ip_network("2001:db8:5::/64")]

    local = home_net.LocalNetworks(networks, lambda: ["192.168.20.7"], clock=lambda: now[0])
    for ok in ("127.0.0.1", "::1", "192.168.1.77", "::ffff:192.168.1.9", "fe80::1%eth0",
               "192.168.20.7"):
        assert local.allows(ok), ok
    for refused in ("192.168.2.5", "10.0.0.5", "172.16.3.4", "100.64.1.2", "8.8.8.8",
                    "2001:db8:6::1", "fd00::5", "", "nonsense",
                    "2001:db8:5::42"):          # an own network, but a public one
        assert not local.allows(refused), refused
    assert local.allows("10.1.2.3", own="10.1.2.3"), "this computer, at an address of its own"
    assert len(reads) == 1, "kept, not read for every request"
    now[0] += home_net.LOCAL_RETRY + 1
    assert not local.allows("10.0.0.5") and len(reads) == 2, "an address outside: read again"
    assert not local.allows("10.0.0.6") and len(reads) == 2, "but not for every one"
    now[0] += home_net.LOCAL_TTL + 1
    assert local.allows("192.168.1.5") and len(reads) == 3, "kept only a while"


def test_this_computer_s_own_networks_are_read_without_qt():
    from dopeiptv.core import home_net
    from dopeiptv.core.remote_api import lan_addresses

    import ipaddress
    nets = home_net.own_networks()
    for a in lan_addresses():
        assert any(ipaddress.ip_address(a) in n for n in nets), (a, nets)
    code = ("import sys; from dopeiptv.core import home_net; home_net.own_networks(); "
            "print('PyQt6.QtNetwork' in sys.modules, 'PyQt6.QtGui' in sys.modules)")
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True,
                       text=True, timeout=60)
    assert r.stdout.split() == ["False", "False"], r.stdout + r.stderr
    if sys.platform.startswith("linux"):
        from PyQt6 import QtNetwork
        v4 = {n for n in home_net._linux_networks() if n.version == 4}
        assert v4 == {n for n in home_net._qt_networks(QtNetwork) if n.version == 4}


def test_only_own_networks_of_the_home_stand_for_it():
    """A VPN's subnet, a public network (a modem in bridge mode), carrier
    NAT's and a 10/8 netmask are not the home: their other users would get
    the tuner, which needs no token. Nerd's other networks still let one in."""
    from ipaddress import ip_network

    from dopeiptv.core import home_net

    for ok in ("192.168.1.0/24", "10.0.0.0/16", "172.16.5.0/24", "169.254.0.0/16",
               "192.168.1.5/32", "fe80::/64", "fd12:3456:789a:1::/64"):
        assert home_net.trusted(ip_network(ok)), ok
    for bad in ("10.0.0.0/8", "172.16.0.0/12", "100.64.0.0/10", "100.101.102.0/24",
                "203.0.113.0/24", "198.51.100.0/22", "2001:db8:5::/64", "fc00::/7",
                "127.0.0.0/8"):
        assert not home_net.trusted(ip_network(bad)), bad
    nets = [ip_network("192.168.1.0/24"), ip_network("203.0.113.0/24"),
            ip_network("100.64.0.0/10"), ip_network("10.0.0.0/8")]
    local = home_net.LocalNetworks(lambda: nets, lambda: ["100.101.102.103"])
    assert local.allows("192.168.1.20")
    for refused in ("203.0.113.9", "100.64.7.7", "10.200.1.1"):
        assert not local.allows(refused), refused
    assert local.allows("100.101.102.103"), "listed by the person in Nerd's other networks"
    assert local.allows("127.0.0.1") and local.allows("::1"), "loopback, always"


def _fake_qt(ifaces):
    from types import SimpleNamespace

    class Flag:
        IsUp, IsPointToPoint = 1, 2

    class Kind:
        Ethernet, Wifi, Ppp, Virtual, Unknown = "eth", "wifi", "ppp", "virtual", "unknown"

    def entry(text):
        ip, prefix = text.split("/")
        return SimpleNamespace(ip=lambda: SimpleNamespace(toString=lambda: ip),
                               prefixLength=lambda: int(prefix))

    def iface(name, kind, flags, addrs):
        return SimpleNamespace(name=lambda: name, type=lambda: getattr(Kind, kind),
                               flags=lambda: flags,
                               addressEntries=lambda: [entry(a) for a in addrs])

    class QNetworkInterface:
        InterfaceFlag, InterfaceType = Flag, Kind

        @staticmethod
        def allInterfaces():
            return [iface(*i) for i in ifaces]

    return SimpleNamespace(QNetworkInterface=QNetworkInterface)


def test_a_tunnel_s_network_is_not_an_own_network_as_qt_reads_them():
    from ipaddress import ip_network

    from dopeiptv.core import home_net

    up, p2p = 1, 2
    qt = _fake_qt([("eth0", "Ethernet", up, ["192.168.1.5/24", "fe80::1/64"]),
                   ("wlan0", "Wifi", up, ["192.168.50.3/24"]),
                   ("eth1", "Ethernet", 0, ["192.168.9.3/24"]),           # down
                   ("tun0", "Unknown", up | p2p, ["10.8.0.6/24"]),        # OpenVPN
                   ("wg0", "Unknown", up | p2p, ["10.6.0.2/24"]),         # WireGuard
                   ("utun4", "Unknown", up, ["fd7a:115c:a1e0::1/64"]),    # macOS
                   ("tap0", "Ethernet", up, ["10.9.0.2/24"]),             # OpenVPN tap
                   ("ztabcdef", "Ethernet", up, ["10.147.17.5/24"]),      # ZeroTier
                   ("tailscale0", "Unknown", up, ["100.101.102.103/32"]),
                   ("gre1", "Virtual", up, ["10.20.0.1/30"]),
                   ("ppp0", "Ppp", up, ["10.64.64.64/32"]),
                   ("tunnel_32512", "Ethernet", up, ["10.7.0.2/24"])])   # Windows' name
    assert set(home_net._qt_networks(qt)) == {
        ip_network("192.168.1.0/24"), ip_network("fe80::/64"), ip_network("192.168.50.0/24")}


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux's own interface lists")
def test_a_tunnel_s_network_is_not_an_own_network_as_linux_reads_them(tmp_path, monkeypatch):
    import fcntl
    import struct
    from ipaddress import ip_network

    from dopeiptv.core import home_net

    up, p2p = 0x1, 0x10
    ifaces = {"lo": (up | 0x8, "127.0.0.1", "255.0.0.0"),
              "eth0": (up, "192.168.1.5", "255.255.255.0"),
              "br0": (up, "10.0.10.2", "255.255.255.0"),        # a bridge holds the LAN
              "tun0": (up | p2p, "10.8.0.6", "255.255.255.0"),
              "wg0": (up | p2p, "10.6.0.2", "255.255.255.0"),
              "tap0": (up, "10.9.0.2", "255.255.255.0"),
              "tailscale0": (up | p2p, "100.101.102.103", "255.255.255.255"),
              "ppp0": (up | p2p, "81.2.3.4", "255.255.255.255")}

    def ioctl(_fd, req, buf):
        flags, addr, mask = ifaces[buf[:16].split(b"\0", 1)[0].decode()]
        if req == home_net._SIOCGIFFLAGS:
            return buf[:16] + struct.pack("H", flags) + buf[18:]
        return buf[:20] + socket.inet_aton(addr if req == home_net._SIOCGIFADDR else mask) \
            + buf[24:]

    inet6 = tmp_path / "if_inet6"
    inet6.write_text("fe800000000000000000000000000001 02 40 20 80 eth0\n"
                     "fd000000000000080000000000000006 05 40 00 80 tun0\n"
                     "fdaa0000000000000000000000000002 06 40 00 80 wg0\n"
                     "fd7a115ca1e0ab120000000000000001 07 80 00 80 tailscale0\n")
    monkeypatch.setattr(socket, "if_nameindex", lambda: list(enumerate(ifaces, 1)))
    monkeypatch.setattr(fcntl, "ioctl", ioctl)
    monkeypatch.setattr(home_net, "_IF_INET6", str(inet6))
    assert set(home_net._linux_networks()) == {
        ip_network("127.0.0.0/8"), ip_network("192.168.1.0/24"), ip_network("10.0.10.0/24"),
        ip_network("fe80::/64")}


def test_the_tuner_s_endpoints_are_refused_beyond_its_own_networks(server, monkeypatch):
    srv = server[0]

    class Elsewhere:
        def allows(self, _address, _own=""):
            return False

    monkeypatch.setattr(srv, "local", Elsewhere())
    for path in ("/discover.json", "/lineup.json", "/lineup_status.json", "/device.xml",
                 "/lineup.post", "/auto/v5"):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(srv, path)
        assert e.value.code == 403, path
    assert _get(srv, f"/playlist.m3u?token={TOKEN}").status == 200, "the token's routes as before"


def test_a_web_page_cannot_drive_the_tuner(server):
    """A page in a browser here asks the tuner by an IP address (an <img>, a
    no-cors fetch: sent without asking) - refused, no connection opened;
    Plex, Jellyfin, VLC and a person typing the address send neither header."""
    srv, _host, provider, _pool = server
    for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"},
                    {"Origin": f"http://127.0.0.1:{srv.port}"},
                    {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}):
        for path in ("/discover.json", "/lineup.json", "/auto/v5"):
            with pytest.raises(urllib.error.HTTPError) as e:
                _get(srv, path, headers)
            assert e.value.code == 403, (path, headers)
    assert provider.total() == 0, "nothing opened for a web page"
    assert _get(srv, "/discover.json").status == 200
    assert _get(srv, "/lineup.json", {"Sec-Fetch-Site": "none"}).status == 200


def test_a_web_page_cannot_shut_the_tuner_or_this_computer_out(server, monkeypatch):
    srv = server[0]
    monkeypatch.setattr(srv, "limiter", hs.RateLimiter(fails=3))
    bad = "/playlist.m3u?token=" + "x" * 32
    for _ in range(5):
        with pytest.raises(urllib.error.HTTPError):
            _get(srv, bad)                      # this computer: never shut out
    assert not srv.limiter.blocked("127.0.0.1")
    monkeypatch.setattr(hs, "_loopback", lambda _who: False)     # as if from the network
    for _ in range(5):
        with pytest.raises(urllib.error.HTTPError):
            _get(srv, bad, {"Sec-Fetch-Site": "cross-site"})
    assert not srv.limiter.blocked("127.0.0.1"), "a web page's guesses never count"
    for _ in range(3):
        with pytest.raises(urllib.error.HTTPError):
            _get(srv, bad)
    assert srv.limiter.blocked("127.0.0.1"), "a device's own guesses as before"
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(srv, f"/playlist.m3u?token={TOKEN}")
    assert e.value.code == 429
    assert _get(srv, "/discover.json").status == 200, "a shut-out address keeps its tuner"


def test_the_tuner_answers_only_under_a_home_name(server):
    """DNS rebinding: a web page at home points its own name at this
    computer and asks the tuner, which needs no token - its Host is that
    public name, so it is refused; the addresses and home names clients use
    still work (Plex, Jellyfin and Channels ask by address)."""
    from dopeiptv.services.home_server import tuner_host_ok
    for ok in (None, "", "192.168.1.5:5004", "127.0.0.1", "[::1]:5004", "[fe80::1]",
               "localhost:5004", "nas", "mypc.local", "box.lan:8000", "tv.home.arpa"):
        assert tuner_host_ok(ok), ok
    for bad in ("evil.example", "evil.example:8000", "attacker.com.", "a.b.c.example.org",
                "[::1", "rebind.it:80"):
        assert not tuner_host_ok(bad), bad
    srv = server[0]
    for path in ("/discover.json", "/lineup.json", "/auto/v5"):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(srv, path, {"Host": "evil.example:8000"})
        assert e.value.code == 403, path
    assert _get(srv, "/discover.json").status == 200          # by its address, as before


def test_the_tuner_answers_under_the_usual_home_names_and_says_what_it_refuses(server,
                                                                              monkeypatch):
    """A Fritz!Box's .fritz.box and Tailscale's MagicDNS names are a home's
    too; a name that is not is refused, and logged once per name - a media
    server set up with it would otherwise lose its tuner with nothing said."""
    from dopeiptv.services.home_server import tuner_host_ok
    for ok in ("pc.fritz.box", "pc.fritz.box:3675", "mypc.tail1a2b3.ts.net", "nas.home.arpa",
               "box.internal", "pc.localdomain", "PC.FRITZ.BOX."):
        assert tuner_host_ok(ok), ok
    for bad in ("evilfritz.box", "fritz.box.evil.example", "ts.net.evil.example",
                "evil-ts.net", "pc.box"):
        assert not tuner_host_ok(bad), bad
    srv = server[0]
    said: list[str] = []

    class Log:
        def info(self, msg, *args):
            said.append(msg % args)

        def __getattr__(self, _name):
            return lambda *a, **k: None

    monkeypatch.setattr(hs, "log", Log())
    for host in ("pc.example.com:3675", "pc.example.com:3675", "nas.example.org"):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(srv, "/lineup.json", {"Host": host})
        assert e.value.code == 403
    refused = [s for s in said if "not a home network's name" in s]
    assert len(refused) == 2, said
    assert "'pc.example.com:3675'" in refused[0] and ".fritz.box" in refused[0]
    assert "'nas.example.org'" in refused[1]
    for i in range(hs.REFUSED_NAMES_MAX + 5):
        srv.refused_name(f"n{i}.example")
    assert len(srv._refused_names) == hs.REFUSED_NAMES_MAX, "a flood of names stays small"


def test_the_discovery_is_not_answered_beyond_its_own_networks():
    from dopeiptv.providers.hdhomerun import discover_packet
    dev = hdhr.new_device_id()
    r = hdhr.Responder(lambda ip: (dev, 2, f"http://{ip}:3675"), port=0, host="127.0.0.1",
                       allowed=lambda _ip: False)
    assert r.start()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(1.5)
        s.sendto(discover_packet(), ("127.0.0.1", r.port))
        with pytest.raises(socket.timeout):
            s.recvfrom(2048)
    finally:
        s.close()
        r.stop()


# -- the person's own choice first, and private viewing ----------------------------------------------

def _asking(answer: bool):
    from dopeiptv.core import connections
    from dopeiptv.ui.mw_connections import _ConnectionsMixin

    full = connections.Verdict(1, 1, 2, 1, 0)

    class Ledger:
        def check(self, *_a, **_k):
            return full

        def limit(self, _src):
            return 1

        def others(self, _src):
            return 0

    class Window(_ConnectionsMixin):
        settings = type("S", (), {"value": staticmethod(lambda _k, d=None: d)})()
        client = None

        def __init__(self) -> None:
            self.room: list = []

        def _server_make_room(self, uses=None, source=None):
            self.room.append(source)

        def _conn_uses(self, free_player=False, free_cell=None):
            return [connections.Use("player", "p1", "http://p.invalid/live/1.ts")]

        def _conn_verdict(self, _uses, **_k):
            return full

        def _tuner_verdict(self, _url, _uses):
            return None

        def _conn_ask(self, _v, _what, _tuner=None):
            return answer

        def _choice_dialog(self, *_a, **_k):
            return 0 if answer else 1

        def _conn_seen(self, *_a, **_k):
            return None

        def _conn_source(self):
            return "p1"

        def _conn_limit(self):
            return 1

        def _conn_ledger(self):
            return Ledger()

        def _conn_warning(self, _v):
            return "full"
    return Window()


@pytest.mark.parametrize("answer", [False, True])
def test_a_device_is_cut_only_once_the_person_said_go_ahead(answer):
    url = "http://p.invalid/live/2.ts"
    for ask in ("_conn_ok_multiview", "_conn_ok_cast", "_conn_ok_cast_buffer", "_conn_ok_test"):
        w = _asking(answer)
        method = getattr(w, ask)
        got = method(None, url) if ask == "_conn_ok_multiview" else (
            method("p1") if ask == "_conn_ok_test" else method(url))
        assert got is answer, ask
        assert bool(w.room) is answer, f"{ask}: a device cut {'only after' if answer else 'on'} the answer"


def test_private_viewing_never_names_the_person_s_channel(server):
    import logging

    from dopeiptv.core import privacy
    from dopeiptv.ui import mw_server

    srv, host, provider, pool = server
    url = f"http://127.0.0.1:{provider.server_address[1]}/live/1.ts"
    lines: list[str] = []

    class Keep(logging.Handler):
        def emit(self, record) -> None:
            lines.append(record.getMessage())

    keep = Keep()
    logger = logging.getLogger("dopeiptv")
    level = logger.level
    logger.addHandler(keep)
    logger.setLevel(logging.INFO)
    privacy.set_private(True)
    try:
        mine = pool.open(url, "live:1")
        host.app_sess, host.app = mine, 1
        assert mine.store.wait_for_data(10)
        a = _watch(srv, f"/stream/p1/1?token={TOKEN}")
        try:
            joined = [m for m in lines if "joins the app's own connection" in m]
            assert joined and not any("One" in m for m in joined), joined
            ch = srv.restream.status()["channels"][0]
            assert ch["shared"] and ch["private"] and "One" not in (ch["name"], ch["stream"])
            w = type("W", (mw_server._ServerMixin,), {})()
            w._home = srv
            told = " ".join(text for _kind, text in w._server_in_use_lines())
            assert "One" not in told and "127.0.0.1" in told, told
        finally:
            a.close()
    finally:
        privacy.set_private(False)
        logger.removeHandler(keep)
        logger.setLevel(level)


# -- channels for adults: out unless the person lets them out ---------------------------------

def test_channels_for_adults_go_out_only_when_the_person_lets_them(server):
    """Off by default (a TV in a child's room has no profile or PIN); on,
    the adult category and the channel the provider marks join the lineup
    and the page's categories - a hidden or locked one never does."""
    from dataclasses import replace
    srv = server[0]
    assert srv.config.adult is False
    assert {c.name for c in srv.lineup()} == {"One", "Two", "Three"}
    srv.refresh(replace(srv.config, adult=True))
    assert {c.name for c in srv.lineup()} == {"One", "Two", "Three", "Night", "Marked"}
    cats = json.loads(_get(srv, "/api/categories?source=p1", {"X-Dopeiptv-Token": TOKEN}).read())
    assert [c["name"] for c in cats["categories"]] == ["News", "XXX Adult"]
    view = json.loads(_get(srv, "/api/settings", {"X-Dopeiptv-Token": TOKEN}).read())
    assert view["adult"] is True


def test_the_adult_switch_is_read_stored_and_checked():
    class Settings:
        def __init__(self, values):
            self.values = values

        def value(self, key, default=None):
            return self.values.get(key, default)

        def setValue(self, key, value):
            self.values[key] = value

    assert hs.read_config(Settings({})).adult is False
    assert hs.read_config(Settings({hs.K_ADULT: "true"})).adult is True
    assert hs.read_config(Settings({hs.K_ADULT: True})).adult is True
    assert hs.read_config(Settings({hs.K_ADULT: "false"})).adult is False
    assert hs.changes_from_page({"adult": True}, ["p1"]) == {hs.K_ADULT: "true"}
    assert hs.changes_from_page({"adult": False}, ["p1"]) == {hs.K_ADULT: "false"}
    with pytest.raises(ValueError):
        hs.changes_from_page({"adult": "yes"}, ["p1"])
    page, _csp = server_page.render({"bg": "#101010"}, {"bg": "#ffffff"},
                                    {"srv_adult": "Channels for adults"})
    assert b'id="adult"' in page and b"Channels for adults" in page


def test_the_share_hands_out_adult_channels_only_when_let():
    from dopeiptv.services.share import Share, kept_out
    cats = {"1": "News", "3": "XXX Adult", "4": "Locked"}
    news = {"stream_id": 1, "category_id": "1"}
    night = {"stream_id": 5, "category_id": "3"}
    marked = {"stream_id": 7, "category_id": "1", "is_adult": "1"}
    locked = {"stream_id": 6, "category_id": "4"}
    by_hand = {"stream_id": 8, "category_id": "1"}
    for adult in (False, True):
        assert not kept_out(news, cats, {"4"}, {"8"}, adult)
        assert kept_out(locked, cats, {"4"}, {"8"}, adult), "locked never goes out"
        assert kept_out(by_hand, cats, {"4"}, {"8"}, adult), "hidden by hand never goes out"
        assert kept_out(night, cats, {"4"}, {"8"}, adult) is not adult
        assert kept_out(marked, cats, {"4"}, {"8"}, adult) is not adult

    let_out = [False]
    share = Share(lambda: [{"id": "p1", "kind": "xtream"}], filters=lambda _pid: ({"4"}, {"8"}, {}),
                  adult=lambda: let_out[0])
    share.channels = lambda _pl: ([news, night, marked, locked, by_hand], cats)  # type: ignore[method-assign]
    pl = {"id": "p1"}
    assert [r["stream_id"] for r in share.handed_out(pl)[0]] == [1]
    let_out[0] = True
    assert [r["stream_id"] for r in share.handed_out(pl)[0]] == [1, 5, 7]


def test_an_address_the_tuner_refuses_is_said_once_with_the_way_in(server, monkeypatch):
    """A media server reaching the tuner over a VPN or Tailscale is refused;
    the log says so once per address, with where to let it in."""
    srv = server[0]
    said: list[str] = []

    class Elsewhere:
        def allows(self, _address, _own=""):
            return False

    class Log:
        def info(self, msg, *args):
            said.append(msg % args)

        def __getattr__(self, _name):
            return lambda *a, **k: None

    monkeypatch.setattr(srv, "local", Elsewhere())
    monkeypatch.setattr(hs, "log", Log())
    for path in ("/discover.json", "/lineup.json"):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(srv, path)
        assert e.value.code == 403
    refused = [s for s in said if "the tuner refused" in s]
    assert len(refused) == 1, said
    assert "127.0.0.1" in refused[0] and "Nerd > Network > Other networks" in refused[0]
    for i in range(hs.REFUSED_NAMES_MAX + 5):
        srv.refused_address(f"10.9.0.{i}")
    assert len(srv._refused_addresses) == hs.REFUSED_NAMES_MAX, "a flood of addresses stays small"
