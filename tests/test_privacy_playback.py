"""Private viewing, asked of the playback and not of the switch alone: a
title or a channel that began in private viewing stays private to its end,
for everything that keeps or sends something about what plays - though the
switch is turned off while it still plays."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from dopeiptv.core import privacy  # noqa: E402
from dopeiptv.core.log import log  # noqa: E402


class _S(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value

    def remove(self, key):
        self.pop(key, None)


@pytest.fixture(autouse=True)
def _clean():
    privacy.set_private(False)
    privacy.playback_stopped()
    yield
    privacy.set_private(False)
    privacy.playback_stopped()


def _began_private(kind, key, *titles):
    """A playback that began in private viewing, which was then turned off
    while it still plays."""
    privacy.set_private(True)
    privacy.playback_started(kind, key)
    privacy.playback_titles(*titles)
    privacy.set_private(False)
    assert not privacy.private() and privacy.playback_private(kind, key)


# -- core/privacy -------------------------------------------------------------------

def test_what_plays_privately_is_known_without_its_kind_and_by_its_names():
    assert not privacy.playing_private() and not privacy.title_private("The Bridge")
    _began_private("movie", 7, "The Bridge", None, " the bridge (2011) ")
    assert privacy.playing_private()
    assert privacy.title_private("the bridge") and privacy.title_private("The Bridge (2011)")
    assert not privacy.title_private("Another Film") and not privacy.title_private("")
    # The next title chosen is not private, and neither are the old names.
    privacy.playback_started("movie", 8)
    privacy.playback_titles("Another Film")
    assert not privacy.playing_private()
    assert not privacy.title_private("The Bridge") and not privacy.title_private("Another Film")


def test_names_are_dropped_with_the_playback():
    _began_private("movie", 7, "The Bridge")
    privacy.playback_stopped()
    assert not privacy.title_private("The Bridge")


# -- watched marks ---------------------------------------------------------------------

def _marker(last):
    from dopeiptv.ui.main_window import MainWindow
    f = SimpleNamespace()
    f._last_playback = last
    f._playback_max_pct = 97.0
    f._AUTO_WATCHED_PCT = MainWindow._AUTO_WATCHED_PCT
    f.player = None
    f.series_ctx = None
    f.marked = []
    f._mark_movie_watched = lambda item, push_to_trakt: f.marked.append(item.get("name"))
    f._mark_episode_watched = lambda item, push_to_trakt: f.marked.append(item.get("name"))
    f._rec_auto_watched = lambda key: f.marked.append(key)
    f._maybe_auto_mark_watched = MainWindow._maybe_auto_mark_watched.__get__(f)
    return f


def test_a_film_watched_privately_is_not_marked_after_its_stop():
    """Stop ends the private playback; the mark is asked for again as the
    next title starts - with the old title's progress still there."""
    _began_private("movie", 7)
    f = _marker({"kind": "movie", "key": 7, "item": {"name": "The Bridge"}})
    f._maybe_auto_mark_watched()            # Stop
    privacy.playback_stopped()
    assert not privacy.playback_private("movie", 7)
    f._maybe_auto_mark_watched()            # the next title starts
    assert f.marked == []
    # One watched in the open is marked as ever.
    g = _marker({"kind": "movie", "key": 8, "item": {"name": "Other"}})
    g._maybe_auto_mark_watched()
    assert g.marked == ["Other"]


def test_a_recording_watched_privately_gets_no_watched_mark():
    _began_private("recording", "/rec/a.ts")
    f = _marker({"kind": "recording", "key": "/rec/a.ts", "item": {}})
    f._maybe_auto_mark_watched()
    privacy.playback_stopped()
    f._maybe_auto_mark_watched()
    assert f.marked == []


# -- what is filed under a title ----------------------------------------------------------

def test_a_track_picked_in_a_private_playback_is_not_filed_under_the_title():
    from dopeiptv.ui.main_window import MainWindow
    f = SimpleNamespace(settings=_S(), series_ctx=None,
                        _RESUMABLE=MainWindow._RESUMABLE,
                        _TRACK_PREFS_MAX=MainWindow._TRACK_PREFS_MAX)
    for n in ("_track_prefs", "_on_track_selected", "_series_pref_keys"):
        setattr(f, n, getattr(MainWindow, n).__get__(f))
    _began_private("movie", 7)
    f._last_playback = {"kind": "movie", "key": 7}
    f._on_track_selected("sid", 3)
    assert "track_prefs" not in f.settings
    f._last_playback = {"kind": "movie", "key": 8}
    f._on_track_selected("sid", 3)
    assert "movie:8" in json.loads(f.settings["track_prefs"])


def test_a_subtitle_of_a_private_playback_is_not_remembered(tmp_path):
    from dopeiptv.ui.main_window import MainWindow
    from dopeiptv.ui.mw_subtitles import _SubtitlesMixin

    class W(_SubtitlesMixin):
        _RESUMABLE = MainWindow._RESUMABLE
        settings = _S()
    w = W()
    _began_private("movie", 7)
    w._last_playback = {"kind": "movie", "key": 7}
    w._on_subtitle_added(str(tmp_path / "The Bridge.sv.srt"))
    assert dict(w.settings) == {}
    w._last_playback = {"kind": "movie", "key": 8}
    w._on_subtitle_added(str(tmp_path / "other.srt"))
    assert "movie:8" in w._subtitle_memory()


def test_the_audio_delay_of_a_private_playback_is_not_kept():
    from dopeiptv.media.embedded import EmbeddedPlayer
    kept: list = []

    class Prefs:
        def set_delay(self, chan, value):
            kept.append((chan, value))
            return {"adelay": value}

    class Player:
        _remember_delay = EmbeddedPlayer._remember_delay
        _delay_chan = "pl1:7"
        _delay_key = None
        _delay_set = False
        _chan_own: dict = {}
        _settings = _S()

        def _prefs_store(self):
            return Prefs()
    p = Player()
    _began_private("live", 7)
    p._remember_delay(0.25)
    assert kept == [] and p._delay_set
    privacy.playback_started("live", 9)         # another channel, in the open
    p._remember_delay(0.25)
    assert kept == [("pl1:7", 0.25)]


def test_a_teletext_page_of_a_private_playback_is_not_kept():
    from dopeiptv.core import teletext_subs as tsubs
    from dopeiptv.media.teletext_subs import TeletextSubsMixin
    put: list = []

    class Store:
        def put(self, ckey, choice):
            put.append(ckey)

        def forget(self, ckey):
            put.append(("forget", ckey))

    class Player:
        _ttx_play = TeletextSubsMixin._ttx_play
        _ttx_forget = TeletextSubsMixin._ttx_forget
        _chan_key = "pl1:7"
        _ttx_current = None
        _ttx_auto = None
        video = SimpleNamespace(mpv=SimpleNamespace(sid=4))

        def _ttx_set_page(self, choice):
            pass

        def _set_mpv(self, name, value):
            pass

        def _ttx_page_store(self):
            return Store()

        def _ttx_check(self):
            pass
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    choice = tsubs.Choice(track=4, kind=tsubs.TELETEXT, lang="swe", page=0x199)
    p = Player()
    _began_private("live", 7)
    p._ttx_play(choice, remember=True)
    p._ttx_forget()
    assert put == []
    privacy.playback_started("live", 9)
    p._ttx_play(choice, remember=True)
    assert put == ["pl1:7"]


# -- a channel's traces ---------------------------------------------------------------------

def test_stream_health_counts_nothing_of_a_private_channel():
    from dopeiptv.core import health
    from dopeiptv.ui.mw_health import _HealthMixin

    class W(_HealthMixin):
        def __init__(self):
            self.settings = _S()
            self.health = health.StreamHealth(self.settings, on=lambda: True)
            self.playlist_store = SimpleNamespace(active_id="pl1")

        def _health_repaint(self):
            pass
    w = W()
    _began_private("live", 7)
    it = {"stream_id": 7, "name": "Hidden Channel"}
    w._health_begin(7, "Hidden Channel", it, "live", False)
    w._health_frame()
    w._health_drop()
    w._health_retry()
    w._health_begin(7, "Hidden Channel", it, "live", False)    # the app's reconnect
    w._health_failed("no stream")
    assert w.health.entries() == {} and dict(w.settings) == {}
    # The next channel, in the open, is counted.
    privacy.playback_started("live", 9)
    w._health_begin(9, "Open", {"stream_id": 9, "name": "Open"}, "live", False)
    assert list(w.health.entries()) == ["pl1:9"]


def test_stream_health_of_a_channel_made_private_while_it_plays():
    """Private viewing turned on mid-channel and off again: what follows
    of that channel is not counted (core/health asks the playback)."""
    from dopeiptv.core import health
    h = health.StreamHealth(_S(), on=lambda: True)
    privacy.playback_started("live", 7)
    assert h.begin("pl1:7", "Channel")
    privacy.set_private(True)
    privacy.playback_started("live", 7)
    privacy.set_private(False)
    assert not h.active()
    h.frame()
    assert not h.drop() and not h.fail("gone")
    assert h.entry("pl1:7").get("drops") is None and h.entry("pl1:7").get("fails") is None


def test_stream_details_of_a_private_channel_are_not_remembered():
    from dopeiptv.ui.mw_stream_info import _StreamInfoMixin

    class W(_StreamInfoMixin):
        settings = _S(show_stream_info="true")
        listw = SimpleNamespace(viewport=lambda: SimpleNamespace(update=lambda: None))
    w = W()
    _began_private("live", 7)
    info = {"width": 1920, "height": 1080, "fps": 50, "codec": "h264"}
    w._remember_stream_info("pl1:7", info)
    assert w._stream_info_store().data == {}
    privacy.playback_started("live", 9)
    w._remember_stream_info("pl1:9", info)
    assert list(w._stream_info_store().data) == ["pl1:9"]


# -- what is sent elsewhere ----------------------------------------------------------------------

def test_radio_browser_gets_no_click_for_a_station_played_privately(monkeypatch):
    from dopeiptv.providers import radio_browser as rb
    from dopeiptv.ui import mw_radio
    clicks: list = []
    monkeypatch.setattr(mw_radio, "run_async", lambda pool, job, *a: clicks.append(job.args[0]))

    class W(mw_radio._RadioMixin):
        settings = _S({rb.SETTING_CLICKS: "true"})
        mode = "live"
        pool = None

        def _start_playback(self, url, title, icon, key, kind, **kw):
            privacy.playback_started(kind, key)

        def _radio_icy_start(self):
            pass

        def _radio(self):
            return SimpleNamespace(click=lambda uuid: None)
    w = W()
    station = {"url": "http://radio.example/s", "name": "Hidden FM", "stationuuid": "abc"}
    privacy.set_private(True)
    w._radio_play(station)
    privacy.set_private(False)
    w._radio_play(station)                  # played again: the same playback
    assert clicks == []
    w._radio_play({"url": "http://radio.example/o", "name": "Open FM", "stationuuid": "def"})
    assert clicks == ["def"]


def test_trakt_is_not_told_when_private_viewing_began_during_its_lookup():
    from dopeiptv.ui import mw_trakt
    sent: list = []
    jobs: list = []

    class Trakt:
        def is_connected(self):
            return True

        def find_movie(self, title):
            return {"ids": {"trakt": 1}}

        def scrobble_start(self, payload, progress):
            sent.append(payload)

    class W(mw_trakt._TraktMixin):
        trakt = Trakt()
        simkl = None
        pool = None
        series_ctx = None
        _trakt_active = None

        def _simkl_start_for_item(self, kind, item):
            pass
    orig = mw_trakt.run_async
    mw_trakt.run_async = lambda pool, job, done, fail: jobs.append((job, done))
    try:
        w = W()
        privacy.playback_started("movie", 7)
        w._watch_accounts_start("movie", 7, {"name": "The Bridge"})
        # Private viewing turned on before Trakt answered the lookup.
        privacy.set_private(True)
        privacy.playback_started("movie", 7)
        privacy.set_private(False)
        job, done = jobs.pop()
        done(job())
        assert sent == [] and w._trakt_active is None
        # In the open the start is sent as ever.
        privacy.playback_started("movie", 8)
        w._watch_accounts_start("movie", 8, {"name": "Other"})
        job, done = jobs.pop()
        done(job())
        assert len(sent) == 1 and w._trakt_active is not None
    finally:
        mw_trakt.run_async = orig


def test_a_title_cast_again_after_private_viewing_is_asked_of_its_playback():
    """The cast's start goes through the same question as a start here."""
    src = Path(__file__).resolve().parent.parent.joinpath(
        "dopeiptv", "ui", "mw_cast.py").read_text(encoding="utf-8")
    body = src.split("def _cast_title(", 1)[1].split("\n    def ", 1)[0]
    assert "self._watch_accounts_start(kind, key, item)" in body
    assert "_trakt_start_for_item" not in body and "_simkl_start_for_item" not in body


def test_tmdb_is_not_asked_about_what_plays_privately(tmp_path):
    from unittest.mock import MagicMock

    from PyQt6.QtCore import QSettings, QThreadPool

    from dopeiptv.providers import metadata
    asked: list = []
    orig = metadata.run_async
    metadata.run_async = lambda pool, fetch, done, fail: asked.append(fetch)
    try:
        s = QSettings(str(tmp_path / "c.ini"), QSettings.Format.IniFormat)
        r = metadata.PosterResolver(QThreadPool(), s, MagicMock())
        r._cache["series:known show"] = {"tmdb_id": 5, "poster_url": "https://x/p.jpg"}
        _began_private("episode", 101, "S1 E1", "Hidden Show", "Known Show")
        assert r.get_full("Hidden Show", "series", lambda d: None) == {}
        assert r.get("Hidden Show", "series", lambda u: None) is None
        assert r.is_resolved("Hidden Show", "series")       # the row does not wait for it
        assert not r.fill_backdrop("Known Show", "series", lambda d: None)
        assert asked == []
        # What it answered before is still there, and the lists around it fetch.
        assert r.get_full("Known Show", "series", lambda d: None)["tmdb_id"] == 5
        assert r.get_full("Another Show", "series", lambda d: None) is None
        assert len(asked) == 1
        # Once that playback is over it is asked as any title.
        privacy.playback_started("movie", 8)
        assert r.get_full("Hidden Show", "series", lambda d: None) is None
        assert len(asked) == 2
    finally:
        metadata.run_async = orig


def test_the_series_page_keeps_no_season_for_a_series_played_privately():
    from dopeiptv.ui.mw_series_page import _SeriesPageMixin
    f = SimpleNamespace(_sp_ctx={"series_id": 7})
    f._sp_private = _SeriesPageMixin._sp_private.__get__(f)
    _began_private("episode", 101)
    f._last_playback = {"kind": "episode", "key": 101, "series_ctx": {"series_id": 7}}
    assert f._sp_private()
    f._sp_ctx = {"series_id": 8}            # another series' page, browsed in the open
    assert not f._sp_private()
    privacy.playback_started("episode", 102)
    f._sp_ctx = {"series_id": 7}
    f._last_playback = {"kind": "episode", "key": 102, "series_ctx": {"series_id": 7}}
    assert not f._sp_private()


def test_the_system_is_not_told_the_title_that_keeps_the_screen_awake():
    from dopeiptv.ui.mw_privacy import note_titles, wake_reason
    assert "The Bridge" in wake_reason("The Bridge")
    privacy.set_private(True)
    privacy.playback_started("movie", 7)
    note_titles({"title": "The Bridge", "item": {"name": "EN| The Bridge", "_clean_title": "Bridge"},
                 "series_ctx": {"name": "Hidden Show"}})
    privacy.set_private(False)
    assert "The Bridge" not in wake_reason("The Bridge")
    for name in ("The Bridge", "EN| The Bridge", "Bridge", "Hidden Show"):
        assert privacy.title_private(name)


# -- the log -----------------------------------------------------------------------------------

def test_a_subtitle_files_name_stays_out_of_the_log(tmp_path):
    from dopeiptv.media.tracks import _subtitle_to_load
    from dopeiptv.ui.main_window import MainWindow
    from dopeiptv.ui.mw_subtitles import _SubtitlesMixin
    seen: list[str] = []

    class Catch(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())
    h = Catch()
    level = log.level
    log.setLevel(logging.DEBUG)
    log.addHandler(h)
    try:
        sub = tmp_path / "The Bridge.ar.srt"
        sub.write_text("1\n00:00:01,000 --> 00:00:02,000\n‫مرحبا‬\n", encoding="utf-8")

        class W(_SubtitlesMixin):
            _RESUMABLE = MainWindow._RESUMABLE
            settings = _S()
            _last_playback = {"kind": "movie", "key": 8}
        _began_private("movie", 7)
        _subtitle_to_load(str(sub))
        privacy.playback_started("movie", 8)
        privacy.set_private(True)           # the log asks the switch too
        W()._on_subtitle_added(str(sub))
    finally:
        log.removeHandler(h)
        log.setLevel(level)
    assert not [m for m in seen if "The Bridge" in m], seen


def test_a_private_streams_address_says_nothing_in_the_log():
    """Not even the server's name: it says whose service it is."""
    url = "http://tv.example:8080/live/anna/hemligt/4711.ts"
    assert privacy.url_for_log(url) == "http://tv.example:8080/live/***/***/4711.ts"
    assert privacy.url_for_log("/home/anna/Films/The Bridge.mkv").endswith("The Bridge.mkv")
    _began_private("live", 4711)
    assert privacy.url_for_log(url) == "<private>"
    assert privacy.url_for_log("/home/anna/Films/The Bridge.mkv") == "<private>"
    assert privacy.url_for_log(None) == "<private>"
    assert privacy.for_log("The Bridge") == "<private>"


def test_the_subtitle_language_of_a_private_playback_is_not_kept():
    from dopeiptv.media.embedded import EmbeddedPlayer
    m = SimpleNamespace(track_list=[{"type": "sub", "id": 2, "lang": "swe"}])
    p = SimpleNamespace(_settings=_S(volume=80))
    p._remember_sub_lang = EmbeddedPlayer._remember_sub_lang.__get__(p)
    _began_private("movie", 7)
    p._remember_sub_lang(m, 2)
    assert "sub_last_lang" not in p._settings
    privacy.playback_started("movie", 8)
    p._remember_sub_lang(m, 2)
    assert p._settings["sub_last_lang"] == "swe"


def test_events_say_what_happened_not_what_to_the_end_of_a_private_playback():
    """Webhooks, MQTT and Home Assistant's retained state: while a playback
    is private they say that something plays, paused or stopped - never its
    title, channel, programme, series or source - and that holds after
    private viewing is turned off while it still plays."""
    from dopeiptv.core import events
    from dopeiptv.ui.mw_events import _EventsMixin
    sent: list = []

    class Dispatcher:
        active = True

        def emit(self, event, fields=None, private=False, state=None):
            sent.append(events.payload(event, fields, private))
            if state is not None:
                self.set_state(*state, private=private)

        def set_state(self, state, fields=None, private=False, now=None):
            sent.append(events.state_payload(state, fields, private))

    class W(_EventsMixin):
        _events = Dispatcher()
        _events_favs = {"Hidden Channel": {"stream_id": 7}}
        playlist_store = SimpleNamespace(active=lambda: {"name": "My provider"})
        xmltv = SimpleNamespace(current_programme=lambda it: {"title": "Secret Show"})
        _last_playback = {"kind": "movie"}
    w = W()
    it = {"stream_id": 7, "name": "Hidden Channel"}
    privacy.set_private(True)
    privacy.playback_started("live", 7)
    w._events_started(7, "Hidden Channel", it, "live", False)
    privacy.set_private(False)                  # mid-playback
    w._events_private_changed(False)
    w._events_started(7, "Hidden Channel", it, "live", False)    # a reconnect
    w._events_paused(True)
    w._events_paused(False)
    w._events_publish_state()
    # The next channel, in the open: never the private one's name as "previous".
    privacy.playback_started("live", 9)
    w._events_started(9, "Open Channel", {"stream_id": 9, "name": "Open Channel"}, "live", False)
    text = json.dumps(sent)
    assert "Hidden Channel" not in text and "My provider" in text
    before = [m for m in sent if m.get("channel") != "Open Channel"]
    assert before and all(m.get("private") is True for m in before)
    assert not [m for m in before if "Secret Show" in json.dumps(m) or "source" in m]
    assert {m.get("event") for m in before} >= {"playback_started", "playback_paused",
                                                "playback_resumed"}
    w._events_stopped()


# -- the system's media controls, the remote API, a recording's events -------------------

def test_the_systems_media_controls_are_not_told_what_plays_privately():
    from dopeiptv.services.media_session import clean_snapshot
    from dopeiptv.ui.mw_mpris import _MprisMixin, _SessionHost
    snap = {"status": "Playing", "track": "movie:7", "title": "The Bridge",
            "artist": "Hidden Show", "album": "A", "art": "file:///p.jpg",
            "length": 100.0, "position": 5.0, "can_seek": True, "can_next": True,
            "can_prev": False, "volume": 0.5, "live": False, "audio": False}

    class W(_MprisMixin):
        def mpris_snapshot(self):
            return dict(snap)

        def mpris_position(self):
            return 5.0
    w = W()
    host = _SessionHost(w)
    assert host.mpris_snapshot() == snap and host.mpris_position() == 5.0
    _began_private("movie", 7)              # the switch is off again
    told = clean_snapshot(host.mpris_snapshot())
    assert (told["title"], told["artist"], told["album"], told["art"]) == ("dopeIPTV", "", "", "")
    assert "7" not in told["track"] and "Bridge" not in json.dumps(told)
    # The controls, the place and the length stay as they are.
    for key in ("status", "length", "position", "can_seek", "can_next", "can_prev", "volume"):
        assert told[key] == snap[key], key
    # The window's own snapshot (the phone remote's) is as before.
    assert w.mpris_snapshot() == snap
    privacy.playback_started("movie", 8)
    assert host.mpris_snapshot() == snap


def test_a_private_cast_is_not_named_to_the_systems_media_controls():
    from dopeiptv.ui.mw_mpris import _MprisMixin, _SessionHost

    class W(_MprisMixin):
        player = None
        xmltv = SimpleNamespace(current_programme=lambda it: {"title": "Secret Show"})

        def _media_cast(self):
            return {"kind": "live", "key": 7, "item": {"stream_id": 7}, "title": "Hidden Channel",
                    "icon_url": "", "paused": False, "length": 0.0, "position": 0.0,
                    "volume": 0.4, "device": "Living room TV", "can_pause": True,
                    "has_volume": True, "muted": False}

        def _media_art(self, url):
            return "file:///logo.png"
    host = _SessionHost(W())
    assert host.mpris_snapshot()["title"] == "Hidden Channel"
    _began_private("live", 7)
    told = host.mpris_snapshot()
    assert told["status"] == "Playing" and told["cast"] == "Living room TV" and told["can_next"]
    assert (told["title"], told["artist"], told["art"], told["track"]) == ("dopeIPTV", "", "", "private")


def test_the_remote_api_says_that_something_plays_privately_not_what():
    from dopeiptv.ui.mw_remote import _RemoteMixin

    class W(_RemoteMixin):
        _last_playback = {"kind": "live", "key": 7}
        playlist_store = None

        def mpris_snapshot(self):
            return {"status": "Playing", "title": "Hidden Channel", "artist": "Secret Show",
                    "volume": 0.5, "position": 12.0, "length": 0}
    w = W()
    _began_private("live", 7)
    out = w._remote_status()
    assert out["state"] == "playing" and out["private"] is True
    assert out["title"] == "" and "programme" not in out and out["volume"] == 50
    # Nor how far or how long (a film's length names it), the source, the
    # TV or who is watching.
    assert out["position"] == 0.0 and out["duration"] == 0.0
    assert not {"source", "cast", "profile"} & set(out)
    assert "Hidden" not in json.dumps(out) and "Secret" not in json.dumps(out)
    privacy.playback_started("live", 9)
    out = w._remote_status()
    assert out["title"] == "Hidden Channel" and out["programme"] == "Secret Show"
    assert "private" not in out


def test_a_recording_begun_privately_is_not_named_when_it_ends_after_the_switch():
    from dopeiptv.core import events
    from dopeiptv.ui.mw_events import _EventsMixin
    sent: list = []

    class Dispatcher:
        active = True

        def emit(self, event, fields=None, private=False, state=None):
            sent.append(events.payload(event, fields, private))

    job = {"id": "j1", "title": "Secret Show", "channel": "Hidden Channel", "status": "scheduled",
           "path": "/rec/Secret Show.ts"}
    other = {"id": "j2", "title": "News", "channel": "Open Channel", "status": "scheduled",
             "path": "/rec/News.ts"}

    class Watch(events.RecordingWatch):
        SETTLE_S = 0.0

    class W(_EventsMixin):
        _events = Dispatcher()
        rec = SimpleNamespace(jobs=[job, other])
    w = W()
    w._events_rec = Watch(w.rec.jobs)
    privacy.set_private(True)
    privacy.playback_started("live", 7)
    job["status"] = "recording"
    w._events_jobs()
    privacy.set_private(False)              # the switch off; the channel still plays
    privacy.playback_started("live", 9)     # ...and then another, in the open
    other["status"] = "recording"
    w._events_jobs()
    job["status"] = other["status"] = "done"
    w._events_jobs()
    w._events_jobs()
    by = {(m["event"], m.get("title", "")): m for m in sent}
    assert ("recording_started", "News") in by and ("recording_finished", "News") in by
    assert by[("recording_finished", "News")]["file"] == "News.ts"
    hidden = [m for m in sent if m.get("private")]
    assert [m["event"] for m in hidden] == ["recording_started", "recording_finished"]
    assert all("title" not in m and "channel" not in m and "file" not in m for m in hidden)
    assert "Secret" not in json.dumps(sent) and "Hidden" not in json.dumps(sent)
    assert not [m for m in sent if m.get("title") == "News" and m.get("private")]


def test_a_failed_private_recording_is_not_named_either():
    from dopeiptv.core import events
    job = {"id": "j1", "title": "Secret Show", "channel": "Hidden Channel", "status": "scheduled"}
    w = events.RecordingWatch([job])
    job["status"] = "recording"
    assert w.update([job], now=1, private=True) == [
        ("recording_started", {"title": "Secret Show", "channel": "Hidden Channel", "private": True})]
    job.update(status="failed", error="no stream")
    (event, fields), = w.update([job], now=2)
    assert event == "recording_failed" and fields["private"] is True
    m = events.payload(event, {k: v for k, v in fields.items() if k != "private"}, True)
    assert m["private"] is True and "title" not in m and "error" not in m
