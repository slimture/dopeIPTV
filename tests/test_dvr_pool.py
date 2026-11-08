"""Step two and three of the app-kept rewind buffer: the history of a
channel left stays (frozen, without a connection) and is there again when
it is watched again, after a gap; one space budget, the channels left
longest ago giving way first; a recording reads the buffer - from the
past on, across a gap, through a channel change - and keeps the channel
it records streaming until it ends."""
from __future__ import annotations

import time
from pathlib import Path

from dopeiptv.core.dvr_ingest import wait_until
from dopeiptv.core.dvr_pool import DvrPool

PKT = b"\x47" + b"x" * 187


class FakeIngest:
    """A provider connection that only says whether it is open; the test
    writes the bytes itself."""
    instances: list["FakeIngest"] = []

    def __init__(self, url: str, store) -> None:
        self.url, self.store = url, store
        self.state = "streaming"
        self.connections = 1
        self.started = self.stopped = False
        FakeIngest.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self, wait: float = 0.0) -> None:
        self.stopped = True
        self.state = "stopped"

    @property
    def alive(self) -> bool:
        return self.started and not self.stopped


def _pool(tmp_path: Path, max_bytes: int = 10**9, keep: float = 600.0):
    FakeIngest.instances.clear()
    return DvrPool(tmp_path, keep, max_bytes, ingest_factory=FakeIngest,
                   min_free=0)


def _feed(session, n: int) -> None:
    """n packets, a segment each: the store's clock moves on 3 s a call."""
    st = session.store
    if not getattr(st, "_test_clock", False):
        now = [time.time()]

        def clock() -> float:
            now[0] += 3.0
            return now[0]
        st._clock, st._test_clock = clock, True
    for _ in range(n):
        st.append(PKT)


def _open_connections() -> int:
    return sum(1 for i in FakeIngest.instances if i.alive)


def test_a_channel_left_keeps_its_history_and_lets_go_of_its_connection(
        tmp_path):
    pool = _pool(tmp_path)
    try:
        a = pool.open("http://p.invalid/a.ts")
        _feed(a, 5)
        pool.release(a)
        assert a.frozen and a.span() is not None
        assert _open_connections() == 0, "a frozen channel holds no connection"
        b = pool.open("http://p.invalid/b.ts")
        assert _open_connections() == 1
        pool.release(b)
        again = pool.open("http://p.invalid/a.ts")
        assert again is a and not a.frozen, "watched again: the same history"
        assert wait_until(lambda: a._freezing is None or not a._freezing.is_alive(), 3)
        _feed(a, 3)
        runs = a.runs()
        assert len(runs) == 2, "what follows a return is a run of its own"
        assert runs[0][2] <= runs[1][1]
        assert _open_connections() == 1
    finally:
        pool.close_all()


def test_space_goes_from_the_channels_left_longest_ago_first(tmp_path):
    pool = _pool(tmp_path, max_bytes=30 * len(PKT))
    try:
        sessions = []
        for name in ("a", "b", "c"):
            s = pool.open(f"http://p.invalid/{name}.ts")
            _feed(s, 12)
            pool.release(s)
            sessions.append(s)
            time.sleep(0.01)
        a, b, c = sessions
        pool.enforce()
        assert pool.total_bytes() <= 30 * len(PKT)
        # The channel left longest ago gives way first, from its oldest.
        assert a.store.total_bytes() == 6 * len(PKT)
        assert b.store.total_bytes() == c.store.total_bytes() == 12 * len(PKT)
        # The channel watched keeps what it holds: the others give way.
        d = pool.open("http://p.invalid/d.ts")
        _feed(d, 25)
        pool.enforce()
        assert d.store.total_bytes() == 25 * len(PKT)
        assert pool.total_bytes() <= 30 * len(PKT)
        assert a.closed and b.closed, "emptied, a channel's history goes"
        assert c.store.total_bytes() == 5 * len(PKT)
    finally:
        pool.close_all()


def test_old_history_and_too_many_channels_go(tmp_path):
    pool = _pool(tmp_path, keep=0.2)
    pool.MAX_FROZEN = 2
    try:
        old = pool.open("http://p.invalid/old.ts")
        _feed(old, 3)
        pool.release(old)
        time.sleep(0.3)
        pool.enforce()
        assert old.closed, "history older than the rewind setting goes"
        pool.keep_seconds = 600
        kept = []
        for name in ("a", "b", "c"):
            s = pool.open(f"http://p.invalid/{name}.ts")
            s.store.keep_seconds = 600
            _feed(s, 2)
            pool.release(s)
            kept.append(s)
            time.sleep(0.01)
        assert kept[0].closed and not kept[1].closed and not kept[2].closed
    finally:
        pool.close_all()


def test_a_recording_reads_the_buffer_and_keeps_its_channel_streaming(
        tmp_path):
    pool = _pool(tmp_path)
    out = tmp_path / "rec.ts"
    try:
        a = pool.open("http://p.invalid/a.ts")
        _feed(a, 4)
        first = a.store.segment_at(a.span()[0])
        rec = a.record(out, from_time=first.t0)     # from the past on
        assert a.recording
        pool.release(a)                 # another channel on screen
        assert a.streaming and not a.frozen, "the recording keeps it open"
        assert _open_connections() == 1
        b = pool.open("http://p.invalid/b.ts")
        assert _open_connections() == 2
        _feed(a, 3)
        a.store.new_run()               # a reconnect gap
        _feed(a, 2)
        rec.stop(wait=5.0)
        assert not rec.alive
        assert out.stat().st_size == 9 * len(PKT), "past, live, past the gap"
        assert wait_until(lambda: a.frozen, 3), \
            "the recording over, the channel left lets go of its connection"
        assert _open_connections() == 1
        pool.release(b)
    finally:
        pool.close_all()


def test_a_stopped_recording_writes_what_is_there_and_ends(tmp_path):
    pool = _pool(tmp_path)
    out = tmp_path / "rec.ts"
    try:
        a = pool.open("http://p.invalid/a.ts")
        _feed(a, 2)
        rec = a.record(out)             # from live
        time.sleep(0.1)
        _feed(a, 2)
        rec.stop(wait=5.0)
        assert not rec.alive and out.stat().st_size > 0
        assert not a.recording
        assert not a.frozen, "watched: a recording ending changes nothing"
    finally:
        pool.close_all()


def test_close_all_ends_recordings_and_wipes_the_buffers(tmp_path):
    pool = _pool(tmp_path)
    out = tmp_path / "rec.ts"
    a = pool.open("http://p.invalid/a.ts")
    _feed(a, 3)
    rec = a.record(out, from_time=a.span()[0])
    root = a.store.root
    pool.close_all(wait=5.0)
    assert not rec.alive and out.stat().st_size == 3 * len(PKT)
    assert wait_until(lambda: not root.exists(), 5)
    assert pool.sessions == []
