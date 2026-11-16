"""Test the connection to a source (core/conn_test.py): each step against a
fake Xtream server on the loopback - the name looked up, the connection,
TLS (plain http: nothing to check; a server that speaks no TLS: a failure),
the login and what it says of the account, a live stream's first byte and
its speed - one that fails ends the steps after it, cancel stops it, and
the text to copy carries no login, address or MAC."""
from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from dopeiptv.core import conn_test as ct
from dopeiptv.core import nerd
from dopeiptv.providers.client import make_client


@pytest.fixture(autouse=True)
def _local_is_direct(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost,::1")
    nerd.install(None)
    yield
    nerd.install(None)


class _S(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value


class Xtream:
    """player_api.php with a login, a channel list, and a live stream that
    sends *rate* bytes a second (0: as fast as it can) for *secs*."""

    def __init__(self, login_status: int = 200, auth: int = 1, stream_status: int = 200,
                 rate: int = 0, hls: bool = False) -> None:
        self.asked: list[str] = []
        srv = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                parts = urlsplit(self.path)
                srv.asked.append(parts.path)
                q = {k: v[0] for k, v in parse_qs(parts.query).items()}
                if parts.path == "/player_api.php":
                    if login_status != 200:
                        return self._send(login_status, b"no")
                    if q.get("action") == "get_live_streams":
                        return self._send(200, json.dumps(
                            [{"stream_id": 11, "name": "First"}]).encode())
                    exp = int(time.time()) + 40 * 86400
                    return self._send(200, json.dumps({"user_info": {
                        "auth": auth, "status": "Active", "exp_date": str(exp),
                        "max_connections": "2", "active_cons": "1"}}).encode())
                if parts.path.startswith("/live/"):
                    if stream_status != 200:
                        return self._send(stream_status, b"")
                    if hls:
                        return self._send(200, b"#EXTM3U\n#EXT-X-TARGETDURATION:2\n"
                                          b"#EXTINF:2,\n/seg/1.ts\n#EXTINF:2,\n/seg/2.ts\n")
                    self.send_response(200)
                    self.send_header("Content-Type", "video/mp2t")
                    self.end_headers()
                    end = time.monotonic() + 3
                    try:
                        while time.monotonic() < end:
                            self.wfile.write(b"G" * 18800)
                            if rate:
                                time.sleep(18800 / rate)
                    except OSError:
                        pass
                    return
                if parts.path.endswith(".ts"):
                    return self._send(200, b"G" * 400_000)
                self._send(404, b"")

            def _send(self, code, body):
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def xtream():
    made = []

    def make(**kw):
        s = Xtream(**kw)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


def source(url: str, **kw) -> dict:
    return dict({"id": "pl1", "name": "My Provider", "kind": "xtream", "server": url,
                 "username": "alice", "password": "s3cretpw"}, **kw)


def run(src: dict, settings=None, secs: float = 0.6) -> ct.ConnTest:
    t = ct.ConnTest(src, make_client, settings, speed_secs=secs)
    t.run()
    assert t.finished
    return t


def states(t: ct.ConnTest) -> dict[str, str]:
    return {s.name: s.state for s in t.snapshot()}


def test_every_step_of_a_good_source(xtream):
    srv = xtream()
    t = run(source(srv.url))
    st = {s.name: s for s in t.snapshot()}
    assert states(t) == {"dns": ct.OK, "tcp": ct.OK, "tls": ct.SKIP, "login": ct.OK,
                         "stream": ct.OK, "speed": ct.OK}
    assert "127.0.0.1" in st["dns"].params["addrs"]
    assert st["tcp"].params["port"] == srv.httpd.server_address[1]
    assert st["tls"].key == "ct_tls_plain"
    assert st["login"].params["code"] == 200
    more = dict(st["login"].more)
    assert more["conn_in_use"] == {"n": 1, "max": 2}
    assert more["ct_acct_ends"]["days"] in (39, 40)
    assert st["stream"].params["channel"] == "First" and st["stream"].ms is not None
    assert t.mbps is not None and t.mbps > 25 and st["speed"].key == "ct_speed_uhd"
    assert all(s.ms is not None for s in t.snapshot() if s.state == ct.OK)


def test_the_channel_last_played_is_the_one_tried(xtream):
    srv = xtream()
    hist = _S(**{"history_pl1": json.dumps([
        {"_kind": "movie", "stream_id": 99, "name": "A film"},
        {"_kind": "live", "stream_id": 42, "name": "Last one"}])})
    t = run(source(srv.url), hist)
    assert [s for s in t.snapshot() if s.name == "stream"][0].params["channel"] == "Last one"
    assert "/live/alice/s3cretpw/42.ts" in srv.asked
    assert ct.last_live(hist, "pl1") == (42, "Last one")
    assert ct.last_live(hist, "other") is None


def test_a_slow_channel_is_said_for_what_it_is(xtream):
    srv = xtream(rate=500_000)          # 4 Mbit/s: SD, not HD
    t = run(source(srv.url), secs=1.0)
    sp = [s for s in t.snapshot() if s.name == "speed"][0]
    assert sp.state == ct.WARN and sp.key == "ct_speed_sd"
    assert ("ct_speed_paced", {}) in sp.more
    assert ct.met(t.mbps) == [True, False, False, False]


def test_a_refused_login_ends_it_there(xtream):
    srv = xtream(login_status=403)
    t = run(source(srv.url))
    assert states(t)["login"] == ct.FAIL
    st = {s.name: s for s in t.snapshot()}
    assert st["login"].key == "ct_login_denied" and st["login"].params["code"] == 403
    assert st["stream"].state == ct.SKIP and st["stream"].key == "ct_skip"
    assert st["speed"].state == ct.SKIP
    t2 = run(source(xtream(auth=0).url))
    assert {s.name: s.key for s in t2.snapshot()}["login"] == "ct_login_wrong"


def test_a_stream_that_is_refused(xtream):
    srv = xtream(stream_status=458)
    t = run(source(srv.url))
    st = {s.name: s for s in t.snapshot()}
    assert st["stream"].state == ct.FAIL and st["stream"].params["code"] == 458
    assert st["speed"].state == ct.SKIP


def test_told_not_to_it_never_opens_the_stream(xtream):
    # One more connection beside what plays was not wanted (the window
    # asked): the network and the login are tested, no stream is opened.
    srv = xtream()
    t = ct.ConnTest(source(srv.url), make_client, None, speed_secs=0.3, stream=False)
    t.run()
    st = {s.name: s for s in t.snapshot()}
    assert st["login"].state == ct.OK
    assert (st["stream"].state, st["stream"].key) == (ct.SKIP, "ct_skip_conn")
    assert (st["speed"].state, st["speed"].key) == (ct.SKIP, "ct_skip_conn")
    assert not [p for p in srv.asked if p.startswith("/live/")] and t.mbps is None


def test_nothing_listens(xtream):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    t = run(source(f"http://127.0.0.1:{port}"))
    st = {x.name: x for x in t.snapshot()}
    assert st["dns"].state == ct.OK and st["tcp"].state == ct.FAIL
    assert st["tcp"].key == "ct_tcp_refused"
    assert st["login"].state == ct.SKIP


def test_an_unknown_name():
    t = run(source("http://no-such-host.invalid:8080"))
    st = {x.name: x for x in t.snapshot()}
    assert st["dns"].state == ct.FAIL and st["dns"].key == "ct_dns_fail"
    assert st["tcp"].state == ct.SKIP


def test_tls_against_a_server_that_speaks_none(xtream):
    srv = xtream()
    url = srv.url.replace("http://", "https://")
    t = run(source(url))
    st = {x.name: x for x in t.snapshot()}
    assert st["tcp"].state == ct.OK and st["tls"].state == ct.FAIL
    assert st["tls"].key == "ct_tls_fail" and st["login"].state == ct.SKIP


def test_hls_is_read_segment_by_segment(xtream):
    srv = xtream(hls=True)
    t = run(source(srv.url))
    assert states(t)["speed"] == ct.OK
    assert "/seg/1.ts" in srv.asked
    assert not t.paced and t.mbps is not None and t.mbps > 1


def test_cancel_stops_it_mid_read(xtream):
    srv = xtream(rate=200_000)
    t = ct.ConnTest(source(srv.url), make_client, None, speed_secs=30)
    th = t.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and states(t)["speed"] != ct.RUN:
        time.sleep(0.05)
    t0 = time.monotonic()
    t.cancel()
    th.join(5)
    assert not th.is_alive() and time.monotonic() - t0 < 5
    assert t.finished and states(t)["speed"] == ct.SKIP
    assert {s.name: s.key for s in t.snapshot()}["speed"] == "ct_skip_cancel"


def test_a_file_has_no_network_steps(tmp_path):
    f = tmp_path / "list.m3u"
    f.write_text("#EXTM3U\n#EXTINF:-1,Local one\nhttp://127.0.0.1:1/x.ts\n", encoding="utf-8")
    t = run({"kind": "m3u", "server": str(f)})
    st = {x.name: x for x in t.snapshot()}
    assert [st[n].key for n in ("dns", "tcp", "tls")] == ["ct_file"] * 3
    assert st["login"].state == ct.OK and st["login"].params["n"] == 1
    assert st["stream"].state == ct.FAIL


class _NoLive:
    """A source that answers and has no live channel to play: a media
    server without Live TV, MythTV (channels for the guide only), films."""

    def __init__(self, url: str, rows=(), address: str = "") -> None:
        self.server, self.rows, self.address = url, list(rows), address

    def authenticate(self) -> dict:
        return {}

    def live_streams(self, _category=None) -> list[dict]:
        return list(self.rows)

    def live_url(self, _sid, _fmt="ts") -> str:
        return self.address


def _no_live_run(srv, kind: str, rows=(), address: str = "") -> ct.ConnTest:
    t = ct.ConnTest({"id": "ms", "kind": kind, "server": srv.url},
                    lambda s: _NoLive(s["server"], rows, address), None, speed_secs=0.3)
    t.run()
    assert t.finished
    return t


def test_a_source_without_live_channels_has_nothing_to_try(xtream):
    # Jellyfin's demo server has no Live TV: the stream and its speed are not
    # applicable - the neutral mark, not a red failure, and nothing after a
    # failed step.
    srv = xtream()
    for kind, rows, login in (
            ("jellyfin", [], "ct_server_ok"), ("plex", [], "ct_server_ok"),
            ("mythtv", [{"stream_id": 5, "name": "BBC One", "_no_live": True}], "ct_box_ok")):
        st = {s.name: s for s in _no_live_run(srv, kind, rows).snapshot()}
        assert (st["login"].state, st["login"].key) == (ct.OK, login), kind
        assert (st["stream"].state, st["stream"].key) == (ct.SKIP, "ct_stream_no_live"), kind
        assert (st["speed"].state, st["speed"].key) == (ct.SKIP, "ct_speed_no_live"), kind
        assert not [s for s in st.values() if s.state == ct.FAIL], kind
    assert not [p for p in srv.asked if p.startswith("/live/")]


def test_a_channel_without_an_address_still_fails(xtream):
    srv = xtream()
    st = {s.name: s for s in _no_live_run(
        srv, "tvheadend", [{"stream_id": 1, "name": "One"}]).snapshot()}
    assert (st["stream"].state, st["stream"].key) == (ct.FAIL, "ct_stream_none")
    assert (st["speed"].state, st["speed"].key) == (ct.SKIP, "ct_skip")


def test_the_window_shows_no_live_channel_as_not_applicable():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication

    from dopeiptv.i18n import tr
    from dopeiptv.ui import conn_test_dialog as dlg
    from dopeiptv.ui.theme import P
    # Kept: a QApplication made here and dropped at once is gone before the
    # widget (alone, the file aborted).
    app = QApplication.instance() or QApplication([])
    assert app is not None
    row = dlg.StepRow("stream")
    row.show_step(ct.Step("stream", ct.SKIP, None, "ct_stream_no_live"))
    assert row.mark.text() == dlg.MARKS[ct.SKIP] == "–"
    assert P["error"] not in row.result.styleSheet()
    assert row.result.text() == tr("ct_stream_no_live")


def test_speed_words():
    assert ct.speed_key(2.0) == "ct_speed_low"
    assert ct.speed_key(3.0) == "ct_speed_sd"
    assert ct.speed_key(7.5) == "ct_speed_hd"
    assert ct.speed_key(12.0) == "ct_speed_fhd"
    assert ct.speed_key(80.0) == "ct_speed_uhd"
    assert ct.met(None) == [False] * 4
    assert [n for n, _v in ct.NEEDS] == ["sd", "hd", "fhd", "uhd"]
    assert [v for _n, v in ct.NEEDS] == [3.0, 6.0, 10.0, 25.0]


def test_the_best_variant_of_a_master_playlist():
    text = ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\nlow.m3u8\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=5000000\nhigh/index.m3u8\n")
    assert ct._variants(text, "http://h.example.com/live/master.m3u8") == [
        "http://h.example.com/live/high/index.m3u8", "http://h.example.com/live/low.m3u8"]


def test_the_copy_carries_no_login_address_or_mac():
    src = {"name": "My Provider", "kind": "xtream", "server": "http://iptv.example.com:8080",
           "username": "alice", "password": "s3cretpw", "mac": "00:1A:79:12:34:56",
           "options": {"backup_servers": "backup.example.net:8080"}}
    lines = ["Test - My Provider", "1. DNS lookup - ok - iptv.example.com: 203.0.113.9, 2001:db8::5",
             "   (GET http://iptv.example.com:8080/player_api.php?username=alice&password=s3cretpw)",
             "backup.example.net answered", "box 00:1A:79:12:34:56 token=abc123", "4. Login - ok - HTTP 200"]
    text = ct.copy_text(lines, src)
    for secret in ("alice", "s3cretpw", "iptv.example.com", "backup.example.net", "203.0.113.9",
                   "2001:db8::5", "00:1A:79:12:34:56", "abc123", "My Provider"):
        assert secret not in text, secret
    assert "HTTP 200" in text and "DNS lookup" in text


def test_account_lines():
    assert ct.account_lines({"exp_date": None, "max_connections": "1", "active_cons": "0"}) == [
        ("ct_acct_open", {}), ("conn_in_use", {"n": 0, "max": 1})]
    gone = ct.account_lines({"exp_date": str(int(time.time()) - 86400 * 3)})
    assert gone[0][0] == "ct_acct_ended"
    assert ct.account_lines({"active_cons": "2"})[-1] == ("conn_in_use_only", {"n": 2})
