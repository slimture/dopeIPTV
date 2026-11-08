"""The rolling store of an application-kept live rewind buffer.

A live channel's MPEG-TS bytes go into short segment files, a run of them
per provider connection. Readers - the local server mpv plays from - take
the bytes from any segment on, following new ones as they arrive, and
stop where their run ends, so a reconnect gap is never played across. Old
segments go by time, by a byte cap, and when the disk runs low; a segment
a reader still has open is only forgotten then, and deleted once it is let
go (Windows cannot delete an open file).
"""
from __future__ import annotations

import os
import shutil
import threading
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

TS_PACKET = 188


@dataclass
class Segment:
    seq: int
    run: int
    t0: float           # wall clock of its first byte
    t1: float           # of its newest byte
    size: int
    path: Path
    closed: bool = False


class StoreClosed(Exception):
    """The store was closed while a reader waited on it."""


class SegmentStore:
    SEG_SECONDS = 2.0
    MIN_FREE_BYTES = 1 << 30

    def __init__(self, root: Path, keep_seconds: float, max_bytes: int,
                 clock: Callable[[], float] = time.time,
                 min_free: int | None = None) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.keep_seconds = keep_seconds
        self.max_bytes = max_bytes
        self.min_free = self.MIN_FREE_BYTES if min_free is None else min_free
        self._clock = clock
        self._cond = threading.Condition()
        self._segs: list[Segment] = []
        self._by_seq: dict[int, Segment] = {}
        self._run = 0
        self._run_bytes = 0
        self._next_seq = 0
        self._cur: Segment | None = None
        self._fh: BinaryIO | None = None
        self._pins: dict[int, int] = {}
        self._doomed: dict[int, Path] = {}
        self._closed = False
        self._low_disk = False
        self.trimmed = False    # space, not time, has shortened it

    # -- writing -------------------------------------------------------------

    def append(self, data: bytes) -> None:
        if not data:
            return
        with self._cond:
            if self._closed:
                return
            now = self._clock()
            view = memoryview(data)
            while view:
                cur = self._cur
                if cur is None or now - cur.t0 >= self.SEG_SECONDS:
                    # Roll on a TS packet boundary of the run, so every
                    # segment starts on a packet.
                    cut = (-self._run_bytes) % TS_PACKET if cur else 0
                    if cut and cur is not None:
                        self._write(view[:cut], now)
                        view = view[cut:]
                    self._roll(now)
                    continue
                self._write(view, now)
                break
            self._evict(now)
            self._cond.notify_all()

    def _write(self, view: memoryview, now: float) -> None:
        assert self._cur is not None and self._fh is not None
        if self._low_disk:
            return
        self._fh.write(view)
        self._fh.flush()
        self._cur.size += len(view)
        self._cur.t1 = now
        self._run_bytes += len(view)

    def _roll(self, now: float) -> None:
        self._close_current()
        seq = self._next_seq
        self._next_seq += 1
        path = self.root / f"{seq:08d}.ts"
        self._fh = open(path, "wb")
        seg = Segment(seq, self._run, now, now, 0, path)
        self._segs.append(seg)
        self._by_seq[seq] = seg
        self._cur = seg
        self._check_disk()

    def _close_current(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
        if self._cur is not None:
            self._cur.closed = True
            self._cur = None

    def new_run(self) -> None:
        """The provider connection ended: what follows is a run of its own."""
        with self._cond:
            self._close_current()
            self._run += 1
            self._run_bytes = 0
            self._cond.notify_all()

    def _check_disk(self) -> None:
        try:
            free = shutil.disk_usage(self.root).free
        except OSError:
            return
        self._low_disk = free < self.min_free

    # -- retention -----------------------------------------------------------

    def _evict(self, now: float, keep_one: bool = True) -> None:
        total = sum(s.size for s in self._segs)
        while len(self._segs) > (1 if keep_one else 0):
            oldest = self._segs[0]
            too_old = now - oldest.t1 > self.keep_seconds
            too_big = total > self.max_bytes
            if not (too_old or too_big or self._low_disk):
                break
            if not too_old:
                self.trimmed = True
            self._segs.pop(0)
            del self._by_seq[oldest.seq]
            total -= oldest.size
            self._forget(oldest)
            if self._low_disk and total <= self.max_bytes // 4:
                break

    def _forget(self, seg: Segment) -> None:
        """Delete a segment that is out of the store - once no reader has it
        open."""
        if self._pins.get(seg.seq):
            self._doomed[seg.seq] = seg.path
        else:
            self._unlink(seg.path)

    def expire(self) -> None:
        """Retention for a store nothing writes to (a channel left)."""
        with self._cond:
            if not self._closed:
                self._evict(self._clock(), keep_one=False)

    def trim(self, nbytes: int) -> int:
        """Let the oldest segments go until *nbytes* are freed - all of
        them, if need be. The bytes freed."""
        freed = 0
        with self._cond:
            while self._segs and freed < nbytes:
                seg = self._segs.pop(0)
                if seg is self._cur:
                    self._close_current()
                del self._by_seq[seg.seq]
                freed += seg.size
                self._forget(seg)
            if freed:
                self.trimmed = True
        return freed

    def total_bytes(self) -> int:
        with self._cond:
            return sum(s.size for s in self._segs)

    @staticmethod
    def _unlink(path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass

    # -- reading -------------------------------------------------------------

    def span(self) -> tuple[float, float] | None:
        """Wall-clock times of the oldest and the newest byte held."""
        with self._cond:
            if not self._segs:
                return None
            return self._segs[0].t0, self._segs[-1].t1

    def runs(self) -> list[tuple[int, float, float]]:
        """(run, first byte, last byte) of each run still held."""
        with self._cond:
            out: list[tuple[int, float, float]] = []
            for s in self._segs:
                if out and out[-1][0] == s.run:
                    out[-1] = (s.run, out[-1][1], s.t1)
                else:
                    out.append((s.run, s.t0, s.t1))
            return out

    def segment_at(self, t: float) -> Segment | None:
        """The segment to start from to play wall-clock time *t*: the last
        one beginning at or before it - or, in a gap or before the first,
        the next one there is."""
        with self._cond:
            best = None
            for s in self._segs:
                if s.t0 <= t:
                    best = s
                else:
                    return best if best is not None and t <= best.t1 + 0.5 \
                        else s
            return best

    def segment_after(self, t: float) -> Segment | None:
        """The first segment beginning after *t* (the next run after a gap)."""
        with self._cond:
            for s in self._segs:
                if s.t0 > t:
                    return s
            return None

    def live_segment(self, lead: float) -> Segment | None:
        """Where to join live: the segment *lead* seconds behind the newest
        byte, within the newest run, so mpv has something to start from."""
        with self._cond:
            if not self._segs:
                return None
            newest = self._segs[-1]
            best = newest
            for s in reversed(self._segs):
                if s.run != newest.run:
                    break
                best = s
                if s.t0 <= newest.t1 - lead:
                    break
            return best

    def first_from(self, seq: int) -> Segment | None:
        """The oldest segment held numbered *seq* or later."""
        with self._cond:
            for s in self._segs:
                if s.seq >= seq:
                    return s
            return None

    def newest(self) -> Segment | None:
        with self._cond:
            return self._segs[-1] if self._segs else None

    def _live_data(self) -> bool:
        # Bytes of the current run: after a reconnect or a channel come back
        # to, the end of the run before is history, not live.
        return bool(self._segs and self._segs[-1].size
                    and self._segs[-1].run == self._run)

    def wait_for_data(self, timeout: float) -> bool:
        """Wait until the current run has bytes (or the store closes)."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while not self._live_data() and not self._closed:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._cond.wait(left)
            return self._live_data()

    def read_from(self, seq: int, chunk: int = 256 * 1024,
                  wait: float = 1.0,
                  stop: Callable[[], bool] | None = None,
                  on_segment: Callable[[Segment], None] | None = None,
                  ) -> Generator[bytes, None, None]:
        """The bytes from segment *seq* on, following the store live, until
        the run ends, the next segment is gone or the store closes - or,
        once *stop* says so, until what there is has been read. *on_segment*
        hears of each segment as reading moves onto it."""
        with self._cond:
            seg = self._by_seq.get(seq)
            if seg is None:
                return
            run = seg.run
        while True:
            if on_segment is not None:
                on_segment(seg)
            self._pin(seg.seq)
            try:
                with open(seg.path, "rb") as fh:
                    pos = 0
                    while True:
                        data = fh.read(chunk)
                        if data:
                            pos += len(data)
                            yield data
                            continue
                        with self._cond:
                            if self._closed:
                                return
                            if seg.closed and pos >= seg.size:
                                break
                            if pos >= seg.size:
                                if stop is not None and stop():
                                    return
                                self._cond.wait(wait)
            except OSError:
                return
            finally:
                self._unpin(seg.seq)
            with self._cond:
                nxt = self._by_seq.get(seg.seq + 1)
                while nxt is None and not self._closed:
                    # The next segment of this run not started yet, or the
                    # run has ended.
                    if self._run != run or seg.seq + 1 < self._next_seq:
                        return
                    if stop is not None and stop():
                        return
                    self._cond.wait(wait)
                    nxt = self._by_seq.get(seg.seq + 1)
                if nxt is None or nxt.run != run:
                    return
                seg = nxt

    def _pin(self, seq: int) -> None:
        with self._cond:
            self._pins[seq] = self._pins.get(seq, 0) + 1

    def _unpin(self, seq: int) -> None:
        with self._cond:
            n = self._pins.get(seq, 0) - 1
            if n > 0:
                self._pins[seq] = n
                return
            self._pins.pop(seq, None)
            path = self._doomed.pop(seq, None)
        if path is not None:
            self._unlink(path)

    # -- end -----------------------------------------------------------------

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._close_current()
            self._cond.notify_all()

    def wipe(self) -> None:
        """Delete everything held (after close)."""
        self.close()
        shutil.rmtree(self.root, ignore_errors=True)


def sweep_abandoned(base: Path, older_than: float = 600.0) -> int:
    """Remove session folders nobody writes to any more - left by a crash
    or a killed app. A running session writes a segment every two seconds,
    so a folder untouched for ten minutes is no one's."""
    removed = 0
    try:
        entries = list(base.iterdir())
    except OSError:
        return 0
    now = time.time()
    for entry in entries:
        try:
            if entry.is_dir() and now - entry.stat().st_mtime > older_than:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def session_dir(base: Path, token: str) -> Path:
    return base / f"{os.getpid()}-{token[:12]}"
