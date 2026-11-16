"""Server mode's guides kept fresh (services/home_server.py).

The guide the server hands out (``/xmltv.xml``) is each served source's
guide as the app keeps it on disk. The window refreshes only its active
source's guide, and without a window (``dopeiptv --server``) nothing did:
the server handed out the last guide downloaded on that computer until it
ran out. ``GuideKeeper`` downloads a served source's guide again once its
copy is older than the app's own rule (``XmltvGuide.CACHE_TTL``), one source
at a time, on a thread of its own, at most one try an hour per source. The
source the window keeps itself is left to it (``kept``). Only the guide's
own address is asked - never a stream, so no connection to the account.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..core.log import log


class GuideKeeper:
    #: The first look, a while after the server started (the window's own
    #: guide load and the lists go first).
    FIRST_S = 45.0
    #: How often the guides are looked at - the window's own guide check.
    EVERY_S = 20 * 60.0
    #: At most one try an hour per source, as the window's own refresh.
    RETRY_S = 3600.0

    def __init__(self, sources: Callable[[], list[dict]], client: Callable[[dict], Any],
                 kept: Callable[[], set[str]] | None = None,
                 guide_path: Callable[[str], str] | None = None,
                 max_age: float | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._sources = sources
        self._client = client
        self._kept = kept
        self._guide_path = guide_path
        self._max_age = max_age
        self._clock = clock
        self._tried: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- on and off ---------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop,),
                                        name="server-guides", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stops looking; a download under way ends by itself (its own
        timeouts)."""
        self._stop.set()
        self._thread = None

    def _run(self, stop: threading.Event) -> None:
        if stop.wait(self.FIRST_S):
            return
        while True:
            try:
                self.check(stop)
            except Exception as e:
                log.debug("server: guides not looked at: %s", e)
            if stop.wait(self.EVERY_S):
                return

    # -- the guides ---------------------------------------------------------------------
    def path(self, pid: str) -> Path:
        if self._guide_path is None:
            from ..providers.epg import epg_cache_path
            self._guide_path = epg_cache_path
        return Path(self._guide_path(pid))

    def max_age(self) -> float:
        if self._max_age is None:
            from ..providers.epg import XmltvGuide
            self._max_age = float(XmltvGuide.CACHE_TTL)
        return self._max_age

    def due(self, pid: str, now: float | None = None) -> bool:
        """Whether source *pid*'s guide on disk is missing or older than the
        app's rule - and no try was made in the last hour."""
        last = self._tried.get(pid)
        if last is not None and self._clock() - last < self.RETRY_S:
            return False
        try:
            age = (time.time() if now is None else now) - self.path(pid).stat().st_mtime
        except OSError:
            return True
        return age > self.max_age()

    def check(self, stop: threading.Event | None = None) -> int:
        """Look at every served source's guide once; the number refreshed."""
        kept = set(self._kept() if self._kept is not None else ())
        done = 0
        for pl in list(self._sources() or []):
            if stop is not None and stop.is_set():
                break
            pid = str(pl.get("id") or "")
            if not pid or pid in kept or pl.get("kind") == "local" or not self.due(pid):
                continue
            self._tried[pid] = self._clock()
            if self.refresh(pl, stop):
                done += 1
        return done

    def refresh(self, pl: dict, stop: threading.Event | None = None) -> bool:
        from ..providers.epg import XmltvGuide
        pid = str(pl.get("id") or "")
        try:
            guide = XmltvGuide(self._client(pl), pl.get("epg_url") or None,
                               cache_path=str(self.path(pid)))
            if stop is not None and stop.is_set():
                return False
            size = guide.refresh_file()
        except Exception as e:
            log.info("server: the guide of source %s was not downloaded (%s)", pid, e)
            return False
        log.info("server: the guide of source %s downloaded again (%d KB)", pid, size // 1024)
        return True
