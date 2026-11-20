"""The player's side of the application-kept rewind buffer (DvrSession).

With Settings -> Playback -> "Rewind with dopeIPTV's own buffer" on, a live
channel in the TS format is no longer opened by mpv: a DvrSession reads it
from the provider into its store, and mpv plays the session's local server.
current_url stays the provider's address, so history, recording and the
app's reconnect see the channel as before.

The DVR row works in the session's time - wall-clock seconds since it began
- instead of mpv's: the position is where the opened segment began plus
mpv's position in it. A seek within what mpv holds is mpv's own; anything
further opens the local stream at another segment, which is quick, and never
touches the provider. An end of file (a reconnect gap in the store) moves on
to what follows; a stall is answered from the store. Only when the session's
own provider connection gives up does the app's reconnect take over.

Mixed into EmbeddedPlayer; it reads the player's attributes.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from PyQt6.QtCore import QStandardPaths, QTimer

from typing import TYPE_CHECKING

from ..core.local_buffer import BufferWindow, buffer_window
from ..core.log import log
from ..core.privacy import url_for_log
from ..i18n import tr

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..core.dvr_pool import DvrPool
    from ..core.dvr_record import BufferRecorder
    from ..core.dvr_session import DvrSession
    from ..core.dvr_store import Segment

#: Space choices for the buffer, in GB (Settings -> Playback).
DVR_SPACE_GB = ("1", "2", "5", "10", "20")
_swept = False


def dvr_base() -> Path:
    """Where sessions keep their segments; abandoned ones swept once."""
    global _swept
    loc = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.CacheLocation)
    base = Path(loc or Path.home() / ".cache" / "dopeiptv") / "dvr"
    if not _swept:
        _swept = True
        from ..core.dvr_store import memory_base, sweep_abandoned

        def sweep() -> None:
            sweep_abandoned(base)
            memory = memory_base()
            if memory is not None and memory.is_dir():
                sweep_abandoned(memory)
        threading.Thread(target=sweep, name="dvr-sweep", daemon=True).start()
    return base


def dvr_memory() -> Path | None:
    """Where a channel watched privately from its start keeps its buffer
    in memory (core/dvr_store.memory_base), or None."""
    from ..core.dvr_store import memory_base
    try:
        return memory_base()
    except Exception as e:      # never in the way of playback
        log.debug("DVR: no buffer in memory: %s", e)
        return None


class DvrPlayerMixin:
    _dvr: "DvrSession | None" = None
    _dvr_pool: "DvrPool | None" = None
    _buffer_rec: "BufferRecorder | None" = None
    _dvr_origin = 0.0
    _dvr_open_wall: float | None = None
    _dvr_reopened = 0.0
    #: Server mode (ui/mw_server.py) while it runs: asked before a stream
    #: opens, with (url, a live channel's buffer key or None) - the session
    #: the server streams that channel from, to play without a connection of
    #: its own, or None (the server let go of what the player needs first).
    #: None while server mode is off: playback is as it always was.
    live_hook: "Callable[[str, str | None], DvrSession | None] | None" = None
    #: The window's answer to which source (id) the stream about to open is
    #: from (ui/mw_connections): put on its session, so a buffer a recording
    #: or a cast keeps after the player moved on is counted on its own
    #: account. None: not known, as before.
    source_of: "Callable[[], str] | None" = None

    def _dvr_source(self) -> str:
        ask = self.source_of
        try:
            return str(ask() or "") if ask is not None else ""
        except Exception as e:      # never in the way of playback
            log.debug("DVR: the stream's source not known: %s", e)
            return ""

    # -- settings --------------------------------------------------------------

    def _dvr_wanted(self, url: str) -> bool:
        s = self._settings
        if s is None or s.value("live_rewind_kept", "false") != "true":
            return False
        parts = urlparse(url or "")
        return (parts.scheme in ("http", "https")
                and not parts.path.lower().endswith(".m3u8"))

    def _dvr_space_bytes(self) -> int:
        try:
            gb = int(self._settings.value("live_rewind_space_gb", "2"))
        except (TypeError, ValueError, AttributeError):
            gb = 2
        return max(1, gb) << 30

    # -- start and end ---------------------------------------------------------

    def _dvr_start(self, url: str, key: str | None = None) -> str | None:
        """Start (or resume) the session for *url*; the address mpv should
        play, or None to play the provider's as before."""
        if not self._dvr_wanted(url):
            if (self._dvr_pool is not None and not self._settings_dvr_on()
                    and not self._dvr_pool.shared
                    and not any(s.recording for s in self._dvr_pool.sessions)):
                # Turned off: the history of the channels left goes too.
                self._dvr_pool.close_all()
                self._dvr_pool = None
            return None
        minutes = self._live_rewind_minutes()
        try:
            if self._dvr_pool is None:
                self._dvr_pool = self._dvr_new_pool(minutes * 60)
            self._dvr_pool.configure(minutes * 60, self._dvr_space_bytes())
            session = self._dvr_pool.open(url, key, source=self._dvr_source(),
                                          private=self._dvr_playing_private())
        except Exception as e:
            from ..core.dvr_ingest import NotAStream
            from ..core.dvr_pool import NoPrivateBuffer
            if isinstance(e, NotAStream):
                # Its provider sent a page or a message, not a stream: mpv
                # opens the address itself and says what it found.
                log.info("DVR: %s, not a stream - playing as before", e)
            elif isinstance(e, NoPrivateBuffer):
                # Watched privately with no memory out of a disk's reach:
                # no buffer of the app's - never one on the disk.
                log.info("DVR: private viewing - no rewind buffer of the app's (%s)", e)
            else:
                log.warning("DVR: own buffer unavailable (%s) - playing as "
                            "before", e)
            return None
        self._dvr = session
        self._dvr_private(session)
        self._dvr_origin = session.origin
        self._dvr_open_wall = None
        self._dvr_open_seq = None
        self._dvr_reopened = 0.0
        log.info("DVR: own buffer for %s (%d min, %d GB)", url_for_log(url),
                 minutes, self._dvr_space_bytes() >> 30)
        return session.live_url()

    def _dvr_adopt(self, session: "DvrSession") -> str | None:
        """Play *session*, which server mode streams already: its local
        server, no second provider connection. The address mpv plays, or
        None to play as before."""
        pool = self._dvr_pool
        if pool is None or session.closed:
            return None
        try:
            pool.adopt(session)
            url = session.live_url()
            if not getattr(session, "source", ""):
                session.source = self._dvr_source()
        except Exception as e:
            log.warning("DVR: the server's channel not shared (%s) - playing as before", e)
            return None
        self._dvr = session
        self._dvr_private(session)
        self._dvr_origin = session.origin
        self._dvr_open_wall = None
        self._dvr_open_seq = None
        self._dvr_reopened = 0.0
        log.info("DVR: the server's connection for this channel, shared")
        return url

    # -- private viewing ---------------------------------------------------------

    @staticmethod
    def _dvr_playing_private() -> bool:
        from ..core import privacy
        try:
            return bool(privacy.playing_private())
        except Exception as e:
            log.debug("DVR: private viewing not known: %s", e)
            return False

    @staticmethod
    def _dvr_private(session: "DvrSession") -> None:
        """A channel watched privately (core/privacy.py: asked of the
        playback, which play() was told of before it got here): its
        history goes when it is left, and never outlives the app."""
        from ..core import privacy
        try:
            if privacy.playing_private():
                session.mark_private()
        except Exception as e:
            log.debug("DVR: not marked private: %s", e)

    def dvr_mark_private(self) -> None:
        """Private viewing was turned on while a channel plays."""
        if self._dvr is not None:
            self._dvr.mark_private()

    def private_reopen_needed(self) -> bool:
        """Private viewing was turned on while a stream plays whose rewind
        buffer is on the disk - the app's (core/dvr_pool.on_disk_private)
        or mpv's own disk cache: it must be opened again, so what follows
        is kept in memory or not at all."""
        pool = self._dvr_pool
        if self._dvr is not None and pool is not None and pool.on_disk_private():
            return True
        return bool(getattr(self, "_mpv_cache_on_disk", False))

    def dvr_forget_private(self) -> None:
        """Private viewing ended: the buffers of the channels left in it go."""
        if self._dvr_pool is not None:
            self._dvr_pool.forget_private()

    def dvr_pool_for_server(self, keep_seconds: float) -> "DvrPool":
        """The pool server mode reads its channels from: the player's own
        (made now, with the rewind buffer's space, when it has none), so a
        channel both watch has one connection."""
        if self._dvr_pool is None:
            minutes = self._live_rewind_minutes()
            self._dvr_pool = self._dvr_new_pool(max(keep_seconds, minutes * 60))
        return self._dvr_pool

    def _dvr_new_pool(self, keep_seconds: float) -> "DvrPool":
        from ..core.dvr_pool import DvrPool     # only when it is used
        pool = DvrPool(dvr_base(), keep_seconds, self._dvr_space_bytes(), memory=dvr_memory())
        # From the recording's or the cast's thread: a signal crosses to
        # the window's thread by itself.
        signal = getattr(self, "private_on_disk", None)
        if signal is not None:
            pool.on_private_disk = lambda _s: signal.emit()
        return pool

    def _end_dvr(self) -> None:
        """Leave the session - its provider connection closes at once,
        before another channel may need the account's only one, unless a
        recording reads it. Its history stays in the pool."""
        session, self._dvr = self._dvr, None
        self._dvr_open_wall = None
        self._dvr_open_seq = None
        if session is None:
            return
        if self._dvr_pool is not None:
            self._dvr_pool.release(session)
        else:
            session.close()

    def _settings_dvr_on(self) -> bool:
        s = self._settings
        return s is not None and s.value("live_rewind_kept", "false") == "true"

    def dvr_shutdown(self) -> None:
        """The app ends: every buffer goes, recordings write what they hold
        first."""
        pool, self._dvr_pool = self._dvr_pool, None
        self._dvr = None
        if pool is not None:
            pool.close_all(wait=3.0)

    # -- recording from the buffer -----------------------------------------------

    def _dvr_record(self, path: str) -> bool:
        """Record what is on screen on, from the buffer: True when it does.
        It needs no connection of its own and goes on through a pause."""
        session = self._dvr
        if session is None:
            return False
        try:
            self._buffer_rec = session.record(path, self._dvr_wall_now())
        except Exception as e:
            log.warning("DVR: recording from the buffer failed (%s)", e)
            self._buffer_rec = None
            return False
        return True

    def take_buffer_recorder(self) -> "BufferRecorder | None":
        """The recording just started from the buffer, for its job to own."""
        rec, self._buffer_rec = self._buffer_rec, None
        return rec

    def _stop_buffer_record(self) -> None:
        rec, self._buffer_rec = self._buffer_rec, None
        if rec is not None:
            rec.stop()

    # -- time ------------------------------------------------------------------

    def _dvr_resolve_open(self) -> float | None:
        if self._dvr_open_wall is None and self._dvr is not None:
            self._dvr_open_wall = self._dvr.server.last_opened_t0
        return self._dvr_open_wall

    def _dvr_pos(self, pos: float | None) -> float | None:
        """mpv's position in the session's time."""
        opened = self._dvr_resolve_open()
        if opened is None or pos is None:
            return None
        return opened - self._dvr_origin + float(pos)

    def _local_position(self) -> float:
        if self._dvr is None:
            return self.playback_position()
        return self._dvr_pos(self.playback_position()) or 0.0

    def _dvr_gaps(self, start: float, span: float) -> list[float]:
        """Where a run begins after a gap, as fractions of the row - the
        channel left and come back to, or a reconnect."""
        session = self._dvr
        if session is None or span <= 0:
            return []
        out = []
        for _run, t0, _t1 in session.runs()[1:]:
            f = (t0 - self._dvr_origin - start) / span
            if 0.0 < f < 1.0:
                out.append(round(f, 3))
        return out

    def _dvr_hover(self, start: float, span: float, frac: float) -> str | None:
        """The clock time under the pointer on the DVR row - and whether
        anything was kept there."""
        session = self._dvr
        if session is None or span <= 0:
            return None
        t = self._dvr_origin + start + frac * span
        label = time.strftime("%H:%M:%S", time.localtime(t))
        if not any(t0 - 1.0 <= t <= t1 + 1.0 for _r, t0, t1 in session.runs()):
            label += " · " + tr("dvr_gap")
        return label

    def _dvr_window(self) -> BufferWindow | None:
        span = self._dvr.span() if self._dvr is not None else None
        if span is None or self._dvr_resolve_open() is None:
            return None
        return BufferWindow(span[0] - self._dvr_origin,
                            span[1] - self._dvr_origin)

    # -- moving about ----------------------------------------------------------

    def _dvr_recording_in_player(self) -> bool:
        try:
            return bool(self.video.mpv["stream-record"])
        except Exception:
            return False

    def _dvr_seek(self, target: float, window: BufferWindow) -> None:
        """Go to *target* (session time): in what mpv holds, else from the
        store."""
        session, m = self._dvr, self.video.mpv
        if session is None or m is None:
            return
        wall = self._dvr_origin + window.clamp(target)
        opened = self._dvr_resolve_open()
        if opened is not None:
            local = wall - opened
            try:
                held = buffer_window(m.demuxer_cache_state, m.playback_time)
            except Exception:
                held = None
            if held is not None and held.start + 0.3 <= local <= held.end - 0.3:
                try:
                    m.command("seek", local, "absolute+exact")
                except Exception as e:
                    log.debug("DVR seek failed: %s", e)
                return
        if self._dvr_recording_in_player():
            return      # a recording made in the player keeps its stream
        seg = session.segment_at(wall)
        if seg is not None:
            self._dvr_open(seg)

    def _dvr_open(self, seg: "Segment") -> None:
        """Play the store from *seg* - the local server, not the provider."""
        session, m = self._dvr, self.video.mpv
        if session is None or m is None:
            return
        self._dvr_open_wall = seg.t0
        self._dvr_open_seq = seg.seq
        self._dvr_reopened = time.monotonic()
        self._eof_seen = False
        self._stall_since = 0.0
        try:
            m.play(session.url_for(seg))
        except Exception as e:
            log.warning("DVR: reopening the buffer failed: %s", e)
            return
        if not self._user_paused:
            self._set_mpv("pause", False)
            QTimer.singleShot(300, lambda: (not self._user_paused
                                            and self._set_mpv("pause", False)))

    def _dvr_wall_now(self) -> float | None:
        opened = self._dvr_resolve_open()
        try:
            return None if opened is None else \
                opened + float(self.video.mpv.playback_time or 0.0)
        except Exception:
            return None

    def _dvr_on_eof(self) -> bool:
        """The local stream ended - a reconnect gap, or what it was reading
        was let go. Go on from what follows; True when handled."""
        session = self._dvr
        if session is None or self._dvr_recording_in_player():
            return False
        if time.monotonic() - self._dvr_reopened < 2.0:
            return True             # the reopen is on its way
        state = session.state
        if state in ("failed", "stopped"):
            return False            # the app's own reconnect takes over
        at = self._dvr_wall_now()
        seq = getattr(self, "_dvr_open_seq", None)
        seg = session.next_run_after(at if at is not None else 0.0, seq) \
            if (at is not None or seq is not None) else None
        if seg is None:
            # The next run has not begun yet: look again shortly.
            QTimer.singleShot(1000, self._dvr_on_eof)
            return True
        log.info("DVR: on from segment %d after the end of a run", seg.seq)
        self._dvr_open(seg)
        return True

    def _dvr_on_stall(self) -> bool:
        """mpv sat starved: answer from the store, where it was. True when
        handled; False to let the app reconnect (its own connection gave up,
        or a recording in the player holds the stream)."""
        session = self._dvr
        if (session is None or session.state == "failed"
                or self._dvr_recording_in_player()):
            return False
        at = self._dvr_wall_now()
        seg = (session.segment_at(at) if at is not None else None) \
            or session.live_segment()
        if seg is not None:
            log.info("DVR: stalled - reopening the buffer at segment %d",
                     seg.seq)
            self._dvr_open(seg)
        return True
