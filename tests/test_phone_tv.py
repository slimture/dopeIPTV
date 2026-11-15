"""The phone remote and TV mode together: the state says whether TV mode is
on and the page turns into a remote while it is (a d-pad, the colour keys,
a touchpad); TV mode on and off and its keys asked by the phone, the remote
API and Home Assistant, every one of them through the window's one method
(mw_tv.tv_command, into _tv_input as a pad's keys); the touchpad's gestures
read by the server; and while TV mode is off nothing those keys do touches
playback but the player's own controls."""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import re

import pytest

from dopeiptv.core import events, mqtt_control, phone_page, phone_remote, tv_input
from dopeiptv.core.phone_remote import PhoneServer
from dopeiptv.core.remote_api import RemoteServer

TOKEN = "Ab3_-x9Ab3_-x9Ab3_-x9Ab3_-x9Qz12"


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)


def ask(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=h)
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    return r.status, (json.loads(raw) if raw[:1] == b"{" else raw)


# -- what is asked, read the same everywhere -----------------------------------------------

def test_tv_mode_on_off_or_toggle_and_its_keys_are_read_alike():
    sw = tv_input.switch_mode
    assert [sw(v) for v in ("on", "ON", " Off ", "toggle", "TOGGLE", "true", "0", True, False)] == \
        ["on", "on", "off", "toggle", "toggle", "on", "off", "on", "off"]
    assert all(sw(v) is None for v in ("", "press", "maybe", None, 1, ["on"]))
    act = tv_input.remote_action
    assert act("up") == "up" and act(" OK ") == "ok" and act("digit_7") == "digit_7" and act("Red") == "red"
    assert all(act(v) is None for v in ("", "reboot", "digit_10", "digit_", None, 5, {"a": 1}))
    assert all(act(a) == a for a in tv_input.ALL)


def test_the_touchpad_swipe_is_an_arrow_a_tap_ok_a_hold_back():
    g = phone_remote.gesture
    m = phone_remote.SWIPE_MIN
    assert g(m, 0, 90) == "right" and g(-m - 30, 12, 90) == "left"
    assert g(8, m, 90) == "down" and g(-20, -m - 1, 400) == "up"
    assert g(-80, 79, 120) == "left" and g(30, -60, 120) == "up"        # the larger axis wins
    assert g(150, 30, 2000) == "right"                                   # a slow swipe is still one
    assert g(0, 0, 80) == "ok" and g(phone_remote.TAP_SLOP, -3, phone_remote.HOLD_MS - 1) == "ok"
    assert g(2, 3, phone_remote.HOLD_MS) == "back" and g(0, 0, 5000) == "back"
    assert g(phone_remote.TAP_SLOP + 1, 0, 100) is None                  # neither a tap nor a swipe
    assert g(m - 1, m - 1, 100) is None
    assert g(float("nan"), 0, 100) is None


def test_home_assistant_asks_tv_mode_and_its_keys():
    P = "home/tv"
    parse = mqtt_control.parse
    assert parse(P, f"{P}/command/tv", b"ON") == ("tv", "on")
    assert parse(P, f"{P}/command/tv", b"OFF") == ("tv", "off")
    assert parse(P, f"{P}/command/tv", b"toggle") == ("tv", "toggle")
    assert parse(P, f"{P}/command/tv_key", b"DOWN") == ("tv_key", "down")
    assert parse(P, f"{P}/command/tv_key", b"digit_4") == ("tv_key", "digit_4")
    for topic, payload, retained in ((f"{P}/command/tv", b"ON", True), (f"{P}/command/tv", b"PRESS", False),
                                     (f"{P}/command/tv", b"", False), (f"{P}/command/tv_key", b"reboot", False),
                                     (f"{P}/command/tv_key", b"", False)):
        assert parse(P, topic, payload, retained) is None, (topic, payload)
    # Commands off: the switch goes with the rest.
    gone = dict(mqtt_control.discovery("n1", P, {"name": "d"}, None))
    assert "homeassistant/switch/n1/tv_mode/config" in gone and gone["homeassistant/switch/n1/tv_mode/config"] is None


def test_the_state_home_assistant_reads_says_whether_tv_mode_is_on():
    d = events.Dispatcher()
    d.set_state("idle")
    assert "tv_mode" not in d.state                    # not known yet: not said
    d.set_tv_mode(True)
    d.set_state("playing", {"kind": "live", "title": "SVT1"})
    assert d.state["tv_mode"] is True and d.state["state"] == "playing"
    d.set_tv_mode(False)
    d.set_state("idle")
    assert d.state["tv_mode"] is False
    d.set_state("off")
    assert "tv_mode" not in d.state                    # the app gone: nothing on


# -- the page ---------------------------------------------------------------------------------

def _sha(text):
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


def _page():
    words = {k: k.replace("_", " ").title() for k in phone_page.WORDS}
    dark = {"bg": "#17171C", "text": "#ECECF1", "accent": "#4C8DFF", "key_red": "#FF5C5C",
            "key_green": "#2FBF71", "key_yellow": "#E8B339", "key_blue": "#4C8DFF"}
    html, csp = phone_page.render(dark, dict(dark, bg="#F5F5F7"), words, "en")
    return html.decode(), csp


def test_the_page_has_a_remote_for_tv_mode_and_its_policy_still_allows_only_it():
    html, csp = _page()
    script = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    style = re.search(r"<style>(.*)</style>", html, re.S).group(1)
    assert f"script-src {_sha(script)};" in csp and f"style-src {_sha(style)};" in csp
    assert "unsafe" not in csp
    # The TV mode button in the header, always there.
    header = re.search(r'<header class="top">(.*?)</header>', html, re.S).group(1)
    assert 'id="tvBtn"' in header and 'aria-pressed="false"' in header
    # The remote: every key a TV key, the colour keys with their labels, the digits folded.
    remote = re.search(r'<section class="tvr" id="tvr" hidden>(.*?)</section>', html, re.S).group(1)
    keys = set(re.findall(r'data-a="([a-z_0-9]+)"', remote))
    assert keys == {"up", "down", "left", "right", "ok", "back", "menu", "red", "green", "yellow", "blue",
                    "play_pause", "ch_up", "ch_down", "vol_up", "vol_down", "mute"} | set(tv_input.DIGITS)
    assert keys <= tv_input.ALL
    assert 'id="digits" hidden' in remote and 'aria-expanded="false"' in remote
    assert 'id="touch" hidden' in remote and 'id="padMode"' in remote
    for c in ("red", "green", "yellow", "blue"):
        assert f"--key-{c}:#" in style and f"var(--key-{c})" in style
    assert '"key_red": "Key Red"' in script or '"key_red":"Key Red"' in script.replace(" ", "")
    # It asks for TV mode, its keys and the touchpad; remembers the touchpad; ticks.
    for path in ('"api/tv"', '"api/tv_key"', '"api/touch"'):
        assert path in script
    assert 'localStorage.getItem("touchpad")' in script and "navigator.vibrate" in script
    assert "setTv(s.tv_mode)" in script
    # The touchpad's hold and slop are the server's own.
    touch = json.loads(re.search(r"const T = (\{.*?\});", script).group(1))
    assert touch == {"hold": phone_remote.HOLD_MS, "slop": phone_remote.TAP_SLOP}
    # Arrows stay where they point in a right-to-left language.
    assert ".pad,.colours,.deck,.digits{direction:ltr}" in style


def test_the_now_card_is_the_way_back_to_the_picture_only_in_tv_mode_while_something_plays():
    html, _csp = _page()
    script = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    style = re.search(r"<style>(.*)</style>", html, re.S).group(1)
    card = re.search(r'(<section class="now" id="now"[^>]*>)(.*?)</section>', html, re.S)
    # As it always was until TV mode is on and something plays: no button,
    # no label, no pill.
    assert "role=" not in card.group(1) and "tabindex" not in card.group(1)
    assert 'id="nowLabel" hidden' in card.group(2)
    assert 'id="nowPill" aria-hidden="true" hidden' in card.group(2)
    # Then the whole card: a button (Enter and Space too) that asks for the picture.
    fn = script[script.index("function nowCard()"):script.index("function nowPressed()")]
    assert "!!tvOn" in fn and '!== "stopped"' in fn
    assert 'setAttribute("role", "button")' in fn and 'setAttribute("tabindex", "0")' in fn
    assert "W.tv_picture" in fn and 'removeAttribute(a)' in fn
    assert 'send("api/tv_key", {action: "picture"})' in script
    assert 'e.key === "Enter" || e.key === " "' in script
    assert "nowCard();" in script[script.index("function setTv("):script.index("function setTouch(")]
    # Filled, the page's own colours, a big enough target, pressed and focused states.
    assert ".now.act{" in style and "min-height:48px" in style.split(".now.act{")[1].split("}")[0]
    pill = style.split(".nowPill{")[1].split("}")[0]
    assert "background:var(--accent)" in pill and "color:var(--on-accent)" in pill
    assert ".now.act:active{" in style and ".now.act:focus-visible{" in style
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", style.split(".now.act{")[1].split(".sticky{")[0])
    # The pill wraps under the text on a narrow phone instead of squeezing it.
    assert "flex-wrap:wrap" in style.split(".now.act{")[1].split("}")[0]
    assert ".now.act .nowText{flex:1 1 180px}" in style
    assert {"tv_picture", "tv_now_playing"} <= set(phone_page.WORDS)
    assert "picture" in tv_input.ALL and tv_input.remote_action("picture") == "picture"


def test_picture_from_the_phone_shows_what_plays_and_nothing_else():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import QObject
    from PyQt6.QtWidgets import QApplication

    from dopeiptv.ui.mw_phone import _PhoneMixin
    from dopeiptv.ui.mw_tv import _TvMixin
    QApplication.instance() or QApplication([])

    class Player:
        current_url = "http://x/1.ts"

    class Page:
        def __init__(self):
            self.handled = []

        def handle(self, action):
            self.handled.append(action)

    class W(_TvMixin, _PhoneMixin, QObject):
        def __init__(self):
            super().__init__()
            self._tv_on, self.player, self._tv_page = True, Player(), Page()
            self.pictures, self.infos = 0, 0

        def _tv_picture(self):
            self.pictures += 1

        def _tv_show_info(self):
            self.infos += 1

    w = W()
    phone = PhoneServer(0, TOKEN, w._phone_do, lambda: (b"", ""), host="127.0.0.1")
    phone.start()
    try:
        assert ask(phone.port, "POST", f"/{TOKEN}/api/tv_key", {"action": "picture"}) == \
            (200, {"ok": True, "tv_mode": True})
        assert w.pictures == 1 and w._tv_page.handled == []
        w.player.current_url = None                     # nothing plays: nothing happens
        assert w.tv_command("tv_key", "picture") == {"ok": True, "tv_mode": True}
        assert w.pictures == 1 and w._tv_page.handled == []
        w.player.current_url = "http://x/1.ts"
        w._tv_watching = True                           # the picture is up: only the info bar
        w._last_playback = {"kind": "live"}
        w.tv_command("tv_key", "picture")
        assert w.pictures == 1 and w.infos == 1
        w._tv_on, w._tv_watching = False, False         # TV mode off: not TV mode's
        assert w.tv_command("tv_key", "picture") == {"ok": False, "tv_mode": False}
        assert w.pictures == 1
    finally:
        phone.stop()


def test_the_window_gives_the_page_every_word_and_colour():
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.ui import mw_phone
    words = mw_phone.page_words()
    assert set(phone_page.WORDS) <= set(words) and all(words[k] for k in phone_page.WORDS)
    from dopeiptv.i18n import tr
    assert words["tv_picture"] == tr("phone_tv_picture") and words["tv_now_playing"] == tr("phone_tv_now_playing")
    dark, light = mw_phone.page_palettes(False)
    for pal in (dark, light):
        assert all(re.fullmatch(r"#[0-9A-Fa-f]{6}", pal[f"key_{c}"]) for c in ("red", "green", "yellow", "blue"))


# -- the servers: each way in, the same handler ----------------------------------------------

def _window():
    pytest.importorskip("PyQt6.QtCore")
    from PyQt6.QtCore import QObject

    from dopeiptv.ui.mw_events import _EventsMixin
    from dopeiptv.ui.mw_phone import _PhoneMixin
    from dopeiptv.ui.mw_remote import _RemoteMixin
    from dopeiptv.ui.mw_tv import _TvMixin

    class Player:
        current_url = "http://x/1.ts"
        _muted = False

        def __init__(self):
            self.done = []

        def toggle_mute(self):
            self.done.append("mute")

        def _nudge_volume(self, n):
            self.done.append(("volume", n))

    class Cfg:
        commands = True

    class Events:
        active = True
        cfg = Cfg()

    class W(_TvMixin, _PhoneMixin, _RemoteMixin, _EventsMixin, QObject):
        def __init__(self):
            super().__init__()
            self.heard, self.keys, self.done = [], [], []
            self.player = Player()
            self._events = Events()
            self.client = None
            self.playlist_store = None

        def tv_command(self, what, value=None):
            self.heard.append((what, value))
            return super().tv_command(what, value)

        def _tv_input(self, action):
            self.keys.append(action)

        def enter_tv_mode(self):
            self.done.append("enter")
            self._tv_on = True

        def exit_tv_mode(self):
            self.done.append("exit")
            self._tv_on = False

        def _come_forward(self):
            self.done.append("forward")

        def _remote_media(self, name, arg=None):
            self.done.append(("media", name))

        def _play_channel_step(self, step):
            self.done.append(("step", step))
            return True

        def mpris_snapshot(self):
            return {"status": "Stopped", "volume": 0.5}

        def _favorite_channels(self):
            return []

        def _chno_pid(self):
            return "a"

    return W()


def test_the_phone_the_remote_api_and_home_assistant_reach_the_same_handler():
    w = _window()
    phone = PhoneServer(0, TOKEN, w._phone_do, lambda: (b"", ""), host="127.0.0.1")
    remote = RemoteServer("127.0.0.1", 0, TOKEN, w._remote_do)
    phone.start()
    remote.start()
    bearer = {"Authorization": "Bearer " + TOKEN}
    try:
        code, out = ask(phone.port, "POST", f"/{TOKEN}/api/tv", {"mode": "on"})
        assert code == 200 and out == {"ok": True, "tv_mode": True} and w.done[-2:] == ["forward", "enter"]
        # A key from each way in: the same method, into TV mode as a pad's.
        assert ask(phone.port, "POST", f"/{TOKEN}/api/tv_key", {"action": "up"})[0] == 200
        assert ask(remote.port, "POST", "/api/tv_key", {"action": "down"}, bearer)[0] == 200
        cmd = mqtt_control.parse("p", "p/command/tv_key", b"OK")
        w._events_command(*cmd)
        assert w.heard[1:] == [("tv_key", "up"), ("tv_key", "down"), ("tv_key", "ok")], w.heard
        assert w.keys == ["up", "down", "ok"]
        # The touchpad: read by the server, the same key.
        assert ask(phone.port, "POST", f"/{TOKEN}/api/touch", {"dx": -90, "dy": 12, "ms": 140})[0] == 200
        assert ask(phone.port, "POST", f"/{TOKEN}/api/touch", {"dx": 1, "dy": 0, "ms": 900})[0] == 200
        code, out = ask(phone.port, "POST", f"/{TOKEN}/api/touch", {"dx": 20, "dy": 0, "ms": 100})
        assert code == 200 and out == {"ok": False}
        assert ask(phone.port, "POST", f"/{TOKEN}/api/touch", {"dx": "far"})[0] == 400
        assert w.keys[-2:] == ["left", "back"] and len(w.keys) == 5
        # What it cannot take: said, nothing done.
        assert ask(phone.port, "POST", f"/{TOKEN}/api/tv_key", {"action": "reboot"})[0] == 400
        assert ask(remote.port, "POST", "/api/tv", {"mode": "sideways"}, bearer)[0] == 400
        assert ask(phone.port, "GET", f"/{TOKEN}/api/tv_key")[0] == 405
        assert ask(remote.port, "GET", "/api/tv", None, bearer)[0] == 405
        assert len(w.keys) == 5
        # On, off, toggle - from each way in; the state says it.
        assert w._phone_state()["tv_mode"] is True and w._remote_status()["tv_mode"] is True
        code, out = ask(remote.port, "POST", "/api/tv", {"mode": "off"}, bearer)
        assert out == {"ok": True, "tv_mode": False} and w.done[-1] == "exit"
        assert w._phone_state()["tv_mode"] is False
        assert ask(remote.port, "POST", "/api/tv", {}, bearer)[1]["tv_mode"] is True      # no mode: toggle
        assert ask(phone.port, "POST", f"/{TOKEN}/api/tv", {"mode": "toggle"})[1]["tv_mode"] is False
        w._events_command(*mqtt_control.parse("p", "p/command/tv", b"ON"))
        assert w._tv_on and w.done[-1] == "enter"
        w._events_command(*mqtt_control.parse("p", "p/command/tv", b"ON"))       # on already: nothing
        assert w.done.count("enter") == 3
        w._events_command(*mqtt_control.parse("p", "p/command/tv", b"OFF"))
        assert not w._tv_on and w.done[-1] == "exit"
    finally:
        phone.stop()
        remote.stop()


def test_with_tv_mode_off_its_keys_leave_playback_alone():
    w = _window()
    assert not w._tv_on
    player_keys = {"play_pause", "play", "pause", "stop", "ch_up", "ch_down", "vol_up", "vol_down", "mute"}
    for action in sorted(tv_input.ALL - player_keys):
        assert w.tv_command("tv_key", action) == {"ok": False, "tv_mode": False}, action
    assert w.keys == [] and w.done == [] and w.player.done == []
    # The player's own controls are the player's keys, as from anywhere else.
    w.tv_command("tv_key", "play_pause")
    w.tv_command("tv_key", "stop")
    w.tv_command("tv_key", "ch_down")
    assert w.done == [("media", "playpause"), ("media", "stop"), ("step", -1)]
    w.tv_command("tv_key", "vol_up")
    w.tv_command("tv_key", "mute")
    assert w.player.done == [("volume", 5), "mute"] and w.keys == []
    # No player: the volume keys do nothing.
    w.player = None
    assert w.tv_command("tv_key", "vol_down") == {"ok": False, "tv_mode": False}
    with pytest.raises(ValueError):
        w.tv_command("tv_key", "reboot")
    with pytest.raises(ValueError):
        w.tv_command("tv", "maybe")
    with pytest.raises(ValueError):
        w.tv_command("channels", None)
