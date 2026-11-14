"""The remote API and sharing (Nerd > Remote and sharing), asked as a real
client asks: the token on every request, reading with GET and changing with
POST, what a bad request gets back, this computer only unless chosen; one
playlist of every source whose channels a real mpv plays through the app's
redirect; the downloaded guides as one guide, whatever their encoding and
however they were compressed; the window doing what is asked, on its own
thread; and the settings page's Test, which starts the server it checks."""
from __future__ import annotations

import gzip
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dopeiptv.core import remote_api
from dopeiptv.core.nerd_check import FAIL, OK, WARN
from dopeiptv.core.remote_api import MAX_BODY, RemoteServer
from dopeiptv.services import share as share_mod
from dopeiptv.services.share import Share

TOKEN = "test-token-0123456789abcdef"
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)


def ask(port, method, path, token=TOKEN, body=None, how="bearer", headers=None, host="127.0.0.1"):
    conn = http.client.HTTPConnection(host, port, timeout=10)
    h = dict(headers or {})
    if token is not None:
        if how == "bearer":
            h["Authorization"] = f"Bearer {token}"
        elif how == "header":
            h["X-Dopeiptv-Token"] = token
        else:
            path += ("&" if "?" in path else "?") + "token=" + token
    data = body
    if body is not None and not isinstance(body, bytes):
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    conn.request(method, path, body=data, headers=h)
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, raw


class Window:
    """What the server asks of the window, and what it answers."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.answers: dict = {}

    def __call__(self, action, params):
        self.calls.append((action, dict(params)))
        a = self.answers.get(action)
        if isinstance(a, BaseException):
            raise a
        return a if a is not None else {"ok": True}


@pytest.fixture
def window():
    return Window()


@pytest.fixture
def served(window):
    started = []

    def start(share=None, remote=True, host="127.0.0.1"):
        srv = RemoteServer(host, 0, TOKEN, window, share=share, remote=remote)
        srv.start()
        started.append(srv)
        return srv
    yield start
    for srv in started:
        srv.stop()


# -- the token ---------------------------------------------------------------------------

def test_every_request_needs_the_token(served):
    port = served().port
    assert ask(port, "GET", "/", token=None)[0] == 401
    assert ask(port, "GET", "/", token="wrong")[0] == 401
    assert ask(port, "GET", "/", token="")[0] == 401
    assert ask(port, "GET", "/", token=TOKEN[:-1])[0] == 401
    for how in ("bearer", "header", "query"):
        code, _h, raw = ask(port, "GET", "/", how=how)
        assert code == 200, how
        assert json.loads(raw) == {"app": "dopeIPTV", "api": 1, "remote": True, "sharing": False}
    code, _h, raw = ask(port, "GET", "/api/status", token=None)
    assert code == 401 and b"unauthorized" in raw


def test_a_client_that_says_nothing_is_let_go(served, monkeypatch):
    """A connection opened and left silent - or a request never finished -
    is closed after a while instead of holding one of the server's threads
    for as long as the client likes."""
    assert remote_api.REQUEST_TIMEOUT == 15
    monkeypatch.setattr(remote_api, "REQUEST_TIMEOUT", 0.5)
    port = served().port
    for said in (b"", b"GET / HTTP/1.1\r\nHost: x\r\n"):
        s = socket.create_connection(("127.0.0.1", port), timeout=10)
        try:
            s.sendall(said)
            t0 = time.monotonic()
            assert s.recv(1024) == b""                    # closed by the server
            assert time.monotonic() - t0 < 5
        finally:
            s.close()
    assert ask(port, "GET", "/")[0] == 200                # and it still answers


def test_an_empty_token_lets_nobody_in(window):
    srv = RemoteServer("127.0.0.1", 0, "", window)
    srv.start()
    try:
        assert ask(srv.port, "GET", "/", token="")[0] == 401
        assert ask(srv.port, "GET", "/", token=None)[0] == 401
    finally:
        srv.stop()


# -- reading and changing ------------------------------------------------------------------

def test_reading_is_get_and_changing_is_post(served, window):
    port = served().port
    window.answers["status"] = {"state": "stopped", "volume": 80}
    code, h, raw = ask(port, "GET", "/api/status")
    assert code == 200 and json.loads(raw) == {"state": "stopped", "volume": 80}
    assert h["content-type"].startswith("application/json") and h["cache-control"] == "no-store"
    assert ask(port, "POST", "/api/status")[0] == 405
    assert ask(port, "GET", "/api/pause")[0] == 405
    assert ask(port, "POST", "/api/pause")[0] == 200
    for path, action in remote_api.WRITE.items():
        assert ask(port, "POST", path, body={})[0] == 200
        assert window.calls[-1][0] == action
    assert ask(port, "PUT", "/api/play")[0] == 405
    assert ask(port, "DELETE", "/api/play")[0] == 405
    assert ask(port, "GET", "/api/nothing")[0] == 404
    assert ask(port, "GET", "/etc/passwd")[0] == 404


def test_what_a_request_asks_reaches_the_window(served, window):
    port = served().port
    ask(port, "POST", "/api/play", body={"channel": "SVT1 HD"})
    assert window.calls[-1] == ("play", {"channel": "SVT1 HD"})
    ask(port, "POST", "/api/play", body=b"channel=TV4&x=1",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert window.calls[-1] == ("play", {"channel": "TV4", "x": "1"})
    # The query too (curl -X POST ".../api/volume?value=40&token=...") - the token is not a parameter.
    ask(port, "POST", "/api/volume?value=40", how="query")
    assert window.calls[-1] == ("volume", {"value": "40"})
    ask(port, "GET", "/api/channels?q=svt")
    assert window.calls[-1] == ("channels", {"q": "svt"})


def test_a_bad_body_is_refused_before_the_window_hears_of_it(served, window):
    port = served().port
    assert ask(port, "POST", "/api/play", body=b"{not json")[0] == 400
    assert ask(port, "POST", "/api/play", body=b'["a list"]')[0] == 400
    # Too long: refused on the header alone, nothing read.
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(f"POST /api/play HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {TOKEN}\r\n"
                  f"Content-Length: {MAX_BODY + 1}\r\n\r\n".encode())
        assert b" 400 " in s.recv(4096).split(b"\r\n")[0] + b" "
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(f"POST /api/play HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {TOKEN}\r\n"
                  "Content-Length: -5\r\n\r\n".encode())
        assert b"400" in s.recv(4096).split(b"\r\n")[0]
    assert window.calls == []


def test_what_goes_wrong_in_the_window_comes_back_as_an_answer(served, window):
    port = served().port
    window.answers["play"] = ValueError('give "channel" (a name) or "url"')
    code, _h, raw = ask(port, "POST", "/api/play", body={})
    assert code == 400 and json.loads(raw)["error"].startswith('give "channel"')
    window.answers["status"] = TimeoutError("status")
    assert ask(port, "GET", "/api/status")[0] == 503
    window.answers["stop"] = RuntimeError("boom")
    code, _h, raw = ask(port, "POST", "/api/stop")
    assert code == 500 and json.loads(raw) == {"error": "RuntimeError"}
    # And the server carries on.
    window.answers.clear()
    assert ask(port, "POST", "/api/stop")[0] == 200


def test_with_remote_control_off_only_sharing_answers(served, window):
    class Lists:
        def m3u(self, base, token):
            return "#EXTM3U\n"

        def guide(self, gz):
            yield b"<tv/>"

        def stream_url(self, s, c):
            return None
    port = served(share=Lists(), remote=False).port
    assert ask(port, "GET", "/api/status")[0] == 404
    assert ask(port, "POST", "/api/pause")[0] == 404
    assert ask(port, "GET", "/playlist.m3u")[0] == 200
    assert window.calls == []
    port2 = served().port
    assert ask(port2, "GET", "/playlist.m3u")[0] == 404
    assert ask(port2, "GET", "/stream/a/1")[0] == 404


def test_a_taken_port_is_said(window):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        with pytest.raises(OSError):
            RemoteServer("127.0.0.1", s.getsockname()[1], TOKEN, window).start()
    finally:
        s.close()


def test_this_computer_only_unless_the_home_network_is_chosen(served):
    ips = remote_api.lan_addresses()
    if not ips:
        pytest.skip("no network address on this machine")
    local = served()
    with pytest.raises(OSError):
        socket.create_connection((ips[0], local.port), timeout=3).close()
    home = served(host="0.0.0.0")
    assert ask(home.port, "GET", "/", host=ips[0])[0] == 200
    assert ask(home.port, "GET", "/", host="127.0.0.1")[0] == 200


def test_the_check_says_how_it_stands(served):
    srv = served()
    c = remote_api.check_server("127.0.0.1", srv.port, TOKEN, True, False, True)
    assert (c.state, c.key, c.params["where"]) == (OK, "nerd_ck_remote_ok", f"127.0.0.1:{srv.port}")
    assert "refused" in c.detail and "/api/status" in c.detail
    assert TOKEN not in c.detail                        # never the secret itself
    assert remote_api.check_server("127.0.0.1", srv.port, TOKEN, False, False, False).state == WARN
    down = remote_api.check_server("127.0.0.1", 1, TOKEN, True, False, False, "OSError: in use")
    assert (down.state, down.key, down.detail) == (FAIL, "nerd_ck_remote_down", "OSError: in use")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
    s.close()
    assert remote_api.check_server("127.0.0.1", free, TOKEN, True, False, True).key == "nerd_ck_remote_noanswer"
    srv.authorized = lambda given: True                 # a server that lets anyone in
    assert remote_api.check_server("127.0.0.1", srv.port, TOKEN, True, False, True).key == "nerd_ck_remote_open"


# -- sharing: one playlist ------------------------------------------------------------------

class M3USource:
    """A plain M3U playlist on the network."""

    def __init__(self, text: str):
        body = text.encode()

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "audio/x-mpegurl")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/list.m3u"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def sources():
    from tests.test_sources_side_by_side import Xtream
    xt = Xtream()
    m3u = M3USource('#EXTM3U\n#EXTINF:-1 tvg-id="svt1.se" tvg-logo="http://l/svt1.png" group-title="Public",SVT1 "HD"\n'
                    "http://127.0.0.1:9/svt1.ts\n#EXTINF:-1,Radio, the one\nhttp://127.0.0.1:9/radio.ts\n")
    pls = [{"id": "main", "name": "Main", "kind": "xtream", "server": xt.base, "username": "anna", "password": "pw-anna"},
           {"id": "pub", "name": "Public TV", "kind": "m3u", "server": m3u.url},
           {"id": "files", "name": "Files", "kind": "local"},
           {"id": "gone", "name": "Gone", "kind": "m3u", "server": "http://127.0.0.1:9/none.m3u"}]
    yield xt, pls
    xt.close()
    m3u.close()


def _entries(text: str) -> list[tuple[str, str]]:
    lines = [ln for ln in text.splitlines() if ln]
    return [(lines[i], lines[i + 1]) for i in range(1, len(lines) - 1, 2)]


def test_every_source_as_one_playlist(sources):
    xt, pls = sources
    text = Share(lambda: pls).m3u("http://192.168.1.5:3673", TOKEN)
    head = text.splitlines()[0]
    assert head.startswith("#EXTM3U") and f'url-tvg="http://192.168.1.5:3673/guide.xml.gz?token={TOKEN}"' in head
    entries = _entries(text)
    assert len(entries) == 4                             # 2 + 2; files and the unreachable one left out
    tv4, k5, svt, radio = entries
    assert 'tvg-id="tv4.se"' in tv4[0] and 'tvg-chno="1"' in tv4[0] and 'group-title="Main · Sverige"' in tv4[0]
    assert tv4[0].endswith(",TV4 HD")
    assert tv4[1] == f"http://192.168.1.5:3673/stream/main/401?token={TOKEN}"
    assert "tvg-id" not in k5[0] and k5[1].endswith(f"/stream/main/405?token={TOKEN}")
    # A quote in a name cannot break the attribute it is written into.
    assert 'tvg-name="SVT1 HD"' in svt[0]
    assert 'group-title="Public TV · Public"' in svt[0] and 'tvg-logo="http://l/svt1.png"' in svt[0]
    assert radio[0].endswith(",Radio, the one") and 'group-title="Public TV · Uncategorized"' in radio[0]
    # No credentials in the list: a channel is an address on this app.
    assert "pw-anna" not in text and xt.base not in text
    # One source alone: its own categories, no source name in front.
    one = Share(lambda: pls[:1]).m3u("http://h", TOKEN)
    assert 'group-title="Sverige"' in one


def test_lists_are_kept_a_while_and_an_edited_source_gets_a_new_client(sources):
    xt, pls = sources
    sh = Share(lambda: pls[:1])
    sh.m3u("http://h", TOKEN)
    n = sum(1 for r in xt.requests if r["path"] == "/player_api.php")
    client = sh._client(pls[0])
    sh.m3u("http://h", TOKEN)
    assert sum(1 for r in xt.requests if r["path"] == "/player_api.php") == n
    assert sh._client(pls[0]) is client
    pls[0] = dict(pls[0], password="changed")
    assert sh._client(pls[0]) is not client                 # an edited source: a client of its own


def test_a_channel_is_sent_on_to_its_source_and_a_real_mpv_plays_it(served, sources):
    from tests.test_nerd import _libmpv, _plays
    xt, pls = sources
    srv = served(share=Share(lambda: pls))
    code, h, raw = ask(srv.port, "GET", "/playlist.m3u")
    assert code == 200 and h["content-type"].startswith("audio/x-mpegurl")
    # The addresses inside are the ones this client used to reach the app.
    tv4 = next(url for info, url in _entries(raw.decode()) if info.endswith(",TV4 HD"))
    assert tv4 == f"http://127.0.0.1:{srv.port}/stream/main/401?token={TOKEN}"
    code, h, _ = ask(srv.port, "GET", "/stream/main/401")
    assert code == 302 and h["location"] == f"{xt.base}/live/anna/pw-anna/401.ts"
    # Only channels the app listed, of sources it shares.
    for path in ("/stream/main/999", "/stream/files/1", "/stream/nope/401", "/stream/main",
                 "/stream/..%2F..%2Fetc/passwd", "/stream/main/401/x"):
        assert ask(srv.port, "GET", path)[0] == 404, path
    assert ask(srv.port, "GET", "/stream/main/401", token="wrong")[0] == 401
    if _libmpv is None:
        pytest.skip("libmpv not available")
    player = _libmpv()
    try:
        assert _plays(player, tv4)                       # through the redirect, as Kodi or VLC would
    finally:
        player.terminate()


def test_a_bad_host_header_cannot_write_the_playlist(served, sources):
    _xt, pls = sources
    srv = served(share=Share(lambda: pls[:1]))
    _c, _h, raw = ask(srv.port, "GET", "/playlist.m3u", headers={"Host": 'evil.example"><script>'})
    assert "evil" not in raw.decode() and f"http://127.0.0.1:{srv.port}/stream/main/401" in raw.decode()


# -- sharing: one guide ---------------------------------------------------------------------

GUIDE_A = ('<?xml version="1.0" encoding="ISO-8859-1"?>\n<!DOCTYPE tv SYSTEM "xmltv.dtd">\n'
           '<tv generator-info-name="a">\n<channel id="ost.se"><display-name>Östersund TV</display-name></channel>\n'
           '<programme start="20261114200000 +0100" stop="20261114210000 +0100" channel="ost.se">'
           "<title>Nyheter för Jämtland</title></programme>\n</tv>\n\n\n")
GUIDE_B = ('<?xml version="1.0" encoding="UTF-8"?>\n<tv>\n<channel id="tv4.se"><display-name>TV4</display-name></channel>\n'
           '<programme start="20261114200000 +0100" stop="20261114213000 +0100" channel="tv4.se">'
           "<title>Nyheterna – 東京</title></programme>\n"
           '<programme start="20261114213000 +0100" stop="20261114220000 +0100" channel="tv4.se">'
           "<title>Sporten</title></programme>\n</tv>")


@pytest.fixture
def guides(tmp_path):
    a = tmp_path / "epg_a.xml"
    raw = GUIDE_A.encode("iso-8859-1")
    a.write_bytes(gzip.compress(raw[:120]) + gzip.compress(raw[120:]))     # two gzip members
    b = tmp_path / "epg_b.xml"
    b.write_bytes(GUIDE_B.encode())                                          # an older plain cache
    bad = tmp_path / "epg_bad.xml"
    bad.write_bytes(gzip.compress(b"<tv><channel id='x'/></tv>")[:20])        # cut short
    empty = tmp_path / "epg_empty.xml"
    empty.write_bytes(b'<?xml version="1.0"?><tv generator-info-name="x"/>')
    paths = {"a": str(a), "b": str(b), "bad": str(bad), "empty": str(empty)}
    pls = [{"id": k, "name": k, "kind": "m3u"} for k in ("a", "bad", "missing", "empty", "b")]
    return Share(lambda: pls, guide_path=lambda pid: paths.get(pid, str(tmp_path / "none.xml")))


def test_the_guides_as_one(guides):
    from dopeiptv.providers.epg import parse_xmltv
    data = b"".join(guides.guide())
    root = ET.fromstring(data)
    assert root.tag == "tv" and root.get("generator-info-name") == "dopeIPTV"
    assert [c.get("id") for c in root.findall("channel")] == ["ost.se", "tv4.se"]
    titles = [p.findtext("title") for p in root.findall("programme")]
    assert titles == ["Nyheter för Jämtland", "Nyheterna – 東京", "Sporten"]
    assert root.find("channel/display-name").text == "Östersund TV"
    assert data.count(b"<tv") == 1 and data.count(b"</tv>") == 1
    # What the app's own guide reader makes of it, as another player would.
    by_id, by_name = parse_xmltv(data, 3650)
    assert set(by_id) >= {"ost.se", "tv4.se"}
    gz = b"".join(guides.guide(gz=True))
    assert gzip.decompress(gz) == data


@pytest.mark.parametrize("step", [1, 2, 7, 4096, 1 << 20])
def test_the_guide_reads_the_same_however_it_arrives(step):
    raw = GUIDE_B.encode()
    chunks = (raw[i:i + step] for i in range(0, len(raw), step))
    body = b"".join(share_mod.guide_body(chunks))
    assert body.strip().startswith(b"<channel id=\"tv4.se\">") and body.strip().endswith(b"</programme>")
    assert b"</tv>" not in body and b"<?xml" not in body


def test_not_a_guide_is_said():
    with pytest.raises(ValueError):
        list(share_mod.guide_body(iter([b"<html><body>Login</body></html>"])))
    assert list(share_mod.guide_body(iter([b""]))) == []


def test_the_guide_through_the_server(served, guides):
    srv = served(share=guides)
    code, h, raw = ask(srv.port, "GET", "/guide.xml.gz")
    assert code == 200 and h["content-type"] == "application/gzip"
    plain = gzip.decompress(raw)
    assert ET.fromstring(plain).findall("programme")
    code, h, raw = ask(srv.port, "GET", "/guide.xml")
    assert code == 200 and raw == plain and h["content-type"].startswith("application/xml")


# -- the window --------------------------------------------------------------------------------

_CHILD = r"""
import json, os, socket, sys, tempfile, threading, http.client
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
from dopeiptv.core import nerd
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import make_client
from dopeiptv.ui.main_window import MainWindow
from tests.test_sources_side_by_side import Xtream

app = QApplication.instance() or QApplication([])
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

xt = Xtream()
port = free_port()
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
pls = [{"id": "a", "name": "Main", "kind": "xtream", "server": xt.base, "username": "anna", "password": "pw-anna"}]
s.setValue("playlists", json.dumps(pls))
s.setValue("active_playlist", "a")
for k, v in {"nerd_enabled": "true", "nerd_remote": "true", "nerd_share": "true",
             "nerd_remote_port": str(port)}.items():
    s.setValue(k, v)
nerd.install(s)
store = PlaylistStore(s)
w = MainWindow(make_client(pls[0]), s, store)
played, media, toasts = [], [], []
w._start_playback = lambda url, title, icon, key, kind, record=True, item=None, catchup=False: played.append((url, title, kind))
w.mpris_command = lambda name, arg=None: media.append((name, arg))
w._show_toast = lambda text, ms=0: toasts.append(text)

st = w._remote_apply()
assert st["running"] and st["port"] == port and st["host"] == "127.0.0.1", st
token = s.value("nerd_remote_token")
assert token and len(token) >= 30 and st["token"] == token          # made at first start, kept

def ask(method, path, body=None, tok=None):
    out = {}
    def run():
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            h = {"Authorization": "Bearer " + (tok or token)}
            data = None
            if body is not None:
                data = json.dumps(body).encode(); h["Content-Type"] = "application/json"
            c.request(method, path, body=data, headers=h)
            r = c.getresponse(); out["code"] = r.status; out["raw"] = r.read(); c.close()
        except Exception as e:
            out["error"] = e
    t = threading.Thread(target=run); t.start()
    while t.is_alive():
        app.processEvents(); t.join(0.01)
    if "error" in out:
        raise out["error"]
    return out["code"], (json.loads(out["raw"]) if out["raw"][:1] in (b"{", b"[") else out["raw"])

def wait(cond, n=500):
    for _ in range(n):
        app.processEvents(); w.pool.waitForDone(5)
        if cond():
            return True
    return False

code, status = ask("GET", "/api/status")
assert code == 200 and status == {"state": "stopped", "volume": status["volume"], "source": "Main"}, status
assert ask("GET", "/api/status", tok="nope")[0] == 401

code, out = ask("GET", "/api/channels?q=tv4")
assert code == 200 and out["channels"] == [{"id": 401, "name": "TV4 HD", "number": 1, "guide_id": "tv4.se"}], out
code, out = ask("GET", "/api/sources")
assert out == {"sources": [{"id": "a", "name": "Main", "kind": "xtream", "active": True}]}, out

code, out = ask("POST", "/api/play", {"channel": "tv4"})
assert code == 200 and wait(lambda: played), played
assert played[-1][0] == xt.base + "/live/anna/pw-anna/401.ts" and played[-1][2] == "live", played
code, out = ask("POST", "/api/play", {"url": "http://127.0.0.1:9/some/clip.ts"})
assert code == 200 and played[-1][:2] == ("http://127.0.0.1:9/some/clip.ts", "clip.ts"), played
code, out = ask("POST", "/api/play", {})
assert code == 400 and "channel" in out["error"]
# A stream, never a file of this computer's - by address or by path.
n = len(played)
for bad in ("file:///etc/passwd", "/etc/passwd", "javascript:alert(1)"):
    code, out = ask("POST", "/api/play", {"url": bad})
    assert code == 400 and "stream" in out["error"], (bad, code, out)
assert len(played) == n, played

code, out = ask("POST", "/api/volume", {"value": 150})
assert code == 200 and out["volume"] == 100 and media[-1] == ("volume", 1.0), media
assert ask("POST", "/api/volume", {"value": "loud"})[0] == 400
ask("POST", "/api/seek", {"by": -30}); assert media[-1] == ("seek", -30.0)
ask("POST", "/api/seek", {"to": 600}); assert media[-1] == ("set_position", 600.0)
assert ask("POST", "/api/seek", {})[0] == 400
for path, name in (("/api/toggle", "playpause"), ("/api/pause", "pause"), ("/api/resume", "play"),
                   ("/api/stop", "stop"), ("/api/next", "next"), ("/api/previous", "previous")):
    assert ask("POST", path)[0] == 200 and media[-1] == (name, None), (path, media[-1])
# Twice in a row is two presses for a script (the media keys' guard is not in the way).
n = len(media); ask("POST", "/api/toggle"); ask("POST", "/api/toggle")
assert len(media) == n + 2

code, raw = ask("GET", "/playlist.m3u")
text = raw.decode()
assert code == 200 and ",TV4 HD" in text and "pw-anna" not in text and f"/stream/a/401?token={token}" in text

# Remote control off, sharing on: the same port, only the lists.
s.setValue("nerd_remote", "false"); nerd.install(s); w._remote_apply()
assert ask("GET", "/api/status")[0] == 404 and ask("GET", "/playlist.m3u")[0] == 200
# Both off: the port is closed.
s.setValue("nerd_share", "false"); nerd.install(s)
assert not w._remote_apply()["running"]
try:
    socket.create_connection(("127.0.0.1", port), timeout=2).close()
    raise AssertionError("still listening")
except ConnectionRefusedError:
    pass
# Nerd off: nothing, whatever the switches say.
s.setValue("nerd_remote", "true"); s.setValue("nerd_enabled", "false"); nerd.install(s)
assert not w._remote_apply()["running"]
# A port another program holds: said, not a crash; and tried again once it is free.
s.setValue("nerd_enabled", "true"); nerd.install(s)
busy = socket.socket(); busy.bind(("127.0.0.1", 0)); busy.listen(1)
s.setValue("nerd_remote_port", str(busy.getsockname()[1]))
st = w._remote_apply()
assert not st["running"] and st["error"] and str(busy.getsockname()[1]) in toasts[-1], (st, toasts)
busy.close()
assert w._remote_apply({k: st[k] for k in ("remote", "share", "port", "host", "token")})["running"]
w._remote_stop()
xt.close()
print("REMOTE_WINDOW_OK")
"""


def test_the_window_does_what_it_is_asked():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "REMOTE_WINDOW_OK" in out.stdout, out.stdout + out.stderr[-4000:]


# -- the settings page -------------------------------------------------------------------------

def test_the_page_turns_it_on_tests_it_and_stores_it(tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import QSettings
    from PyQt6.QtGui import QPixmap
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QDialog, QLineEdit, QPushButton

    from dopeiptv.core import nerd
    from dopeiptv.i18n import tr
    from dopeiptv.ui import nerd_settings
    from dopeiptv.ui.mw_remote import _RemoteMixin
    from dopeiptv.ui.settings_ui import SettingsPage, SettingsShell, Stepper, ToggleSwitch, page_icon
    app = QApplication.instance() or QApplication([])
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.setValue("nerd_enabled", "true")
    nerd.install(s)

    class Win(_RemoteMixin):
        def __init__(self):
            self.settings = s
            self.toasts = []

        def _show_toast(self, text, ms=0):
            self.toasts.append(text)
    win = Win()
    shell = SettingsShell("Search", "none")
    blank = lambda k, sz, c: QPixmap(sz, sz)  # noqa: E731
    page = shell.add_page(SettingsPage("nerd", "Nerd", ""), page_icon("nerd", blank))
    serve = shell.add_page(SettingsPage("nerd_serve", "Remote", ""), page_icon("remote", blank))
    try:
        commit = nerd_settings.build(page, s, QDialog(), pages={"serve": serve}, remote=win._remote_apply)
        switches = {sw.accessibleName(): sw for sw in serve.findChildren(ToggleSwitch)}
        port = next(st for st in serve.findChildren(Stepper))
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        free = sock.getsockname()[1]
        sock.close()
        port.spin.setValue(free)
        switches[tr("nerd_remote")].setChecked(True)
        line = next(ln for ln in nerd_settings_lines(commit) if ln._title == tr("nerd_ck_title_remote"))
        line.test_btn.click()
        for _ in range(250):
            QTest.qWait(20)
            if line.result is not None:
                break
        assert line.result.state == OK, (line.result.key, line.result.detail)
        assert win._remote is not None and win._remote.port == free
        token = win._remote.token
        assert len(token) >= 30 and token not in line.text.text()
        stored = {}
        commit(lambda k, v: stored.__setitem__(k, v))
        assert stored["nerd_remote"] == "true" and stored["nerd_remote_token"] == token
        assert stored["nerd_remote_port"] == str(free) and stored.get("nerd_remote_lan", "false") == "false"
        # The playlist address on the clipboard, token and all.
        copy = next(b for b in serve.findChildren(QPushButton) if b.text() == tr("nerd_share_copy_m3u"))
        copy.click()
        assert QApplication.clipboard().text() == f"http://127.0.0.1:{free}/playlist.m3u?token={token}"
        # A new token locks the old one out once tested (as it would once stored).
        new = next(b for b in serve.findChildren(QPushButton) if b.text() == tr("nerd_remote_new_token"))
        new.click()
        line.test_btn.click()
        for _ in range(250):
            QTest.qWait(20)
            if line.result is not None and win._remote.token != token:
                break
        assert win._remote.token != token
        assert ask(free, "GET", "/", token=token)[0] == 401
        assert ask(free, "GET", "/", token=win._remote.token)[0] == 200
        # Removed: both off, the port closed, and turning one on makes a new token.
        drop = next(b for b in serve.findChildren(QPushButton) if b.text() == tr("nerd_remote_drop_token"))
        last = win._remote.token
        drop.click()
        assert not switches[tr("nerd_remote")].isChecked() and not switches[tr("nerd_share")].isChecked()
        line.test_btn.click()
        for _ in range(100):
            QTest.qWait(20)
            if line.result is not None:
                break
        assert win._remote is None and line.result.key == "nerd_ck_remote_off"
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", free), timeout=2).close()
        switches[tr("nerd_share")].setChecked(True)
        made = [e for e in serve.findChildren(QLineEdit) if e.accessibleName() == tr("nerd_remote_token")][0].text()
        assert len(made) >= 30 and made != last
        # The two copy buttons get the room their words need.
        for b in (copy, ):
            assert b.width() >= b.sizeHint().width() or not b.isVisible()
    finally:
        win._remote_stop()
        nerd.install(None)
        app.processEvents()


def nerd_settings_lines(commit):
    return commit.lines


def test_a_guide_cut_short_keeps_what_was_whole(tmp_path):
    raw = GUIDE_B.encode()
    cut = raw.index(b"<title>Sporten")
    cases = {"plain, cut in a programme": raw[:cut],
             "gzip whole, its guide cut": gzip.compress(raw[:cut]),
             "gzip cut in the middle": gzip.compress(raw)[: len(gzip.compress(raw)) // 2]}
    for name, data in cases.items():
        p = tmp_path / "g.xml"
        p.write_bytes(data)
        pieces = []
        with pytest.raises(ValueError):
            for piece in share_mod.guide_body(share_mod.plain_chunks(str(p))):
                pieces.append(piece)
        body = b"".join(pieces)
        root = ET.fromstring(b"<tv>" + body + b"</tv>")          # whole elements only
        assert b"<title>Sporten" not in body, name
        if name != "gzip cut in the middle":
            assert [c.get("id") for c in root.findall("channel")] == ["tv4.se"], name
            assert [x.findtext("title") for x in root.findall("programme")] == ["Nyheterna – 東京"], name
