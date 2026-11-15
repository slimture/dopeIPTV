"""Kodi as a cast target, over its JSON-RPC API (HTTP).

Kodi is told the stream's address with Player.Open and fetches it itself,
as a Chromecast or a DLNA TV does; the source's user agent and headers go
along as Kodi's own "|User-Agent=..." options. Pause, stop, seek (for a
film), volume, and where it has got to are asked of it the same way. It is
found on the network by mDNS (Kodi announces _xbmc-jsonrpc-h._tcp), or
added by hand with its address, and remembered with the web server's login
(the "kodi_devices" setting).

KodiManager answers to the same calls as DlnaManager and ChromecastManager;
CastHub (providers/casting.py) puts it beside them. Credentials are never
logged: a log line names a Kodi by its name and host:port only.
"""
from __future__ import annotations

import ipaddress
import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

from .. import APP_NAME, VERSION
from ..core._lazy_requests import requests
from ..core.log import log, redact_url
from ..i18n import LocalizedError
from . import mdns
from .cast_bridge import lan_address
from .dlna import FileShare, _NoBridge

# A Kodi's name in the device list says what it is, and keeps it apart from
# a Chromecast or a TV of the same name.
SUFFIX = " (Kodi)"
SERVICE = "_xbmc-jsonrpc-h._tcp.local"
DEFAULT_PORT = 8080
# Kodi's own user name for its web server when only a password is set.
DEFAULT_USER = "kodi"
SETTINGS_KEY = "kodi_devices"
UA = f"{APP_NAME}/{VERSION}"
POLL_S = 1.5
# How long Kodi gets to start playing before the cast counts as refused.
START_WAIT_S = 20.0
# Polls without a player, after it played, before the cast counts as over:
# Kodi drops its player for a moment between two items.
GONE_AFTER = 2
# Polls without any answer at all before Kodi counts as gone (switched off).
UNREACHABLE_AFTER = 10
_INVALID_PARAMS = -32602


class KodiError(Exception):
    """A JSON-RPC error from Kodi: its code and its own words."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"{code} {message}".strip())
        self.code = code


class KodiAuthError(KodiError):
    """Kodi's web server wants a user name and password (HTTP 401)."""

    def __init__(self) -> None:
        super().__init__(401, "unauthorized")


@dataclass
class KodiDevice:
    name: str
    host: str
    port: int = DEFAULT_PORT
    user: str = ""
    password: str = ""
    manual: bool = False

    def __repr__(self) -> str:          # never the login
        return f"KodiDevice({self.name!r}, {self.address})"

    @property
    def address(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}"

    @property
    def endpoint(self) -> str:
        return f"http://{self.address}/jsonrpc"

    @property
    def login(self) -> tuple[str, str] | None:
        if not (self.user or self.password):
            return None
        return (self.user or DEFAULT_USER, self.password)

    @property
    def label(self) -> str:
        return self.name + SUFFIX

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "host": self.host, "port": self.port,
                "user": self.user, "password": self.password}


# -- what goes over the wire -----------------------------------------------------

def parse_address(text: str) -> tuple[str, int] | None:
    """(host, port) from what a person types: "192.168.1.20",
    "192.168.1.20:8080", "kodi.local:8080", "http://host:8080/jsonrpc",
    "[fe80::1]:8080". None for anything that is not an address."""
    text = (text or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = "http://" + text
    try:
        parts = urlsplit(text)
        host = parts.hostname or ""
        port = parts.port or DEFAULT_PORT
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host or "@" in parts.netloc:
        return None
    if not all(c.isalnum() or c in ".-:_%" for c in host):
        return None
    return host, port


def rpc_request(method: str, params: dict[str, Any] | None = None,
                ident: int = 1) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": ident}
    if params is not None:
        body["params"] = params
    return body


def time_obj(seconds: float) -> dict[str, int]:
    """Kodi's Player.Position.Time for *seconds*."""
    ms = max(0, int(round(float(seconds) * 1000)))
    return {"hours": ms // 3_600_000, "minutes": ms // 60_000 % 60,
            "seconds": ms // 1000 % 60, "milliseconds": ms % 1000}


def seconds_of(obj: Any) -> float:
    """Seconds from Kodi's {"hours", "minutes", "seconds", "milliseconds"}."""
    if not isinstance(obj, dict):
        return 0.0
    try:
        return (int(obj.get("hours") or 0) * 3600 + int(obj.get("minutes") or 0) * 60
                + int(obj.get("seconds") or 0) + int(obj.get("milliseconds") or 0) / 1000)
    except (TypeError, ValueError):
        return 0.0


def with_options(url: str, user_agent: str = "",
                 headers: dict[str, str] | None = None) -> str:
    """The stream's address with Kodi's own options after a "|": the user
    agent and any extra headers the source plays with. A local path or an
    address that already carries options is left as it is."""
    if "://" not in url or "|" in url:
        return url
    opts: list[tuple[str, str]] = []
    if user_agent:
        opts.append(("User-Agent", user_agent))
    for k, v in (headers or {}).items():
        if k and k.lower() != "user-agent" and v:
            opts.append((k, v))
    if not opts:
        return url
    return url + "|" + "&".join(f"{quote(k, safe='-')}={quote(v, safe='')}"
                                for k, v in opts)


def is_loopback_url(url: str) -> bool:
    """An address only this computer can open: the app's own rewind buffer
    and share server live there, and no Kodi elsewhere can reach them."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    if host in ("localhost", "localhost.localdomain") or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def load_devices(settings: Any) -> list[KodiDevice]:
    """The Kodis added by hand (or given a login), from the settings."""
    try:
        raw = json.loads(settings.value(SETTINGS_KEY, "") or "[]")
    except (TypeError, ValueError):
        return []
    out: list[KodiDevice] = []
    for d in raw if isinstance(raw, list) else []:
        if not isinstance(d, dict) or not d.get("host"):
            continue
        try:
            port = int(d.get("port") or DEFAULT_PORT)
        except (TypeError, ValueError):
            port = DEFAULT_PORT
        out.append(KodiDevice(name=str(d.get("name") or d["host"])[:80],
                              host=str(d["host"]), port=port,
                              user=str(d.get("user") or ""),
                              password=str(d.get("password") or ""),
                              manual=True))
    return out


def save_devices(settings: Any, devices: list[KodiDevice]) -> None:
    settings.setValue(SETTINGS_KEY, json.dumps([d.to_dict() for d in devices]))


class KodiRpc:
    """One Kodi's JSON-RPC endpoint."""

    def __init__(self, device: KodiDevice, session: Any = None) -> None:
        self.device = device
        self.session = session
        self._ident = 0

    def call(self, method: str, params: dict[str, Any] | None = None,
             timeout: float = 6.0) -> Any:
        self._ident += 1
        http = self.session or requests
        r = http.post(self.device.endpoint,
                      data=json.dumps(rpc_request(method, params, self._ident)),
                      headers={"Content-Type": "application/json",
                               "User-Agent": UA},
                      auth=self.device.login, timeout=timeout)
        if r.status_code == 401:
            raise KodiAuthError()
        if r.status_code != 200:
            raise KodiError(r.status_code, f"HTTP {r.status_code}")
        try:
            body = r.json()
        except ValueError as e:
            raise KodiError(-32700, "not a JSON-RPC answer") from e
        if not isinstance(body, dict):
            raise KodiError(-32700, "not a JSON-RPC answer")
        err = body.get("error")
        if isinstance(err, dict):
            raise KodiError(int(err.get("code") or 0), str(err.get("message") or ""))
        return body.get("result")


def describe(device: KodiDevice, session: Any = None) -> str:
    """Kodi's own name for itself, after checking it answers at all.
    Raises KodiAuthError, KodiError or a requests error."""
    rpc = KodiRpc(device, session)
    if rpc.call("JSONRPC.Ping", timeout=4) != "pong":
        raise KodiError(-32601, "not Kodi")
    try:
        got = rpc.call("XBMC.GetInfoLabels",
                       {"labels": ["System.FriendlyName"]}, timeout=4)
        name = str((got or {}).get("System.FriendlyName") or "").strip()
    except KodiError:
        name = ""
    return name[:80] or device.host


def _clean_name(name: str) -> str:
    name = "".join(c for c in (name or "") if c.isprintable()).strip()
    return name[:80] or "Kodi"


class KodiManager:
    """Finds Kodis and casts to one, the way DlnaManager does."""

    def __init__(self, settings: Any = None, session: Any = None) -> None:
        self.settings = settings
        self.session = session
        self.devices: dict[str, KodiDevice] = {}
        # Found by their address on another part of the network (set_extra).
        self.extra: list[KodiDevice] = []
        self.active: KodiDevice | None = None
        self.bridge = _NoBridge()
        self.files = FileShare()
        self.state = ""
        self.duration = 0.0
        self.last_position = 0.0
        self.position_offset = 0.0
        self.position_at = 0.0
        # The cast ended on Kodi itself - its remote stopped it, or another
        # item took over - and the window takes the strip down.
        self.gone = False
        self._volume: tuple[float, bool] = (0.0, False)
        self._played = False
        self._player_id = 1
        self._file = ""
        self._dvr = False
        self._poll_gen = 0
        self._lock = threading.RLock()

    @staticmethod
    def available() -> bool:
        return True

    def knows(self, name: str) -> bool:
        return name in self.devices or name.endswith(SUFFIX)

    # -- the devices -------------------------------------------------------------

    def remembered(self) -> list[KodiDevice]:
        return load_devices(self.settings) if self.settings is not None else []

    def _named(self, devices: list[KodiDevice]) -> dict[str, KodiDevice]:
        out: dict[str, KodiDevice] = {}
        for d in devices:
            label, n = d.label, 2
            while label in out:
                label = f"{d.name} #{n}{SUFFIX}"
                n += 1
            out[label] = d
        return out

    def _merge(self, found: list[KodiDevice]) -> dict[str, KodiDevice]:
        """The Kodis remembered here and those that answered, once each:
        one remembered at the same address keeps its login and its name."""
        mine = self.remembered()
        at = {(d.host.lower(), d.port): d for d in mine}
        rest: dict[tuple[str, int], KodiDevice] = {}
        for d in list(found) + list(self.extra):
            where = (d.host.lower(), d.port)
            if where not in at:
                rest.setdefault(where, d)
        return self._named(mine + sorted(rest.values(),
                                         key=lambda d: (d.name.lower(), d.address)))

    def set_extra(self, devices: list[KodiDevice]) -> list[str]:
        """Kodis found by their address on another part of the network:
        listed with the others from now on (not remembered). Their names."""
        with self._lock:
            before = {(d.host.lower(), d.port) for d in self.extra}
            self.extra = list(devices)
            # One no longer found there goes - unless it is the one casting.
            keep = [d for d in self.devices.values()
                    if (d.host.lower(), d.port) not in before or d is self.active]
            merged = self._merge(keep)
            self.devices = merged
            wanted = {(d.host.lower(), d.port) for d in devices}
            return [k for k, d in merged.items() if (d.host.lower(), d.port) in wanted]

    def scan(self, timeout: float = 2.5) -> list[str]:
        found: list[KodiDevice] = []
        for inst in mdns.browse(SERVICE, timeout, iface=lan_address()):
            if inst.port <= 0:
                continue
            found.append(KodiDevice(name=_clean_name(inst.name), host=inst.host,
                                    port=inst.port))
        devices = self._merge(found)
        with self._lock:
            self.devices = devices
            if self.active is not None:
                same = next((d for d in devices.values()
                             if d.address == self.active.address), None)
                self.active = same or self.active
        log.info("kodi: %d on the network%s", len(devices),
                 (": " + ", ".join(sorted(devices))) if devices else "")
        return sorted(devices)

    def add(self, host: str, port: int, user: str = "", password: str = "",
            name: str = "") -> str:
        """Remember a Kodi (or its new login) and return its name in the
        device list. Checks that it answers first: LocalizedError when it
        wants a login or does not answer."""
        dev = KodiDevice(name="", host=host, port=port, user=user,
                         password=password, manual=True)
        try:
            dev.name = _clean_name(name or describe(dev, self.session))
        except KodiAuthError as e:
            raise LocalizedError("kodi_needs_login") from e
        except (KodiError, requests.RequestException) as e:
            log.info("kodi: nothing answered at %s (%s)", dev.address,
                     type(e).__name__)
            raise LocalizedError("kodi_no_answer", address=dev.address) from e
        mine = [d for d in self.remembered()
                if (d.host.lower(), d.port) != (host.lower(), port)]
        mine.append(dev)
        if self.settings is not None:
            save_devices(self.settings, mine)
        with self._lock:
            self.devices = {k: v for k, v in self.devices.items()
                            if v.address != dev.address}
            label, n = dev.label, 2
            while label in self.devices:
                label = f"{dev.name} #{n}{SUFFIX}"
                n += 1
            self.devices[label] = dev
        log.info("kodi: %s at %s remembered", dev.name, dev.address)
        return label

    def forget(self, label: str) -> None:
        dev = self.devices.get(label)
        if dev is None or self.settings is None:
            return
        save_devices(self.settings, [d for d in self.remembered()
                                     if d.address != dev.address])
        with self._lock:
            self.devices.pop(label, None)
        log.info("kodi: %s forgotten", dev.name)

    def device(self, label: str) -> KodiDevice | None:
        dev = self.devices.get(label)
        if dev is None:
            dev = self._merge([]).get(label)
        return dev

    # -- talking to the one casting ----------------------------------------------

    def _rpc(self) -> KodiRpc | None:
        dev = self.active
        return KodiRpc(dev, self.session) if dev is not None else None

    def _call(self, method: str, params: dict[str, Any] | None = None,
              timeout: float = 6.0) -> Any:
        rpc = self._rpc()
        if rpc is None:
            return None
        return rpc.call(method, params, timeout)

    def position(self) -> float:
        at = self.position_offset + self.last_position
        if self.position_at and self.state.startswith("PLAYING"):
            at += max(0.0, time.monotonic() - self.position_at)
        return at

    def bridged(self) -> bool:
        return False

    def stale(self, _status: Any) -> bool:
        return False

    def _stream_options(self) -> tuple[str, dict[str, str]]:
        from ..core import nerd
        n = nerd.current()
        src = nerd.source()
        return n.user_agent(src), n.headers(src)

    def cast(self, device_name: str, url: str, title: str,
             known_codecs: list[str] | None = None,
             audio: dict | None = None, subs: dict | None = None,
             start: float = 0.0, duration: float = 0.0,
             settle: bool = False, source: str | None = None,
             quality: str = "original", height: int = 0,
             fps: float = 0.0, dvr: bool = False) -> str:
        with self._lock:
            dev = self.device(device_name)
            if dev is None:
                log.info("kodi: %s not found yet - looking first", device_name)
                self.scan()
                dev = self.devices.get(device_name)
            if dev is None:
                raise LocalizedError("cast_err_not_found", name=device_name)
        if settle:
            time.sleep(1.5)
        # The address the player itself reads, as the provider serves it:
        # Kodi takes a transport stream, and an HLS wrapper too.
        target = source or url
        if not target:
            raise LocalizedError("cast_err_no_address")
        if "://" not in target:
            if not os.path.isfile(target):
                raise LocalizedError("cast_err_no_address")
            target = shown = self.files.start(target)
        else:
            # Never the app's own loopback addresses (the rewind buffer, the
            # share server): only this computer can open them.
            if is_loopback_url(target):
                raise LocalizedError("cast_err_kodi_local")
            self.files.stop()
            shown = target
            ua, headers = self._stream_options()
            target = with_options(target, ua, headers)
        self._poll_gen += 1             # the last cast's watcher stands down
        self.active = dev
        self.gone = False
        self.state = ""
        self._played = False
        self._file = ""
        self._dvr = bool(dvr)
        self.last_position = self.position_offset = self.position_at = 0.0
        self.duration = 0.0 if dvr else float(duration or 0.0)
        log.info("kodi -> %s (%s): %s", dev.name, dev.address, redact_url(shown))
        try:
            self._call("Player.Open", {"item": {"file": target}}, timeout=15)
        except KodiAuthError as e:
            self._drop()
            raise LocalizedError("cast_err_kodi_login", name=dev.name) from e
        except KodiError as e:
            log.info("kodi: %s refused the stream (%s)", dev.name, e)
            self._drop()
            raise LocalizedError("cast_err_kodi_refused", msg=str(e)) from e
        except requests.RequestException as e:
            log.info("kodi: %s did not answer (%s)", dev.name, type(e).__name__)
            self._drop()
            raise LocalizedError("cast_err_dlna_no_answer", name=dev.name) from e
        if not self._wait_for_playing():
            self.stop()
            raise LocalizedError("cast_err_kodi_no_start")
        if start and start > 1 and not dvr:
            try:
                self.seek(start)
            except Exception as e:
                log.info("kodi: could not start part way in (%s)", e)
        self._read_volume()
        self._start_polling()
        return device_name

    def _drop(self) -> None:
        self.active = None
        self.files.stop()

    def _players(self) -> list[dict[str, Any]]:
        got = self._call("Player.GetActivePlayers", timeout=4)
        return [p for p in got or [] if isinstance(p, dict)
                and p.get("type") in ("video", "audio")]

    def _properties(self) -> dict[str, Any]:
        got = self._call("Player.GetProperties",
                         {"playerid": self._player_id,
                          "properties": ["speed", "time", "totaltime"]}, timeout=4)
        return got if isinstance(got, dict) else {}

    def _wait_for_playing(self) -> bool:
        """Whether Kodi starts within START_WAIT_S. A player that appeared
        and is still filling its buffer when the time is up is given the
        benefit of the doubt; none at all is a refusal."""
        deadline = time.monotonic() + START_WAIT_S
        seen = False
        while time.monotonic() < deadline:
            try:
                players = self._players()
                if players:
                    seen = True
                    self._player_id = int(players[0].get("playerid") or 0)
                    props = self._properties()
                    if float(props.get("speed") or 0) > 0:
                        self._played = True
                        self.state = "PLAYING/None"
                        self.last_position = seconds_of(props.get("time"))
                        self.position_at = time.monotonic()
                        return True
                    self.state = "BUFFERING/None"
            except KodiError as e:
                log.info("kodi: no player state (%s)", e)
            except requests.RequestException as e:
                log.info("kodi: no player state (%s)", type(e).__name__)
                return True             # it took the stream; trust it
            time.sleep(0.6)
        return seen

    def _start_polling(self) -> None:
        gen = self._poll_gen

        def run() -> None:
            n = 0
            missing = 0
            silent = 0
            while gen == self._poll_gen and self.active is not None:
                try:
                    missing = self._poll(n, missing)
                    silent = 0
                except Exception as e:
                    log.debug("kodi: poll failed (%s)", type(e).__name__)
                    silent += 1
                    if silent >= UNREACHABLE_AFTER and gen == self._poll_gen:
                        self._ended("Kodi stopped answering")
                        return
                n += 1
                time.sleep(POLL_S)

        threading.Thread(target=run, daemon=True, name="kodi-poll").start()

    def _poll(self, n: int, missing: int) -> int:
        players = self._players()
        if not players:
            missing += 1
            if self._played and missing >= GONE_AFTER:
                if self._dvr:
                    # A stretch of the archive has run out: the window asks
                    # for the next one (mw_cast's _cast_continue_archive).
                    if self.state != "IDLE/FINISHED":
                        log.info("kodi %s: the stretch ended",
                                 getattr(self.active, "name", "?"))
                    self.state = "IDLE/FINISHED"
                else:
                    self._ended("stopped on Kodi")
            elif not self._played:
                self.state = "IDLE/None"
            return missing
        self._player_id = int(players[0].get("playerid") or 0)
        props = self._properties()
        speed = float(props.get("speed") or 0)
        new = "PLAYING/None" if speed > 0 else "PAUSED/None"
        if speed > 0:
            self._played = True
        if new != self.state:
            log.info("kodi %s: %s", getattr(self.active, "name", "?"), new)
        self.state = new
        self.last_position = seconds_of(props.get("time"))
        self.position_at = time.monotonic()
        if n % 4 == 0:
            self._read_volume()
            self._check_item()
        return 0

    def _check_item(self) -> None:
        """Whether Kodi still plays what was cast: another item started on
        Kodi itself ends the cast here, without stopping that one."""
        got = self._call("Player.GetItem",
                         {"playerid": self._player_id, "properties": ["file"]},
                         timeout=4)
        item = (got or {}).get("item") if isinstance(got, dict) else None
        f = str((item or {}).get("file") or "")
        if not f:
            return
        if not self._file:
            self._file = f
        elif f != self._file:
            self._ended("something else plays on Kodi")

    def _ended(self, why: str) -> None:
        log.info("kodi %s: the cast ended there (%s)",
                 getattr(self.active, "name", "?"), why)
        self._poll_gen += 1
        self.state = "IDLE/FINISHED"
        self.gone = True
        self._drop()

    def _read_volume(self) -> None:
        try:
            got = self._call("Application.GetProperties",
                             {"properties": ["volume", "muted"]}, timeout=4)
            if isinstance(got, dict):
                level = max(0, min(100, int(got.get("volume") or 0)))
                self._volume = (level / 100.0, bool(got.get("muted")))
        except (KodiError, ValueError, TypeError, requests.RequestException) as e:
            log.debug("kodi: volume unknown (%s)", type(e).__name__)

    def volume(self) -> tuple[float, bool]:
        """Kodi's level as last read - never a network call, the window
        asks this on its own thread."""
        return self._volume

    def set_volume(self, level: float) -> None:
        level = max(0.0, min(1.0, float(level)))
        try:
            self._call("Application.SetVolume", {"volume": int(round(level * 100))})
            self._volume = (level, self._volume[1])
        except Exception as e:
            log.info("kodi: would not change volume (%s)", type(e).__name__)

    def set_muted(self, muted: bool) -> None:
        try:
            self._call("Application.SetMute", {"mute": bool(muted)})
            self._volume = (self._volume[0], bool(muted))
        except Exception as e:
            log.info("kodi: would not mute (%s)", type(e).__name__)

    def _play(self, play: bool) -> None:
        try:
            self._call("Player.PlayPause", {"playerid": self._player_id, "play": play})
        except Exception as e:
            log.info("kodi: would not %s (%s)", "resume" if play else "pause",
                     type(e).__name__)

    def pause(self) -> None:
        self._play(False)

    def resume(self) -> None:
        self._play(True)

    def seek(self, seconds: float) -> None:
        if self.active is None:
            return
        to = time_obj(seconds)
        try:
            try:
                # Kodi 19 and later: the kind of seek named.
                self._call("Player.Seek", {"playerid": self._player_id,
                                           "value": {"time": to}})
            except KodiError as e:
                if e.code != _INVALID_PARAMS:
                    raise
                # Kodi 18 and earlier: the time itself.
                self._call("Player.Seek", {"playerid": self._player_id, "value": to})
            self.last_position = max(0.0, float(seconds))
            self.position_at = time.monotonic()
        except Exception as e:
            log.info("kodi: would not seek (%s)", e)

    def stop(self) -> None:
        with self._lock:
            self._poll_gen += 1
            if self.active is not None:
                try:
                    self._call("Player.Stop", {"playerid": self._player_id},
                               timeout=3)
                except Exception:
                    pass
                self.active = None
        self.files.stop()

    def shutdown(self) -> None:
        if self.active is not None:
            log.info("kodi: the app is closing - stopping the cast on %s",
                     self.active.name)
        self.stop()
