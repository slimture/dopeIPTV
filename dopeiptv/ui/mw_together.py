"""Watch together in the window: core/together.py keeps the room and the
link to the relay, services/together.py finds what plays in this source,
ui/together_ui.py draws the dialog and the strip under the video.

What plays here goes to the room when it is chosen here (a start, a pause,
a seek - found by watching the position, the player is not changed for
it); what the room plays is found in this source and started, and kept in
step once a second through the player's own play, pause and seek. A live
channel is only tuned (exact sync across providers is impossible; the
strip says so). Something the source lacks is said plainly; the others go
on. Leaving, or closing the app, stops nothing here or there. Never while
private viewing is on.

The relay is spoken to on the link's threads, the room's id and key are
made on a worker (the code is stretched), the title is looked up on the
pool; what comes back is handled here on the window's thread.
"""

from __future__ import annotations

import time
from functools import partial
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal

from ..core import privacy, together
from ..core.log import log
from ..core.privacy import for_log
from ..core.workers import run_async
from ..i18n import tr
from ..services import together as matching

TICK_MS = 1000
#: A seek or pause here is told once the position has settled.
PUBLISH_MS = 800
#: s after a start before positions are read at all (the stream loads).
LOAD_SETTLE = 2.0
#: s after a start before a jump counts as the user's seek.
START_GRACE = 5.0
#: s a joiner waits to hear what the room plays before offering its own.
JOIN_SETTLE = 4.0
#: s a lookup for the room is taken to be on its way.
FOLLOW_FOR = 25.0
#: s after entering in which "X joined" is not toasted (the room's history).
QUIET_JOINS = 4.0


class _TogetherSignals(QObject):
    """The link's threads emit these; the window hears them on its own."""
    message = pyqtSignal(int, object)       # link id, Message
    status = pyqtSignal(int, str, int)      # link id, kind, members


def _derive(lid: int, code: str) -> tuple[int, str, str, bytes]:
    room_id, key = together.derive(code)
    return lid, code, room_id, key


def _locate(lid: int, media: together.Media, client: Any, tmdb_of: Any) -> tuple:
    return lid, media, matching.locate(client, media, tmdb_of)


class _TogetherMixin:
    """MainWindow mixin: watch together."""

    #: Tests point these elsewhere.
    _together_endpoint: str | None = None
    _together_relay_for: Any = None
    #: The session's name in a room - kept for the next room, never stored.
    _together_name = ""

    def _init_together(self) -> None:
        self._together_room: together.Room | None = None
        self._together_link: together.Link | None = None
        self._together_lid = 0
        self._together_conn = ("idle", 0)
        self._together_joining = False
        self._together_entered_at = 0.0
        self._together_local: together.Media | None = None
        self._together_follow: together.Media | None = None
        self._together_follow_at = 0.0
        self._together_start_at: float | None = None
        self._together_started_at = 0.0
        self._together_note = ""
        self._together_missing = False         # what the room plays is not in this source
        self._together_publish_pending = False
        self._together_settle_at = 0.0
        self._together_keepalive_at = 0.0
        self._together_expect_pause: tuple[bool, float] | None = None
        self._together_dialog = None
        self._together_strip = None
        self._together_fix = together.Corrector()
        self._together_watch = together.LocalWatch()
        self._together_signals = _TogetherSignals(self)
        q = Qt.ConnectionType.QueuedConnection
        self._together_signals.message.connect(self._together_on_message, q)
        self._together_signals.status.connect(self._together_on_status, q)
        self._together_tick_timer = QTimer(self)
        self._together_tick_timer.setInterval(TICK_MS)
        self._together_tick_timer.timeout.connect(self._together_tick)
        self._together_publish_timer = QTimer(self)
        self._together_publish_timer.setSingleShot(True)
        self._together_publish_timer.timeout.connect(self._together_publish_now)
        self._together_hold_timer = QTimer(self)
        self._together_hold_timer.setSingleShot(True)
        self._together_hold_timer.timeout.connect(self._together_hold_end)
        p = getattr(self, "player", None)
        if p is not None:
            p.paused_changed.connect(self._together_paused)
            p.stopped.connect(self._together_stopped)
            p.extra_options.append((self._together_menu_text, self.open_together))

    @staticmethod
    def _together_menu_text() -> str:
        return tr("together_title") + "…"

    # -- what the dialog and the strip show -------------------------------------------

    def together_view(self) -> dict[str, Any]:
        room = self._together_room
        view: dict[str, Any] = {"in": room is not None, "private": privacy.private()}
        if room is None:
            return view
        state = room.state
        view.update(code=room.code, me=room.name, conn=self._together_conn[0],
                    joined=self._together_joining, note=self._together_note,
                    members=[(m.name, m.status) for m in
                             sorted(room.members.values(), key=lambda m: m.name.casefold())],
                    title=state.media.name if state is not None else "",
                    live=state is not None and not state.media.synced)
        return view

    def _together_refresh(self) -> None:
        strip = self._together_strip
        if strip is None and self._together_room is not None:
            strip = self._together_build_strip()
        if strip is not None and not sip.isdeleted(strip):
            strip.refresh()
        dlg = self._together_dialog
        if dlg is not None and not sip.isdeleted(dlg) and dlg.isVisible():
            dlg.refresh()

    def _together_build_strip(self):
        """Under the video, above the details - only while in a room."""
        from .together_ui import TogetherStrip
        info = getattr(self, "_info_scroll", None)
        host = info.parentWidget() if info is not None else None
        lay = host.layout() if host is not None else None
        if lay is None:
            return None
        strip = TogetherStrip(self, host)
        lay.insertWidget(max(0, lay.indexOf(info)), strip)
        self._together_strip = strip
        return strip

    def open_together(self) -> None:
        """The app menu's and the player's "Watch together…"."""
        dlg = self._together_dialog
        if dlg is not None and not sip.isdeleted(dlg):
            dlg.refresh()
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            return
        from .together_ui import TogetherDialog
        dlg = TogetherDialog(self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dlg.destroyed.connect(self._together_dialog_gone)
        self._together_dialog = dlg
        dlg.show()

    def _together_dialog_gone(self, *_a) -> None:
        self._together_dialog = None

    def _together_toast(self, text: str, ms: int = 4000) -> None:
        try:
            self._show_toast(text, ms)
        except Exception:
            pass

    # -- entering and leaving ---------------------------------------------------------

    def together_start(self, name: str) -> str | None:
        """Start a room here; None, or what stands in the way."""
        return self._together_enter(together.new_code(), name, joining=False)

    def together_join(self, code: str, name: str) -> str | None:
        shown = together.parse_code(code)
        if shown is None:
            return tr("together_code_bad")
        return self._together_enter(shown, name, joining=True)

    def _together_enter(self, code: str, name: str, joining: bool) -> str | None:
        if privacy.private():
            return tr("together_private")
        clean = together.clean_name(name)
        if not clean:
            return tr("together_name_needed")
        if self._together_room is not None:
            self.together_leave(quiet=True)
        type(self)._together_name = clean
        self._together_lid += 1
        clock = together.Clock()
        self._together_room = together.Room(code, together.new_member(), clean, clock)
        self._together_joining = joining
        self._together_conn = ("connecting", 0)
        self._together_note = ""
        self._together_follow = None
        self._together_entered_at = time.monotonic()
        log.info("together: %s a room", "joining" if joining else "starting")
        run_async(self.pool, _derive, self._together_on_entered,
                  self._together_derive_failed, self._together_lid, code)
        self._together_refresh()
        return None

    def _together_derive_failed(self, _msg: str = "") -> None:
        log.warning("together: no key made for the room")
        self.together_leave(quiet=True)

    def _together_on_entered(self, got: tuple) -> None:
        lid, code, room_id, key = got
        room = self._together_room
        if lid != self._together_lid or room is None or room.code != code:
            return
        if privacy.private():
            self.together_leave(quiet=True)
            return
        sig = self._together_signals
        # Each link's news carries its own id: a late one from a room left
        # is told apart from the next room's.
        link = together.Link(room_id, key, room.me, room.clock,
                             on_message=partial(sig.message.emit, lid),
                             on_status=partial(sig.status.emit, lid),
                             relay_for=self._together_relay_for,
                             endpoint=self._together_endpoint)
        self._together_link = link
        link.start()
        self._together_local = self._together_now_playing()
        room.status = self._together_my_status()
        link.send(room.hello())
        now = time.monotonic()
        self._together_keepalive_at = now + room.KEEPALIVE
        self._together_fix.reset()
        self._together_watch.reset()
        if self._together_local is not None and not self._together_joining:
            # Started here with something on: the room plays it.
            self._together_publish_pending = True
            if not self._together_local.synced:
                self._together_publish_now()
        self._together_settle_at = now + JOIN_SETTLE if self._together_joining else 0.0
        self._together_tick_timer.start()
        self._together_refresh()

    def together_leave(self, quiet: bool = False, wait: float = 0.0) -> None:
        """Out of the room: a bye to the others and the seat given up. What
        plays here goes on as it is, and so does theirs."""
        room, link = self._together_room, self._together_link
        if room is None:
            return
        self._together_room = None
        self._together_link = None
        self._together_lid += 1
        self._together_tick_timer.stop()
        self._together_publish_timer.stop()
        if self._together_hold_timer.isActive():
            self._together_hold_timer.stop()
            self._together_hold_end(force=True)
        self._together_follow = None
        self._together_note = ""
        self._together_missing = False
        self._together_publish_pending = False
        self._together_conn = ("idle", 0)
        if link is not None:
            link.close(room.bye(), wait)
        log.info("together: left the room")
        if not quiet:
            self._together_toast(tr("together_left_room"))
        self._together_refresh()

    def _together_close(self) -> None:
        """closeEvent: the bye gets a moment on its way."""
        try:
            self.together_leave(quiet=True, wait=1.0)
        except Exception:
            log.debug("together: close", exc_info=True)

    def _together_private_on(self) -> None:
        """Private viewing was turned on: out of the room at once."""
        if self._together_room is not None:
            self.together_leave(quiet=True)
            self._together_toast(tr("together_private_left"), 6000)

    # -- what the room says -------------------------------------------------------------

    def _together_on_status(self, lid: int, kind: str, members: int) -> None:
        if lid != self._together_lid or self._together_room is None:
            return
        self._together_conn = (kind, members)
        if kind == "full":
            self.together_leave(quiet=True)
            self._together_toast(tr("together_full", most=together.MAX_MEMBERS), 6000)
            dlg = self._together_dialog
            if dlg is not None and not sip.isdeleted(dlg):
                dlg.show_error(tr("together_full", most=together.MAX_MEMBERS))
            return
        self._together_refresh()

    def _together_on_message(self, lid: int, msg: together.Message) -> None:
        room, link = self._together_room, self._together_link
        if lid != self._together_lid or room is None or link is None:
            return
        now = time.monotonic()
        quiet = now - self._together_entered_at < QUIET_JOINS
        for kind, what in room.receive(msg, now):
            if kind == "joined" and not quiet:
                self._together_toast(tr("together_joined", name=what.name), 3000)
            elif kind == "left":
                self._together_toast(tr("together_left", name=what.name), 3000)
            elif kind == "hello":
                room.status = self._together_my_status()
                link.send(room.hello(answer=True))
            elif kind == "state":
                self._together_on_state(what[1])
        self._together_refresh()

    def _together_on_state(self, state: together.PlayState) -> None:
        """The room plays *state* now (someone chose it)."""
        media = state.media
        self._together_publish_pending = False
        self._together_missing = False
        if matching.same(self._together_local, media):
            self._together_follow = None
            self._together_note = ""
            self._together_fix.reset()
            self._together_set_status("ok")
            if media.synced:
                self._together_tick()
            return
        now = time.monotonic()
        if (self._together_follow is not None and matching.same(self._together_follow, media)
                and now - self._together_follow_at < FOLLOW_FOR):
            return          # already on its way
        self._together_follow = media
        self._together_follow_at = now
        self._together_note = tr("together_finding", title=media.name)
        self._together_set_status("load")
        tmdb = getattr(self, "tmdb", None)
        tmdb_of = tmdb.tmdb_id_for if tmdb is not None else None
        run_async(self.pool, _locate, self._together_found, self._together_lookup_failed,
                  self._together_lid, media, self.client, tmdb_of)

    def _together_lookup_failed(self, _msg: str = "") -> None:
        media = self._together_follow
        if media is not None:
            self._together_found((self._together_lid, media, None))

    def _together_found(self, got: tuple) -> None:
        lid, media, found = got
        room = self._together_room
        if lid != self._together_lid or room is None or room.state is None:
            return
        if not matching.same(room.state.media, media) or not matching.same(self._together_follow, media):
            return          # the room moved on meanwhile
        if found is None:
            log.info("together: %s is not in this source", for_log(media.name))
            self._together_follow = None
            self._together_missing = True
            self._together_note = tr("together_not_here", title=media.name)
            self._together_set_status("miss")
            self._together_toast(tr("together_not_here", title=media.name), 7000)
            self._together_refresh()
            return
        self._together_note = ""
        self._together_play(found, room.state)
        self._together_refresh()

    def _together_play(self, found: matching.Found, state: together.PlayState) -> None:
        """Start what the room plays, from this source, where the room is."""
        if found.kind == "live":
            self.play_live_channel(found.row)
            return
        room = self._together_room
        if room is None:
            return
        pos = state.position_at(room.clock.now_ms())
        if state.playing:
            pos += self._together_fix.lead      # it loads meanwhile; a hold evens it out
        self._together_start_at = pos
        row = found.row
        title = str(row.get("name") or state.media.name)
        icon = row.get("stream_icon") or row.get("cover")
        try:
            if found.kind == "movie":
                self._start_playback(found.url, title, icon, self._item_key(row), "movie", item=row)
            elif found.kind == "episode":
                saved = self.series_ctx
                self.series_ctx = found.series
                try:
                    queue = found.queue or []
                    self._ep_queue_override = queue
                    self._ep_index_override = next(
                        (i for i, e in enumerate(queue) if e is row), -1)
                    self._start_playback(found.url, title, icon, self._item_key(row), "episode",
                                         item=row)
                finally:
                    self.series_ctx = saved
        finally:
            self._together_start_at = None

    def _together_take_start(self) -> float | None:
        """_start_playback: where to start what the room plays (no resume
        question), or None for a start of this computer's own."""
        at, self._together_start_at = self._together_start_at, None
        return at

    # -- what plays here ------------------------------------------------------------------

    def _together_now_playing(self) -> together.Media | None:
        p = getattr(self, "player", None)
        lp = getattr(self, "_last_playback", None) or {}
        if p is None or not getattr(p, "current_url", None) or not lp:
            return None
        if lp.get("kind") == "live" and getattr(self, "_playing_catchup", False):
            return None
        if privacy.playback_private(lp.get("kind") or "", lp.get("key")):
            return None         # watched privately: never told to a room
        item = lp.get("item")
        if item is None and lp.get("kind") == "live":
            item = self._together_failover_row(lp.get("key"))
        return self._together_describe(lp.get("kind") or "", item, lp.get("series_ctx"))

    def _together_failover_row(self, key: Any) -> dict | None:
        """Failover plays the channel from another source (no row of this
        one's): it is still the room's channel."""
        look = getattr(self, "_failover_row_for", None)
        return look(key) if callable(look) else None

    def _together_describe(self, kind: str, item: Any, series: Any) -> together.Media | None:
        tmdb = getattr(self, "tmdb", None)
        return matching.describe(kind, item if isinstance(item, dict) else None,
                                 series if isinstance(series, dict) else None,
                                 tmdb.tmdb_id_for if tmdb is not None else None)

    def _together_my_status(self) -> str:
        if self._together_follow is not None:
            return "load"
        if self._together_missing:
            return "miss"
        return "ok" if self._together_local is not None else "idle"

    def _together_set_status(self, status: str) -> None:
        room, link = self._together_room, self._together_link
        if room is None or room.status == status:
            return
        room.status = status
        if link is not None:
            link.send(room.keepalive())
            self._together_keepalive_at = time.monotonic() + room.KEEPALIVE

    def _together_started(self, key: Any, title: str, item: Any, kind: str,
                          catchup: bool) -> None:
        """_start_playback and the live preview: the player took a stream."""
        try:
            if self._together_room is None:
                return
            lp = getattr(self, "_last_playback", None) or {}
            if item is None and kind == "live":
                item = self._together_failover_row(key)
            private = privacy.playback_private(kind, key)
            media = None if catchup or private else self._together_describe(
                kind, item, lp.get("series_ctx") if kind == "episode" else None)
            self._together_on_local(media)
        except Exception:
            log.debug("together: started", exc_info=True)

    def _together_on_local(self, media: together.Media | None) -> None:
        room = self._together_room
        if room is None:
            return
        self._together_local = media
        self._together_started_at = time.monotonic()
        self._together_fix.reset()
        self._together_watch.reset()
        self._together_publish_timer.stop()
        following = self._together_follow
        if media is not None:
            self._together_missing = False
        if following is not None and matching.same(following, media):
            self._together_follow = None
            self._together_note = ""
            self._together_set_status("ok")
            self._together_refresh()
            return
        if room.state is not None and matching.same(room.state.media, media):
            self._together_note = ""
            self._together_set_status("ok")          # a reconnect, or the same title again
            self._together_refresh()
            return
        if media is None:
            self._together_note = tr("together_not_shareable")
            self._together_set_status("idle")
            self._together_refresh()
            return
        # Chosen here: the room follows.
        self._together_follow = None
        self._together_note = ""
        self._together_publish_pending = True
        self._together_set_status("ok")
        if not media.synced:
            self._together_publish_now()
        self._together_refresh()

    def _together_stopped(self) -> None:
        """The player's Stop: nothing goes to the others."""
        if self._together_room is None:
            return
        self._together_local = None
        self._together_publish_pending = False
        self._together_set_status("idle")
        self._together_refresh()

    def _together_paused(self, paused: bool) -> None:
        """The player's paused_changed: told to the room unless it was the
        room's doing (a pause mirrored, a hold)."""
        try:
            room = self._together_room
            if room is None:
                return
            exp = self._together_expect_pause
            if exp is not None and exp[0] == bool(paused) and time.monotonic() <= exp[1]:
                self._together_expect_pause = None
                return
            if self._together_hold_timer.isActive():
                return
            media, state = self._together_local, room.state
            if media is None or not media.synced:
                return
            if state is None or not matching.same(state.media, media):
                return
            if time.monotonic() - self._together_started_at < LOAD_SETTLE:
                return
            self._together_publish_timer.start(300)
        except Exception:
            log.debug("together: paused", exc_info=True)

    def _together_player_paused(self) -> bool:
        p = self.player
        try:
            return bool(p.video.mpv.pause)
        except Exception:
            return bool(getattr(p, "_paused", False))

    def _together_publish_now(self) -> None:
        """What plays here, where, playing or not: the room's from now."""
        room, link, media = self._together_room, self._together_link, self._together_local
        if room is None or link is None or media is None or privacy.private():
            return
        p = getattr(self, "player", None)
        pos, playing = 0.0, True
        if media.synced and p is not None:
            pos = p.playback_position()
            playing = not self._together_player_paused()
        link.send(room.publish(media, playing, pos))
        self._together_publish_pending = False
        self._together_fix.reset()
        self._together_watch.reset()
        log.info("together: the room plays %s", for_log(media.name))
        self._together_refresh()

    # -- once a second ----------------------------------------------------------------------

    def _together_tick(self) -> None:
        room, link = self._together_room, self._together_link
        if room is None or link is None:
            return
        try:
            self._together_tick_room(room, link)
        except Exception:
            log.debug("together: tick", exc_info=True)

    def _together_tick_room(self, room: together.Room, link: together.Link) -> None:
        now = time.monotonic()
        gone = room.expire(now)
        for m in gone:
            self._together_toast(tr("together_left", name=m.name), 3000)
        if gone:
            self._together_refresh()
        if now >= self._together_keepalive_at:
            room.status = self._together_my_status()
            link.send(room.keepalive())
            self._together_keepalive_at = now + room.KEEPALIVE
        if self._together_settle_at and now >= self._together_settle_at:
            self._together_settle_at = 0.0
            if room.state is None and self._together_local is not None:
                self._together_publish_pending = True      # nobody plays anything: this does
        if (self._together_follow is not None and now - self._together_follow_at > FOLLOW_FOR
                and not matching.same(self._together_local, self._together_follow)):
            self._together_follow = None                     # a start refused here
            self._together_note = ""
            self._together_set_status(self._together_my_status())
            self._together_refresh()
        p = getattr(self, "player", None)
        media = self._together_local
        if p is None or media is None or not getattr(p, "current_url", None):
            return
        if not media.synced:
            if self._together_publish_pending:
                self._together_publish_now()
            return
        if now - self._together_started_at < LOAD_SETTLE or self._together_hold_timer.isActive():
            return
        pos, duration = p.playback_position(), p.playback_duration()
        if pos <= 0.2 and duration <= 0:
            return              # not loaded yet
        playing = not self._together_player_paused()
        if self._together_publish_pending:
            self._together_publish_now()
            return
        state = room.state
        if state is None or not matching.same(state.media, media):
            return
        jumped = self._together_watch.tick(pos, playing, now)
        if jumped and now - self._together_started_at >= START_GRACE:
            self._together_publish_timer.start(PUBLISH_MS)       # a seek here
            return
        if self._together_publish_timer.isActive():
            return
        fix = self._together_fix.decide(pos, playing, state.position_at(room.clock.now_ms()),
                                        state.playing, now, duration)
        self._together_apply(fix, now)

    def _together_apply(self, fix: together.Fix, now: float) -> None:
        p = self.player
        if fix.action == "none" or p is None or not getattr(p, "current_url", None):
            return
        if fix.action in ("play", "pause", "hold"):
            pause = fix.action != "play"
            if pause == self._together_player_paused():
                return
            self._together_expect_pause = (pause, now + 3.0)
            p.toggle_pause()
            if fix.action == "hold":
                self._together_hold_timer.start(max(100, int(fix.value * 1000)))
        elif fix.action == "seek":
            self._together_watch.expect_seek(fix.value, now)
            p._do_seek(fix.value)

    def _together_hold_end(self, force: bool = False) -> None:
        """A hold is over: play on - unless the room paused meanwhile."""
        p = getattr(self, "player", None)
        room = self._together_room
        if p is None or not getattr(p, "current_url", None):
            return
        if not force and (room is None or room.state is None or not room.state.playing):
            return
        if self._together_player_paused():
            self._together_expect_pause = (False, time.monotonic() + 3.0)
            p.toggle_pause()
