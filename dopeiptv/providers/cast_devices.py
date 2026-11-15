"""Devices to cast to added by their address: a Chromecast or a DLNA TV that
the automatic search does not find - on another part of a network split in
VLANs, where mDNS and SSDP do not reach unless the router passes them on.

The cast window's "Add a device by address…" takes an address (192.168.20.31,
tv.lan, 192.168.20.31:8080) or a DLNA TV's description address
(http://192.168.20.31:9197/dmr), and finds out what is there - a
Chromecast by its setup page, a Kodi by its JSON-RPC, a DLNA renderer by
UPnP's unicast search or the paths renderers keep their description at -
or asks only the kind the person chose (providers/lan_scan.py does the
asking). A Chromecast and a DLNA TV are kept in ``cast_added_devices`` (a
JSON list), each with what casting to it needs, so it is in the list after
a restart even while it is off; a Kodi goes where Kodis added by hand are
(providers/kodi.py, ``kodi_devices``, with its login). CastHub
(providers/casting.py) hands them to their managers; casting to one is as
to one that was found.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from ..core.log import log
from ..i18n import LocalizedError

SETTINGS_KEY = "cast_added_devices"
#: What the person may pick ("auto": find out).
KINDS = ("auto", "chromecast", "dlna", "kodi")
#: What is kept here (a Kodi is kept with the other Kodis).
STORED_KINDS = ("chromecast", "dlna")
MAX_DEVICES = 50


@dataclass
class Added:
    kind: str                   # "chromecast" or "dlna"
    host: str
    name: str
    port: int = 0
    uuid: str = ""
    model: str = ""
    #: DLNA: the renderer as dlna.Renderer.to_dict() gives it.
    renderer: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        """One device however often it is added: a Chromecast by its
        address, a TV by its description address."""
        if self.kind == "dlna":
            return "dlna:" + str(self.renderer.get("location") or self.host)
        return f"{self.kind}:{self.host.lower()}"

    @property
    def address(self) -> str:
        if self.kind == "dlna":
            return str(self.renderer.get("location") or self.host)
        return self.host

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "host": self.host, "name": self.name}
        if self.port:
            out["port"] = self.port
        if self.uuid:
            out["uuid"] = self.uuid
        if self.model:
            out["model"] = self.model
        if self.renderer:
            out["renderer"] = dict(self.renderer)
        return out

    def as_found(self) -> dict[str, Any]:
        """As lan_scan reports a device it found."""
        d = self.to_dict()
        if self.kind == "dlna":
            from .dlna import Renderer
            d["renderer"] = Renderer.from_dict(self.renderer)
            d["location"] = self.renderer.get("location", "")
        return d

    @classmethod
    def from_found(cls, d: dict[str, Any]) -> "Added | None":
        kind = str(d.get("kind") or "")
        if kind not in STORED_KINDS or not d.get("host"):
            return None
        r: Any = d.get("renderer")
        renderer = r.to_dict() if hasattr(r, "to_dict") else dict(r or {})
        if kind == "dlna" and not renderer:
            return None
        try:
            port = int(d.get("port") or 0)
        except (TypeError, ValueError):
            port = 0
        return cls(kind=kind, host=str(d["host"]), name=_clean(str(d.get("name") or d["host"])),
                   port=port, uuid=str(d.get("uuid") or "")[:64],
                   model=str(d.get("model") or "")[:80], renderer=renderer)


def _clean(name: str) -> str:
    name = "".join(c for c in name if c.isprintable()).strip()
    return name[:80]


# -- the settings ---------------------------------------------------------------------

def load(settings: Any) -> list[Added]:
    if settings is None:
        return []
    try:
        raw = json.loads(settings.value(SETTINGS_KEY, "") or "[]")
    except (TypeError, ValueError):
        return []
    out: list[Added] = []
    for d in raw if isinstance(raw, list) else []:
        dev = Added.from_found(d) if isinstance(d, dict) else None
        if dev is not None and (dev.kind != "dlna" or dev.as_found()["renderer"] is not None):
            out.append(dev)
    return out[:MAX_DEVICES]


def save(settings: Any, devices: list[Added]) -> None:
    if settings is not None:
        settings.setValue(SETTINGS_KEY, json.dumps([d.to_dict() for d in devices[:MAX_DEVICES]]))


def remember(settings: Any, dev: Added) -> None:
    """Keep *dev*, in place of one with the same key."""
    mine = [d for d in load(settings) if d.key != dev.key]
    save(settings, mine + [dev])
    log.info("cast: %s at %s kept (%s)", dev.name, dev.host, dev.kind)


def forget(settings: Any, key: str) -> bool:
    mine = load(settings)
    rest = [d for d in mine if d.key != key]
    if len(rest) == len(mine):
        return False
    save(settings, rest)
    return True


# -- what the person typed ----------------------------------------------------------------

def parse_address(text: str) -> tuple[str, int, str] | None:
    """(host, port or 0, a description address or "") from what a person
    types: "192.168.20.31", "192.168.20.31:8080", "tv.lan", "[fd00::5]:8008",
    "http://192.168.20.31:9197/dmr" (a DLNA TV's description). None for
    anything that is not an address."""
    s = (text or "").strip()
    if not s or any(c.isspace() for c in s):
        return None
    full = s if "://" in s else "http://" + s
    try:
        u = urlsplit(full)
        host = u.hostname or ""
        port = u.port or 0
    except ValueError:
        return None
    if u.scheme != "http" or not host or "@" in u.netloc:
        return None
    if not all(c.isalnum() or c in ".-:_%" for c in host) or host.startswith(("-", ".")):
        return None
    described = full if "://" in s and u.path not in ("", "/") else ""
    return host, port, described


def detect(text: str, kind: str = "auto", ssdp_port: int = 1900,
           ports: dict[str, int] | None = None) -> dict[str, Any]:
    """(worker thread) What is at the address typed, as lan_scan reports a
    device: a Chromecast, a Kodi or a DLNA renderer - the one *kind* names,
    or the first that answers ("auto"). LocalizedError when the address is
    not one, or nothing of that kind answers there."""
    from . import lan_scan
    from ..core._lazy_requests import requests
    where = parse_address(text)
    if where is None:
        raise LocalizedError("cast_addr_bad")
    host, port, described = where
    shown = f"{host}:{port}" if port else host
    if described:
        if kind not in ("auto", "dlna"):
            raise LocalizedError("cast_addr_nothing", address=shown)
        got = lan_scan._renderer(described, host)
        if got is None:
            raise LocalizedError("cast_addr_not_dlna", address=described)
        return got
    order = ["chromecast", "kodi", "dlna"] if kind == "auto" else [kind]
    if any(k not in KINDS for k in order):
        raise LocalizedError("cast_addr_bad")
    want = dict(lan_scan.CAST_PORTS, **(ports or {}))
    sess = requests.Session()
    sess.headers["User-Agent"] = lan_scan.USER_AGENT
    with sess:
        for k in order:
            if k == "chromecast":
                got = lan_scan._chromecast(sess, host, want["chromecast"])
                if got is None and kind == "chromecast" and lan_scan._open(host, 8009, 1.0):
                    # Chosen as one, and its cast port answers: a receiver
                    # whose setup page is not on 8008.
                    got = {"kind": "chromecast", "host": host, "port": 8009,
                           "name": f"Chromecast ({host})", "uuid": "", "model": ""}
            elif k == "kodi":
                got = lan_scan._kodi(sess, host, port or want["kodi"])
            else:
                got = lan_scan.dlna_at(_address_of(host), ssdp_port)
            if got is not None:
                log.info("cast: %s answers at %s", got["kind"], shown)
                return got
    raise LocalizedError("cast_addr_nothing", address=shown)


def _address_of(host: str) -> str:
    """The address a name stands for (UDP answers come from an address)."""
    import socket
    try:
        return str(socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0])
    except (OSError, IndexError):
        return host
