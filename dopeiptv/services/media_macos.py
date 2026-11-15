"""macOS: Now Playing - what plays in Control Center, on the lock screen and
in the menu bar, and the keyboard's media keys and AirPods driving the player.

MPNowPlayingInfoCenter is told what plays (title, the programme or series
under it, the logo, a channel as live or a title's length and position);
MPRemoteCommandCenter's commands hand back what was asked: play, pause,
play/pause, stop, the next and previous channel or episode, and for a title
the skip buttons and the scrubber. PyObjC's MediaPlayer wrapper (a
macOS-only requirement) does the bridging; without it the session is off.

The system calls the commands' handlers on the main thread - the GUI
thread - and each only queues its command (PolledSession.ask) and answers
at once; an exception must never cross back into Objective-C.
"""
from __future__ import annotations

from typing import Any

from ..core.log import log
from .media_session import (SKIP_BACK_S, SKIP_FWD_S, MediaHost, PolledSession,
                            local_art)

# MPRemoteCommandHandlerStatus
_SUCCESS = 0
_FAILED = 200
# MPNowPlayingPlaybackState
_PLAYING, _PAUSED, _STOPPED = 1, 2, 3
# MPNowPlayingInfoMediaType
_MEDIA_AUDIO, _MEDIA_VIDEO = 1, 2

# Command (an MPRemoteCommandCenter property) -> what the window is asked.
_COMMANDS = (
    ("playCommand", "play"),
    ("pauseCommand", "pause"),
    ("togglePlayPauseCommand", "playpause"),
    ("stopCommand", "stop"),
    ("nextTrackCommand", "next"),
    ("previousTrackCommand", "previous"),
    ("skipForwardCommand", "skip_forward"),
    ("skipBackwardCommand", "skip_backward"),
    ("changePlaybackPositionCommand", "position"),
)


def _frameworks() -> tuple[Any, Any, Any]:
    import AppKit
    import Foundation
    import MediaPlayer
    return MediaPlayer, AppKit, Foundation


def check() -> None:
    """Raises when this build lacks what the session uses (--self-check)."""
    mp, ak, fd = _frameworks()
    for module, names in (
            (mp, ("MPNowPlayingInfoCenter", "MPRemoteCommandCenter",
                  "MPMediaItemArtwork", "MPMediaItemPropertyTitle",
                  "MPNowPlayingInfoPropertyElapsedPlaybackTime")),
            (ak, ("NSImage",)),
            (fd, ("NSMutableDictionary", "NSNumber", "NSString", "NSArray"))):
        for name in names:
            getattr(module, name)


class _Artwork:
    """The artwork's request handler: the picture made on the GUI thread,
    handed to the system at whatever size and on whatever thread it asks."""

    def __init__(self, image: Any) -> None:
        self.image = image

    def __call__(self, _size: Any) -> Any:
        return self.image


class NowPlayingSession(PolledSession):
    """The window's media session as macOS's Now Playing."""

    NAME = "now playing"

    def __init__(self, host: MediaHost, parent: Any = None) -> None:
        super().__init__(host, parent)
        self._mp: Any = None
        self._ak: Any = None
        self._fd: Any = None
        self._center: Any = None
        self._cc: Any = None
        self._targets: list[tuple[Any, Any]] = []      # (command, target)
        self._art_for = ""                             # the art string seen
        self._artwork: Any = None

    # -- PolledSession ---------------------------------------------------------

    def _open(self) -> bool:
        self._mp, self._ak, self._fd = _frameworks()
        self._center = self._mp.MPNowPlayingInfoCenter.defaultCenter()
        cc = self._cc = self._mp.MPRemoteCommandCenter.sharedCommandCenter()
        for prop, name in _COMMANDS:
            command = getattr(cc, prop)()
            handler = getattr(self, "_on_" + name)
            self._targets.append((command, command.addTargetWithHandler_(handler)))
        for prop, step in (("skipForwardCommand", SKIP_FWD_S),
                           ("skipBackwardCommand", SKIP_BACK_S)):
            getattr(cc, prop)().setPreferredIntervals_(
                self._fd.NSArray.arrayWithObject_(
                    self._fd.NSNumber.numberWithDouble_(step)))
        self._enable(self.snap)
        return True

    def _close(self) -> None:
        for command, target in self._targets:
            try:
                command.removeTarget_(target)
                command.setEnabled_(False)
            except Exception as e:
                log.debug("now playing: removing a command: %s", e)
        self._targets.clear()
        if self._center is not None:
            self._center.setNowPlayingInfo_(None)
            self._center.setPlaybackState_(_STOPPED)
        self._artwork = None
        self._art_for = ""

    def _show(self, new: dict[str, Any], old: dict[str, Any],
              what: set[str]) -> None:
        if what & {"caps", "status"}:
            self._enable(new)
        if what & {"meta", "status", "position"}:
            self._publish(new)

    # -- what the system is told -------------------------------------------------

    def _enable(self, s: dict[str, Any]) -> None:
        cc = self._cc
        if cc is None:
            return
        playing = s["status"] != "Stopped"
        # Play always: after a stop it plays the last title again, as the
        # player's own button does. (_close turned every command off.)
        for prop, on in (("playCommand", True),
                         ("togglePlayPauseCommand", True),
                         ("pauseCommand", playing),
                         ("stopCommand", playing),
                         ("nextTrackCommand", s["can_next"]),
                         ("previousTrackCommand", s["can_prev"]),
                         ("skipForwardCommand", s["can_seek"]),
                         ("skipBackwardCommand", s["can_seek"]),
                         ("changePlaybackPositionCommand", s["can_seek"])):
            getattr(cc, prop)().setEnabled_(bool(on))

    def _publish(self, s: dict[str, Any]) -> None:
        center = self._center
        if s["status"] == "Stopped":
            center.setNowPlayingInfo_(None)
            center.setPlaybackState_(_STOPPED)
            return
        mp, fd = self._mp, self._fd
        info = fd.NSMutableDictionary.dictionary()

        def put(key_name: str, value: Any) -> None:
            # A key this macOS does not know is left out, not an error.
            key = getattr(mp, key_name, None)
            if key is not None and value is not None:
                info.setObject_forKey_(value, key)

        def text(value: str) -> Any:
            # A real NSString, not a proxy of a Python one: the dictionary
            # crosses to the system's media service.
            return fd.NSString.stringWithString_(value) if value else None

        number = fd.NSNumber
        playing = s["status"] == "Playing"
        put("MPMediaItemPropertyTitle", text(s["title"]))
        put("MPMediaItemPropertyArtist", text(s["artist"]))
        put("MPMediaItemPropertyAlbumTitle", text(s["album"]))
        put("MPNowPlayingInfoPropertyMediaType", number.numberWithUnsignedInteger_(
            _MEDIA_AUDIO if s["audio"] else _MEDIA_VIDEO))
        put("MPNowPlayingInfoPropertyIsLiveStream",
            number.numberWithBool_(bool(s["live"])))
        # Shown while it plays, never kept for Siri's suggestions - as MPRIS,
        # which a desktop shows and forgets.
        put("MPNowPlayingInfoPropertyExcludeFromSuggestions",
            number.numberWithBool_(True))
        put("MPNowPlayingInfoPropertyPlaybackRate",
            number.numberWithDouble_(1.0 if playing else 0.0))
        put("MPNowPlayingInfoPropertyDefaultPlaybackRate",
            number.numberWithDouble_(1.0))
        if s["length"] > 0:
            # The system moves the position on from here at the rate above;
            # it is told again on a pause, a play and a seek.
            put("MPMediaItemPropertyPlaybackDuration",
                number.numberWithDouble_(s["length"]))
            put("MPNowPlayingInfoPropertyElapsedPlaybackTime",
                number.numberWithDouble_(min(s["position"], s["length"])))
        put("MPMediaItemPropertyArtwork", self._artwork_for(s["art"]))
        center.setNowPlayingInfo_(info)
        center.setPlaybackState_(_PLAYING if playing else _PAUSED)

    def _artwork_for(self, art: str) -> Any:
        """The logo or cover as an MPMediaItemArtwork, made once per picture;
        None for none, or for one that is not on this computer."""
        if art == self._art_for:
            return self._artwork
        self._art_for = art
        self._artwork = None
        path = local_art(art)
        if path is None:
            return None
        image = self._ak.NSImage.alloc().initWithContentsOfFile_(path)
        if image is None:
            return None
        size = image.size()
        w, h = float(size.width), float(size.height)
        if w <= 0 or h <= 0:
            return None
        self._artwork = self._mp.MPMediaItemArtwork.alloc(
        ).initWithBoundsSize_requestHandler_((w, h), _Artwork(image))
        return self._artwork

    # -- what the system asks (main thread) ------------------------------------

    def _handle(self, name: str, arg: Any = None) -> int:
        try:
            self.ask(name, arg)
            return _SUCCESS
        except Exception as e:
            log.debug("now playing: %s: %s", name, e)
            return _FAILED

    def _on_play(self, _event: Any) -> int:
        return self._handle("play")

    def _on_pause(self, _event: Any) -> int:
        return self._handle("pause")

    def _on_playpause(self, _event: Any) -> int:
        return self._handle("playpause")

    def _on_stop(self, _event: Any) -> int:
        return self._handle("stop")

    def _on_next(self, _event: Any) -> int:
        return self._handle("next")

    def _on_previous(self, _event: Any) -> int:
        return self._handle("previous")

    def _on_skip_forward(self, event: Any) -> int:
        return self._handle("seek", _interval(event, SKIP_FWD_S))

    def _on_skip_backward(self, event: Any) -> int:
        return self._handle("seek", -_interval(event, SKIP_BACK_S))

    def _on_position(self, event: Any) -> int:
        try:
            position = float(event.positionTime())
        except Exception:
            return _FAILED
        return self._handle("set_position", position)


def _interval(event: Any, default: float) -> float:
    """An MPSkipIntervalCommandEvent's interval in seconds (always forward)."""
    try:
        value = abs(float(event.interval()))
    except Exception:
        return default
    return value or default
