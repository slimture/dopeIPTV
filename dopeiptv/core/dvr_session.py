"""An application-kept rewind buffer for one live channel: the provider
connection, the store and the local server, started and ended together.

Step one of the design agreed in the issue that followed pull request #22:
the active channel only, history dropped on a channel change, the store
wiped at start and when the app ends. Opt-in; with it off, live channels
play exactly as before.
"""
from __future__ import annotations

import secrets
import threading
from pathlib import Path

from .dvr_ingest import Ingest
from .dvr_server import LIVE_LEAD, LocalServer
from .dvr_store import Segment, SegmentStore, session_dir
from .log import log


class DvrSession:
    def __init__(self, url: str, base: Path, keep_seconds: float,
                 max_bytes: int, ingest: Ingest | None = None) -> None:
        self.token = secrets.token_hex(8)
        self.store = SegmentStore(session_dir(base, self.token),
                                  keep_seconds, max_bytes)
        self.server = LocalServer(self.store)
        self.ingest = ingest or Ingest(url, self.store)
        self._closed = False
        self.ingest.start()

    # -- where to play from --------------------------------------------------

    def url_for(self, seg: Segment) -> str:
        return self.server.url(seg.seq)

    def live_url(self) -> str:
        """Live, from a few seconds back: the server picks the segment when
        mpv asks - and waits for the provider's first bytes if need be."""
        return self.server.live_url()

    def live_segment(self) -> Segment | None:
        return self.store.live_segment(LIVE_LEAD)

    def segment_at(self, t: float) -> Segment | None:
        return self.store.segment_at(t)

    def segment_after(self, t: float) -> Segment | None:
        return self.store.segment_after(t)

    def span(self) -> tuple[float, float] | None:
        return self.store.span()

    @property
    def state(self) -> str:
        return "stopped" if self._closed else self.ingest.state

    @property
    def trimmed(self) -> bool:
        return self.store.trimmed

    # -- end -----------------------------------------------------------------

    def close(self) -> None:
        """The provider connection now; the rest (server, files) behind it,
        off the caller's thread."""
        if self._closed:
            return
        self._closed = True
        self.ingest.stop()
        threading.Thread(target=self._finish, name="dvr-close",
                         daemon=True).start()

    def _finish(self) -> None:
        self.store.close()
        self.server.close()
        self.ingest.stop(wait=5.0)
        self.store.wipe()
        log.info("DVR: session closed, %d provider connection(s) in all",
                 self.ingest.connections)
