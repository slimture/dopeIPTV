"""Windows: the System Media Transport Controls - what plays in the media box
by the volume and on the lock screen, and the keyboard's media keys driving
the player.

The controls come from a Windows.Media.Playback.MediaPlayer that plays
nothing: its own command manager is off, so the controls show only what the
window says (title, the programme or series under it, the logo, a title's
timeline) and their buttons come back here - play, pause, stop, next and
previous (and a remote's channel up and down), fast forward and rewind and
the timeline for a title. PyWinRT's packages (Windows-only requirements) do
the bridging; without them the session is off.

The buttons arrive on a thread of Windows' own: each only queues its
command onto the GUI thread (PolledSession.ask). The logo is read from the
image cache into a stream in memory, never fetched by Windows itself.
"""
from __future__ import annotations

import time
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal

from ..core.log import log
from .media_session import (SKIP_BACK_S, SKIP_FWD_S, MediaHost, PolledSession,
                            local_art, say_once)

# Windows asks for the timeline at least this often while a title plays.
TIMELINE_EVERY_S = 5.0
# SystemMediaTransportControlsButton -> what the window is asked.
_BUTTONS: dict[int, tuple[str, Any]] = {
    0: ("play", None),            # PLAY
    1: ("pause", None),           # PAUSE
    2: ("stop", None),            # STOP
    4: ("seek", SKIP_FWD_S),      # FAST_FORWARD
    5: ("seek", -SKIP_BACK_S),    # REWIND
    6: ("next", None),            # NEXT
    7: ("previous", None),        # PREVIOUS
    8: ("next", None),            # CHANNEL_UP
    9: ("previous", None),        # CHANNEL_DOWN
}
# CO_E_NOTINITIALIZED: this thread has no COM apartment yet.
_NOT_INITIALIZED = (-2147221008, 0x800401F0)
_ASYNC_STARTED = 0
_ASYNC_COMPLETED = 1


def _winrt() -> SimpleNamespace:
    from winrt.windows.media import (MediaPlaybackStatus, MediaPlaybackType,
                                     SystemMediaTransportControlsTimelineProperties)
    from winrt.windows.media.playback import MediaPlayer
    from winrt.windows.storage.streams import (DataWriter,
                                               InMemoryRandomAccessStream,
                                               RandomAccessStreamReference)
    return SimpleNamespace(
        MediaPlayer=MediaPlayer, Status=MediaPlaybackStatus,
        Type=MediaPlaybackType,
        Timeline=SystemMediaTransportControlsTimelineProperties,
        DataWriter=DataWriter, MemoryStream=InMemoryRandomAccessStream,
        StreamReference=RandomAccessStreamReference)


def check() -> None:
    """Raises when this build lacks what the session uses (--self-check)."""
    w = _winrt()
    for enum, name in ((w.Status, "PLAYING"), (w.Type, "VIDEO")):
        getattr(enum, name)


def _new_player(w: SimpleNamespace) -> Any:
    try:
        return w.MediaPlayer()
    except OSError as e:
        if getattr(e, "winerror", None) not in _NOT_INITIALIZED:
            raise
    # Qt sets up COM on its GUI thread; should it not have yet, this thread
    # joins a single-threaded apartment, as Qt's own would be.
    from winrt import runtime
    runtime.init_apartment(runtime.ApartmentType.SINGLE_THREADED)
    return w.MediaPlayer()


class SmtcSession(PolledSession):
    """The window's media session as Windows' System Media Transport
    Controls."""

    NAME = "smtc"
    # A thumbnail stream finished writing, on a thread of Windows' own.
    _stored = pyqtSignal()

    def __init__(self, host: MediaHost, parent: Any = None) -> None:
        super().__init__(host, parent)
        self._w: Any = None
        self._player: Any = None
        self._smtc: Any = None
        self._tokens: list[tuple[str, Any]] = []
        self._timeline_at = 0.0
        self._thumb_for = ""          # the art string seen
        self._thumb: Any = None       # its RandomAccessStreamReference
        self._loading: Any = None     # (art, stream, writer, operation)
        self._stored.connect(self._thumb_stored,
                             Qt.ConnectionType.QueuedConnection)

    # -- PolledSession ---------------------------------------------------------

    def _open(self) -> bool:
        w = self._w = _winrt()
        player = self._player = _new_player(w)
        # Off, or the controls would follow this idle player, not the window.
        player.command_manager.is_enabled = False
        smtc = self._smtc = player.system_media_transport_controls
        smtc.is_enabled = True
        self._tokens.append(("button", smtc.add_button_pressed(self._on_button)))
        self._tokens.append(("position", smtc.add_playback_position_change_requested(
            self._on_position)))
        self._enable(self.snap)
        smtc.playback_status = w.Status.CLOSED
        return True

    def _close(self) -> None:
        smtc = self._smtc
        if smtc is not None:
            for kind, token in self._tokens:
                try:
                    if kind == "button":
                        smtc.remove_button_pressed(token)
                    else:
                        smtc.remove_playback_position_change_requested(token)
                except Exception as e:
                    log.debug("smtc: removing a handler: %s", e)
            try:
                smtc.display_updater.clear_all()
                smtc.display_updater.update()
                smtc.playback_status = self._w.Status.CLOSED
                smtc.is_enabled = False
            except Exception as e:
                log.debug("smtc: clearing: %s", e)
        self._tokens.clear()
        if self._player is not None:
            try:
                self._player.close()
            except Exception as e:
                log.debug("smtc: closing the player: %s", e)
        self._smtc = self._player = None
        self._thumb = self._loading = None
        self._thumb_for = ""

    def _show(self, new: dict[str, Any], old: dict[str, Any],
              what: set[str]) -> None:
        smtc, w = self._smtc, self._w
        if what & {"caps", "status", "meta"}:
            self._enable(new)
        if new["status"] == "Stopped":
            if "status" in what:
                # Closed, not stopped: nothing playing, no media box.
                smtc.playback_status = w.Status.CLOSED
                self._display(new)
        else:
            if "meta" in what or old["status"] == "Stopped":
                self._display(new)
            if what & {"status", "meta"}:
                smtc.playback_status = (w.Status.PLAYING
                                        if new["status"] == "Playing"
                                        else w.Status.PAUSED)
        self._timeline(new, what)

    # -- what Windows is told ------------------------------------------------------

    def _enable(self, s: dict[str, Any]) -> None:
        smtc = self._smtc
        on = s["status"] != "Stopped"
        smtc.is_play_enabled = True
        smtc.is_pause_enabled = on
        smtc.is_stop_enabled = on
        smtc.is_next_enabled = s["can_next"]
        smtc.is_previous_enabled = s["can_prev"]
        smtc.is_channel_up_enabled = s["live"] and s["can_next"]
        smtc.is_channel_down_enabled = s["live"] and s["can_prev"]
        smtc.is_fast_forward_enabled = s["can_seek"]
        smtc.is_rewind_enabled = s["can_seek"]

    def _display(self, s: dict[str, Any]) -> None:
        u = self._smtc.display_updater
        u.clear_all()
        if s["status"] != "Stopped":
            if s["audio"]:
                u.type = self._w.Type.MUSIC
                music = u.music_properties
                music.title = s["title"]
                music.artist = s["artist"]
                music.album_title = s["album"]
            else:
                u.type = self._w.Type.VIDEO
                video = u.video_properties
                video.title = s["title"]
                video.subtitle = s["artist"]
            thumb = self._thumbnail(s["art"])
            if thumb is not None:
                u.thumbnail = thumb
        u.update()

    def _timeline(self, s: dict[str, Any], what: set[str]) -> None:
        now = time.monotonic()
        due = (s["status"] == "Playing" and s["length"] > 0
               and now - self._timeline_at >= TIMELINE_EVERY_S)
        if not (due or what & {"meta", "status", "position"}):
            return
        length = s["length"] if s["status"] != "Stopped" else 0.0
        t = self._w.Timeline()
        zero, end = timedelta(0), timedelta(seconds=length)
        t.start_time = zero
        t.min_seek_time = zero
        t.end_time = end
        t.max_seek_time = end if s["can_seek"] else zero
        t.position = timedelta(seconds=min(s["position"], length))
        self._smtc.update_timeline_properties(t)
        self._timeline_at = now

    # -- the thumbnail ---------------------------------------------------------------

    def _thumbnail(self, art: str) -> Any:
        """The logo or cover as a stream reference, made once per picture;
        None for none - or while it is still being written, after which
        _thumb_stored shows it."""
        if art == self._thumb_for:
            return self._thumb
        self._thumb_for = art
        self._thumb = self._loading = None
        path = local_art(art)
        if path is None:
            return None
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            w = self._w
            stream = w.MemoryStream()
            writer = w.DataWriter(stream)
            writer.write_bytes(data)
            op = writer.store_async()
            self._loading = (art, stream, writer, op)
            op.completed = self._on_stored
        except Exception as e:
            self._loading = None
            say_once("smtc:thumb", "smtc: no thumbnail (%s: %s)",
                     type(e).__name__, e)
        return None

    def _on_stored(self, _op: Any, _status: Any) -> None:
        # On a thread of Windows' own (or at once, if it was done already).
        try:
            if self.active:
                self._stored.emit()
        except RuntimeError:
            pass

    def _thumb_stored(self) -> None:
        loading = self._loading
        if not self.active or loading is None:
            return
        art, stream, writer, op = loading
        try:
            status = int(op.status)
            if status == _ASYNC_STARTED:
                return                 # an older one finished; this one will say so
            self._loading = None
            if status != _ASYNC_COMPLETED:
                say_once("smtc:thumb", "smtc: no thumbnail (status %s)", status)
                return
            # Detached, or the writer closes the stream when it goes.
            writer.detach_stream()
            stream.seek(0)
            self._thumb = self._w.StreamReference.create_from_stream(stream)
        except Exception as e:
            say_once("smtc:thumb", "smtc: no thumbnail (%s: %s)",
                     type(e).__name__, e)
            return
        if self.snap["status"] != "Stopped" and self.snap["art"] == art:
            try:
                u = self._smtc.display_updater
                u.thumbnail = self._thumb
                u.update()
            except Exception as e:
                log.debug("smtc: showing the thumbnail: %s", e)

    # -- what Windows asks (its own threads) ---------------------------------------

    def _on_button(self, _sender: Any, args: Any) -> None:
        try:
            command = _BUTTONS.get(int(args.button))
        except Exception:
            return
        if command is not None:
            self.ask(*command)

    def _on_position(self, _sender: Any, args: Any) -> None:
        try:
            position = args.requested_playback_position.total_seconds()
        except Exception:
            return
        self.ask("set_position", position)
