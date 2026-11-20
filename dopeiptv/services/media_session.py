"""The system's media controls: one session per system, fed by the window.

What is playing is shown where the operating system shows it, and what the
system asks - play, pause, the next channel, a seek - comes back to the
window:

- Linux: MPRIS on the session bus (services/mpris.py) - GNOME's and KDE's
  media controls, the media keys, headphone buttons, KDE Connect.
- macOS: Now Playing (services/media_macos.py) - Control Center, the lock
  screen, the menu bar, the keyboard's media keys, AirPods.
- Windows: the System Media Transport Controls (services/media_windows.py) -
  the media box by the volume, the media keys, the lock screen.

The window is one MediaHost whatever the system; its methods keep the names
MPRIS gave them first. A backend whose package is missing, or that fails,
says so once in the debug log and the app runs as it does without one.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time
from typing import Any, Protocol, cast

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal

from ..core.log import log

# How often the window is asked what is playing; a change the window
# reports at once (refresh) does not wait for it.
POLL_MS = 1000
# A move of the position that playing time does not explain is a seek.
SEEK_JUMP_S = 2.0
# The system's skip buttons go as far as the player's own (back 10, on 30).
SKIP_BACK_S = 10.0
SKIP_FWD_S = 30.0
# A picture larger than this is not handed to the system as artwork.
ART_MAX_BYTES = 8 * 1024 * 1024

# What the session shows changes with these ("meta"), with the status, and
# with what may be asked ("caps").
_META = ("track", "title", "artist", "album", "art", "length", "live", "audio")
_CAPS = ("can_seek", "can_next", "can_prev")

# Per system: what a backend imports, for supported() and the self-check.
_NEEDS = {
    "darwin": ("MediaPlayer", "AppKit", "Foundation"),
    "win32": ("winrt.windows.media.playback", "winrt.windows.media",
              "winrt.windows.storage.streams"),
}


class MediaHost(Protocol):
    def mpris_snapshot(self) -> dict[str, Any]:
        """status ("Playing"/"Paused"/"Stopped"), track (any id that changes
        per title), title, artist, album, art (URL), length and position (s),
        can_seek, can_next, can_prev, volume (0-1), live (a channel as it
        airs), audio (music rather than a picture)."""

    def mpris_position(self) -> float:
        """Seconds into what is playing, read now."""

    def mpris_command(self, name: str, arg: Any = None) -> None:
        """play, pause, playpause, stop, next, previous, seek (+/- s),
        set_position (s), volume (0-1), raise, quit."""


def clean_snapshot(snap: dict[str, Any]) -> dict[str, Any]:
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
        "live": bool(snap.get("live")),
        "audio": bool(snap.get("audio")),
    }


def changes(old: dict[str, Any], new: dict[str, Any]) -> set[str]:
    """What differs between two clean snapshots: "meta", "status", "caps"."""
    what: set[str] = set()
    if any(new[k] != old[k] for k in _META):
        what.add("meta")
    if new["status"] != old["status"]:
        what.add("status")
    if any(new[k] != old[k] for k in _CAPS):
        what.add("caps")
    return what


_said: set[str] = set()


def say_once(key: str, msg: str, *args: Any) -> None:
    """A debug line the first time only: a backend that cannot work here
    would otherwise say so every second."""
    if key in _said:
        return
    _said.add(key)
    log.debug(msg, *args)


def local_art(art: str) -> str | None:
    """The file behind *art* when it is one on this computer (a file:// URL
    or a path) and small enough to hand over; None for a web address - the
    system is never sent off to fetch a provider's picture itself."""
    if not art:
        return None
    if art.startswith("file:"):
        path = os.path.normpath(QUrl(art).toLocalFile())
    elif os.path.isabs(art):
        path = art
    else:
        return None
    # A row's picture is its source's word too: only a file where the app's
    # own pictures are (its cache, a picon folder: core/picture_files.py).
    from ..core import picture_files
    if not picture_files.trusted(path):
        return None
    try:
        size = os.path.getsize(path)
    except (OSError, ValueError):
        return None
    return path if 0 < size <= ART_MAX_BYTES else None


def carries_login(url: str) -> bool:
    """Whether a web address holds a login or a token, as the log's own
    masking finds them (core/log.redact_url: user:password@, an Xtream
    path, token/api_key/X-Plex-Token... in the query, an account's own
    values)."""
    from ..core.log import redact_url
    return redact_url(url) != url


def art_for_bus(art: str) -> str:
    """*art* as it may be said on the session bus (MPRIS), where every
    program of the desktop reads it and KDE Connect passes it on to a
    phone: a file on this computer, or a web address with no login in it.
    A media server's pictures carry the account's token (Plex's
    X-Plex-Token) - "" for such an address: the window fetches the picture
    into the image cache, and the copy there is said instead."""
    if not art:
        return ""
    if art.startswith("file:"):
        return art
    if os.path.isabs(art):
        return QUrl.fromLocalFile(art).toString()
    if art.startswith(("http://", "https://")) and not carries_login(art):
        return art
    return ""


def supported() -> bool:
    """Whether this system has a session to offer: MPRIS on Linux, or the
    backend's packages where they are needed."""
    if sys.platform.startswith("linux"):
        return True
    needs = _NEEDS.get(sys.platform)
    if not needs:
        return False
    try:
        return all(importlib.util.find_spec(n) is not None for n in needs)
    except (ImportError, ValueError):
        return False


def bundle_problem() -> str | None:
    """Why this build could not show the system's media controls, or None.
    For --self-check: imports what the backend uses and looks up its
    classes, starting nothing."""
    try:
        if sys.platform == "darwin":
            from . import media_macos
            media_macos.check()
        elif sys.platform == "win32":
            from . import media_windows
            media_windows.check()
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    return None


def create_session(host: MediaHost, parent: QObject | None = None) -> Any:
    """The session for this system, not yet started; None where there is
    none. start() says whether it is on."""
    if sys.platform.startswith("linux"):
        from .mpris import MprisService
        return MprisService(host, parent)
    if sys.platform == "darwin":
        from .media_macos import NowPlayingSession
        return NowPlayingSession(host, parent)
    if sys.platform == "win32":
        from .media_windows import SmtcSession
        return SmtcSession(host, parent)
    return None


class PolledSession(QObject):
    """What the macOS and Windows sessions share: the window asked what is
    playing every second (and at once when it says so), the changes handed
    to the backend, and what the system asks carried out on the GUI thread.

    A backend implements _open (True when on), _close and _show; a system
    callback, on whatever thread it arrives, calls ask()."""

    #: The session's name in the log.
    NAME = "media session"
    # Always queued: the window acts on its own thread, after the system's
    # callback has returned.
    _asked = pyqtSignal(str, object)

    def __init__(self, host: MediaHost, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.host = host
        self.snap = clean_snapshot({})
        self.active = False
        self._pos_seen = (0.0, 0.0)        # (position, monotonic time)
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.refresh)
        # Through Any: PyQt6's stubs differ between versions on connect's type.
        cast(Any, self._asked).connect(self._carry_out,
                                       Qt.ConnectionType.QueuedConnection)

    # -- what a backend implements -------------------------------------------

    def _open(self) -> bool:
        raise NotImplementedError

    def _close(self) -> None:
        """Undo _open; also called after an _open that failed half-way."""

    def _show(self, new: dict[str, Any], old: dict[str, Any],
              what: set[str]) -> None:
        """Tell the system; *what* names what changed ("meta", "status",
        "caps", "position" for a seek) and is empty on a plain poll."""

    # -- life ------------------------------------------------------------------

    def start(self) -> bool:
        """On, or False - with nothing left behind - where the backend's
        package is missing or the system refuses."""
        if self.active:
            return True
        # From nothing: the first refresh tells the system everything, also
        # when the switch in Settings turns the session on again.
        self.snap = clean_snapshot({})
        self._pos_seen = (0.0, 0.0)
        try:
            ok = bool(self._open())
        except Exception as e:
            say_once(f"{self.NAME}:open", "%s: media controls off (%s: %s)",
                     self.NAME, type(e).__name__, e)
            ok = False
        if not ok:
            self._close_quietly()
            return False
        self.active = True
        self.refresh()
        self._timer.start()
        log.info("%s: media controls on", self.NAME)
        return True

    def stop(self) -> None:
        self._timer.stop()
        if self.active:
            self.active = False
            self._close_quietly()

    def _close_quietly(self) -> None:
        try:
            self._close()
        except Exception as e:
            log.debug("%s: closing: %s", self.NAME, e)

    # -- what the system asks --------------------------------------------------

    def ask(self, name: str, arg: Any = None) -> None:
        """What the system asked, from any thread; carried out on the GUI
        thread. Never raises into the system's callback."""
        try:
            if self.active:
                self._asked.emit(name, arg)
        except RuntimeError:
            pass                       # the session is gone

    def _carry_out(self, name: str, arg: Any) -> None:
        if not self.active:
            return
        s = self.snap
        if name in ("seek", "set_position"):
            # As MPRIS: no seeking what cannot seek (a channel, whose skip
            # would move through its rewind buffer), nor past the end.
            try:
                value = float(arg)
            except (TypeError, ValueError):
                return
            if not s["can_seek"]:
                return
            if name == "set_position" and (
                    value < 0 or (s["length"] and value > s["length"])):
                return
            arg = value
        log.debug("%s: %s %s", self.NAME, name, "" if arg is None else arg)
        try:
            self.host.mpris_command(name, arg)
        except Exception as e:
            log.warning("%s: %s failed: %s", self.NAME, name, e)
        # The system should see the result now, not on the next poll.
        QTimer.singleShot(150, self.refresh)

    # -- keeping the system up to date ----------------------------------------

    def refresh(self) -> None:
        """Read the window's state; tell the system what changed."""
        if not self.active:
            return
        try:
            new = clean_snapshot(self.host.mpris_snapshot())
        except Exception as e:
            log.debug("%s: snapshot failed: %s", self.NAME, e)
            return
        old = self.snap
        self.snap = new
        what = changes(old, new)
        if self._seeked(old, new, "meta" in what):
            what.add("position")
        try:
            self._show(new, old, what)
        except Exception as e:
            say_once(f"{self.NAME}:show", "%s: could not show what plays "
                     "(%s: %s)", self.NAME, type(e).__name__, e)

    def _seeked(self, old: dict[str, Any], new: dict[str, Any],
                new_track: bool) -> bool:
        """A jump in position that playing time does not explain: the
        system's progress bar learns of a seek only when told."""
        now = time.monotonic()
        pos = new["position"]
        last_pos, last_at = self._pos_seen
        self._pos_seen = (pos, now)
        if new_track or new["status"] == "Stopped" or not new["can_seek"]:
            return False
        expected = last_pos + (now - last_at
                               if old["status"] == "Playing" else 0.0)
        return abs(pos - expected) > SEEK_JUMP_S
