"""Events pushed out (Nerd > Remote and sharing): the messages and what
private viewing leaves out of them; the MQTT packets as a broker reads them
(a fake one here, on this computer, reading CONNECT, PUBLISH, SUBSCRIBE and
PINGREQ itself); the broker's connection kept, lost and made again; webhooks
POSTed with their token header and tried again; the queue that never grows
past its limit; the recordings' statuses turned into events; the page's
Tests; and the window, whose playback path tells both receivers."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dopeiptv.core import events, mqtt
from dopeiptv.core.nerd_check import FAIL, OK, WARN

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)


def until(cond, secs=10.0):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# -- a broker, as small as it can be ---------------------------------------------------

def _length(data: bytes, i: int) -> tuple[int, int]:
    n, mult = 0, 1
    while True:
        b = data[i]
        i += 1
        n += (b & 0x7F) * mult
        if not b & 0x80:
            return n, i
        mult *= 128


def _str(data: bytes, i: int) -> tuple[bytes, int]:
    n = int.from_bytes(data[i:i + 2], "big")
    return data[i + 2:i + 2 + n], i + 2 + n


def parse_connect(body: bytes) -> dict:
    name, i = _str(body, 0)
    level, flags = body[i], body[i + 1]
    keepalive = int.from_bytes(body[i + 2:i + 4], "big")
    i += 4
    client, i = _str(body, i)
    out = {"protocol": name, "level": level, "flags": flags, "keepalive": keepalive,
           "client": client.decode(), "clean": bool(flags & 0x02), "will": None,
           "user": None, "password": None}
    if flags & 0x04:
        topic, i = _str(body, i)
        msg, i = _str(body, i)
        out["will"] = (topic.decode(), msg, bool(flags & 0x20), (flags >> 3) & 3)
    if flags & 0x80:
        user, i = _str(body, i)
        out["user"] = user.decode()
    if flags & 0x40:
        pw, i = _str(body, i)
        out["password"] = pw.decode()
    assert i == len(body), "bytes left over in CONNECT"
    return out


class FakeBroker:
    """Reads the packets the app sends, answers as a broker does; *connack*
    and *suback* are what it answers a login and a subscription with."""

    def __init__(self, connack: int = 0, suback: int = 0):
        self.connack, self.suback = connack, suback
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.port = self.srv.getsockname()[1]
        self.connects: list[dict] = []
        self.packets: list[tuple[int, int, bytes]] = []        # (type, flags, body) after CONNECT
        self.publishes: list[tuple[str, bytes, bool, int]] = []  # topic, payload, retain, connection
        self.pings = self.disconnects = 0
        self.conns: list[socket.socket] = []
        self._subs: dict[int, list[str]] = {}
        self._lock = threading.Lock()
        self._open = True
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while self._open:
            try:
                conn, _a = self.srv.accept()
            except OSError:
                return
            with self._lock:
                self.conns.append(conn)
                n = len(self.conns) - 1
            threading.Thread(target=self._serve, args=(conn, n), daemon=True).start()

    def _serve(self, conn: socket.socket, n: int):
        buf = b""
        try:
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                buf += chunk
                while len(buf) >= 2:
                    try:
                        length, i = _length(buf, 1)
                    except IndexError:
                        break
                    if len(buf) < i + length:
                        break
                    first, body, buf = buf[0], buf[i:i + length], buf[i + length:]
                    if not self._packet(conn, n, first >> 4, first & 0x0F, body):
                        conn.close()
                        return
        except OSError:
            return

    def _packet(self, conn, n, ptype, flags, body) -> bool:
        if ptype == 1:
            info = parse_connect(body)
            info["conn"] = n
            self.connects.append(info)
            conn.sendall(bytes([0x20, 2, 0, self.connack]))
            return self.connack == 0
        self.packets.append((ptype, flags, body))
        if ptype == 3:
            topic, i = _str(body, 0)
            assert (flags >> 1) & 3 == 0, "QoS 0 only"
            self.publishes.append((topic.decode(), body[i:], bool(flags & 1), n))
            for other, subs in list(self._subs.items()):
                if topic.decode() in subs:
                    self.conns[other].sendall(bytes([0x30]) + mqtt.encode_length(len(body)) + body)
        elif ptype == 8:
            assert flags == 2
            pid = body[:2]
            topic, i = _str(body, 2)
            if self.suback == 0:
                self._subs.setdefault(n, []).append(topic.decode())
            conn.sendall(bytes([0x90, 3]) + pid + bytes([self.suback]))
        elif ptype == 12:
            self.pings += 1
            conn.sendall(b"\xd0\x00")
        elif ptype == 14:
            self.disconnects += 1
            return False
        return True

    def topics(self, topic: str) -> list[dict]:
        return [json.loads(p) for t, p, _r, _n in self.publishes if t == topic and p[:1] == b"{"]

    def events(self, prefix: str = "dopeiptv") -> list[str]:
        return [m["event"] for m in self.topics(f"{prefix}/event")]

    def drop_all(self):
        """The broker restarts: every connection gone, no will sent here."""
        with self._lock:
            for c in self.conns:
                try:
                    c.shutdown(socket.SHUT_RDWR)
                    c.close()
                except OSError:
                    pass

    def close(self):
        self._open = False
        self.drop_all()
        self.srv.close()


@pytest.fixture
def broker():
    made = []

    def make(**kw):
        b = FakeBroker(**kw)
        made.append(b)
        return b
    yield make
    for b in made:
        b.close()


# -- a webhook receiver -------------------------------------------------------------

class Receiver:
    def __init__(self, codes=()):
        self.codes = list(codes)          # answered in turn, then 200
        self.got: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):            # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                code = outer.codes.pop(0) if outer.codes else 200
                outer.got.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                  "body": json.loads(body), "code": code})
                self.send_response(code)
                self.send_header("Content-Length", "0")
                self.end_headers()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/api/webhook/secret-hook-id"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def taken(self) -> list[dict]:
        return [g["body"] for g in self.got if 200 <= g["code"] < 300]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def receiver():
    made = []

    def make(codes=()):
        r = Receiver(codes)
        made.append(r)
        return r
    yield make
    for r in made:
        r.close()


class Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)


# -- the messages -----------------------------------------------------------------------

def test_an_event_says_when_what_and_nothing_empty():
    m = events.payload("playback_started", {"kind": "live", "title": "TV4 HD", "channel": "TV4 HD",
                                            "programme": "", "source": None}, now=1_800_000_000)
    assert m["event"] == "playback_started" and m["app"] == "dopeIPTV"
    assert m["kind"] == "live" and m["title"] == m["channel"] == "TV4 HD"
    assert "programme" not in m and "source" not in m and "private" not in m
    # ISO 8601, local time with its offset.
    from datetime import datetime
    assert datetime.fromisoformat(m["time"]).timestamp() == 1_800_000_000
    assert m["time"][-6] in "+-"
    # The fields cannot overwrite what every event says.
    assert events.payload("x", {"event": "y", "time": "z"})["event"] == "x"


def test_private_viewing_sends_what_happened_not_what():
    fields = {"kind": "live", "title": "TV4 HD", "channel": "TV4 HD", "programme": "Nyheterna",
              "series": "S", "source": "Main", "previous": "SVT1", "file": "a.ts", "error": "e",
              "reason": "stopped", "catchup": True}
    m = events.payload("channel_changed", fields, private=True)
    assert m["private"] is True and m["event"] == "channel_changed"
    assert not set(m) & events.WHAT
    assert m["kind"] == "live" and m["reason"] == "stopped" and m["catchup"] is True
    st = events.state_payload("playing", fields, private=True)
    assert st["state"] == "playing" and "title" not in st and st["private"] is True
    assert "event" not in st


def test_the_state_says_what_only_while_something_plays():
    what = {"kind": "movie", "title": "Film"}
    assert events.state_payload("playing", what)["title"] == "Film"
    assert events.state_payload("paused", what)["title"] == "Film"
    idle = events.state_payload("idle", what)
    assert idle["state"] == "idle" and "title" not in idle and "kind" not in idle


def test_nothing_is_configured_while_nerd_or_a_switch_is_off():
    s = Settings(nerd_enabled="false", nerd_hooks="true", nerd_hooks_urls="http://h/x",
                 nerd_mqtt="true", nerd_mqtt_host="broker")
    assert not events.config(s).on
    s["nerd_enabled"] = "true"
    cfg = events.config(s)
    assert cfg.hooks == ("http://h/x",) and cfg.mqtt_host == "broker" and cfg.mqtt_port == 1883
    assert cfg.prefix == "dopeiptv" and cfg.client_id.startswith("dopeiptv-") and len(cfg.client_id) <= 23
    s.update(nerd_hooks="false", nerd_mqtt="false")
    assert not events.config(s).on
    s.update(nerd_mqtt="true", nerd_mqtt_host="  ")
    assert not events.config(s).on


def test_addresses_header_and_prefix_are_read_with_care():
    good, bad = events.hook_urls("http://a/1\n\n# a comment\nftp://b/2\nhttps://c:8443/3\nnot an url\nhttp://a/1")
    assert good == ["http://a/1", "https://c:8443/3"] and bad == [4, 6]
    assert events.hook_header("") is None
    assert events.hook_header("X-Token: abc") == ("X-Token", "abc")
    assert events.hook_header("abc123") == ("Authorization", "Bearer abc123")
    assert events.hook_header("Authorization: Bearer xyz") == ("Authorization", "Bearer xyz")
    assert events.hook_header("a\nb") is None
    assert events.clean_prefix("/home/tv/") == "home/tv"
    assert events.clean_prefix("home/+") == events.clean_prefix("#") == events.clean_prefix("") == "dopeiptv"
    assert events.shown_url("http://u:p@ha.local:8123/api/webhook/secret?x=1") == "http://ha.local:8123/•••"
    assert events.shown_url("https://example.org") == "https://example.org/"


# -- the packets --------------------------------------------------------------------------

def test_remaining_lengths_are_encoded_as_the_spec_says():
    for n, raw in ((0, b"\x00"), (127, b"\x7f"), (128, b"\x80\x01"), (16383, b"\xff\x7f"),
                   (16384, b"\x80\x80\x01"), (2097151, b"\xff\xff\x7f"), (268435455, b"\xff\xff\xff\x7f")):
        assert mqtt.encode_length(n) == raw
        assert _length(raw + b"\x00", 0) == (n, len(raw))
    with pytest.raises(ValueError):
        mqtt.encode_length(268435456)


def test_connect_without_and_with_a_login(broker):
    b = broker()
    c = mqtt.Client("127.0.0.1", b.port, "dopeiptv-pc", keepalive=60, timeout=3,
                    will=("home/tv/availability", b"offline", True))
    c.connect()
    c.close()
    assert until(lambda: b.disconnects == 1)
    info = b.connects[0]
    assert info["protocol"] == b"MQTT" and info["level"] == 4 and info["clean"]
    assert info["keepalive"] == 60 and info["client"] == "dopeiptv-pc"
    assert info["user"] is None and info["password"] is None
    assert info["will"] == ("home/tv/availability", b"offline", True, 0)
    c = mqtt.Client("127.0.0.1", b.port, "x", "anna", "pw-anna", timeout=3)
    c.connect()
    c.close()
    assert until(lambda: len(b.connects) == 2)
    info = b.connects[1]
    assert (info["user"], info["password"], info["will"]) == ("anna", "pw-anna", None)
    assert info["flags"] & 0xC0 == 0xC0
    # A password with no user name: 3.1.1 wants the user flag too (an empty one).
    c = mqtt.Client("127.0.0.1", b.port, "x", "", "only-pw", timeout=3)
    c.connect()
    c.close()
    assert until(lambda: len(b.connects) == 3)
    assert (b.connects[2]["user"], b.connects[2]["password"]) == ("", "only-pw")


def test_publish_retained_and_not_and_a_ping(broker):
    b = broker()
    c = mqtt.Client("127.0.0.1", b.port, "x", timeout=3)
    c.connect()
    c.publish("home/tv/event", b'{"event":"x"}')
    c.publish("home/tv/state", "{\"state\":\"idle\"}", retain=True)
    c.publish("home/tv/äö", b"\x00\xff" * 200)        # UTF-8 topic, a payload past one length byte
    assert c.ping() >= 0
    assert b.pings == 1 and len(b.publishes) == 3     # in order: the PINGRESP came after all three
    assert b.publishes[0][:3] == ("home/tv/event", b'{"event":"x"}', False)
    assert b.publishes[1][:3] == ("home/tv/state", b'{"state":"idle"}', True)
    assert b.publishes[2][:2] == ("home/tv/äö", b"\x00\xff" * 200)
    with pytest.raises(ValueError):
        c.publish("home/+/event", b"")
    c.close()


def test_a_refused_login_and_a_server_that_is_no_broker(broker):
    b = broker(connack=5)
    c = mqtt.Client("127.0.0.1", b.port, "x", "anna", "bad", timeout=3)
    with pytest.raises(mqtt.MqttRefused) as e:
        c.connect()
    assert e.value.code == 5 and not c.connected
    r = Receiver()
    try:
        port = r.httpd.server_address[1]
        c = mqtt.Client("127.0.0.1", port, "x", timeout=3)
        with pytest.raises((mqtt.MqttError, OSError)):
            c.connect()
    finally:
        r.close()


# -- the broker, as the app keeps it ------------------------------------------------------

def _mqtt_cfg(port, **kw):
    base = {"mqtt_host": "127.0.0.1", "mqtt_port": port, "client_id": "dopeiptv-test", "prefix": "home/tv"}
    base.update(kw)
    return events.Config(**base)


def test_the_app_connects_with_a_will_and_publishes_events_and_the_state(broker):
    b = broker()
    d = events.Dispatcher()
    try:
        assert d.configure(_mqtt_cfg(b.port, mqtt_user="anna", mqtt_pass="pw", ha=True))
        assert until(lambda: b.topics("home/tv/state"))
        info = b.connects[0]
        assert info["will"] == ("home/tv/availability", b"offline", True, 0)
        assert (info["user"], info["password"], info["client"]) == ("anna", "pw", "dopeiptv-test")
        avail = [(p, r) for t, p, r, _n in b.publishes if t == "home/tv/availability"]
        assert avail == [(b"online", True)]
        assert b.topics("home/tv/state")[-1]["state"] == "idle"
        # Home Assistant: two sensors on the state topic, retained.
        ha = [(t, json.loads(p), r) for t, p, r, _n in b.publishes if t.startswith("homeassistant/")]
        assert [t for t, _c, _r in ha] == ["homeassistant/sensor/dopeiptv-test/playback/config",
                                           "homeassistant/sensor/dopeiptv-test/title/config"]
        assert all(r and c["state_topic"] == "home/tv/state" and c["availability_topic"] == "home/tv/availability"
                   for _t, c, r in ha)
        assert ha[0][1]["unique_id"] == "dopeiptv-test_playback" and ha[0][1]["device"]["name"] == "dopeIPTV"
        n = len(b.publishes)
        what = {"kind": "live", "title": "TV4 HD", "channel": "TV4 HD"}
        d.emit("playback_started", what, state=("playing", what))
        assert until(lambda: len(b.publishes) >= n + 2)
        ev = [p for p in b.publishes[n:] if p[0] == "home/tv/event"]
        st = [p for p in b.publishes[n:] if p[0] == "home/tv/state"]
        assert len(ev) == 1 and not ev[0][2] and json.loads(ev[0][1])["title"] == "TV4 HD"
        assert len(st) == 1 and st[0][2] and json.loads(st[0][1])["state"] == "playing"
        # Quitting: the event, the state "off", offline, and a clean DISCONNECT (no will).
        d.quit(2.0)
        d.join(5.0)
        assert until(lambda: b.disconnects == 1)           # the broker has read it all
        assert b.events("home/tv")[-1] == "app_quitting"
        assert b.topics("home/tv/state")[-1]["state"] == "off"
        assert [p for t, p, _r, _n in b.publishes if t == "home/tv/availability"][-1] == b"offline"
        assert not d.active
        assert d.emit("playback_started") is None          # nothing after quitting
    finally:
        d.quit(0)


def test_a_broker_that_restarted_costs_a_reconnect_not_the_event(broker, monkeypatch):
    monkeypatch.setattr(events, "VERIFY_IDLE_S", 0.0)    # every publish is checked first
    b = broker()
    d = events.Dispatcher()
    try:
        d.configure(_mqtt_cfg(b.port))
        assert until(lambda: b.topics("home/tv/state"))
        b.drop_all()
        time.sleep(0.1)
        d.emit("playback_paused", {"title": "Film"})
        assert until(lambda: "playback_paused" in b.events("home/tv")), b.publishes
        assert len(b.connects) == 2
        last = [p for p in b.publishes if p[0] == "home/tv/event"][-1]
        assert last[3] == b.connects[1]["conn"]          # on the new connection
        # The state went out again on it too (it is retained, the broker may have lost it).
        assert any(t == "home/tv/state" and n == b.connects[1]["conn"] for t, _p, _r, n in b.publishes)
    finally:
        d.quit(0)


def test_no_broker_is_retried_later_and_never_holds_up_the_caller(monkeypatch):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    d = events.Dispatcher()
    try:
        d.configure(_mqtt_cfg(port))
        t0 = time.monotonic()
        for i in range(200):
            d.emit("playback_started", {"title": str(i)})
        assert time.monotonic() - t0 < 0.5
        sink = d.sinks[0]
        assert until(lambda: sink._fails >= 1)
        assert sink._next_try > time.monotonic()           # backing off
        assert len(sink._q) <= events.QUEUE_MAX
    finally:
        d.quit(0)


def test_a_new_setting_lets_the_old_connection_go_first(broker):
    b = broker()
    d = events.Dispatcher()
    try:
        d.configure(_mqtt_cfg(b.port))
        assert until(lambda: len(b.connects) == 1 and b.topics("home/tv/state"))
        assert not d.configure(_mqtt_cfg(b.port))          # the same: nothing changes
        d.configure(_mqtt_cfg(b.port, ha=True))
        assert until(lambda: len(b.connects) == 2 and any(t.startswith("homeassistant/") for t, *_ in b.publishes))
        avail = [(p, n) for t, p, _r, n in b.publishes if t == "home/tv/availability"]
        # The old one said offline before the new one said online.
        assert avail[-1] == (b"online", b.connects[1]["conn"]) and (b"offline", b.connects[0]["conn"]) in avail
    finally:
        d.quit(0)


# -- webhooks ---------------------------------------------------------------------------------

def test_a_webhook_gets_json_and_its_token_header(receiver):
    r = receiver()
    d = events.Dispatcher()
    try:
        d.configure(events.Config(hooks=(r.url,), header=("Authorization", "Bearer tok-123")))
        d.emit("playback_started", {"kind": "movie", "title": "Film"})
        assert until(lambda: r.taken())
        got = r.got[0]
        assert got["path"] == "/api/webhook/secret-hook-id"
        assert got["headers"]["authorization"] == "Bearer tok-123"
        assert got["headers"]["content-type"].startswith("application/json")
        assert got["headers"]["user-agent"].startswith("dopeIPTV/")
        assert got["body"]["event"] == "playback_started" and got["body"]["title"] == "Film"
    finally:
        d.quit(0)


def test_a_webhook_is_tried_again_on_a_server_error_not_on_a_refusal(receiver, monkeypatch):
    monkeypatch.setattr(events, "RETRY_WAITS", (0.05, 0.05))
    r = receiver(codes=[503, 500])
    sink = events.WebhookSink(r.url, None)
    sink.start()
    try:
        sink.put("event", events.payload("playback_stopped"))
        assert until(lambda: sink.sent == 1)
        assert [g["code"] for g in r.got] == [503, 500, 200]
        r.codes = [400]
        sink.put("event", events.payload("playback_paused"))
        assert until(lambda: sink.failed == 1)
        time.sleep(0.2)
        assert [g["code"] for g in r.got][3:] == [400]           # not tried again
        r.codes = [500, 500, 500]
        sink.put("event", events.payload("playback_resumed"))
        assert until(lambda: sink.failed == 2)
        assert len(r.got) == 7                                     # three tries, then given up
    finally:
        sink.close(0)


def test_a_dead_webhook_holds_up_neither_the_others_nor_the_broker(receiver, broker, monkeypatch):
    monkeypatch.setattr(events, "RETRY_WAITS", (2.0, 2.0))
    good = receiver()
    bad = receiver(codes=[500] * 10)
    b = broker()
    d = events.Dispatcher()
    try:
        d.configure(events.Config(hooks=(bad.url, good.url), mqtt_host="127.0.0.1", mqtt_port=b.port,
                                  client_id="t", prefix="p"))
        d.emit("playback_started", {"title": "A"})
        assert until(lambda: good.taken() and "playback_started" in b.events("p"), 1.5)
    finally:
        d.quit(0)


def test_the_queue_never_grows_past_its_limit_the_oldest_go():
    sink = events.WebhookSink("http://127.0.0.1:9/", None, limit=50)      # never started
    for i in range(60):
        sink.put("event", {"n": i})
    assert len(sink._q) == 50 and sink.dropped == 10
    assert sink._q[0][1] == {"n": 10} and sink._q[-1][1] == {"n": 59}
    # Only the latest state is kept waiting.
    m = events.MqttSink(_mqtt_cfg(1))
    m.put("state", {"s": 1})
    m.put("event", {"e": 1})
    m.put("state", {"s": 2})
    assert list(m._q) == [("event", {"e": 1}), ("state", {"s": 2})]
    # Closed: nothing more is taken.
    sink.close(0)
    sink.put("event", {"n": 99})
    assert sink._q[-1][1] == {"n": 59}


def test_private_viewing_reaches_both_without_titles(receiver, broker):
    r = receiver()
    b = broker()
    d = events.Dispatcher()
    try:
        d.configure(events.Config(hooks=(r.url,), mqtt_host="127.0.0.1", mqtt_port=b.port, client_id="t",
                                  prefix="p"))
        what = {"kind": "live", "title": "TV4 HD", "channel": "TV4 HD", "programme": "News"}
        d.emit("playback_started", what, private=True, state=("playing", what))
        assert until(lambda: r.taken() and b.events("p") and b.topics("p/state")[-1]["state"] == "playing")
        for m in (r.taken()[0], b.topics("p/event")[0], b.topics("p/state")[-1]):
            assert m["private"] is True and m["kind"] == "live"
            assert "TV4" not in json.dumps(m) and "News" not in json.dumps(m)
    finally:
        d.quit(0)


# -- recordings -------------------------------------------------------------------------------

def test_recordings_start_finish_and_fail():
    jobs = [{"id": "old", "status": "recording", "title": "Was on"},
            {"id": "t", "status": "scheduled", "title": "Timer", "channel": "SVT1"}]
    w = events.RecordingWatch(jobs)
    assert w.update(jobs, now=0) == []                         # what was there already says nothing
    jobs.append({"id": "a", "status": "recording", "title": "News", "channel": "TV4 HD",
                 "path": "/home/me/Videos/News 2026.ts"})
    assert w.update(jobs, now=1) == [("recording_started", {"title": "News", "channel": "TV4 HD"})]
    jobs[2]["status"] = "done"
    w.stopped("News", "channel changed")
    assert w.update(jobs, now=2) == [] and w.pending          # not until its file was looked at
    assert w.update(jobs, now=2 + events.RecordingWatch.SETTLE_S) == [
        ("recording_finished", {"title": "News", "channel": "TV4 HD", "file": "News 2026.ts",
                                "reason": "channel changed"})]
    assert not w.pending
    # Stopped, then found empty: failed, not finished.
    jobs.append({"id": "b", "status": "recording", "title": "Film", "channel": ""})
    w.update(jobs, now=10)
    jobs[3]["status"] = "done"
    w.update(jobs, now=11)
    jobs[3].update(status="failed", error="no data captured")
    assert w.update(jobs, now=12) == [("recording_failed", {"title": "Film", "channel": "", "error": "no data captured"})]
    assert w.update(jobs, now=20) == []
    # A timer whose time passed; an error naming a file says nothing of it.
    jobs[1].update(status="failed", error="[Errno 13] Permission denied: '/home/me/x.ts'")
    assert w.update(jobs, now=21) == [("recording_failed", {"title": "Timer", "channel": "SVT1", "error": ""})]


# -- the checks --------------------------------------------------------------------------------

def test_the_webhook_check_says_whether_the_test_arrived(receiver):
    r = receiver()
    c = events.check_hooks([r.url], ("Authorization", "Bearer very-secret-token"))
    assert c.state == OK and c.key == "nerd_ck_hooks_ok" and "ms" in c.params
    assert r.taken()[0]["event"] == "test" and r.got[0]["headers"]["authorization"] == "Bearer very-secret-token"
    assert "very-secret-token" not in c.detail and "secret-hook-id" not in c.detail
    assert "playback_started" in c.detail                       # the payload, documented
    denied = receiver(codes=[401])
    c = events.check_hooks([denied.url], None)
    assert c.state == FAIL and c.key == "nerd_ck_hooks_denied"
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead = f"http://127.0.0.1:{s.getsockname()[1]}/x"
    s.close()
    c = events.check_hooks([r.url, dead], None, timeout=2)
    assert c.state == WARN and c.params == {"ok": 1, "n": 2}
    c = events.check_hooks([dead], None, timeout=2)
    assert c.state == FAIL and c.key == "nerd_ck_hooks_fail"
    assert events.check_hooks([], None).key == "nerd_ck_hooks_none"


def test_the_mqtt_check_reads_its_test_back(broker):
    b = broker()
    c = events.check_mqtt(_mqtt_cfg(b.port, mqtt_user="anna", mqtt_pass="pw-anna-secret"))
    assert c.state == OK and c.key == "nerd_ck_mqtt_ok", c.detail
    assert b.connects[0]["client"].endswith("-test") and b.connects[0]["client"] != "dopeiptv-test"
    assert b.events("home/tv") == ["test"]
    assert "pw-anna-secret" not in c.detail and "anna" not in c.detail
    assert "home/tv/state" in c.detail
    # A login that may not read it back: the broker took it, as its PINGRESP says.
    b2 = broker(suback=0x80)
    c = events.check_mqtt(_mqtt_cfg(b2.port))
    assert c.state == OK and c.key == "nerd_ck_mqtt_sent" and b2.events("home/tv") == ["test"]
    c = events.check_mqtt(_mqtt_cfg(broker(connack=4).port, mqtt_pass="pw-x"))
    assert c.state == FAIL and c.key == "nerd_ck_mqtt_login" and "pw-x" not in c.detail
    assert events.check_mqtt(_mqtt_cfg(broker(connack=2).port)).params == {"code": 2}


def test_the_mqtt_check_tells_no_broker_from_something_else(receiver):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    c = events.check_mqtt(_mqtt_cfg(port))
    assert c.state == FAIL and c.key == "nerd_ck_mqtt_down" and c.params == {"where": f"127.0.0.1:{port}"}
    r = receiver()
    c = events.check_mqtt(_mqtt_cfg(r.httpd.server_address[1]))
    assert c.state == FAIL and c.key in ("nerd_ck_mqtt_notmqtt", "nerd_ck_mqtt_down")
    assert events.check_mqtt(events.Config()).key == "nerd_ck_mqtt_nohost"


# -- the settings page ----------------------------------------------------------------------------

def test_the_page_tests_both_and_stores_them(tmp_path, receiver, broker):
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import QSettings
    from PyQt6.QtGui import QPixmap
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QDialog, QLineEdit, QPlainTextEdit

    from dopeiptv.core import nerd
    from dopeiptv.i18n import tr
    from dopeiptv.ui import nerd_settings
    from dopeiptv.ui.settings_ui import SettingsPage, SettingsShell, Stepper, ToggleSwitch, page_icon
    app = QApplication.instance() or QApplication([])
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.setValue("nerd_enabled", "true")
    nerd.install(s)
    r, b = receiver(), broker()
    shell = SettingsShell("Search", "none")
    blank = lambda k, sz, c: QPixmap(sz, sz)  # noqa: E731
    page = shell.add_page(SettingsPage("nerd", "Nerd", ""), page_icon("nerd", blank))
    serve = shell.add_page(SettingsPage("nerd_serve", "Remote", ""), page_icon("remote", blank))
    try:
        commit = nerd_settings.build(page, s, QDialog(), pages={"serve": serve})
        switches = {sw.accessibleName(): sw for sw in serve.findChildren(ToggleSwitch)}
        edits = {e.accessibleName(): e for e in serve.findChildren(QLineEdit) if e.accessibleName()}
        texts = {e.accessibleName(): e for e in serve.findChildren(QPlainTextEdit) if e.accessibleName()}
        hook_line = next(ln for ln in commit.lines if ln._title == tr("nerd_card_hooks"))
        mqtt_line = next(ln for ln in commit.lines if ln._title == tr("nerd_card_mqtt"))
        assert hook_line.result.key == "nerd_ck_events_off"
        switches[tr("nerd_hooks")].setChecked(True)
        texts[tr("nerd_hooks_urls")].setPlainText("nonsense\n" + r.url)
        QTest.qWait(400)
        assert hook_line.result.key == "nerd_ck_hooks_bad" and hook_line.result.params == {"n": 1}
        texts[tr("nerd_hooks_urls")].setPlainText(r.url)
        edits[tr("nerd_hooks_header")].setText("tok-abc-123")
        assert edits[tr("nerd_hooks_header")].echoMode() == QLineEdit.EchoMode.Password
        hook_line.test_btn.click()
        assert until_qt(QTest, lambda: hook_line.result is not None and hook_line.result.state == OK)
        assert r.got[-1]["headers"]["authorization"] == "Bearer tok-abc-123"
        assert "tok-abc-123" not in hook_line.text.text() + hook_line.result.detail

        switches[tr("nerd_mqtt")].setChecked(True)
        edits[tr("nerd_mqtt_host")].setText("127.0.0.1")
        port = [st for st in serve.findChildren(Stepper) if st.spin.value() == 1883][0]
        switches[tr("nerd_mqtt_tls")].setChecked(True)
        assert port.spin.value() == 8883                           # TLS: its own port
        switches[tr("nerd_mqtt_tls")].setChecked(False)
        assert port.spin.value() == 1883
        port.spin.setValue(b.port)
        edits[tr("nerd_mqtt_prefix")].setText("home/tv")
        mqtt_line.test_btn.click()
        assert until_qt(QTest, lambda: mqtt_line.result is not None and mqtt_line.result.key == "nerd_ck_mqtt_ok")
        assert b.events("home/tv") == ["test"]

        stored = {}
        commit(lambda k, v: stored.__setitem__(k, v))
        assert stored["nerd_hooks"] == "true" and stored["nerd_hooks_urls"] == r.url
        assert stored["nerd_hooks_header"] == "tok-abc-123" and stored["nerd_mqtt"] == "true"
        assert stored["nerd_mqtt_port"] == str(b.port) and stored["nerd_mqtt_prefix"] == "home/tv"
        assert "nerd_mqtt_ha" not in stored                    # untouched: not written
        for k, v in stored.items():
            s.setValue(k, v)
        cfg = events.config(s)
        assert cfg.hooks == (r.url,) and cfg.header == ("Authorization", "Bearer tok-abc-123")
        assert (cfg.mqtt_host, cfg.mqtt_port, cfg.prefix) == ("127.0.0.1", b.port, "home/tv")
        diag = nerd_settings.diagnostics(s)
        assert "tok-abc-123" not in diag and "nerd_hooks_header = (set)" in diag
    finally:
        nerd.install(None)
        app.processEvents()


def until_qt(qtest, cond, secs=10.0):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        qtest.qWait(20)
        if cond():
            return True
    return cond()


# -- the window --------------------------------------------------------------------------------

_CHILD = r"""
import faulthandler, json, os, sys, tempfile, time
faulthandler.dump_traceback_later(150, exit=True)       # a dialog nobody answers is a failure, not a hang


def _failed(*exc):
    import traceback
    traceback.print_exception(*exc)
    sys.stderr.flush()
    os._exit(1)                                          # not through Qt's teardown
sys.excepthook = _failed
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from dopeiptv.core import nerd, privacy
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw
from tests.test_events import FakeBroker, Receiver


class Client(DemoClient):
    def live_url(self, stream_id, fmt="ts"):
        return f"http://127.0.0.1:9/{stream_id}.ts"


def external(*_a, **_k):
    raise AssertionError("an external player was started")


def until(done, secs=10.0):
    end = time.monotonic() + secs
    while time.monotonic() < end and not done():
        app.processEvents()
        time.sleep(0.01)
    return done()


mw.launch_player = external
hook, broker = Receiver(), FakeBroker()
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
s.setValue("playlists", json.dumps([{"id": "a", "name": "Main", "kind": "m3u",
                                     "server": "http://127.0.0.1:9/a.m3u"}]))
s.setValue("active_playlist", "a")
for k, v in {"nerd_enabled": "true", "nerd_hooks": "true", "nerd_hooks_urls": hook.url,
             "nerd_hooks_header": "X-Token: hook-secret", "nerd_mqtt": "true",
             "nerd_mqtt_host": "127.0.0.1", "nerd_mqtt_port": str(broker.port),
             "nerd_mqtt_prefix": "home/tv"}.items():
    s.setValue(k, v)
nerd.install(s)
w = mw.MainWindow(Client(), s, PlaylistStore(s))
if w.player is None:
    print("EVENTS_SKIP: no embedded player here")
    os._exit(0)
played = []
w.player.play = lambda url, *a, **k: played.append(url) or True
w.wake.acquire = w.wake.release = lambda *a, **k: None

def both():
    return [m["event"] for m in hook.taken()], broker.events("home/tv")

def seen(*names):
    return until(lambda: all(n in both()[0] and n in both()[1] for n in names))

w._events_apply(started=True)
assert seen("app_started"), both()
assert broker.topics("home/tv/state")[-1]["state"] == "idle"

# A channel: started on both, and the state says what plays.
tv4 = {"name": "TV4 HD", "stream_id": 7}
w._start_playback("http://127.0.0.1:9/7.ts", "TV4 HD", None, 7, "live", item=tv4)
assert played == ["http://127.0.0.1:9/7.ts"]
assert seen("playback_started"), both()
started = [m for m in hook.taken() if m["event"] == "playback_started"][-1]
assert (started["kind"], started["title"], started["channel"], started["source"]) == ("live", "TV4 HD", "TV4 HD", "Main"), started
assert "127.0.0.1:9" not in json.dumps(hook.taken()) + json.dumps(broker.topics("home/tv/event"))
assert hook.got[0]["headers"]["x-token"] == "hook-secret"
assert until(lambda: broker.topics("home/tv/state")[-1].get("title") == "TV4 HD")
assert broker.topics("home/tv/state")[-1]["state"] == "playing"
# The app reconnecting the same channel is no new start.
w._start_playback("http://127.0.0.1:9/7.ts", "TV4 HD", None, 7, "live", item=tv4)

# Another channel: started, then channel_changed with the one before.
svt = {"name": "SVT1", "stream_id": 8}
w._start_playback("http://127.0.0.1:9/8.ts", "SVT1", None, 8, "live", item=svt)
assert seen("channel_changed"), both()
changed = [m for m in hook.taken() if m["event"] == "channel_changed"][-1]
assert (changed["channel"], changed["previous"]) == ("SVT1", "TV4 HD"), changed
assert [m["event"] for m in hook.taken()].count("playback_started") == 2, hook.taken()

# Paused, resumed, stopped - the player's own signals.
w.player.paused_changed.emit(True)
assert seen("playback_paused"), both()
assert until(lambda: broker.topics("home/tv/state")[-1]["state"] == "paused")
w.player.paused_changed.emit(False)
assert seen("playback_resumed"), both()
w.player.stopped.emit()
assert seen("playback_stopped"), both()
stopped = [m for m in hook.taken() if m["event"] == "playback_stopped"][-1]
assert stopped["reason"] == "stopped" and stopped["channel"] == "SVT1", stopped
assert until(lambda: broker.topics("home/tv/state")[-1]["state"] == "idle")
assert "title" not in broker.topics("home/tv/state")[-1]

# A recording that starts in the player.
path = os.path.join(tempfile.mkdtemp(), "SVT1 news.ts")
job = w.rec.add_inplayer_job("SVT1 news", path, None, channel="SVT1")
assert seen("recording_started"), both()
rec = [m for m in hook.taken() if m["event"] == "recording_started"][-1]
assert (rec["title"], rec["channel"]) == ("SVT1 news", "SVT1"), rec
with open(path, "wb") as fh:
    fh.write(b"\x47" * 188)
w.rec.finish_inplayer(job["id"], "stopped")
# Told once the app has looked at its file.
assert seen("recording_finished"), both()
fin = [m for m in hook.taken() if m["event"] == "recording_finished"][-1]
assert (fin["file"], fin["reason"]) == ("SVT1 news.ts", "stopped") and path not in json.dumps(fin), fin

# Private viewing: what happened, not what.
privacy.set_private(True)
w._start_playback("http://127.0.0.1:9/9.ts", "Kanal 9", None, 9, "live", item={"name": "Kanal 9", "stream_id": 9})
assert until(lambda: [m for m in hook.taken() if m.get("private")]), hook.taken()
priv = [m for m in hook.taken() if m.get("private")][-1]
assert priv["event"] == "playback_started" and "Kanal 9" not in json.dumps(priv), priv
assert until(lambda: broker.topics("home/tv/state")[-1].get("private"))
assert "Kanal 9" not in json.dumps(broker.topics("home/tv/state")[-1])
privacy.set_private(False)

# Off: nothing more, whatever plays.
s.setValue("nerd_enabled", "false"); nerd.install(s); w._events_apply()
n = len(hook.got)
w._start_playback("http://127.0.0.1:9/10.ts", "Kanal 10", None, 10, "live", item={"name": "Kanal 10", "stream_id": 10})
time.sleep(0.3); app.processEvents()
assert len(hook.got) == n

# On again, and quitting: app_quitting, the state off, offline.
s.setValue("nerd_enabled", "true"); nerd.install(s); w._events_apply()
t0 = time.monotonic()
w._events_quit()
assert time.monotonic() - t0 < 0.2              # never waited for on the window's thread
w._events_join(3.0)
assert seen("app_quitting"), both()
# The broker's thread takes them in its own time: wait, as above.
assert until(lambda: broker.topics("home/tv/state")[-1]["state"] == "off"), broker.topics("home/tv/state")
assert until(lambda: [p for t, p, _r, _n in broker.publishes if t == "home/tv/availability"][-1] == b"offline")
print("EVENTS_OK", flush=True)
os._exit(0)
"""


def _child_env(tmp_path) -> dict:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        env[var] = str(tmp_path / var.lower())
    return env


def test_the_window_tells_both_what_plays(tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=_child_env(tmp_path),
                         capture_output=True, text=True, timeout=180)
    if "EVENTS_SKIP" in out.stdout:
        pytest.skip(out.stdout.strip())
    assert "EVENTS_OK" in out.stdout, out.stdout + out.stderr[-4000:]


# -- HTTPS checked with certifi too; the broker never named in the log -------------------------

def test_https_webhooks_check_certificates_with_certifi_as_well(monkeypatch):
    """The Mac app (and other packaged builds) may not find the system's
    certificates: a webhook's HTTPS is checked against certifi's as well,
    as the broker's TLS is (mqtt.tls_context). No network here."""
    import ssl
    import urllib.request
    certifi = pytest.importorskip("certifi")
    op = events.opener_for("https://hooks.example.com/api/webhook/x")
    https = [h for h in op.handlers if isinstance(h, urllib.request.HTTPSHandler)]
    assert len(https) == 1 and https[0]._context is events.https_context()
    ctx = events.https_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    with open(certifi.where(), encoding="ascii", errors="replace") as fh:
        pem = fh.read()
    end = "-----END CERTIFICATE-----"
    first = pem[pem.index("-----BEGIN CERTIFICATE-----"):pem.index(end) + len(end)]
    assert ssl.PEM_cert_to_DER_cert(first) in ctx.get_ca_certs(binary_form=True)
    # A home system is still asked directly, whatever proxy the desktop names.
    monkeypatch.setenv("http_proxy", "http://proxy.example:3128")
    monkeypatch.setenv("https_proxy", "http://proxy.example:3128")
    for url, direct in (("http://homeassistant.local:8123/api/webhook/x", True),
                        ("http://nodered:1880/x", True),
                        ("https://hooks.example.com/x", False)):
        handlers = events.opener_for(url).handlers
        proxied = [h for h in handlers if isinstance(h, urllib.request.ProxyHandler) and h.proxies]
        assert bool(proxied) != direct, url


def test_the_broker_is_never_named_in_the_log():
    import logging
    cfg = events.Config(mqtt_host="broker.example.org", mqtt_port=8883, mqtt_tls=True,
                        client_id="t", prefix="home/tv")
    shown = events.broker_shown(cfg)
    assert "broker.example.org" not in shown and "8883" in shown and "TLS" in shown
    sink = events.MqttSink.__new__(events.MqttSink)
    sink.cfg = cfg
    assert sink.quiet("certificate is not valid for 'broker.example.org'") == \
        "certificate is not valid for '(broker)'"
    # A broker that is not there: the lines say so, never where it is.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    seen: list[str] = []

    class Catch(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())
    h = Catch()
    level = events.log.level
    events.log.setLevel(logging.INFO)
    events.log.addHandler(h)
    d = events.Dispatcher()
    try:
        d.configure(_mqtt_cfg(port))
        assert until(lambda: any("no MQTT broker" in m for m in seen)), seen
    finally:
        d.quit(0)
        events.log.removeHandler(h)
        events.log.setLevel(level)
    lines = [m for m in seen if "MQTT" in m]
    assert lines and not [m for m in lines if "127.0.0.1" in m], lines
