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
from ..core.log import log, redact_url

if TYPE_CHECKING:
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
        from ..core.dvr_store import sweep_abandoned
        threading.Thread(target=sweep_abandoned, args=(base,),
                         name="dvr-sweep", daemon=True).start()
    return base


class DvrPlayerMixin:
    _dvr: "DvrSession | None" = None
    _dvr_origin = 0.0
    _dvr_open_wall: float | None = None
    _dvr_reopened = 0.0

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

    def _dvr_start(self, url: str) -> str | None:
        """Start a session for *url*; the address mpv should play, or None
        to play the provider's as before."""
        if not self._dvr_wanted(url):
            return None
        minutes = self._live_rewind_minutes()
        try:
            from ..core.dvr_session import DvrSession  # only when it is used
            session = DvrSession(url, dvr_base(), keep_seconds=minutes * 60,
                                 max_bytes=self._dvr_space_bytes())
        except Exception as e:
            log.warning("DVR: own buffer unavailable (%s) - playing as before",
                        e)
            return None
        self._dvr = session
        self._dvr_origin = time.time()
        self._dvr_open_wall = None
        self._dvr_reopened = 0.0
        log.info("DVR: own buffer for %s (%d min, %d GB)", redact_url(url),
                 minutes, self._dvr_space_bytes() >> 30)
        return session.live_url()

    def _end_dvr(self) -> None:
        """Close the session - its provider connection at once, before
        another channel may need the account's only one."""
        session, self._dvr = self._dvr, None
        self._dvr_open_wall = None
        if session is not None:
            session.close()

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
        seg = session.segment_after(at - 0.5) if at is not None else None
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
