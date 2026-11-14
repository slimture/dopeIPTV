"""Events pushed out, in the window (core/events.py sends them): what the
playback path and the recordings tell it - a start, a pause, a stop, a
channel changed, a recording started or ended - handed to the dispatcher
as Nerd > Remote and sharing says. It only watches: each hook returns at
once while nothing is configured, swallows its own errors, and does no more
on the window's thread than put a small message on a queue."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QTimer

from ..core import events, privacy
from ..core.log import log


class _EventsMixin:
    _events: events.Dispatcher | None = None
    _events_now: dict | None = None        # what plays: key, kind and what the events say of it
    _events_state = "idle"                  # idle, playing, paused
    _events_rec: events.RecordingWatch | None = None

    # -- the dispatcher -------------------------------------------------------------

    def _events_apply(self, started: bool = False) -> None:
        """Start, change or stop the receivers as the settings say now.
        *started*: the app has just started (app_started is sent)."""
        try:
            cfg = events.config(self.settings)
            if self._events is None:
                if not cfg.on:
                    return
                self._events = events.Dispatcher()
            d = self._events
            d.configure(cfg)
            if self._events_rec is None:
                self._events_rec = events.RecordingWatch(list(self.rec.jobs))
            if started and d.active:
                d.emit("app_started", {"version": events._version()}, privacy.private())
            if d.active:
                self._events_publish_state()
        except Exception:
            log.warning("events: could not apply the settings", exc_info=True)

    def _events_live(self) -> events.Dispatcher | None:
        d = self._events
        return d if d is not None and d.active else None

    def _events_publish_state(self) -> None:
        d = self._events_live()
        if d is not None:
            now = self._events_now or {}
            d.set_state(self._events_state, now.get("fields"), privacy.private())

    def _events_send(self, event: str, fields: dict | None, state: str | None = None) -> None:
        d = self._events_live()
        if d is None:
            return
        now = self._events_now or {}
        d.emit(event, fields, privacy.private(),
               state=(state, now.get("fields")) if state is not None else None)

    def _events_quit(self) -> None:
        """closeEvent: app_quitting and "off" on their way; never waited for here."""
        d = self._events
        if d is not None:
            try:
                d.quit(1.0)
            except Exception:
                log.debug("events: quit", exc_info=True)

    def _events_join(self, timeout: float = 1.0) -> None:
        """closeEvent, late: what is still being sent gets until *timeout*."""
        d = self._events
        if d is not None:
            try:
                d.join(timeout)
            except Exception:
                pass

    # -- what plays -----------------------------------------------------------------

    def _events_what(self, title: str, item: dict | None, kind: str, catchup: bool) -> dict[str, Any]:
        """What the events say of a playback: never its address."""
        it = item or {}
        out: dict[str, Any] = {"kind": kind, "title": title or it.get("name") or ""}
        if kind == "live":
            out["channel"] = title or it.get("name") or ""
            try:
                prog = self.xmltv.current_programme(it) if it else None
            except Exception:
                prog = None
            out["programme"] = (prog or {}).get("title") or ""
            if catchup:
                out["catchup"] = True
        elif kind == "episode":
            sctx = getattr(self, "series_ctx", None) or {}
            out["series"] = sctx.get("name") or sctx.get("title") or ""
        if kind in ("live", "movie", "episode"):
            store = getattr(self, "playlist_store", None)
            active = store.active() if store is not None else None
            out["source"] = (active or {}).get("name") or ""
        return out

    def _events_started(self, key: Any, title: str, item: dict | None, kind: str,
                        catchup: bool) -> None:
        """_start_playback: the embedded player took a stream."""
        try:
            if self._events_live() is None:
                return
            prev, was = self._events_now, self._events_state
            ident = (kind, key if key is not None else title)
            if prev is not None and prev["id"] == ident and was != "idle":
                return      # a reconnect, a seek in the archive: the same playback
            fields = self._events_what(title, item, kind, catchup)
            self._events_now = {"id": ident, "fields": fields}
            self._events_state = "playing"
            self._events_send("playback_started", fields, "playing")
            if prev is not None and was != "idle" and prev["id"][0] == "live" and kind == "live":
                self._events_send("channel_changed",
                                  dict(fields, previous=prev["fields"].get("channel") or ""))
        except Exception:
            log.debug("events: started", exc_info=True)

    def _events_paused(self, paused: bool) -> None:
        """The player's paused_changed."""
        try:
            if self._events_live() is None or self._events_now is None:
                return
            if paused and self._events_state == "playing":
                self._events_state = "paused"
                self._events_send("playback_paused", self._events_now["fields"], "paused")
            elif not paused and self._events_state == "paused":
                self._events_state = "playing"
                self._events_send("playback_resumed", self._events_now["fields"], "playing")
        except Exception:
            log.debug("events: paused", exc_info=True)

    def _events_end(self, event: str, reason: str = "") -> None:
        if self._events_live() is None or self._events_now is None or self._events_state == "idle":
            return
        self._events_state = "idle"
        fields = dict(self._events_now["fields"])
        if reason:
            fields["reason"] = reason
        self._events_send(event, fields, "idle")

    def _events_stopped(self) -> None:
        """The player's stopped: Stop, the sleep timer."""
        try:
            self._events_end("playback_stopped", "stopped")
        except Exception:
            log.debug("events: stopped", exc_info=True)

    def _events_finished(self) -> None:
        """The player's finished, told before _on_player_finished may start
        the next episode. A live channel's end is a drop the app reconnects."""
        try:
            if (getattr(self, "_last_playback", None) or {}).get("kind") == "live":
                return
            self._events_end("playback_stopped", "ended")
        except Exception:
            log.debug("events: finished", exc_info=True)

    def _events_failed(self) -> None:
        """The reconnects are spent: it did not play."""
        try:
            self._events_end("playback_failed")
        except Exception:
            log.debug("events: failed", exc_info=True)

    # -- recordings -------------------------------------------------------------------

    def _events_rec_stopped(self, title: str, reason: str) -> None:
        """RecordingManager.recording_stopped: why one ended."""
        if self._events_rec is not None:
            self._events_rec.stopped(title, reason)

    def _events_jobs(self) -> None:
        """RecordingManager.jobs_changed: what started, finished or failed."""
        try:
            watch = self._events_rec
            if watch is None:
                return
            told = watch.update(list(self.rec.jobs))
            if self._events_live() is not None:
                for event, fields in told:
                    self._events_send(event, fields)
            if watch.pending:
                QTimer.singleShot(int(events.RecordingWatch.SETTLE_S * 1000) + 200, self._events_jobs)
        except Exception:
            log.debug("events: recordings", exc_info=True)
