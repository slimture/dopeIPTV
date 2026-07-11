"""Autoplay-next-episode logic (MainWindow._on_player_finished).

Exercises the pure decision logic with a fake window so we can assert what
gets played without a real player or provider. Mirrors how the app wires the
embedded player's finished() signal to episode autoplay.
"""
from types import SimpleNamespace

import pytest

from dopeiptv.ui.main_window import MainWindow


class _FakeSettings:
    def __init__(self, values):
        self._v = values

    def value(self, key, default=None):
        return self._v.get(key, default)


EPISODES = [
    {"id": 1, "container_extension": "mp4", "name": "S1 E1"},
    {"id": 2, "container_extension": "mp4", "name": "S1 E2"},
    {"id": 3, "container_extension": "mp4", "name": "S1 E3"},
]


def _make(autoplay="true", mode="embedded", last=None):
    f = SimpleNamespace()
    f.settings = _FakeSettings({"autoplay_next_episode": autoplay})
    f._last_playback = last
    f.series_ctx = {"series_id": 7}
    f.played = []
    f.marked = []
    f._save_resume_position = lambda: None
    f._maybe_auto_mark_watched = (
        lambda: f.marked.append(f._last_playback.get("key")))
    f.playback_mode = lambda: mode
    f._item_key = lambda e: e.get("id")
    f.client = SimpleNamespace(
        episode_url=lambda i, ext: f"http://ep/{i}.{ext}")

    def start(url, title, icon, key, kind, record=True, item=None):
        f.played.append((title, key, kind))

    f._start_playback = start
    for name in ("_autoplay_next_episode", "_next_episode_item",
                 "_has_next_episode", "_advance_to_next_episode"):
        setattr(f, name, getattr(MainWindow, name).__get__(f))
    return f


def _last(idx):
    return {"kind": "episode", "key": EPISODES[idx]["id"],
            "item": EPISODES[idx], "series_ctx": {"series_id": 7},
            "ep_queue": EPISODES, "ep_index": idx}


def test_autoplays_next_episode_and_marks_finished():
    f = _make(last=_last(0))
    MainWindow._on_player_finished(f)
    assert f.played == [("S1 E2", 2, "episode")]
    assert 1 in f.marked


def test_advances_through_the_season():
    f = _make(last=_last(1))
    MainWindow._on_player_finished(f)
    assert f.played == [("S1 E3", 3, "episode")]


def test_last_episode_marks_but_does_not_autoplay():
    f = _make(last=_last(2))
    MainWindow._on_player_finished(f)
    assert f.played == []
    assert 3 in f.marked


def test_setting_off_disables_autoplay():
    f = _make(autoplay="false", last=_last(0))
    MainWindow._on_player_finished(f)
    assert f.played == []
    assert 1 in f.marked          # still marked as watched


def test_movie_finish_ignored_by_autoplay():
    f = _make(last={"kind": "movie", "key": 99})
    MainWindow._on_player_finished(f)
    assert f.played == []
    assert f.marked == []


def test_external_mode_does_not_autoplay_in_app():
    f = _make(mode="external", last=_last(0))
    MainWindow._on_player_finished(f)
    assert f.played == []
    assert 1 in f.marked


def test_queue_override_carried_forward():
    f = _make(last=_last(0))
    MainWindow._on_player_finished(f)
    assert f._ep_queue_override is EPISODES
    assert f._ep_index_override == 1


@pytest.mark.parametrize("last", [None, {}, {"kind": "live"}])
def test_no_op_when_nothing_playable(last):
    f = _make(last=last)
    MainWindow._on_player_finished(f)
    assert f.played == []


def test_manual_next_button_skips_without_waiting():
    # The player's 'next episode' button advances even mid-episode, and does
    # NOT force a watched mark (that's left to _start_playback's threshold).
    f = _make(last=_last(0))
    MainWindow._play_next_episode(f)
    assert f.played == [("S1 E2", 2, "episode")]
    assert f.marked == []


def test_manual_next_on_last_episode_is_noop():
    f = _make(last=_last(2))
    MainWindow._play_next_episode(f)
    assert f.played == []


def test_has_next_episode_reflects_position():
    f = _make(last=_last(0))
    assert f._has_next_episode() is True
    f = _make(last=_last(2))
    assert f._has_next_episode() is False
