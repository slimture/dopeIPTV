"""Local files notices changes on disk by itself (#34): what is watched and
which shares are on the network, when it reads again (two seconds after
the last change, ten at most after the first, never while a walk runs -
one is queued), the light re-walk where no events come, and what changed
between two listings of a folder (core/local_watch.py) - then the watcher
on a thread of its own (ui/local_watcher.py)."""
from __future__ import annotations

import os
import time

import pytest

from dopeiptv.core import local_watch as lw

MOUNTS = """\
sysfs /sys sysfs rw,nosuid 0 0
/dev/nvme0n1p2 / ext4 rw,relatime 0 0
/dev/sda1 /media/me/USB\\040Disk vfat rw 0 0
//nas/video /mnt/nas cifs rw,vers=3.0 0 0
nas:/export/films /mnt/nfs nfs4 rw 0 0
gvfsd-fuse /run/user/1000/gvfs fuse.gvfsd-fuse rw,nosuid 0 0
me@box:/home /mnt/box fuse.sshfs rw 0 0
/dev/sdb1 /mnt/nas/local ext4 rw 0 0
garbage
"""


# -- the network --------------------------------------------------------------------

def test_the_mount_table_read_with_its_escapes():
    got = lw.parse_mounts(MOUNTS)
    assert ("/media/me/USB Disk", "vfat") in got
    assert ("/mnt/nas", "cifs") in got and ("/run/user/1000/gvfs", "fuse.gvfsd-fuse") in got
    assert all(point.startswith("/") for point, _fs in got)       # sysfs's /sys, not "garbage"
    assert lw.mount_of("/mnt/nas/Films/x.mkv", got) == ("/mnt/nas", "cifs")
    # The longest mount point wins: a local disk mounted inside the share.
    assert lw.mount_of("/mnt/nas/local/a", got) == ("/mnt/nas/local", "ext4")
    assert lw.mount_of("/mnt/nasty/a", got) == ("/", "ext4")      # not under /mnt/nas
    assert lw.mount_of("/home/me", got) == ("/", "ext4")


def test_which_folders_are_on_the_network():
    m = lw.parse_mounts(MOUNTS)

    def net(p, platform="linux"):
        return lw.on_network(p, m, platform=platform)
    assert net("/mnt/nas/Films") and net("/mnt/nfs/a") and net("/mnt/box/me")
    assert net("/run/user/1000/gvfs/smb-share:server=nas,share=video/Films")
    assert lw.on_network("/home/me/.gvfs/share/x", [], platform="linux")
    assert not net("/home/me/Videos") and not net("/media/me/USB Disk/Films")
    assert not net("/mnt/nas/local/Films")                     # its own disk, inside
    assert not net("")
    # A Mac: shares mount under /Volumes (a disk of its own there is read
    # again lightly too); Windows: a network path or a mapped drive.
    assert net("/Volumes/video/Films", "darwin") and not net("/Users/me/Movies", "darwin")
    assert lw.on_network(r"\\nas\video\Films", platform="win32")
    assert lw.on_network("Z:\\Films", platform="win32", remote_drives=["Z:"])
    assert not lw.on_network("C:\\Films", platform="win32", remote_drives=["Z:"])
    assert lw.is_network_fs("smb3") and lw.is_network_fs("NFS4") and not lw.is_network_fs("btrfs")


# -- what to watch ---------------------------------------------------------------------

def test_the_plan_watches_the_local_folders_up_to_the_cap():
    p = lw.plan(["/a", "/a/", "/a/b", "/n/x", "/a/c"], network=lambda d: d.startswith("/n"))
    assert p.watch == ("/a", "/a/b", "/a/c") and p.poll and p.why == "network"
    assert lw.plan(["/a", "/a/b"]) == lw.Plan(("/a", "/a/b"), False, "")
    many = [f"/lib/d{i:04d}" for i in range(lw.CAP + 5)]
    p = lw.plan(many)
    assert len(p.watch) == lw.CAP and p.watch[0] == "/lib/d0000"   # the first ones kept
    assert p.poll and p.why == "cap"
    assert lw.plan([]) == lw.Plan()
    # On Windows a watch holds a handle on its folder: a library's top alone.
    assert lw.cap_for("library", "win32") == 1 and lw.cap_for("folder", "win32") == lw.CAP
    assert lw.cap_for("library", "linux") == lw.CAP == lw.cap_for("library", "darwin")
    p = lw.plan(["/lib", "/lib/a", "/lib/b"], cap=lw.cap_for("library", "win32"))
    assert p.watch == ("/lib",) and p.poll and p.why == "cap"


def test_a_librarys_folders_from_what_it_holds():
    root = os.path.normpath("/m/Lib")
    paths = [f"{root}/Films/Dune (2021)/Dune.mkv", f"{root}/Shows/Sky/Season 1/S01E01.mkv",
             f"{root}/loose.mkv", "/elsewhere/x.mkv", ""]
    got = lw.library_dirs(root, paths)
    assert got == [root, f"{root}/Films", f"{root}/Shows", f"{root}/Films/Dune (2021)",
                   f"{root}/Shows/Sky", f"{root}/Shows/Sky/Season 1"]
    assert lw.library_dirs(root, paths, limit=3) == got[:3]       # one past the cap: passed
    assert lw.library_dirs(root, []) == [root]


# -- when to read again -------------------------------------------------------------------

def test_a_burst_of_changes_is_read_once_after_a_quiet_moment():
    r = lw.Refresh(debounce=2.0, max_wait=10.0)
    assert r.due_in(0.0) is None and not r.take(0.0)
    r.changed(100.0)
    assert r.due_in(100.0) == 2.0 and not r.take(101.0)
    r.changed(101.5)                                   # another change: the wait goes on
    assert r.due_in(102.0) == pytest.approx(1.5)
    assert r.take(103.5) and not r.pending             # read once
    assert not r.take(110.0)


def test_changes_that_keep_coming_are_read_within_the_max_wait():
    r = lw.Refresh(debounce=2.0, max_wait=10.0)
    t = 0.0
    r.changed(t)
    while t < 9.0:
        t += 1.0
        r.changed(t)
        assert not r.take(t)
    assert r.due_in(10.0) == 0.0 and r.take(10.0)      # a copy of an hour still shows


def test_never_while_a_walk_runs_one_is_queued():
    r = lw.Refresh(debounce=2.0)
    r.changed(0.0)
    assert not r.take(5.0, busy=True) and r.pending    # queued for after it
    assert not r.take(6.0, busy=True)
    assert r.take(7.0, busy=False) and not r.pending   # the walk ended: once
    r.changed(8.0)
    r.forget()                                          # another folder shown
    assert not r.pending and not r.take(20.0)


def test_the_light_re_walk_is_spaced_by_what_it_costs():
    assert not lw.poll_due(1000.0, 1.0, 1000.0 + lw.POLL_MIN - 1)
    assert lw.poll_due(1000.0, 1.0, 1000.0 + lw.POLL_MIN)
    # A walk of a minute over a share: ten minutes before the next.
    assert not lw.poll_due(0.0, 60.0, 599.0) and lw.poll_due(0.0, 60.0, 600.0)
    assert lw.poll_due(0.0, -5.0, lw.POLL_MIN)


# -- what changed --------------------------------------------------------------------------

def test_what_changed_between_two_listings():
    assert lw.diff(["a", "b"], ["a", "b"]) is None
    up = lw.diff(["a", "b", "c", "d"], ["a", "b2", "c", "e", "f"])
    assert up is not None and not up.reset
    assert up.removed == (1, 3) and up.inserted == ((1, "b2"), (3, "e"), (4, "f"))
    # Applied to the list shown: the new listing, every row kept as it was.
    rows = [{"k": k} for k in "abcd"]
    kept = {id(r) for r in rows if r["k"] in "ac"}
    out = lw.apply(rows, up, lambda k: {"k": k})
    assert [r["k"] for r in out] == ["a", "b2", "c", "e", "f"] and out is rows
    assert {id(r) for r in out if r["k"] in "ac"} == kept
    # Everything gone, or into an empty list.
    assert lw.diff(["a"], []) == lw.Update(removed=(0,))
    assert lw.diff([], ["x", "y"]) == lw.Update(inserted=((0, "x"), (1, "y")))
    # The rows kept in another order: laid out anew.
    assert lw.diff(["a", "b", "c"], ["c", "a", "b"]).reset
    assert lw.diff(["a", "a"], ["a"]).reset            # a key twice: never guessed
    assert lw.runs([5, 1, 2, 3, 7, 8]) == [(1, 3), (5, 5), (7, 8)]


def test_a_rename_is_one_out_and_one_in_where_it_sorts():
    old = ["/f/Alpha", "/f/Beta", "/f/clip1.mkv", "/f/clip2.mkv"]
    new = ["/f/Alpha", "/f/Beta", "/f/clip2.mkv", "/f/zzz.mkv"]
    up = lw.diff(old, new)
    assert up.removed == (2,) and up.inserted == ((3, "/f/zzz.mkv"),)
    assert lw.apply(list(old), up, str) == new


# -- the watcher, on a thread of its own ------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _until(app, cond, secs=5.0):
    end = time.monotonic() + secs
    while not cond() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    return cond()


def test_the_watcher_reports_a_change_from_its_own_thread(app, tmp_path):
    import threading

    from dopeiptv.ui.local_watcher import DirWatcher
    seen: list[tuple[str, bool]] = []
    w = DirWatcher()
    w.changed.connect(lambda p: seen.append(
        (p, threading.current_thread() is threading.main_thread())))
    assert w.running()
    w.watch([str(tmp_path), str(tmp_path / "not-there")])     # one gone: left out, quietly
    assert _until(app, lambda: w.count == 1)
    (tmp_path / "new.mkv").write_bytes(b"x")
    assert _until(app, lambda: bool(seen))
    assert seen[0] == (str(tmp_path), True)                    # said on the window's thread
    w.watch([])
    assert _until(app, lambda: w.count == 0)
    seen.clear()
    (tmp_path / "more.mkv").write_bytes(b"x")
    assert not _until(app, lambda: bool(seen), 0.6)            # let go
    w.stop()
    assert not w.running() and not w._thread.isRunning()
    w.watch([str(tmp_path)])                                   # after its end: nothing
    w.stop()


# -- the library's walk and the list (the window's side, on a stub) ----------------------

def test_a_walk_never_lays_its_library_over_another_view(tmp_path, monkeypatch):
    """A walk of the library still under way - a change on disk walks it
    again quietly - when the Folders view is picked: it ends without laying
    the library over the folder's listing; a walk says which folders it
    went through (what is watched after it)."""
    import dopeiptv.providers.cast_bridge as cb
    import dopeiptv.ui.mw_local as ml
    import dopeiptv.ui.mw_local_library as mll
    from dopeiptv.core._lazy_requests import requests as rq
    from tests.test_local_responsive import _drain, _Window
    jobs: list = []
    monkeypatch.setattr(ml, "run_async", lambda pool, fn, done, err=None: jobs.append(
        (pool, fn, done, err)))
    monkeypatch.setattr(mll, "run_async", ml.run_async)
    monkeypatch.setattr(cb, "ffmpeg_path", lambda: None)

    def offline(*_a, **_k):
        raise OSError("offline in the tests")
    monkeypatch.setattr(rq, "get", offline, raising=False)
    root = tmp_path / "Lib"
    for n in range(3):
        film = root / f"Film {n} ({2000 + n})"
        film.mkdir(parents=True)
        (film / f"Film {n} ({2000 + n}).mkv").write_bytes(b"x")
    w = _Window()
    w._current_cat = str(root)
    w.settings.setValue("local_view", "series")
    w._load_local_library(str(root), show_cache=False, quiet=True)
    assert w._local_scan_active and w._local_scan_quiet and len(jobs) == 1
    w._local_toggle_view()                      # -> Folders, the walk still going
    _drain(jobs)
    assert not w._local_scan_active
    kinds = {r.get("_kind") for r in w.all_items}
    assert kinds == {"localdir"}, kinds         # the folder's own rows, not the library
    assert len(w.all_items) == 3
    walked = []
    w._local_walk_done = lambda r, dirs, cost: walked.append((r, sorted(dirs)))
    w._local_scan_done = {}
    w._local_toggle_view()                      # the library again: walked
    _drain(jobs)
    assert walked and walked[-1][0] == str(root) and len(walked[-1][1]) == 4
