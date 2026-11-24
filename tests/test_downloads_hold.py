"""A download and the window's one connection (ui/mw_downloads.py), and what
it asks of the disk: a channel waiting for the app's reconnect - no address
in the player for a moment - still holds the account, so no download starts
in the gap; the player about to open a stream holds the downloads waiting as
well as the one running, so one a beat let run a moment ago does not start
just after it; and the pills, the menus and the column under the poster
trust the folder's last read - the file is looked at once, as it plays."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from dopeiptv.core import downloads as dl  # noqa: E402
from dopeiptv.ui import mw_downloads  # noqa: E402
from dopeiptv.ui.main_window import MainWindow  # noqa: E402
from dopeiptv.ui.mw_connections import _ConnectionsMixin  # noqa: E402
from dopeiptv.ui.mw_downloads import _DownloadsMixin  # noqa: E402

LIVE = "http://192.0.2.1/live/kund/hemligt/1.ts"


class Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value


class Store:
    active_id = "a1b2c3d4"

    def playlists(self):
        return [{"id": "a1b2c3d4", "name": "Pirat", "kind": "xtream", "server": "http://192.0.2.1",
                 "username": "kund", "password": "hemligt"}]


class W(_DownloadsMixin, _ConnectionsMixin):
    """The window's parts the downloads ask of it, nothing more."""
    _stoppable = MainWindow._stoppable

    def __init__(self, tmp_path, limit=None):
        self.settings = Settings({dl.DIR_KEY: str(tmp_path)})
        self.downloads = dl.Downloads(self.settings, gated=True)
        self.playlist_store = Store()
        self.client = SimpleNamespace(max_connections=limit)
        self.player = SimpleNamespace(current_url=None, _dvr=None, _dvr_pool=None)
        self._playing_group = None
        self._last_playback = None
        self._dl_born = -1e9
        self.toasts: list = []
        self.started: list = []

    def _show_toast(self, text, ms=0):
        self.toasts.append(text)

    def _start_playback(self, url, title, icon, key, kind, *a, **k):
        self.started.append((url, kind, str(key)))


def _job(w: W, sid: str = "9001") -> dl.Job:
    job = w.downloads.add("movie", "a1b2c3d4", sid, title="Film " + sid)
    assert job is not None
    return job


def test_a_channel_waiting_to_come_back_holds_the_account(tmp_path):
    w = W(tmp_path)
    job = _job(w)
    assert w._dl_hold_reason(job) == "", "nothing plays: it may run"
    # Live, then the app's reconnect: its 0.3 s / 1.5 s / 15 s with no address.
    w._last_playback = {"url": LIVE, "kind": "live", "key": 1}
    w._playing_group = "live"
    w.player.current_url = LIVE
    assert w._dl_hold_reason(job) == "connection"
    w.player.current_url = None                     # the gap
    assert w._stoppable() and w._dl_hold_reason(job) == "connection"
    w._diag_shown = True                             # given up for good: nothing comes back
    assert w._dl_hold_reason(job) == ""
    w._diag_shown = False
    # A known limit with room for both: it runs beside it.
    w.client.max_connections = 2
    assert w._dl_hold_reason(job) == ""
    w.client.max_connections = None
    # Stop: nothing to come back.
    w._playing_group = None
    assert not w._stoppable() and w._dl_hold_reason(job) == ""
    # A station or a file is no connection to the account.
    w._playing_group = "radio"
    w._last_playback = {"url": "http://radio.example/stream", "kind": "radio"}
    assert w._dl_hold_reason(job) == ""
    w._last_playback = {"url": str(tmp_path / "a.mkv"), "kind": "local"}
    assert w._dl_hold_reason(job) == ""


def test_the_player_about_to_open_holds_the_waiting_downloads_too(tmp_path):
    """The last beat (up to a second old) let a waiting download run; the
    player opens a stream now: the download is held before the worker can
    start it - not only the one running is stopped."""
    w = W(tmp_path)
    job = _job(w)
    w.downloads.set_holds({job.id: ""})              # the beat: it may run
    assert w._dl_before_play(LIVE) is True, "never stops the playback"
    assert w.downloads.holds.get(job.id) == "connection"
    assert w.downloads._next() is None, "the worker does not start it"
    # The next beat, the player playing: still held; once it stops, it goes on.
    w.player.current_url = LIVE
    w._last_playback = {"url": LIVE, "kind": "live", "key": 1}
    w._playing_group = "live"
    w.downloads.set_holds({job.id: w._dl_hold_reason(job)})
    assert w.downloads._next() is None
    w.player.current_url = None
    w._playing_group = None
    w.downloads.set_holds({job.id: w._dl_hold_reason(job)})
    assert w.downloads._next() is not None
    # Another connection of the app's (a recording, a cast): the same.
    w2 = W(tmp_path / "two")
    other = _job(w2, "9002")
    w2.downloads.set_holds({other.id: ""})
    w2._dl_make_room("a1b2c3d4")
    assert w2.downloads.holds.get(other.id) == "connection" and w2.downloads._next() is None
    # A known limit with room for both leaves it be.
    w3 = W(tmp_path / "three", limit=3)
    free = _job(w3, "9003")
    w3.downloads.set_holds({free.id: ""})
    w3._dl_before_play(LIVE)
    assert w3.downloads.holds.get(free.id) == ""


def test_the_pills_trust_the_folders_read_and_the_file_is_looked_at_as_it_plays(
        tmp_path, monkeypatch):
    video = tmp_path / "Movies" / "Hemma (2021).mp4"
    video.parent.mkdir()
    video.write_bytes(b"x" * 2000)
    (tmp_path / "Movies" / ("Hemma (2021).mp4" + dl.SIDECAR)).write_text(json.dumps(
        {"format": 1, "kind": "movie", "source": "a1b2c3d4", "stream_id": "9001",
         "title": "Hemma", "added": 5}))
    w = W(tmp_path)
    w.downloads.scan()
    film = {"name": "Hemma", "stream_id": 9001}
    looks: list = []
    real = os.path.isfile

    def isfile(path):
        looks.append(path)
        return real(path)
    monkeypatch.setattr(mw_downloads.os.path, "isfile", isfile)
    for _ in range(50):                             # painted, over and over
        assert w._dl_pills("movie", film) == ("dl",)
        assert w._dl_find("movie", film) is not None
    assert looks == [], "no look at the disk while painting"
    # Played: one look. Its file gone meanwhile: from the source, as any
    # title - and no longer listed.
    video.unlink()
    w._dl_start("http://192.0.2.1/movie/kund/hemligt/9001.mp4", "Hemma", None, "9001", "movie",
                record=True, item=film)
    assert len(looks) == 1
    assert w.started == [("http://192.0.2.1/movie/kund/hemligt/9001.mp4", "movie", "9001")]
    assert w.downloads.find("a1b2c3d4", "movie", "9001") is None
    assert w._dl_pills("movie", film) == ()
