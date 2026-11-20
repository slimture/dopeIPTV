"""The rewind buffer of a channel watched in private viewing: gone when
another channel is watched, when private viewing ends and as the app
closes - never kept for a later session."""
from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from dopeiptv.core import privacy


@pytest.fixture(autouse=True)
def _clean():
    privacy.set_private(False)
    privacy.playback_stopped()
    yield
    privacy.set_private(False)
    privacy.playback_stopped()


def _began_private(kind, key):
    privacy.set_private(True)
    privacy.playback_started(kind, key)
    privacy.set_private(False)


PKT = b"\x47" + b"x" * 187


class _Ingest:
    def __init__(self, url, store):
        self.state = "streaming"
        self.connections = 1

    def start(self):
        pass

    def stop(self, wait: float = 0.0):
        self.state = "stopped"


def _pool(tmp_path):
    from dopeiptv.core.dvr_pool import DvrPool
    return DvrPool(tmp_path, 600.0, 10**9, ingest_factory=_Ingest, min_free=0)


def _gone(path: Path, within: float = 5.0) -> bool:
    end = time.monotonic() + within
    while path.exists() and time.monotonic() < end:
        time.sleep(0.02)
    return not path.exists()


def test_a_private_channels_buffer_goes_when_another_channel_is_watched(tmp_path):
    pool = _pool(tmp_path)
    try:
        a = pool.open("http://p/a.ts", "live:a")
        a.mark_private()
        a.store.append(PKT)
        root = a.store.root
        assert (root / ".private").exists()
        # A reconnect, or Stop and then Play: left and watched again, kept.
        pool.release(a)
        assert pool.open("http://p/a.ts", "live:a") is a and not a.closed
        pool.release(a)
        b = pool.open("http://p/b.ts", "live:b")
        b.store.append(PKT)
        assert a.closed and _gone(root)
        # One watched in the open stays, as ever.
        pool.release(b)
        c = pool.open("http://p/c.ts", "live:c")
        assert not b.closed and b.frozen and c is pool.active
    finally:
        pool.close_all()


def test_private_buffers_left_go_as_private_viewing_ends(tmp_path):
    pool = _pool(tmp_path)
    try:
        a = pool.open("http://p/a.ts", "live:a")
        a.mark_private()
        a.store.append(PKT)
        pool.release(a)                     # Stop
        b = pool.open("http://p/b.ts", "live:b")
        assert a.closed
        b.mark_private()
        b.store.append(PKT)
        pool.forget_private()               # the switch off while b still plays
        assert not b.closed and pool.active is b
        pool.release(b)
        pool.forget_private()
        assert b.closed and _gone(b.store.root)
    finally:
        pool.close_all()


def test_a_private_buffer_is_off_the_disk_before_the_app_is_through_closing(tmp_path):
    pool = _pool(tmp_path)
    a = pool.open("http://p/a.ts", "live:a")
    a.mark_private()
    for _ in range(3):
        a.store.append(PKT)
    root = a.store.root
    assert any(root.glob("*.ts"))
    pool.close_all()                        # no wait: the files are gone all the same
    assert not any(root.glob("*.ts")) if root.exists() else True
    assert _gone(root)


def test_a_private_buffer_a_crash_left_is_swept_at_the_next_start(tmp_path):
    from dopeiptv.core.dvr_store import mark_private, sweep_abandoned
    if os.name == "nt":
        pytest.skip("a process that ended is not told from one running")
    dead = 2 ** 22 + 12345                   # no such process
    left = tmp_path / f"{dead}-aaaaaaaaaaaa"
    left.mkdir()
    mark_private(left)
    (left / "00000000.ts").write_bytes(PKT)
    plain = tmp_path / f"{dead}-bbbbbbbbbbbb"
    plain.mkdir()
    mine = tmp_path / f"{os.getpid()}-cccccccccccc"
    mine.mkdir()
    mark_private(mine)
    assert sweep_abandoned(tmp_path) == 1
    assert not left.exists() and plain.exists() and mine.exists()


def test_the_player_marks_the_buffer_of_a_private_channel():
    from dopeiptv.media.dvr_player import DvrPlayerMixin
    marked: list = []
    session = SimpleNamespace(mark_private=lambda: marked.append(1))
    DvrPlayerMixin._dvr_private(session)
    assert marked == []
    _began_private("live", 7)
    DvrPlayerMixin._dvr_private(session)
    assert marked == [1]


# -- a private channel's buffer in memory ------------------------------------------------------

MOUNTS = """sysfs /sys sysfs rw 0 0
/dev/sda2 / ext4 rw,relatime 0 0
tmpfs /run/user/1000 tmpfs rw,nosuid,nodev,relatime,size=1630000k,mode=700,uid=1000 0 0
/dev/sdb1 /run/user/1000/doc fuse.portal rw 0 0
tmpfs /mnt/with\\040space tmpfs rw 0 0
"""


def test_what_lives_in_memory_is_read_from_the_mount_table():
    from dopeiptv.core.dvr_store import in_memory
    assert in_memory(Path("/run/user/1000"), MOUNTS)
    assert in_memory(Path("/run/user/1000/dopeiptv-dvr/1-ab"), MOUNTS)
    assert not in_memory(Path("/run/user/1000/doc/x"), MOUNTS), "the longest mount point wins"
    assert not in_memory(Path("/home/me/.cache"), MOUNTS)
    assert not in_memory(Path("/run/user/10000"), MOUNTS), "a prefix of a name is no mount point"
    assert in_memory(Path("/mnt/with space/x"), MOUNTS)
    assert not in_memory(Path("/run/user/1000"), "garbage\n\n")


def test_the_memory_folder_is_only_this_users_own_tmpfs(tmp_path):
    from dopeiptv.core.dvr_store import memory_base
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    os.chmod(run, 0o700)
    mounts = f"tmpfs {run} tmpfs rw 0 0\n"
    env = {"XDG_RUNTIME_DIR": str(run)}
    assert memory_base(env, mounts, "linux") == run / "dopeiptv-dvr"
    assert memory_base(env, mounts, "darwin") is None
    assert memory_base(env, mounts, "win32") is None
    assert memory_base({}, mounts, "linux") is None
    assert memory_base({"XDG_RUNTIME_DIR": "relative"}, mounts, "linux") is None
    assert memory_base(env, f"/dev/sda1 {run} ext4 rw 0 0\n", "linux") is None, "on disk"
    os.chmod(run, 0o755)
    assert memory_base(env, mounts, "linux") is None, "another user could look in"
    os.chmod(run, 0o700)
    assert memory_base({"XDG_RUNTIME_DIR": str(tmp_path / "missing")}, mounts, "linux") is None


def test_a_budget_in_memory_takes_half_of_what_is_free_and_never_too_little(tmp_path, monkeypatch):
    from dopeiptv.core import dvr_store
    free = {"n": 4 << 30}
    monkeypatch.setattr(dvr_store.shutil, "disk_usage",
                        lambda p: SimpleNamespace(total=8 << 30, used=0, free=free["n"]))
    mem = f"tmpfs {tmp_path} tmpfs rw 0 0\n"
    no_swap = "Filename\tType\tSize\tUsed\tPriority\n"
    assert dvr_store.memory_budget(tmp_path / "m", 2 << 30, mem, no_swap) == dvr_store.MEMORY_MAX
    assert dvr_store.memory_budget(tmp_path / "m", 200 << 20, mem, no_swap) == 200 << 20
    free["n"] = 600 << 20
    assert dvr_store.memory_budget(tmp_path / "m", 2 << 30, mem, no_swap) == (600 - 64) << 19
    free["n"] = 100 << 20
    assert dvr_store.memory_budget(tmp_path / "m", 2 << 30, mem, no_swap) == 0, "too little: none"
    free["n"] = 4 << 30
    assert dvr_store.memory_budget(tmp_path / "m", 2 << 30, f"/dev/sda1 {tmp_path} ext4 rw 0 0\n",
                                   no_swap) == 0, "the folder made is on disk: none in memory"
    # A tmpfs whose pages swap could carry to a plain disk: none either -
    # unless the tmpfs is never swapped.
    plain = no_swap + "/swapfile-not-there file 1024 0 -2\n"
    assert dvr_store.memory_budget(tmp_path / "m", 2 << 30, mem, plain) == 0, "swap not encrypted"
    assert dvr_store.memory_budget(tmp_path / "m", 2 << 30, f"tmpfs {tmp_path} tmpfs rw,noswap 0 0\n",
                                   plain) == dvr_store.MEMORY_MAX, "a tmpfs never swapped"


def test_the_buffers_own_folder_is_checked_where_it_really_is(tmp_path, monkeypatch):
    """The run folder may be this user's tmpfs while the folder under it is
    a link to a disk, or a disk mounted there: the folder written into is
    checked itself - a link refused, its file system by the mount table
    where it really is - and a store meant for memory writes nothing
    anywhere else."""
    from dopeiptv.core import dvr_store
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    mounts = f"tmpfs {run} tmpfs rw 0 0\n"
    no_swap = "Filename\tType\tSize\tUsed\tPriority\n"
    good = run / "dopeiptv-dvr"
    good.mkdir(mode=0o700)
    assert dvr_store.memory_folder(good, mounts, no_swap)
    os.chmod(good, 0o755)
    assert dvr_store.memory_folder(good, mounts, no_swap) and (os.stat(good).st_mode & 0o077) == 0, \
        "narrowed"
    disk = tmp_path / "disk"
    disk.mkdir(mode=0o700)
    link = run / "linked"
    link.symlink_to(disk, target_is_directory=True)
    assert not dvr_store.memory_folder(link, mounts, no_swap), "a link to a disk folder"
    assert not dvr_store.memory_folder(run / "missing", mounts, no_swap)
    sub = run / "mounted"
    sub.mkdir(mode=0o700)
    assert not dvr_store.memory_folder(sub, mounts + f"/dev/sdb1 {sub} ext4 rw 0 0\n", no_swap), \
        "a disk mounted under the tmpfs"
    assert not dvr_store.memory_folder(good, mounts, no_swap + "/dev/sda9 partition 1 0 -2\n"), \
        "swap on a plain partition could carry it to the disk"
    # A store meant for memory: nothing written where the folder is not.
    from dopeiptv.core import memory_safe
    monkeypatch.setattr(memory_safe, "stays_in_memory",
                        lambda p, m=None, s=None, sys_root="": (
                            str(p).startswith(str(run))
                            and not str(os.path.realpath(p)).startswith(str(disk)), ""))
    import pytest
    with pytest.raises(dvr_store.NotInMemory):
        dvr_store.SegmentStore(link / "1-ab", 60.0, 10**8, memory=True)
    assert not any(p.is_file() for p in disk.rglob("*")), "nothing written on the disk"
    store = dvr_store.SegmentStore(good / "1-cd", 60.0, 10**8, memory=True)
    assert store.root.is_dir()


def test_a_private_channel_without_safe_memory_has_no_buffer_at_all(tmp_path, monkeypatch):
    """No memory out of a disk's reach - its own folder not in memory, or
    no room - and a private channel gets no buffer: never one on the disk,
    and no connection opened for the try (the player opens the address)."""
    from dopeiptv.core import dvr_pool as dp
    from dopeiptv.core import dvr_store
    from dopeiptv.core.dvr_pool import DvrPool, NoPrivateBuffer
    disk, memory = tmp_path / "disk", tmp_path / "memory"
    monkeypatch.setattr(dp, "memory_budget", lambda base, cap: 300 << 20)
    monkeypatch.setattr(dvr_store, "memory_folder", lambda p, mounts=None, swaps=None: False)
    opened = []

    class Counting(_Ingest):
        def __init__(self, *a, **k):
            opened.append(1)
            super().__init__(*a, **k)
    pool = DvrPool(disk, 600.0, 10**9, ingest_factory=Counting, min_free=0, memory=memory)
    try:
        with pytest.raises(NoPrivateBuffer):
            pool.open("http://h/live/1.ts", "1", private=True)
        monkeypatch.setattr(dp, "memory_budget", lambda base, cap: 0)
        with pytest.raises(NoPrivateBuffer):
            pool.open("http://h/live/1.ts", "1", private=True)
        no_memory = DvrPool(disk, 600.0, 10**9, ingest_factory=Counting, min_free=0, memory=None)
        with pytest.raises(NoPrivateBuffer):
            no_memory.open("http://h/live/1.ts", "1", private=True)
        assert opened == [], "no connection opened"
        assert pool.sessions == [] and not any(p.is_file() for p in tmp_path.rglob("*")), \
            "nothing on the disk"
    finally:
        pool.close_all(wait=5.0)


def test_a_channel_private_from_its_start_never_touches_the_disk(tmp_path, monkeypatch):
    from dopeiptv.core import dvr_pool as dp
    from dopeiptv.core.dvr_pool import DvrPool
    from dopeiptv.core import dvr_store
    disk, memory = tmp_path / "disk", tmp_path / "memory"
    monkeypatch.setattr(dp, "memory_budget", lambda base, cap: 300 << 20)
    monkeypatch.setattr(dvr_store, "memory_folder",
                        lambda p, mounts=None, swaps=None: str(p).startswith(str(memory)))
    pool = DvrPool(disk, 600.0, 10**9, ingest_factory=_Ingest, min_free=0, memory=memory)
    try:
        s = pool.open("http://h/live/1.ts", "1", private=True)
        assert s.private and s.memory_cap == 300 << 20
        assert s.store.root.parent == memory and s.store.min_free == dp.MEMORY_MIN_FREE
        assert not disk.exists() or not any(disk.iterdir())
        s.store.append(PKT * 50)
        pool.enforce()
        assert s.store.max_bytes == 300 << 20, "its own cap, not the disk's share"
        plain = pool.open("http://h/live/2.ts", "2")
        assert plain.store.root.parent == disk and plain.memory_cap is None and not plain.private
        assert _gone(s.store.root), "left privately: gone at once"
        # Again private, but no room in memory: no buffer at all.
        monkeypatch.setattr(dp, "memory_budget", lambda base, cap: 0)
        with pytest.raises(dp.NoPrivateBuffer):
            pool.open("http://h/live/3.ts", "3", private=True)
    finally:
        pool.close_all(wait=5.0)
    assert not any(memory.iterdir()) if memory.exists() else True


def test_the_player_asks_for_memory_while_viewing_privately():
    import inspect
    from dopeiptv.media import dvr_player
    src = inspect.getsource(dvr_player.DvrPlayerMixin._dvr_start)
    assert "private=self._dvr_playing_private()" in src and "self._dvr_new_pool(" in src
    assert "self._dvr_new_pool(" in inspect.getsource(dvr_player.DvrPlayerMixin.dvr_pool_for_server)
    assert "memory=dvr_memory()" in inspect.getsource(dvr_player.DvrPlayerMixin._dvr_new_pool)
    assert "sweep_abandoned(memory)" in inspect.getsource(dvr_player.dvr_base)
