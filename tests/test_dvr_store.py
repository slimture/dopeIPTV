"""The rolling store of the app-kept rewind buffer: segments on TS packet
boundaries, runs, retention by time, bytes and disk, and readers that follow
it live and are never pulled out from under."""
from __future__ import annotations

import threading

from dopeiptv.core.dvr_store import TS_PACKET, SegmentStore, sweep_abandoned


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def store(tmp_path, keep=60.0, max_bytes=10**9, **kw):
    clock = Clock()
    s = SegmentStore(tmp_path / "s", keep, max_bytes, clock=clock,
                     min_free=0, **kw)
    return s, clock


def test_segments_roll_on_time_and_on_a_packet_boundary(tmp_path):
    s, clock = store(tmp_path)
    s.append(b"x" * 1000)
    clock.t += 2.5
    s.append(b"y" * 1000)
    segs = s._segs
    assert len(segs) == 2
    assert segs[0].size % TS_PACKET == 0 and segs[0].size == 1128
    assert segs[0].closed and not segs[1].closed
    assert segs[1].size == 1000 - 128
    assert segs[1].path.read_bytes() == b"y" * 872


def test_old_segments_go_by_time(tmp_path):
    s, clock = store(tmp_path, keep=10.0)
    for _ in range(30):
        s.append(b"z" * 188)
        clock.t += 1.0
    first, last = s.span()
    assert last - first <= 10.0 + 2 * SegmentStore.SEG_SECONDS
    assert len(list(s.root.iterdir())) == len(s._segs)
    assert not s.trimmed


def test_the_byte_cap_wins_and_says_so(tmp_path):
    s, clock = store(tmp_path, keep=3600.0, max_bytes=188 * 40)
    for _ in range(60):
        s.append(b"z" * 188 * 4)
        clock.t += 1.0
    assert sum(x.size for x in s._segs) <= 188 * 40 + 188 * 8
    assert s.trimmed


def test_a_segment_being_read_is_deleted_only_when_let_go(tmp_path):
    s, clock = store(tmp_path, keep=4.0)
    s.append(b"a" * 188)
    first = s._segs[0]
    reader = s.read_from(first.seq)
    assert next(reader) == b"a" * 188          # pinned now
    for _ in range(10):
        clock.t += 1.0
        s.append(b"b" * 188)
    assert first.seq not in s._by_seq          # forgotten
    assert first.path.exists()                 # but not pulled away
    reader.close()
    assert not first.path.exists()


def test_a_reader_follows_live_and_stops_where_its_run_ends(tmp_path):
    s, clock = store(tmp_path)
    s.append(b"1" * 188)
    got: list[bytes] = []
    done = threading.Event()

    def read():
        for chunk in s.read_from(s._segs[0].seq, wait=0.05):
            got.append(chunk)
        done.set()

    threading.Thread(target=read, daemon=True).start()
    clock.t += 2.5
    s.append(b"2" * 188)                       # next segment, same run
    s.new_run()                                # a reconnect
    s.append(b"3" * 188)                       # the next run: not read
    assert done.wait(5)
    assert b"".join(got) == b"1" * 188 + b"2" * 188
    assert [r[0] for r in s.runs()] == [0, 1]


def test_a_time_in_a_gap_starts_at_the_next_run(tmp_path):
    s, clock = store(tmp_path)
    s.append(b"a" * 188)
    clock.t += 1.0
    s.append(b"a" * 188)
    s.new_run()
    clock.t += 30.0
    s.append(b"b" * 188)
    gap_start = s._segs[0].t1 + 10
    assert s.segment_at(gap_start).run == 1
    assert s.segment_at(1000.5).run == 0
    assert s.segment_after(1001.0).run == 1


def test_closing_ends_every_reader(tmp_path):
    s, _clock = store(tmp_path)
    s.append(b"a" * 188)
    done = threading.Event()

    def read():
        for _ in s.read_from(0, wait=0.05):
            pass
        done.set()

    threading.Thread(target=read, daemon=True).start()
    s.wipe()
    assert done.wait(5)
    assert not s.root.exists()


def test_low_disk_keeps_little_and_drops_what_has_no_room(tmp_path):
    clock = Clock()
    s = SegmentStore(tmp_path / "s", 3600.0, 10**9, clock=clock,
                     min_free=1 << 62)
    for _ in range(5):
        s.append(b"z" * 188)
        clock.t += 2.5
    assert len(s._segs) == 1


def test_abandoned_session_folders_are_swept(tmp_path):
    import os
    import time
    old, new = tmp_path / "111-old", tmp_path / "222-new"
    old.mkdir()
    new.mkdir()
    t = time.time() - 3600
    os.utime(old, (t, t))
    assert sweep_abandoned(tmp_path) == 1
    assert not old.exists() and new.exists()


def test_live_waits_for_the_run_that_is_coming_not_the_one_before(tmp_path):
    """A channel come back to, or a reconnect: the end of the run before
    is history - joining live must wait for the new run's first bytes."""
    import threading
    from dopeiptv.core.dvr_store import SegmentStore
    st = SegmentStore(tmp_path / "s", 600, 10**9, min_free=0)
    st.append(b"\x47" + b"x" * 187)
    assert st.wait_for_data(0.1)
    st.new_run()
    assert not st.wait_for_data(0.2), "the old run's bytes are not live"
    threading.Timer(0.2, lambda: st.append(b"\x47" + b"y" * 187)).start()
    assert st.wait_for_data(3.0)
    assert st.live_segment(4.0).run == 1
