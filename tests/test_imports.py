"""Smoke tests: verify every module imports without error."""

import importlib
import pytest


MODULES = [
    "dopeiptv",
    "dopeiptv.client",
    "dopeiptv.epg",
    "dopeiptv.stores",
    "dopeiptv.workers",
    "dopeiptv.recording",
    "dopeiptv.theme",
    "dopeiptv.wakelock",
]

MODULES_NEED_DISPLAY = [
    "dopeiptv.app",
    "dopeiptv.channel_list",
    "dopeiptv.chromecast",
    "dopeiptv.dialogs",
    "dopeiptv.embedded",
    "dopeiptv.main_window",
    "dopeiptv.players",
]


@pytest.mark.parametrize("mod", MODULES)
def test_import(mod):
    importlib.import_module(mod)


@pytest.mark.parametrize("mod", MODULES_NEED_DISPLAY)
def test_import_gui(mod):
    try:
        importlib.import_module(mod)
    except ImportError:
        pytest.skip("display or Qt not available")
