"""A local DVR must never seek into a hole or outside its current cache."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dopeiptv.core.local_buffer import (
    LIVE_REOPEN_LAG, BufferWindow, LiveLag, buffer_window)
from dopeiptv.media.embedded import EmbeddedPlayer


def state(*ranges):
    return {"seekable-ranges": [{"start": s, "end": e} for s, e in ranges]}


def test_ranges_are_sorted_and_overlaps_merged():
    assert buffer_window(state((30, 60), (0, 40)), 20) == BufferWindow(0, 60)


def test_disconnected_ranges_are_not_presented_as_one_seekable_window():
    assert buffer_window(state((0, 20), (40, 90)), 50) == BufferWindow(40, 90)
    assert buffer_window(state((0, 20), (40, 90)), 30) is None


@pytest.mark.parametrize("raw,position", [
    (None, 10), ({}, 10), ({"seekable-ranges": None}, 10),
    ({"seekable-ranges": [None, {}, {"start": "bad", "end": 2}]}, 10),
    (state((0, float("inf"))), 10), (state((0, 30)), float("nan")),
    (state((0, 30)), None), (state((4, 4)), 4),
])
def test_invalid_or_unready_cache_has_no_bar(raw, position):
    assert buffer_window(raw, position) is None


class Mpv(dict):
    def __init__(self):
        self.playback_time = 70.0
        self.demuxer_cache_state = state((30, 90))
        self.commands = []

    def command(self, *args):
        self.commands.append(args)


def player():
    m = Mpv()
    p = SimpleNamespace(
        video=SimpleNamespace(mpv=m), current_url="http://stream.invalid/live.ts",
        _local_buffer_enabled=True, _local_slider_window=BufferWindow(0, 90))
    p.local_buffer_window = lambda: EmbeddedPlayer.local_buffer_window(p)
    p._seek_local_target = lambda t, w: EmbeddedPlayer._seek_local_target(p, t, w)
    p.playback_position = lambda: m.playback_time
    p._set_mpv = lambda k, v: m.__setitem__(k, v)
    p._reveal_local_buffer = lambda: None
    p._live_lag = LiveLag()
    p.reopened = []
    p.reopen_live = SimpleNamespace(emit=lambda: p.reopened.append(True))
    p.receivers = lambda _signal: 1
    p.go_to_buffer_end = lambda w=None: EmbeddedPlayer.go_to_buffer_end(p, w)
    return p, m


def test_drag_release_rechecks_the_evicted_start_and_clamps_it():
    p, m = player()
    EmbeddedPlayer._seek_local_buffer(p, 2)
    assert m.commands == [("seek", 30.25, "absolute+exact")]


def test_live_uses_newest_cached_packet_and_unpauses_without_reloading():
    p, m = player()
    EmbeddedPlayer._local_go_live(p)
    assert m.commands == [("seek", 89.75, "absolute+exact")]
    assert m["pause"] is False


def test_live_far_behind_the_channel_opens_it_again():
    p, m = player()
    p._live_lag.lag = LIVE_REOPEN_LAG + 1
    EmbeddedPlayer._local_go_live(p)
    assert p.reopened == [True]
    assert m.commands == []


def test_live_without_anyone_to_reopen_still_goes_to_the_buffer_end():
    p, m = player()
    p._live_lag.lag = LIVE_REOPEN_LAG + 1
    p.receivers = lambda _signal: 0
    EmbeddedPlayer._local_go_live(p)
    assert p.reopened == []
    assert m.commands == [("seek", 89.75, "absolute+exact")]


def _feed(lag, clock, end, gop=2.0):
    """The seekable end moves a keyframe interval at a time."""
    return lag.observe(clock, end - (end % gop))


def test_a_buffer_keeping_up_with_the_channel_is_not_behind():
    lag = LiveLag()
    for i in range(240):
        t = i * 0.5
        assert _feed(lag, 1000 + t, 50 + t) < LIVE_REOPEN_LAG / 4


def test_a_reader_that_stopped_falls_behind_by_the_time_it_stood():
    lag = LiveLag()
    for i in range(20):
        lag.observe(1000 + i * 0.5, 50 + i * 0.5)
    # Paused past the network buffer: the end stands for 60 s.
    for i in range(1, 121):
        lag.observe(1009.5 + i * 0.5, 59.5)
    assert 59 <= lag.lag <= 61
    # Reading again: what the server held arrives in a burst, the rest is lost.
    lag.observe(1070, 64.5)
    lag.observe(1070.5, 65)
    assert 54 <= lag.lag <= 56


@pytest.mark.parametrize("jump", [-120.0, 600.0])
def test_a_timestamp_jump_starts_the_measure_again(jump):
    lag = LiveLag()
    for i in range(20):
        lag.observe(1000 + i * 0.5, 50 + i * 0.5)
    for i in range(1, 61):
        lag.observe(1009.5 + i * 0.5, 59.5)
    assert lag.lag >= 29
    lag.observe(1040, 59.5 + jump)
    assert lag.lag == 0
    lag.reset()
    assert lag.lag == 0 and lag.observe(5, 5) == 0


def test_lost_cache_cannot_turn_a_drag_into_a_network_seek():
    p, m = player()
    m.demuxer_cache_state = state((100, 180))
    EmbeddedPlayer._seek_local_buffer(p, 20)
    assert m.commands == []


def test_relative_seek_stays_inside_local_cache():
    p, m = player()
    EmbeddedPlayer._relative_seek(p, -200)
    assert m.commands == [("seek", 30.25, "absolute+exact")]


def test_relative_seek_does_not_escape_a_lost_local_cache():
    p, m = player()
    m.demuxer_cache_state = None
    EmbeddedPlayer._relative_seek(p, -10)
    assert m.commands == []


def test_non_live_and_stopped_players_have_no_local_timeline():
    p, m = player()
    p._local_buffer_enabled = False
    assert p.local_buffer_window() is None
    p._local_buffer_enabled = True
    p.current_url = None
    assert p.local_buffer_window() is None


@pytest.mark.parametrize("ahead", [0.8, 2.0, 4.9])
def test_the_playhead_past_the_last_keyframe_keeps_the_window(ahead):
    # At the live edge mpv's seekable range ends at the last keyframe, behind
    # the playhead: measured 0.8 s on Linux with a 2 s keyframe interval.
    assert buffer_window(state((0, 90)), 90 + ahead) == BufferWindow(0, 90)


def test_a_playhead_far_past_the_range_has_no_window():
    assert buffer_window(state((0, 90)), 96) is None
