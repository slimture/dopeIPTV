"""Find TV boxes and servers on the home network: Enigma2 receivers,
Tvheadend servers and HDHomeRun tuners (and what imitates one), and the
media servers - Jellyfin, Emby and Plex.

HDHomeRun tuners, Jellyfin and Emby (UDP 7359) and Plex (GDM, UDP 32414)
answer a broadcast; everything is also asked directly, every address of the
local /24 at once with short timeouts, on the port its kind listens on (a
server in a container often never hears a broadcast). Only private networks
are scanned, only when the user asks.
"""

from __future__ import annotations

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from ..core._lazy_requests import requests
from ..core.log import log
from . import hdhomerun, jellyfin, plex
from .boxes import USER_AGENT, child_text, parse_xml

PORTS = {"enigma2": 80, "tvheadend": 9981, "hdhomerun": 80, "jellyfin": 8096, "plex": 32400}
CONNECT = 0.35
READ = 2.5


def local_networks() -> list[ipaddress.IPv4Network]:
    """The /24 of each private IPv4 address this machine has."""
    addrs: set[str] = set()
    for probe in ("10.255.255.255", "192.168.255.255", "172.31.255.255"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((probe, 9))          # no packet: it only picks a route
                addrs.add(s.getsockname()[0])
        except OSError:
            continue
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(str(info[4][0]))
    except OSError:
        pass
    nets: dict[str, ipaddress.IPv4Network] = {}
    for a in addrs:
        try:
            ip = ipaddress.IPv4Address(a)
        except ValueError:
            continue
        if ip.is_private and not ip.is_loopback and not ip.is_link_local:
            net = ipaddress.IPv4Network(f"{a}/24", strict=False)
            nets[str(net)] = net
    return list(nets.values())


def _open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=CONNECT):
            return True
    except OSError:
        return False


def _http_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def probe(host: str, ports: dict[str, int] | None = None) -> list[dict]:
    """What answers on *host*: [{"kind", "server", "name"}]."""
    ports = dict(PORTS, **(ports or {}))
    found: list[dict] = []
    h = _http_host(host)
    sess = requests.Session()
    sess.headers["User-Agent"] = USER_AGENT
    with sess:
        p = ports["enigma2"]
        if _open(host, p):
            base = f"http://{h}" + ("" if p == 80 else f":{p}")
            try:
                r = sess.get(base + "/web/about", timeout=(CONNECT * 3, READ))
                if r.status_code == 401 and "enigma" in (r.headers.get("WWW-Authenticate", "")
                                                          + r.headers.get("Server", "")).lower():
                    found.append({"kind": "enigma2", "server": base, "name": f"Enigma2 ({host})",
                                  "login": True})
                elif r.ok and b"<e2about" in r.content[:4096]:
                    root = parse_xml(r.content)
                    about = root.find("e2about")
                    model = child_text(about, "e2model") if about is not None else ""
                    image = " ".join(x for x in (
                        child_text(about, "e2distroversion") if about is not None else "",
                        child_text(about, "e2imageversion") if about is not None else "") if x)
                    found.append({"kind": "enigma2", "server": base,
                                  "name": " ".join(x for x in (model or "Enigma2", f"({image})" if image else "")
                                                   if x) + f" - {host}"})
            except Exception:
                pass
            if ports["hdhomerun"] == p:
                _hdhr(sess, base, host, found)
        if ports["hdhomerun"] != p and _open(host, ports["hdhomerun"]):
            q = ports["hdhomerun"]
            _hdhr(sess, f"http://{h}" + ("" if q == 80 else f":{q}"), host, found)
        p = ports["tvheadend"]
        if _open(host, p):
            base = f"http://{h}:{p}"
            try:
                r = sess.get(base + "/api/serverinfo", timeout=(CONNECT * 3, READ))
                realm = r.headers.get("WWW-Authenticate", "").lower()
                if r.status_code == 401 and "tvheadend" in realm:
                    found.append({"kind": "tvheadend", "server": base, "name": f"Tvheadend - {host}",
                                  "login": True})
                elif r.ok:
                    js = r.json()
                    if isinstance(js, dict) and "sw_version" in js:
                        found.append({"kind": "tvheadend", "server": base,
                                      "name": f"Tvheadend {js.get('sw_version', '')} - {host}".replace("  ", " ")})
            except Exception:
                pass
        p = ports["jellyfin"]
        if _open(host, p):
            _jellyfin(sess, f"http://{h}:{p}", host, found)
        p = ports["plex"]
        if _open(host, p):
            _plex(sess, f"http://{h}:{p}", host, found)
    return found


def _jellyfin(sess: Any, base: str, host: str, found: list[dict]) -> None:
    """Jellyfin and Emby answer the same question; Jellyfin names itself,
    Emby answers under /emby as well."""
    try:
        r = sess.get(base + "/System/Info/Public", timeout=(CONNECT * 3, READ))
        js = r.json() if r.ok else None
    except Exception:
        return
    if not isinstance(js, dict) or not (js.get("Id") or js.get("ServerName")):
        return
    kind = "jellyfin" if "jellyfin" in str(js.get("ProductName") or "").lower() else ""
    if not kind:
        try:
            r = sess.get(base + "/emby/System/Info/Public", timeout=(CONNECT * 3, READ))
            kind = "emby" if r.ok and isinstance(r.json(), dict) else "jellyfin"
        except Exception:
            kind = "jellyfin"
    name = str(js.get("ServerName") or jellyfin.NAMES[kind])
    found.append({"kind": kind, "server": base, "name": f"{name} - {host}", "id": str(js.get("Id") or "")})


def _plex(sess: Any, base: str, host: str, found: list[dict]) -> None:
    try:
        r = sess.get(base + "/identity", headers={"Accept": "application/json"},
                     timeout=(CONNECT * 3, READ))
        js = r.json() if r.ok else None
    except Exception:
        return
    mc = js.get("MediaContainer") if isinstance(js, dict) else None
    ident = str(mc.get("machineIdentifier") or "") if isinstance(mc, dict) else ""
    if ident:
        found.append({"kind": "plex", "server": base, "name": f"Plex - {host}", "id": ident})


def _hdhr(sess, base: str, host: str, found: list[dict]) -> None:
    try:
        r = sess.get(base + "/discover.json", timeout=(CONNECT * 3, READ))
        js = r.json() if r.ok else None
    except Exception:
        return
    if isinstance(js, dict) and (js.get("DeviceID") or js.get("LineupURL")):
        name = str(js.get("FriendlyName") or "HDHomeRun")
        model = str(js.get("ModelNumber") or "")
        found.append({"kind": "hdhomerun", "server": base,
                      "name": f"{name} ({model}) - {host}" if model else f"{name} - {host}"})


def scan(progress: Callable[[int], None] | None = None, hosts: list[str] | None = None,
         ports: dict[str, int] | None = None, workers: int = 64,
         broadcast: bool = True) -> list[dict]:
    """Every box and server found: the broadcasts first (HDHomeRun,
    Jellyfin and Emby, Plex's GDM), then each host of the local networks (or
    *hosts*) asked directly. One entry per address."""
    found: dict[tuple[str, str], dict] = {}
    if broadcast:
        # The three listen at once: each waits out its own answers.
        with ThreadPoolExecutor(max_workers=3) as pool:
            asks = [pool.submit(f) for f in (hdhomerun.discover, jellyfin.discover, plex.discover)]
            answers: list[list[dict]] = []
            for a in asks:
                try:
                    answers.append(list(a.result()))
                except Exception as e:
                    log.debug("lan scan: a broadcast failed: %s", e)
                    answers.append([])
        for d in answers[0]:
            found[("hdhomerun", d["server"].rstrip("/"))] = {
                "kind": "hdhomerun", "server": d["server"].rstrip("/"),
                "name": f"HDHomeRun {d.get('device_id', '')}".strip()}
        for d in answers[1] + answers[2]:
            found[(d["kind"], d["server"].rstrip("/"))] = dict(d, server=d["server"].rstrip("/"))
    if hosts is None:
        hosts = [str(ip) for net in local_networks() for ip in net.hosts()]
    if not hosts:
        return list(found.values())
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(hosts)))) as pool:
        for got in pool.map(lambda h: probe(h, ports), hosts):
            done += 1
            for d in got:
                # Asked directly, a box names itself better than a broadcast.
                found[(d["kind"], d["server"].rstrip("/"))] = d
            if progress:
                progress(done * 100 // len(hosts))
    log.info("lan scan: %d box(es) on %d address(es)", len(found), len(hosts))
    return sorted(found.values(), key=lambda d: (d["kind"], d["server"]))
