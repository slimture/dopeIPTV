"""Find TV boxes and servers on the home network: Enigma2 receivers,
Tvheadend servers, HDHomeRun tuners (and what imitates one), VDR
(restfulapi, else streamdev), DVBViewer Media Servers, NextPVR and MythTV
backends, SAT>IP servers, and the media servers - Jellyfin, Emby and Plex;
and, for the cast window, what can be cast to on another part of the
network: Chromecasts, Kodis and DLNA TVs.

HDHomeRun tuners, Jellyfin and Emby (UDP 7359) and Plex (GDM, UDP 32414)
answer a broadcast, SAT>IP servers an SSDP search (providers/satip.py);
everything else is also asked directly, every address of the local /24 on
the port its kind listens on (a server in a container often never hears a
broadcast). Only private networks are scanned, only when the user asks.

A broadcast or a multicast search (mDNS, SSDP) does not cross to another
part of a network split in VLANs unless the router passes it on; a question
to one address does. The other networks Nerd > Network lists
(core/home_net.py) are asked that way, together with the local /24 - and a
SAT>IP server there by UPnP's unicast search (UDP 1900).

Every address and port is tried at once (``open_ports``; ``sweep`` per
address): a few hundred connections in flight without a thread each, well
under the process's file limit (macOS allows 256), each given a moment - an
address where nothing is there costs one short wait, not one per port.
IPv4 or IPv6 addresses, no names. Only an address that took a connection is
asked further, and only on the ports that did; each kind is told by what it
answers there, never by the port alone - an Enigma2's /web/about,
Tvheadend's /api/serverinfo, an HDHomeRun's discover.json, Jellyfin's and
Emby's /System/Info/Public, Plex's /identity, VDR's restfulapi (8002) or
streamdev's channels.m3u (3000), a DVBViewer Media Server's version (8089),
NextPVR's session.initiate (8866), MythTV's GetHostName (6544); for the
cast window a Kodi's JSON-RPC (8080), a Chromecast's eureka_info (8008), a
DLNA TV by UPnP's unicast search (UDP 1900) or the paths renderers keep
their description at. Off the window's thread; *cancel* (a threading.Event)
stops a search at its next step.
"""

from __future__ import annotations

import errno
import functools
import ipaddress
import json
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
from . import dlna, hdhomerun, jellyfin, plex, satip
from .boxes import USER_AGENT, child_text, parse_xml

PORTS = {"enigma2": 80, "tvheadend": 9981, "hdhomerun": 80, "jellyfin": 8096, "plex": 32400,
         "vdr": 8002, "streamdev": 3000, "dvbviewer": 8089, "nextpvr": 8866, "mythtv": 6544}
#: What can be cast to, and where each listens: Kodi's web server, a
#: Chromecast's setup page (DLNA answers on UDP 1900).
CAST_PORTS = {"kodi": 8080, "chromecast": 8008}
CAST_KINDS = ("chromecast", "dlna", "kodi")
CONNECT = 0.35
READ = 2.5
#: The most connections a sweep has in flight at once.
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
    """One port, one connection (a name is looked up)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


# -- many connections at once ------------------------------------------------------

def _socket_budget() -> int:
    """How many connects a sweep has on the way at once: well under the
    process's file limit (macOS allows 256 by default; 128 are left to the
    rest of the app), IN_FLIGHT at most, and within what select() takes on
    Windows (512 sockets)."""
    if sys.platform == "win32":
        return 256
    try:
        import resource
        soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    except (ImportError, OSError, ValueError):
        return 128
    if soft == resource.RLIM_INFINITY:
        return IN_FLIGHT
    return max(16, min(IN_FLIGHT, soft - 128))


# A connect that has not finished yet, as each system says it (10035:
# Windows' WSAEWOULDBLOCK).
_PENDING = {errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EAGAIN, errno.EALREADY, 10035}


def open_ports(targets: Iterable[tuple[str, int]], timeout: float = CONNECT,
               limit: int | None = None, cancel: threading.Event | None = None
               ) -> set[tuple[str, int]]:
    """Which (address, port) of *targets* take a TCP connection: up to
    *limit* (else ``_socket_budget()``) in flight at once without a thread
    each, every one given *timeout* - an address where nothing is there
    costs that once, not once per port in a row. IPv4 or IPv6 addresses
    only, no names; *cancel* stops it at its next step."""
    sel = selectors.DefaultSelector()
    todo = [(h, p) for h, p in targets if 0 < p < 65536]
    todo.reverse()
    cap = max(1, limit or _socket_budget())
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
            # Every one has the same timeout: the oldest is the first due.
            first = next(iter(flying.values()))
            for key, _ev in sel.select(max(0.0, min(first - time.monotonic(), 0.25))):
                ready = key.fileobj
                if not isinstance(ready, socket.socket) or ready not in flying:
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


def sweep(hosts: Iterable[str], ports: Iterable[int], timeout: float = CONNECT,
          budget: int | None = None, cancel: threading.Event | None = None
          ) -> dict[str, set[int]]:
    """{address: its open ports} of those of *hosts* where any of *ports*
    takes a connection: every (address, port) tried at once (open_ports, up
    to *budget* on the way) - a /24 and ten ports in a few short waits, not
    one wait per port and host."""
    wanted = sorted(set(ports))
    opened: dict[str, set[int]] = {}
    for host, port in open_ports([(h, p) for h in hosts for p in wanted], timeout, budget, cancel):
        opened.setdefault(host, set()).add(port)
    return opened


def _open_ports(host: str, ports: Iterable[int]) -> set[int]:
    return sweep([host], ports).get(host, set())


# -- boxes and media servers ------------------------------------------------------------

def probe(host: str, ports: dict[str, int] | None = None,
          opened: set[int] | None = None) -> list[dict]:
    """What answers on *host*: [{"kind", "server", "name"}]. *opened*: the
    ports a sweep found taking a connection, no other is asked; None: the
    host's ports are tried here, all at once."""
    ports = dict(PORTS, **(ports or {}))
    found: list[dict] = []
    h = _http_host(host)
    up = _open_ports(host, ports.values()) if opened is None else opened
    if not up:
        return found
    sess = requests.Session()
    sess.headers["User-Agent"] = USER_AGENT
    with sess:
        p = ports["enigma2"]
        if p in up:
            base = f"http://{h}" + ("" if p == 80 else f":{p}")
            try:
                r = sess.get(base + "/web/about", timeout=(CONNECT * 3, READ), **_AS_IT_STANDS)
                about_xml = _short_body(r) or b""
                if r.status_code == 401 and "enigma" in (r.headers.get("WWW-Authenticate", "")
                                                          + r.headers.get("Server", "")).lower():
                    found.append({"kind": "enigma2", "server": base, "name": f"Enigma2 ({host})",
                                  "login": True})
                elif b"<e2about" in about_xml[:4096]:
                    root = parse_xml(about_xml)
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
        if ports["hdhomerun"] != p and ports["hdhomerun"] in up:
            q = ports["hdhomerun"]
            _hdhr(sess, f"http://{h}" + ("" if q == 80 else f":{q}"), host, found)
        p = ports["tvheadend"]
        if p in up:
            base = f"http://{h}:{p}"
            try:
                r = sess.get(base + "/api/serverinfo", timeout=(CONNECT * 3, READ), **_AS_IT_STANDS)
                realm = r.headers.get("WWW-Authenticate", "").lower()
                if r.status_code == 401 and "tvheadend" in realm:
                    r.close()
                    found.append({"kind": "tvheadend", "server": base, "name": f"Tvheadend - {host}",
                                  "login": True})
                else:
                    js = _short_json(r)
                    if isinstance(js, dict) and "sw_version" in js:
                        found.append({"kind": "tvheadend", "server": base,
                                      "name": f"Tvheadend {js.get('sw_version', '')} - {host}".replace("  ", " ")})
            except Exception:
                pass
        p = ports["jellyfin"]
        if p in up:
            _jellyfin(sess, f"http://{h}:{p}", host, found)
        p = ports["plex"]
        if p in up:
            _plex(sess, f"http://{h}:{p}", host, found)
        _home_servers(sess, h, host, ports, up, found)
    return found


def _home_servers(sess: Any, h: str, host: str, ports: dict[str, int], up: set[int],
                  found: list[dict]) -> None:
    """VDR, DVBViewer, NextPVR and MythTV, each on its port, each told by
    its own answer (a 401 only when it names the kind)."""
    def ask(kind: str, path: str, **kw: Any) -> Any:
        if ports[kind] not in up:
            return None
        try:
            return sess.get(f"http://{h}:{ports[kind]}{path}", timeout=(CONNECT * 3, READ),
                            **_AS_IT_STANDS, **kw)
        except Exception:
            return None

    def base(kind: str) -> str:
        return f"http://{h}:{ports[kind]}"

    def wants_login(r: Any, *words: str) -> bool:
        said = (r.headers.get("WWW-Authenticate", "") + r.headers.get("Server", "")).lower()
        return r.status_code == 401 and any(w in said for w in words)

    def js(r: Any) -> Any:
        return _short_json(r) if r is not None else None

    info = js(ask("vdr", "/info.json"))
    if isinstance(info, dict) and ("vdr" in info or "diskusage" in info):
        vdr: dict = info["vdr"] if isinstance(info.get("vdr"), dict) else {}
        found.append({"kind": "vdr", "server": base("vdr"),
                      "name": " ".join(f"VDR {vdr.get('version') or ''}".split()) + f" - {host}"})
    else:
        r = ask("streamdev", "/channels.m3u")
        head = _short_body(r, 4096, whole=False) if r is not None else None
        if head is not None and head.lstrip()[:7] == b"#EXTM3U":
            found.append({"kind": "vdr", "server": base("streamdev"), "name": f"VDR (streamdev) - {host}"})
    r = ask("dvbviewer", "/api/version.html")
    if r is not None and wants_login(r, "dvbviewer", "recording service", "media server"):
        r.close()
        found.append({"kind": "dvbviewer", "server": base("dvbviewer"), "name": f"DVBViewer - {host}",
                      "login": True})
    elif r is not None:
        body = _short_body(r) or b""
        try:
            name = " ".join((parse_xml(body).text or "").split()) if b"<version" in body[:2048] else ""
        except Exception:
            name = ""
        if "dvbviewer" in name.lower() or "recording service" in name.lower():
            found.append({"kind": "dvbviewer", "server": base("dvbviewer"), "name": f"{name} - {host}"})
    r = ask("nextpvr", "/service", params={"method": "session.initiate", "ver": "1.0", "device": "dopeIPTV"})
    body = (_short_body(r) or b"") if r is not None else b""
    if b"<rsp" in body[:2048] and b"<salt>" in body:
        found.append({"kind": "nextpvr", "server": base("nextpvr"), "name": f"NextPVR - {host}",
                      "login": True})
    r = ask("mythtv", "/Myth/GetHostName", headers={"Accept": "application/json"})
    if r is not None and wants_login(r, "mythtv"):
        r.close()
        found.append({"kind": "mythtv", "server": base("mythtv"), "name": f"MythTV - {host}", "login": True})
    else:
        got = js(r)
        if isinstance(got, dict) and isinstance(got.get("String"), str):
            found.append({"kind": "mythtv", "server": base("mythtv"),
                          "name": f"MythTV ({' '.join(got['String'].split())}) - {host}"})


def _jellyfin(sess: Any, base: str, host: str, found: list[dict]) -> None:
    """Jellyfin and Emby answer the same question; Jellyfin names itself,
    Emby answers under /emby as well."""
    try:
        r = sess.get(base + "/System/Info/Public", timeout=(CONNECT * 3, READ), **_AS_IT_STANDS)
    except Exception:
        return
    js = _short_json(r)
    if not isinstance(js, dict) or not (js.get("Id") or js.get("ServerName")):
        return
    kind = "jellyfin" if "jellyfin" in str(js.get("ProductName") or "").lower() else ""
    if not kind:
        try:
            r = sess.get(base + "/emby/System/Info/Public", timeout=(CONNECT * 3, READ),
                         **_AS_IT_STANDS)
            kind = "emby" if isinstance(_short_json(r), dict) else "jellyfin"
        except Exception:
            kind = "jellyfin"
    name = str(js.get("ServerName") or jellyfin.NAMES[kind])
    found.append({"kind": kind, "server": base, "name": f"{name} - {host}", "id": str(js.get("Id") or "")})


def _plex(sess: Any, base: str, host: str, found: list[dict]) -> None:
    try:
        r = sess.get(base + "/identity", headers={"Accept": "application/json"},
                     timeout=(CONNECT * 3, READ), **_AS_IT_STANDS)
    except Exception:
        return
    js = _short_json(r)
    mc = js.get("MediaContainer") if isinstance(js, dict) else None
    ident = str(mc.get("machineIdentifier") or "") if isinstance(mc, dict) else ""
    if ident:
        found.append({"kind": "plex", "server": base, "name": f"Plex - {host}", "id": ident})


def _hdhr(sess, base: str, host: str, found: list[dict]) -> None:
    try:
        r = sess.get(base + "/discover.json", timeout=(CONNECT * 3, READ), **_AS_IT_STANDS)
    except Exception:
        return
    js = _short_json(r)
    if isinstance(js, dict) and (js.get("DeviceID") or js.get("LineupURL")):
        name = str(js.get("FriendlyName") or "HDHomeRun")
        model = str(js.get("ModelNumber") or "")
        found.append({"kind": "hdhomerun", "server": base,
                      "name": f"{name} ({model}) - {host}" if model else f"{name} - {host}"})


def scan(progress: Callable[[int], None] | None = None, hosts: list[str] | None = None,
         ports: dict[str, int] | None = None, workers: int = 64,
         broadcast: bool = True, extra: list[str] | None = None,
         cancel: threading.Event | None = None, ssdp_port: int = satip.SSDP_PORT) -> list[dict]:
    """Every box and server found: the broadcasts first (HDHomeRun,
    Jellyfin and Emby, Plex's GDM, SAT>IP's SSDP search), then each address
    of the local networks (or *hosts*) and of the other networks Nerd lists
    (*extra*: their addresses; None: as the settings say) asked directly -
    all of them swept at once first, and only an address that took a
    connection asked what it is; a SAT>IP server on the other networks by a
    search sent to each (*ssdp_port*). One entry per address; stopped
    (*cancel*), what was found so far."""
    found: dict[tuple[str, str], dict] = {}
    if broadcast:
        # The four listen at once: each waits out its own answers.
        with ThreadPoolExecutor(max_workers=4) as pool:
            asks = [pool.submit(f) for f in (hdhomerun.discover, jellyfin.discover, plex.discover,
                                              satip.discover)]
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
        for d in answers[1] + answers[2] + answers[3]:
            found[(d["kind"], d["server"].rstrip("/"))] = dict(d, server=d["server"].rstrip("/"))
    if hosts is None:
        hosts = [str(ip) for net in local_networks() for ip in net.hosts()]
    if extra is None:
        extra = home_net.configured()
    mine = set(hosts)
    extra = [h for h in extra if h not in mine]
    everyone = list(dict.fromkeys([*hosts, *extra]))
    if not everyone:
        return list(found.values())

    def stopped() -> bool:
        return cancel is not None and cancel.is_set()
    every = dict(PORTS, **(ports or {}))
    # A SAT>IP server on another network answers a search sent to it, beside
    # the sweep (its own wait, not one more after it).
    searcher = ThreadPoolExecutor(max_workers=1)
    sat = searcher.submit(satip.discover_at, extra, 1.5, ssdp_port, cancel) if extra else None
    # Which addresses have one of the ports open at all, every one asked at
    # once; only those are asked what they are.
    opened = sweep(everyone, every.values(), cancel=cancel)
    alive = [h for h in everyone if opened.get(h)]
    if progress:
        progress(50)
    done = 0
    if alive and not stopped():
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(alive)))) as pool:
            for got in pool.map(lambda h: [] if stopped() else probe(h, ports, opened[h]), alive):
                done += 1
                for d in got:
                    # Asked directly, a box names itself better than a broadcast.
                    found[(d["kind"], d["server"].rstrip("/"))] = d
                if progress:
                    progress(min(99, 50 + done * 50 // len(alive)))
    try:
        for d in sat.result() if sat is not None else []:
            found[("satip", d["server"].rstrip("/"))] = dict(d, server=d["server"].rstrip("/"))
    except Exception as e:
        log.debug("lan scan: the SAT>IP search failed: %s", e)
    finally:
        searcher.shutdown(wait=False)
    if progress:
        progress(100)
    if extra:
        log.info("lan scan: %d address(es) of other networks asked, %d answered",
                 len(extra), sum(1 for h in extra if opened.get(h)))
    log.info("lan scan: %d box(es) on %d address(es), %d answering", len(found), len(everyone), len(alive))
    return sorted(found.values(), key=lambda d: (d["kind"], d["server"]))


# -- what can be cast to --------------------------------------------------------------

#: What a device's answer about itself is read to, at most: these are a few
#: hundred bytes, and whatever answers on the port is free to send more.
SHORT_ANSWER = 256 * 1024


def _short_json(r: Any) -> Any:
    """A short JSON answer of an address that was asked what it is - read
    to SHORT_ANSWER and no further, from that address and nowhere it points
    on to (the request followed no redirect). None for anything else."""
    try:
        if r.status_code != 200:
            r.close()
            return None
        raw = dlna._read_body(r, SHORT_ANSWER)
        if len(raw) > SHORT_ANSWER:
            return None
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return None


#: How every question of a search is asked: of the address asked and
#: nowhere it points on to, its answer read only as far as it is wanted.
_AS_IT_STANDS: dict[str, Any] = {"allow_redirects": False, "stream": True}


def _short_body(r: Any, limit: int = SHORT_ANSWER, whole: bool = True) -> bytes | None:
    """An answer's body as _short_json reads it, for one that is no JSON:
    None unless the address said 200; read to *limit*, and - *whole* - none
    at all when there is more (with *whole* off, its first *limit* bytes)."""
    try:
        if r.status_code != 200:
            r.close()
            return None
        raw = dlna._read_body(r, limit)
    except Exception:
        return None
    if len(raw) > limit and whole:
        return None
    return raw[:limit]


def _kodi(sess: Any, host: str, port: int) -> dict | None:
    """A Kodi's JSON-RPC: "pong" to a ping; a 401 from Kodi's own web
    server (its realm names it) is a Kodi that wants its login."""
    url = f"http://{_http_host(host)}:{port}/jsonrpc"
    try:
        r = sess.post(url, json={"jsonrpc": "2.0", "method": "JSONRPC.Ping", "id": 1},
                      timeout=(CONNECT * 3, READ), allow_redirects=False, stream=True)
    except Exception:
        return None
    if r.status_code == 401:
        realm = (r.headers.get("WWW-Authenticate", "") + r.headers.get("Server", "")).lower()
        r.close()
        if "xbmc" in realm or "kodi" in realm:
            return {"kind": "kodi", "host": host, "port": port, "name": f"Kodi ({host})",
                    "login": True}
        return None
    js = _short_json(r)
    if not isinstance(js, dict) or js.get("result") != "pong":
        return None
    name = ""
    try:
        r = sess.post(url, json={"jsonrpc": "2.0", "method": "XBMC.GetInfoLabels", "id": 2,
                                 "params": {"labels": ["System.FriendlyName"]}},
                      timeout=(CONNECT * 3, READ), allow_redirects=False, stream=True)
        got = (_short_json(r) or {}).get("result")
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
                     params={"params": "name,device_info"}, timeout=(CONNECT * 3, READ),
                     allow_redirects=False, stream=True)
    except Exception:
        return None
    js = _short_json(r)
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
        tcp = pool.submit(sweep, hosts, want.values(), cancel=cancel)
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
