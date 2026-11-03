"""A local DVR must never seek into a hole or outside its current cache."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dopeiptv.core.local_buffer import BufferWindow, buffer_window
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
