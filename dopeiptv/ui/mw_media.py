"""Media servers in the window (providers/mediaserver.py: Jellyfin, Emby,
Plex): what plays told to the server - a start, every ten seconds while it
plays, a pause, a stop - so its resume point and watched mark follow; the
server's resume points offered when a title starts (the newer of the
server's and this computer's) and listed under Continue watching; a watched
mark made here sent to it; why a server has no live TV said on the status
line. It only watches: each hook returns at once for any other kind of
source, swallows its own errors, and does no more on the window's thread
than put a small job on the client's queue. Private viewing reports
nothing."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QTimer

from ..core import privacy
from ..core.log import log


class _MediaServerMixin:
    _ms_now: dict | None = None
    _ms_timer: QTimer | None = None

    def _ms_client(self) -> Any:
        client = getattr(self, "client", None)
        return client if getattr(client, "reports_playback", False) else None

    def _ms_position(self) -> tuple[float, float]:
        player = getattr(self, "player", None)
        if player is None:
            return 0.0, 0.0
        try:
            return float(player.playback_position() or 0), float(player.playback_duration() or 0)
        except Exception:
            return 0.0, 0.0

    # -- the playback path's hooks ------------------------------------------------

    def _ms_started(self, url: str, key: Any, kind: str, item: dict | None,
                    start_at: float = 0.0) -> None:
        """_start_playback: the embedded player took a stream."""
        try:
            self._ms_leave()
            client = self._ms_client()
            if (client is None or kind not in ("movie", "episode", "live")
                    or privacy.playback_private(kind, key)):
                return
            target = client.report_target(key, kind, item, url or "")
            if target is None:
                return
            self._ms_now = {"client": client, "target": target, "url": url, "pos": float(start_at),
                            "dur": 0.0, "paused": False}
            client.report("start", target, start_at, 0.0)
            if self._ms_timer is None:
                self._ms_timer = QTimer(self)
                self._ms_timer.timeout.connect(self._ms_tick)
            self._ms_timer.start(int(client.REPORT_EVERY * 1000))
        except Exception:
            log.debug("media server: started", exc_info=True)

    def _ms_tick(self) -> None:
        now = self._ms_now
        if now is None:
            if self._ms_timer is not None:
                self._ms_timer.stop()
            return
        try:
            player = getattr(self, "player", None)
            if player is None or getattr(player, "current_url", None) != now["url"]:
                return
            pos, dur = self._ms_position()
            if pos > 0:
                now["pos"], now["dur"] = pos, dur or now["dur"]
            if not now["paused"] and not privacy.private():
                now["client"].report("progress", now["target"], now["pos"], now["dur"])
        except Exception:
            log.debug("media server: progress", exc_info=True)

    def _ms_paused(self, paused: bool) -> None:
        """The player's paused_changed."""
        now = self._ms_now
        if now is None or bool(paused) == now["paused"]:
            return
        try:
            now["paused"] = bool(paused)
            pos, dur = self._ms_position()
            if pos > 0:
                now["pos"], now["dur"] = pos, dur or now["dur"]
            now["client"].report("progress", now["target"], now["pos"], now["dur"], paused=bool(paused))
        except Exception:
            log.debug("media server: paused", exc_info=True)

    def _ms_leave(self, ended: bool = False) -> None:
        """What played has stopped: Stop, another title, the end, the app
        closing. Told once, with where it got to."""
        now, self._ms_now = self._ms_now, None
        if self._ms_timer is not None:
            self._ms_timer.stop()
        if now is None:
            return
        try:
            player = getattr(self, "player", None)
            if player is not None and getattr(player, "current_url", None) == now["url"]:
                pos, dur = self._ms_position()
                if pos > 0:
                    now["pos"], now["dur"] = pos, dur or now["dur"]
            if ended and now["dur"]:
                now["pos"] = now["dur"]
            now["client"].report("stop", now["target"], now["pos"], now["dur"])
        except Exception:
            log.debug("media server: stopped", exc_info=True)

    def _ms_stopped(self) -> None:
        """The player's stopped."""
        self._ms_leave()

    def _ms_finished(self) -> None:
        """The player's finished, told before the next episode may start. A
        live channel's end is a drop the app reconnects."""
        now = self._ms_now
        if now is not None and now["target"].get("kind") != "live":
            self._ms_leave(ended=True)

    def _ms_close(self) -> None:
        """closeEvent: the stop on its way, given a moment to get there."""
        client = (self._ms_now or {}).get("client")
        self._ms_leave()
        if client is not None:
            try:
                client.flush_reports(1.0)
            except Exception:
                pass

    # -- the server's resume points -----------------------------------------------------

    def _ms_resume_at(self, key: Any, kind: str, item: dict | None, local: float) -> float:
        """Where a title starts: this computer's resume point, or the
        server's when that is the newer (watched on a phone since)."""
        client = self._ms_client()
        if client is None or kind not in ("movie", "episode"):
            return local
        try:
            state = client.server_state(kind, key)
            it = item or {}
            if state is None and "_played" in it:
                state = {"pos": float(it.get("_server_pos") or 0), "played": bool(it.get("_played")),
                         "last_played": int(it.get("_last_played") or 0)}
            if state is None or not state["last_played"]:
                return local
            if local > 0 and self.resume.saved_at(key, kind) >= state["last_played"]:
                return local
            return state["pos"] if state["pos"] > 60 else 0.0
        except Exception:
            log.debug("media server: resume point", exc_info=True)
            return local

    def _continue_rows(self) -> list[dict]:
        """Continue watching: this computer's partly watched titles and the
        server's, the newest first. One the server says was finished since
        is left out; a kids profile sees none for adults (mw_profiles), and
        nobody one of a category locked while the lock is shut (mw_lists)."""
        rows = self._lock_filter(self._continue_rows_all())
        if getattr(self, "_offline", lambda: False)():
            # Offline mode: only what is downloaded goes on (mw_offline).
            kind_of = {"vod": "movie", "movie": "movie", "episode": "episode"}
            rows = [r for r in rows if kind_of.get(str(r.get("_kind") or "")) and
                    self._dl_find(kind_of[str(r.get("_kind"))], r, self._item_key(r)) is not None]
        only = getattr(self, "_kids_only", None)
        return only(rows, "vod") if callable(only) else rows

    def _continue_rows_all(self) -> list[dict]:
        rows = self.resume.continue_watching()
        client = self._ms_client()
        if client is None:
            return rows
        try:
            server_rows = client.resume_items()
            out: list[dict] = []
            seen: set[tuple[Any, str]] = set()
            for r in rows:
                kind = "movie" if r.get("_kind") == "vod" else r.get("_kind")
                state = client.server_state(str(kind), self._item_key(r))
                if state and state["played"] and state["last_played"] > int(r.get("_ts") or 0):
                    continue
                out.append(r)
                seen.add((r.get("_kind"), str(self._item_key(r))))
            for r in server_rows:
                ident = (r.get("_kind"), str(self._item_key(r)))
                if ident not in seen:
                    seen.add(ident)
                    out.append(r)
            out.sort(key=lambda r: int(r.get("_ts") or 0), reverse=True)
            return out
        except Exception:
            log.debug("media server: continue watching", exc_info=True)
            return rows

    def _ms_forget(self, it: dict) -> None:
        """Removed from Continue watching: the server's resume point too."""
        client = self._ms_client()
        if client is None:
            return
        try:
            kind = "episode" if it.get("_kind") == "episode" else "movie"
            target = client.report_target(self._item_key(it), kind, it, str(it.get("_url") or ""))
            if target is not None:
                client.report("stop", target, 0.0, float(it.get("_resume_dur") or 0))
        except Exception:
            log.debug("media server: forget", exc_info=True)

    # -- watched marks ---------------------------------------------------------------

    def _ms_mark(self, item: dict | None, played: bool, kind: str = "movie") -> None:
        """A watched mark made here (the menu, or watched to the end): the
        server's too."""
        client = self._ms_client()
        if client is None or not item:
            return
        try:
            target = (client.report_target(self._item_key(item), kind, item, str(item.get("_url") or ""))
                      if kind != "series" else None)
            if target is None and item.get("_ms_id"):
                target = {"id": str(item["_ms_id"]), "kind": kind}
            if target is None:
                return
            client.mark(target, played)
            if "_played" in item:
                item["_played"] = played
                if played:
                    item.pop("_progress_pct", None)
        except Exception:
            log.debug("media server: mark", exc_info=True)

    # -- live TV ------------------------------------------------------------------------

    def _ms_live_note(self, categories: list) -> None:
        """Live TV with no channels: why, when the server says (Plex). A box
        says it whenever it has one - MythTV lists its channels for the
        guide only, VDR's may lack streamdev - or else what its guide lacks
        (a VDR with streamdev alone, a SAT>IP server), unless the source has
        a guide address of its own."""
        client = getattr(self, "client", None)
        note, guide = (getattr(client, name, "") for name in ("live_note", "guide_note"))
        if isinstance(note, str) and note and (not categories or self._ms_client() is None):
            self._set_status(note)
        elif isinstance(guide, str) and guide and not self._source_guide_url():
            self._set_status(guide)

    def _source_guide_url(self) -> str:
        """The active source's own guide address: with one, what its box's
        guide lacks (a SAT>IP server has none) is not said."""
        store = getattr(self, "playlist_store", None)
        try:
            pl = store.active() if store is not None else None
        except Exception:
            pl = None
        return str((pl or {}).get("epg_url") or "").strip()
