"""MPRIS: the media session every Linux desktop controls.

Exports org.mpris.MediaPlayer2 and org.mpris.MediaPlayer2.Player on the
session bus, so GNOME's and KDE's media controls, the keyboard's media
keys, headphone buttons and KDE Connect on a phone all drive the player.
Linux only; elsewhere, or without a session bus, it stays off quietly.

Window-agnostic: the window answers through MprisHost - snapshot() says
what is playing, command() does what was asked - and this module only
speaks D-Bus. QtDBus comes with PyQt6, so there is nothing to install.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any, Protocol

from PyQt6.QtCore import QMetaType, QObject, QTimer

from ..core.log import log

BUS_NAME = "org.mpris.MediaPlayer2.dopeiptv"
OBJECT_PATH = "/org/mpris/MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
# How often the window is asked what is playing. The desktop draws its
# controls from PropertiesChanged; a state change the window reports at
# once (see refresh) does not wait for this.
POLL_MS = 1000


class MprisHost(Protocol):
    def mpris_snapshot(self) -> dict[str, Any]:
        """status ("Playing"/"Paused"/"Stopped"), track (any id that changes
        per title), title, artist, album, art (URL), length and position (s),
        can_seek, can_next, can_prev, volume (0-1)."""

    def mpris_position(self) -> float:
        """Seconds into what is playing, read now."""

    def mpris_command(self, name: str, arg: Any = None) -> None:
        """play, pause, playpause, stop, next, previous, seek (+/- s),
        set_position (s), volume (0-1), raise, quit."""


def _int64(value: int) -> Any:
    from PyQt6.QtDBus import QDBusArgument
    arg = QDBusArgument()
    arg.add(int(value), QMetaType.Type.LongLong.value)  # type: ignore[arg-type]
    return arg


def _strings(values: list[str]) -> Any:
    from PyQt6.QtDBus import QDBusArgument
    arg = QDBusArgument()
    arg.add(list(values), QMetaType.Type.QStringList.value)  # type: ignore[arg-type]
    return arg


def _clean(snap: dict[str, Any]) -> dict[str, Any]:
    """The snapshot with every field present and of the right type."""
    def num(key: str) -> float:
        try:
            return max(0.0, float(snap.get(key) or 0.0))
        except (TypeError, ValueError):
            return 0.0
    status = snap.get("status")
    return {
        "status": status if status in ("Playing", "Paused") else "Stopped",
        "track": str(snap.get("track") or ""),
        "title": str(snap.get("title") or ""),
        "artist": str(snap.get("artist") or ""),
        "album": str(snap.get("album") or ""),
        "art": str(snap.get("art") or ""),
        "length": num("length"),
        "position": num("position"),
        "can_seek": bool(snap.get("can_seek")),
        "can_next": bool(snap.get("can_next")),
        "can_prev": bool(snap.get("can_prev")),
        "volume": min(1.0, num("volume")),
    }


def _build_adaptors(service: MprisService) -> None:
    """The two exported interfaces, as QDBusAbstractAdaptors of *service*.
    Built here so QtDBus is imported only where it is used."""
    # Not in PyQt6's stubs, which also type pyqtClassInfo as returning
    # nothing - it is a class decorator all the same.
    from PyQt6.QtCore import (  # type: ignore[attr-defined]
        pyqtClassInfo, pyqtProperty, pyqtSignal, pyqtSlot)
    from PyQt6.QtDBus import QDBusAbstractAdaptor, QDBusObjectPath

    @pyqtClassInfo("D-Bus Interface", "org.mpris.MediaPlayer2")  # type: ignore[func-returns-value]
    class Root(QDBusAbstractAdaptor):
        @pyqtProperty(bool)
        def CanQuit(self) -> bool:
            return True

        @pyqtProperty(bool)
        def CanRaise(self) -> bool:
            return True

        @pyqtProperty(bool)
        def HasTrackList(self) -> bool:
            return False

        @pyqtProperty(str)
        def Identity(self) -> str:
            return "dopeIPTV"

        @pyqtProperty(str)
        def DesktopEntry(self) -> str:
            return "dopeiptv"

        @pyqtProperty("QStringList")
        def SupportedUriSchemes(self) -> list[str]:
            return []

        @pyqtProperty("QStringList")
        def SupportedMimeTypes(self) -> list[str]:
            return []

        @pyqtSlot()
        def Raise(self) -> None:
            service.command("raise")

        @pyqtSlot()
        def Quit(self) -> None:
            service.command("quit")

    @pyqtClassInfo("D-Bus Interface", PLAYER_IFACE)  # type: ignore[func-returns-value]
    class Player(QDBusAbstractAdaptor):
        Seeked = pyqtSignal("qlonglong")

        @pyqtProperty(str)
        def PlaybackStatus(self) -> str:
            return service.snap["status"]

        @pyqtProperty(str)
        def LoopStatus(self) -> str:
            return "None"

        @pyqtProperty(float)
        def Rate(self) -> float:
            return 1.0

        @pyqtProperty(float)
        def MinimumRate(self) -> float:
            return 1.0

        @pyqtProperty(float)
        def MaximumRate(self) -> float:
            return 1.0

        @pyqtProperty(bool)
        def Shuffle(self) -> bool:
            return False

        @pyqtProperty("QVariantMap")
        def Metadata(self) -> dict:
            return service.metadata()

        def _volume(self) -> float:
            return service.snap["volume"]

        def _set_volume(self, value: float) -> None:
            service.command("volume", max(0.0, min(1.0, float(value))))

        Volume = pyqtProperty(float, fget=_volume, fset=_set_volume)

        @pyqtProperty("qlonglong")
        def Position(self) -> int:
            return service.position_us()

        @pyqtProperty(bool)
        def CanGoNext(self) -> bool:
            return service.snap["can_next"]

        @pyqtProperty(bool)
        def CanGoPrevious(self) -> bool:
            return service.snap["can_prev"]

        @pyqtProperty(bool)
        def CanPlay(self) -> bool:
            return True

        @pyqtProperty(bool)
        def CanPause(self) -> bool:
            return service.snap["status"] != "Stopped"

        @pyqtProperty(bool)
        def CanSeek(self) -> bool:
            return service.snap["can_seek"]

        @pyqtProperty(bool)
        def CanControl(self) -> bool:
            return True

        @pyqtSlot()
        def Next(self) -> None:
            service.command("next")

        @pyqtSlot()
        def Previous(self) -> None:
            service.command("previous")

        @pyqtSlot()
        def Pause(self) -> None:
            service.command("pause")

        @pyqtSlot()
        def PlayPause(self) -> None:
            service.command("playpause")

        @pyqtSlot()
        def Stop(self) -> None:
            service.command("stop")

        @pyqtSlot()
        def Play(self) -> None:
            service.command("play")

        @pyqtSlot("qlonglong")
        def Seek(self, offset: int) -> None:
            if service.snap["can_seek"]:
                service.command("seek", offset / 1e6)

        @pyqtSlot(QDBusObjectPath, "qlonglong")
        def SetPosition(self, track: QDBusObjectPath, position: int) -> None:
            # The spec: ignore a position for a track that is no longer the
            # current one, and anything past its end.
            if (not service.snap["can_seek"] or position < 0
                    or track.path() != service.track_path()):
                return
            length = service.snap["length"]
            if length and position / 1e6 > length:
                return
            service.command("set_position", position / 1e6)

        @pyqtSlot(str)
        def OpenUri(self, _uri: str) -> None:
            return

    service.root_adaptor = Root(service)
    service.player_adaptor = Player(service)


class MprisService(QObject):
    """One media session on the session bus, fed by an MprisHost."""

    def __init__(self, host: MprisHost, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.host = host
        self.snap = _clean({})
        self.bus_name = ""
        self.root_adaptor: Any = None
        self.player_adaptor: Any = None
        self._bus: Any = None
        self._track_no = 0
        self._track_key = ""
        self._pos_seen = (0.0, 0.0)        # (position, monotonic time)
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.refresh)

    def start(self) -> bool:
        """Join the session bus. False - and nothing left behind - where
        there is no bus to join."""
        if not sys.platform.startswith("linux"):
            return False
        try:
            from PyQt6.QtDBus import QDBusConnection
        except Exception as e:
            log.info("mpris: QtDBus is not available (%s)", e)
            return False
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            log.info("mpris: no session bus - media controls off")
            return False
        _build_adaptors(self)
        if not bus.registerObject(
                OBJECT_PATH, self,
                QDBusConnection.RegisterOption.ExportAdaptors):
            log.info("mpris: could not export %s", OBJECT_PATH)
            return False
        # A second instance takes a name of its own, as the spec asks,
        # rather than leaving the first one's controls dead.
        for name in (BUS_NAME, f"{BUS_NAME}.instance{os.getpid()}"):
            if bus.registerService(name):
                self.bus_name = name
                break
        else:
            bus.unregisterObject(OBJECT_PATH)
            log.info("mpris: the bus name is taken - media controls off")
            return False
        self._bus = bus
        self.refresh()
        self._timer.start()
        log.info("mpris: media controls on the session bus as %s",
                 self.bus_name)
        return True

    def stop(self) -> None:
        self._timer.stop()
        if self._bus is not None:
            try:
                self._bus.unregisterService(self.bus_name)
                self._bus.unregisterObject(OBJECT_PATH)
            except Exception:
                pass
            self._bus = None

    # -- what the adaptors read ---------------------------------------------

    def track_path(self) -> str:
        if self.snap["status"] == "Stopped" or not self.snap["track"]:
            return NO_TRACK
        return f"/org/dopeiptv/track/{self._track_no}"

    def metadata(self) -> dict[str, Any]:
        from PyQt6.QtDBus import QDBusObjectPath
        s = self.snap
        md: dict[str, Any] = {"mpris:trackid": QDBusObjectPath(
            self.track_path())}
        if s["status"] == "Stopped":
            return md
        if s["title"]:
            md["xesam:title"] = s["title"]
        if s["artist"]:
            md["xesam:artist"] = _strings([s["artist"]])
        if s["album"]:
            md["xesam:album"] = s["album"]
        if s["art"]:
            md["mpris:artUrl"] = s["art"]
        if s["length"] > 0:
            md["mpris:length"] = _int64(int(s["length"] * 1e6))
        return md

    def position_us(self) -> int:
        if self.snap["status"] == "Stopped":
            return 0
        try:
            return int(max(0.0, float(self.host.mpris_position())) * 1e6)
        except Exception:
            return 0

    # -- keeping the desktop up to date -------------------------------------

    def command(self, name: str, arg: Any = None) -> None:
        log.debug("mpris: %s %s", name, "" if arg is None else arg)
        try:
            self.host.mpris_command(name, arg)
        except Exception as e:
            log.warning("mpris: %s failed: %s", name, e)
        # The desktop should see the result now, not on the next poll.
        QTimer.singleShot(150, self.refresh)

    def refresh(self) -> None:
        """Read the window's state; tell the desktop what changed."""
        if self._bus is None:
            return
        try:
            new = _clean(self.host.mpris_snapshot())
        except Exception as e:
            log.debug("mpris: snapshot failed: %s", e)
            return
        old = self.snap
        if new["track"] != self._track_key:
            self._track_key = new["track"]
            self._track_no += 1
        changed: dict[str, Any] = {}
        if new["status"] != old["status"]:
            changed["PlaybackStatus"] = new["status"]
            changed["CanPause"] = new["status"] != "Stopped"
        meta_keys = ("track", "title", "artist", "album", "art", "length")
        meta_changed = (any(new[k] != old[k] for k in meta_keys)
                        or new["status"] != old["status"])
        for key, prop in (("can_seek", "CanSeek"), ("can_next", "CanGoNext"),
                          ("can_prev", "CanGoPrevious"),
                          ("volume", "Volume")):
            if new[key] != old[key]:
                changed[prop] = new[key]
        self.snap = new
        if meta_changed:
            changed["Metadata"] = self.metadata()
        if changed:
            self._send_changed(changed)
        self._check_seek(old, new, meta_changed)

    def _check_seek(self, old: dict[str, Any], new: dict[str, Any],
                    new_track: bool) -> None:
        """A jump in position that playing time does not explain is a
        seek, and the desktop's progress bar only learns of it from Seeked."""
        now = time.monotonic()
        pos = new["position"]
        last_pos, last_at = self._pos_seen
        self._pos_seen = (pos, now)
        if new_track or new["status"] == "Stopped" or not new["can_seek"]:
            return
        expected = last_pos + (now - last_at
                               if old["status"] == "Playing" else 0.0)
        if abs(pos - expected) > 2.0 and self.player_adaptor is not None:
            try:
                self.player_adaptor.Seeked.emit(int(pos * 1e6))
            except Exception as e:
                log.debug("mpris: Seeked failed: %s", e)

    def _send_changed(self, changed: dict[str, Any]) -> None:
        from PyQt6.QtDBus import QDBusMessage
        msg = QDBusMessage.createSignal(
            OBJECT_PATH, "org.freedesktop.DBus.Properties",
            "PropertiesChanged")
        msg.setArguments([PLAYER_IFACE, changed, _strings([])])
        if not self._bus.send(msg):
            log.debug("mpris: PropertiesChanged was not sent")
