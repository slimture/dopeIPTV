"""Failover in the window (services/failover.py finds the channel): when a live
channel has not come back after the usual reconnects, the same channel
from another of the user's sources - its own user agent and proxy - and a
note saying so. Once per channel; the next channel played is the active
source's again."""

from __future__ import annotations

from typing import Any

from ..core import nerd
from ..core.log import log
from ..core.privacy import for_log
from ..core.workers import run_async
from ..i18n import tr
from ..services import failover


class _FailoverMixin:
    _failover_source: dict | None = None
    _failover_url: str | None = None
    _failover_row: dict | None = None       # the other source's row of it
    _failover_key: Any = None
    _failover_tried: Any = None
    # Moves on with every start and stop: a lookup that comes back after the
    # user played something else, or pressed Stop, must not take over.
    _failover_gen = 0

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
        # A kids profile: the other source's channel for adults is no choice.
        kids = bool(getattr(self, "_profile_kids", lambda: False)())
        # Nor is one the other source's lists leave out: a hidden category,
        # a locked one while it is locked, a channel hidden by hand.
        shown = self._failover_shown(others, kids)
        gen = self._failover_gen

        def done(found) -> None:
            if gen != self._failover_gen:
                log.info("failover: %s found too late - something else was chosen", for_log(name))
                return
            if not found:
                log.info("failover: no other source has %s", for_log(name))
                fallback()
                return
            pl, row, url = found
            self._failover_play(pl, row, url, name)

        def failed(_e=None) -> None:
            if gen == self._failover_gen:
                fallback()

        run_async(self.pool, lambda: failover.find_elsewhere(playlists, active, wanted, kids=kids,
                                                             shown=shown),
                  done, failed)
        return True

    def _failover_shown(self, others: list[dict], kids: bool) -> dict[str, failover.Shown]:
        """Per other source, what its lists leave out - read here, on the
        window's thread, from that source's own hide and lock rules (as the
        window keeps them under its id)."""
        from ..core.stores import CategoryOverrides, ChannelOverrides
        parental = getattr(self, "parental", None)
        locked = kids or not getattr(parental, "session_unlocked", False)
        out: dict[str, failover.Shown] = {}
        for pl in others:
            pid = pl.get("id")
            if not pid:
                continue
            out[str(pid)] = failover.Shown.of(
                CategoryOverrides(self.settings, f"category_overrides_{pid}"),
                ChannelOverrides(self.settings, f"channel_overrides_{pid}"), locked)
        return out

    def _failover_play(self, pl: dict, row: dict, url: str, name: str) -> None:
        nerd.set_source(pl)
        self._failover_source = pl
        self._failover_url = url
        source = str(pl.get("name") or pl.get("server") or "")
        title = f"{row.get('name') or name} · {source}"
        self._failover_row = dict(row)
        self._failover_key = f"failover:{pl.get('id')}:{row.get('stream_id')}"
        self._start_playback(url, title, row.get("stream_icon"), self._failover_key,
                             "live", record=False)
        self._show_toast(tr("failover_playing", name=name, source=source), 6000)

    def _failover_row_for(self, key: Any) -> dict | None:
        """The other source's row of the channel playing from it as *key* -
        what Watch together tells the room it is (the same channel, so the
        room stays on it)."""
        if self._failover_source is not None and key is not None and key == self._failover_key:
            return self._failover_row
        return None

    def _failover_stopped(self) -> None:
        """Stop was pressed: a lookup under way is no longer wanted."""
        self._failover_gen += 1

    def _failover_leave(self, url: str, key: Any) -> None:
        """Called as anything starts: the active source's options back once
        something other than the failover stream plays, and a new channel
        may fail over again."""
        self._failover_gen += 1
        if self._failover_source is not None and url != self._failover_url:
            store = getattr(self, "playlist_store", None)
            nerd.set_source(store.active() if store is not None else None)
            self._failover_source = None
            self._failover_url = None
            self._failover_row = None
            self._failover_key = None
        if url != self._failover_url and key != self._failover_tried:
            self._failover_tried = None
