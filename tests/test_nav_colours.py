"""A nav entry's colours reach a style sheet only as #rrggbb.

nav_color_<key> and nav_bg_<key> sync and come back with a backup; they
were pasted into the entry's style sheet as they were. A crafted one added
rules of its own (a fake label, a tooltip, a sidebar 800 px wide), and a
text colour that is not #rrggbb ("red") raised while the window was built,
so the app no longer started. Anything but #rrggbb is the theme's own now."""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from dopeiptv.ui.mw_nav import _NavMixin, nav_colour  # noqa: E402

BAD = ('red', 'white', '#12345', '#1234567', '#gggggg', ' #123456', '#123456;',
       'red;} QPushButton#NavBtn{qproperty-text:"Spoofed entry";min-width:777px;',
       None, 123, ["#ffffff"], "")


def test_only_rrggbb_is_a_nav_colour():
    assert nav_colour("#3b5ba5") == "#3b5ba5"
    assert nav_colour("#ABCDEF") == "#ABCDEF"
    for v in BAD:
        assert nav_colour(v) == "", v


class Button:
    def __init__(self) -> None:
        self.sheet, self.active = None, None

    def setStyleSheet(self, s) -> None:  # noqa: N802
        self.sheet = s

    def set_active_colors(self, fill, ink) -> None:
        self.active = (fill, ink)

    def property(self, _name):
        return None


class Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)


class Win(_NavMixin):
    def __init__(self, values: dict) -> None:
        self.settings = Settings(values)
        self.nav_btns = {"live": Button()}


def test_a_bad_colour_is_the_themes_own_and_never_in_the_style_sheet():
    w = Win({"nav_color_live": "red",
             "nav_bg_live": 'red;} QPushButton#NavBtn{qproperty-text:"Spoofed entry";'})
    w._apply_nav_color("live")                 # raised ValueError before
    assert w.nav_btns["live"].sheet == "" and w.nav_btns["live"].active == ("", "")
    w = Win({"nav_color_live": "#ffffff", "nav_bg_live": "white;} X{"})
    w._apply_nav_color("live")
    sheet = w.nav_btns["live"].sheet
    assert "color:#ffffff;" in sheet and "white" not in sheet and "X{" not in sheet
    w = Win({"nav_bg_live": "#203040"})
    w._apply_nav_color("live")
    assert "background:#203040;" in w.nav_btns["live"].sheet
