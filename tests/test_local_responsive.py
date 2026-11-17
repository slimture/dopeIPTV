"""Local files never holds the window's thread on the disk (1.6.1).

A search, the switch between the library and Folders view and a big folder
froze the window: each search match's folder, each subfolder of a listing
and each music shelf's albums were read on the window's thread for their
album art and covers - a network round trip each on a share. Here every job
the section sends to a pool is run apart from the callbacks that come back
to the window, and a read of the folder searched or listed from those
callbacks fails the test. The rest: the search and a folder's listing go in
slices on threads of their own, their rows are put in as they come (never a
reset under the user), a file played meanwhile still plays on through its
whole folder, and the library's cache is kept in memory and written in
order off the window's thread.
"""
from __future__ import annotations

import builtins
import json
import os

import pytest

import dopeiptv.ui.mw_local as ml
from dopeiptv.core.local_listing import FolderListing
from tests.test_local_section import _Stub


class _Guard:
    """Fails on any read of *root* while the window's side runs: the jobs
    the section hands to a pool run with it lifted, as on the pool."""

    def __init__(self, monkeypatch, root: str) -> None:
        self.root = root
        self.gui = True
        self.hits: list[str] = []
        real_scandir, real_listdir = os.scandir, os.listdir
        real_stat, real_open = os.stat, builtins.open

        def watch(fn):
            def wrapped(p=".", *a, **k):
                try:
                    path = os.fspath(p)
                except TypeError:
                    path = ""
                if self.gui and isinstance(path, str) \
                        and path.startswith(self.root):
                    self.hits.append(f"{fn.__name__}({path})")
                return fn(p, *a, **k)
            wrapped.__name__ = fn.__name__
            return wrapped
        monkeypatch.setattr(os, "scandir", watch(real_scandir))
        monkeypatch.setattr(os, "listdir", watch(real_listdir))
        monkeypatch.setattr(os, "stat", watch(real_stat))
        monkeypatch.setattr(builtins, "open", watch(real_open))


@pytest.fixture
def jobs(monkeypatch):
    """The section's pool jobs, queued: (pool, job, done, fail)."""
    queued: list = []
    monkeypatch.setattr(ml, "run_async",
                        lambda pool, fn, done, err=None: queued.append(
                            (pool, fn, done, err)))
    # No ffmpeg thumbnails, no network: neither is what is measured here.
    import dopeiptv.providers.cast_bridge as cb
    monkeypatch.setattr(cb, "ffmpeg_path", lambda: None)
    from dopeiptv.core._lazy_requests import requests as _rq

    def offline(*_a, **_k):
        raise OSError("offline in the tests")
    monkeypatch.setattr(_rq, "get", offline, raising=False)
    return queued


def _drain(queued, guard: _Guard | None = None, limit: int = 2000) -> None:
    """Every job on the pool's side, its callback on the window's."""
    n = 0
    while queued and n < limit:
        n += 1
        _pool, fn, done, err = queued.pop(0)
        if guard is not None:
            guard.gui = False
        try:
            result = fn()
        except Exception as e:      # noqa: BLE001 - as run_async hands it on
            if guard is not None:
                guard.gui = True
            if err:
                err(e)
            continue
        if guard is not None:
            guard.gui = True
        done(result)
    assert not queued, "the jobs did not run out"


class _Model:
    """The list model's surface the section uses, counting resets."""

    def __init__(self) -> None:
        self.items: list = []
        self.resets = 0
        self.inserts = 0

    def set_items(self, items, _kind) -> None:
        self.items = items
        self.resets += 1

    def insert_items(self, at, items) -> None:
        if items:
            self.items[at:at] = items
            self.inserts += 1

    def append_items(self, items) -> None:
        self.insert_items(len(self.items), items)

    def rowCount(self) -> int:              # noqa: N802 - Qt's name
        return len(self.items)

    def item_at(self, row):
        return self.items[row] if 0 <= row < len(self.items) else None

    def index(self, row):
        return row

    def refresh_all(self) -> None:
        pass


class _View:
    def __init__(self) -> None:
        self.current = None

    def setCurrentIndex(self, ix) -> None:  # noqa: N802 - Qt's name
        self.current = ix

    def scrollTo(self, _ix) -> None:        # noqa: N802 - Qt's name
        pass

    def currentIndex(self):                 # noqa: N802 - Qt's name
        return type("I", (), {"row": lambda _s: -1})()


class _Window(_Stub):
    def __init__(self) -> None:
        super().__init__()
        self.list_model = _Model()
        self.listw = _View()
        self.all_items = []

    def _render_rows(self, rows, kind, empty_msg=None):
        super()._render_rows(rows, kind, empty_msg)
        self.list_model.set_items(rows, kind)

    def _count_text(self, _kind, n):
        return f"{n} rows"

    def _save_library_cache(self, root, series, movies, collections=None):
        self._libcache = {root: {
            "series": series, "movies": movies,
            "collections": collections or {},
            "albums": dict(getattr(self, "_local_album_index", {}) or {})}}


def _library(tmp_path):
    """Music with tracks in album folders (what the album art reads), film
    folders, and one big folder."""
    root = tmp_path / "Share"
    for a in range(12):
        album = root / "Music" / f"Artist {a} - Album {a}"
        album.mkdir(parents=True)
        for t in range(1, 5):
            (album / f"{t:02d} Song {a}-{t}.mp3").write_bytes(b"ID3")
    for f in range(30):
        film = root / f"Film Name {f:02d} ({1990 + f})"
        film.mkdir(parents=True)
        (film / f"Film Name {f:02d} ({1990 + f}).mkv").write_bytes(b"x")
    big = root / "Clips"
    for d in range(8):
        (big / f"Day {d}").mkdir(parents=True)
    for c in range(120):
        (big / f"Clip {c:03d}.mkv").write_bytes(b"x")
    return str(root)


def test_a_search_reads_nothing_on_the_windows_thread(tmp_path, monkeypatch,
                                                      jobs):
    root = _library(tmp_path)
    guard = _Guard(monkeypatch, root)
    w = _Window()
    w._current_cat = root
    w.search_text = "song"                  # a match in every album folder
    w._load_local_items(root)
    _drain(jobs, guard)
    assert len(w.rendered) == 48
    assert guard.hits == []


def test_the_view_switch_and_a_big_folder_read_nothing_on_it(tmp_path,
                                                            monkeypatch, jobs):
    root = _library(tmp_path)
    guard = _Guard(monkeypatch, root)
    w = _Window()
    w._current_cat = root
    w.settings.setValue("local_view", "series")
    w._load_local_items(root)               # the library, walked
    _drain(jobs, guard)
    assert any(r.get("_kind") == "localalbum" for r in w.rendered)
    w._local_toggle_view()                  # "View: library" -> folders
    _drain(jobs, guard)
    names = [r["name"] for r in w.rendered]
    assert names[:2] == ["Clips", "Film Name 00 (1990)"]
    w._local_descend(os.path.join(root, "Clips"))
    _drain(jobs, guard)
    assert len(w.rendered) == 128
    w._local_ctx = None
    w._local_toggle_view()                  # and back to the library
    _drain(jobs, guard)
    assert any(r.get("_kind") == "localalbum" for r in w.rendered)
    assert guard.hits == []


def test_the_search_and_a_listing_run_on_their_own_threads(tmp_path, jobs):
    """Not on the shared pool, where they waited behind the thumbnails, tag
    reads and art lookups their own rows set off."""
    root = _library(tmp_path)
    w = _Window()
    w._current_cat = root
    w._load_local_items(root)
    assert jobs[0][0] is w._local_walk_pool()
    _drain(jobs)
    w.search_text = "clip"
    w._load_local_items(root)
    assert jobs[0][0] is w._local_walk_pool() and w._local_walk is not None
    assert w._local_walk_pool() is not w.pool
    assert w._local_walk_pool().maxThreadCount() == 2


def test_a_folders_rows_come_in_slices_in_its_order(tmp_path, monkeypatch,
                                                    jobs):
    """One entry a slice, each slice painted: one layout for the first rows,
    then rows put in - the folders after the folders, the files after the
    files - the folder's order whole at the end, as listing it at once."""
    root = tmp_path / "Mixed"
    for d in ("b-dir", "A-dir", "z-dir"):
        (root / d).mkdir(parents=True)
    for f in ("c.mkv", "A.mp4", "b.mp3", "notes.txt"):
        (root / f).write_bytes(b"x")
    monkeypatch.setattr(FolderListing, "ASKED", 0.0)   # files come at once
    w = _Window()
    w._LIST_FIRST = w._SEARCH_SLICE = w._SEARCH_PAINT = 0.0
    w._current_cat = str(root)
    w._load_local_items(str(root))
    seen = []
    while jobs:
        _drain([jobs.pop(0)])           # one job, its callback
        seen.append(len(w.list_model.items))
    names = [os.path.basename(r["_path"]) for r in w.list_model.items]
    assert names == ["A-dir", "b-dir", "z-dir", "A.mp4", "b.mp3", "c.mkv"]
    assert w.list_model.resets == 1 and w.list_model.inserts >= 3
    assert seen == sorted(seen) and len(set(seen)) > 3   # it grew, slice by slice
    assert w._local_listing is None


def test_the_selection_comes_back_as_the_folder_is_read(tmp_path, jobs):
    """Backing out of a folder, or Rescan, selects its row once the new
    listing has it - not in the old list it replaces."""
    root = tmp_path / "Back"
    for d in ("one", "two", "three"):
        (root / d).mkdir(parents=True)
    w = _Window()
    w._current_cat = str(root)
    w._load_local_items(str(root))
    w._local_select_key(str(root / "two"))  # asked before the rows are there
    assert w._local_want_key == str(root / "two")
    _drain(jobs)
    assert w.listw.current == 2 and w._local_want_key is None


def test_a_file_played_while_its_folder_is_read_plays_on_through_it(tmp_path,
                                                                    jobs):
    root = tmp_path / "Album"
    root.mkdir()
    for n in range(1, 7):
        (root / f"{n:02d} Track.mp3").write_bytes(b"x")
    w = _Window()
    w._current_cat = str(root)
    w._load_local_items(str(root))
    jobs.clear()                    # the listing stopped after its first slice
    w._local_listing.step(0.0)      # ... the folder read, one file told
    first = os.path.join(str(root), "01 Track.mp3")
    row = ml._file_row(first, w.AUDIO_EXTS)
    w._local_list_names = (os.path.normpath(str(root)),
                           w._local_listing.media_names())
    queue = w._local_listing_rows(first, row)
    assert [os.path.basename(r["_path"]) for r in queue] == \
        [f"{n:02d} Track.mp3" for n in range(1, 7)]
    assert queue[0] is row
    w._local_listing = None         # read whole: the list is the folder
    assert w._local_listing_rows(first, row) is None


def test_a_folder_is_asked_about_its_album_art_once(tmp_path, jobs):
    """A listing's or a search's rows come in slices, each naming their
    folder again: one job reads it while it is under way, and what it came
    to is put on a row made again without another read."""
    album = tmp_path / "Artist - Album"
    album.mkdir()
    (album / "01.mp3").write_bytes(b"x")
    w = _Window()
    rows = [{"_kind": "local", "_key": f"k{n}", "_path": str(album / "01.mp3"),
             "stream_icon": ""} for n in range(3)]
    w.all_items = rows
    w._local_fetch_album_art(rows[:1])
    w._local_fetch_album_art(rows[1:])      # the next slice: same folder
    assert len(jobs) == 1
    w._local_art_found[str(album)] = "https://art/x.jpg"
    jobs.clear()
    again = [dict(rows[0], stream_icon="")]
    w._local_fetch_album_art(again)
    assert jobs == [] and again[0]["stream_icon"] == "https://art/x.jpg"


class _CacheWindow(ml._LocalFilesMixin):
    """The library cache's own methods, over a file in the test's folder."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.pool = object()
        self._local_album_index = {}
        self._local_cover_index = {}

    def _library_cache_path(self) -> str:
        return self.path


def test_the_library_cache_is_written_off_the_window_in_order(tmp_path, jobs):
    path = str(tmp_path / "meta" / "local_library.json")
    w = _CacheWindow(path)
    w._save_library_cache("/r", {"Show": [[1, 1, "/r/s.mkv", "s"]]},
                          [{"_key": "a"}], {})
    w._save_library_cache("/r", {}, [{"_key": "a"}, {"_key": "b"}], {})
    assert not os.path.exists(path)         # nothing written on the window's
    (_p1, older, _d1, _e1), (_p2, newer, _d2, _e2) = jobs
    newer()
    older()                                 # late: a newer copy is there
    on_disk = json.load(open(path, encoding="utf-8"))["roots"]["/r"]
    assert [m["_key"] for m in on_disk["movies"]] == ["a", "b"]
    os.unlink(path)                         # kept as last written:
    assert [m["_key"] for m in w._library_cache()["/r"]["movies"]] == \
        ["a", "b"]                          # no file read for it
    jobs.clear()
    w._save_library_cache("/r", {}, [{"_key": "c"}], {})
    w._local_clear_library_cache()          # cleared while on its way
    jobs[0][1]()
    assert not os.path.exists(path) and w._library_cache() == {}


def test_a_walk_that_finds_nothing_new_leaves_the_list_as_it_is(tmp_path,
                                                                jobs):
    """A list with section headers is laid out row by row - a library of
    19,000 films took the window 0.45 s - so the same rows again (a slice
    of a walk, Rescan) are not put in again."""
    w = _Window()
    w._current_cat = str(tmp_path)
    movies = [{"name": f"Film {n}", "_kind": "local", "_key": f"/f{n}.mkv",
               "_path": f"/f{n}.mkv", "_year": "2000", "stream_icon": ""}
              for n in range(5)]
    w._apply_library({}, movies, {})
    assert w.list_model.resets == 1
    w._apply_library({}, [dict(m) for m in movies], {})
    assert w.list_model.resets == 1
    w._apply_library({}, movies + [dict(movies[0], name="New", _key="/n.mkv",
                                        _path="/n.mkv")], {})
    assert w.list_model.resets == 2


def test_the_list_model_puts_rows_in_without_a_reset(app_or_skip):
    from dopeiptv.ui.channel_list import ChannelListModel
    m = ChannelListModel()
    m.set_items([{"name": "a"}, {"name": "c"}], "rec")
    resets, inserted = [], []
    m.modelReset.connect(lambda: resets.append(1))
    m.rowsInserted.connect(lambda _p, first, last: inserted.append((first, last)))
    m.insert_items(1, [{"name": "b"}])
    m.append_items([{"name": "d"}, {"name": "e"}])
    assert [m.item_at(r)["name"] for r in range(m.rowCount())] == \
        ["a", "b", "c", "d", "e"]
    assert resets == [] and inserted == [(1, 1), (3, 4)]
    m.append_items([{"_header": "H"}])      # a header: laid out again
    assert resets == [1] and not m.uniform


@pytest.fixture
def app_or_skip():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# -- the real window ----------------------------------------------------------

_CHILD = r"""
import builtins, json, os, sys, tempfile, threading, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
tree = sys.argv[1]
GUI = threading.get_ident()
gui_reads = []

def watch(fn):
    def wrapped(p=".", *a, **k):
        try:
            path = os.fspath(p)
        except TypeError:
            path = ""
        if isinstance(path, str) and path.startswith(tree) \
                and threading.get_ident() == GUI:
            gui_reads.append(f"{fn.__name__}({path})")
        return fn(p, *a, **k)
    return wrapped

for name in ("scandir", "listdir", "stat", "lstat"):
    setattr(os, name, watch(getattr(os, name)))
builtins.open = watch(builtins.open)

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
_tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _tmp)
import dopeiptv.providers.cast_bridge as cb
cb.ffmpeg_path = lambda: None                   # no thumbnails: not measured
from dopeiptv.core._lazy_requests import requests as rq
def offline(*_a, **_k):
    raise OSError("offline in the test")
rq.get = offline
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw_mod
from dopeiptv.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
settings = QSettings(os.path.join(_tmp, "s.ini"), QSettings.Format.IniFormat)
settings.setValue("home_enabled", "false")
settings.setValue("local_dirs", json.dumps([tree]))
settings.setValue("local_view", "series")
mw_mod.launch_player = lambda *a, **k: None
w = MainWindow(DemoClient(), settings)
w.cover.resolver = None
w.resize(1280, 800)
w.show()

def settle(cond, secs=60, what=""):
    end = time.time() + secs
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    assert cond(), "timed out: " + what

def idle(secs):
    end = time.time() + secs
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)

def names():
    return [r.get("name") for r in (w.all_items or []) if not r.get("_header")]

def walked():
    return not getattr(w, "_local_scan_active", False) and bool(getattr(w, "_local_scan_done", None))

def listed():
    return getattr(w, "_local_listing", None) is None

settle(lambda: w.list_model.rowCount() > 0, what="first list")
w.switch_mode("local")
# The place of the new look, where the list was: Rescan and the Folders /
# Library switch in its header (mw_place_local).
settle(lambda: w._place_local_showing(), what="the place")
assert w._lp_page.rescan.isVisibleTo(w) and w._lp_page.header.switch.isVisibleTo(w)
assert w._lp_page.header.view_key() == "series"
settle(walked, what="the library walked")
settle(lambda: "Music" in names(), what="the music shelf")
idle(0.5)

w.local_view_btn.click()                                  # -> Folders view
settle(lambda: listed() and "Clips" in names(), what="folders view")
w.search.setText("song")                                  # a search
settle(lambda: getattr(w, "_local_walk", None) is None
       and len([r for r in w.all_items if r.get("_hit")]) == 48, what="matches")
w.search.clear()
settle(lambda: listed() and "Clips" in names(), what="the folder again")
w._local_descend(os.path.join(tree, "Clips"))             # a big folder
settle(lambda: listed() and len(names()) == 128, what="the big folder")
idle(0.5)

# Rescan in Folders view: a file put in since shows, the row kept selected.
key = os.path.join(tree, "Clips", "Clip 005.mkv")
w._local_select_key(key)
# (made on another thread: the test's own write is not the window's)
with_new = os.path.join(tree, "Clips", "Clip 200.mkv")
threading.Thread(target=lambda: open(with_new, "wb").close()).start()
time.sleep(0.2)
w.isActiveWindow = lambda: True                           # F5's way in
w._shortcut_run("local_rescan")
settle(lambda: listed() and len(names()) == 129, what="rescanned folder")
cur = w.list_model.item_at(w.listw.currentIndex().row())
assert cur is not None and cur.get("_key") == key, cur

w._local_up()
settle(lambda: listed() and "Clips" in names(), what="back up")
w.local_view_btn.click()                                  # -> the library
settle(lambda: "Music" in names(), what="the library again")
# Rescan in the library: a walk at once, though one ended moments ago.
film = os.path.join(tree, "Film Name 77 (1977)")
threading.Thread(target=lambda: (os.makedirs(film),
                                 open(os.path.join(film, "Film Name 77 (1977).mkv"), "wb").close())).start()
time.sleep(0.2)
w.local_rescan_btn.click()
assert getattr(w, "_local_scan_active", False), "no walk started"
settle(walked, what="the library walked again")
settle(lambda: any("Film Name 77" in (n or "") for n in names()), what="the new film")

keys = w.shortcut_keys()
assert keys["local_rescan"] == "F5", keys
assert w._shortcuts["local_rescan"].key().toString() == "F5"
w.switch_mode("live")
assert not w.local_rescan_btn.isVisibleTo(w)
print("GUI_READS", json.dumps(gui_reads[:20]), len(gui_reads), flush=True)
print("LOCAL_RESPONSIVE_OK", flush=True)
os._exit(0)
"""


def test_the_window_reads_nothing_of_local_files_on_its_thread(tmp_path):
    """The real window: the library, "View: folders", a search, a big folder
    and Rescan (its button and F5) in both views - and not one read of the
    folders on the window's thread."""
    pytest.importorskip("PyQt6")
    import subprocess
    import sys
    root = _library(tmp_path)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               XDG_CONFIG_HOME=str(tmp_path / "config"),
               XDG_DATA_HOME=str(tmp_path / "data"),
               XDG_CACHE_HOME=str(tmp_path / "cache"))
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.run([sys.executable, "-c", _CHILD, root],
                          capture_output=True, text=True, env=env, cwd=repo,
                          timeout=240)
    assert "LOCAL_RESPONSIVE_OK" in proc.stdout, (
        f"stdout={proc.stdout[-3000:]!r}\nstderr={proc.stderr[-4000:]!r}")
    line = next(ln for ln in proc.stdout.splitlines()
                if ln.startswith("GUI_READS"))
    assert line.endswith(" 0"), line
