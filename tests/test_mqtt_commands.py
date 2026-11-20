"""Home Assistant's commands over MQTT (core/mqtt_control.py, the broker's
thread in core/events.py, the window in ui/mw_events.py): what a message on
a command topic asks and what is ignored (a retained one, an unknown one, a
value it cannot take - logged at debug, never what it said); the controls'
discovery and taking them away again; a command heard at once while the
connection is idle; the volume and the favourite in the state; and the
window doing what is asked, on its own thread, with private viewing still
saying nothing of what plays."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time

import pytest

from dopeiptv.core import events, mqtt, mqtt_control
from tests.test_events import FakeBroker, until

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = "home/tv"


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)


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


# -- what a message asks ---------------------------------------------------------------------

def test_a_command_is_read_from_its_topic():
    parse = mqtt_control.parse
    for name in ("playpause", "stop", "channel_up", "channel_down"):
        assert parse(P, f"{P}/command/{name}", b"PRESS") == (name, None)
        assert parse(P, f"{P}/command/{name}", b"") == (name, None)
    assert parse(P, f"{P}/command/volume", b"55") == ("volume", 55)
    assert parse(P, f"{P}/command/volume", b" 33.6 ") == ("volume", 34)
    assert parse(P, f"{P}/command/volume", b"250") == ("volume", 100)
    assert parse(P, f"{P}/command/volume", b"-4") == ("volume", 0)
    assert parse(P, f"{P}/command/play", "SVT1 HD".encode()) == ("play", "SVT1 HD")
    assert parse(P, f"{P}/command/play", b"12") == ("play", "12")
    assert parse(P, f"{P}/command/favorite", "Kanal 5 – Öst".encode()) == ("favorite", "Kanal 5 – Öst")


def test_what_is_no_command_is_ignored_and_never_logged(caplog):
    parse = mqtt_control.parse
    caplog.set_level(logging.DEBUG, logger=events.log.name)
    bad = [(f"{P}/command/playpause", b"PRESS", True),          # retained: would run at every connect
           (f"{P}/command/reboot", b"now", False),
           (f"{P}/commandx/stop", b"", False),
           ("other/command/stop", b"", False),
           (f"{P}/command/volume", b"loud", False),
           (f"{P}/command/volume", b"nan", False),
           (f"{P}/command/volume", b"", False),
           (f"{P}/command/play", b"", False),
           (f"{P}/command/play", b"x" * (mqtt_control.MAX_TEXT + 1), False),
           (f"{P}/command/play", b"\xff\xfe secret channel", False),
           (f"{P}/command/favorite", b"   ", False)]
    for topic, payload, retained in bad:
        assert parse(P, topic, payload, retained) is None, topic
    text = caplog.text
    assert "loud" not in text and "secret" not in text and "reboot" not in text
    assert any(r.levelno == logging.DEBUG for r in caplog.records)
    assert not any(r.levelno > logging.DEBUG for r in caplog.records)


def test_the_list_names_each_favourite_once():
    rows = [{"name": "TV4 HD", "stream_id": 1}, {"name": "TV4  HD ", "stream_id": 2}, {"name": "", "stream_id": 3},
            {"name": "None", "stream_id": 4}, {"name": "SVT1", "stream_id": 5}, {"name": "TV4 HD", "stream_id": 6}]
    opts = mqtt_control.favorite_options(rows)
    assert [(o, r["stream_id"]) for o, r in opts] == [("TV4 HD", 1), ("TV4 HD (2)", 2), ("SVT1", 5),
                                                      ("TV4 HD (3)", 6)]
    many = mqtt_control.favorite_options({"name": f"Ch {i}", "stream_id": i} for i in range(500))
    assert len(many) == mqtt_control.MAX_OPTIONS


def test_the_controls_home_assistant_finds():
    device = {"identifiers": ["n1"], "name": "dopeIPTV"}
    found = dict(mqtt_control.discovery("n1", P, device, ["TV4 HD", "SVT1"]))
    assert sorted(found) == sorted(mqtt_control.config_topics("n1"))
    buttons = [c for t, c in found.items() if "/button/" in t]
    assert [b["command_topic"] for b in buttons] == [f"{P}/command/{n}" for n in
                                                     ("playpause", "stop", "channel_up", "channel_down")]
    assert all(b["payload_press"] == "PRESS" for b in buttons)
    vol = found["homeassistant/number/n1/volume/config"]
    assert (vol["min"], vol["max"], vol["state_topic"]) == (0, 100, f"{P}/state")
    assert "value_json.volume" in vol["value_template"]
    sel = found["homeassistant/select/n1/favorite/config"]
    assert sel["options"] == ["TV4 HD", "SVT1"] and sel["command_topic"] == f"{P}/command/favorite"
    assert "'None'" in sel["value_template"]
    txt = found["homeassistant/text/n1/play/config"]
    assert txt["command_topic"] == f"{P}/command/play" and txt["max"] == mqtt_control.MAX_TEXT
    for c in found.values():
        assert c["availability_topic"] == f"{P}/availability" and c["device"] is device
        assert c["unique_id"].startswith("n1_")
    sw = found["homeassistant/switch/n1/tv_mode/config"]
    assert sw["command_topic"] == f"{P}/command/tv" and sw["state_topic"] == f"{P}/state"
    assert (sw["payload_on"], sw["payload_off"]) == ("ON", "OFF") and "value_json.tv_mode" in sw["value_template"]
    assert len({c["unique_id"] for c in found.values()}) == 8
    # No favourites: no list; commands off: every control taken away.
    assert dict(mqtt_control.discovery("n1", P, device, []))["homeassistant/select/n1/favorite/config"] is None
    assert all(c is None for _t, c in mqtt_control.discovery("n1", P, device, None))


# -- the broker's thread -----------------------------------------------------------------------

def _cfg(port, **kw):
    base = {"mqtt_host": "127.0.0.1", "mqtt_port": port, "client_id": "dopeiptv-test", "prefix": P,
            "ha": True, "commands": True}
    base.update(kw)
    return events.Config(**base)


def _sender(port):
    c = mqtt.Client("127.0.0.1", port, "ha-sender", timeout=5)
    c.connect()
    return c


def test_commands_are_heard_at_once_and_handed_on(broker):
    b = broker()
    heard: list[tuple[str, object]] = []
    d = events.Dispatcher()
    d.on_command = lambda name, value: heard.append((name, value))
    d.set_favorites(["TV4 HD"])
    send = None
    try:
        d.configure(_cfg(b.port))
        assert until(lambda: any(p[0] == 8 for p in b.packets))           # SUBSCRIBE
        sub = next(p for p in b.packets if p[0] == 8)[2]
        assert f"{P}/command/+".encode() in sub
        cfgs = {t: p for t, p, r, _n in b.publishes if t.startswith("homeassistant/")}
        assert "homeassistant/button/dopeiptv-test/playpause/config" in cfgs
        assert json.loads(cfgs["homeassistant/select/dopeiptv-test/favorite/config"])["options"] == ["TV4 HD"]
        time.sleep(0.3)                                                    # idle now
        send = _sender(b.port)
        t0 = time.monotonic()
        send.publish(f"{P}/command/playpause", b"PRESS")
        assert until(lambda: heard == [("playpause", None)])
        assert time.monotonic() - t0 < 1.5                                 # not a keepalive later
        send.publish(f"{P}/command/volume", b"55")
        send.publish(f"{P}/command/reboot", b"now")
        send.publish(f"{P}/command/play", "Kanal 5".encode())
        assert until(lambda: len(heard) == 3)
        assert heard[1:] == [("volume", 55), ("play", "Kanal 5")]
        # A retained one, as a broker hands it to a new subscriber: not a command.
        conn = b.connects[0]["conn"]
        body = mqtt._utf8(f"{P}/command/stop") + b"PRESS"
        b.conns[conn].sendall(bytes([0x31]) + mqtt.encode_length(len(body)) + body)
        send.publish(f"{P}/command/stop", b"PRESS")
        assert until(lambda: len(heard) == 4) and heard[-1] == ("stop", None)
        time.sleep(0.2)
        assert len(heard) == 4
        # Events still go out on the same connection meanwhile.
        d.emit("playback_started", {"title": "TV4 HD"})
        assert until(lambda: "playback_started" in b.events(P))
        assert d.sinks[0].commands_heard == 4
        # A new list of favourites: the list again.
        d.set_favorites(["TV4 HD", "SVT1"])
        assert until(lambda: [json.loads(p)["options"] for t, p, _r, _n in b.publishes
                              if t.endswith("/select/dopeiptv-test/favorite/config") and p][-1:]
                     == [["TV4 HD", "SVT1"]])
    finally:
        if send is not None:
            send.close()
        d.quit(0)


def test_a_broker_that_never_stops_sending_holds_nothing():
    """take() read for as long as anything was waiting: a broker sending
    without a pause kept the events' thread there for good, and every
    command it sent went to the window unpaced. One take() reads at most
    TAKE_MAX packets now, and the commands handed on are paced (a token
    bucket), the rest dropped."""
    import socket
    import threading

    here, broker_end = socket.socketpair()
    cl = mqtt.Client("127.0.0.1", 1883, timeout=5)
    cl._sock = here
    packet = mqtt.publish_packet(f"{P}/command/volume", b"40")
    stop = threading.Event()

    def flood():
        while not stop.is_set():
            try:
                broker_end.sendall(packet * 64)
            except OSError:
                return
    th = threading.Thread(target=flood, daemon=True)
    th.start()
    try:
        t0 = time.monotonic()
        got = cl.take(wait=1.0)
        assert time.monotonic() - t0 < 2.0
        assert 0 < len(got) <= 64 and got[0] == (f"{P}/command/volume", b"40", False)

        heard: list[tuple[str, object]] = []
        sink = events.MqttSink(_cfg(1), on_command=lambda name, value: heard.append((name, value)))
        sink.client = cl
        try:
            t0 = time.monotonic()
            while time.monotonic() - t0 < 1.0:
                sink._hear()
            spent = time.monotonic() - t0
            assert heard and heard[0] == ("volume", 40)
            most = sink.COMMANDS_BURST + sink.COMMANDS_PER_S * spent + 1
            assert len(heard) <= most, (len(heard), most)
            assert sink._dropped > 0
        finally:
            sink.client = None
            sink.close(0)
    finally:
        stop.set()
        broker_end.close()
        here.close()
        th.join(5)


def test_the_state_has_the_volume_and_the_favourite_unless_private(broker):
    b = broker()
    d = events.Dispatcher()
    try:
        d.configure(_cfg(b.port))
        assert until(lambda: b.topics(f"{P}/state"))
        d.set_volume(40)
        assert until(lambda: b.topics(f"{P}/state")[-1].get("volume") == 40)
        d.set_state("playing", {"title": "TV4 HD", "channel": "TV4 HD", "favorite": "TV4 HD"})
        assert until(lambda: b.topics(f"{P}/state")[-1].get("favorite") == "TV4 HD")
        assert b.topics(f"{P}/state")[-1]["volume"] == 40
        d.set_state("playing", {"title": "TV4 HD", "channel": "TV4 HD", "favorite": "TV4 HD"}, private=True)
        assert until(lambda: b.topics(f"{P}/state")[-1].get("private"))
        st = b.topics(f"{P}/state")[-1]
        assert "favorite" not in st and "TV4" not in json.dumps(st) and st["volume"] == 40
        d.set_volume(40)                       # the same: nothing new
        n = len(b.topics(f"{P}/state"))
        time.sleep(0.2)
        assert len(b.topics(f"{P}/state")) == n
    finally:
        d.quit(0)


def test_commands_turned_off_take_the_controls_away_and_listen_no_more(broker):
    b = broker()
    heard = []
    d = events.Dispatcher()
    d.on_command = lambda name, value: heard.append(name)
    send = None
    try:
        d.configure(_cfg(b.port))
        assert until(lambda: any(p[0] == 8 for p in b.packets))
        d.configure(_cfg(b.port, commands=False))
        assert until(lambda: len(b.connects) == 2)
        removed = lambda: {t for t, p, r, n in b.publishes  # noqa: E731
                           if n == b.connects[1]["conn"] and t.startswith("homeassistant/") and p == b"" and r}
        assert until(lambda: removed() == set(mqtt_control.config_topics("dopeiptv-test")))
        assert sum(1 for p in b.packets if p[0] == 8) == 1               # no new subscription
        send = _sender(b.port)
        send.publish(f"{P}/command/stop", b"PRESS")
        time.sleep(0.5)
        assert heard == []
        # The connection after that has nothing left to take away.
        d.configure(_cfg(b.port, commands=False, client_id="dopeiptv-test2"))
        assert until(lambda: len(b.connects) == 3 and b.topics(f"{P}/state"))
        time.sleep(0.3)
        assert not [t for t, p, r, n in b.publishes if n == b.connects[2]["conn"] and p == b""]
    finally:
        if send is not None:
            send.close()
        d.quit(0)


def test_without_commands_the_app_subscribes_to_nothing(broker):
    b = broker()
    d = events.Dispatcher()
    try:
        d.configure(_cfg(b.port, commands=False))
        assert until(lambda: b.topics(f"{P}/state"))
        time.sleep(0.3)
        assert not [p for p in b.packets if p[0] == 8]
        assert not [t for t, *_ in b.publishes if "/button/" in t or "/number/" in t]
    finally:
        d.quit(0)


def test_a_broker_gone_while_listening_costs_a_reconnect(broker, monkeypatch):
    monkeypatch.setattr(events, "BACKOFF", (0.0, 0.2, 0.2, 0.2, 0.2, 0.2))
    b = broker()
    heard = []
    d = events.Dispatcher()
    d.on_command = lambda name, value: heard.append(name)
    send = None
    try:
        d.configure(_cfg(b.port))
        assert until(lambda: any(p[0] == 8 for p in b.packets))
        b.drop_all()
        assert until(lambda: len(b.connects) >= 2 and sum(1 for p in b.packets if p[0] == 8) >= 2)
        send = _sender(b.port)
        send.publish(f"{P}/command/channel_up", b"PRESS")
        assert until(lambda: heard == ["channel_up"])
    finally:
        if send is not None:
            send.close()
        d.quit(0)


# -- the window ---------------------------------------------------------------------------------

_CHILD = r"""
import faulthandler, json, os, sys, tempfile, time
faulthandler.dump_traceback_later(150, exit=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from dopeiptv.core import mqtt, nerd, privacy
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import make_client
from dopeiptv.ui import main_window as mw
from tests.test_events import FakeBroker
from tests.test_sources_side_by_side import Xtream

def _failed(*exc):
    import traceback
    traceback.print_exception(*exc)
    sys.stderr.flush()
    os._exit(1)
sys.excepthook = _failed

def until(done, secs=10.0):
    end = time.monotonic() + secs
    while time.monotonic() < end and not done():
        app.processEvents(); time.sleep(0.01)
    return done()

xt, broker = Xtream(), FakeBroker()
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
pls = [{"id": "a", "name": "Main", "kind": "xtream", "server": xt.base, "username": "anna", "password": "pw-anna"}]
s.setValue("playlists", json.dumps(pls))
s.setValue("active_playlist", "a")
for k, v in {"nerd_enabled": "true", "nerd_mqtt": "true", "nerd_mqtt_host": "127.0.0.1",
             "nerd_mqtt_port": str(broker.port), "nerd_mqtt_prefix": "home/tv", "nerd_mqtt_ha": "true",
             "nerd_mqtt_commands": "true", "nerd_mqtt_client": "tvpc"}.items():
    s.setValue(k, v)
nerd.install(s)
w = mw.MainWindow(make_client(pls[0]), s, PlaylistStore(s))
played, media, commands = [], [], []
def start_playback(url, title, icon, key, kind, record=True, item=None, catchup=False):
    played.append(title)
    w._last_playback = {"url": url, "title": title, "kind": kind, "item": item or {}}
    w._events_started(key, title, item, kind, catchup)
w._start_playback = start_playback
w.mpris_command = lambda name, arg=None: media.append((name, arg))
w._come_forward = lambda: None
w._media_active = lambda: bool(played)
w.favs.add("all", {"name": "Kanal 5", "stream_id": 405, "category_id": "1"})
w._events_apply(started=True)
assert until(lambda: any(p[0] == 8 for p in broker.packets)), broker.packets

def select_options():
    found = [json.loads(p) for t, p, _r, _n in broker.publishes if t == "homeassistant/select/tvpc/favorite/config" and p]
    return found[-1]["options"] if found else None
assert until(lambda: select_options() == ["Kanal 5"]), select_options()

send = mqtt.Client("127.0.0.1", broker.port, "ha", timeout=5)
send.connect()
def cmd(name, payload=b"PRESS"):
    send.publish("home/tv/command/" + name, payload)

cmd("playpause")
assert until(lambda: ("playpause", None) in media), media
cmd("volume", b"55")
assert until(lambda: ("volume", 0.55) in media), media
cmd("play", "kanal 5".encode())
assert until(lambda: played[-1:] == ["Kanal 5"]), played
cmd("play", b"1")
assert until(lambda: played[-1:] == ["TV4 HD"]), played
cmd("favorite", "Kanal 5".encode())
assert until(lambda: played[-1:] == ["Kanal 5"]), played
assert until(lambda: broker.topics("home/tv/state")[-1].get("favorite") == "Kanal 5"), broker.topics("home/tv/state")[-1]
cmd("favorite", b"Not one")
cmd("channel_down")
assert until(lambda: played[-1:] == ["TV4 HD"]), played
cmd("stop")
assert until(lambda: ("stop", None) in media), media
# The volume, as the player's slider says it.
if w.player is not None:
    w.player.vol.setValue(23)
    assert until(lambda: broker.topics("home/tv/state")[-1].get("volume") == 23), broker.topics("home/tv/state")[-1]

# A new favourite: Home Assistant's list follows.
w.favs.add("all", {"name": "TV4 HD", "stream_id": 401, "category_id": "1"})
assert until(lambda: select_options() == ["Kanal 5", "TV4 HD"]), select_options()

# TV mode: Home Assistant's switch, and its keys - the window's one way in.
heard = []
real_tv = w.tv_command
w.tv_command = lambda what, value=None: (heard.append((what, value)), real_tv(what, value))[1]
w._tv_input = lambda a: heard.append(("key", a))
def switch():
    found = [json.loads(p) for t, p, _r, _n in broker.publishes if t == "homeassistant/switch/tvpc/tv_mode/config" and p]
    return found[-1] if found else None
assert switch() and switch()["command_topic"] == "home/tv/command/tv"
cmd("tv", b"ON")
assert until(lambda: w._tv_on and broker.topics("home/tv/state")[-1].get("tv_mode") is True), broker.topics("home/tv/state")[-1]
cmd("tv_key", b"down")
assert until(lambda: ("key", "down") in heard), heard
cmd("tv", b"OFF")
assert until(lambda: not w._tv_on and broker.topics("home/tv/state")[-1].get("tv_mode") is False)
assert heard == [("tv", "on"), ("tv_key", "down"), ("key", "down"), ("tv", "off")], heard
del w.tv_command, w._tv_input

# Private viewing: commands still work, the state still says nothing of what.
privacy.set_private(True)
cmd("play", "Kanal 5".encode())
assert until(lambda: played[-1:] == ["Kanal 5"] and broker.topics("home/tv/state")[-1].get("private")), played
st = broker.topics("home/tv/state")[-1]
assert "Kanal" not in json.dumps(st) and "favorite" not in st, st
privacy.set_private(False)

# Commands off: nothing more is done.
s.setValue("nerd_mqtt_commands", "false"); nerd.install(s); w._events_apply()
assert until(lambda: len(broker.connects) >= 2)
n = len(played) + len(media)
cmd("play", "TV4 HD".encode()); cmd("stop")
time.sleep(0.5); app.processEvents()
assert len(played) + len(media) == n
send.close()
w._events_quit(); w._events_join(2.0)
xt.close()
print("MQTT_WINDOW_OK", flush=True)
os._exit(0)
"""


def test_the_window_does_what_home_assistant_asks(tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        env[var] = str(tmp_path / var.lower())
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "MQTT_WINDOW_OK" in out.stdout, out.stdout + out.stderr[-4000:]
