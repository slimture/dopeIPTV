"""Events pushed out, in the window (core/events.py sends them): what the
playback path and the recordings tell it - a start, a pause, a stop, a
channel changed, a recording started or ended - handed to the dispatcher
as Nerd > Remote and sharing says. It only watches: each hook returns at
once while nothing is configured, swallows its own errors, and does no more
on the window's thread than put a small message on a queue.

With MQTT's "Take commands" on, it also carries out what Home Assistant
asks (core/mqtt_control.py): the broker's thread hands each command over a
queued signal, and it is done here, on the window's thread - the same
controls as the media keys and the remote API, TV mode's switch and keys
through mw_tv's tv_command as the phone's. What goes back is the state as
ever (TV mode on or off in it): while viewing privately, what happened, not
what."""

from __future__ import annotations

from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ..core import events, mqtt_control, privacy
from ..core.log import log


class _CommandBridge(QObject):
    """A command from the broker's thread onto the window's."""

    got = pyqtSignal(str, object)


class _EventsMixin:
    _events: events.Dispatcher | None = None
    _events_now: dict | None = None        # what plays: key, kind and what the events say of it
    _events_state = "idle"                  # idle, playing, paused
    _events_rec: events.RecordingWatch | None = None
    _events_cmd: _CommandBridge | None = None
    _events_favs: dict | None = None        # Home Assistant's list: option -> the favourite
    _events_vol_timer: QTimer | None = None
    _events_fav_timer: QTimer | None = None

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
            if self._events_cmd is None:
                self._events_cmd = _CommandBridge(self)
                self._events_cmd.got.connect(self._events_command)
            bridge = self._events_cmd
            d.on_command = bridge.got.emit
            self._events_volume_now(publish=False)
            d.tv_mode = bool(getattr(self, "_tv_on", False))
            if hasattr(self, "_events_profile"):
                self._events_profile()
            self._events_favorites()
            d.configure(cfg)
            if self._events_rec is None:
                self._events_rec = events.RecordingWatch(list(self.rec.jobs))
            if started and d.active:
                d.emit("app_started", {"version": events._version()}, privacy.private())
            if d.active:
                self._events_publish_state()
        except Exception:
            log.warning("events: could not apply the settings", exc_info=True)

    # -- Home Assistant's commands (the window's thread) ------------------------------

    def _events_command(self, name: str, value: Any) -> None:
        """A command from the broker: done as the media keys and the remote
        API do it. Never what it said in the log - a channel's name is
        what is watched."""
        try:
            if sip.isdeleted(self) or self._events_live() is None or not self._events.cfg.commands:
                return
            log.info("events: %s, asked over MQTT", name)
            if name in ("playpause", "channel_up", "channel_down", "favorite", "play") \
                    and getattr(self, "_profile_locked", lambda: False)():
                # "Who's watching?" waits for the PIN at start: nothing of
                # the profile's is played before it is given.
                log.info("events: %s left - who is watching is not chosen yet", name)
                return
            if name in ("playpause", "stop"):
                self._remote_media(name)
            elif name in ("channel_up", "channel_down"):
                self._play_channel_step(1 if name == "channel_up" else -1)
            elif name == "volume":
                self._remote_media("volume", max(0, min(100, int(value))) / 100.0)
            elif name == "favorite":
                row = (self._events_favs or {}).get(str(value))
                if row is not None and self._chno_rows([row]):
                    self.play_live_channel(row)
                else:
                    log.debug("events: no such favourite - ignored")
            elif name == "play":
                text = str(value).strip()
                self.handle_command({"number": int(text)} if text.isdigit() else {"channel": text})
            elif name in ("tv", "tv_key"):
                self.tv_command(name, value)
            elif name == "profile":
                try:
                    self._profile_switch_remote({"name": str(value)})
                except ValueError:
                    log.debug("events: no such profile, or one with a PIN - ignored")
        except Exception:
            log.debug("events: a command", exc_info=True)

    def _events_tv_mode(self) -> None:
        """TV mode on or off (mw_tv): Home Assistant's switch follows."""
        d = self._events
        if d is not None:
            try:
                d.set_tv_mode(bool(getattr(self, "_tv_on", False)))
            except Exception:
                log.debug("events: tv mode", exc_info=True)

    def _events_favorites(self) -> None:
        """The favourite channels Home Assistant's list offers, again (none
        while who is watching waits for a PIN at start: _favorite_channels)."""
        try:
            pairs = mqtt_control.favorite_options(self._favorite_channels())
            self._events_favs = dict(pairs)
            if self._events is not None:
                self._events.set_favorites(name for name, _r in pairs)
        except Exception:
            log.debug("events: favourites", exc_info=True)

    def _favorites_changed(self) -> None:
        """FavoriteStore.on_change: told once the changes settle."""
        t = self._events_fav_timer
        if t is None:
            t = self._events_fav_timer = QTimer(self)
            t.setSingleShot(True)
            t.setInterval(300)
            t.timeout.connect(self._events_favorites)
        t.start()

    def _watch_favorites(self) -> None:
        """The source's favourites (made anew with each source) report
        their changes."""
        favs = getattr(self, "favs", None)
        if favs is not None:
            favs.on_change = self._favorites_changed
        if self._events is not None:
            self._favorites_changed()

    def _events_volume(self, *_a) -> None:
        """The player's volume moved: told once it rests."""
        if self._events_live() is None:
            return
        t = self._events_vol_timer
        if t is None:
            t = self._events_vol_timer = QTimer(self)
            t.setSingleShot(True)
            t.setInterval(300)
            t.timeout.connect(self._events_volume_now)
        t.start()

    def _events_volume_now(self, publish: bool = True) -> None:
        d = self._events
        p = getattr(self, "player", None)
        if d is None or p is None:
            return
        try:
            muted = bool(getattr(p, "_muted", False))
            value = int(getattr(p, "_vol_before_mute", p.vol.value()) if muted else p.vol.value())
        except Exception:
            return
        if publish:
            d.set_volume(value)
        else:
            d.volume = max(0, min(100, value))

    def _events_live(self) -> events.Dispatcher | None:
        d = self._events
        return d if d is not None and d.active else None

    def _events_private(self) -> bool:
        """While viewing privately - and for the rest of a playback that was
        private (core/privacy.py): what happened, not what."""
        return privacy.private() or bool((self._events_now or {}).get("private"))

    def _events_publish_state(self) -> None:
        d = self._events_live()
        if d is not None:
            d.set_state(self._events_state, self._events_state_fields(), self._events_private())

    def _events_private_changed(self, on: bool) -> None:
        """Private viewing turned on or off: what plays now is private for
        the rest of its run, and the retained state says so at once."""
        try:
            if on and self._events_now is not None:
                self._events_now["private"] = True
            self._events_publish_state()
            withdraw = getattr(self._events, "withdraw_titled", None) if on else None
            if callable(withdraw):
                # Still waiting to go out, not yet sent: taken back.
                withdraw()
        except Exception:
            log.debug("events: private viewing", exc_info=True)

    def _events_state_fields(self) -> dict | None:
        """What the state says plays: the events' fields, and - for Home
        Assistant's list - the favourite it is (left out while private)."""
        now = self._events_now
        if now is None:
            return None
        fields = dict(now.get("fields") or {})
        key = now["id"][1] if now["id"][0] == "live" else None
        if key is not None and self._events_favs and not self._events_private():
            name = next((n for n, row in self._events_favs.items()
                         if str(row.get("stream_id")) == str(key)), None)
            if name:
                fields["favorite"] = name
        return fields

    def _events_send(self, event: str, fields: dict | None, state: str | None = None,
                     playback: bool = True) -> None:
        """*playback*: an event of what plays (a recording's is not)."""
        d = self._events_live()
        if d is None:
            return
        d.emit(event, fields, self._events_private() if playback else privacy.private(),
               state=(state, self._events_state_fields()) if state is not None else None)

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
            self._events_now = {"id": ident, "fields": fields,
                                "private": privacy.playback_private(kind, key)}
            self._events_state = "playing"
            self._events_send("playback_started", fields, "playing")
            if prev is not None and was != "idle" and prev["id"][0] == "live" and kind == "live":
                # Never the name of a channel watched privately.
                was_private = bool(prev.get("private"))
                self._events_send("channel_changed",
                                  dict(fields, previous="" if was_private
                                       else prev["fields"].get("channel") or ""))
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
            # A recording that began while what played was private stays
            # so to its end, though private viewing is off by then.
            # This window's recordings: not what the server records (the
            # events say what the app here does).
            told = watch.update([j for j in self.rec.jobs
                                 if not (j.get("_elsewhere") or j.get("_was_elsewhere"))],
                                private=privacy.playing_private())
            d = self._events_live()
            if d is not None:
                for event, fields in told:
                    private = bool(fields.pop("private", False)) or privacy.private()
                    d.emit(event, fields, private)
            if watch.pending:
                QTimer.singleShot(int(events.RecordingWatch.SETTLE_S * 1000) + 200, self._events_jobs)
        except Exception:
            log.debug("events: recordings", exc_info=True)
