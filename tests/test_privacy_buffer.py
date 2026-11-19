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
