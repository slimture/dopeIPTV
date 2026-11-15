"""Your statistics in the window: the time the docked player plays counted
into the viewing log (core/viewing_stats.py), and its window from the
History menu (ui/stats_window.py).

It only watches: the playback path tells it a stream started
(``_stats_begin``, beside the health and events hooks), the player's
signals tell it of a pause, a stop, an end or an error, and a minute's
timer adds what played since - only while the player has a stream and is
not paused, never more than the timer's own interval plus a little (a
suspended computer counts nothing). The log saves every few minutes and as
the app closes. Private viewing adds nothing: the log checks it itself.
Multiview, casting and external players are not counted.
"""

from __future__ import annotations

import time
from typing import Any

from PyQt6.QtCore import QTimer

from ..core import viewing_stats
from ..core.log import log


class _StatsMixin:
    _stats_log: viewing_stats.ViewingLog | None = None
    _stats_now: dict | None = None      # what is counted: id, name, kind
    _stats_mark: float | None = None    # monotonic time counted up to
    _stats_paused = False
    _stats_timer: QTimer | None = None
    _stats_saved_at = 0.0

    TICK_MS = 60_000
    #: The most one tick adds: its interval and a little.
    TICK_CAP_S = 90.0
    SAVE_EVERY_S = 300.0

    def _stats_store(self) -> viewing_stats.ViewingLog:
        """The viewing log, read once; a new one takes what the resume
        positions know already (every source's)."""
        if self._stats_log is None:
            self._stats_log = viewing_stats.ViewingLog(self.settings)
            if self._stats_log.fresh:
                try:
                    self._stats_seed(self._stats_log)
                except Exception as e:
                    log.debug("stats: not seeded from the resume positions: %s", e)
        return self._stats_log

    def _stats_seed(self, vlog: viewing_stats.ViewingLog) -> None:
        import json

        from ..core import privacy
        rs = getattr(self, "_resume_settings", None) or self.settings
        found = []
        for key in privacy.resume_keys(rs):
            source = (str(key)[len("resume_positions_"):]
                      if str(key).startswith("resume_positions_") else "")
            try:
                data = json.loads(rs.value(key, "") or "{}")
            except (TypeError, ValueError):
                continue
            found += viewing_stats.resume_seed(data, source)
        n = vlog.seed(found)
        vlog.save(force=True)
        if n:
            log.info("stats: started from %d saved position(s)", n)

    def _stats_source(self) -> str:
        store = getattr(self, "playlist_store", None)
        return str(getattr(store, "active_id", "") or "")

    def _stats_what(self, key: Any, title: str, item: dict | None, kind: str) -> dict:
        """What a playback counts as: an episode as its series."""
        sk = viewing_stats.kind_of(kind)
        name = title or (item or {}).get("name") or ""
        ident_key: Any = key if key is not None else name
        if sk == viewing_stats.SERIES:
            ctx = ((getattr(self, "_last_playback", None) or {}).get("series_ctx")
                   or getattr(self, "series_ctx", None) or {})
            series = ctx.get("name") or ctx.get("title")
            if series:
                name = str(series)
                ident_key = ctx.get("series_id") or series
        return {"id": viewing_stats.ident(sk, self._stats_source(), ident_key),
                "name": str(name), "kind": sk}

    # -- the playback path's hooks -------------------------------------------------

    def _stats_begin(self, key: Any, title: str, item: dict | None, kind: str,
                     catchup: bool) -> None:
        """The docked player took a stream: what played before is counted,
        this one from now."""
        try:
            self._stats_flush()
            self._stats_now = self._stats_what(key, title, item, kind)
            self._stats_mark = time.monotonic()
            self._stats_paused = False
            if self._stats_timer is None:
                self._stats_timer = QTimer(self)
                self._stats_timer.setInterval(self.TICK_MS)
                self._stats_timer.timeout.connect(self._stats_tick)
            if not self._stats_timer.isActive():
                self._stats_timer.start()
        except Exception:
            log.debug("stats: begin", exc_info=True)

    def _stats_playing(self) -> bool:
        p = getattr(self, "player", None)
        return bool(p is not None and getattr(p, "current_url", None)
                    and not self._stats_paused)

    def _stats_flush(self) -> None:
        """What played since the last mark goes into the log."""
        now = time.monotonic()
        mark, self._stats_mark = self._stats_mark, now
        what = self._stats_now
        if what is None or mark is None or not self._stats_playing():
            return
        secs = min(max(0.0, now - mark), self.TICK_CAP_S)
        if secs >= 1:
            self._stats_store().add(what["id"], what["name"], what["kind"], secs)

    def _stats_tick(self) -> None:
        try:
            self._stats_flush()
            if not self._stats_playing() and self._stats_now is not None:
                p = getattr(self, "player", None)
                if p is None or not getattr(p, "current_url", None):
                    self._stats_end()
                    return
            if time.monotonic() - self._stats_saved_at >= self.SAVE_EVERY_S:
                self._stats_save()
        except Exception:
            log.debug("stats: tick", exc_info=True)

    def _stats_paused_changed(self, paused: bool) -> None:
        """The player's paused_changed: a pause is not watching."""
        try:
            self._stats_flush()
            self._stats_paused = bool(paused)
        except Exception:
            log.debug("stats: paused", exc_info=True)

    def _stats_end(self, *_args) -> None:
        """Stop, the end of a title, an error: counted up to now, then
        nothing until the next stream."""
        try:
            self._stats_flush()
            self._stats_now = None
            self._stats_mark = None
            if self._stats_timer is not None:
                self._stats_timer.stop()
            self._stats_save()
        except Exception:
            log.debug("stats: end", exc_info=True)

    def _stats_drop_pending(self) -> None:
        """Clear recent history is about to run: what played since the last
        mark is not counted either."""
        if self._stats_mark is not None:
            self._stats_mark = time.monotonic()

    def _stats_save(self) -> None:
        self._stats_saved_at = time.monotonic()
        if getattr(self, "_resetting", False):
            return              # Settings > Reset all emptied the settings: leave them
        if self._stats_log is not None:
            try:
                self._stats_log.save()
            except Exception as e:
                log.warning("stats: not saved: %s", e)

    def _stats_close(self) -> None:
        """closeEvent: what played last is counted and kept."""
        self._stats_end()

    # -- the window -------------------------------------------------------------------

    def open_stats(self) -> None:
        """Your statistics (History menu)."""
        from .stats_window import StatsWindow
        self._stats_flush()
        StatsWindow(self, self._stats_store(), self.settings).exec()
