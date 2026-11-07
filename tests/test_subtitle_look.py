"""How subtitles look: size, colour and a box behind them, from Settings."""
from __future__ import annotations

from dopeiptv.media.embedded import SUB_COLORS, _sub_look


class _Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)


def _applied(**values) -> dict:
    out: dict = {}
    _sub_look(_Settings(values), out.__setitem__)
    return out


def test_defaults_are_mpvs_own():
    assert _applied() == {
        "sub-scale": 1.0, "sub-color": "#FFFFFF",
        "sub-border-style": "outline-and-shadow",
        "sub-back-color": "#00000000"}


def test_size_colour_and_box():
    out = _applied(sub_scale="150", sub_color="yellow", sub_background="true")
    assert out["sub-scale"] == 1.5
    assert out["sub-color"] == SUB_COLORS["yellow"]
    assert out["sub-border-style"] == "background-box"
    assert out["sub-back-color"] == "#B3000000"


def test_damaged_values_fall_back():
    out = _applied(sub_scale="9000", sub_color="plaid")
    assert out["sub-scale"] == 1.0 and out["sub-color"] == "#FFFFFF"


def test_the_player_menu_changes_the_look_without_touching_the_track():
    from types import SimpleNamespace
    from dopeiptv.media.embedded import EmbeddedPlayer

    class Mpv(dict):
        pass

    m = Mpv(sid=3)
    p = SimpleNamespace(_settings=_Settings(), video=SimpleNamespace(mpv=m))
    p._settings.setValue = p._settings.__setitem__
    EmbeddedPlayer._set_sub_look(p, "sub_color", "yellow")
    EmbeddedPlayer._set_sub_look(p, "sub_background", "true")
    assert p._settings["sub_color"] == "yellow"
    assert m["sub-color"] == SUB_COLORS["yellow"]
    assert m["sub-back-color"] == "#B3000000"
    assert m["sid"] == 3          # a track picked by hand stays picked
