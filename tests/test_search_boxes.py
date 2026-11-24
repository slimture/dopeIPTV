"""The three search boxes say what they do (#32): the one over the list
filters the list shown (a folder and its subfolders in Local files,
radio-browser.info under Radio's Search), the sidebar's finds categories by
name or by what is in them - or filters the list in the list sections, Local
files too - and the Search page searches everything."""
from __future__ import annotations

from types import SimpleNamespace

from dopeiptv.i18n import set_language, tr
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.mw_radio import SEARCH


class _Box:
    def __init__(self):
        self.placeholder = self.tip = self.name = ""

    def setPlaceholderText(self, text):
        self.placeholder = text

    def setToolTip(self, text):
        self.tip = text

    def setAccessibleName(self, text):   # a placeholder is no name (ui/a11y.py)
        self.name = text


def _win(mode, **kw):
    w = SimpleNamespace(mode=mode, search=_Box(), cat_search=_Box(),
                        cat_search_btn=_Box(), _local_ctx=None,
                        _local_series=None, settings=SimpleNamespace(
                            value=lambda k, d=None: kw.get(k, d)), **{
                                k: v for k, v in kw.items()
                                if not k.startswith("local_")})
    for n in ("_sync_search_hint", "_sync_cat_search_hint",
              "_local_walk_mode", "_local_view"):
        setattr(w, n, getattr(MainWindow, n).__get__(w))
    w._CAT_FIND = MainWindow._CAT_FIND
    return w


def test_the_box_over_the_list_says_it_filters_the_list():
    set_language("en")
    for mode in ("live", "vod", "series", "fav", "history", "rec", "radio"):
        w = _win(mode, _current_cat="12")
        w._sync_search_hint()
        assert w.search.placeholder == "Filter this list…", mode
        assert w.search.name == w.search.placeholder, mode     # what a screen reader says


def test_in_a_local_folder_it_says_it_searches_the_subfolders():
    set_language("en")
    w = _win("local", _current_cat="/media/MusicVideos")
    w._sync_search_hint()
    assert w.search.placeholder == "Search this folder and its subfolders…"
    # The library's own rows are filtered by name, as any list.
    w = _win("local", _current_cat="/media/MusicVideos", local_view="series")
    w._sync_search_hint()
    assert w.search.placeholder == "Filter this list…"
    w._local_ctx = "/media/MusicVideos/reggae"      # a folder opened from it
    w._sync_search_hint()
    assert w.search.placeholder == tr("local_search_placeholder")


def test_under_radio_search_it_names_the_directory():
    set_language("en")
    w = _win("radio", _current_cat=SEARCH)
    w._sync_search_hint()
    assert w.search.placeholder == tr("radio_search_placeholder")
    w.mode = "live"                     # left Radio: not radio's words still
    w._sync_search_hint()
    assert w.search.placeholder == "Filter this list…"


def test_the_sidebar_box_says_what_it_finds_and_its_toggle_too():
    set_language("en")
    want = {"live": "Find categories by name or channel…",
            "vod": "Find categories by name or movie…",
            "series": "Find categories by name or series…",
            "fav": "Filter this list…", "local": "Filter this list…",
            "history": "Filter this list…"}
    for mode, text in want.items():
        w = _win(mode)
        w._sync_cat_search_hint()
        assert w.cat_search.placeholder == text, mode
        assert w.cat_search_btn.tip == text, mode
        assert w.cat_search.name == w.cat_search_btn.name == text, mode


def test_in_local_files_the_sidebar_box_filters_the_list():
    """It showed there and did nothing."""
    set_language("en")
    shown = []
    rows = [{"name": "Top Song", "_kind": "local", "_key": "a"},
            {"name": "Bob Marley - One Love", "_kind": "local", "_key": "b",
             "_hit": True}]
    w = SimpleNamespace(
        mode="local", series_ctx=None, all_items=rows,
        _current_cat="/media/M", _local_ctx=None, _local_series=None,
        cat_search=SimpleNamespace(text=lambda: "one"),
        search=SimpleNamespace(text=lambda: ""),
        channel_ov=SimpleNamespace(anything_hidden=lambda: False,
                                   anything_renamed=lambda m: False),
        list_model=SimpleNamespace(
            set_items=lambda items, kind: shown.append(list(items))),
        settings=SimpleNamespace(value=lambda k, d=None: d),
        _sorted=lambda x: x, _set_list_status=lambda *a: None,
        _count_text=lambda k, n: str(n))
    for n in ("_run_category_search", "_render_item_search", "_content_kind",
              "_display_name_fn", "_local_walk_mode", "_local_view"):
        setattr(w, n, getattr(MainWindow, n).__get__(w))
    w._run_category_search()
    assert [r["_key"] for r in shown[-1]] == ["b"]

    # A folder's matches from the box over the list are not filtered again
    # by its text: found by their file names, the name could drop them.
    w.search = SimpleNamespace(text=lambda: "bob.marley")
    w.cat_search = SimpleNamespace(text=lambda: "love")
    w._run_category_search()
    assert [r["_key"] for r in shown[-1]] == ["b"]


def test_every_language_has_the_new_words_and_none_of_the_old():
    import json
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "dopeiptv/locale"
    new = ("list_filter_placeholder", "local_search_placeholder",
           "cat_find_live", "cat_find_vod", "cat_find_series",
           "sa_placeholder_all")
    old = ("search_placeholder", "cat_search_placeholder",
           "cat_search_items", "sa_placeholder")
    for f in sorted(root.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        assert all(d.get(k) for k in new), f.name
        assert not any(k in d for k in old), f.name
