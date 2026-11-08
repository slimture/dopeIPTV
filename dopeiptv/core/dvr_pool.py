"""The application-kept rewind buffers of the channels watched this session.

The channel on screen has its session streaming. Another one chosen
freezes the one left (its provider connection closes first, its history
stays); coming back resumes it, with a gap between what was kept and
what follows. A recording made from a buffer keeps that session
streaming while it runs, whichever channel is on screen.

One budget for all of them, as agreed in the issue that followed pull
request #22: history older than the rewind setting goes, and over the
space cap - or with the disk running low - the channels left longest ago
give way first, then the one watched. At most MAX_FROZEN channels are
kept. Everything is wiped when the app ends (and swept at the next start
after a crash): history is for this session only.
"""
from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .dvr_session import DvrSession, IngestFactory
from .dvr_store import SegmentStore
from .log import log, redact_url


class DvrPool:
    MAX_FROZEN = 8
    TICK = 5.0              # seconds between checks of time and space

    def __init__(self, base: Path, keep_seconds: float, max_bytes: int,
                 ingest_factory: IngestFactory | None = None,
                 clock: Callable[[], float] = time.time,
                 min_free: int | None = None) -> None:
        self.base = base
        self.keep_seconds = keep_seconds
        self.max_bytes = max_bytes
        self.min_free = SegmentStore.MIN_FREE_BYTES if min_free is None \
            else min_free
        self._factory = ingest_factory
        self._clock = clock
        self._sessions: dict[str, DvrSession] = {}
        self.active: DvrSession | None = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- the channel on screen -----------------------------------------------

    def open(self, url: str, key: str | None = None) -> DvrSession:
        """The session for a channel about to be watched: its own again if
        it was watched before (or is being recorded), else a new one."""
        key = key or url
        with self._lock:
            s = self._sessions.get(key)
            if s is not None and s.closed:
                s = None
            if s is None:
                s = DvrSession(url, self.base, self.keep_seconds,
                               self.max_bytes, key=key,
                               ingest_factory=self._factory)
                s.on_recording_done = self._recording_done
                self._sessions[key] = s
            elif s.frozen:
                s.resume(url)
            s.store.keep_seconds = self.keep_seconds
            s.last_used = self._clock()
            self.active = s
            self._enforce()
            self._start_ticking()
            log.info("DVR pool: watching %s (%d channel(s) kept)",
                     redact_url(url), len(self._sessions))
            return s

    def release(self, s: DvrSession) -> None:
        """The player is done with *s*: frozen - unless a recording still
        reads it, which keeps its connection until it ends."""
        with self._lock:
            if self.active is s:
                self.active = None
            s.last_used = self._clock()
            if s.closed:
                return
            if s.recording:
                log.info("DVR pool: a recording keeps the channel left "
                         "streaming")
            else:
                s.freeze()
            self._enforce()

    def _recording_done(self, s: DvrSession) -> None:
        with self._lock:
            if s is not self.active and not s.recording and s.streaming:
                s.freeze()
            self._enforce()

    def configure(self, keep_seconds: float, max_bytes: int) -> None:
        """The rewind settings, which may change between channels."""
        with self._lock:
            self.keep_seconds = keep_seconds
            self.max_bytes = max_bytes
            for s in self._sessions.values():
                s.store.keep_seconds = keep_seconds

    # -- what it holds -------------------------------------------------------

    @property
    def sessions(self) -> list[DvrSession]:
        with self._lock:
            return list(self._sessions.values())

    def total_bytes(self) -> int:
        return sum(s.store.total_bytes() for s in self.sessions)

    def connections(self) -> int:
        """Provider connections the buffers hold (or are opening) now."""
        return sum(1 for s in self.sessions if s.streaming)

    # -- the budget ----------------------------------------------------------

    def enforce(self) -> None:
        with self._lock:
            self._enforce()

    def _enforce(self) -> None:
        for s in list(self._sessions.values()):
            if s.closed:
                self._sessions.pop(s.key, None)
            elif s.frozen:
                s.expire()
                if s.span() is None:
                    self._drop(s, "nothing left in time")
        frozen = sorted((s for s in self._sessions.values() if s.frozen),
                        key=lambda s: s.last_used)
        while len(frozen) > self.MAX_FROZEN:
            self._drop(frozen.pop(0), "too many channels kept")
        if frozen and self._low_disk():
            for s in frozen:
                self._drop(s, "the disk is running low")
            frozen = []
        # The streaming ones share the cap; what they hold is theirs to
        # trim. The frozen ones give way to them, oldest-watched first.
        live = [s for s in self._sessions.values() if s.streaming]
        for s in live:
            s.store.max_bytes = self.max_bytes // max(1, len(live))
        total = sum(s.store.total_bytes() for s in self._sessions.values())
        for s in frozen:
            if total <= self.max_bytes:
                break
            total -= s.store.trim(total - self.max_bytes)
            if s.span() is None:
                self._drop(s, "space for the channel watched")

    def _drop(self, s: DvrSession, why: str) -> None:
        self._sessions.pop(s.key, None)
        s.close()
        log.info("DVR pool: a channel's history let go (%s)", why)

    def _low_disk(self) -> bool:
        try:
            return shutil.disk_usage(self.base).free < self.min_free
        except OSError:
            return False

    # -- housekeeping --------------------------------------------------------

    def _start_ticking(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._tick, name="dvr-pool",
                                        daemon=True)
        self._thread.start()

    def _tick(self) -> None:
        while not self._stop.wait(self.TICK):
            try:
                self.enforce()
            except Exception as e:
                log.debug("DVR pool: %s", e)

    def close_all(self, wait: float = 0.0) -> None:
        """Every buffer gone - the app ends, or the option was turned off.
        Recordings write what they hold first."""
        self._stop.set()
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self.active = None
        for s in sessions:
            s.close()
        if wait:
            deadline = time.monotonic() + wait
            for s in sessions:
                for r in list(s.recorders):
                    r.join(max(0.0, deadline - time.monotonic()))
