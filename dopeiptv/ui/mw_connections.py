"""Connections to the account in the window (core/connections.py): the
app's own counted as they are now - the player, the rewind buffers, a
recording over a connection of its own, each multiview cell - and, before
the app opens one more itself, a plain warning when that would go over
what the account allows, with the choice to go ahead anyway.

For everyone, not only Nerd. Nothing is said while the account gives no
limit, and nothing is ever stopped: the warning is a question.
"""

from __future__ import annotations

import time

from ..core import connections
from ..core.connections import Use
from ..core.log import log
from ..i18n import tr

#: How long after the provider answered its count of connections in use
#: still stands next to the app's own count now.
SEEN_FRESH_SECS = 15.0
KIND_WORDS = {"player": "conn_k_player", "buffer": "conn_k_buffer",
              "recording": "conn_k_recording", "multiview": "conn_k_multiview"}


class _ConnectionsMixin:

    # -- what is counted -----------------------------------------------------------

    def _conn_ledger(self) -> connections.Ledger:
        led = getattr(self, "_conns", None)
        if led is None:
            led = self._conns = connections.Ledger()
        return led

    def _conn_source(self) -> str:
        """The active source's id: whose account the checks are about."""
        store = getattr(self, "playlist_store", None)
        return str(getattr(store, "active_id", "") or "") if store is not None else ""

    def _conn_player_source(self) -> str:
        """The source the docked player plays from (failover: another)."""
        other = getattr(self, "_failover_source", None)
        if other:
            return str(other.get("id") or "")
        return self._conn_source()

    def _conn_uses(self, free_player: bool = False, free_cell=None) -> list[Use]:
        """The connections the app holds now. *free_player*: as they will be
        once the player has let its stream go (its buffer stays while a
        recording reads it); *free_cell*: a multiview cell about to be
        given another stream."""
        uses: list[Use] = []
        p = getattr(self, "player", None)
        src = self._conn_player_source()
        dvr = getattr(p, "_dvr", None) if p is not None else None
        try:
            if dvr is not None and dvr.streaming:
                if not free_player or dvr.recording:
                    uses.append(Use("buffer", src))
            elif (p is not None and getattr(p, "current_url", None)
                  and connections.is_network(p.current_url) and not free_player):
                uses.append(Use("player", src))
            pool = getattr(p, "_dvr_pool", None) if p is not None else None
            for s in (pool.sessions if pool is not None else []):
                if s is not dvr and s.streaming:
                    uses.append(Use("buffer", ""))     # kept by a recording
        except Exception as e:      # the player half torn down
            log.debug("connections: player not counted: %s", e)
        rec = getattr(self, "rec", None)
        for j in (rec.jobs if rec is not None else []):
            if (j.get("status") == "recording" and not j.get("inplayer")
                    and j.get("recorder") is None and connections.is_network(j.get("url"))):
                uses.append(Use("recording", str(j.get("source") or "")))
        win = getattr(self, "_multiview_win", None)
        for c in (getattr(win, "cells", None) or []):
            if c is not free_cell and connections.is_network(getattr(c, "url", None)):
                uses.append(Use("multiview", str(getattr(c, "source_id", "") or "")))
        return uses

    def _conn_seen(self, source: str | None = None, info: dict | None = None,
                   own: int | None = None) -> None:
        """What the provider said of the connections into the ledger: *info*
        (an account_info answer for *source*, the app holding *own* of them
        when it was asked), else what the active source's client last heard
        - its count in use only while it is fresh."""
        led = self._conn_ledger()
        src = self._conn_source() if source is None else source
        if info is not None:
            limit, active = connections.numbers((info or {}).get("user_info"))
            if own is None:
                own = connections.own_count(self._conn_uses(), src)
            led.report(src, limit, active, own)
            return
        seen = getattr(getattr(self, "client", None), "account_seen", None)
        if not isinstance(seen, dict) or source not in (None, self._conn_source()):
            return
        at = float(seen.get("at") or 0.0)
        fresh = time.monotonic() - at <= SEEN_FRESH_SECS
        led.report(src, seen.get("max"), seen.get("active") if fresh else None,
                   connections.own_count(self._conn_uses(), src), at=at)

    def _conn_limit(self) -> int | None:
        n = getattr(getattr(self, "client", None), "max_connections", None)
        try:
            return int(n) if n else None
        except (TypeError, ValueError):
            return None

    def _conn_verdict(self, uses: list[Use], adding: int = 1) -> connections.Verdict | None:
        """Over the active source's limit with *adding* more next to *uses*?"""
        try:
            self._conn_seen()
            return self._conn_ledger().check(self._conn_source(), uses, adding,
                                             limit=self._conn_limit())
        except Exception as e:      # counting must never stand in the way
            log.debug("connections: not checked: %s", e)
            return None

    # -- what is said ------------------------------------------------------------------

    def _conn_words(self, v: connections.Verdict) -> str:
        """The connections in use, by whom: "player, multiview window ×2,
        another device"."""
        parts = []
        for kind, n in connections.counted(v.kinds):
            word = tr(KIND_WORDS.get(kind, "conn_k_player"))
            parts.append(word if n == 1 else tr("conn_times", what=word, n=n))
        if v.others:
            word = tr("conn_k_other")
            parts.append(word if v.others == 1 else tr("conn_times", what=word, n=v.others))
        return ", ".join(parts)

    def _conn_warning(self, v: connections.Verdict) -> str:
        """One plain paragraph: what the account allows, what is in use and
        by whom, and what one more may do."""
        used = self._conn_words(v)
        text = tr("conn_over", max=v.limit, n=v.in_use)
        if used:
            text += " " + tr("conn_used_by", who=used)
        return text + " " + tr("conn_risk")

    def _conn_with_warning(self, body: str, v: connections.Verdict | None) -> str:
        return body if v is None else body + "\n\n" + self._conn_warning(v)

    def _conn_ask(self, v: connections.Verdict, what: str) -> bool:
        """Warn about *v* before *what* (an i18n key: what is about to
        open the connection); True to go ahead."""
        idx = self._choice_dialog(
            tr("conn_title"), tr(what) + "\n\n" + self._conn_warning(v),
            [(tr("conn_anyway"), "normal"), (tr("common_cancel"), "primary")])
        go = idx == 0
        log.info("connections: %d of %d in use - one more %s", v.in_use, v.limit,
                 "anyway" if go else "not opened")
        return go

    # -- the places a connection is opened -----------------------------------------------

    def _conn_ok_multiview(self, cell: int | None, url: str = "") -> bool:
        """*url* into multiview cell *cell* (None: the first free one): the
        docked player lets its stream go first where Settings says so, and
        a cell that plays already gives its stream up for the new one. A
        file of this computer's takes no connection."""
        if not connections.is_network(url):
            return True
        free_player = self.settings.value("mv_stop_docked", "true") == "true"
        win = getattr(self, "_multiview_win", None)
        target = None
        cells = list(getattr(win, "cells", None) or [])
        if cells:
            if cell is not None and 0 <= cell < len(cells):
                target = cells[cell]
            else:
                target = next((c for c in cells if c.url is None),
                              getattr(win, "_focused", None) or cells[0])
        v = self._conn_verdict(self._conn_uses(free_player=free_player, free_cell=target))
        return v is None or self._conn_ask(v, "conn_what_multiview")
