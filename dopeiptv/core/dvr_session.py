"""An application-kept rewind buffer for one live channel: the provider
connection, the store and the local server.

Watched, it has all three. Left for another channel it is frozen: the
provider connection closes at once - another channel may need the
account's only one - and the history stays on disk, without a server,
until the channel is watched again (resume: a new connection, a new run
after a gap) or the pool lets it go. A recording made from it keeps its
connection open while it runs, watched or not.

Steps one and two of the design agreed in the issue that followed pull
request #22. Opt-in; with it off, live channels play exactly as before.
"""
from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .dvr_ingest import Ingest
from .dvr_record import BufferRecorder
from .dvr_server import LIVE_LEAD, LocalServer
from .dvr_store import Segment, SegmentStore, session_dir
from .log import log

IngestFactory = Callable[[str, SegmentStore], Ingest]


class DvrSession:
    KEY_LEAD = 3.0      # seconds a recording reaches back for a key frame
    def __init__(self, url: str, base: Path, keep_seconds: float,
                 max_bytes: int, ingest: Ingest | None = None,
                 key: str | None = None,
                 ingest_factory: IngestFactory | None = None) -> None:
        self.url = url
        self.key = key or url
        self.token = secrets.token_hex(8)
        self.store = SegmentStore(session_dir(base, self.token),
                                  keep_seconds, max_bytes)
        # Session time 0, kept across freezes: positions in the row stay put.
        self.origin = time.time()
        self.last_used = self.origin
        self._factory = ingest_factory or Ingest
        self.server: LocalServer | None = LocalServer(self.store)
        self.ingest = ingest or self._factory(url, self.store)
        self.recorders: list[BufferRecorder] = []
        self.on_recording_done: Callable[["DvrSession"], None] | None = None
        self._closed = False
        self._frozen = False
        self._freezing: threading.Thread | None = None
        self._lock = threading.Lock()
        self.connections = 0
        self.ingest.start()

    # -- where to play from --------------------------------------------------

    def _server(self) -> LocalServer:
        if self.server is None:
            raise RuntimeError("DVR session has no server (frozen)")
        return self.server

    def url_for(self, seg: Segment) -> str:
        return self._server().url(seg.seq)

    def live_url(self) -> str:
        """Live, from a few seconds back: the server picks the segment when
        mpv asks - and waits for the provider's first bytes if need be."""
        return self._server().live_url()

    def live_segment(self) -> Segment | None:
        return self.store.live_segment(LIVE_LEAD)

    def segment_at(self, t: float) -> Segment | None:
        return self.store.segment_at(t)

    def segment_after(self, t: float) -> Segment | None:
        return self.store.segment_after(t)

    def next_run_after(self, t: float, seq: int | None = None
                       ) -> Segment | None:
        return self.store.next_run_after(t, seq)

    def span(self) -> tuple[float, float] | None:
        return self.store.span()

    def runs(self) -> list[tuple[int, float, float]]:
        return self.store.runs()

    @property
    def contested(self) -> bool:
        """The provider keeps taking the connection away - another device
        or app on the same account, most likely."""
        return not self._closed and bool(
            getattr(self.ingest, "contested", False))

    @property
    def state(self) -> str:
        if self._closed:
            return "stopped"
        if self._frozen:
            return "frozen"
        return self.ingest.state

    @property
    def frozen(self) -> bool:
        return self._frozen

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def trimmed(self) -> bool:
        return self.store.trimmed

    @property
    def streaming(self) -> bool:
        """It has a provider connection (or is opening one)."""
        return not (self._closed or self._frozen)

    # -- recording -----------------------------------------------------------

    def record(self, path: str | Path, from_time: float | None = None
               ) -> BufferRecorder:
        """Record into *path* from wall-clock *from_time* (None: live) on."""
        if not self.streaming:
            raise RuntimeError("DVR session is not streaming")
        # A little before the picture asked for: the recording begins on
        # the key frame at or before it (KeyframeStart), not after it.
        first = (self.store.segment_at(from_time - self.KEY_LEAD)
                 if from_time is not None else None) \
            or self.live_segment() or self.store.newest()
        if first is None:
            raise RuntimeError("DVR buffer holds nothing yet")
        rec = BufferRecorder(self.store, path, first,
                             on_done=self._recording_ended)
        with self._lock:
            self.recorders.append(rec)
        rec.start()
        log.info("DVR: recording from the buffer, %.0f s back",
                 max(0.0, time.time() - first.t0))
        return rec

    @property
    def recording(self) -> bool:
        with self._lock:
            return any(r.alive and not r.stopping for r in self.recorders)

    def _recording_ended(self, rec: BufferRecorder) -> None:
        with self._lock:
            if rec in self.recorders:
                self.recorders.remove(rec)
        cb = self.on_recording_done
        if cb is not None:
            cb(self)

    # -- freeze and resume ---------------------------------------------------

    def freeze(self) -> None:
        """Nobody watches it: the provider connection now, the server behind
        it; the history stays. What follows a resume is a run of its own."""
        if self._closed or self._frozen:
            return
        self._frozen = True
        ingest, server = self.ingest, self.server
        self.server = None
        self.connections += ingest.connections
        ingest.stop()

        def finish() -> None:
            ingest.stop(wait=5.0)
            # Only once the old connection can append no more.
            self.store.new_run()
            if server is not None:
                server.close()

        t = threading.Thread(target=finish, name="dvr-freeze", daemon=True)
        self._freezing = t
        t.start()
        log.info("DVR: %s left - history kept, provider connection closed",
                 self._label())

    def resume(self, url: str | None = None) -> None:
        """Watched again: a new provider connection, after a gap."""
        if self._closed or not self._frozen:
            return
        if self._freezing is not None:
            self._freezing.join(6.0)
            self._freezing = None
        self.url = url or self.url
        self.server = LocalServer(self.store)
        self.ingest = self._factory(self.url, self.store)
        self._frozen = False
        self.ingest.start()
        log.info("DVR: %s again - its history kept", self._label())

    def expire(self) -> None:
        self.store.expire()

    def _label(self) -> str:
        span = self.store.span()
        held = 0.0 if span is None else span[1] - span[0]
        return f"channel ({held / 60:.1f} min held)"

    # -- end -----------------------------------------------------------------

    def close(self) -> None:
        """The provider connection now; the rest (recordings, server, files)
        behind it, off the caller's thread."""
        if self._closed:
            return
        self._closed = True
        with self._lock:
            recs = list(self.recorders)
        for r in recs:
            r.stop()
        self.ingest.stop()
        threading.Thread(target=self._finish, args=(recs,), name="dvr-close",
                         daemon=True).start()

    def _finish(self, recs: list[BufferRecorder]) -> None:
        for r in recs:
            r.join(5.0)         # what they hold is written first
        if self._freezing is not None:
            self._freezing.join(6.0)
        self.store.close()
        if self.server is not None:
            self.server.close()
        self.ingest.stop(wait=5.0)
        self.store.wipe()
        log.info("DVR: session closed, %d provider connection(s) in all",
                 self.connections + self.ingest.connections)
