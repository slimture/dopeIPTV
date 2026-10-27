"""The list filter skips per-row lookups when nothing is hidden or renamed
(80,000 films took a third of a second on the GUI thread) - and still hides
and finds by the user's names when something is."""
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QSettings

from dopeiptv.core.stores import ChannelOverrides
from dopeiptv.ui.main_window import MainWindow

ROWS = [{"stream_id": 1, "name": "Alpha"}, {"stream_id": 2, "name": "Beta"},
        {"stream_id": 3, "name": "Gamma"}]


@pytest.fixture()
def win():
    s = QSettings("dopeiptv-test", "list-filter")
    s.clear()
    shown = []
    w = SimpleNamespace(
        mode="vod", series_ctx=None, all_items=list(ROWS),
        channel_ov=ChannelOverrides(s, "co"), _pending_jump_key=None,
        search=SimpleNamespace(text=lambda: w.text), text="",
        list_model=SimpleNamespace(
            set_items=lambda items, kind: shown.append(list(items))),
        _sorted=lambda x: x, _hide_busy=lambda: None,
        _set_status=lambda *a: None, _count_text=lambda k, n: "",
        shown=shown)
    for n in ("_apply_filter", "_content_kind", "_channel_hidden",
              "channel_display_name", "_display_name_fn", "_search_filter"):
        setattr(w, n, getattr(MainWindow, n).__get__(w))
    w._item_key = MainWindow._item_key
    yield w
    s.clear()


def test_nothing_hidden_or_renamed(win):
    win._apply_filter()
    assert win.shown[-1] == ROWS
    assert win.shown[-1] is not win.all_items     # the model gets its own
    win.text = "et"
    win._apply_filter()
    assert [r["name"] for r in win.shown[-1]] == ["Beta"]


def test_hidden_rows_stay_hidden(win):
    win.channel_ov.update("vod", 2, hidden=True)
    win._apply_filter()
    assert [r["name"] for r in win.shown[-1]] == ["Alpha", "Gamma"]
    win.channel_ov.update("live", 1, hidden=True)  # another kind: no effect
    win._apply_filter()
    assert [r["name"] for r in win.shown[-1]] == ["Alpha", "Gamma"]


def test_a_renamed_row_is_found_by_its_new_name(win):
    win.channel_ov.update("vod", 3, name="Zeta Prime")
    win.text = "zeta"
    win._apply_filter()
    assert [r["stream_id"] for r in win.shown[-1]] == [3]
    assert win._search_filter(ROWS) == [ROWS[2]]
    win.text = "gamma"                 # its provider name no longer matches
    win._apply_filter()
    assert win.shown[-1] == []
