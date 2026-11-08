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
        _update_ts_timeline=lambda: None, _ts_live_offset=0.0)
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
