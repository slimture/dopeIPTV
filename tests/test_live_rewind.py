"""Rewinding a live channel: a buffer in memory sized by Settings, or on
disk where the user picks that - mpv's disk cache is append-only, its hidden
file grows 1-4 GB an hour for as long as a channel plays."""
from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtCore import QSettings

from dopeiptv.core.local_buffer import (
    BYTES_PER_MINUTE, MEMORY_MAX_MINUTES, rewind_bytes)
from dopeiptv.media.embedded import EmbeddedPlayer
from dopeiptv.ui.main_window import MainWindow


def _player(tmp_path, minutes=None, disk=None):
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    if minutes is not None:
        s.setValue("live_rewind_minutes", str(minutes))
    if disk is not None:
        s.setValue("live_rewind_disk", disk)
    p = SimpleNamespace(_settings=s)
    p._live_rewind_minutes = lambda: EmbeddedPlayer._live_rewind_minutes(p)
    p._live_rewind_disk = lambda: EmbeddedPlayer._live_rewind_disk(p)
    return p


def test_two_minutes_of_hd_in_memory_by_default(tmp_path):
    p = _player(tmp_path)
    assert EmbeddedPlayer._live_rewind_minutes(p) == 2
    assert not EmbeddedPlayer._live_rewind_disk(p)
    assert EmbeddedPlayer._back_buffer(p, True) == 2 * 60 * 1024 * 1024


def test_memory_stops_at_ten_minutes_disk_goes_further(tmp_path):
    assert rewind_bytes(5, False) == 5 * BYTES_PER_MINUTE
    assert rewind_bytes(360, False) == MEMORY_MAX_MINUTES * BYTES_PER_MINUTE
    assert rewind_bytes(360, True) == 360 * BYTES_PER_MINUTE
    p = _player(tmp_path, 240, "true")
    assert EmbeddedPlayer._back_buffer(p, True) == 240 * BYTES_PER_MINUTE


def test_other_streams_keep_what_they_had(tmp_path, monkeypatch):
    p = _player(tmp_path, 10, "true")
    monkeypatch.delenv("DOPEIPTV_DEMUX_MAX_BACK", raising=False)
    assert EmbeddedPlayer._back_buffer(p, False) == "50MiB"
    monkeypatch.setenv("DOPEIPTV_DEMUX_MAX_BACK", "192MiB")
    assert EmbeddedPlayer._back_buffer(p, False) == "192MiB"


def test_off_and_damaged_values(tmp_path):
    assert EmbeddedPlayer._live_rewind_minutes(_player(tmp_path, 0)) == 0
    assert EmbeddedPlayer._live_rewind_minutes(_player(tmp_path, "x")) == 2


def test_play_puts_the_buffer_on_disk_only_when_asked():
    import inspect
    src = inspect.getsource(EmbeddedPlayer.play)
    assert "self._live_rewind_disk()" in src.split('m["cache-on-disk"]', 1)[1][:120]
    assert "_live_rewind_minutes() > 0" in src


def _window(playback=None, catchup=False, jobs=()):
    calls = []
    w = SimpleNamespace(
        _last_playback=playback, _playing_catchup=catchup, _diag_shown=True,
        rec=SimpleNamespace(jobs=list(jobs)),
        player=SimpleNamespace(go_to_buffer_end=lambda: calls.append("end")),
        _retry_last_stream=lambda: calls.append("reopen"))
    return w, calls


LIVE = {"kind": "live", "url": "http://x.invalid/live/1.ts", "item": {}}


def test_live_far_behind_opens_the_channel_again():
    w, calls = _window(LIVE)
    MainWindow._reopen_live_edge(w)
    assert calls == ["reopen"]
    assert w._diag_shown is False


def test_never_while_recording_or_in_an_archive():
    for w, calls in (_window(LIVE, jobs=[{"status": "recording"}]),
                     _window(LIVE, catchup=True),
                     _window(None),
                     _window({"kind": "movie"})):
        MainWindow._reopen_live_edge(w)
        assert calls == ["end"]
    w, calls = _window(LIVE, jobs=[{"status": "done"}])
    MainWindow._reopen_live_edge(w)
    assert calls == ["reopen"]
