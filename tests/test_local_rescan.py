"""Rescan in Local files (1.6.1): the button in its header and F5 - a key of
the rebindable kind - read what it shows from the disk again, now. Folders
view lists the folder again, the selected row kept; the library walks its
folder again at once (though one ended moments ago), the list as it is
meanwhile; a walk under way goes on. (What changes on disk also shows by
itself a moment later: tests/test_local_watch.py, tests/test_local_place.py.)"""
from __future__ import annotations

import os

import pytest

import dopeiptv.ui.mw_local as ml
import dopeiptv.ui.mw_local_library as mll
from dopeiptv.core import shortcut_keys
from tests.test_local_responsive import _drain, _Window


@pytest.fixture
def jobs(monkeypatch):
    queued: list = []
    monkeypatch.setattr(ml, "run_async",
                        lambda pool, fn, done, err=None: queued.append(
                            (pool, fn, done, err)))
    monkeypatch.setattr(mll, "run_async", ml.run_async)
    import dopeiptv.providers.cast_bridge as cb
    monkeypatch.setattr(cb, "ffmpeg_path", lambda: None)
    from dopeiptv.core._lazy_requests import requests as _rq

    def offline(*_a, **_k):
        raise OSError("offline in the tests")
    monkeypatch.setattr(_rq, "get", offline, raising=False)
    return queued


def _films(tmp_path):
    root = tmp_path / "Films"
    for n in range(3):
        d = root / f"Film {n} ({2000 + n})"
        d.mkdir(parents=True)
        (d / f"Film {n} ({2000 + n}).mkv").write_bytes(b"x")
    return root


def _names(w):
    return [r.get("name") for r in w.all_items if not r.get("_header")]


def test_folders_view_lists_the_folder_again_the_row_kept(tmp_path, jobs):
    root = tmp_path / "Clips"
    root.mkdir()
    for n in range(3):
        (root / f"clip{n}.mkv").write_bytes(b"x")
    w = _Window()
    w._current_cat = str(root)
    w._load_local_items(str(root))
    _drain(jobs)
    assert _names(w) == ["clip0", "clip1", "clip2"]
    w._local_select_key(str(root / "clip1.mkv"))
    w._local_current_key = lambda: str(root / "clip1.mkv")
    (root / "clip3.mkv").write_bytes(b"x")          # put in since
    w._local_rescan()
    _drain(jobs)
    assert _names(w) == ["clip0", "clip1", "clip2", "clip3"]
    assert w.listw.current == 1                     # the same row selected


def test_a_search_in_the_folder_walks_again(tmp_path, jobs):
    root = tmp_path / "M"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "Bob One.mkv").write_bytes(b"x")
    w = _Window()
    w._current_cat = str(root)
    w.search_text = "bob"
    w._load_local_items(str(root))
    _drain(jobs)
    assert _names(w) == ["Bob One"]
    (root / "Bob Two.mkv").write_bytes(b"x")
    w._local_rescan()
    _drain(jobs)
    assert sorted(_names(w)) == ["Bob One", "Bob Two"]


def test_the_library_walks_again_at_once(tmp_path, jobs):
    """A walk that ended moments ago is not walked again on a visit (two
    minutes); Rescan walks it now, the list left as it is until the walk
    has something to show."""
    root = _films(tmp_path)
    w = _Window()
    w._current_cat = str(root)
    w.settings.setValue("local_view", "series")
    w._load_local_items(str(root))
    _drain(jobs)
    assert len(_names(w)) == 3
    w._load_local_items(str(root))                  # a visit: the cache only
    assert jobs == []
    resets = w.list_model.resets
    new = root / "Film 9 (2009)"
    new.mkdir()
    (new / "Film 9 (2009).mkv").write_bytes(b"x")
    w._local_rescan()
    assert w._local_scan_active and len(jobs) == 1  # walking, at once
    assert w.list_model.resets == resets            # nothing laid out yet
    _drain(jobs)
    assert "Film 9 (2009)" in _names(w)
    assert not w._local_scan_active


def test_a_walk_under_way_goes_on(tmp_path, jobs):
    root = _films(tmp_path)
    w = _Window()
    w._current_cat = str(root)
    w.settings.setValue("local_view", "series")
    w._load_local_items(str(root))
    token = w._local_scan_token
    assert w._local_scan_active
    w._local_rescan()
    assert w._local_scan_token == token and len(jobs) == 1


def test_rescan_is_local_files_own(tmp_path, jobs):
    root = _films(tmp_path)
    w = _Window()
    w._current_cat = str(root)
    w.mode = "live"
    w._local_rescan()
    w.mode = "local"
    w._current_cat = "__add__"
    w._local_rescan()
    assert jobs == []


def test_its_key_acts_only_where_local_files_shows(tmp_path, jobs):
    root = tmp_path / "C"
    root.mkdir()
    w = _Window()
    w._current_cat = str(root)
    asked = []
    w._local_rescan = lambda: asked.append(1)
    w.isActiveWindow = lambda: True
    w._shortcut_local_rescan()
    assert asked == [1]
    for blocker in ("_series_page_showing", "_search_page_showing",
                    "_guide_page_showing"):
        setattr(w, blocker, lambda: True)
        w._shortcut_local_rescan()
        delattr(w, blocker)
    w._tv_on = True
    w._shortcut_local_rescan()
    w._tv_on = False
    w.isActiveWindow = lambda: False                # another window's key
    w._shortcut_local_rescan()
    w.isActiveWindow = lambda: True
    w.mode = "vod"
    w._shortcut_local_rescan()
    assert asked == [1]


def test_f5_is_rescans_rebindable_key():
    assert shortcut_keys.DEFAULTS["local_rescan"] == "F5"
    assert shortcut_keys.LABELS["local_rescan"] == "sc_local_rescan"
    assert shortcut_keys.reserved("F5", "linux") is None
    assert shortcut_keys.effective({})["local_rescan"] == "F5"
    # No other action of the app's own has it.
    assert [sid for sid, key in shortcut_keys.DEFAULTS.items()
            if shortcut_keys.same(key, "F5")] == ["local_rescan"]
    over, lost = shortcut_keys.assign({}, "local_rescan", "Ctrl+R")
    assert shortcut_keys.effective(over)["local_rescan"] == "Ctrl+R"
    assert lost == []


def test_the_window_wires_it_and_the_palette_lists_it_in_local_files(app):
    from PyQt6.QtWidgets import QWidget

    from dopeiptv.i18n import tr
    from dopeiptv.ui import mw_palette, mw_shortcuts
    from unittest.mock import MagicMock
    win = MagicMock()
    cbs = mw_shortcuts._ShortcutsMixin._shortcut_callbacks(win)
    assert cbs["local_rescan"] is win._shortcut_local_rescan
    tips = {sid: attr for sid, attr, _t in
            mw_shortcuts._ShortcutsMixin._KEY_TIPS}
    assert tips["local_rescan"] == "local_rescan_btn"

    class Host(QWidget, mw_palette._PaletteMixin):
        pass

    host = Host()
    host.shortcut_keys = lambda: dict(shortcut_keys.DEFAULTS)
    host._shortcut_callbacks = lambda: {sid: (lambda: None)
                                        for sid, *_ in shortcut_keys.ACTIONS}
    host._player_up = lambda: False
    host.can_cast_playing = lambda: True
    host._menu_actions = {}

    def rescan_rows():
        return [e for e in host._palette_action_entries()
                if e.text == tr("sc_local_rescan")]
    host.mode = "live"
    assert rescan_rows() == []
    host.mode = "local"
    (row,) = rescan_rows()
    assert row.kind == tr("nav_local") and row.keys == "F5"
    host.deleteLater()


def test_its_words_are_in_every_language():
    import json

    from dopeiptv import i18n
    ldir = os.path.join(os.path.dirname(i18n.__file__), "locale")
    for key in ("local_rescan", "local_rescan_tip", "sc_local_rescan"):
        assert key in i18n._STRINGS
    for name in os.listdir(ldir):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(ldir, name), encoding="utf-8") as fh:
            words = json.load(fh)
        for key in ("local_rescan", "local_rescan_tip", "sc_local_rescan"):
            assert words.get(key), (name, key)
        # The tooltip ends in its key, which the window puts in force.
        assert words["local_rescan_tip"].rstrip(")）").endswith("F5"), name


@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])
