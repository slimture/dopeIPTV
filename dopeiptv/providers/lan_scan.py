"""Find TV boxes and servers on the home network: Enigma2 receivers,
Tvheadend servers and HDHomeRun tuners (and what imitates one), and the
media servers - Jellyfin, Emby and Plex; and, for the cast window, what can
be cast to on another part of the network: Chromecasts, Kodis and DLNA TVs.

HDHomeRun tuners, Jellyfin and Emby (UDP 7359) and Plex (GDM, UDP 32414)
answer a broadcast; everything is also asked directly, every address of the
local /24 at once with short timeouts, on the port its kind listens on (a
server in a container often never hears a broadcast). Only private networks
are scanned, only when the user asks.

A broadcast or a multicast search (mDNS, SSDP) does not cross to another
part of a network split in VLANs unless the router passes it on; a question
to one address does. The other networks Nerd > Network lists
(core/home_net.py) are asked that way: every address and port at once
(``open_ports``: a few hundred connections in flight, each given a moment),
then only the addresses that answered, each kind on its own port - an
Enigma2's /web/about, Tvheadend's /api/serverinfo, an HDHomeRun's
discover.json, Jellyfin's and Emby's /System/Info/Public, Plex's /identity,
a Kodi's JSON-RPC (8080), a Chromecast's eureka_info (8008), a DLNA TV by
UPnP's unicast search (UDP 1900) or the paths renderers keep their
description at. Off the window's thread; *cancel* (a threading.Event) stops
a search at its next step.
"""

from __future__ import annotations

import errno
import functools
import ipaddress
import selectors
import socket
import sys
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable

from ..core import home_net
from ..core._lazy_requests import requests
from ..core.log import log
from ..core.nerd_check import OK, WARN, Check
from . import dlna, hdhomerun, jellyfin, plex
from .boxes import USER_AGENT, child_text, parse_xml

PORTS = {"enigma2": 80, "tvheadend": 9981, "hdhomerun": 80, "jellyfin": 8096, "plex": 32400}
#: What can be cast to, and where each listens: Kodi's web server, a
#: Chromecast's setup page (DLNA answers on UDP 1900).
CAST_PORTS = {"kodi": 8080, "chromecast": 8008}
CAST_KINDS = ("chromecast", "dlna", "kodi")
CONNECT = 0.35
READ = 2.5
#: Connections in flight at once while sweeping other networks.
IN_FLIGHT = 384


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


def _open(host: str, port: int, timeout: float = CONNECT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


# -- many connections at once ------------------------------------------------------

def _in_flight() -> int:
    """How many connections a sweep keeps open at once: what the system's
    limits allow (select() on Windows takes 512 sockets at most)."""
    if sys.platform == "win32":
        return 200
    try:
        import resource
        soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    except (ImportError, OSError, ValueError):
        return 128
    if soft == resource.RLIM_INFINITY:
        return IN_FLIGHT
    return max(16, min(IN_FLIGHT, soft // 2 - 64))


_PENDING = {errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, 10035}


def open_ports(targets: Iterable[tuple[str, int]], timeout: float = CONNECT,
               limit: int | None = None, cancel: threading.Event | None = None
               ) -> set[tuple[str, int]]:
    """Which (address, port) of *targets* take a TCP connection: many in
    flight at once without a thread each, every one given *timeout* - an
    address where nothing is there costs that once, not once per port in a
    row. Addresses only, no names."""
    sel = selectors.DefaultSelector()
    todo = list(targets)
    todo.reverse()
    cap = max(1, limit or _in_flight())
    flying: OrderedDict[socket.socket, float] = OrderedDict()
    found: set[tuple[str, int]] = set()

    def drop(sock: socket.socket) -> None:
        flying.pop(sock, None)
        try:
            sel.unregister(sock)
        except (KeyError, ValueError, OSError):
            pass
        sock.close()
    try:
        while todo or flying:
            if cancel is not None and cancel.is_set():
                break
            while todo and len(flying) < cap:
                host, port = todo.pop()
                try:
                    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET,
                                         socket.SOCK_STREAM)
                except OSError:
                    if not flying:           # nothing to wait for: give it up
                        continue
                    todo.append((host, port))  # out of descriptors: some will close
                    break
                sock.setblocking(False)
                try:
                    err = sock.connect_ex((host, port))
                except OSError:
                    sock.close()
                    continue
                if err == 0:
                    found.add((host, port))
                    sock.close()
                elif err in _PENDING:
                    sel.register(sock, selectors.EVENT_WRITE, (host, port))
                    flying[sock] = time.monotonic() + timeout
                else:
                    sock.close()             # refused, or no route there
            if not flying:
                continue
            first = next(iter(flying.values()))
            for key, _ev in sel.select(max(0.0, min(first - time.monotonic(), 0.25))):
                ready = key.fileobj
                if not isinstance(ready, socket.socket):
                    continue
                try:
                    ok = ready.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0
                except OSError:
                    ok = False
                if ok:
                    found.add(key.data)
                drop(ready)
            now = time.monotonic()
            while flying:
                sock, until = next(iter(flying.items()))
                if until > now:
                    break
                drop(sock)
    finally:
        for sock in list(flying):
            drop(sock)
        sel.close()
    return found


# -- boxes and media servers ------------------------------------------------------------

def probe(host: str, ports: dict[str, int] | None = None,
          opened: set[int] | None = None) -> list[dict]:
    """What answers on *host*: [{"kind", "server", "name"}]. *opened*: the
    ports a sweep found taking a connection; no other is tried."""
    ports = dict(PORTS, **(ports or {}))
    found: list[dict] = []
    h = _http_host(host)

    def is_open(port: int) -> bool:
        return port in opened if opened is not None else _open(host, port)
    sess = requests.Session()
    sess.headers["User-Agent"] = USER_AGENT
    with sess:
        p = ports["enigma2"]
        if is_open(p):
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
        if ports["hdhomerun"] != p and is_open(ports["hdhomerun"]):
            q = ports["hdhomerun"]
            _hdhr(sess, f"http://{h}" + ("" if q == 80 else f":{q}"), host, found)
        p = ports["tvheadend"]
        if is_open(p):
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
        if is_open(p):
            _jellyfin(sess, f"http://{h}:{p}", host, found)
        p = ports["plex"]
        if is_open(p):
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


def _sweep(hosts: list[str], ports: Iterable[int], cancel: threading.Event | None
           ) -> dict[str, set[int]]:
    """{address: its open ports} of those of *hosts* where any is."""
    wanted = sorted(set(ports))
    opened: dict[str, set[int]] = {}
    for host, port in open_ports([(h, p) for h in hosts for p in wanted], cancel=cancel):
        opened.setdefault(host, set()).add(port)
    return opened


def scan(progress: Callable[[int], None] | None = None, hosts: list[str] | None = None,
         ports: dict[str, int] | None = None, workers: int = 64,
         broadcast: bool = True, extra: list[str] | None = None,
         cancel: threading.Event | None = None) -> list[dict]:
    """Every box and server found: the broadcasts first (HDHomeRun,
    Jellyfin and Emby, Plex's GDM), then each host of the local networks (or
    *hosts*) asked directly, then the other networks Nerd lists (*extra*:
    their addresses; None: as the settings say) - swept first, and only an
    address that took a connection asked further. One entry per address."""
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
    if extra is None:
        extra = home_net.configured()
    mine = set(hosts)
    extra = [h for h in extra if h not in mine]
    total = len(hosts) + (len(extra) if extra else 0)
    if not total:
        return list(found.values())
    done = 0

    def took(got: list[dict]) -> None:
        nonlocal done
        done += 1
        for d in got:
            # Asked directly, a box names itself better than a broadcast.
            found[(d["kind"], d["server"].rstrip("/"))] = d
        if progress:
            progress(min(99, done * 100 // total))
    if hosts:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(hosts)))) as pool:
            for got in pool.map(lambda h: [] if cancel is not None and cancel.is_set()
                                else probe(h, ports), hosts):
                took(got)
    if extra and not (cancel is not None and cancel.is_set()):
        want = dict(PORTS, **(ports or {}))
        opened = _sweep(extra, want.values(), cancel)
        done += len(extra) - len(opened)
        alive = sorted(opened)
        if alive:
            with ThreadPoolExecutor(max_workers=max(1, min(workers, len(alive)))) as pool:
                for got in pool.map(lambda h: [] if cancel is not None and cancel.is_set()
                                    else probe(h, ports, opened[h]), alive):
                    took(got)
        log.info("lan scan: %d address(es) of other networks asked, %d answered",
                 len(extra), len(alive))
    if progress:
        progress(100)
    log.info("lan scan: %d box(es) on %d address(es)", len(found), total)
    return sorted(found.values(), key=lambda d: (d["kind"], d["server"]))


# -- what can be cast to --------------------------------------------------------------

def _kodi(sess: Any, host: str, port: int) -> dict | None:
    """A Kodi's JSON-RPC: "pong" to a ping; a 401 from Kodi's own web
    server (its realm names it) is a Kodi that wants its login."""
    url = f"http://{_http_host(host)}:{port}/jsonrpc"
    try:
        r = sess.post(url, json={"jsonrpc": "2.0", "method": "JSONRPC.Ping", "id": 1},
                      timeout=(CONNECT * 3, READ))
    except Exception:
        return None
    if r.status_code == 401:
        realm = (r.headers.get("WWW-Authenticate", "") + r.headers.get("Server", "")).lower()
        if "xbmc" in realm or "kodi" in realm:
            return {"kind": "kodi", "host": host, "port": port, "name": f"Kodi ({host})",
                    "login": True}
        return None
    try:
        js = r.json() if r.ok else None
    except ValueError:
        return None
    if not isinstance(js, dict) or js.get("result") != "pong":
        return None
    name = ""
    try:
        r = sess.post(url, json={"jsonrpc": "2.0", "method": "XBMC.GetInfoLabels", "id": 2,
                                 "params": {"labels": ["System.FriendlyName"]}},
                      timeout=(CONNECT * 3, READ))
        got = (r.json() or {}).get("result") if r.ok else None
        if isinstance(got, dict):
            name = str(got.get("System.FriendlyName") or "").strip()
    except Exception:
        pass
    name = "".join(c for c in name if c.isprintable())[:80]
    return {"kind": "kodi", "host": host, "port": port, "name": name or f"Kodi ({host})"}


def _chromecast(sess: Any, host: str, port: int = 8008) -> dict | None:
    """A Chromecast (or a TV or speaker with one inside) by its setup page:
    its name, model and id."""
    try:
        r = sess.get(f"http://{_http_host(host)}:{port}/setup/eureka_info",
                     params={"params": "name,device_info"}, timeout=(CONNECT * 3, READ))
        js = r.json() if r.ok else None
    except Exception:
        return None
    if not isinstance(js, dict) or not (js.get("name") or js.get("device_info")):
        return None
    raw = js.get("device_info")
    info: dict = raw if isinstance(raw, dict) else {}
    name = str(info.get("name") or js.get("name") or "").strip()
    name = "".join(c for c in name if c.isprintable())[:80]
    udn = str(info.get("ssdp_udn") or js.get("ssdp_udn") or "")
    return {"kind": "chromecast", "host": host, "port": 8009,
            "name": name or f"Chromecast ({host})", "uuid": udn,
            "model": str(info.get("model_name") or "")[:80]}


def _renderer(location: str, host: str) -> dict | None:
    r = dlna.describe(location)
    if r is None:
        return None
    return {"kind": "dlna", "host": host, "location": location, "name": r.name,
            "renderer": r}


def dlna_at(host: str, ssdp_port: int = 1900, paths: tuple[tuple[int, str], ...] | None = None,
            timeout: float = 1.5, cancel: threading.Event | None = None) -> dict | None:
    """The DLNA renderer at *host*: asked by UPnP's unicast search, else at
    the paths renderers keep their description at."""
    for loc in dlna.ssdp_unicast([host], timeout, ssdp_port, cancel).get(host, []):
        got = _renderer(loc, host)
        if got is not None:
            return got
    table = dlna.DESCRIPTION_PATHS if paths is None else paths
    opened = {p for _h, p in open_ports([(host, p) for p, _path in table], cancel=cancel)}
    for port, path in table:
        if port in opened:
            got = _renderer(f"http://{_http_host(host)}:{port}{path}", host)
            if got is not None:
                return got
    return None


def probe_cast(host: str, opened: set[int] | None = None, ports: dict[str, int] | None = None,
               kinds: tuple[str, ...] = ("chromecast", "kodi")) -> list[dict]:
    """The Chromecast and the Kodi at *host* (DLNA is asked in a sweep of
    its own, by UDP): [{"kind", "host", "port", "name", ...}]."""
    ports = dict(CAST_PORTS, **(ports or {}))
    out: list[dict] = []
    sess = requests.Session()
    sess.headers["User-Agent"] = USER_AGENT
    with sess:
        if "chromecast" in kinds and (opened is None or ports["chromecast"] in opened):
            got = _chromecast(sess, host, ports["chromecast"])
            if got:
                out.append(got)
        if "kodi" in kinds and (opened is None or ports["kodi"] in opened):
            got = _kodi(sess, host, ports["kodi"])
            if got:
                out.append(got)
    return out


def scan_cast(hosts: list[str], ports: dict[str, int] | None = None, ssdp_port: int = 1900,
              cancel: threading.Event | None = None, workers: int = 32) -> list[dict]:
    """What can be cast to at *hosts* (the other networks' addresses),
    asked by unicast: a sweep of the Chromecast and Kodi ports beside
    UPnP's unicast search, then each address that answered. A DLNA entry
    carries its dlna.Renderer ("renderer")."""
    if not hosts:
        return []
    want = dict(CAST_PORTS, **(ports or {}))
    with ThreadPoolExecutor(max_workers=2) as pool:
        tcp = pool.submit(_sweep, hosts, want.values(), cancel)
        udp = pool.submit(dlna.ssdp_unicast, hosts, 2.0, ssdp_port, cancel)
        opened = tcp.result()
        answers = udp.result()
    found: list[dict] = []
    jobs: list[Callable[[], Any]] = []
    for host in sorted(opened):
        jobs.append(functools.partial(probe_cast, host, opened[host], ports))
    for host, locs in sorted(answers.items()):
        for loc in locs:
            jobs.append(functools.partial(_renderer, loc, host))
    if jobs and not (cancel is not None and cancel.is_set()):
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(jobs)))) as pool:
            for got in pool.map(lambda job: job(), jobs):
                if isinstance(got, list):
                    found += got
                elif isinstance(got, dict):
                    found.append(got)
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for d in found:
        key = (d["kind"], str(d.get("location") or f"{d['host']}:{d.get('port', '')}"))
        if d["kind"] == "dlna":
            udn = getattr(d.get("renderer"), "udn", "")
            key = ("dlna", udn or key[1])
        if key not in seen:
            seen.add(key)
            out.append(d)
    log.info("cast scan: %d device(s) on %d address(es) of other networks", len(out), len(hosts))
    return out


# -- the Nerd page's Test ------------------------------------------------------------

def check_networks(value: Any, cancel: threading.Event | None = None) -> Check:
    """Search the other networks as typed (boxes, media servers and what can
    be cast to) and say what answered."""
    hosts = home_net.addresses(value)
    if not hosts:
        return home_net.check_list(value)
    started = time.monotonic()
    boxes = scan(hosts=[], extra=hosts, broadcast=False, cancel=cancel)
    casts = scan_cast(hosts, cancel=cancel)
    names = [d["name"] for d in boxes] + [d["name"] for d in casts]
    secs = round(time.monotonic() - started, 1)
    detail = "\n".join(f"{d['kind']}: {d['name']} ({d.get('server') or d.get('location') or d.get('host')})"
                       for d in boxes + casts)
    if not names:
        return Check(WARN, "nerd_ck_ln_nothing", {"n": len(hosts), "secs": secs},
                     f"{len(hosts)} addresses, {secs} s")
    shown = ", ".join(names[:4]) + (" …" if len(names) > 4 else "")
    return Check(OK, "nerd_ck_ln_found", {"n": len(names), "names": shown, "secs": secs}, detail)
