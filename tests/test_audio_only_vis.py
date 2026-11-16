"""An audio-only stream from a source (an iHeart channel in an M3U) gets the
visualiser once mpv lists its tracks; a stream with video never gets a
filter graph written. No libmpv: fake track lists and a fake core."""
from __future__ import annotations

import pytest

from dopeiptv.core import stream_info

AUDIO = {"type": "audio", "id": 1, "selected": True, "codec": "aac"}
VIDEO = {"type": "video", "id": 1, "selected": True, "codec": "h264"}
ART = {"type": "video", "id": 1, "albumart": True, "image": True, "codec": "png"}
STILL = {"type": "video", "id": 2, "albumart": False, "image": True, "codec": "mjpeg"}


@pytest.mark.parametrize("tracks, expected", [
    ([AUDIO], True),
    ([AUDIO, ART], True),                      # cover art is no picture
    ([AUDIO, STILL], True),
    ([VIDEO, AUDIO], False),
    ([AUDIO, ART, VIDEO], False),
    ([VIDEO], False),
    ([], False),
    ([{"type": "sub", "id": 1}], False),
    ([AUDIO, {"type": "video", "id": 1, "albumart": "yes"}], False),
    (None, False),
    ("tracks", False),
    ([None, AUDIO], True),
])
def test_audio_only_reads_the_track_list(tracks, expected):
    assert stream_info.audio_only(tracks) is expected


class _Settings:
    def __init__(self, **values):
        self.values = {"vis_on": "true", "vis_style": "bars", **values}

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


class _Player:
    def __init__(self):
        self.calls: list[tuple] = []
        self.playing: list[bool] = []
        self.only = False

    def set_visualiser(self, on, style="bars", *, playing=False):
        self.calls.append(("vis", on, style if on else ""))
        self.playing.append(playing)

    def set_equaliser(self, *a, **k):
        self.calls.append(("eq",))

    def audio_only(self):
        return self.only


def _window(**settings):
    from dopeiptv.ui.main_window import MainWindow

    class _W:
        AUDIO_EXTS = MainWindow.AUDIO_EXTS
        _apply_audio_visuals = MainWindow._apply_audio_visuals
        _eq_settings = MainWindow._eq_settings
        _vis_choose = MainWindow._vis_choose
        _on_audio_only = MainWindow._on_audio_only
        _playing_audio_only = MainWindow._playing_audio_only

    w = _W()
    w.settings = _Settings(**settings)
    w.player = _Player()
    w._last_playback = {}
    return w


def _play(w, url, kind="live", audio_only=None):
    """_start_playback's order: the visuals before play(), then what mpv
    says once it has listed the tracks."""
    w._last_playback = {"url": url, "kind": kind}
    w._apply_audio_visuals(url, audio=kind == "radio")
    w.player.only = bool(audio_only)
    if audio_only is not None:
        w._on_audio_only(audio_only)


def test_an_audio_only_channel_gets_the_users_visualiser():
    w = _window(vis_style="scope")
    _play(w, "http://host/live/u/p/1001", audio_only=True)
    assert w.player.calls == [("vis", True, "scope")]
    # Put up on the stream already playing (its own initial sync).
    assert w.player.playing == [True]
    assert w._vis_active is True


def test_a_channel_with_video_is_never_written_to():
    w = _window()
    for n in range(3):
        _play(w, f"http://host/live/u/p/{n}")      # mpv never says audio-only
    _play(w, "http://host/movie/u/p/9.mkv", kind="movie")
    assert w.player.calls == []


def test_the_next_channel_takes_it_down_as_after_music():
    w = _window()
    _play(w, "http://host/hls/listen.m3u8", audio_only=True)
    w.player.calls.clear()
    _play(w, "http://host/live/u/p/2")                # video
    assert w.player.calls == [("vis", False, "")]
    w.player.calls.clear()
    _play(w, "http://host/live/u/p/3")
    assert w.player.calls == []


class _Core(dict):
    """What set_visualiser writes, in order: options (python-mpv's m[...])
    and the playing file's own (m.file_local[...])."""
    def __init__(self):
        super().__init__()
        self.writes: list[tuple] = []
        core = self

        class _FileLocal:
            def __setitem__(self, key, value):
                core.writes.append((f"file-local-options/{key}", value))

        self.file_local = _FileLocal()

    def __setitem__(self, key, value):
        self.writes.append((key, value))
        super().__setitem__(key, value)


def _real_set_visualiser(on, style, playing, before=(False, "")):
    from dopeiptv.media.embedded import EmbeddedPlayer

    class _Video:
        mpv = _Core()

    class _P:
        video = _Video()
        _vis_now = before
        _vis_filter = EmbeddedPlayer._vis_filter
        set_visualiser = EmbeddedPlayer.set_visualiser

    p = _P()
    p.set_visualiser(on, style, playing=playing)
    return p.video.mpv.writes


def test_put_up_while_playing_starts_the_sound_where_it_is_for_that_file():
    writes = _real_set_visualiser(True, "bars", playing=True)
    assert writes[0] == ("file-local-options/initial-audio-sync", False)
    assert writes[1][0] == "lavfi-complex" and "showcqt" in writes[1][1]
    # Before a load (music, a radio station) only the graph, as it was.
    writes = _real_set_visualiser(True, "bars", playing=False)
    assert [k for k, _ in writes] == ["lavfi-complex"]
    # Taking it down touches nothing else; unchanged writes nothing.
    assert _real_set_visualiser(False, "bars", True, before=(True, "bars")) == [
        ("lavfi-complex", "")]
    assert _real_set_visualiser(True, "wave", True, before=(True, "wave")) == []


def test_turned_off_means_nothing_written():
    w = _window(vis_on="false")
    _play(w, "http://host/live/u/p/1001", audio_only=True)
    assert w.player.calls == []
    assert not getattr(w, "_vis_active", False)


def test_music_is_not_put_up_twice():
    w = _window()
    _play(w, "/music/01.flac", kind="local", audio_only=True)
    assert w.player.calls == [("vis", True, "bars"), ("eq",)]


def test_a_picture_after_all_takes_it_down():
    w = _window()
    _play(w, "http://host/live/u/p/1001", audio_only=True)
    w.player.calls.clear()
    w._on_audio_only(False)
    assert w.player.calls == [("vis", False, "")]
    assert w._vis_active is False
    # Music's visualiser is not this one's to take down.
    w2 = _window()
    _play(w2, "/music/01.flac", kind="local")
    w2.player.calls.clear()
    w2._on_audio_only(False)
    assert w2.player.calls == []


def test_a_style_picked_from_the_menu_applies_to_an_audio_only_stream():
    w = _window()
    _play(w, "http://host/live/u/p/1001", audio_only=True)
    w.player.calls.clear()
    w._vis_choose(True, "freqs")
    assert w.player.calls == [("vis", True, "freqs")]
    assert w.settings.values["vis_style"] == "freqs"
    w._vis_choose(False, "freqs")
    assert w.player.calls[-1] == ("vis", False, "")
    assert w._vis_active is False


def test_a_style_picked_while_video_plays_is_only_remembered():
    w = _window()
    _play(w, "http://host/live/u/p/2")
    w._vis_choose(True, "wave")
    assert w.player.calls == []
    assert w.settings.values["vis_style"] == "wave"


def test_turned_on_from_the_menu_during_music_comes_down_for_video():
    w = _window(vis_on="false")
    _play(w, "/music/01.flac", kind="local")
    w._vis_choose(True, "bars")
    w.player.calls.clear()
    _play(w, "http://host/live/u/p/2")
    assert w.player.calls == [("vis", False, "")]


# -- the player's side: what mpv says, for the file play() opened only --------

def _player_side():
    from PyQt6.QtCore import QObject, pyqtSignal

    from dopeiptv.media.audio_only import AudioOnlyMixin

    class _Core:
        def __init__(self):
            self.path = ""
            self.track_list = []
            self.events = {}
            self.observed = {}

        def event_callback(self, name):
            def deco(fn):
                self.events.setdefault(name, []).append(fn)
                return fn
            return deco

        def observe_property(self, name, fn):
            self.observed.setdefault(name, []).append(fn)

    class _Video:
        mpv = None

    class _P(AudioOnlyMixin, QObject):
        _ao_event = pyqtSignal(str, str)
        audio_only_changed = pyqtSignal(bool)

        def __init__(self):
            super().__init__()
            self.video = _Video()
            self.current_url = None
            self._ao_init()

    p = _P()
    said: list[bool] = []
    p.audio_only_changed.connect(said.append)
    core = _Core()
    p.video.mpv = core
    return p, core, said


def _open(p, core, path, tracks):
    p._ao_arm(core, path)
    p.current_url = path
    core.path, core.track_list = path, tracks
    p._ao_on_event("loaded", path)


def test_the_player_says_sound_alone_once():
    p, core, said = _player_side()
    _open(p, core, "http://host/live/1001", [AUDIO])
    assert said == [True] and p.audio_only() is True
    p._ao_on_event("tracks", "")
    assert said == [True]
    # Hooked once per core however many streams it opens.
    _open(p, core, "http://host/live/1002", [VIDEO, AUDIO])
    assert len(core.events["file-loaded"]) == 1
    assert len(core.observed["track-list"]) == 1


def test_the_player_says_nothing_for_video():
    p, core, said = _player_side()
    _open(p, core, "http://host/live/1002", [VIDEO, AUDIO])
    p._ao_on_event("tracks", "")
    assert said == [] and p.audio_only() is False


def test_an_earlier_streams_late_event_is_ignored():
    p, core, said = _player_side()
    p._ao_arm(core, "http://host/live/new")
    p.current_url = "http://host/live/new"
    core.path, core.track_list = "http://host/live/old", [AUDIO]
    p._ao_on_event("loaded", "http://host/live/old")
    p._ao_on_event("tracks", "")                 # before its own file-loaded
    assert said == []


def test_between_files_the_answer_stands():
    """The rewind buffer opened again on the same stream: its track list
    is empty for a moment - no sound and no picture is no verdict (it took
    the visualiser down in the real player)."""
    p, core, said = _player_side()
    _open(p, core, "http://127.0.0.1:9/dvr/live", [AUDIO])
    core.track_list = []
    p._ao_on_event("tracks", "")
    core.track_list = [{"type": "sub", "id": 1}]
    p._ao_on_event("tracks", "")
    assert said == [True] and p.audio_only() is True
    # The reopened file (another address) is not this one's to judge.
    core.path, core.track_list = "http://127.0.0.1:9/dvr/seg/12", [AUDIO]
    p._ao_on_event("loaded", core.path)
    assert said == [True]


def test_has_picture():
    assert stream_info.has_picture([VIDEO, AUDIO])
    assert not stream_info.has_picture([AUDIO, ART, STILL])
    assert not stream_info.has_picture([])
    assert not stream_info.has_picture(None)


def test_a_picture_appearing_later_is_said():
    p, core, said = _player_side()
    _open(p, core, "http://host/live/1001", [AUDIO])
    core.track_list = [AUDIO, VIDEO]
    p._ao_on_event("tracks", "")
    assert said == [True, False]


def test_stopped_is_not_audio_only():
    p, core, said = _player_side()
    _open(p, core, "http://host/live/1001", [AUDIO])
    p._ao_reset()
    p.current_url = None
    assert p.audio_only() is False


# -- the styles ----------------------------------------------------------------

def test_every_style_has_a_graph_and_a_name():
    from dopeiptv.i18n import _STRINGS
    from dopeiptv.media.embedded import EmbeddedPlayer

    graphs = {s: EmbeddedPlayer._vis_filter(None, s) for s in EmbeddedPlayer.VIS_STYLES}
    assert {"scope", "freqs"} <= set(graphs)
    assert len(set(graphs.values())) == len(graphs)
    for style, graph in graphs.items():
        assert graph.startswith("[aid1]asplit=2[ao][a];[a]aselect=not(isnan(pts)),")
        assert graph.endswith("[vo]")
        assert f"vis_style_{style}" in _STRINGS
    # Drawn on a transparent canvas: made opaque (mpv shows a checkerboard).
    for style in ("wave", "scope", "freqs"):
        assert ",format=rgb24[vo]" in graphs[style]
