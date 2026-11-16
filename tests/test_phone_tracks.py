"""Subtitles, sound and the sleep timer from the phone remote: the sheet is
the player's own menu entries (named as it names them - forced, external -
the one on marked), read only when it is asked for, and a pick triggers
the entry itself (the player's path: per-title memory, teletext pages);
the sleep timer is the player's own, set to TV mode's steps; the state
carries only a small, steady stamp and the moment the timer stops."""
from __future__ import annotations

import re
import time
from types import SimpleNamespace

import pytest

from dopeiptv.core import phone_page


class _Mpv:
    def __init__(self, tracks):
        self.track_list = tracks
        self.reads = 0

    def __getitem__(self, key):
        self.reads += 1
        return {"sub-visibility": True}[key]


_APP: list = []      # the application, kept while the tests run

TRACKS = [{"type": "video", "id": 1},
          {"type": "audio", "id": 1, "lang": "swe", "title": "Dolby Digital 5.1", "selected": True},
          {"type": "audio", "id": 2, "lang": "eng"},
          {"type": "sub", "id": 1, "lang": "fin", "forced": True},
          {"type": "sub", "id": 2, "lang": "eng", "selected": True},
          {"type": "sub", "id": 3, "title": "Commentary", "external": True}]


def _player(picks):
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.media.embedded import EmbeddedPlayer

    class Player:
        """The player's own track entries and sleep timer, on a stand-in
        (a QWidget is not made for this)."""
        _tracks_of = EmbeddedPlayer._tracks_of
        track_label = staticmethod(EmbeddedPlayer.track_label)
        fill_audio_tracks = EmbeddedPlayer.fill_audio_tracks
        fill_sub_tracks = EmbeddedPlayer.fill_sub_tracks
        sleep_left = EmbeddedPlayer.sleep_left
        _sleep_timer = SimpleNamespace(isActive=lambda: False)
        _sleep_deadline = 0.0
    p = Player()
    p.video = SimpleNamespace(mpv=_Mpv([dict(t) for t in TRACKS]))
    p._pick_track = lambda prop, tid: picks.append((prop, tid))
    p._ttx_is_track = lambda t: False
    p.current_url = "http://x/movie/1.mkv"
    return p


def test_the_players_menu_entries_name_forced_and_external_tracks():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication, QMenu
    _APP.append(QApplication.instance() or QApplication([]))
    picks = []
    p = _player(picks)
    audio, subs = QMenu(), QMenu()
    p.fill_audio_tracks(audio)
    p.fill_sub_tracks(subs)
    assert [(a.text(), a.isChecked()) for a in audio.actions()] == [
        ("swe Dolby Digital 5.1", True), ("eng", False)]
    assert [(a.text(), a.isChecked()) for a in subs.actions()] == [
        ("Off", False), ("fin (forced)", False), ("eng", True), ("Commentary (external)", False)]
    subs.actions()[0].trigger()
    audio.actions()[1].trigger()
    assert picks == [("sid", "no"), ("aid", 2)]


def _window(picks, playing=True):
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication
    _APP.append(QApplication.instance() or QApplication([]))
    from dopeiptv.ui.mw_phone import _PhoneMixin
    from dopeiptv.ui.mw_tv import _TvMixin

    p = _player(picks)
    p.sleeps = []
    p._start_sleep_timer = lambda minutes: p.sleeps.append(minutes)
    if not playing:
        p.current_url = None

    class W(_PhoneMixin, _TvMixin):
        def __init__(self):
            self.player = p

        def _remote_status(self):
            return {"state": "playing" if self.player.current_url else "stopped", "volume": 50,
                    "kind": "movie", "title": "Sintel", "position": 30.0, "duration": 900.0}

        def _phone_stamp(self):
            return "s"

        def _phone_sections(self):
            return []
    return W()


def test_the_sheet_is_read_when_asked_and_a_pick_is_the_players_own():
    picks = []
    w = _window(picks)
    sheet = w._phone_do("tracks", {})
    assert sheet["audio"] == [{"label": "swe Dolby Digital 5.1", "on": True, "ok": True},
                              {"label": "eng", "on": False, "ok": True}]
    assert [e["label"] for e in sheet["subs"]] == ["Off", "fin (forced)", "eng", "Commentary (external)"]
    assert [e["on"] for e in sheet["subs"]] == [False, False, True, False]
    stamp = sheet["stamp"]
    assert w._phone_do("track", {"stamp": stamp, "kind": "subs", "i": 3}) == {"ok": True}
    assert w._phone_do("track", {"stamp": stamp, "kind": "audio", "i": 1}) == {"ok": True}
    assert picks == [("sid", 3), ("aid", 2)]
    # A sheet read again since (or none): the pick is refused, nothing picked.
    w._phone_do("tracks", {})
    assert w._phone_do("track", {"stamp": stamp, "kind": "subs", "i": 0}) == {"ok": False, "stale": True}
    stamp2 = w._phone_sheet[0]
    for bad in ({"stamp": stamp2, "kind": "subs", "i": 9}, {"stamp": stamp2, "kind": "subs", "i": "x"}):
        with pytest.raises(ValueError):
            w._phone_do("track", bad)
    assert w._phone_do("track", {"stamp": stamp2, "kind": "video", "i": 0})["ok"] is False
    assert picks == [("sid", 3), ("aid", 2)]
    # Nothing playing: an empty sheet.
    assert _window([], playing=False)._phone_do("tracks", {}) == {"stamp": "", "audio": [], "subs": []}


def test_the_state_carries_a_steady_stamp_and_never_reads_the_tracks():
    picks = []
    w = _window(picks)
    mpv = w.player.video.mpv
    first, _t = w._phone_state_parts()
    again, _t = w._phone_state_parts()
    assert first["tracks"] == again["tracks"] and mpv.reads == 0
    w._track_ver = 1                                       # a track picked (_on_track_selected)
    assert w._phone_state_parts()[0]["tracks"] != first["tracks"]
    assert "sleep_at" not in first


def test_the_sleep_timer_is_the_players_own_and_its_end_is_steady_in_the_state():
    w = _window([])
    assert w._phone_do("sleep", {"minutes": 45}) == {"ok": True}
    assert w._phone_do("sleep", {"minutes": 0}) == {"ok": True}
    assert w.player.sleeps == [45, 0]
    for bad in ({"minutes": 7}, {"minutes": "soon"}, {}):
        with pytest.raises(ValueError):
            w._phone_do("sleep", bad)
    # Running: the moment it stops, the same in every poll.
    w.player._sleep_timer = SimpleNamespace(isActive=lambda: True)
    w.player._sleep_deadline = time.monotonic() + 600
    a = w._phone_state_parts()[0]["sleep_at"]
    time.sleep(0.05)
    b = w._phone_state_parts()[0]["sleep_at"]
    assert a == b and abs(a - (time.time() + 600)) < 2
    # Nothing playing: no timer to set.
    assert _window([], playing=False)._phone_do("sleep", {"minutes": 30}) == {"ok": False}


def test_the_page_offers_the_sheets_on_now_playing_and_in_tv_modes_remote():
    words = {k: k.replace("_", " ").title() for k in phone_page.WORDS}
    html, csp = phone_page.render({"bg": "#17171C"}, {"bg": "#F5F5F7"}, words,
                                  sleep_steps=(15, 30, 45, 60, 90, 120))
    html = html.decode()
    script = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    assert 'id="tracksBtn"' in html and 'id="sleepBtn"' in html and 'id="tvActs"' in html
    assert '"sleep": [15, 30, 45, 60, 90, 120]' in script.replace('"sleep":[15,30,45,60,90,120]',
                                                                 '"sleep": [15, 30, 45, 60, 90, 120]')
    # The tracks asked for only when the sheet opens, and read again when the stamp says so.
    assert script.count('api("api/tracks")') == 1
    load = script[script.index("function actsOffer("):script.index("function initSheets(")]
    assert "s.tracks !== sheet.seen" in load
    # Back closes a sheet before anything else.
    back = script[script.index("function goneBack()"):]
    assert back.index("if (sheet)") < back.index("if (eps.open)")
