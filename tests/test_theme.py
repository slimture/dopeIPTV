"""Unit tests for dopeiptv.ui.theme."""

import pytest

from dopeiptv.ui import theme
from dopeiptv.ui.theme import (
    ACCENTS, P, THEME_ORDER, THEMES, build_style, effective_theme, is_light,
    resolve_accent, rounded, ui_scale_factor,
)


def test_themes_not_empty():
    assert len(THEMES) >= 5


def test_accents_not_empty():
    assert len(ACCENTS) >= 7


def test_palette_has_keys():
    for key in ("bg", "side", "text", "sel", "hover", "muted", "error"):
        assert key in P, f"missing palette key: {key}"


def test_build_style_returns_string():
    qss = build_style()
    assert isinstance(qss, str)
    assert len(qss) > 100


class _Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)


def _luminance(hex_color: str) -> float:
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("key", list(THEMES))
def test_every_theme_has_every_colour_and_readable_text(key):
    need = {k for k, v in THEMES["graphite"].items() if isinstance(v, str)}
    assert need <= set(THEMES[key]), need - set(THEMES[key])
    t = THEMES[key]
    assert _contrast(t["text"], t["bg"]) >= 7, key
    assert _contrast(t["text"], t["sel"]) >= 4.5, key
    assert _contrast(t["muted"], t["bg"]) >= 3, key
    assert is_light(key) == (_luminance(t["bg"]) > 0.5), key
    # A check that worked (✓) or warns (!) reads on the page and on a card.
    for status in ("ok", "warn"):
        assert min(_contrast(t[status], t["bg"]), _contrast(t[status], t["pane"])) >= 3, (key, status)
    # Nerd's own colour is text too (the list, the crumb, the page picked in
    # the sidebar): readable on the page, a card and the sidebar.
    assert min(_contrast(t["nerd"], t[k]) for k in ("bg", "pane", "side")) >= 4.5, key


def test_the_picker_lists_dark_themes_then_light_ones():
    lights = [k for k in THEME_ORDER if is_light(k)]
    assert lights == ["light", "latte", "solarized_light", "sand", "sky"]
    assert THEME_ORDER[-len(lights):] == lights
    assert sorted(THEME_ORDER) == sorted(THEMES)


def test_following_the_desktop_picks_the_light_theme_only_when_it_is_light():
    s = _Settings(theme="nord", theme_light="latte")
    assert effective_theme(s, system_dark=False) == "nord"
    s["theme_follow_system"] = "true"
    assert effective_theme(s, system_dark=False) == "latte"
    assert effective_theme(s, system_dark=True) == "nord"
    s["theme"] = "gone"
    assert effective_theme(s, system_dark=True) == "graphite"


def test_unknown_desktop_mode_keeps_the_chosen_theme(monkeypatch):
    monkeypatch.setattr(theme, "system_is_dark", lambda: None)
    s = _Settings(theme="nord", theme_light="latte", theme_follow_system="true")
    assert effective_theme(s) == "nord"


def test_accent_of_the_theme_and_of_your_own():
    assert resolve_accent("dracula", "theme")[0] == "#BD93F9"
    assert resolve_accent("graphite", "theme")[0] == ACCENTS["blue"][1]
    assert resolve_accent("nord", "custom", "#12ab34")[0] == "#12AB34"
    # A damaged custom colour falls back to the theme's own.
    assert resolve_accent("nord", "custom", "red")[0] == "#88C0D0"
    assert resolve_accent("nord", "gold")[0] == ACCENTS["gold"][1]


def test_apply_theme_reads_accent_and_corners_from_settings():
    try:
        theme.apply_theme(_Settings(theme="kanagawa", accent="custom",
                                    accent_custom="#336699",
                                    corner_style="round"))
        assert P["bg"] == THEMES["kanagawa"]["bg"]
        assert P["accent"] == "#336699" and P["accent_hi"] != "#336699"
        assert "border-radius: 13px" in build_style()      # 8 px x 1.6
    finally:
        theme.apply_theme(_Settings())


def test_corners_scale_every_radius():
    qss = "a { border-radius: 8px; } b { border-radius:12px } c { border-radius: 0px }"
    assert rounded(qss, 1.0) == qss
    assert rounded(qss, 0.0) == ("a { border-radius: 2px; } b { border-radius: 2px }"
                                 " c { border-radius: 0px }")
    assert "border-radius: 19px" in rounded(qss, 1.6)


@pytest.mark.parametrize("value,factor", [
    ("100", None), ("125", 1.25), ("90", 0.9), ("150", 1.5),
    ("300", None), ("abc", None), (None, None)])
def test_interface_size(value, factor):
    assert ui_scale_factor(value) == factor


@pytest.mark.parametrize("accent,ink", [
    ("#4C8DFF", "#FFFFFF"),          # the default blue keeps white text
    ("#88C0D0", theme.ON_ACCENT_DARK),   # Nord's frost
    ("#BD93F9", theme.ON_ACCENT_DARK),   # Dracula's purple
    ("#A7C080", theme.ON_ACCENT_DARK),   # Everforest's green
    ("#E8B339", theme.ON_ACCENT_DARK),   # gold
    ("#1E6FD9", "#FFFFFF"),
])
def test_text_on_the_accent_stays_readable(accent, ink):
    assert theme.on_accent(accent) == ink
    assert _contrast(theme.on_accent(accent), accent) >= 3


@pytest.mark.parametrize("key", [k for k in THEMES if "accent" in THEMES[k]])
def test_every_theme_accent_carries_its_button_text(key):
    accent = THEMES[key]["accent"][0]
    assert _contrast(theme.on_accent(accent), accent) >= 3, key


def test_the_stylesheet_puts_the_accent_text_colour_on_accent_buttons():
    try:
        theme.apply_theme(_Settings(theme="nord", accent="theme"))
        assert P["on_accent"] == theme.ON_ACCENT_DARK
        qss = build_style()
        assert f"background: {P['accent']}; border: none; color: {theme.ON_ACCENT_DARK}" in qss
        assert "color: white" not in qss.split("QPushButton#Primary")[1].split("}")[0]
    finally:
        theme.apply_theme(_Settings())

