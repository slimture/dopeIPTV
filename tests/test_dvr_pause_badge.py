"""Pausing a catch-up channel in its rewind buffer is the buffer's pause:
no TIMESHIFT badge (the provider's archive plays no part), and the
timeline reads how far behind live it is from the buffer. Without a
buffer the badge stays as it was."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

from dopeiptv.ui.main_window import MainWindow  # noqa: E402


def _host(dvr: bool):
    badges: list = []
    player = SimpleNamespace(
        set_live_badge=badges.append,
        local_buffer_window=lambda: object() if dvr else None,
        ts_timeline=SimpleNamespace(isVisible=lambda: False))
    h = SimpleNamespace(
        player=player, _playing_catchup=False, _pause_started=None,
        _last_playback={"kind": "live", "item": {"stream_id": 1}},
        _timeshift_days=lambda it: 3, _apply_play_icon=lambda: None,
        _update_ts_timeline=lambda: None, _ts_live_offset=0.0,
        _warn_pause_while_recording=lambda: None)
    h._dvr_buffered = lambda: MainWindow._dvr_buffered(h)
    return h, badges


def test_a_pause_in_the_buffer_is_not_timeshift():
    h, badges = _host(dvr=True)
    MainWindow._on_paused_changed(h, True)
    assert "timeshift" not in badges
    h._pause_started = time.time() - 30
    MainWindow._on_paused_changed(h, False)
    assert badges and badges[-1] is None
    assert h._ts_live_offset == 0.0


def test_without_a_buffer_the_pause_still_says_timeshift():
    h, badges = _host(dvr=False)
    MainWindow._on_paused_changed(h, True)
    assert badges == ["timeshift"]
    h._pause_started = time.time() - 30
    MainWindow._on_paused_changed(h, False)
    assert badges[-1] == "timeshift" and h._ts_live_offset >= 29


def _timeline_host(behind, catchup=False):
    shown: list = []
    player = SimpleNamespace(
        ts_timeline=SimpleNamespace(isVisible=lambda: True),
        local_behind=lambda: behind,
        playback_position=lambda: 30.0,
        set_timeline_segments=lambda segs: None,
        update_timeshift_position=lambda off, title, paused=False:
            shown.append((off, paused)))
    h = SimpleNamespace(
        player=player, _playing_catchup=catchup, _pause_started=None,
        _ts_segment_start=time.time() - 600 if catchup else None,
        _last_playback={"kind": "live", "item": {"stream_id": 1}},
        _ts_depth_min=120, _ts_live_offset=0.0,
        xmltv=SimpleNamespace(programmes_in=lambda it, a, b: []))
    return h, shown


def test_the_timeline_reads_the_buffer_and_never_raises():
    # In the buffer, at live and behind; from mpv's buffer; in the archive.
    for behind in (0.0, 90.0, None):
        h, shown = _timeline_host(behind)
        MainWindow._update_ts_timeline(h)
        assert shown, behind
        assert abs(shown[-1][0] - (behind or 0.0) / 60.0) < 1e-6
    h, shown = _timeline_host(None, catchup=True)
    MainWindow._update_ts_timeline(h)
    assert 9.0 < shown[-1][0] < 10.0
