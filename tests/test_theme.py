"""Unit tests for dopeiptv.ui.theme."""

from dopeiptv.ui.theme import ACCENTS, P, THEMES, build_style


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
