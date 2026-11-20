"""Seeking and typing from the phone remote: a film's, an episode's, a
file's or a recording's place is sought through the media keys' own seek
(the player's seek bar's), clamped to its length, never in a live channel
or a stream that cannot seek; the page's bar is dragged and let go (one
request) and has the player's skip steps; a search box's field takes the
focus in the tap's own handler (the phone's keyboard comes up); TV mode's
search takes text typed on the phone's own keyboard through tv_command,
plain and capped, and the state says when it shows - its text only, so the
poll's tag changes only as it is typed."""
from __future__ import annotations

import base64
import hashlib
import http.client
import re
import sys

import pytest

from dopeiptv.core import phone_page, tv_input
from dopeiptv.core.phone_remote import PhoneServer
from tests.phone_sign import signed_request

TOKEN = "Ab3_-x9Ab3_-x9Ab3_-x9Ab3_-x9Qz12"


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)


def ask(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    path, data, h = signed_request(TOKEN, method, path, body)
    conn.request(method, path, body=data, headers=h)
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    return r.status, raw


class _Slider:
    def value(self):
        return 40


class _Player:
    def __init__(self, calls, mode="vod", length=3000.0):
        self.calls, self._seek_mode, self.length = calls, mode, length
        self.current_url = "http://x/movie/1.mkv"
        self._paused = False
        self.vol = _Slider()

    def playback_position(self):
        return 120.0

    def playback_duration(self):
        return self.length

    def _do_seek(self, s):
        self.calls.append(("abs", s))

    def _relative_seek(self, s):
        self.calls.append(("rel", s))


def _window(kind="movie", mode="vod", length=3000.0):
    pytest.importorskip("PyQt6.QtWidgets")
    from types import SimpleNamespace

    from dopeiptv.ui.mw_mpris import _MprisMixin
    from dopeiptv.ui.mw_phone import _PhoneMixin
    from dopeiptv.ui.mw_remote import _RemoteMixin

    calls: list = []

    class W(_PhoneMixin, _RemoteMixin, _MprisMixin):
        def __init__(self):
            self.player = _Player(calls, mode, length)
            self._playing_key = "k1"
            self._last_playback = {"kind": kind, "title": "Sintel", "icon_url": None, "item": {}}
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

    return W(), calls


def test_a_place_is_sought_through_the_media_keys_seek_clamped_to_the_length(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    w, calls = _window()
    assert w._phone_do("seek", {"to": 600}) == {"ok": True, "position": 600.0}
    assert w._phone_do("seek", {"to": 99999}) == {"ok": True, "position": 3000.0}
    assert w._phone_do("seek", {"to": -40}) == {"ok": True, "position": 0.0}
    # The skip keys: from where it is (120 s), the player's steps.
    assert w._phone_do("seek", {"by": -10}) == {"ok": True, "position": 110.0}
    assert w._phone_do("seek", {"by": 30}) == {"ok": True, "position": 150.0}
    assert w._phone_do("seek", {"by": -500})["position"] == 0.0
    # Each one the player's own absolute seek - its seek bar's, the media keys'.
    assert calls == [("abs", 600.0), ("abs", 3000.0), ("abs", 0.0), ("abs", 110.0), ("abs", 150.0),
                     ("abs", 0.0)]
    for bad in ({}, {"to": "soon"}, {"by": float("nan")}, {"to": float("inf")}):
        with pytest.raises(ValueError):
            w._phone_do("seek", bad)


def test_nothing_is_sought_in_what_cannot_seek():
    # A live channel: the player has no length for it.
    live, calls = _window(kind="live", mode="live")
    assert live._phone_do("seek", {"to": 60}) == {"ok": False}
    # A stream with no length, and nothing playing.
    film, calls2 = _window(length=0.0)
    assert film._phone_do("seek", {"by": 30}) == {"ok": False}
    stopped, calls3 = _window()
    stopped.player.current_url = None
    assert stopped._phone_do("seek", {"to": 60}) == {"ok": False}
    assert calls == calls2 == calls3 == []


def test_the_state_says_when_a_title_can_be_sought():
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.ui.mw_phone import _PhoneMixin

    class W(_PhoneMixin):
        player = None
        status = {"state": "playing", "volume": 50, "kind": "movie", "title": "Sintel", "position": 12.5,
                  "duration": 888.0}

        def _remote_status(self):
            return dict(self.status)

        def _phone_stamp(self):
            return "s"

        def _phone_sections(self):
            return []
    w = W()
    st, _target = w._phone_state_parts()
    assert st["seek"] is True and st["duration"] == 888.0
    w.status = dict(W.status, kind="live", duration=0)
    assert w._phone_state_parts()[0]["seek"] is False


def test_typed_text_is_plain_and_capped():
    assert tv_input.entry_text("star\ttrek\n") == "star trek "
    assert tv_input.entry_text("a\x00b\x1b[2Jc‮") == "ab[2Jc"
    assert tv_input.entry_text("👨‍👩") == "👨‍👩"           # an emoji's joiner kept
    assert tv_input.entry_text("x" * tv_input.TEXT_MAX) == "x" * tv_input.TEXT_MAX
    for bad in (None, 5, ["a"], "x" * (tv_input.TEXT_MAX + 1)):
        with pytest.raises(ValueError):
            tv_input.entry_text(bad)


def test_the_phones_keyboard_types_into_tv_modes_search_and_the_state_says_it_shows():
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.ui.mw_phone import _PhoneMixin
    from dopeiptv.ui.mw_tv import _TvMixin

    class Search:
        query = ""

        def set_text(self, text):
            self.query = text

    class Page:
        def __init__(self):
            self.search = Search()
            self.showing = True

        def search_screen(self):
            return self.search if self.showing else None

    class W(_PhoneMixin, _TvMixin):
        player = None

        def __init__(self):
            self._tv_on, self._tv_watching, self._tv_page = True, False, Page()

        def _remote_status(self):
            return {"state": "stopped", "volume": 50, "tv_mode": self._tv_on}

        def _phone_stamp(self):
            return "s"

        def _phone_sections(self):
            return []

    w = W()
    assert w._phone_do("tv_text", {"text": "drä\nma"}) == {"ok": True, "tv_mode": True, "text": "drä ma"}
    assert w._tv_page.search.query == "drä ma"
    st = w._phone_state_parts()[0]
    assert st["tv_entry"] == "drä ma"
    # Under the picture, on another screen, or TV mode off: no text entry.
    w._tv_watching = True
    assert "tv_entry" not in w._phone_state_parts()[0]
    assert w._phone_do("tv_text", {"text": "x"}) == {"ok": False, "tv_mode": True}
    w._tv_watching = False
    w._tv_page.showing = False
    assert "tv_entry" not in w._phone_state_parts()[0]
    w._tv_on = False
    assert w._phone_do("tv_text", {"text": "x"})["ok"] is False
    with pytest.raises(ValueError):
        w._phone_do("tv_text", {"text": "x" * 500})


def test_the_search_screen_takes_the_whole_text_and_searches_as_typing_does():
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.ui.tv_screens import SearchScreen
    changed = []
    scr = SearchScreen.__new__(SearchScreen)
    scr.query = "ab"
    scr._changed = lambda: changed.append(scr.query)
    scr.set_text("abc")
    scr.set_text("abc")                                    # the same again: nothing
    scr.set_text("y" * 99)
    assert changed == ["abc", "y" * 60]


def test_seek_and_typing_over_the_server():
    calls = []

    def call(action, params):
        calls.append((action, params))
        return {"ok": True}
    srv = PhoneServer(0, TOKEN, call, lambda: (b"", ""), host="127.0.0.1")
    srv.start()
    try:
        assert ask(srv.port, "POST", f"/{TOKEN}/api/seek", {"to": 61.5})[0] == 200
        assert ask(srv.port, "POST", f"/{TOKEN}/api/tv_text", {"text": "news"})[0] == 200
        assert ask(srv.port, "GET", f"/{TOKEN}/api/seek")[0] == 405
        assert calls == [("seek", {"to": 61.5}), ("tv_text", {"text": "news"})]
    finally:
        srv.stop()


# -- the page ---------------------------------------------------------------------------------

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


def test_the_page_drags_the_bar_and_sends_one_seek_when_let_go():
    html, csp, script, style = _page()
    assert f"script-src {_sha(script)};" in csp and f"style-src {_sha(style)};" in csp
    assert '"back": 10' in script.replace('"back":10', '"back": 10')
    assert 'id="skipBack"' in html and 'id="skipFwd"' in html and 'id="nowKnob"' in html
    bar = script[script.index("function seekBar()"):script.index("function show(s)")]
    # The finger's place while dragging; the seek only on release, once.
    move = bar[bar.index('"pointermove"'):bar.index('"pointerup"')]
    assert "dragShow(" in move and "api/seek" not in move
    assert 'send("api/seek"' in script and script.count('send("api/seek"') == 1
    end = script[script.index("function seekEnd("):script.index("function seekTo(")]
    assert "seekTo(" in end
    # Mirrored right to left: the finger's fraction from the right edge.
    assert 'r.right - e.clientX' in script and "[dir=rtl] .seekTip{transform:translateX(50%)}" in style
    # Keys of 44 px; the knob placed by the logical start.
    assert "width:44px;height:44px" in style.split(".skip{", 1)[1].split("}", 1)[0]
    assert "height:44px" in style.split(".seekbar.can{", 1)[1].split("}", 1)[0]
    assert "inset-inline-start" in style.split(".knob{", 1)[1].split("}", 1)[0]
    # Only what can seek: never a live channel, never in TV mode.
    can = script[script.index("function canSeek()"):script.index("function playPos()")]
    assert "st.seek" in can and "!tvOn" in can


def test_a_tap_focuses_the_field_in_its_own_handler_and_tv_search_has_one():
    html, _csp, script, _style = _page()
    for field in ('id="q"', 'id="tvText"'):
        tag = re.search(r"<input " + field + r"[^>]*>", html).group(0)
        for attr in ('inputmode="search"', 'enterkeyhint="search"', 'autocomplete="off"',
                     'autocapitalize="off"', 'autocorrect="off"', 'spellcheck="false"'):
            assert attr in tag, (field, attr)
    tap = script[script.index("function focusOnTap("):script.index("function init()")]
    assert "input.focus()" in tap and "await" not in tap and "setTimeout" not in tap
    assert 'focusOnTap($("searchBox"), $("q"))' in script and 'focusOnTap($("tvTextBox"), tvText)' in script
    entry = script[script.index("function tvEntry("):script.index("function tvTextSend(")]
    assert 's.tv_entry' in entry and "document.activeElement !== input" in entry
    assert 'send("api/tv_text"' in script
