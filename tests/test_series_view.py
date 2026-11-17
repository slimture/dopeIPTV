"""A series as its page shows it (core/series_view.py): what is up next."""
from __future__ import annotations


def test_the_episode_playing_is_up_next_however_little_of_it_played():
    from dopeiptv.core import series_view as sv
    order = [{"id": "1"}, {"id": "2"}, {"id": "3"}]
    nothing = sv.Progress()
    nxt = sv.next_up(order, lambda e: False, lambda e: nothing)
    assert nxt.episode is order[0]                       # nothing played: the first
    nxt = sv.next_up(order, lambda e: False, lambda e: nothing,
                     playing=lambda e: e["id"] == "2")
    assert nxt.episode is order[1] and nxt.resume and not nxt.first
    # One already watched to its end playing again: the order as before.
    nxt = sv.next_up(order, lambda e: e["id"] == "2", lambda e: nothing,
                     playing=lambda e: e["id"] == "2")
    assert nxt.episode is order[2]
