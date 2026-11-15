"""TV mode plays in its own picture: the player's page is shown before the
first stream opens (its video surface makes the mpv core when it is shown),
and a refused play never opens an external mpv window behind the app."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dopeiptv.ui.mw_tv import _TvMixin  # noqa: E402

_SRC = (Path(__file__).resolve().parent.parent / "dopeiptv/ui/main_window.py"
        ).read_text(encoding="utf-8")


class _Shown:
    def __init__(self):
        self.shown = 0
        self.mpv = None

    def show(self):
        self.shown += 1


class _Player(_Shown):
    def __init__(self):
        super().__init__()
        self.video = _Shown()


class _Stack:
    def __init__(self, index):
        self.index = index

    def currentIndex(self):  # noqa: N802 - Qt's name
        return self.index

    def setCurrentIndex(self, i):  # noqa: N802 - Qt's name
        self.index = i


class _Win(_TvMixin):
    def __init__(self, on, index=3):
        self._tv_on = on
        self.player = _Player()
        self._center_stack = _Stack(index)


def test_tv_mode_shows_the_player_before_its_first_stream():
    w = _Win(True)
    w._tv_before_play()
    assert w._center_stack.index == 0 and w.player.shown and w.player.video.shown


def test_nothing_changes_outside_tv_mode_or_once_the_core_is_made():
    w = _Win(False)
    w._tv_before_play()
    assert w._center_stack.index == 3 and not w.player.shown
    w = _Win(True)
    w.player.video.mpv = object()
    w._tv_before_play()
    assert w._center_stack.index == 3 and not w.player.shown


def test_the_playback_path_asks_before_play_and_never_opens_mpv_in_tv_mode():
    asked = _SRC.index('getattr(self, "_tv_before_play", None)')
    plays = _SRC.index("if self.player.play(url, title, start=resume_at,")
    assert asked < plays
    tv = _SRC.index('elif getattr(self, "_tv_on", False):', plays)
    external = _SRC.index('launch_player("mpv", url, title, self)', plays)
    assert tv < external
    assert 'tr("tv_play_refused")' in _SRC[tv:external]
