"""A running cast as the media keys, the phone remote and the remote API see
and drive it (ui/mw_cast.py _cast_media, _cast_command)."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")


class _Hub:
    def __init__(self, bridged=False, duration=0.0):
        self.active = object()
        self.duration = duration
        self._bridged = bridged

    def position(self):
        return 600.0

    def bridged(self):
        return self._bridged


def _win(live=True, bridged=False, airplay=False, paused=False):
    from datetime import datetime

    from dopeiptv.ui.mw_cast import _CastMixin

    calls = []

    class Win(_CastMixin):
        def __init__(self):
            self.cast = _Hub(bridged, 0.0 if live else 5400.0)
            self._cast_device = "Living room TV"
            self._cast_ctx = {"sid": 7 if live else None, "title": "Rapport" if live else "Sintel",
                              "kind": "live" if live else "movie", "item": {"stream_id": 7},
                              "key": "k"}
            self._last_playback = {"icon_url": None}
            self._cast_paused_at = datetime.now() if paused else None
            self._cast_level = 40
            self._cast_muted = False

        def _cast_on_airplay(self):
            return airplay

        def _toggle_cast_pause(self):
            calls.append("pause")

        def _end_cast(self, why):
            calls.append("end")

        def _cast_seek(self, to):
            calls.append(("seek", to))

        def _cast_volume(self, level):
            calls.append(("volume", level))

        def _toggle_cast_mute(self):
            calls.append("mute")

    return Win(), calls


def test_what_the_tv_plays():
    w, _ = _win(live=True, bridged=False)
    m = w._cast_media()
    assert m["device"] == "Living room TV" and m["title"] == "Rapport" and m["kind"] == "live"
    assert m["length"] == 0.0 and m["position"] == 0.0
    assert not m["can_pause"]                # a broadcast straight to the TV holds nothing
    assert m["has_volume"] and m["volume"] == 0.4
    film, _ = _win(live=False, paused=True)
    m = film._cast_media()
    assert m["length"] == 5400.0 and m["position"] == 600.0 and m["paused"] and m["can_pause"]
    film._cast_device = None                 # the cast is over
    assert film._cast_media() is None
    tv, _ = _win(airplay=True)
    assert not tv._cast_media()["has_volume"]    # an Apple TV's volume is its own remote's


def test_what_the_keys_do_on_the_tv():
    w, calls = _win(live=False)
    assert w._cast_command("play", None, w._cast_media()) and calls == []    # playing already
    assert w._cast_command("pause", None, w._cast_media()) and calls == ["pause"]
    assert w._cast_command("seek", 9000.0, w._cast_media())                  # past the end
    assert w._cast_command("set_position", -5.0, w._cast_media())
    assert calls[1:] == [("seek", 5400.0), ("seek", 0.0)]
    assert w._cast_command("volume", 0.7, w._cast_media()) and calls[-1] == ("volume", 0.7)
    assert w._cast_command("mute", None, w._cast_media()) and calls[-1] == "mute"
    assert w._cast_command("stop", None, w._cast_media()) and calls[-1] == "end"
    live, calls = _win(live=True, bridged=False)
    assert not live._cast_command("playpause", None, live._cast_media())     # cannot hold
    assert not live._cast_command("seek", 10.0, live._cast_media())          # no length
    assert calls == []
    tv, calls = _win(airplay=True)
    assert not tv._cast_command("volume", 0.5, tv._cast_media())
    assert not tv._cast_command("mute", None, tv._cast_media())
    assert calls == []
