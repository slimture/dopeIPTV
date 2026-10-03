"""Several favorites at once: move them between folders, by menu or by drag.

Filing favorites into folders went one row at a time. Rows can now be
gathered with Ctrl/Shift-click and moved together, dragged onto a folder in
the sidebar, or dragged within a folder to reorder it.

The window half runs in a subprocess: the window embeds a QOpenGLWidget whose
offscreen teardown can abort at interpreter exit, so we assert on the child's
success marker (same pattern as the other window tests).
"""
from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from dopeiptv.core.stores import FAV_DEFAULT_GROUP, FavoriteStore

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _store():
    data: dict = {}
    s = MagicMock()
    s.value = lambda k, d="": data.get(k, d)
    s.setValue = lambda k, v: data.__setitem__(k, v)
    return FavoriteStore(s)


def _ids(store, group):
    return [x["stream_id"] for x in store.items(group)]


def test_move_takes_rows_out_of_the_folder_they_came_from():
    fav = _store()
    for i in (1, 2, 3):
        fav.add("News", {"stream_id": i})
    fav.move([{"stream_id": 1}, {"stream_id": 3}], "Sport", "News")
    assert _ids(fav, "News") == [2]
    assert _ids(fav, "Sport") == [1, 3]


def test_move_from_a_merged_view_leaves_other_folders_alone():
    fav = _store()
    fav.add(FAV_DEFAULT_GROUP, {"stream_id": 1})
    fav.add("Kids", {"stream_id": 1})
    fav.move([{"stream_id": 1}], "Sport")
    assert _ids(fav, FAV_DEFAULT_GROUP) == []
    assert _ids(fav, "Kids") == [1]
    assert _ids(fav, "Sport") == [1]


def test_move_never_duplicates_and_onto_itself_is_a_no_op():
    fav = _store()
    fav.add("Sport", {"stream_id": 1})
    fav.add(FAV_DEFAULT_GROUP, {"stream_id": 1})
    fav.move([{"stream_id": 1}], "Sport")
    assert _ids(fav, "Sport") == [1]
    fav.move([{"stream_id": 1}], "Sport", "Sport")
    assert _ids(fav, "Sport") == [1]


def test_reorder_keeps_unlisted_rows_in_their_slots():
    fav = _store()
    for i in (1, 2, 3, 4, 5):
        fav.add("All", {"stream_id": i})
    # Row 3 is hidden in the view, so only 1, 2, 4, 5 are put in order.
    fav.reorder("All", [5, 1, 2, 4])
    assert _ids(fav, "All") == [5, 1, 3, 2, 4]


def test_reorder_ignores_unknown_and_repeated_ids():
    fav = _store()
    for i in (1, 2, 3):
        fav.add("All", {"stream_id": i})
    fav.reorder("All", [3, 3, 99, 1, 2])
    assert _ids(fav, "All") == [3, 1, 2]
    fav.reorder("Nope", [1])
    assert "Nope" not in fav.groups


_CHILD = r"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QItemSelectionModel, QSettings, Qt
from PyQt6.QtWidgets import QAbstractItemView, QApplication, QMenu

from dopeiptv.core.stores import FAV_DEFAULT_GROUP
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
settings = QSettings("dopeiptv-test", "fav-bulk")
settings.clear()
w = MainWindow(DemoClient(), settings)
for i in range(1, 6):
    w.favs.add(FAV_DEFAULT_GROUP, {"stream_id": i, "name": f"Ch {i}"})
w.favs.ensure_group("Sport")
w.favs.ensure_group("News")
w.movie_favs.add(FAV_DEFAULT_GROUP, {"stream_id": 77, "name": "Film"})


def ids(group):
    return [x["stream_id"] for x in w.favs.items(group)]


def open_cat(data):
    for r in range(w.cat_list.count()):
        if w.cat_list.item(r).data(Qt.ItemDataRole.UserRole) == data:
            w.cat_list.setCurrentRow(r)
            app.processEvents()
            return
    raise AssertionError(f"no sidebar row {data!r}")


def select_rows(rows):
    sm = w.listw.selectionModel()
    sm.clearSelection()
    for r in rows:
        sm.select(w.list_model.index(r),
                  QItemSelectionModel.SelectionFlag.Select
                  | QItemSelectionModel.SelectionFlag.Rows)


def shown():
    return [w.list_model.item_at(r)["stream_id"]
            for r in range(w.list_model.rowCount())]


w.switch_mode("fav")
app.processEvents()
assert (w.listw.selectionMode()
        == QAbstractItemView.SelectionMode.ExtendedSelection)
assert w.listw.dragEnabled()
open_cat(("chan", None))
assert shown() == [1, 2, 3, 4, 5], shown()

# Zapping sets the current row in code while Ctrl is held (Ctrl+Right); that
# must select the one row, never toggle it into a multi-selection.
cmd = w.listw.selectionCommand(w.list_model.index(1), None)
assert cmd & QItemSelectionModel.SelectionFlag.ClearAndSelect

# The context menu acts on the whole selection and offers "Move to".
menus = []
QMenu.exec = lambda self, *a, **k: menus.append(self)
select_rows([0, 2])
w._context_menu(w.listw.visualRect(w.list_model.index(2)).center())
assert len(w.listw.selectionModel().selectedRows()) == 2, \
    "right-click dropped the multi-selection"
texts = [a.text() for a in menus[-1].actions()]
assert any(t.endswith("(2)") for t in texts), texts
subs = [a.menu().title() for a in menus[-1].actions() if a.menu()]
assert tr("ctx_move_to") + " (2)" in subs, subs
assert tr("ctx_add_to_folder") + " (2)" in subs, subs
batch = w._fav_batch(2, w.list_model.item_at(2), "fav")
assert [x["stream_id"] for x in batch] == [1, 3], batch

w._fav_move("chan", batch, "Sport")
app.processEvents()
assert ids("Sport") == [1, 3], ids("Sport")
assert ids(FAV_DEFAULT_GROUP) == [2, 4, 5], ids(FAV_DEFAULT_GROUP)

# Drag two rows from Sport onto News in the sidebar.
open_cat(("chan", "Sport"))
assert shown() == [1, 3], shown()
select_rows([0, 1])
label = w._fav_drag_start([0, 1])
assert label and "+1" in label, label
assert not w._fav_drop_allowed(("chan", "Sport"))     # where they are
assert not w._fav_drop_allowed(("movie", None))       # wrong kind
assert w._fav_drop_allowed(("chan", "News"))
w._fav_drop_on_folder(("chan", "News"))
app.processEvents()
w._fav_drag_finish()
assert ids("News") == [1, 3] and ids("Sport") == [], (ids("News"),
                                                      ids("Sport"))

# Drag within a folder to reorder it; the moved row stays selected.
for i in (2, 4, 5):
    w.favs.move([{"stream_id": i, "name": f"Ch {i}"}], "News")
open_cat(("chan", "News"))
assert shown() == [1, 3, 2, 4, 5], shown()
w._fav_drag_start([3])                       # "Ch 4"
assert w._fav_can_reorder()
w._fav_reorder_drop(0)
app.processEvents()
w._fav_drag_finish()
assert ids("News") == [4, 1, 3, 2, 5], ids("News")
assert shown() == [4, 1, 3, 2, 5], shown()
sel = [ix.row() for ix in w.listw.selectionModel().selectedRows()]
assert sel == [0], sel

# A sorted list is not the stored order: no reorder there.
w.settings.setValue(w._sort_setting_key(), "alpha_desc")
w._fav_drag_start([0])
assert not w._fav_can_reorder()
w._fav_drag_finish()

# Qt flips dragging with the view mode (on for a poster grid, off for a
# list); ours follows the section instead.
from PyQt6.QtWidgets import QListView
w.listw.setViewMode(QListView.ViewMode.IconMode)
assert w.listw.dragEnabled()
w.listw.setViewMode(QListView.ViewMode.ListMode)
assert w.listw.dragEnabled()

# Outside Favorites nothing can be dragged, but rows can still be gathered.
w.switch_mode("live")
app.processEvents()
assert not w.listw.dragEnabled()
w.listw.setViewMode(QListView.ViewMode.IconMode)
assert not w.listw.dragEnabled(), "a poster could be dragged loose again"
w.listw.setViewMode(QListView.ViewMode.ListMode)
assert (w.listw.selectionMode()
        == QAbstractItemView.SelectionMode.ExtendedSelection)
assert w._fav_drag_start([0]) is None

print("FAV_BULK_OK")
"""


def test_window_moves_and_reorders_favorites():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD], capture_output=True, text=True,
        env=env, cwd=_REPO_ROOT, timeout=180)
    assert "FAV_BULK_OK" in proc.stdout, (
        f"favorites bulk checks failed\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-3000:]!r}")
