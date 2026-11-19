"""A kids profile turns Nerd on only where it changes Settings: with a
grown-up's PIN. About's seven taps turned it on from the Help menu's About
too, and the servers, webhooks and MQTT set up before came back with it."""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from dopeiptv.core import nerd  # noqa: E402
from dopeiptv.ui.mw_settings import _SettingsMixin  # noqa: E402


class Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value) -> None:  # noqa: N802
        self[key] = value


class Win(_SettingsMixin):
    def __init__(self, kids: bool) -> None:
        self.settings = Settings()
        self.kids = kids
        self.toasts: list = []

    def _profile_kids(self) -> bool:
        return self.kids

    def _show_toast(self, text, ms) -> None:
        self.toasts.append(text)


def test_seven_taps_turn_nerd_on_for_a_kids_profile_only_over_settings(monkeypatch):
    monkeypatch.setattr(nerd, "install", lambda _s: None)
    w = Win(kids=True)
    w._unlock_nerd()                            # Help > About
    assert "nerd_enabled" not in w.settings and w.toasts == []
    w._unlock_nerd(in_settings=True)            # About over Settings: the PIN given
    assert w.settings["nerd_enabled"] == "true"
    grown = Win(kids=False)
    grown._unlock_nerd()
    assert grown.settings["nerd_enabled"] == "true" and len(grown.toasts) == 1
