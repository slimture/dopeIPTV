"""MainWindow mixin: the desktop's media controls (MPRIS) and media keys.

The window's half of services/mpris.py: what is playing, and doing what the
desktop asks - GNOME's and KDE's media controls, headphone buttons, KDE
Connect. The keyboard's media keys also work in the window itself on every
system; a Linux desktop routes them through MPRIS instead, and a key that
arrives both ways is acted on once.
"""
from __future__ import annotations

import time
from typing import Any

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QKeySequence, QShortcut

from ..core.log import log

# The same action from the key and from MPRIS inside this window counts once.
_SAME_PRESS_S = 0.35
_MEDIA_KEYS = (
    (Qt.Key.Key_MediaTogglePlayPause, "playpause"),
    (Qt.Key.Key_MediaPlay, "play"),
    (Qt.Key.Key_MediaPause, "pause"),
    (Qt.Key.Key_MediaStop, "stop"),
    (Qt.Key.Key_MediaNext, "next"),
    (Qt.Key.Key_MediaPrevious, "previous"),
)


class _MprisMixin:
    """MainWindow mixin: MPRIS host and the media keys."""

    def _start_media_session(self) -> None:
        for key, name in _MEDIA_KEYS:
            sc = QShortcut(QKeySequence(key), self,
                           activated=lambda n=name: self._media_key(n))
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._mpris = None
        if self.settings.value("mpris", "true") != "true":
            return
        from ..services.mpris import MprisService
        svc = MprisService(self, self)
        if not svc.start():
            svc.deleteLater()
            return
        self._mpris = svc
        if self.player is not None:
            for signal in (self.player.paused_changed, self.player.stopped,
                           self.player.finished):
                signal.connect(self._mpris_refresh_soon)

    def _stop_media_session(self) -> None:
        svc = getattr(self, "_mpris", None)
        if svc is not None:
            svc.stop()
            self._mpris = None

    def _apply_media_session_setting(self) -> None:
        """Settings changed the switch: join or leave the bus now."""
        want = self.settings.value("mpris", "true") == "true"
        if want and getattr(self, "_mpris", None) is None:
            from ..services.mpris import MprisService
            svc = MprisService(self, self)
            if svc.start():
                self._mpris = svc
            else:
                svc.deleteLater()
        elif not want:
            self._stop_media_session()

    def _mpris_refresh_soon(self, *_args) -> None:
        svc = getattr(self, "_mpris", None)
        if svc is not None:
            QTimer.singleShot(0, svc.refresh)

    def _media_key(self, name: str) -> None:
        self.mpris_command(name)

    # -- MprisHost -------------------------------------------------------------

    def _media_active(self) -> bool:
        p = self.player
        return bool(p is not None and getattr(p, "current_url", None)
                    and self._playing_key is not None)

    def mpris_position(self) -> float:
        p = self.player
        if p is None or not self._media_active():
            return 0.0
        return p.playback_position()

    def mpris_snapshot(self) -> dict[str, Any]:
        p = self.player
        if p is None or not self._media_active():
            return {"status": "Stopped",
                    "volume": (p.vol.value() / 100.0) if p is not None else 0}
        last = getattr(self, "_last_playback", None) or {}
        kind = last.get("kind") or ""
        title = last.get("title") or ""
        artist = album = ""
        item = last.get("item") or {}
        if kind == "live":
            # The channel is what you tuned; what it is showing is the line
            # under it - as a radio's station and song.
            prog = None
            try:
                prog = self.xmltv.current_programme(item) if item else None
            except Exception:
                prog = None
            artist = (prog or {}).get("title") or ""
        elif kind == "episode":
            sctx = last.get("series_ctx") or {}
            artist = sctx.get("name") or sctx.get("title") or ""
        elif kind == "local":
            artist = item.get("_artist") or ""
            album = item.get("_album") or ""
        seekable = getattr(p, "_seek_mode", "vod") == "vod"
        length = p.playback_duration() if seekable else 0.0
        if kind == "episode":
            can_next = self._has_next_episode()
        else:
            can_next = kind in ("live", "local")
        return {
            "status": ("Paused" if getattr(p, "_paused", False)
                       else "Playing"),
            "track": f"{kind}:{self._playing_key}",
            "title": title,
            "artist": artist,
            "album": album,
            "art": self._media_art(last.get("icon_url")),
            "length": length,
            "position": p.playback_position() if length > 0 else 0.0,
            "can_seek": length > 0,
            "can_next": can_next,
            "can_prev": kind in ("live", "local"),
            "volume": p.vol.value() / 100.0,
        }

    def _media_art(self, url) -> str:
        """A local copy where the image cache has one - not every desktop
        fetches a picture from the network itself."""
        if not url:
            return ""
        try:
            path = self.logos.disk_file(url)
        except Exception:
            path = None
        if path is not None:
            return QUrl.fromLocalFile(str(path)).toString()
        return str(url)

    def mpris_command(self, name: str, arg: Any = None) -> None:
        now = time.monotonic()
        pressed = self.__dict__.setdefault("_media_pressed", {})
        if now - pressed.get(name, 0.0) < _SAME_PRESS_S:
            return
        pressed[name] = now
        p = self.player
        log.info("media control: %s", name)
        if name == "raise":
            if self.isMinimized():
                self.showNormal()
            self.show()
            self.raise_()
            self.activateWindow()
            return
        if name == "quit":
            self.close()
            return
        if p is None:
            return
        mv = self._multiview_active()
        if mv is not None and name in ("playpause", "play", "pause"):
            self._toggle_pause_shortcut()
            return
        active = self._media_active()
        paused = getattr(p, "_paused", False)
        kind = (getattr(self, "_last_playback", None) or {}).get("kind")
        if name == "playpause" or (name == "play" and (paused or not active)) \
                or (name == "pause" and active and not paused):
            # Toggling after a Stop plays the last title again, as the
            # player's own button does.
            p.toggle_pause()
        elif name == "stop":
            if active:
                # The button, not player.stop(): it is what also ends the
                # recording, the Trakt scrobble and the wake lock.
                p.stop_btn.click()
        elif name == "next":
            if kind == "episode":
                if self._has_next_episode():
                    self._play_next_episode()
            elif active:
                self._zap(1)
        elif name == "previous":
            if active and kind != "episode":
                self._zap(-1)
        elif name == "seek" and active:
            p._relative_seek(float(arg or 0.0))
        elif name == "set_position" and active:
            p._do_seek(max(0.0, float(arg or 0.0)))
        elif name == "volume":
            p._set_volume(int(round(max(0.0, min(1.0, float(arg))) * 100)))
        self._mpris_refresh_soon()
