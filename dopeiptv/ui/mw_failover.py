"""Failover in the window (core/failover.py finds the channel): when a live
channel has not come back after the usual reconnects, the same channel
from another of the user's sources - its own user agent and proxy - and a
note saying so. Once per channel; the next channel played is the active
source's again."""

from __future__ import annotations

from typing import Any

from ..core import failover, nerd
from ..core.log import log
from ..core.workers import run_async
from ..i18n import tr


class _FailoverMixin:
    _failover_source: dict | None = None
    _failover_url: str | None = None
    _failover_tried: Any = None

    def _failover_on(self) -> bool:
        n = nerd.current()
        return n.on and n.flag("nerd_failover")

    def _try_failover(self, fallback) -> bool:
        """The usual reconnects are spent on a live channel: look for it in
        another source. True when that is under way (*fallback* runs when
        no source has it); False when failover is off or already tried."""
        if not self._failover_on():
            return False
        lp = getattr(self, "_last_playback", None) or {}
        item = lp.get("item")
        if lp.get("kind") != "live" or not item or self._failover_tried == lp.get("key"):
            return False
        store = getattr(self, "playlist_store", None)
        if store is None:
            return False
        others = failover.candidates(store.playlists(), store.active_id)
        if not others:
            return False
        self._failover_tried = lp.get("key")
        name = str(item.get("name") or "")
        self._set_status(tr("failover_looking", name=name), emphasis=True)
        playlists, active, wanted = store.playlists(), store.active_id, dict(item)

        def done(found) -> None:
            if not found:
                log.info("failover: no other source has %s", name)
                fallback()
                return
            pl, row, url = found
            self._failover_play(pl, row, url, name)

        run_async(self.pool, lambda: failover.find_elsewhere(playlists, active, wanted),
                  done, lambda _e=None: fallback())
        return True

    def _failover_play(self, pl: dict, row: dict, url: str, name: str) -> None:
        nerd.set_source(pl)
        self._failover_source = pl
        self._failover_url = url
        source = str(pl.get("name") or pl.get("server") or "")
        title = f"{row.get('name') or name} · {source}"
        self._start_playback(url, title, row.get("stream_icon"), f"failover:{pl.get('id')}:{row.get('stream_id')}",
                             "live", record=False)
        self._show_toast(tr("failover_playing", name=name, source=source), 6000)

    def _failover_leave(self, url: str, key: Any) -> None:
        """Called as anything starts: the active source's options back once
        something other than the failover stream plays, and a new channel
        may fail over again."""
        if self._failover_source is not None and url != self._failover_url:
            store = getattr(self, "playlist_store", None)
            nerd.set_source(store.active() if store is not None else None)
            self._failover_source = None
            self._failover_url = None
        if url != self._failover_url and key != self._failover_tried:
            self._failover_tried = None
