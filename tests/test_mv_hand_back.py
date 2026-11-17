"""Multiview closed by the person: the cell they followed - the one heard -
goes on in the window (ui/mw_multiview.py), a live channel as a zap, a film
where the cell got to; never while the window plays or the app is closing."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")

from dopeiptv.ui.mw_multiview import _MultiviewMixin  # noqa: E402


class _Mpv:
    def __init__(self, duration):
        self.duration = duration


class _Video:
    def __init__(self, duration):
        self.mpv = _Mpv(duration)


class _Cell:
    def __init__(self, url, muted=True, duration=None, item=None, client="c", pos=0.0):
        self.url = url
        self._live_url = url
        self.title = f"T {url}"
        self._item = item or {"stream_id": 7, "name": f"N {url}"}
        self._client = client
        self._ts_capable = False
        self.video = _Video(duration)
        self._muted = muted
        self._pos = pos

    def is_muted(self):
        return self._muted

    def resume_pos(self):
        return self._pos


class _Win:
    def __init__(self, cells, focused=None):
        self.cells = cells
        self._focused = focused


class _Player:
    current_url = None


class _Host(_MultiviewMixin):
    def __init__(self, visible=True):
        self.player = _Player()
        self.client = "c"
        self._visible = visible
        self.calls = []
        self._sp_start_once = None

    def isVisible(self):
        return self._visible

    def play_live_channel(self, it):
        self.calls.append(("live", it["stream_id"]))

    def _start_playback(self, url, title, icon, key, kind, item=None):
        self.calls.append(("start", url, kind, self._sp_start_once))

    def _item_key(self, it):
        return it.get("stream_id")

    def _play_kind_for(self, it):
        return "episode"


def test_the_cell_heard_is_picked_else_the_focused_else_the_first():
    h = _Host()
    a, b, c = _Cell("a"), _Cell("b", muted=False), _Cell("c")
    assert h._mv_hand_back_pick(_Win([a, b, c], focused=a))["url"] == "b"
    b._muted = True
    assert h._mv_hand_back_pick(_Win([a, b, c], focused=c))["url"] == "c"
    assert h._mv_hand_back_pick(_Win([_Cell(None), b, c]))["url"] == "b"
    assert h._mv_hand_back_pick(_Win([_Cell(None)])) is None
    h.player.current_url = "playing"                # the window plays: nothing
    assert h._mv_hand_back_pick(_Win([a, b, c], focused=a)) is None


def test_a_live_cell_zaps_a_film_goes_on_where_it_got_to():
    h = _Host()
    h._mv_back = h._mv_hand_back_pick(_Win([_Cell("a", muted=False)]))
    h._mv_hand_back_due()
    assert h.calls == [("live", 7)]
    film = _Cell("f", muted=False, duration=5400.0, item={"stream_id": 9, "_kind": "episode"},
                 pos=1234.0)
    h._mv_back = h._mv_hand_back_pick(_Win([film]))
    h._mv_hand_back_due()
    assert h.calls[-1] == ("start", "f", "episode", (9, 1234.0)) and h._sp_start_once is None
    other = _Cell("o", muted=False, client="another source")
    h._mv_back = h._mv_hand_back_pick(_Win([other]))
    h._mv_hand_back_due()
    assert h.calls[-1] == ("start", "o", "live", None)


def test_nothing_while_the_app_closes_or_something_started_meanwhile():
    h = _Host(visible=False)
    h._mv_back = h._mv_hand_back_pick(_Win([_Cell("a", muted=False)]))
    h._mv_hand_back_due()
    h2 = _Host()
    h2._mv_back = h2._mv_hand_back_pick(_Win([_Cell("a", muted=False)]))
    h2.player.current_url = "started"
    h2._mv_hand_back_due()
    assert h.calls == [] and h2.calls == []
