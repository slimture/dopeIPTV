"""The rolling store of an application-kept live rewind buffer.

A live channel's MPEG-TS bytes go into short segment files, a run of them
per provider connection. Readers - the local server mpv plays from - take
the bytes from any segment on, following new ones as they arrive, and
stop where their run ends, so a reconnect gap is never played across. Old
segments go by time, by a byte cap, and when the disk runs low; a segment
a reader still has open is only forgotten then, and deleted once it is let
go (Windows cannot delete an open file).

A channel watched privately from its start keeps its segments in memory
where the system has a folder there for this user alone (``memory_base``:
Linux's ``$XDG_RUNTIME_DIR``, a tmpfs gone at logout and at a restart), with
a budget of its own (``memory_budget``) - so nothing of it ever reaches the
disk. Elsewhere it is on disk as any buffer, marked (``PRIVATE_MARK``) and
deleted as it is left, and at the next start after a crash.
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .log import log, open_private, private_folder

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


class SegmentStore:
    SEG_SECONDS = 2.0
    MIN_FREE_BYTES = 1 << 30

    def __init__(self, root: Path, keep_seconds: float, max_bytes: int,
                 clock: Callable[[], float] = time.time,
                 min_free: int | None = None, memory: bool = False) -> None:
        self.root = root
        # What was watched, this user's alone: the folder 0700 (narrowed
        # when it was there before), each segment 0600 (open_private).
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_folder(str(self.root))
        # A buffer meant for memory: its own folder checked where it really
        # is before a byte is written - else nothing is (NotInMemory).
        if memory and not memory_folder(self.root):
            raise NotInMemory(f"not in memory: {self.root.name}")
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
        self._fh = open_private(str(path), "wb")
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

    def next_run_after(self, t: float, seq: int | None = None
                       ) -> Segment | None:
        """Where playback goes on once a run has been read to its end: the
        first segment of a later run than the one being played - segment
        *seq* when that is known and still held, else the one wall-clock
        time *t* falls in. None while no later run has begun.

        (The first segment beginning after *t* was asked for before: on a
        run of one short segment that is the very segment just played, and
        playback opened it again and again.)"""
        with self._cond:
            cur = self._by_seq.get(seq) if seq is not None else None
            if cur is None:
                for s in self._segs:
                    if s.t0 <= t:
                        cur = s
                    else:
                        break
            if cur is None:
                return self._segs[0] if self._segs else None
            for s in self._segs:
                if s.seq > cur.seq and s.run != cur.run:
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
            if not self._pins:
                self._cond.notify_all()         # wipe() may be waiting for this
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

    #: How long wipe() waits for readers to let go of what they have open.
    WIPE_WAIT = 5.0

    def wipe(self) -> None:
        """Delete everything held (after close; off the window's thread).
        Windows refuses to delete a file that is open, so a segment a reader
        still had open stayed behind with its folder: the close wakes the
        readers, they let go, and what is left goes then."""
        self.close()
        shutil.rmtree(self.root, ignore_errors=True)
        if not self.root.exists():
            return
        deadline = time.monotonic() + self.WIPE_WAIT
        with self._cond:
            while self._pins:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self._cond.wait(left)
        shutil.rmtree(self.root, ignore_errors=True)


#: In a session folder whose channel was watched privately (core/privacy.py).
PRIVATE_MARK = ".private"


def mark_private(root: Path) -> None:
    """Say on disk that *root* holds a channel watched privately: should
    the app be killed before it deletes it, the next start does at once."""
    try:
        (root / PRIVATE_MARK).touch()
    except OSError:
        pass


def _pid_of(entry: Path) -> int:
    try:
        return int(entry.name.split("-", 1)[0])
    except ValueError:
        return 0


def _gone(pid: int) -> bool:
    """Whether the process that made a folder has ended - known for sure
    only where asking is harmless (not Windows, where os.kill ends it)."""
    if pid <= 0 or os.name == "nt":
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def sweep_abandoned(base: Path, older_than: float = 600.0) -> int:
    """Remove session folders nobody writes to any more - left by a crash
    or a killed app. A running session writes a segment every two seconds,
    so a folder untouched for ten minutes is no one's. One watched
    privately (PRIVATE_MARK) whose process has ended goes whatever its
    age: a private channel's buffer is never for a later session."""
    removed = 0
    try:
        entries = list(base.iterdir())
    except OSError:
        return 0
    now = time.time()
    for entry in entries:
        try:
            if not entry.is_dir():
                continue
            left_private = ((entry / PRIVATE_MARK).exists()
                            and _pid_of(entry) != os.getpid()
                            and _gone(_pid_of(entry)))
            if left_private or now - entry.stat().st_mtime > older_than:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def session_dir(base: Path, token: str) -> Path:
    return base / f"{os.getpid()}-{token[:12]}"


# -- a private session's buffer in memory ---------------------------------------

#: The most a private session keeps in memory, and of what that memory has
#: free at most a half (the rest of the system lives there too).
MEMORY_MAX = 1 << 30
#: What must stay free in that memory before older segments go.
MEMORY_MIN_FREE = 64 << 20
#: Below this a buffer in memory is not worth it: the disk, as before.
MEMORY_LEAST = 64 << 20
#: The kinds of file system that live in memory alone.
_IN_MEMORY = ("tmpfs", "ramfs")


def _unescape(field: str) -> str:
    # /proc/mounts writes a space, a tab, a newline and a backslash in octal.
    for code, ch in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        field = field.replace(code, ch)
    return field


def in_memory(path: Path, mounts: str | None = None) -> bool:
    """Whether *path* is on a file system in memory, by the mount table
    (Linux's /proc/self/mounts): the longest mount point it is under."""
    if mounts is None:
        try:
            with open("/proc/self/mounts", encoding="utf-8", errors="replace") as fh:
                mounts = fh.read(1 << 20)
        except OSError:
            return False
    where = os.path.realpath(str(path))
    best, kind = "", ""
    for line in mounts.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        point = _unescape(parts[1]).rstrip("/") or "/"
        if (where == point or where.startswith(point.rstrip("/") + "/")) and len(point) > len(best):
            best, kind = point, parts[2]
    return kind in _IN_MEMORY


def memory_base(env: dict | None = None, mounts: str | None = None,
                platform: str | None = None) -> Path | None:
    """Where a private session's buffer goes in memory: a folder under
    ``$XDG_RUNTIME_DIR`` - only on Linux, only when it is this user's own
    (0700, theirs) and on tmpfs; None anywhere else."""
    if (platform or sys.platform) != "linux":
        return None
    run = (os.environ if env is None else env).get("XDG_RUNTIME_DIR") or ""
    if not run or not os.path.isabs(run):
        return None
    try:
        st = os.stat(run)
    except OSError:
        return None
    import stat as _stat
    if not _stat.S_ISDIR(st.st_mode) or st.st_mode & 0o077 or st.st_uid != os.getuid():
        return None
    if not in_memory(Path(run), mounts):
        return None
    return Path(run) / "dopeiptv-dvr"


class NotInMemory(OSError):
    """A folder meant to be in memory is not, or is not this user's own."""


def memory_folder(path: Path, mounts: str | None = None) -> bool:
    """Whether *path* - made already - is where a buffer in memory may be
    written: a folder itself (never a link to one), this user's own, 0700
    (narrowed when it is not), and on a file system in memory where it
    really is (the mount table: a link above it, or a disk mounted on or
    under it, is found so)."""
    import stat as _stat
    try:
        st = os.lstat(str(path))
        if not _stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
            return False
        if st.st_mode & 0o077:
            os.chmod(str(path), 0o700)
            if os.lstat(str(path)).st_mode & 0o077:
                return False
    except OSError:
        return False
    return in_memory(path, mounts)


def memory_budget(base: Path, max_bytes: int, mounts: str | None = None) -> int:
    """What a private session may keep in memory at *base*: the rewind
    buffer's own cap, MEMORY_MAX, half of what is free there - 0 when that
    is too little to be worth it, or when the folder made is not in memory
    or not this user's own (memory_folder)."""
    try:
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not memory_folder(base, mounts):
            log.warning("DVR: the folder for a private buffer in memory is not in memory "
                        "or not this user's own - the private buffer goes on disk, marked")
            return 0
        free = shutil.disk_usage(base).free
    except OSError:
        return 0
    budget = min(int(max_bytes), MEMORY_MAX, max(0, free - MEMORY_MIN_FREE) // 2)
    return budget if budget >= MEMORY_LEAST else 0
