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


def test_the_series_name_comes_off_however_many_times_it_is_there():
    from dopeiptv.core.series_view import episode_title, without_series
    name = "Jorden runt på 6 steg"
    assert without_series(f"{name} · {name} · {name} · S2 * E3", name) == "S2 * E3"
    assert without_series(f"{name} - S02E03", name) == "S02E03"
    assert without_series("Showtime - Pilot", "Show") == "Showtime - Pilot"    # a word of its own
    assert without_series(name, name) == name                                  # nothing after it
    assert without_series("x", "") == "x"
    ep = {"title": f"S2 * E3 - {name} - S02E03", "season": "2", "episode_num": 3}
    assert episode_title(ep, name) == ""                                       # nothing of its own
    ep = {"title": f"{name} - S02E03 - {name} - Kairo", "episode_num": 3}
    assert episode_title(ep, name) == "Kairo"
    # Bounded: a name a hundred times over is still quick, and comes off its bound's worth.
    many = f"{name} · " * 100 + "Kairo"
    assert without_series(many, name).startswith(name)
