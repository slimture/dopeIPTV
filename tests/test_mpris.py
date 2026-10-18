"""MPRIS media session: what the desktop is told and what it can do.

The D-Bus half runs against a private session bus (dbus-run-session) in a
subprocess when one can be started; the host logic is checked directly.
"""
import os
import shutil
import subprocess
import sys
import textwrap

import pytest

from dopeiptv.services import mpris

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_clean_fills_and_types_every_field():
    s = mpris._clean({"status": "Playing", "length": "12.5", "volume": 3,
                      "position": -4, "can_next": 1})
    assert s["status"] == "Playing"
    assert s["length"] == 12.5
    assert s["volume"] == 1.0          # clamped to the spec's 0-1
    assert s["position"] == 0.0
    assert s["can_next"] is True and s["can_seek"] is False
    assert mpris._clean({"status": "Buffering"})["status"] == "Stopped"


class _Host:
    def __init__(self):
        self.snap = {"status": "Stopped"}
        self.calls = []

    def mpris_snapshot(self):
        return dict(self.snap)

    def mpris_position(self):
        return self.snap.get("position", 0.0)

    def mpris_command(self, name, arg=None):
        self.calls.append((name, arg))


def test_metadata_and_track_change(qapp=None):
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    host = _Host()
    svc = mpris.MprisService(host)
    svc._bus = object()                 # pretend to be on a bus
    sent = []
    svc._send_changed = lambda changed: sent.append(changed)
    host.snap = {"status": "Playing", "track": "live:1", "title": "SVT1",
                 "artist": "Rapport", "art": "file:///tmp/x.png"}
    svc.refresh()
    assert sent and sent[-1]["PlaybackStatus"] == "Playing"
    first = svc.track_path()
    assert first.startswith("/org/dopeiptv/track/")
    meta = sent[-1]["Metadata"]
    assert meta["xesam:title"] == "SVT1"
    assert "mpris:length" not in meta  # a channel has no length
    host.snap = dict(host.snap, track="live:2", title="SVT2")
    svc.refresh()
    assert svc.track_path() != first
    host.snap = {"status": "Stopped"}
    svc.refresh()
    assert svc.track_path() == mpris.NO_TRACK


_CHILD = textwrap.dedent(r"""
    import os, subprocess, sys
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    from dopeiptv.services import mpris

    app = QApplication([])
    calls = []

    class Host:
        def mpris_snapshot(self):
            return {"status": "Playing", "track": "vod:7", "title": "Big Buck",
                    "artist": "Blender", "length": 600.0, "position": 42.0,
                    "can_seek": True, "can_next": True, "volume": 0.5}
        def mpris_position(self):
            return 42.0
        def mpris_command(self, name, arg=None):
            calls.append((name, arg))

    svc = mpris.MprisService(Host())
    assert svc.start(), "could not join the session bus"
    dest = (svc.bus_name, "/org/mpris/MediaPlayer2")

    def busctl(*args):
        return subprocess.run(["busctl", "--user", *args], capture_output=True,
                              text=True, timeout=10).stdout

    import threading
    out = []

    def drive():
        # On its own thread: busctl asks THIS process, whose event loop has
        # to keep running to answer.
        meta = busctl("get-property", *dest, mpris.PLAYER_IFACE, "Metadata")
        out.append("META " + meta.strip())
        out.append("POS " + busctl("get-property", *dest, mpris.PLAYER_IFACE,
                                   "Position").strip())
        out.append("STATUS " + busctl("get-property", *dest,
                                      mpris.PLAYER_IFACE,
                                      "PlaybackStatus").strip())
        busctl("call", *dest, mpris.PLAYER_IFACE, "PlayPause")
        busctl("call", *dest, mpris.PLAYER_IFACE, "Seek", "x", "5000000")
        busctl("call", *dest, mpris.PLAYER_IFACE, "SetPosition", "ox",
               "/org/dopeiptv/track/1", "90000000")
        busctl("call", *dest, mpris.PLAYER_IFACE, "Next")
        busctl("set-property", *dest, mpris.PLAYER_IFACE, "Volume", "d",
               "0.25")
        out.append("DONE")

    worker = threading.Thread(target=drive, daemon=True)

    def check():
        worker.start()
        QTimer.singleShot(100, wait)

    def wait():
        if worker.is_alive():
            QTimer.singleShot(100, wait)
            return
        QTimer.singleShot(300, finish)

    def finish():
        for line in out:
            print(line)
        print("CALLS", calls)
        svc.stop()
        app.quit()

    QTimer.singleShot(200, check)
    app.exec()
""")


@pytest.mark.skipif(not sys.platform.startswith("linux")
                    or not shutil.which("dbus-run-session")
                    or not shutil.which("busctl"),
                    reason="needs dbus-run-session and busctl")
def test_the_desktop_sees_and_drives_the_session():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run(
        ["dbus-run-session", "--", sys.executable, "-c", _CHILD],
        capture_output=True, text=True, env=env, cwd=_REPO_ROOT, timeout=120)
    out = proc.stdout
    if "could not join the session bus" in proc.stderr:
        pytest.skip("no session bus could be started here")
    assert '"mpris:length" x 600000000' in out, out + proc.stderr
    assert '"xesam:artist" as 1 "Blender"' in out
    assert '"xesam:title" s "Big Buck"' in out
    assert "POS x 42000000" in out
    assert 'STATUS s "Playing"' in out
    assert "('playpause', None)" in out
    assert "('seek', 5.0)" in out
    assert "('set_position', 90.0)" in out
    assert "('next', None)" in out
    assert "('volume', 0.25)" in out


# -- the window's half (ui/mw_mpris.py), on a stand-in window -------------

class _Slider:
    def __init__(self, v):
        self.v = v

    def value(self):
        return self.v


class _Button:
    def __init__(self, calls):
        self.calls = calls

    def click(self):
        self.calls.append("stop button")


class _Player:
    def __init__(self, calls):
        self.calls = calls
        self.current_url = "http://x/movie/1.mkv"
        self._paused = False
        self._seek_mode = "vod"
        self.vol = _Slider(40)
        self.stop_btn = _Button(calls)

    def playback_position(self):
        return 30.0

    def playback_duration(self):
        return 5400.0

    def toggle_pause(self):
        self.calls.append("toggle")
        self._paused = not self._paused

    def _relative_seek(self, s):
        self.calls.append(("rel", s))

    def _do_seek(self, s):
        self.calls.append(("abs", s))

    def _set_volume(self, v):
        self.calls.append(("vol", v))


def _window(kind="movie"):
    from types import SimpleNamespace

    from dopeiptv.ui.mw_mpris import _MprisMixin

    calls = []

    class Win(_MprisMixin):
        def __init__(self):
            self.player = _Player(calls)
            self._playing_key = "k1"
            self._last_playback = {"kind": kind, "title": "Sintel",
                                   "icon_url": None,
                                   "item": {"stream_id": 1},
                                   "series_ctx": {"name": "Blender Shorts"}}
            self.xmltv = SimpleNamespace(
                current_programme=lambda it: {"title": "Rapport"})
            self.logos = SimpleNamespace(disk_file=lambda u: None)

        def _multiview_active(self):
            return None

        def _has_next_episode(self):
            return True

        def _play_next_episode(self):
            calls.append("next episode")

        def _zap(self, d):
            calls.append(("zap", d))

        def _toggle_pause_shortcut(self):
            calls.append("mv pause")

    return Win(), calls


def test_window_reports_what_is_playing():
    w, _ = _window("movie")
    s = w.mpris_snapshot()
    assert s["status"] == "Playing" and s["title"] == "Sintel"
    assert s["length"] == 5400.0 and s["can_seek"]
    assert not s["can_next"] and not s["can_prev"]
    assert s["volume"] == 0.4
    live, _ = _window("live")
    live.player._seek_mode = "live"
    s = live.mpris_snapshot()
    assert s["artist"] == "Rapport"       # what the channel is showing
    assert s["length"] == 0.0 and not s["can_seek"]
    assert s["can_next"] and s["can_prev"]
    ep, _ = _window("episode")
    assert ep.mpris_snapshot()["artist"] == "Blender Shorts"
    w.player.current_url = None
    assert w.mpris_snapshot()["status"] == "Stopped"


def test_window_does_what_the_desktop_asks():
    import time

    w, calls = _window("movie")
    w.mpris_command("pause")
    w.mpris_command("pause")             # a repeat within the debounce
    assert calls == ["toggle"]
    w.mpris_command("play")
    w.mpris_command("seek", -10.0)
    w.mpris_command("set_position", 120.0)
    w.mpris_command("volume", 0.25)
    w.mpris_command("stop")
    assert calls[1:] == ["toggle", ("rel", -10.0), ("abs", 120.0),
                         ("vol", 25), "stop button"]
    ep, calls = _window("episode")
    ep.mpris_command("next")
    ep.mpris_command("previous")          # no previous episode to go to
    assert calls == ["next episode"]
    live, calls = _window("live")
    live.mpris_command("next")
    time.sleep(0.4)
    live.mpris_command("next")
    live.mpris_command("previous")
    assert calls == [("zap", 1), ("zap", 1), ("zap", -1)]
