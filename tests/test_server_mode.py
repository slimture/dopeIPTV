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

    class Hls(BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:
            asked.append(self.path)
            if self.path.endswith(".m3u8"):
                body = ("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n"
                        + "".join(f"#EXTINF:2.0,\nseg{i}.ts\n" for i in range(3))
                        + "#EXT-X-ENDLIST\n").encode()
                ctype = "application/vnd.apple.mpegurl"
            else:
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
