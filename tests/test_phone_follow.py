"""The phone remote follows what plays, fits a small phone, and costs the
window's thread little: the state says which channel plays and whether it is
one of the lists' (here), now and next are looked up off the window's thread
in a guide already in memory (never loaded for a poll), the playing
channel's details and the stamp are worked out again only on a change, the
same state again is a 304; the page marks and scrolls to the playing row once
the person's own scrolling has rested, offers "Show in list" when the list
shown leaves it out, asks less often while nothing changes, and its layout
is fluid down to 320 px with every key 44 px or more."""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import re
import threading
import time

import pytest

from dopeiptv.core import phone_page, phone_remote
from dopeiptv.core.phone_remote import PhoneServer
from tests.phone_sign import signed_request

TOKEN = "Ab3_-x9Ab3_-x9Ab3_-x9Ab3_-x9Qz12"


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)


def ask(port, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    path, _data, h = signed_request(TOKEN, "GET", path, None, headers)
    conn.request("GET", path, headers=h)
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, raw


def _sha(text):
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


def _page():
    words = {k: k.replace("_", " ").title() for k in phone_page.WORDS}
    html, csp = phone_page.render({"bg": "#17171C", "text": "#ECECF1", "accent": "#4C8DFF"},
                                  {"bg": "#F5F5F7", "text": "#1B1B1F", "accent": "#4C8DFF"}, words)
    html = html.decode()
    script = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    style = re.search(r"<style>(.*)</style>", html, re.S).group(1)
    return html, csp, script, style


def _rule(style, selector):
    return style.split(selector + "{", 1)[1].split("}", 1)[0]


# -- the window's side -----------------------------------------------------------------------

class Guide:
    """A guide: loaded or not; it notes where it was asked."""

    def __init__(self, loaded):
        self.loaded = loaded
        self.asked: list[bool] = []
        self.bridge = None

    def is_loaded(self):
        return self.loaded

    def listings_for(self, item, limit=8):
        self.asked.append(bool(self.bridge and self.bridge.inside))
        if not self.loaded:
            raise AssertionError("a poll made the guide load")
        now = time.time()
        return [{"title": "Rapport", "start_timestamp": now - 60, "stop_timestamp": now + 60},
                {"title": "Sport", "start_timestamp": now + 60, "stop_timestamp": now + 600}]


class Favs:
    version = 0


class Bridge:
    """The window's thread, as far as the server's side can tell."""

    def __init__(self, do):
        self.do, self.inside, self.asked = do, False, []

    def call(self, action, params):
        self.asked.append(action)
        self.inside = True
        try:
            return self.do(action, params)
        finally:
            self.inside = False


def _window(loaded=True):
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.ui.mw_phone import _PhoneMixin

    class W(_PhoneMixin):
        def __init__(self):
            self.player, self.client, self.favs = None, object(), Favs()
            self.xmltv = Guide(loaded)
            self.elsewhere, self.fav_reads, self.icons_read = "", 0, 0
            self.fav_ids = [7]
            self._last_playback = {"kind": "live", "title": "SVT1",
                                   "item": {"stream_id": 7, "name": "SVT1", "num": 1,
                                            "stream_icon": "http://logos.example/svt1.png"}}

        def _remote_status(self):
            return {"state": "playing", "volume": 50, "source": "Main", "tv_mode": False, "kind": "live",
                    "title": "SVT1", "position": 0, "duration": 0}

        def _chno_pid(self):
            return "a"

        def _favorite_channels(self):
            self.fav_reads += 1
            return [{"stream_id": i} for i in self.fav_ids]

        def _playing_elsewhere(self):
            return self.elsewhere

        def _phone_logo_address(self, icon, picons=None):
            self.icons_read += 1
            return super()._phone_logo_address(icon, picons)

    return W()


def test_the_state_says_which_channel_plays_and_whether_it_is_one_of_the_lists():
    w = _window()
    st = w._phone_state()
    assert st["channel"] == {"id": 7, "name": "SVT1", "number": 1, "logo": True, "here": True}, st
    assert st["now"]["title"] == "Rapport" and st["next"]["title"] == "Sport" and st["time"] > 0
    # Played from another source (failover, the search): its id is that one's.
    w.elsewhere = "Pirat"
    assert w._phone_state()["channel"]["here"] is False
    json.dumps(w._phone_state())                      # all of it fit for the page


def test_a_poll_never_loads_the_guide_and_looks_it_up_off_the_windows_thread():
    w = _window(loaded=False)
    bridge = w._phone_bridge = Bridge(w._phone_do)
    w.xmltv.bridge = bridge
    st = w._phone_call("state", {})
    assert "now" not in st and "next" not in st and w.xmltv.asked == []
    assert st["channel"]["id"] == 7 and st["time"] > 0
    assert bridge.asked == ["_state"]                 # one trip to the window's thread
    w.xmltv.loaded = True
    st = w._phone_call("state", {})
    assert st["now"]["title"] == "Rapport" and st["next"]["title"] == "Sport"
    assert w.xmltv.asked == [False]                   # asked here, not on the window's thread
    # What the window's thread hands over holds nothing but the state and where to look.
    parts, target = w._phone_state_parts()
    assert "now" not in parts and "time" not in parts and target[0] is w.xmltv
    assert target[1]["stream_id"] == 7


def test_the_stamp_and_the_playing_channel_are_worked_out_again_only_on_a_change():
    w = _window()
    first = w._phone_stamp()
    for _ in range(5):
        assert w._phone_stamp() == first
    assert w.fav_reads == 1
    w.fav_ids = [7, 9]
    w.favs.version += 1                               # a favourite added
    assert w._phone_stamp() != first and w.fav_reads == 2
    for _ in range(5):
        w._phone_state()
    assert w.icons_read == 1
    w._last_playback = {"kind": "live", "item": {"stream_id": 9, "name": "SVT2"}}
    assert w._phone_state()["channel"]["id"] == 9 and w.icons_read == 2


def test_a_favourite_store_counts_its_changes(tmp_path):
    pytest.importorskip("PyQt6.QtCore")
    from PyQt6.QtCore import QSettings

    from dopeiptv.core.stores import FavoriteStore
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    store = FavoriteStore(s)
    assert store.version == 0
    store.add("all", {"stream_id": 1})
    store.set_group_locked("Kids", True)
    store.remove(1)
    assert store.version == 3


# -- the same state again ---------------------------------------------------------------------

def test_the_same_state_again_is_a_304_whatever_the_clock_says():
    state = {"state": "playing", "volume": 40, "time": 1.0}

    def call(action, params):
        if action == "state":
            state["time"] += 1.5
            return dict(state)
        return {"channels": [], "favorites": []}

    srv = PhoneServer(0, TOKEN, call, lambda: (b"", ""), host="127.0.0.1")
    srv.start()
    try:
        code, h, raw = ask(srv.port, f"/{TOKEN}/api/state")
        tag = h["etag"]
        assert code == 200 and json.loads(raw)["volume"] == 40 and tag.startswith('"')
        assert h["cache-control"] == "no-store"
        code, h, raw = ask(srv.port, f"/{TOKEN}/api/state", {"If-None-Match": tag})
        assert code == 304 and raw == b"" and h["etag"] == tag
        state["volume"] = 41
        code, h, raw = ask(srv.port, f"/{TOKEN}/api/state", {"If-None-Match": tag})
        assert code == 200 and h["etag"] != tag and json.loads(raw)["volume"] == 41
        assert "etag" not in ask(srv.port, f"/{TOKEN}/api/channels")[1]
    finally:
        srv.stop()
    assert phone_remote.state_tag({"a": 1, "time": 1}) == phone_remote.state_tag({"time": 2, "a": 1})


# -- the page ---------------------------------------------------------------------------------

def test_the_page_follows_what_plays():
    html, csp, script, _style = _page()
    assert f"script-src {_sha(script)};" in csp
    # The playing channel as the state names it, and only one of these lists'.
    assert 'ch && ch.here !== false ? String(ch.id) : null' in script
    show = script[script.index("function show(s)"):script.index("function tick()")]
    assert "markPlaying()" in show and "followSoon(0)" in show and "opened" in show
    # Marked, then brought into view once the person's scrolling has rested.
    fn = script[script.index("function followNow()"):script.index("function fillTo(")]
    assert "REST_MS" in fn and "touching" in fn and "touchedAt" in fn and "bringIntoView(" in fn
    assert "rows.indexOf(" in fn and "fillTo(" in fn
    assert re.search(r"const REST_MS = \d{3,4};", script)
    view = script[script.index("function bringIntoView("):script.index("function showInList()")]
    assert 'behavior: still ? "auto" : "smooth"' in view and "prefers-reduced-motion: reduce" in view
    assert "/ 2" in view and '$("bar")' in view and '$("dock")' in view
    assert "in view: the mark is enough" in view
    hands = script[script.index("function watchHands()"):script.index("function dockRoom()")]
    for ev in ("touchstart", "touchend", "wheel", "scroll"):
        assert f'"{ev}"' in hands
    assert "{passive: true}" in hands and "autoUntil" in hands
    # Rows far down drawn a few hundred a frame first.
    assert "requestAnimationFrame(step)" in script
    # Not in the list shown: offered, never switched to on its own.
    assert '<button class="showIn" id="showIn" hidden>' in html
    offer = script[script.index("function offer()"):script.index("function followNow()")]
    assert "tvOn" in offer and "rows.includes(" in offer
    sil = script[script.index("function showInList()"):script.index("function watchHands()")]
    assert 'setTab("all")' in sil and 'query = ""' in sil and "follow = 2" in sil
    assert "W.show_in_list" in script and "show_in_list" in phone_page.WORDS
    # TV mode back off: a change it missed is followed then.
    tv = script[script.index("function setTv("):script.index("function setTouch(")]
    assert "followSoon(0)" in tv


def test_the_page_asks_with_its_tag_and_less_often_while_nothing_changes():
    _html, _csp, script, _style = _page()
    get = script[script.index("async function getState()"):script.index("function hide(")]
    assert '"If-None-Match": etag' in get and "r.status === 304" in get and 'r.headers.get("ETag")' in get
    delay = script[script.index("function pollDelay()"):script.index("async function poll()")]
    assert "1500" in delay and "3000" in delay and "1000" in delay and "quickUntil" in delay
    assert "quickUntil = Date.now() +" in script[script.index("async function send("):]
    poll = script[script.index("async function poll()"):script.index("async function loadChannels(")]
    assert "same++" in poll and "pollDelay()" in poll and "document.hidden" in poll


def test_the_page_fits_a_small_phone():
    html, csp, _script, style = _page()
    assert f"style-src {_sha(style)};" in csp
    # Fluid, not one layout per phone: the bar, the dock and the remote's
    # rows wrap; the remote is a grid that gives the d-pad the room left.
    assert style.count("clamp(") >= 20
    for sel in (".sticky", ".dockIn", ".folds", ".stage"):
        assert "flex-wrap:wrap" in _rule(style, sel), sel
    assert "minmax(var(--pad-min),1fr)" in _rule(style, "\n.tvr")
    assert 'class="side"' in html and 'class="dockIn"' in html
    # The safe areas: the notch on either side, the home bar, the status bar.
    for edge in ("left", "right", "top", "bottom"):
        assert f"env(safe-area-inset-{edge}" in style
    assert "top:var(--safe-t)" in _rule(style, ".sticky")
    # Every key at least 44 px.
    assert "height:44px" in _rule(style, ".tvBtn") and "min-width:44px" in _rule(style, ".tvBtn")
    assert "min-height:44px" in _rule(style, ".pchip") and "min-height:44px" in _rule(style, ".tab")
    assert "min-width:44px" in _rule(style, ".key") and "min-height:44px" in _rule(style, ".showIn")
    assert "min-height:var(--key-h)" in _rule(style, ".pill") and "min-height:46px" in _rule(style, ".fold")
    assert "--key-h:clamp(46px," in style and "height:44px" in _rule(style, ".nowPill")
    assert "--pad:clamp(150px," in style                # 3 x 44 px and more across the d-pad
    # Nothing sideways, and the bar stays stuck (overflow on html/body would unstick it).
    assert "overflow-x" not in style
    assert "minmax(0,1fr)" in _rule(style, ".colours")
    # A long list scrolls without laying out every row.
    assert ".list li{content-visibility:auto;contain-intrinsic-size:auto 58px}" in style
    # The list ends above the dock however tall it is.
    assert 'setProperty("--dock"' in _script and "ResizeObserver" in _script
    # Still only colours from the palette.
    rules = style.split("@media (prefers-color-scheme: light)")[1].split("}", 1)[1]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", rules)


def test_the_window_gives_the_page_its_new_word():
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.i18n import tr
    from dopeiptv.ui import mw_phone
    words = mw_phone.page_words()
    assert words["show_in_list"] == tr("phone_page_show_in_list") and words["show_in_list"]


def test_a_poll_from_the_server_thread_is_answered_while_the_guide_is_away():
    """The page is served by the real server; the window's work is one call."""
    w = _window(loaded=False)
    w._phone_bridge = Bridge(w._phone_do)
    srv = PhoneServer(0, TOKEN, w._phone_call, lambda: (b"", ""), host="127.0.0.1")
    srv.start()
    try:
        out = {}
        t = threading.Thread(target=lambda: out.update(r=ask(srv.port, f"/{TOKEN}/api/state")))
        t.start()
        t.join(10)
        code, h, raw = out["r"]
        st = json.loads(raw)
        assert code == 200 and st["channel"]["here"] is True and "now" not in st and h["etag"]
    finally:
        srv.stop()
