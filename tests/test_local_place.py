"""The Local files place (ui/local_place.py, ui/mw_place_local.py) and the
changes on disk it notices by itself (ui/mw_local_watch.py, #34).

Without a window: a card's picture fitted or covering its box, a view of
20,000 cards dressed only as they paint, rows put in and taken out with the
choice kept, the controls on rows of their own on a narrow pane. In the
window: the place where the list was (the list still its source of truth),
its header (the folder's name, "Local files" at the top, the count, Back,
the filter, Rescan naming F5, Folders / Library, the library's Grid /
List remembered), a folder opened and Back, the mouse's back button,
Continue from the resume points played without the resume question, a
file played through the window's path; then a file added, renamed and
removed on disk showing without Rescan, the scroll and the choice kept, a
folder that went away said quietly, a share read again lightly, the
watcher's thread ended with the window."""
import os
import subprocess
import sys
import time

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    a = QApplication.instance() or QApplication([])
    from dopeiptv.ui import theme as th
    th.apply_theme(theme="graphite")
    return a


def _let_go(place):
    """Closed, then gone: a page left open stays the active window for the
    tests after it in this process (deleteLater alone waits for an event
    loop that never comes)."""
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QApplication
    place.close()
    place.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_a_picture_fits_its_box_or_covers_it():
    from dopeiptv.ui.local_place import fits_inside
    assert not fits_inside(200, 300, 2, 3)          # a poster in a poster's box
    assert fits_inside(600, 600, 2, 3)              # a square cover: whole
    assert fits_inside(128, 128, 16, 9)             # a folder's glyph in a row
    assert fits_inside(200, 300, 16, 9)             # a poster in a row's box
    assert not fits_inside(1920, 1080, 16, 9)       # a frame in a row's box
    assert not fits_inside(1920, 1080, 2, 3)        # a frame in a poster's: its middle
    assert not fits_inside(0, 10, 2, 3)


def test_twenty_thousand_cards_are_dressed_only_as_they_paint(app):
    from dopeiptv.ui.local_place import LocalCard, LocalPlace
    place = LocalPlace(None)
    place.resize(900, 700)
    dressed = []

    def dress(card):
        dressed.append(card.key)
        card.line = "x"
    place.view.dress_fn = dress
    t0 = time.perf_counter()
    place.view.set_items([LocalCard(key=f"/f/{i}", title=f"File {i}", data={})
                          for i in range(20_000)])
    place.show()
    for _ in range(5):
        app.processEvents()
    assert time.perf_counter() - t0 < 3.0
    assert 0 < len(set(dressed)) < 200, len(set(dressed))
    # Put in and taken out where they are: the choice kept.
    view = place.view
    view.setCurrentIndex(view.model_.index(5, 0))
    view.insert_items(0, [LocalCard(key="/f/new", title="New", data={})])
    assert view.current_item().key == "/f/5" and view.row_of("/f/new") == 0
    view.remove_rows([0, 1, 2])
    assert view.current_item().key == "/f/5" and view.row_of("/f/5") == 3
    assert view.model_.rowCount() == 19_998
    # Redressed after a change, again only what paints.
    dressed.clear()
    view.redress()
    for _ in range(3):
        app.processEvents()
    assert 0 < len(set(dressed)) < 200
    _let_go(place)


def test_a_narrow_pane_gives_the_controls_rows_of_their_own(app):
    from dopeiptv.ui.local_place import LocalPlace
    place = LocalPlace(None)
    place.header.set_views([("folders", "Folders"), ("series", "Library")], "series")
    place.show_layout_switch(True, "poster")
    place.resize(1200, 600)
    place.show()
    app.processEvents()
    assert not place.is_narrow() and place.filter.parent() is not place._tools
    assert place.layout_switch.isVisible()
    place.resize(520, 600)
    app.processEvents()
    assert place.is_narrow() and place._tools.isVisible()
    assert place.filter.isVisible() and place.header.switch.isVisible()
    assert place.layout_switch.isVisible()
    place.resize(250, 600)                          # the narrowest: Grid / List left out
    app.processEvents()
    assert place.is_narrow() and not place.layout_switch.isVisible()
    assert place.minimumSizeHint().width() <= 250, place.minimumSizeHint()
    place.set_back("Back to " + "A very long folder name " * 6)
    app.processEvents()
    assert place.back.text().count("…") == 1 and place.back.width() <= 250
    place.resize(1200, 600)
    app.processEvents()
    assert not place.is_narrow() and place.layout_switch.isVisible()
    assert not place._tools.isVisible()
    _let_go(place)


_CHILD = r"""
import json, os, shutil, sys, tempfile, threading, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QEvent, QPointF, QSettings, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

tree = sys.argv[1]
_tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _tmp)
import dopeiptv.providers.cast_bridge as cb
cb.ffmpeg_path = lambda: None
from dopeiptv.core._lazy_requests import requests as rq
def offline(*_a, **_k):
    raise OSError("offline in the test")
rq.get = offline
from dopeiptv.core import local_watch as lw
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw_mod, mw_local_watch, mw_place_local
from dopeiptv.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
settings = QSettings(os.path.join(_tmp, "s.ini"), QSettings.Format.IniFormat)
settings.setValue("home_enabled", "false")
settings.setValue("music_art_online", "false")
settings.setValue("local_dirs", json.dumps([tree]))
mw_mod.launch_player = lambda *a, **k: None
w = MainWindow(DemoClient(), settings)
w.cover.resolver = None
asked = []
w._choice_dialog = lambda *a, **k: asked.append(a) or 1
played = []
w._start_playback = lambda url, title, icon, key, kind, **k: played.append(
    (url, kind, w._sp_start_once))
w.resize(1440, 900)
w.show()

def settle(cond, secs=20, what=""):
    end = time.time() + secs
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert cond(), "timed out: " + what

def idle(secs):
    end = time.time() + secs
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)

def on_thread(fn):
    t = threading.Thread(target=fn)
    t.start()
    t.join()

def listed():
    return w._local_listing is None

settle(lambda: w.list_model.rowCount() > 0, what="first list")

# -- the place where the list was ----------------------------------------------------
assert w._show_place("local") is True
settle(lambda: w._place_local_showing() and listed() and w.list_model.rowCount() == 5,
       what="the place")
place = w._lp_page
view = place.view
settle(lambda: view.model_.rowCount() == 5, what="cards")
assert not w.listw.isVisible() and place.isVisible() and w.count_lbl.isVisible()
assert w.nav_btns["local"].isChecked() and w._place == "local"
h = place.header
assert h.title_text() == tr("nav_local"), h.title_text()
assert h.count.text() == " · ".join([os.path.basename(tree), tr("local_n_folders", n=2),
                                     tr("count_files", n=3)]), h.count.text()
assert h.view_key() == "folders" and not place._back_row.isVisible()
assert not place.layout_switch.isVisible() and view.layout_kind == "list"
assert [c.key for c in view.items()] == [r["_key"] for r in w.list_model.items()]
assert "F5" in place.rescan.toolTip() and w._local_rescan_place_btn is place.rescan
assert w.count_lbl.text().endswith(tr("count_files", n=5)), w.count_lbl.text()

# The watch on the folder shown, on a thread of its own.
settle(lambda: w._lw_status()["count"] == 1, what="watching the root")
assert w._lw_status()["target"] == ("folder", os.path.normpath(tree))

# -- a folder opened, and Back ------------------------------------------------------
films = os.path.join(tree, "Films")
card = next(c for c in view.items() if c.key == films)
view.activated.emit(card)
settle(lambda: listed() and w._local_ctx == films and view.model_.rowCount() == 30,
       what="the folder")
assert h.title_text() == "Films" and place._back_row.isVisible()
assert place.back.text() == tr("fp_back_to", name=tr("nav_local"))
settle(lambda: w._lw_status()["target"] == ("folder", films) and w._lw_status()["count"] == 1,
       what="watching the folder")

# Continue: the files in progress under it, from the resume points; played
# from where it got to, without the resume question.
inside = os.path.join(films, "Film 07.mkv")
w.resume.record("local", inside, 600, 3000)
w.resume.record("local", os.path.join(tree, "Other", "x.mkv"), 600, 3000)   # not under it
w._lp_rows_refresh()
assert [c.key for c in place.cont.cards()] == [inside] and place.cont.isVisible()
assert place.cont.cards()[0].line == tr("home_min_left", n=40)
place.cont.activated.emit(place.cont.cards()[0])
assert played[-1] == (inside, "local", (inside, 600.0)), played[-1]
assert asked == [] and w._place_local_showing()
cur = w.list_model.item_at(w.listw.currentIndex().row())
assert cur is not None and cur["_key"] == inside       # its folder's queue: the list

# A file's card plays it through the window's path (a double click, Enter).
other = next(c for c in view.items() if c.key.endswith("Film 03.mkv"))
view.activated.emit(other)
assert played[-1][:2] == (other.key, "local")

# The order for every folder, named on the sort's button: Z-A puts the
# files the other way, folders still first; by name again reads the folder
# in its own order; the choice kept.
assert h.sort_btn.isVisible() and h.sort_key() == "name"
assert [k for k, _w in h._sort_opts] == ["name", "name_desc", "modified", "oldest", "largest", "type"]
names = [os.path.basename(c.key) for c in view.items()]
h._sort_picked("name_desc")
settle(lambda: [os.path.basename(c.key) for c in view.items()] == names[::-1], what="Z-A")
assert settings.value("local_sort") == "name_desc" and h.sort_btn.text() == tr("sort_za")
h._sort_picked("name")
settle(lambda: listed() and [os.path.basename(c.key) for c in view.items()] == names, what="A-Z")
assert w._local_ctx == films

# The mouse's back button: one folder up.
applied = []                    # the watcher's thread has done what it was asked
w._lw._worker.applied.connect(lambda n: applied.append(n))
vp = view.viewport()
pos = QPointF(10, 10)
ev = QMouseEvent(QEvent.Type.MouseButtonPress, pos, vp.mapToGlobal(pos),
                 Qt.MouseButton.BackButton, Qt.MouseButton.BackButton,
                 Qt.KeyboardModifier.NoModifier)
QApplication.sendEvent(vp, ev)
settle(lambda: listed() and w._local_ctx is None and view.model_.rowCount() == 5, what="up")
assert h.title_text() == tr("nav_local") and not place._back_row.isVisible()
assert view.current_item() is not None and view.current_item().key == films  # where it was

# -- changes on disk, without Rescan ---------------------------------------------------
view.focus_key(films)
before = view.current_item().key
resets = []
w.list_model.modelReset.connect(lambda: resets.append(1))
# One folder up is another folder watched - on the watcher's own thread,
# a moment later: a file made before the system watches it is no change.
settle(lambda: w._lw_status()["target"] == ("folder", os.path.normpath(tree))
       and w._lw.watching == (os.path.normpath(tree),) and applied,
       what="the folder up watched")
on_thread(lambda: open(os.path.join(tree, "added.mkv"), "wb").close())
settle(lambda: view.row_of(os.path.join(tree, "added.mkv")) >= 0, 12, "a file added")
assert resets == [], "laid out anew"
assert view.current_item().key == before                # the choice kept
assert w.list_model.rowCount() == 6 and view.model_.rowCount() == 6
assert h.count.text().endswith(tr("count_files", n=4)), h.count.text()
on_thread(lambda: os.rename(os.path.join(tree, "b.mkv"), os.path.join(tree, "renamed.mkv")))
settle(lambda: view.row_of(os.path.join(tree, "renamed.mkv")) >= 0
       and view.row_of(os.path.join(tree, "b.mkv")) < 0, 12, "a file renamed")
on_thread(lambda: os.remove(os.path.join(tree, "added.mkv")))
settle(lambda: view.row_of(os.path.join(tree, "added.mkv")) < 0, 12, "a file removed")
assert resets == [] and view.current_item().key == before
assert [c.key for c in view.items()] == [r["_key"] for r in w.list_model.items()]
# Reading a folder again is no change of its own: nothing more is read.
runs = []
real_run = w._lw_run
w._lw_run = lambda: runs.append(1) or real_run()
idle(4)
assert runs == [], runs

# A folder that goes away: said quietly.
w._local_descend(os.path.join(tree, "Gone"))
settle(lambda: listed() and view.model_.rowCount() == 2, what="the folder to go")
settle(lambda: w._lw_status()["count"] == 1, what="watching it")
on_thread(lambda: shutil.rmtree(os.path.join(tree, "Gone")))
settle(lambda: view.model_.rowCount() == 0, 12, "its rows gone")
settle(lambda: place.note.text() == tr("local_gone") and place._note_box.isVisible(),
       what="the note")
w._local_up()
settle(lambda: listed() and view.model_.rowCount() == 4, what="up again")

# A share sends no events: read again lightly, while the place shows.
mw_local_watch.POLL_TICK_MS = 100
real_plan, real_due = mw_local_watch.plan_job, mw_local_watch.lw.poll_due
mw_local_watch.plan_job = lambda dirs, kind="folder": lw.Plan((), True, "network")
mw_local_watch.lw.poll_due = lambda *a, **k: True
w._lw_sync(force=True)
settle(lambda: w._lw_status()["poll"] and w._lw_status()["count"] == 0, what="no watch")
on_thread(lambda: open(os.path.join(tree, "polled.mkv"), "wb").close())
settle(lambda: view.row_of(os.path.join(tree, "polled.mkv")) >= 0, 12, "read again lightly")
mw_local_watch.plan_job, mw_local_watch.lw.poll_due = real_plan, real_due
w._lw_sync(force=True)
settle(lambda: not w._lw_status()["poll"] and w._lw_status()["count"] == 1, what="watched again")

# -- the library: posters or rows, remembered ---------------------------------------------
h.switch.buttons[1].click()
settle(lambda: not w._local_scan_active and w._local_view() == "series"
       and view.model_.rowCount() > 0, what="the library")
assert place.layout_switch.isVisible() and view.layout_kind == "poster"
assert all(not (c.data or {}).get("_header") for c in view.items())
assert settings.value("local_view") == "series"
settle(lambda: w._lw_status()["target"] == ("library", os.path.normpath(tree)), what="lib watch")
settle(lambda: w._lw_status()["count"] == 2, what="the library's folders watched")
place.layout_switch.buttons[1].click()
assert view.layout_kind == "list" and settings.value(mw_place_local.VIEW_KEY) == "list"
w.switch_mode("live")
settle(lambda: not w._place_local_active() and w.listw.isVisible(), what="TV's list")
settle(lambda: w._lw_status()["count"] == 0 and w._lw_status()["target"] is None,
       what="nothing watched away from it")
w._show_place("local")
settle(lambda: w._place_local_showing() and view.model_.rowCount() > 0, what="back")
assert view.layout_kind == "list" and place.layout_switch.current() == "list"
# A film added to the library: walked again quietly, laid out once it ends.
new = os.path.join(tree, "Films", "Brand New (2024).mkv")
settle(lambda: w._lw_status()["count"] == 2, what="watching again")
on_thread(lambda: open(new, "wb").close())
settle(lambda: any(c.key == new for c in view.items()), 25, "the film walked in")
# ...and walking the library is none either.
settle(lambda: not w._local_scan_active and not w._lw_status()["pending"], what="quiet")
runs.clear()
idle(4)
assert runs == [], runs

# -- the window closes: the watcher's thread ends ------------------------------------------
watcher = w._lw
w._lw_shutdown()
assert w._lw is None and not watcher.running() and not watcher._thread.isRunning()
print("LOCAL_PLACE_OK", flush=True)
os._exit(0)
"""


def _tree(tmp_path) -> str:
    root = tmp_path / "Share"
    films = root / "Films"
    films.mkdir(parents=True)
    for i in range(30):
        (films / f"Film {i:02d}.mkv").write_bytes(b"x")
    gone = root / "Gone"
    gone.mkdir()
    for n in ("one.mkv", "two.mkv"):
        (gone / n).write_bytes(b"x")
    for n in ("a.mkv", "b.mkv", "c.mkv"):
        (root / n).write_bytes(b"x" * 1500)
    return str(root)


def test_the_local_files_place_in_the_window(tmp_path):
    pytest.importorskip("PyQt6")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=str(tmp_path / "c"),
               XDG_DATA_HOME=str(tmp_path / "d"), XDG_CACHE_HOME=str(tmp_path / "k"))
    proc = subprocess.run([sys.executable, "-c", _CHILD, _tree(tmp_path)],
                          capture_output=True, text=True, env=env, cwd=_REPO_ROOT,
                          timeout=240)
    assert "LOCAL_PLACE_OK" in proc.stdout, (
        f"stdout={proc.stdout[-3000:]!r}\nstderr={proc.stderr[-4000:]!r}")


def test_the_root_is_only_a_folder_the_sidebar_has_picked():
    """No folder added yet: another place's category (a TV category "1")
    still the window's is no folder - it was shown as one, and "Add folder"
    was not offered."""
    from types import SimpleNamespace as NS
    from PyQt6.QtCore import Qt
    from dopeiptv.ui.mw_place_local import _PlaceLocalMixin

    def win(cat, picked):
        item = None if picked is None else NS(data=lambda role: picked if role == Qt.ItemDataRole.UserRole else None)
        return NS(_current_cat=cat, cat_list=NS(currentItem=lambda: item))
    root = _PlaceLocalMixin._lp_root
    assert root(win("1", None)) == ""                       # TV's category, no folder
    assert root(win("1", "__add__")) == ""                  # "+ Add folder…" picked
    assert root(win("__add__", "__add__")) == ""
    assert root(win("/media/films", "/media/films")) == "/media/films"
    assert root(win("/media/films", "/media/other")) == ""  # changing over
