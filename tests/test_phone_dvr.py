"""A live channel's rewind buffer on the phone remote: while one plays in the
player's buffer the state says how far behind live and how far back the
buffer reaches (the DVR row's reading, nothing asked of mpv), told so the
state keeps its tag while only the clock moves; the phone goes back, skips
and returns to live through the DVR row's own paths (the slider, the
arrows, LIVE) - never a stream of its own; the page's bar, skips and Live,
mirrored right to left, and in a real browser where node with Playwright's
Chromium is there."""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.phone_sign import signed_request
from dopeiptv.core import phone_page
from dopeiptv.core.local_buffer import LIVE_REOPEN_LAG, BufferWindow, LiveLag
from dopeiptv.core.phone_remote import BufferTold, PhoneServer, RateLimiter, buffer_reckoned, state_tag

TOKEN = "Ab3_-x9Ab3_-x9Ab3_-x9Ab3_-x9Qz12"
LOCALES = Path(__file__).resolve().parent.parent / "dopeiptv" / "locale"


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)


# -- what the phone is told ---------------------------------------------------------------------

def test_played_behind_live_the_same_is_told_while_only_the_clock_moves():
    told = BufferTold()
    first = told.tell("u", 252.0, 600.0, False, True, 1000.0)
    assert first == {"behind": 252.0, "reach": 600.0, "live": False, "grows": True, "t": 1000.0}
    # Playing: behind live stays, the buffer fills by whole segments.
    for s in range(1, 9):
        again = told.tell("u", 252.0 + (s % 2) * 1.5, 600.0 + s - (s % 2), False, True, 1000.0 + s)
        assert again is first
    # The poll's tag leaves the clock out: the same state again is a 304.
    tags = {state_tag({"state": "playing", "dvr": told.tell("u", 252.0, 600.0 + s, False, True, 1000.0 + s),
                       "time": 1000.0 + s}) for s in range(3)}
    assert len(tags) == 1


def test_a_full_buffer_is_known_within_moments_and_then_stays_put():
    told = BufferTold()
    seen = []
    for s in range(0, 90):
        d = told.tell("u", 0.0, 1800.0, True, True, float(s))
        if not seen or d is not seen[-1]:
            seen.append(d)
    # Assumed filling at first; told it no longer does once looked at (EVERY).
    assert seen[0]["grows"] is True and seen[-1]["grows"] is False
    assert len(seen) <= 4 and seen[-1]["t"] <= 2 * BufferTold.EVERY
    assert all(d["live"] is True and d["behind"] == 0.0 for d in seen)


def test_a_pause_a_seek_live_and_another_channel_are_told_at_once():
    told = BufferTold()
    a = told.tell("u", 30.0, 600.0, False, True, 0.0)
    paused = told.tell("u", 30.0, 601.0, False, False, 1.0)
    assert paused is not a and paused["t"] == 1.0
    # Paused, behind live grows with the clock - as the page reckons it.
    for s in range(2, 20):
        assert told.tell("u", 30.0 + (s - 1), 600.0 + s, False, False, float(s)) is paused
    assert buffer_reckoned(paused, 11.0, False) == (40.0, 611.0)
    played = told.tell("u", 49.0, 620.0, False, True, 20.0)
    assert played is not paused
    sought = told.tell("u", 300.0, 621.0, False, True, 21.0)
    assert sought is not played and sought["behind"] == 300.0
    live = told.tell("u", 0.0, 622.0, True, True, 22.0)
    assert live["live"] is True and live is not sought
    other = told.tell("v", 0.0, 4.0, True, True, 23.0)
    assert other is not live and other["reach"] == 4.0 and other["grows"] is True
    told.forget()
    assert told.told is None


def test_the_reckoning_never_puts_the_playhead_past_the_oldest_moment():
    full = {"behind": 590.0, "reach": 600.0, "live": False, "grows": False, "t": 100.0}
    assert buffer_reckoned(full, 130.0, False) == (600.0, 600.0)
    assert buffer_reckoned(full, 130.0, True) == (590.0, 600.0)
    filling = dict(full, grows=True)
    assert buffer_reckoned(filling, 130.0, True) == (590.0, 630.0)
    assert buffer_reckoned(filling, 90.0, False) == (590.0, 600.0)       # no clock running back
    told = BufferTold()
    assert told.tell("u", 900.0, 600.0, False, True, 0.0)["behind"] == 600.0


# -- the player: the DVR row's reading and its slider ---------------------------------------------

_APP: list = []


class _Mpv(dict):
    def __init__(self):
        self.playback_time = 21.0
        self.demuxer_cache_state = None
        self.commands = []

    def command(self, *args):
        self.commands.append(args)


def _player(window):
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

    from dopeiptv.media.embedded import EmbeddedPlayer, _SeekSlider
    _APP.append(QApplication.instance() or QApplication([]))       # kept alive
    m = _Mpv()
    p = SimpleNamespace(
        video=SimpleNamespace(mpv=m), current_url="http://x.invalid/l.ts",
        _sync_bar_switch=lambda: False, _local_buffer_enabled=True, _local_buffer_ready=False,
        _local_slider_window=None, _local_fs_window=None, _paused=False, _local_at_live=False,
        _dvr_live=True, _live_ahead=0.0, _dvr_span=0.0, _live_lag=LiveLag(),
        local_buffer_time=QLabel(), local_buffer_seek=_SeekSlider(), local_buffer_live=QPushButton(),
        reopen_live=object(), receivers=lambda _s: 1, _dvr=None)
    p.local_buffer_window = lambda: window[0]
    p._set_local_buffer_visible = lambda _v: None
    p._local_buffer_hide_timer = SimpleNamespace(stop=lambda: None)
    p._label_live_button = lambda force=False: None
    p._seek_local_target = lambda t, w: EmbeddedPlayer._seek_local_target(p, t, w)
    p._seek_local_buffer = lambda s, o=None: EmbeddedPlayer._seek_local_buffer(p, s, o)
    p._lag_counted = lambda: EmbeddedPlayer._lag_counted(p)
    p.dvr_reading = lambda: EmbeddedPlayer.dvr_reading(p)
    p._set_mpv = lambda k, v: m.__setitem__(k, v)
    return p, m, EmbeddedPlayer


def test_the_reading_is_the_dvr_rows_own_and_the_phones_bar_seeks_through_its_slider():
    window = [BufferWindow(0, 30)]
    p, m, ep = _player(window)
    assert ep.dvr_reading(p) is None                     # no row yet
    ep._update_local_buffer(p, 21)                       # at live: the network buffer is live
    assert ep.dvr_reading(p) == (0.0, 21.0, True)
    # Rewound 16 s: behind live, from the live point.
    ep._seek_local_target(p, 5, window[0])
    window[0] = BufferWindow(0, 34)
    ep._update_local_buffer(p, 9)
    assert ep.dvr_reading(p) == (16.0, 25.0, False)
    # The phone's bar let go 10 s behind live: the slider's own seek.
    assert ep.seek_behind_live(p, 10.0) is True
    assert m.commands[-1] == ("seek", 15.0, "absolute+exact")
    # Behind nothing: the live point, never the very end of what arrived.
    assert ep.seek_behind_live(p, 0.0) is True
    assert m.commands[-1] == ("seek", 25.0, "absolute+exact") and p._dvr_live is True
    # Further back than the buffer reaches: its oldest moment.
    ep.seek_behind_live(p, 999.0)
    assert m.commands[-1] == ("seek", 0.25, "absolute+exact")


def test_far_behind_with_mpvs_own_buffer_the_reach_counts_the_lag_and_no_row_no_reading():
    window = [BufferWindow(0, 30)]
    p, m, ep = _player(window)
    ep._update_local_buffer(p, 21)
    p._live_lag.lag = LIVE_REOPEN_LAG + 5
    behind, reach, live = ep.dvr_reading(p)
    assert reach == 21.0 + LIVE_REOPEN_LAG + 5 and live is True
    # Asked to be nearer live than the buffer's end: its live point.
    ep.seek_behind_live(p, 3.0)
    assert m.commands[-1] == ("seek", 21.0, "absolute+exact")
    p.current_url = None
    assert ep.dvr_reading(p) is None and ep.seek_behind_live(p, 5.0) is False
    p.current_url = "http://x.invalid/l.ts"
    window[0] = None
    ep._update_local_buffer(p, 21)                       # the row gone (HLS, the buffer off)
    assert ep.dvr_reading(p) is None and p._local_at_live is False


# -- the window: the state and the commands -----------------------------------------------------

class _Player:
    def __init__(self, reading, calls):
        self.reading, self.calls = reading, calls
        self.current_url = "http://x.invalid/live/1.ts"
        self._paused, self._seek_mode = False, "live"
        self.vol = SimpleNamespace(value=lambda: 40)

    def dvr_reading(self):
        return self.reading

    def seek_behind_live(self, behind):
        self.calls.append(("behind", behind))
        return True

    def _local_go_live(self):
        self.calls.append(("live",))

    def _relative_seek(self, s):
        self.calls.append(("rel", s))

    def _do_seek(self, s):
        self.calls.append(("abs", s))

    def playback_position(self):
        return 0.0

    def playback_duration(self):
        return 0.0


def _window(reading, kind="live"):
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.ui.mw_mpris import _MprisMixin
    from dopeiptv.ui.mw_phone import _PhoneMixin
    from dopeiptv.ui.mw_remote import _RemoteMixin

    calls: list = []

    class W(_PhoneMixin, _RemoteMixin, _MprisMixin):
        def __init__(self):
            self.player = _Player(reading, calls)
            self._playing_key = "k1"
            self._last_playback = {"kind": kind, "title": "TV4 HD", "icon_url": None, "item": {}}
            self.xmltv = SimpleNamespace(current_programme=lambda it: None)
            self.logos = SimpleNamespace(disk_file=lambda u: None)

        def _multiview_active(self):
            return None

        def _has_next_episode(self):
            return False

        def _has_previous_episode(self):
            return False

        def _profile_locked(self):
            return False

        def _phone_stamp(self):
            return "s"

        def _phone_sections(self):
            return []

        def _phone_rec_state(self):
            return None

        def _phone_rec_asks(self):
            return True

        def _phone_late_ask(self):
            return None

    return W(), calls


def test_the_phone_seeks_in_the_buffer_through_the_dvr_rows_own_paths(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    w, calls = _window((252.0, 600.0, False))
    # The bar let go: the row's slider, clamped to what the buffer holds.
    assert w._phone_do("seek", {"behind": 120}) == {"ok": True, "behind": 120.0}
    assert w._phone_do("seek", {"behind": 5000})["behind"] == 600.0
    assert w._phone_do("seek", {"behind": -3})["behind"] == 0.0
    # The skips: the player's arrows, through the media keys' seek.
    assert w._phone_do("seek", {"by": -10}) == {"ok": True, "behind": 262.0}
    # Live: the row's LIVE.
    assert w._phone_do("seek", {"live": True}) == {"ok": True, "behind": 0.0}
    assert calls == [("behind", 120.0), ("behind", 600.0), ("behind", 0.0), ("rel", -10.0), ("live",)]
    # Live far behind opens the channel again: the app's questions in it are the phone's.
    from dopeiptv.ui.mw_phone import _ASKING
    assert "seek" in _ASKING
    for bad in ({}, {"behind": "soon"}, {"by": float("nan")}, {"behind": float("inf")}):
        with pytest.raises(ValueError):
            w._phone_do("seek", bad)


def test_a_skip_forward_stops_at_live():
    w, calls = _window((12.0, 600.0, False))
    assert w._phone_do("seek", {"by": 30}) == {"ok": True, "behind": 0.0}
    assert calls == [("rel", 12.0)]
    at_live, calls2 = _window((0.0, 600.0, True))
    assert at_live._phone_do("seek", {"by": 30}) == {"ok": True, "behind": 0.0}
    assert calls2 == []


def test_without_a_buffer_nothing_of_it_is_sought():
    live, calls = _window(None)
    assert live._phone_do("seek", {"live": True}) == {"ok": False}
    assert live._phone_do("seek", {"behind": 30}) == {"ok": False}
    assert live._phone_do("seek", {"by": -10}) == {"ok": False}     # a live channel cannot seek
    assert calls == []


def test_the_state_says_where_in_the_buffer_and_keeps_its_tag(monkeypatch):
    from dopeiptv.ui import mw_phone
    clock = [1000.0]
    monkeypatch.setattr(mw_phone.time, "time", lambda: clock[0])
    w, _calls = _window((252.0, 600.0, False))
    st, _target = w._phone_state_parts()
    assert st["dvr"] == {"behind": 252.0, "reach": 600.0, "live": False, "grows": True, "t": 1000.0}
    assert st["seek"] is False                            # a live channel has no length
    # Two seconds on, behind live as it was and the buffer two seconds fuller.
    clock[0] = 1002.0
    w.player.reading = (252.4, 602.0, False)
    st2, _target = w._phone_state_parts()
    assert st2["dvr"] is st["dvr"] and state_tag(dict(st2, time=1)) == state_tag(dict(st, time=2))
    # Small: three numbers and two flags, nothing else.
    assert len(json.dumps(st2["dvr"])) < 100
    # No buffer (the setting off, an HLS channel): nothing of it.
    w.player.reading = None
    assert "dvr" not in w._phone_state_parts()[0]
    # A film never, nor anything while stopped.
    film, _c = _window((10.0, 60.0, False), kind="movie")
    assert "dvr" not in film._phone_state_parts()[0]
    w.player.reading = (1.0, 60.0, False)
    w.player.current_url = None
    w._playing_key = None
    st3, _target = w._phone_state_parts()
    assert st3["state"] == "stopped" and "dvr" not in st3 and w._phone_buffer.told is None


def test_the_seek_goes_over_the_server_as_it_was_sent():
    calls = []

    def call(action, params):
        calls.append((action, params))
        return {"ok": True}
    srv = PhoneServer(0, TOKEN, call, lambda: (b"", ""), host="127.0.0.1")
    srv.start()
    try:
        for body in ({"behind": 30}, {"by": -10}, {"live": True}):
            path, data, h = signed_request(TOKEN, "POST", f"/{TOKEN}/api/seek", body)
            conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
            conn.request("POST", path, body=data, headers=h)
            assert conn.getresponse().status == 200
            conn.close()
        assert calls == [("seek", {"behind": 30}), ("seek", {"by": -10}), ("seek", {"live": True})]
    finally:
        srv.stop()


# -- the words ------------------------------------------------------------------------------------

def test_the_new_words_are_in_every_language():
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.i18n import tr
    from dopeiptv.ui import mw_phone
    assert {"live", "go_live", "behind"} <= set(phone_page.WORDS)
    words = mw_phone.page_words()
    assert words["live"] == tr("phone_page_live") == "Live"
    assert words["go_live"] == tr("phone_page_go_live") and "{t}" in words["behind"]
    files = sorted(LOCALES.glob("*.json"))
    assert len(files) == 26
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        for key in ("phone_page_live", "phone_page_go_live", "phone_page_behind"):
            assert str(data.get(key) or "").strip(), (f.name, key)
        assert data["phone_page_behind"].count("{t}") == 1, f.name


# -- the page -------------------------------------------------------------------------------------

def _sha(text):
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


def _page(rtl=False):
    words = {k: k.replace("_", " ").title() for k in phone_page.WORDS}
    html, csp = phone_page.render({"bg": "#17171C", "text": "#ECECF1", "accent": "#4C8DFF"},
                                  {"bg": "#F5F5F7", "text": "#1B1B1F", "accent": "#4C8DFF"}, words,
                                  "ar" if rtl else "en", rtl, skips=(10, 30))
    html = html.decode()
    script = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    style = re.search(r"<style>(.*)</style>", html, re.S).group(1)
    return html, csp, script, style


def _fn(script, name, nxt):
    return script[script.index(f"function {name}("):script.index(f"function {nxt}(")]


def test_the_page_seeks_the_buffer_on_the_same_bar_with_one_request_when_let_go():
    html, csp, script, style = _page()
    assert f"script-src {_sha(script)};" in csp and f"style-src {_sha(style)};" in csp
    # Live in the row under the bar, hidden until the state has a buffer.
    times = re.search(r'<div class="nowTimes" id="nowTimes">.*?</div>', html).group(0)
    assert re.search(r'<button class="liveBtn" id="liveBtn" hidden>', times)
    assert 'class="toLive flip"' in times                 # toward live, mirrored right to left
    # The same bar: a buffer can be sought, never in TV mode.
    can = _fn(script, "canSeek", "playPos")
    assert "st.dvr" in can and "!tvOn" in can and "st.seek" in can
    # The finger's place as how far behind live; one seek when let go.
    drag = _fn(script, "dragShow", "seekEnd")
    assert "st.dvr" in drag and "ago(b)" in drag and "W.live" in drag
    end = _fn(script, "seekEnd", "seekTo")
    assert "dvrTo(" in end and "behind:" in end and "seekTo(" in end
    assert script.count('send("api/seek"') == 1
    move = script[script.index('"pointermove"'):script.index('"pointerup"')]
    assert "api/seek" not in move and "dvrTo(" not in move
    # A seek asks for the next state whole, and the bar stays where it was sent.
    sought = _fn(script, "sought", "skip")
    assert 'etag = ""' in sought
    to = _fn(script, "dvrTo", "sought")
    assert "dvrHold = {" in to and "until" in to


def test_the_page_reckons_the_buffer_as_the_app_does_and_shows_live_or_how_far_behind():
    _html, _csp, script, style = _page()
    now = _fn(script, "dvrNow", "ago")
    # buffer_reckoned's rules: the reach grows only while filling, behind
    # live only while paused, never past the oldest moment; held after a seek.
    assert "d.grows ? el : 0" in now and "playing ? 0 : el" in now and "Math.min(reach" in now
    assert "dvrHold" in now and "Math.abs(real.behind - want) <= 2" in now
    # "−4:12" kept left to right inside a right-to-left page.
    assert '"\\u2066\\u2212" + hms(secs) + "\\u2069"' in script
    tick = _fn(script, "dvrTick", "seekBar")
    assert 'classList.toggle("on", n.live)' in tick and "W.live : ago(behind)" in tick
    assert "n.live ? W.live : W.go_live" in tick                # what a screen reader hears
    assert "ago(n.reach)" in tick and 'W.behind.replace("{t}"' in tick
    # The buffer's bar comes before the programme's on a live channel.
    t = _fn(script, "tick", "setVol")
    assert t.index("dvrTick()") < t.index("s.now && s.now.stop")
    # Skips stop at live; Live is the row's LIVE.
    skip = _fn(script, "skip", "goLive")
    assert "n.behind < 1" in skip and "dvrTo(n.behind - by, {by})" in skip
    assert 'dvrTo(0, {live: true})' in _fn(script, "goLive", "dvrTick")
    assert 'label("liveBtn", W.go_live)' in script and '$("liveBtn").addEventListener("click", goLive)' in script
    # Its look: a key of 44 px, lit as the app's LIVE while at the live edge.
    rule = style.split(".liveBtn{", 1)[1].split("}", 1)[0]
    assert "height:44px" in rule and "min-width:44px" in rule and "white-space:nowrap" in rule
    assert ".liveBtn.on{background:var(--rec)" in style
    # On a narrow phone Live takes a line of its own rather than run past the card.
    assert ".nowTimes.dvr{flex-wrap:wrap" in style
    shown = _fn(script, "seekable", "barFrac")
    assert 'hide($("liveBtn"), !dvr)' in shown and 'hide($("nowEnd"), dvr)' in shown


# -- the page in a real browser ---------------------------------------------------------------

def _node():
    """(node, its environment) when Playwright and its Chromium are there, else None."""
    from tests.test_phone_layout import _node as found
    return found()


class _Host:
    """A live channel 4:12 behind live in a full buffer reaching back 32:10,
    told as the window tells it; a seek moves it as the player would, Stop
    puts it back for the next run."""

    def __init__(self):
        self.lock = threading.Lock()
        self.t0 = int(time.time())
        self.seeks: list = []
        self.reset()

    def reset(self):
        self.dvr = {"behind": 252.0, "reach": 1930.0, "live": False, "grows": False, "t": time.time()}

    def __call__(self, action, params):
        with self.lock:
            if action == "state":
                return {"state": "playing", "time": time.time(), "volume": 64, "muted": False, "source": "Pirat",
                        "stamp": "s1", "tv_mode": False, "kind": "live", "title": "TV4 HD", "position": 0,
                        "duration": 0, "seek": False, "tracks": "1", "rec_ask": False, "can_rec": True,
                        "channel": {"id": 1002, "name": "TV4 HD", "number": 3, "logo": False, "here": True},
                        "now": {"title": "Rapport", "start": self.t0 - 900, "stop": self.t0 + 900},
                        "dvr": self.dvr, "sections": ["all", "fav"]}
            if action == "channels":
                return {"channels": [{"id": 1002, "name": "TV4 HD", "number": 3, "logo": False}],
                        "favorites": []}
            if action == "seek":
                self.seeks.append(params)
                behind, reach = self.dvr["behind"], self.dvr["reach"]
                if params.get("live"):
                    behind = 0.0
                elif "behind" in params:
                    behind = float(params["behind"])
                else:
                    behind = max(0.0, min(reach, behind - float(params["by"])))
                self.dvr = dict(self.dvr, behind=behind, live=behind < 1, t=time.time())
                return {"ok": True, "behind": behind}
            if action == "stop":
                self.reset()
            return None if action == "logo" else {"ok": True}


def test_the_buffer_on_the_page_in_a_browser():
    pytest.importorskip("PyQt6.QtWidgets")
    found = _node()
    if found is None:
        pytest.skip("node with Playwright's Chromium is not installed")
    node, env = found
    from dopeiptv import i18n
    from dopeiptv.ui import mw_phone
    servers, hosts = {}, {}
    try:
        for lang in ("en", "ar"):
            i18n.set_language(lang)
            try:
                words = mw_phone.page_words()
            finally:
                i18n.set_language("en")
            dark, light = mw_phone.page_palettes(False)
            page = phone_page.render(dark, light, words, lang, lang == "ar")
            hosts[lang] = _Host()
            servers[lang] = PhoneServer(0, TOKEN, hosts[lang], lambda p=page: p, host="127.0.0.1",
                                        limiter=RateLimiter(rate=500.0, burst=500.0))
            servers[lang].start()
        runs = [{"url": f"http://127.0.0.1:{servers[lang].port}/r/#{TOKEN}", "w": w, "h": h, "lang": lang}
                for lang, w, h in (("en", 320, 568), ("en", 390, 844), ("ar", 375, 667))]
        done = subprocess.run([node, str(Path(__file__).parent / "data" / "phone_dvr.js"), json.dumps(runs)],
                              capture_output=True, text=True, timeout=300, env=env)
    finally:
        for srv in servers.values():
            srv.stop()
    lines = done.stdout.strip().splitlines()
    assert lines, done.stderr[-2000:]
    out = json.loads(lines[-1])
    assert "fatal" not in out, out.get("fatal")
    assert out["problems"] == [], "\n".join(out["problems"])
    assert out["errors"] == [], "\n".join(out["errors"])
    for tag, r in out["runs"].items():
        first = r["first"]
        # The bar from 32:10 back to live, 4:12 behind; Live says how far.
        assert first["start"] == "\u2066\u221232:10\u2069" and first["live"] == "\u2066\u22124:12\u2069", tag
        assert first["can"] and first["endHidden"] and not first["on"], tag
        assert abs(float(first["bar"].rstrip("%")) - 100 * (1 - 252 / 1930)) < 0.2, tag
        # Dragged to the middle: the time under the finger, then where it went.
        assert re.fullmatch("\u2066\u221216:0[4-6]\u2069", r["tip"]), (tag, r["tip"])
        assert re.fullmatch("\u2066\u221216:0[4-6]\u2069", r["afterDrag"]["live"]), tag
        assert not r["afterDrag"]["on"], tag
        after_back = r["afterBack"]["live"]
        assert re.fullmatch("\u2066\u221216:1[4-6]\u2069", after_back), (tag, after_back)
        assert r["afterLive"]["on"] and r["afterLive"]["live"] == r["words"]["live"], tag
        assert r["afterLive"]["bar"] == "100%", tag
    # Each run: one seek for the drag, one for the skip back, one for Live;
    # the skip forward at live sends nothing. Mirrored: the same seeks.
    for lang, host in hosts.items():
        runs_of = 2 if lang == "en" else 1
        assert len(host.seeks) == 3 * runs_of, (lang, host.seeks)
        for i in range(runs_of):
            drag, back, live = host.seeks[3 * i:3 * i + 3]
            assert set(drag) == {"behind"} and 963 <= drag["behind"] <= 967, (lang, drag)
            assert back == {"by": -10} and live == {"live": True}, lang
    # Steady at live: the same state again is a 304.
    assert out["steady"] == [304, 304]
