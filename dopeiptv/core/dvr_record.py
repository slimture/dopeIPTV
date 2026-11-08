"""A recording made from an application-kept rewind buffer.

It copies the store's bytes into a file from a segment on and follows the
channel live: a pause, a rewind or another channel on screen changes
nothing for it, and it needs no connection of its own - the buffer's one
provider connection feeds both. It can begin in the past, as far back as
the buffer holds. Across a reconnect gap it goes on with the next run.
Stopped, it writes what the buffer holds up to then and ends.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from .dvr_store import Segment, SegmentStore
from .log import log


class BufferRecorder:
    POLL = 0.5          # waiting for the next run after a reconnect gap

    def __init__(self, store: SegmentStore, path: str | Path, first: Segment,
                 on_done: Callable[["BufferRecorder"], None] | None = None
                 ) -> None:
        self.store = store
        self.path = Path(path)
        self.first = first
        self.on_done = on_done
        self.bytes = 0
        self.error = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="dvr-record",
                                        daemon=True)

    def start(self) -> "BufferRecorder":
        self._thread.start()
        return self

    def stop(self, wait: float = 0.0) -> None:
        """Write what the buffer holds now, then end."""
        self._stop.set()
        if wait and self._thread.is_alive():
            self._thread.join(wait)

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def _run(self) -> None:
        last = [self.first]

        def moved(seg: Segment) -> None:
            last[0] = seg

        try:
            with open(self.path, "ab") as fh:
                seq: int | None = self.first.seq
                while seq is not None:
                    for chunk in self.store.read_from(
                            seq, stop=self._stop.is_set, on_segment=moved):
                        fh.write(chunk)
                        self.bytes += len(chunk)
                    seq = None
                    # The run ended (a reconnect), the reader fell behind
                    # retention, the store closed - or it was stopped.
                    while not self.store.closed:
                        nxt = self.store.first_from(last[0].seq + 1)
                        if nxt is not None:
                            seq = nxt.seq
                            break
                        if self._stop.is_set():
                            break
                        self._stop.wait(self.POLL)
        except OSError as e:
            self.error = str(e)
            log.warning("DVR recording: %s", e)
        finally:
            log.info("DVR recording ended: %.1f MB", self.bytes / 1e6)
            if self.on_done is not None:
                try:
                    self.on_done(self)
                except Exception as e:
                    log.debug("DVR recording done callback: %s", e)
