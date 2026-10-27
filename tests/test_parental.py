"""Parental control holds wherever content can be reached or unlocked.

A locked category asked for the PIN when opened from the sidebar, but its
films were one click away on Home's "New movies" shelf, in the guide and in
the guide search, and the lock itself could be lifted from a context menu
without the PIN at all. And switching playlists kept the previous playlist's
hide/lock rules, so the new one was judged by the wrong list.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest
from PyQt6.QtCore import QSettings

from dopeiptv.core.stores import (
    CategoryOverrides, ChannelOverrides, FavoriteStore, ParentalControl,
)
from dopeiptv.ui import main_window as mw
from dopeiptv.ui.main_window import MainWindow


@pytest.fixture()
def settings():
    s = QSettings("dopeiptv-test", "parental")
    s.clear()
    yield s
    s.clear()


def _window(settings, unlocked=False):
    w = SimpleNamespace()
    w.settings = settings
    w.overrides = CategoryOverrides(settings, "category_overrides_a")
    w.channel_ov = ChannelOverrides(settings, "channel_overrides_a")
    w.parental = ParentalControl(settings)
    w.parental.session_unlocked = unlocked
    w._item_key = MainWindow._item_key
    w._channel_hidden = lambda it, kind: MainWindow._channel_hidden(
        w, it, kind)
    w._catalog_filter = lambda mode: MainWindow._catalog_filter(w, mode)
    return w


CATALOG = [
    {"stream_id": 1, "name": "Family film", "category_id": 10},
    {"stream_id": 2, "name": "Adult film", "category_id": "20"},
    {"stream_id": 3, "name": "Hidden film", "category_id": 30},
    {"stream_id": 4, "name": "Hidden single film", "category_id": 10},
]


def test_the_whole_catalogue_leaves_out_locked_and_hidden(settings):
    w = _window(settings)
    w.overrides.update("vod", "20", locked=True)
    w.overrides.update("vod", 30, hidden=True)
    w.channel_ov.update("vod", 4, hidden=True)
    names = [it["name"] for it in MainWindow._visible_catalog(
        w, CATALOG, "vod")]
    assert names == ["Family film"]

    # The PIN entered this session opens the locked category; hidden stays.
    w.parental.session_unlocked = True
    names = [it["name"] for it in MainWindow._visible_catalog(
        w, CATALOG, "vod")]
    assert names == ["Family film", "Adult film"]


def test_a_rule_for_one_kind_does_not_hide_another(settings):
    w = _window(settings)
    w.overrides.update("live", 10, locked=True)
    assert len(MainWindow._visible_catalog(w, CATALOG, "vod")) == 4


def _ctx(settings, pin_ok: bool):
    w = _window(settings)
    w.mode = "vod"
    w.favs = FavoriteStore(settings, "favorites_a")
    w._load_categories = lambda: None
    w._request_unlock = mock.Mock(return_value=pin_ok)
    w._set_category_flag = lambda cid, **f: MainWindow._set_category_flag(
        w, cid, **f)
    return w


@pytest.mark.parametrize("pin_ok", [False, True])
def test_unlocking_a_category_asks_for_the_pin(settings, pin_ok):
    w = _ctx(settings, pin_ok)
    w.overrides.update("vod", "20", locked=True)
    MainWindow._unlock_category(w, "20")
    w._request_unlock.assert_called_once()
    assert w.overrides.is_locked("vod", "20") is (not pin_ok)


@pytest.mark.parametrize("pin_ok", [False, True])
def test_unlocking_a_favorite_folder_asks_for_the_pin(settings, pin_ok):
    w = _ctx(settings, pin_ok)
    w.favs.ensure_group("Late")
    w.favs.set_group_locked("Late", True)
    MainWindow._set_fav_lock(w, "Late", False)
    assert w.favs.is_locked("Late") is (not pin_ok)


@pytest.mark.parametrize("pin_ok", [False, True])
def test_removing_a_locked_folder_asks_for_the_pin(settings, pin_ok):
    w = _ctx(settings, pin_ok)
    w.favs.ensure_group("Late")
    w.favs.set_group_locked("Late", True)
    MainWindow._remove_fav_folder(w, w.favs, "Late")
    assert ("Late" in w.favs.groups) is (not pin_ok)


def test_removing_an_unlocked_folder_needs_no_pin(settings):
    w = _ctx(settings, False)
    w.favs.ensure_group("Kids")
    MainWindow._remove_fav_folder(w, w.favs, "Kids")
    w._request_unlock.assert_not_called()
    assert "Kids" not in w.favs.groups


def test_switching_playlist_loads_that_playlists_rules(settings):
    CategoryOverrides(settings, "category_overrides_a").update(
        "vod", "20", locked=True)
    CategoryOverrides(settings, "category_overrides_b").update(
        "vod", "77", hidden=True)
    w = mock.MagicMock()
    w.settings = settings
    w._resume_settings = settings
    w.overrides = CategoryOverrides(settings, "category_overrides_a")
    w.playlist_store.get.return_value = {"id": "b", "name": "B"}

    def sync(_pool, fn, done, _fail):
        done(fn())

    with mock.patch.object(mw, "make_client"), \
            mock.patch.object(mw, "run_async", sync):
        MainWindow.switch_playlist(w, "b")
    assert w.overrides.key == "category_overrides_b"
    assert w.overrides.is_hidden("vod", "77")
    assert not w.overrides.is_locked("vod", "20")
    assert w.channel_ov.key == "channel_overrides_b"
