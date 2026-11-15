"""One front for every kind of receiver: Chromecast, DLNA and Kodi, and
those the window adds (AirPlay on macOS, services/airplay_macos.py).

The window casts through a single object. Each call goes to the manager the
chosen device belongs to - a DLNA renderer carries the " (DLNA)" suffix in
its name, a Kodi " (Kodi)", an added receiver knows its own entries - and
everything the window asks about the running cast (where it has got to, its
state, its volume) is answered by the one running it.
"""
from __future__ import annotations

import threading
from typing import Any

from ..core.log import log
from .chromecast import ChromecastManager
from .dlna import DlnaManager
from .kodi import KodiManager


class CastHub:
    def __init__(self, chromecast: Any = None, dlna: Any = None,
                 kodi: Any = None) -> None:
        self.chromecast = chromecast if chromecast is not None \
            else ChromecastManager()
        self.dlna = dlna if dlna is not None else DlnaManager()
        self.kodi = kodi if kodi is not None else KodiManager()
        # Receivers added from above (add_receiver): each answers knows(),
        # entries() and what every manager answers.
        self.added: list[Any] = []
        self._current: Any = self.chromecast

    def add_receiver(self, mgr: Any) -> None:
        self.added.append(mgr)

    def _managers(self) -> tuple[Any, ...]:
        return (self.chromecast, self.dlna, self.kodi, *self.added)

    def is_dlna(self, name: str | None) -> bool:
        return bool(name) and self.dlna.knows(name)

    def is_kodi(self, name: str | None) -> bool:
        return bool(name) and self.kodi.knows(name)

    def is_airplay(self, name: str | None) -> bool:
        return bool(name) and any(getattr(m, "KIND", "") == "airplay"
                                  and m.knows(name) for m in self.added)

    def plays_as_is(self, name: str | None) -> bool:
        """A device that fetches the stream itself and plays it as it is -
        a DLNA TV or a Kodi: no converter, so no track or picture choice."""
        return self.is_dlna(name) or self.is_kodi(name)

    def ended_elsewhere(self) -> bool:
        """The running cast ended on the device itself (its own remote, or
        another item took over) - only a Kodi says so."""
        return bool(getattr(self._current, "gone", False))

    def _for(self, name: str) -> Any:
        for mgr in self.added:
            if mgr.knows(name):
                return mgr
        if self.is_kodi(name):
            return self.kodi
        return self.dlna if self.is_dlna(name) else self.chromecast

    # -- the running cast, from whichever manager runs it ---------------------

    @property
    def active(self) -> Any:
        return self._current.active

    @property
    def state(self) -> str:
        return self._current.state

    @property
    def duration(self) -> float:
        return self._current.duration

    @property
    def bridge(self) -> Any:
        return self._current.bridge

    def position(self) -> float:
        return self._current.position()

    def bridged(self) -> bool:
        return self._current.bridged()

    def volume(self) -> tuple[float, bool]:
        return self._current.volume()

    def set_volume(self, level: float) -> None:
        self._current.set_volume(level)

    def set_muted(self, muted: bool) -> None:
        self._current.set_muted(muted)

    def pause(self) -> None:
        self._current.pause()

    def resume(self) -> None:
        self._current.resume()

    def seek(self, seconds: float) -> None:
        self._current.seek(seconds)

    def stop(self) -> None:
        self._current.stop()

    def shutdown(self) -> None:
        # The one with a cast running goes first. On app close this races
        # the process exit, and tearing down Chromecast discovery must not
        # spend the time the TV's Stop needs.
        managers = sorted(self._managers(),
                          key=lambda m: getattr(m, "active", None) is None)
        for mgr in managers:
            try:
                mgr.shutdown()
            except Exception as e:
                log.info("cast: shutting down %s failed (%s)",
                         type(mgr).__name__, e)

    # -- finding devices and casting to one -----------------------------------

    @staticmethod
    def chromecast_available() -> bool:
        return ChromecastManager.available()

    def scan(self) -> list[str]:
        """Chromecasts, DLNA renderers and Kodis, looked for side by side -
        each search takes seconds, and none waits for the others."""
        found: dict[str, list[str]] = {"cc": [], "dlna": [], "kodi": []}

        def chromecasts() -> None:
            if not ChromecastManager.available():
                return
            try:
                found["cc"] = self.chromecast.scan()
            except Exception as e:
                log.info("cast: looking for Chromecasts failed (%s)", e)

        def renderers() -> None:
            try:
                found["dlna"] = self.dlna.scan()
            except Exception as e:
                log.info("dlna: looking for TVs failed (%s)", e)

        def kodis() -> None:
            try:
                found["kodi"] = self.kodi.scan()
            except Exception as e:
                log.info("kodi: looking for Kodi failed (%s)", e)

        threads = [threading.Thread(target=fn, daemon=True)
                   for fn in (chromecasts, renderers, kodis)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        added = [n for m in self.added for n in m.entries()]
        return added + found["cc"] + found["dlna"] + found["kodi"]

    def cast(self, device_name: str, url: str, title: str,
             *args: Any, **kwargs: Any) -> str:
        mgr = self._for(device_name)
        for other in self._managers():
            if other is mgr or getattr(other, "active", None) is None:
                continue
            # One cast at a time: moving it to another kind of device ends
            # it on the old one, or two TVs would play the same channel.
            log.info("cast: moving to %s - stopping the cast on the other "
                     "kind of device", device_name)
            try:
                other.stop()
            except Exception as e:
                log.info("cast: stopping the old cast failed (%s)", e)
        self._current = mgr
        return mgr.cast(device_name, url, title, *args, **kwargs)
