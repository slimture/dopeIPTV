"""The system's media controls on macOS and Windows (services/media_session.py
and its backends), on Linux with stand-ins for PyObjC and PyWinRT.

Neither bridge is installed where the suite runs, so each backend is driven
through fake modules put in sys.modules: what it tells the system, what the
system's buttons do, that a button pressed on another thread is carried out
on the GUI thread, and that a missing bridge leaves the app as it was.
"""
import importlib.machinery
import logging
import os
import sys
import threading
import time
import types
from datetime import timedelta
from enum import IntEnum
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
# A 1x1 PNG: the logo the image cache would have on disk.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d6a4c400"
    "00000049454e44ae426082")


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _fresh_once(monkeypatch):
    from dopeiptv.services import media_session
    monkeypatch.setattr(media_session, "_said", set())


def _pump(qapp, seconds=0.05):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)


def _module(name, **attrs):
    mod = types.ModuleType(name)
    mod.__spec__ = importlib.machinery.ModuleSpec(name, None)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


class _Host:
    def __init__(self):
        self.snap = {"status": "Stopped"}
        self.calls = []
        self.threads = []

    def mpris_snapshot(self):
        return dict(self.snap)

    def mpris_position(self):
        return self.snap.get("position", 0.0)

    def mpris_command(self, name, arg=None):
        self.calls.append((name, arg))
        self.threads.append(threading.get_ident())


def _art(tmp_path):
    from PyQt6.QtCore import QUrl
    p = tmp_path / "logo.png"
    p.write_bytes(_PNG)
    return QUrl.fromLocalFile(str(p)).toString(), str(p)


_LIVE = {"status": "Playing", "track": "live:1", "title": "SVT1",
         "artist": "Rapport", "live": True, "can_next": True,
         "can_prev": True, "volume": 0.5}
_VOD = {"status": "Paused", "track": "movie:7", "title": "Sintel",
        "length": 600.0, "position": 42.0, "can_seek": True, "volume": 0.5}


# -- the shared part ---------------------------------------------------------

def test_clean_snapshot_has_every_field_and_mpris_keeps_its_names():
    from dopeiptv.services import media_session, mpris
    s = media_session.clean_snapshot({"status": "Playing", "live": 1})
    assert s["live"] is True and s["audio"] is False
    assert media_session.clean_snapshot({})["status"] == "Stopped"
    # MPRIS reads the same snapshot through the same host as before.
    assert mpris._clean is media_session.clean_snapshot
    assert mpris.MprisHost is media_session.MediaHost


def test_changes_names_what_differs():
    from dopeiptv.services.media_session import changes, clean_snapshot
    a = clean_snapshot(_VOD)
    assert changes(a, dict(a)) == set()
    assert changes(a, dict(a, title="Other")) == {"meta"}
    assert changes(a, dict(a, status="Playing")) == {"status"}
    assert changes(a, dict(a, can_next=True)) == {"caps"}


def test_local_art_is_a_file_on_this_computer(tmp_path):
    from dopeiptv.services.media_session import local_art
    url, path = _art(tmp_path)
    assert local_art(url) == path
    assert local_art(path) == path
    assert local_art("https://provider.example/logo.png") is None
    assert local_art(str(tmp_path / "gone.png")) is None
    assert local_art("") is None


def test_create_session_per_system(qapp, monkeypatch):
    from dopeiptv.services import media_session
    from dopeiptv.services.media_macos import NowPlayingSession
    from dopeiptv.services.media_windows import SmtcSession
    from dopeiptv.services.mpris import MprisService
    host = _Host()
    for platform, cls in (("linux", MprisService), ("darwin", NowPlayingSession),
                          ("win32", SmtcSession)):
        monkeypatch.setattr(sys, "platform", platform)
        svc = media_session.create_session(host)
        assert type(svc) is cls, platform
        svc.deleteLater()
    monkeypatch.setattr(sys, "platform", "sunos5")
    assert media_session.create_session(host) is None


class _Backend:
    """A PolledSession whose system is a list."""

    @staticmethod
    def make(host, fail_open=False, fail_show=False):
        from dopeiptv.services.media_session import PolledSession

        class B(PolledSession):
            NAME = "test session"

            def __init__(self, h):
                super().__init__(h)
                self.shown, self.closed = [], 0

            def _open(self):
                if fail_open:
                    raise ImportError("No module named 'Bridge'")
                return True

            def _close(self):
                self.closed += 1

            def _show(self, new, old, what):
                if fail_show:
                    raise RuntimeError("the system said no")
                self.shown.append(set(what))
        return B(host)


def test_a_missing_bridge_is_said_once_and_leaves_nothing(qapp, caplog):
    caplog.set_level(logging.DEBUG, logger="dopeiptv")
    svc = _Backend.make(_Host(), fail_open=True)
    assert not svc.start() and not svc.start()
    assert not svc.active and svc.closed == 2
    said = [r for r in caplog.records if "media controls off" in r.getMessage()]
    assert len(said) == 1 and said[0].levelno == logging.DEBUG
    svc.ask("play")
    _pump(qapp)
    assert svc.host.calls == []


def test_a_failing_system_never_reaches_the_window(qapp, caplog):
    caplog.set_level(logging.DEBUG, logger="dopeiptv")
    host = _Host()
    svc = _Backend.make(host, fail_show=True)
    assert svc.start()
    host.snap = dict(_LIVE)
    svc.refresh()
    svc.refresh()
    said = [r for r in caplog.records if "could not show" in r.getMessage()]
    assert len(said) == 1
    svc.stop()


def test_commands_from_any_thread_run_on_the_gui_thread(qapp):
    host = _Host()
    svc = _Backend.make(host)
    assert svc.start()
    host.snap = dict(_VOD)
    svc.refresh()
    worker = threading.Thread(target=lambda: (svc.ask("pause"),
                                              svc.ask("seek", 30.0)))
    worker.start()
    worker.join()
    assert host.calls == []              # queued, not run on that thread
    _pump(qapp)
    assert host.calls == [("pause", None), ("seek", 30.0)]
    assert set(host.threads) == {threading.get_ident()}
    # As MPRIS: never past the end, never seeking what cannot seek.
    svc.ask("set_position", 9999.0)
    svc.ask("set_position", -1.0)
    svc.ask("set_position", 120.0)
    _pump(qapp)
    assert host.calls[2:] == [("set_position", 120.0)]
    host.snap = dict(_LIVE)
    svc.refresh()
    svc.ask("seek", 30.0)
    svc.ask("next")
    _pump(qapp)
    assert host.calls[3:] == [("next", None)]
    svc.stop()
    assert svc.closed == 1
    svc.ask("play")
    _pump(qapp)
    assert host.calls[-1] == ("next", None)


def test_switched_on_again_while_playing_shows_it_at_once(qapp):
    host = _Host()
    svc = _Backend.make(host)
    host.snap = dict(_LIVE)
    assert svc.start()
    assert "meta" in svc.shown[-1]
    svc.stop()
    assert svc.start()
    assert svc.shown[-1] >= {"meta", "status", "caps"}
    svc.stop()


def test_a_seek_is_noticed(qapp):
    host = _Host()
    svc = _Backend.make(host)
    assert svc.start()
    host.snap = dict(_VOD)
    svc.refresh()
    svc.refresh()
    assert svc.shown[-1] == set()
    host.snap = dict(_VOD, position=300.0)
    svc.refresh()
    assert svc.shown[-1] == {"position"}
    svc.stop()


# -- macOS: Now Playing --------------------------------------------------------

class _Command:
    def __init__(self, name):
        self.name = name
        self.handlers = []
        self.enabled = True
        self.intervals = None

    def addTargetWithHandler_(self, handler):
        self.handlers.append(handler)
        return ("target", self.name)

    def removeTarget_(self, target):
        assert target == ("target", self.name)
        self.handlers.clear()

    def setEnabled_(self, on):
        self.enabled = on

    def setPreferredIntervals_(self, values):
        self.intervals = values


class _Event:
    def __init__(self, **values):
        self.values = values

    def interval(self):
        return self.values["interval"]

    def positionTime(self):
        if "position" not in self.values:
            raise AttributeError("positionTime")
        return self.values["position"]


def _fake_macos(monkeypatch, missing_keys=()):
    from dopeiptv.services import media_macos

    class InfoCenter:
        def __init__(self):
            self.info, self.state = "unset", None

        def setNowPlayingInfo_(self, info):
            self.info = None if info is None else dict(info)

        def setPlaybackState_(self, state):
            self.state = state

    info_center = InfoCenter()
    commands = {prop: _Command(prop) for prop, _ in media_macos._COMMANDS}

    class CommandCenter:
        def __getattr__(self, name):
            if name in commands:
                return lambda: commands[name]
            raise AttributeError(name)

    command_center = CommandCenter()

    class Artwork:
        @classmethod
        def alloc(cls):
            return cls()

        def initWithBoundsSize_requestHandler_(self, size, handler):
            self.size, self.handler = size, handler
            return self

    class Size:
        width, height = 64.0, 32.0

    class Image:
        @classmethod
        def alloc(cls):
            return cls()

        def initWithContentsOfFile_(self, path):
            self.path = path
            return self if os.path.isfile(path) else None

        def size(self):
            return Size()

    class Dictionary(dict):
        @classmethod
        def dictionary(cls):
            return cls()

        def setObject_forKey_(self, value, key):
            self[key] = value

    keys = {k: k.replace("MPMediaItemProperty", "item.").replace(
        "MPNowPlayingInfoProperty", "now.") for k in (
        "MPMediaItemPropertyTitle", "MPMediaItemPropertyArtist",
        "MPMediaItemPropertyAlbumTitle", "MPMediaItemPropertyArtwork",
        "MPMediaItemPropertyPlaybackDuration",
        "MPNowPlayingInfoPropertyElapsedPlaybackTime",
        "MPNowPlayingInfoPropertyPlaybackRate",
        "MPNowPlayingInfoPropertyDefaultPlaybackRate",
        "MPNowPlayingInfoPropertyMediaType",
        "MPNowPlayingInfoPropertyIsLiveStream",
        "MPNowPlayingInfoPropertyExcludeFromSuggestions") if k not in missing_keys}
    mp = _module(
        "MediaPlayer",
        MPNowPlayingInfoCenter=types.SimpleNamespace(
            defaultCenter=lambda: info_center),
        MPRemoteCommandCenter=types.SimpleNamespace(
            sharedCommandCenter=lambda: command_center),
        MPMediaItemArtwork=Artwork, **keys)
    ak = _module("AppKit", NSImage=Image)
    fd = _module(
        "Foundation", NSMutableDictionary=Dictionary,
        NSNumber=types.SimpleNamespace(
            numberWithDouble_=float, numberWithBool_=bool,
            numberWithUnsignedInteger_=int),
        NSString=types.SimpleNamespace(stringWithString_=str),
        NSArray=types.SimpleNamespace(arrayWithObject_=lambda x: [x]))
    for mod in (mp, ak, fd):
        monkeypatch.setitem(sys.modules, mod.__name__, mod)
    return info_center, commands


def test_macos_now_playing_shows_what_plays(qapp, monkeypatch, tmp_path):
    from dopeiptv.services.media_macos import NowPlayingSession
    center, commands = _fake_macos(monkeypatch)
    host = _Host()
    svc = NowPlayingSession(host)
    assert svc.start()
    assert all(len(c.handlers) == 1 for c in commands.values())
    assert commands["skipForwardCommand"].intervals == [30.0]
    assert commands["skipBackwardCommand"].intervals == [10.0]
    assert not commands["nextTrackCommand"].enabled        # nothing plays

    url, path = _art(tmp_path)
    host.snap = dict(_LIVE, art=url)
    svc.refresh()
    info = center.info
    assert info["item.Title"] == "SVT1" and info["item.Artist"] == "Rapport"
    assert info["now.IsLiveStream"] is True
    assert info["now.PlaybackRate"] == 1.0 and center.state == 1
    assert info["now.MediaType"] == 2                        # video
    assert info["now.ExcludeFromSuggestions"] is True
    assert "item.PlaybackDuration" not in info               # a channel
    art = info["item.Artwork"]
    assert art.size == (64.0, 32.0) and art.handler((10, 10)).path == path
    assert commands["nextTrackCommand"].enabled
    assert not commands["skipForwardCommand"].enabled
    assert not commands["changePlaybackPositionCommand"].enabled

    host.snap = dict(_VOD)
    svc.refresh()
    info = center.info
    assert info["item.PlaybackDuration"] == 600.0
    assert info["now.ElapsedPlaybackTime"] == 42.0
    assert info["now.PlaybackRate"] == 0.0 and center.state == 2
    assert info["now.IsLiveStream"] is False
    assert "item.Artwork" not in info and "item.Artist" not in info
    assert commands["skipForwardCommand"].enabled
    host.snap = dict(_VOD, position=300.0)                  # a seek
    svc.refresh()
    assert center.info["now.ElapsedPlaybackTime"] == 300.0

    host.snap = dict(_VOD, track="local:a.mp3", title="Jail", artist="Ye",
                     album="Donda", audio=True, status="Playing")
    svc.refresh()
    assert center.info["now.MediaType"] == 1
    assert center.info["item.AlbumTitle"] == "Donda"

    host.snap = {"status": "Stopped"}
    svc.refresh()
    assert center.info is None and center.state == 3
    svc.stop()
    assert all(not c.handlers and not c.enabled for c in commands.values())
    # Switched on again in Settings: play works after a stop, as before.
    assert svc.start()
    assert all(len(c.handlers) == 1 for c in commands.values())
    assert commands["playCommand"].enabled
    assert commands["togglePlayPauseCommand"].enabled
    assert not commands["pauseCommand"].enabled
    svc.stop()


def test_macos_commands_reach_the_window(qapp, monkeypatch):
    from dopeiptv.services.media_macos import NowPlayingSession
    _center, commands = _fake_macos(monkeypatch)
    host = _Host()
    svc = NowPlayingSession(host)
    assert svc.start()
    host.snap = dict(_VOD)
    svc.refresh()

    def press(prop, **event):
        return commands[prop].handlers[0](_Event(**event))

    assert press("togglePlayPauseCommand") == 0
    assert host.calls == []           # answered at once, carried out after
    _pump(qapp)
    assert host.calls == [("playpause", None)]
    for prop in ("playCommand", "pauseCommand", "stopCommand",
                 "nextTrackCommand", "previousTrackCommand"):
        assert press(prop) == 0
    assert press("skipForwardCommand", interval=15.0) == 0
    assert press("skipBackwardCommand", interval=0.0) == 0   # its default
    assert press("changePlaybackPositionCommand", position=120.0) == 0
    assert press("changePlaybackPositionCommand") == 200     # a bad event
    _pump(qapp)
    assert host.calls[1:] == [("play", None), ("pause", None), ("stop", None),
                              ("next", None), ("previous", None),
                              ("seek", 15.0), ("seek", -10.0),
                              ("set_position", 120.0)]
    svc.stop()


def test_macos_without_a_key_this_system_lacks(qapp, monkeypatch):
    from dopeiptv.services.media_macos import NowPlayingSession
    center, _ = _fake_macos(
        monkeypatch, missing_keys=("MPNowPlayingInfoPropertyExcludeFromSuggestions",))
    host = _Host()
    svc = NowPlayingSession(host)
    assert svc.start()
    host.snap = dict(_LIVE)
    svc.refresh()
    assert center.info["item.Title"] == "SVT1"
    assert "now.ExcludeFromSuggestions" not in center.info
    svc.stop()


def test_macos_without_pyobjc_stays_off(qapp, monkeypatch):
    from dopeiptv.services import media_session
    from dopeiptv.services.media_macos import NowPlayingSession
    monkeypatch.setitem(sys.modules, "MediaPlayer", None)
    svc = NowPlayingSession(_Host())
    assert not svc.start() and not svc.active
    monkeypatch.setattr(sys, "platform", "darwin")
    assert "No module" in (media_session.bundle_problem() or "")
    assert not media_session.supported()


def test_macos_bundle_check_and_support(qapp, monkeypatch):
    from dopeiptv.services import media_session
    _fake_macos(monkeypatch)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert media_session.bundle_problem() is None
    assert media_session.supported()


# -- Windows: the System Media Transport Controls --------------------------------

class _Status(IntEnum):
    CLOSED = 0
    CHANGING = 1
    STOPPED = 2
    PLAYING = 3
    PAUSED = 4


class _Type(IntEnum):
    UNKNOWN = 0
    MUSIC = 1
    VIDEO = 2
    IMAGE = 3


class _Props(types.SimpleNamespace):
    pass


class _Updater:
    def __init__(self):
        self.shown = []
        self.clear_all()

    def clear_all(self):
        self.type = _Type.UNKNOWN
        self.music_properties = _Props(title="", artist="", album_title="")
        self.video_properties = _Props(title="", subtitle="")
        self.thumbnail = None

    def update(self):
        self.shown.append({
            "type": self.type, "thumbnail": self.thumbnail,
            "music": dict(vars(self.music_properties)),
            "video": dict(vars(self.video_properties))})


class _Smtc:
    def __init__(self):
        self.display_updater = _Updater()
        self.handlers = {}
        self.timelines = []
        self.playback_status = None
        self.is_enabled = False
        self._token = 0

    def _add(self, kind, handler):
        self._token += 1
        self.handlers[kind] = (self._token, handler)
        return self._token

    def add_button_pressed(self, handler):
        return self._add("button", handler)

    def add_playback_position_change_requested(self, handler):
        return self._add("position", handler)

    def remove_button_pressed(self, token):
        assert self.handlers.pop("button")[0] == token

    def remove_playback_position_change_requested(self, token):
        assert self.handlers.pop("position")[0] == token

    def update_timeline_properties(self, t):
        self.timelines.append(dict(vars(t)))


class _Op:
    def __init__(self):
        self.status = 0
        self.handler = None

    @property
    def completed(self):
        return self.handler

    @completed.setter
    def completed(self, handler):
        self.handler = handler

    def finish(self, status=1):
        self.status = status
        self.handler(self, status)


def _fake_windows(monkeypatch, uninitialized=0):
    made = {"players": [], "ops": [], "apartments": []}
    tries = {"n": uninitialized}

    class MediaPlayer:
        def __init__(self):
            if tries["n"]:
                tries["n"] -= 1
                e = OSError("CoInitialize has not been called")
                e.winerror = -2147221008
                raise e
            self.command_manager = _Props(is_enabled=True)
            self.system_media_transport_controls = _Smtc()
            self.closed = False
            made["players"].append(self)

        def close(self):
            self.closed = True

    class Timeline:
        pass

    class Stream:
        def __init__(self):
            self.position = None

        def seek(self, pos):
            self.position = pos

    class DataWriter:
        def __init__(self, stream):
            self.stream, self.data, self.detached = stream, b"", False

        def write_bytes(self, data):
            self.data += bytes(data)

        def store_async(self):
            op = _Op()
            made["ops"].append((op, self))
            return op

        def detach_stream(self):
            self.detached = True
            return self.stream

    class Reference:
        @staticmethod
        def create_from_stream(stream):
            return ("thumbnail", stream)

    runtime = _module(
        "winrt.runtime",
        ApartmentType=types.SimpleNamespace(SINGLE_THREADED=0, MULTI_THREADED=1),
        init_apartment=made["apartments"].append)
    mods = [
        _module("winrt", runtime=runtime), _module("winrt.windows"),
        runtime,
        _module("winrt.windows.media", MediaPlaybackStatus=_Status,
                MediaPlaybackType=_Type,
                SystemMediaTransportControlsTimelineProperties=Timeline),
        _module("winrt.windows.media.playback", MediaPlayer=MediaPlayer),
        _module("winrt.windows.storage"),
        _module("winrt.windows.storage.streams", DataWriter=DataWriter,
                InMemoryRandomAccessStream=Stream,
                RandomAccessStreamReference=Reference),
    ]
    for mod in mods:
        monkeypatch.setitem(sys.modules, mod.__name__, mod)
    return made


def test_windows_smtc_shows_what_plays(qapp, monkeypatch, tmp_path):
    from dopeiptv.services.media_windows import SmtcSession
    made = _fake_windows(monkeypatch)
    host = _Host()
    svc = SmtcSession(host)
    assert svc.start()
    player = made["players"][0]
    smtc = player.system_media_transport_controls
    assert player.command_manager.is_enabled is False
    assert smtc.is_enabled and smtc.playback_status == _Status.CLOSED
    assert set(smtc.handlers) == {"button", "position"}

    url, _path = _art(tmp_path)
    host.snap = dict(_VOD, art=url, artist="Blender", status="Playing")
    svc.refresh()
    shown = smtc.display_updater.shown[-1]
    assert shown["type"] == _Type.VIDEO
    assert shown["video"] == {"title": "Sintel", "subtitle": "Blender"}
    assert shown["thumbnail"] is None                     # still being written
    assert smtc.playback_status == _Status.PLAYING
    assert smtc.is_fast_forward_enabled and not smtc.is_channel_up_enabled
    t = smtc.timelines[-1]
    assert t["end_time"] == timedelta(seconds=600) == t["max_seek_time"]
    assert t["position"] == timedelta(seconds=42)

    # The logo, written into a stream in memory on a thread of Windows' own.
    op, writer = made["ops"][0]
    assert writer.data == _PNG
    worker = threading.Thread(target=op.finish)
    worker.start()
    worker.join()
    _pump(qapp)
    assert writer.detached and writer.stream.position == 0
    assert smtc.display_updater.shown[-1]["thumbnail"] == ("thumbnail", writer.stream)
    # Shown again from the same stream, never written twice.
    host.snap = dict(host.snap, artist="Blender Shorts")
    svc.refresh()
    assert smtc.display_updater.shown[-1]["thumbnail"] == ("thumbnail", writer.stream)
    assert len(made["ops"]) == 1

    # Every five seconds while a title plays, Windows hears the position.
    n = len(smtc.timelines)
    svc.refresh()
    assert len(smtc.timelines) == n
    svc._timeline_at -= 10
    svc.refresh()
    assert len(smtc.timelines) == n + 1

    host.snap = dict(_LIVE)
    svc.refresh()
    assert smtc.is_channel_up_enabled and smtc.is_next_enabled
    assert not smtc.is_fast_forward_enabled
    assert smtc.timelines[-1]["end_time"] == timedelta(0)
    assert smtc.display_updater.shown[-1]["video"]["subtitle"] == "Rapport"

    host.snap = dict(_LIVE, track="local:a.mp3", title="Jail", artist="Ye",
                     album="Donda", audio=True, live=False)
    svc.refresh()
    shown = smtc.display_updater.shown[-1]
    assert shown["type"] == _Type.MUSIC
    assert shown["music"] == {"title": "Jail", "artist": "Ye", "album_title": "Donda"}

    host.snap = {"status": "Stopped"}
    svc.refresh()
    assert smtc.playback_status == _Status.CLOSED
    assert smtc.display_updater.shown[-1]["type"] == _Type.UNKNOWN
    svc.stop()
    assert smtc.handlers == {} and not smtc.is_enabled and player.closed


def test_windows_buttons_reach_the_gui_thread(qapp, monkeypatch):
    from dopeiptv.services.media_windows import SmtcSession
    made = _fake_windows(monkeypatch)
    host = _Host()
    svc = SmtcSession(host)
    assert svc.start()
    smtc = made["players"][0].system_media_transport_controls
    host.snap = dict(_VOD, status="Playing")
    svc.refresh()
    button = smtc.handlers["button"][1]
    position = smtc.handlers["position"][1]

    def press_all():
        for b in (1, 0, 2, 4, 5, 6, 7, 3):            # 3: record, not ours
            button(smtc, types.SimpleNamespace(button=b))
        position(smtc, types.SimpleNamespace(
            requested_playback_position=timedelta(seconds=90)))
        button(smtc, object())                          # a broken event
    worker = threading.Thread(target=press_all)
    worker.start()
    worker.join()
    assert host.calls == []
    _pump(qapp)
    assert host.calls == [("pause", None), ("play", None), ("stop", None),
                          ("seek", 30.0), ("seek", -10.0), ("next", None),
                          ("previous", None), ("set_position", 90.0)]
    assert set(host.threads) == {threading.get_ident()}
    host.snap = dict(_LIVE)
    svc.refresh()
    del host.calls[:]
    for b in (8, 9, 4):                               # channel up/down, ff
        button(smtc, types.SimpleNamespace(button=b))
    _pump(qapp)
    assert host.calls == [("next", None), ("previous", None)]
    svc.stop()


def test_windows_joins_an_apartment_only_when_there_is_none(qapp, monkeypatch):
    from dopeiptv.services.media_windows import SmtcSession
    made = _fake_windows(monkeypatch)
    svc = SmtcSession(_Host())
    assert svc.start() and made["apartments"] == []
    svc.stop()
    made = _fake_windows(monkeypatch, uninitialized=1)
    svc = SmtcSession(_Host())
    assert svc.start() and made["apartments"] == [0]
    svc.stop()


def test_windows_without_pywinrt_stays_off(qapp, monkeypatch):
    from dopeiptv.services import media_session
    from dopeiptv.services.media_windows import SmtcSession
    for name in ("winrt", "winrt.windows", "winrt.windows.media"):
        monkeypatch.setitem(sys.modules, name, None)
    svc = SmtcSession(_Host())
    assert not svc.start() and not svc.active
    monkeypatch.setattr(sys, "platform", "win32")
    assert media_session.bundle_problem()
    assert not media_session.supported()


def test_windows_bundle_check_and_support(qapp, monkeypatch):
    from dopeiptv.services import media_session
    _fake_windows(monkeypatch)
    monkeypatch.setattr(sys, "platform", "win32")
    assert media_session.bundle_problem() is None
    assert media_session.supported()


# -- the window's side (ui/mw_mpris.py) ------------------------------------------

def _window(kind="movie", item=None):
    from types import SimpleNamespace

    from dopeiptv.ui.mw_mpris import _MprisMixin
    calls, fetched = [], []

    class Player:
        current_url = "http://x/1"
        _paused = False
        _seek_mode = "vod"
        vol = SimpleNamespace(value=lambda: 40)

        def playback_position(self):
            return 30.0

        def playback_duration(self):
            return 5400.0

        def toggle_pause(self):
            calls.append("toggle")
            self._paused = not self._paused

    class Win(_MprisMixin):
        AUDIO_EXTS = (".mp3", ".flac")

        def __init__(self):
            self.player = Player()
            self._playing_key = "k1"
            self._last_playback = {"kind": kind, "title": "T",
                                   "icon_url": "https://p.example/logo.png",
                                   "item": item or {"stream_id": 1}}
            self.xmltv = SimpleNamespace(current_programme=lambda it: None)
            self.logos = SimpleNamespace(
                disk_file=lambda u: None,
                get=lambda u, cb: fetched.append(u))

        def _multiview_active(self):
            return None

        def _has_next_episode(self):
            return False

    return Win(), calls, fetched


def test_window_says_live_and_music(monkeypatch):
    live, _, _ = _window("live")
    assert live.mpris_snapshot()["live"] is True
    movie, _, _ = _window("movie")
    s = movie.mpris_snapshot()
    assert s["live"] is False and s["audio"] is False
    song, _, _ = _window("local", {"_path": "/m/Jail.MP3"})
    assert song.mpris_snapshot()["audio"] is True
    video, _, _ = _window("local", {"_path": "/m/Holiday.mkv"})
    assert video.mpris_snapshot()["audio"] is False


def test_window_fetches_art_for_macos_and_windows_only(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    w, _, fetched = _window("live")
    assert w.mpris_snapshot()["art"] == "https://p.example/logo.png"
    assert fetched == []                       # MPRIS: as it always was
    monkeypatch.setattr(sys, "platform", "win32")
    w.mpris_snapshot()
    w.mpris_snapshot()
    assert fetched == ["https://p.example/logo.png"]   # once


def test_play_pause_is_one_press_off_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    w, calls, _ = _window("movie")
    assert w._media_press_key("pause") == "pause"
    w.mpris_command("pause")
    w.mpris_command("playpause")
    assert calls == ["toggle", "toggle"]        # Linux: unchanged
    for platform in ("win32", "darwin"):
        monkeypatch.setattr(sys, "platform", platform)
        w, calls, _ = _window("movie")
        assert w._media_press_key("play") == "playpause"
        assert w._media_press_key("next") == "next"
        # The key heard by the window, then Windows' Pause for the same press.
        w.mpris_command("playpause")
        w.mpris_command("pause")
        assert calls == ["toggle"], platform


def test_window_joins_late_off_linux_and_never_after_stop(qapp, monkeypatch, tmp_path):
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QWidget

    from dopeiptv.services import media_session
    from dopeiptv.ui import mw_mpris

    started = []

    class Svc:
        def start(self):
            started.append(self)
            return True

        def stop(self):
            started.remove(self)

        def refresh(self):
            pass

    monkeypatch.setattr(media_session, "create_session", lambda host, parent: Svc())
    monkeypatch.setattr(mw_mpris, "_JOIN_LATER_MS", 10)

    class Win(mw_mpris._MprisMixin, QWidget):
        def __init__(self):
            super().__init__()
            self.player = None
            self.settings = QSettings(str(tmp_path / "s.ini"),
                                      QSettings.Format.IniFormat)

    monkeypatch.setattr(sys, "platform", "darwin")
    w = Win()
    w._start_media_session()
    assert w._mpris is None                    # not in the first paint's way
    _pump(qapp, 0.2)
    assert isinstance(w._mpris, Svc) and len(started) == 1
    w._stop_media_session()
    assert w._mpris is None and started == []

    w2 = Win()
    w2._start_media_session()
    w2._stop_media_session()                   # closed within the moment
    _pump(qapp, 0.2)
    assert w2._mpris is None and started == []

    monkeypatch.setattr(sys, "platform", "linux")
    w3 = Win()
    w3._start_media_session()
    assert isinstance(w3._mpris, Svc)          # Linux: at once, as before
    w3._stop_media_session()
    for win in (w, w2, w3):
        win.deleteLater()


# -- the build -------------------------------------------------------------------

def _requirements(lines):
    out = {}
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        spec, _, marker = line.partition(";")
        name = spec.strip()
        for sep in "<>=!~":
            name = name.split(sep, 1)[0]
        out[name.strip().lower()] = marker.strip().replace("'", '"')
    return out


def test_the_bridges_are_installed_and_bundled_on_their_own_system_only():
    import tomllib
    req = _requirements((_REPO / "requirements.txt").read_text().splitlines())
    with open(_REPO / "pyproject.toml", "rb") as fh:
        deps = _requirements(tomllib.load(fh)["project"]["dependencies"])
    bridges = {n: m for n, m in req.items()
               if n.startswith(("pyobjc", "winrt"))}
    assert "pyobjc-framework-mediaplayer" in bridges
    assert "winrt-windows.media.playback" in bridges
    for name, marker in bridges.items():
        system = "darwin" if name.startswith("pyobjc") else "win32"
        # Linux - CI, the AppImage, the Flatpak - never installs them.
        assert marker == f'sys_platform == "{system}"', name
        assert deps.get(name) == marker, name     # the wheel says the same
    mac_spec = (_REPO / "dopeiptv-mac.spec").read_text()
    win_spec = (_REPO / "dopeiptv-win.spec").read_text()
    from dopeiptv.services.media_session import _NEEDS
    for module in _NEEDS["darwin"]:
        assert f'"{module}"' in mac_spec, module
    for module in _NEEDS["win32"]:
        assert f'"{module}"' in win_spec, module
    assert "winrt" not in (_REPO / "dopeiptv.spec").read_text()
