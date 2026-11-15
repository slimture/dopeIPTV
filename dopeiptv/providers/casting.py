"""One front for every kind of receiver: Chromecast, DLNA and Kodi, and
those the window adds (AirPlay on macOS, services/airplay_macos.py).

The window casts through a single object. Each call goes to the manager the
chosen device belongs to - a DLNA renderer carries the " (DLNA)" suffix in
its name, a Kodi " (Kodi)", an added receiver knows its own entries - and
everything the window asks about the running cast (where it has got to, its
state, its volume) is answered by the one running it.

Devices known by their address stand beside those the searches find: the
ones added by hand (providers/cast_devices.py, kept in the settings) and,
while Nerd is on, what answers on the other networks Nerd > Network lists
(core/home_net.py, asked by providers/lan_scan.py beside the searches) -
each handed to its own manager, which lists it after every search of its
own and casts to it as to one it found.
"""
from __future__ import annotations

import threading
from typing import Any

from ..core import home_net
from ..core.log import log
from ..i18n import LocalizedError
from . import cast_devices
from .cast_feed import is_loopback
from .chromecast import ChromecastManager, _host_of
from .dlna import DlnaManager
from .kodi import KodiDevice, KodiManager


class CastHub:
    def __init__(self, chromecast: Any = None, dlna: Any = None,
                 kodi: Any = None, settings: Any = None) -> None:
        self.chromecast = chromecast if chromecast is not None \
            else ChromecastManager()
        self.dlna = dlna if dlna is not None else DlnaManager()
        self.kodi = kodi if kodi is not None else KodiManager()
        # Where the devices added by address are kept (cast_devices).
        self.settings = settings
        # What answered on the other networks at the last search.
        self.swept: list[dict] = []
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

    # -- devices known by their address -------------------------------------

    def stored(self) -> list[cast_devices.Added]:
        """The Chromecasts and TVs added by address."""
        return cast_devices.load(self.settings)

    def _hand_out(self, attach: bool = True) -> dict[str, list[str]]:
        """The devices added by address and those found on the other
        networks, each to its manager: {"cc", "dlna", "kodi"} -> their names.
        *attach* False: the Chromecasts wait for the scan about to start,
        which makes their device objects."""
        known = [d.as_found() for d in self.stored()] + list(self.swept)
        names: dict[str, list[str]] = {"cc": [], "dlna": [], "kodi": []}
        hosts = [d for d in known if d["kind"] == "chromecast"]
        tvs = [d["renderer"] for d in known if d["kind"] == "dlna" and d.get("renderer")]
        kodis = [KodiDevice(name=str(d.get("name") or d["host"]), host=str(d["host"]),
                            port=int(d.get("port") or 8080))
                 for d in known if d["kind"] == "kodi"]
        for key, mgr, method, items in (("cc", self.chromecast, "set_hosts", hosts),
                                        ("dlna", self.dlna, "set_extra", tvs),
                                        ("kodi", self.kodi, "set_extra", kodis)):
            give = getattr(mgr, method, None)
            if not callable(give) or (key == "cc" and not ChromecastManager.available()):
                continue
            try:
                names[key] = list(give(items) if attach or key != "cc"
                                  else give(items, attach=False))
            except Exception as e:
                log.info("cast: %s by address not listed (%s)", key, e)
        return names

    def _listed(self, key: str) -> set[str] | None:
        """The names a manager lists now, or None when it does not say."""
        if key == "cc":
            devices = getattr(self.chromecast, "devices", None)
            return {c.name for c in list(devices)} if isinstance(devices, list) else None
        table = getattr(self.dlna, "renderers", None) if key == "dlna" \
            else getattr(self.kodi, "devices", None)
        return set(table.copy()) if isinstance(table, dict) else None

    def _label(self, dev: dict) -> str:
        """The name a device known by its address has in the list now (a
        copy of each list is read: a scan may be changing it)."""
        if dev["kind"] == "dlna":
            loc = getattr(dev.get("renderer"), "location", "") or dev.get("location", "")
            renderers = list((getattr(self.dlna, "renderers", None) or {}).items())
            return next((n for n, r in renderers if r.location == loc), "")
        if dev["kind"] == "chromecast":
            devices = list(getattr(self.chromecast, "devices", None) or [])
            return next((c.name for c in devices if _host_of(c) == dev["host"]), "")
        return ""

    def added_key(self, name: str | None) -> str:
        """The key of the device added by address that is *name* in the
        list, or "" for any other."""
        if not name:
            return ""
        for d in self.stored():
            if self._label(d.as_found()) == name:
                return d.key
        return ""

    def is_added(self, name: str | None) -> bool:
        return bool(self.added_key(name))

    def add_by_address(self, text: str, kind: str = "auto") -> str:
        """(worker thread) Find out what is at the address typed, keep it,
        and return its name in the list. A Kodi is kept with the other Kodis
        (its login is asked there). LocalizedError when nothing of that
        kind answers, or for a Kodi that wants a login."""
        got = cast_devices.detect(text, kind)
        if got["kind"] == "kodi":
            if got.get("login"):
                raise LocalizedError("cast_addr_kodi_login")
            return str(self.kodi.add(got["host"], int(got["port"]), name=got["name"]))
        if got["kind"] == "chromecast" and not ChromecastManager.available():
            raise LocalizedError("cast_addr_no_chromecast")
        dev = cast_devices.Added.from_found(got)
        if dev is None:
            raise LocalizedError("cast_addr_nothing", address=text.strip())
        cast_devices.remember(self.settings, dev)
        self._hand_out()
        return self._label(dev.as_found()) or dev.name

    def forget_added(self, key: str) -> bool:
        """Stop keeping a device added by address (by its key): it leaves
        the list unless a search finds it again."""
        if not cast_devices.forget(self.settings, key):
            return False
        # Handed out without it, its manager lists it no more.
        self._hand_out()
        log.info("cast: a device added by address is forgotten")
        return True

    def scan(self) -> list[str]:
        """Chromecasts, DLNA renderers and Kodis, looked for side by side -
        each search takes seconds, and none waits for the others. Those
        known by their address are listed with them, and (Nerd) the other
        networks are asked at the same time."""
        hosts = home_net.configured()
        if not hosts:
            self.swept = []             # Nerd off, or the list emptied
        self._hand_out(attach=False)
        found: dict[str, list[str]] = {"cc": [], "dlna": [], "kodi": []}
        far: dict[str, list[dict] | None] = {"got": None}

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

        def other_networks() -> None:
            if not hosts:
                return
            from . import lan_scan
            try:
                far["got"] = lan_scan.scan_cast(hosts)
            except Exception as e:
                log.info("cast: asking the other networks failed (%s)", e)

        threads = [threading.Thread(target=fn, daemon=True)
                   for fn in (chromecasts, renderers, kodis, other_networks)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        if far["got"] is not None:
            self.swept = list(far["got"])
            for key, names in self._hand_out().items():
                found[key] += [n for n in names if n not in found[key]]
                # One the last sweep found and this one did not is gone.
                still = self._listed(key)
                if still is not None:
                    found[key] = [n for n in found[key] if n in still]
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
        if not getattr(mgr, "TAKES_FEED", False):
            # A TV or a Kodi plays an address as it is: the pieces of an
            # archive or the rewind buffer are a converter's to read - and
            # the buffer's own address is this computer's, which no TV
            # reaches.
            feed = kwargs.pop("feed", None)
            # source: a keyword, or the seventh after the title (the
            # managers' order: codecs, audio, subs, start, duration,
            # settle, source, ...).
            source = kwargs.get("source", args[6] if len(args) > 6 else None)
            if feed is not None and (
                    is_loopback(url) or is_loopback(str(source or ""))):
                log.info("cast: %s cannot be fed the rewind buffer - its "
                         "address is this computer's", device_name)
                raise LocalizedError("cast_err_no_address")
        return mgr.cast(device_name, url, title, *args, **kwargs)

    def waiting_for_route(self) -> bool:
        """AirPlay: the Apple TV not picked yet, or not playing the stream
        yet (what the cast strip says meanwhile)."""
        fn = getattr(self._current, "waiting_for_route", None)
        return bool(callable(fn) and fn())

    def takes_feed(self, name: str | None) -> bool:
        """Whether *name*'s kind of receiver reads a cast_feed.Feed (the
        converter here does), rather than an address as it is."""
        return bool(name) and bool(getattr(self._for(name), "TAKES_FEED",
                                           False))
